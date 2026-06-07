"""Analytical regression suite for the canonical converter corpus.

Per ``AGENTS.md`` rule 7: every PR must pass this gate before merge.
It pins the structural integrity + honest realism verdict of every
entry in ``TAS/data/converters.ndjson`` so that any change to the
schema bridge, decomposer, realism gate, or extractor dispatcher
that silently drifts how the canonical corpus is interpreted will
trip a single targeted test with a clear diff.

The corpus today is 48 entries (47 designs + 1 intentionally-empty
placeholder).  All entries use placeholder component URIs (no real
MAS attached), so the expected baseline verdict for every populated
entry is :class:`RealismVerdict.INCOMPLETE` with every check reporting
``UNAVAILABLE`` — *that is the honest behaviour today*.  When the
librarian agent starts populating real components, the corresponding
golden rows will need to flip to ``PASS`` (or whatever the truth is)
and the diff will land in a single, reviewable commit.

The classifier is deliberately conservative — it only labels what is
unambiguous from stage roles + component fingerprints.  Ambiguous
non-isolated single-FET designs are labelled ``buck`` for the
purposes of the duty-cycle bounds check (a buck and a boost share
the same realism gate today; the topology label only swings the
half-duty rule, which does not apply to either).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from heaviside.pipeline import evaluate_tas
from heaviside.pipeline.realism import CheckStatus, RealismVerdict

CORPUS_PATH = Path(__file__).resolve().parents[3] / "TAS" / "data" / "converters.ndjson"
GOLDEN_PATH = Path(__file__).resolve().parent / "golden_baseline.json"


# ---------------------------------------------------------------------------
# Corpus loading + classification
# ---------------------------------------------------------------------------


def _load_corpus() -> list[dict]:
    if not CORPUS_PATH.is_file():
        pytest.skip(f"corpus missing at {CORPUS_PATH}")
    return [json.loads(line) for line in CORPUS_PATH.read_text().splitlines() if line.strip()]


def _component_fingerprint(entry: dict) -> tuple[str, ...]:
    """Sorted tuple of every component name across every stage.

    Stable shape -> robust classifier signal even if stage order
    changes.
    """
    names: list[str] = []
    for s in entry.get("topology", {}).get("stages", []):
        for c in s.get("circuit", {}).get("components", []) or []:
            n = c.get("name")
            if isinstance(n, str):
                names.append(n)
    return tuple(sorted(names))


# Canonical fingerprints discovered by inspection of the corpus on
# 2026-05-19.  When the corpus shape evolves, regenerate this map and
# the golden file together in the same PR.
_FINGERPRINT_TO_LABEL: dict[tuple[str, ...], str] = {
    # 30 entries — non-synchronous single-FET non-isolated converter.
    # Labelled ``buck`` for the realism gate (the duty-cycle bound is
    # the same 0.05..0.95 used for boost / buck-boost; only the
    # forward-family half-duty rule cares about exact topology).
    ("C_in", "C_out", "D1", "L1", "Q1", "U1"): "buck",
    #  4 entries — synchronous buck.
    ("C_in", "C_out", "L1", "Q_high", "Q_low", "U1"): "buck",
    # 10 entries — single-switch forward (T1 + L_out + forward + freewheel).
    ("C_in", "C_out", "D1", "D_fw", "L_out", "Q1", "T1", "U1"): "single_switch_forward",
    #  3 entries — flyback (T1 + single diode + single switch, no L_out).
    ("C_in", "C_out", "D1", "Q1", "T1", "U1"): "flyback",
    #  1 entry — empty placeholder.
    (): "_empty",
}


def _classify(entry: dict) -> str:
    fp = _component_fingerprint(entry)
    label = _FINGERPRINT_TO_LABEL.get(fp)
    if label is None:
        pytest.fail(
            f"converter corpus: unknown component fingerprint {fp!r} — "
            "either the corpus added a new topology shape (update "
            "_FINGERPRINT_TO_LABEL) or an existing entry drifted "
            "(investigate the diff against converters.ndjson)"
        )
    return label


# ---------------------------------------------------------------------------
# Spec extraction
# ---------------------------------------------------------------------------


def _spec_from_entry(entry: dict) -> dict | None:
    """Build the minimum spec ``evaluate_tas`` needs from PEAS inputs.

    Returns ``None`` for entries with no design requirements (the
    empty placeholder).  Falls back to ``nominal`` when the
    inputVoltage range is unspecified — the realism gate today does
    not require min/max on the spec (only the extractor does, which
    we do not invoke here).
    """
    dr = entry.get("inputs", {}).get("designRequirements")
    ops = entry.get("inputs", {}).get("operatingPoints", []) or []
    if not isinstance(dr, dict) or not dr:
        return None
    vin = dr.get("inputVoltage", {}) or {}
    vin_nom = vin.get("nominal")
    if vin_nom is None:
        return None
    spec: dict = {
        "inputVoltage": {
            "minimum": vin.get("minimum", vin_nom),
            "maximum": vin.get("maximum", vin_nom),
            "nominal": vin_nom,
        }
    }
    outs = dr.get("outputs") or []
    if outs and isinstance(outs[0], dict):
        vout = (outs[0].get("voltage") or {}).get("nominal")
        if vout is not None:
            op = ops[0] if ops else {}
            op_outs = op.get("outputs", []) if isinstance(op, dict) else []
            power = op_outs[0].get("power") if op_outs and isinstance(op_outs[0], dict) else None
            iout = (power / vout) if isinstance(power, (int, float)) and vout > 0 else 1.0
            fsw_nom = (dr.get("switchingFrequency") or {}).get("nominal", 100_000.0)
            spec["operatingPoints"] = [
                {
                    "outputVoltages": [vout],
                    "outputCurrents": [iout],
                    "switchingFrequency": fsw_nom,
                }
            ]
    return spec


# ---------------------------------------------------------------------------
# Snapshot generation
# ---------------------------------------------------------------------------


def _snapshot_entry(idx: int, entry: dict) -> dict:
    """Compact, diffable representation of an entry's realism outcome."""
    label = _classify(entry)
    fp = list(_component_fingerprint(entry))
    spec = _spec_from_entry(entry)
    if spec is None or label == "_empty":
        return {
            "index": idx,
            "label": label,
            "fingerprint": fp,
            "verdict": "SKIPPED_EMPTY",
            "summary": {},
            "check_statuses": {},
        }
    report = evaluate_tas(entry, topology=label, spec=spec)
    return {
        "index": idx,
        "label": label,
        "fingerprint": fp,
        "verdict": report.verdict.name,
        "summary": report.summary,
        "check_statuses": {c.name: c.status.name for c in report.checks},
    }


