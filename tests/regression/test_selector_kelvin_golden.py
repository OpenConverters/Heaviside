"""Frozen-golden gate for the Kelvin-backed selectors (ABT #125 phase 4/§9.4).

The Python streaming-scan bodies of ``selector.select_*`` were deleted in favour
of delegation to Kelvin (PyKelvin). ``data/selector_kelvin_golden.json`` is the
snapshot of the LAST Python implementation's outputs, captured just before the
switchover, across mosfet/diode/capacitor/resistor under BOTH the kirchhoff_fill
and the richer assemble constraints. This test re-runs the same cases through the
(now Kelvin-backed) ``select_*`` and asserts the full frozen ``*Selection`` — the
chosen part's fields, the margins, and ``alternatives_considered`` — is unchanged.

Any drift here means Kelvin's ranking/filtering diverged from the retired Python
selector: a real regression, not noise. (``diff_kelvin`` was the Python-vs-Kelvin
differential PRE-switchover; post-switchover its Python side is gone, so this
golden is the standing gate.)

Requires PyKelvin on PYTHONPATH (Kelvin/build) + the live TAS DB; skips otherwise.
"""
from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("PyKelvin", reason="PyKelvin not built — see kelvin_adapter")

_DATA = Path(os.environ.get("HEAVISIDE_TAS_DATA_DIR", "/home/alf/PSMA/TAS/data"))
_GOLDEN = Path(__file__).parent / "data" / "selector_kelvin_golden.json"

pytestmark = pytest.mark.skipif(
    not (_DATA / "mosfets.ndjson").exists(),
    reason="live TAS DB not present at HEAVISIDE_TAS_DATA_DIR",
)


def _cases():
    from heaviside.catalogue.selector import (
        CapacitorConstraints,
        CapacitorTiebreaker,
        DiodeConstraints,
        DiodeTiebreaker,
        MosfetConstraints,
        MosfetTiebreaker,
        ResistorConstraints,
    )

    for vds in (30, 60, 100, 250, 650):
        for idc in (2, 10, 30):
            yield ("mosfet", MosfetConstraints(vds_min=vds, id_min=idc, rds_on_max=0.1,
                                               qg_max=float("inf")),
                   dict(tiebreaker=MosfetTiebreaker.LOWEST_RDS_ON))
            yield ("mosfet", MosfetConstraints(vds_min=vds, id_min=idc, rds_on_max=0.1, qg_max=2e-8),
                   dict(tiebreaker=MosfetTiebreaker.LOWEST_QG))
    for vr in (40, 100, 600):
        for ifa in (1, 10):
            yield ("diode", DiodeConstraints(vrrm_min=vr, if_avg_min=ifa),
                   dict(tiebreaker=DiodeTiebreaker.LOWEST_VF))
    for t in (1e-9, 1e-7, 1e-6, 1e-5):
        yield ("capacitor", CapacitorConstraints(capacitance_min=t, capacitance_max=t * 2,
                                                 v_rated_min=50),
               dict(tiebreaker=CapacitorTiebreaker.LOWEST_ESR))
        yield ("capacitor", CapacitorConstraints(capacitance_min=t * 0.8, capacitance_max=t * 10,
                                                 v_rated_min=50),
               dict(tiebreaker=CapacitorTiebreaker.LOWEST_ESR))
    for r in (1.0, 100.0, 1e3, 1e4, 1e5):
        yield ("resistor", ResistorConstraints(target_ohms=r), dict())
        yield ("resistor", ResistorConstraints(target_ohms=r, max_value_deviation=0.2), dict())


def _run(fam, c, kw):
    from heaviside.catalogue.selector import (
        SelectionError,
        select_capacitor,
        select_diode,
        select_mosfet,
        select_resistor,
    )

    fns = {"mosfet": select_mosfet, "diode": select_diode,
           "capacitor": select_capacitor, "resistor": select_resistor}
    try:
        sel = fns[fam](c, tas_data_dir=_DATA, **kw)
        d = dataclasses.asdict(sel)
        d.pop("constraints", None)
        return {"ok": True, "mpn": d["chosen"].get("mpn"),
                "alt": d.get("alternatives_considered"),
                "margins": {k: (round(v, 9) if isinstance(v, float) else v)
                            for k, v in (d.get("margins") or {}).items()},
                "chosen": {k: (round(v, 12) if isinstance(v, float) else v)
                           for k, v in d["chosen"].items() if k not in ("datasheet_url",)}}
    except SelectionError as e:
        return {"ok": False, "rej": dict(e.rejection_counts), "total": e.total_rows_considered}


_CORPUS = Path(__file__).parent / "data" / "selector_kelvin_golden.corpus.json"


def _assert_frozen_corpus() -> None:
    """A golden only means something against the corpus it was frozen on.

    This gate asserts "the selector still picks what it used to". Run it over a different
    catalogue and it asserts nothing of the sort: it compares a choice made from one
    universe of parts against a choice made from another, and the mosfet case alone moves
    from 6,486 candidates to 872 between the full TAS DB and the public extract, picking
    CSD17507Q5A in one and AO4423 in the other. Neither answer is a regression and neither
    is a pass; the question was malformed.

    So the dataset is pinned and a mismatch REFUSES rather than skips. Skipping is how a
    gate quietly stops existing — and the failure this separates is the expensive one:
    "the selector broke" and "the data underneath moved" produce the same red without it.
    (ABT #695.)
    """
    frozen = json.loads(_CORPUS.read_text())
    drift = []
    for family, want in sorted(frozen.items()):
        path = _DATA / want["file"]
        if not path.exists():
            drift.append(f"{family}: {path} missing")
            continue
        size = path.stat().st_size
        if size != want["bytes"]:
            rows = sum(1 for _ in open(path, "rb"))
            drift.append(
                f"{family}: {rows:,} rows / {size:,} B, golden frozen against "
                f"{want['rows']:,} rows / {want['bytes']:,} B")
    if drift:
        raise AssertionError(
            "REFUSING to judge the selector against a different catalogue than the golden "
            "was frozen on — a pass or a fail here would both be meaningless.\n  "
            + "\n  ".join(drift)
            + f"\n\nHEAVISIDE_TAS_DATA_DIR is {_DATA}. Point it at the frozen corpus, or "
              "regenerate the golden AND this corpus fingerprint together if the catalogue "
              "has legitimately moved — which is itself a decision, since it re-baselines "
              "every case in the gate."
        )


def test_selectors_match_frozen_golden():
    _assert_frozen_corpus()
    gold = json.loads(_GOLDEN.read_text())
    diffs = []
    for i, (fam, c, kw) in enumerate(_cases()):
        key = f"{i}:{fam}"
        got = _run(fam, c, kw)
        if gold.get(key) != got:
            diffs.append((key, gold.get(key), got))
    assert not diffs, "Kelvin selector drifted from the frozen Python golden:\n" + "\n".join(
        f"  {k}\n    gold={g}\n    got ={n}" for k, g, n in diffs[:8]
    )
