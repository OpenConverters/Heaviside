"""Generate the Kelvin parity golden from the Heaviside deterministic selector.

The golden pins, per (constraints, tiebreaker) case, the MPN the HS `select_*` chose, its
margins, and (on failure) the full rejection histogram — over a COMMITTED fixture extract of
real TAS rows (Kelvin/tests/fixtures/*.ndjson). Kelvin's Catch2 [parity] suite replays it and
asserts candidates[0] == chosen, histograms equal, margins equal. While the Python selector is
canonical, this is the contract Kelvin must reproduce byte-for-decision.

Run:  python -m heaviside.tools.gen_kelvin_golden \
          --fixtures ../Kelvin/tests/fixtures --out ../Kelvin/tests/golden

Cases are derived from REAL sampled rows (so every "hit" case targets an actual part) plus
unsatisfiable constraints (exercising SelectionError + histogram) and a seeded fuzz. Determinism:
a fixed seed; no wall-clock. The output is stable across runs on the same fixture.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path
from typing import Any

from heaviside.catalogue._reader import iter_envelopes
from heaviside.catalogue.selector import (
    Capacitor,
    CapacitorConstraints,
    CapacitorTiebreaker,
    ControllerConstraints,
    Diode,
    DiodeConstraints,
    DiodeTiebreaker,
    Mosfet,
    MosfetConstraints,
    MosfetTiebreaker,
    Resistor,
    ResistorConstraints,
    SelectionError,
    select_capacitor,
    select_controller,
    select_diode,
    select_mosfet,
    select_resistor,
)


def _jsonify(x: Any) -> Any:
    """inf/-inf/nan -> None (JSON-safe; Kelvin emits null for inf margins too)."""
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if isinstance(x, dict):
        return {k: _jsonify(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonify(v) for v in x]
    return x


def _sample_rows(fixtures: Path, filename: str, view_cls, n: int, seed: int) -> list:
    rows = []
    for _lineno, env in iter_envelopes(fixtures / filename):
        v = view_cls.from_envelope(env)
        if v is not None:
            rows.append(v)
    rng = random.Random(seed)
    rng.shuffle(rows)
    return rows[:n]


# ---------------------------------------------------------------------------
# MOSFET
# ---------------------------------------------------------------------------
def gen_mosfet(fixtures: Path, data_dir_env: dict) -> list[dict]:
    cases: list[dict] = []
    rows = _sample_rows(fixtures, "mosfets.ndjson", Mosfet, 12, seed=101)
    tiebreakers = [
        MosfetTiebreaker.LOWEST_RDS_ON,
        MosfetTiebreaker.LOWEST_QG,
        MosfetTiebreaker.HIGHEST_VDS_MARGIN,
        MosfetTiebreaker.HIGHEST_ID_MARGIN,
    ]

    def emit(c: MosfetConstraints, tb: MosfetTiebreaker):
        rec: dict[str, Any] = {
            "constraints": {
                "vds_min": c.vds_min,
                "id_min": c.id_min,
                "rds_on_max": c.rds_on_max,
                "qg_max": None if math.isinf(c.qg_max) else c.qg_max,
                "technology_allowed": sorted(c.technology_allowed),
                "exclude_discontinued": c.exclude_discontinued,
                "op_i_rms": c.op_i_rms,
                "op_vds": c.op_vds,
                "op_duty": c.op_duty,
                "op_fsw": c.op_fsw,
            },
            "tiebreaker": tb.value,
        }
        try:
            sel = select_mosfet(c, tiebreaker=tb, tas_data_dir=fixtures)
            rec["expect"] = {"chosen": sel.chosen.mpn,
                             "margins": _jsonify(dict(sel.margins)),
                             "alternatives": sel.alternatives_considered}
        except SelectionError as e:
            rec["expect"] = {"error": "SelectionError",
                             "rejections": dict(e.rejection_counts),
                             "total": e.total_rows_considered}
        except ValueError:
            rec["expect"] = {"error": "ValueError"}
        cases.append(rec)

    for i, m in enumerate(rows):
        # A constraint this real row satisfies (guarantees >=1 hit).
        c = MosfetConstraints(vds_min=m.vds_rated * 0.5, id_min=m.id_continuous * 0.5,
                              rds_on_max=m.rds_on * 2.0, qg_max=math.inf)
        emit(c, tiebreakers[i % len(tiebreakers)])
        # HV total-loss variant with an operating point (abt #64 territory when vds>100).
        c_hv = MosfetConstraints(vds_min=max(m.vds_rated * 0.5, 1.0), id_min=m.id_continuous * 0.5,
                                 rds_on_max=m.rds_on * 4.0, qg_max=math.inf,
                                 op_i_rms=max(m.id_continuous * 0.5, 0.1), op_vds=m.vds_rated,
                                 op_duty=0.5, op_fsw=100000.0)
        emit(c_hv, MosfetTiebreaker.LOWEST_TOTAL_LOSS)

    # Technology-restricted case.
    if rows:
        m = rows[0]
        emit(MosfetConstraints(vds_min=m.vds_rated * 0.5, id_min=m.id_continuous * 0.5,
                               rds_on_max=m.rds_on * 4.0, qg_max=math.inf,
                               technology_allowed=frozenset({"GaN"})),
             MosfetTiebreaker.LOWEST_RDS_ON)
    # Unsatisfiable (SelectionError + histogram).
    emit(MosfetConstraints(vds_min=1e9, id_min=1.0, rds_on_max=1.0, qg_max=math.inf),
         MosfetTiebreaker.LOWEST_RDS_ON)
    return cases


# ---------------------------------------------------------------------------
# DIODE
# ---------------------------------------------------------------------------
def gen_diode(fixtures: Path) -> list[dict]:
    cases: list[dict] = []
    rows = _sample_rows(fixtures, "diodes.ndjson", Diode, 12, seed=202)
    tbs = [DiodeTiebreaker.LOWEST_VF, DiodeTiebreaker.LOWEST_QRR,
           DiodeTiebreaker.HIGHEST_VRRM_MARGIN, DiodeTiebreaker.HIGHEST_IF_MARGIN]

    def emit(c: DiodeConstraints, tb: DiodeTiebreaker):
        rec: dict[str, Any] = {
            "constraints": {"vrrm_min": c.vrrm_min, "if_avg_min": c.if_avg_min,
                            "qrr_max": c.qrr_max, "exclude_discontinued": c.exclude_discontinued},
            "tiebreaker": tb.value,
        }
        try:
            sel = select_diode(c, tiebreaker=tb, tas_data_dir=fixtures)
            rec["expect"] = {"chosen": sel.chosen.mpn, "margins": _jsonify(dict(sel.margins)),
                             "alternatives": sel.alternatives_considered}
        except SelectionError as e:
            rec["expect"] = {"error": "SelectionError", "rejections": dict(e.rejection_counts),
                             "total": e.total_rows_considered}
        cases.append(rec)

    for i, d in enumerate(rows):
        emit(DiodeConstraints(vrrm_min=d.vrrm_rated * 0.5, if_avg_min=d.if_avg_rated * 0.5),
             tbs[i % len(tbs)])
        emit(DiodeConstraints(vrrm_min=d.vrrm_rated * 0.5, if_avg_min=d.if_avg_rated * 0.5,
                              qrr_max=max(d.qrr, 1e-12)), DiodeTiebreaker.LOWEST_QRR)
    emit(DiodeConstraints(vrrm_min=1e9, if_avg_min=1.0), DiodeTiebreaker.LOWEST_VF)
    return cases


# ---------------------------------------------------------------------------
# CAPACITOR
# ---------------------------------------------------------------------------
def gen_capacitor(fixtures: Path) -> list[dict]:
    cases: list[dict] = []
    rows = _sample_rows(fixtures, "capacitors.ndjson", Capacitor, 12, seed=303)
    tbs = [CapacitorTiebreaker.LOWEST_ESR, CapacitorTiebreaker.HIGHEST_RIPPLE_HEADROOM,
           CapacitorTiebreaker.HIGHEST_VOLTAGE_MARGIN, CapacitorTiebreaker.HIGHEST_CAPACITANCE]

    def emit(c: CapacitorConstraints, tb: CapacitorTiebreaker):
        rec: dict[str, Any] = {
            "constraints": {"capacitance_min": c.capacitance_min, "capacitance_max": c.capacitance_max,
                            "v_rated_min": c.v_rated_min, "ripple_current_min": c.ripple_current_min,
                            "technology_allowed": sorted(c.technology_allowed),
                            "exclude_discontinued": c.exclude_discontinued},
            "tiebreaker": tb.value,
        }
        try:
            sel = select_capacitor(c, tiebreaker=tb, tas_data_dir=fixtures)
            rec["expect"] = {"chosen": sel.chosen.mpn, "margins": _jsonify(dict(sel.margins)),
                             "alternatives": sel.alternatives_considered}
        except SelectionError as e:
            rec["expect"] = {"error": "SelectionError", "rejections": dict(e.rejection_counts),
                             "total": e.total_rows_considered}
        cases.append(rec)

    # This fixture's readable caps carry status=None (Würth parametric rows), so hit cases set
    # exclude_discontinued=False (a supported Kelvin option); a default-exclude case is a miss.
    for i, x in enumerate(rows):
        emit(CapacitorConstraints(capacitance_min=x.capacitance * 0.9,
                                  capacitance_max=x.capacitance * 2.0, v_rated_min=x.v_rated * 0.5,
                                  exclude_discontinued=False),
             tbs[i % len(tbs)])
        # Resonant-style tight band around the real value.
        emit(CapacitorConstraints(capacitance_min=x.capacitance * 0.85,
                                  capacitance_max=x.capacitance * 1.15, v_rated_min=x.v_rated * 0.5,
                                  exclude_discontinued=False),
             CapacitorTiebreaker.HIGHEST_CAPACITANCE)
        if x.ripple_current_rms > 0:
            emit(CapacitorConstraints(capacitance_min=x.capacitance * 0.5,
                                      capacitance_max=x.capacitance * 3.0, v_rated_min=x.v_rated * 0.5,
                                      ripple_current_min=x.ripple_current_rms * 0.5,
                                      exclude_discontinued=False),
                 CapacitorTiebreaker.HIGHEST_RIPPLE_HEADROOM)
    # Default exclude_discontinued -> the status=None rows are all rejected (miss + histogram).
    if rows:
        x = rows[0]
        emit(CapacitorConstraints(capacitance_min=x.capacitance * 0.9,
                                  capacitance_max=x.capacitance * 2.0, v_rated_min=x.v_rated * 0.5),
             CapacitorTiebreaker.LOWEST_ESR)
    emit(CapacitorConstraints(capacitance_min=1.0, capacitance_max=10.0, v_rated_min=1.0),
         CapacitorTiebreaker.LOWEST_ESR)
    return cases


# ---------------------------------------------------------------------------
# RESISTOR
# ---------------------------------------------------------------------------
def gen_resistor(fixtures: Path) -> list[dict]:
    cases: list[dict] = []
    rows = _sample_rows(fixtures, "resistors.ndjson", Resistor, 16, seed=404)

    def emit(c: ResistorConstraints):
        rec: dict[str, Any] = {
            "constraints": {"target_ohms": c.target_ohms, "max_tolerance": c.max_tolerance,
                            "max_value_deviation": c.max_value_deviation},
        }
        try:
            sel = select_resistor(c, tas_data_dir=fixtures)
            rec["expect"] = {"chosen": sel.chosen.mpn, "deviation": sel.deviation,
                             "alternatives": sel.alternatives_considered}
        except SelectionError as e:
            rec["expect"] = {"error": "SelectionError", "rejections": dict(e.rejection_counts),
                             "total": e.total_rows_considered}
        cases.append(rec)

    for r in rows:
        emit(ResistorConstraints(target_ohms=r.resistance, max_tolerance=max(r.tolerance, 0.01),
                                 max_value_deviation=0.2))
    emit(ResistorConstraints(target_ohms=1.2345e6, max_tolerance=0.001, max_value_deviation=0.01))
    return cases


# ---------------------------------------------------------------------------
# CONTROLLER
# ---------------------------------------------------------------------------
def gen_controller(fixtures: Path) -> list[dict]:
    cases: list[dict] = []
    topos = ["flyback", "boost", "buck", "llc", "power_factor_correction", "phase_shifted_full_bridge"]
    cats = [None, "pwmController", "gateDriver", "llcController", "pfcController"]

    def emit(c: ControllerConstraints):
        rec: dict[str, Any] = {
            "constraints": {"topology": c.topology, "vin_nom": c.vin_nom, "fsw_khz": c.fsw_khz,
                            "integrated_fet": c.integrated_fet, "category": c.category},
        }
        try:
            sel = select_controller(c, tas_data_dir=fixtures)
            rec["expect"] = {"chosen": sel.chosen.mpn, "alternatives": sel.alternatives_considered}
        except SelectionError as e:
            rec["expect"] = {"error": "SelectionError", "rejections": dict(e.rejection_counts),
                             "total": e.total_rows_considered}
        cases.append(rec)

    for t in topos:
        for cat in cats:
            emit(ControllerConstraints(topology=t, vin_nom=100.0, fsw_khz=100.0,
                                       integrated_fet=None, category=cat))
    return cases


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    families = {
        "mosfet": gen_mosfet(args.fixtures, {}),
        "diode": gen_diode(args.fixtures),
        "capacitor": gen_capacitor(args.fixtures),
        "resistor": gen_resistor(args.fixtures),
        "controller": gen_controller(args.fixtures),
    }
    for fam, cases in families.items():
        out = args.out / f"kelvin_golden_{fam}.json"
        payload = {"family": fam, "fixture": f"{fam}s.ndjson", "cases": cases}
        out.write_text(json.dumps(payload, indent=1, sort_keys=True))
        n_ok = sum(1 for c in cases if "chosen" in c["expect"])
        n_err = len(cases) - n_ok
        print(f"{fam}: {len(cases)} cases ({n_ok} hits, {n_err} misses) -> {out}")


if __name__ == "__main__":
    main()