def _build_current_snapshot() -> list[dict]:
    return [_snapshot_entry(i, e) for i, e in enumerate(_load_corpus())]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_corpus_size_matches_agents_rule_7():
    """AGENTS.md rule 7 declares the corpus at 48 designs.  Catching
    accidental adds / removes belongs in its own test so the diff
    is obvious."""
    n = len(_load_corpus())
    assert n == 48, (
        f"converter corpus has {n} entries — AGENTS.md rule 7 expects 48. "
        "If this is intentional, update both AGENTS.md and the golden file."
    )


def test_all_entries_classify():
    """Every entry must match a known component fingerprint.  A new
    topology shape in the corpus must be added to _FINGERPRINT_TO_LABEL
    explicitly — silent skipping of unknown shapes is not allowed."""
    for i, entry in enumerate(_load_corpus()):
        label = _classify(entry)
        assert isinstance(label, str) and label, f"#{i} produced empty label"


def test_corpus_snapshot_matches_golden():
    """The honest realism verdict + check-status map for every entry
    must match the committed golden file.

    To regenerate (after a deliberate corpus or extractor change):

        python -m tests.regression.converters.regen_golden
    """
    current = _build_current_snapshot()
    if not GOLDEN_PATH.is_file():
        # Bootstrap: write the golden on first run so the next run
        # has something to diff against.  The test still fails so the
        # author notices and reviews the file before committing.
        GOLDEN_PATH.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        pytest.fail(
            f"golden baseline did not exist; wrote {GOLDEN_PATH} — review "
            "and commit it, then re-run the suite"
        )
    expected = json.loads(GOLDEN_PATH.read_text())
    assert current == expected, (
        f"converter corpus regression: snapshot drift vs {GOLDEN_PATH.name}. "
        "If the change is intentional (new extractor, schema bump, librarian "
        "added real component data), regenerate with "
        "`python -m tests.regression.converters.regen_golden` and review."
    )


@pytest.mark.parametrize("idx", list(range(48)))
def test_entry_is_evaluable_or_explicitly_empty(idx):
    """Each populated entry must produce a valid RealismReport without
    raising; the one empty placeholder must remain explicitly skipped."""
    entry = _load_corpus()[idx]
    label = _classify(entry)
    spec = _spec_from_entry(entry)
    if label == "_empty":
        assert spec is None, (
            f"#{idx}: classified as _empty but produced a non-null spec — "
            "either un-classify it or remove the empty entry from the corpus"
        )
        return
    assert spec is not None, f"#{idx} ({label}): could not build spec from PEAS inputs"
    report = evaluate_tas(entry, topology=label, spec=spec)
    assert isinstance(report.verdict, RealismVerdict)
    # Today's honest expectation: no real component data attached, so
    # nothing in the gate should be able to compute a PASS or FAIL yet.
    # When the librarian agent populates real components for an entry,
    # this assertion will trip and the golden + this guard must be
    # updated together.
    for c in report.checks:
        assert c.status in (CheckStatus.UNAVAILABLE, CheckStatus.NOT_APPLICABLE), (
            f"#{idx} ({label}): check {c.name!r} returned {c.status.name} on a "
            "placeholder-only entry — the corpus may have grown real component "
            "data; regenerate the golden and remove this guard for affected entries"
        )
