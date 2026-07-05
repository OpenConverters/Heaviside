#!/usr/bin/env python3
"""Generate the crossref GOLDEN corpus from the current Python engine.

The Python deterministic crossref (scoring.py + param_check.py + stress.py + the
operating-point rescue in crossref_pipeline.py) is the working, FAE-beating
reference. This script calls those REAL functions on a broad set of inputs and
records their outputs as a language-neutral golden JSON. Two consumers assert
against it:

  * tests/unit/test_crossref_golden.py  — Python must still reproduce the golden
    (regression guard; green today, since the golden IS Python's output).
  * Kelvin (Catch2 + a PyKelvin parity test) — the C++ port must reproduce the
    SAME golden. The still-failing entries are the exact port TODO list.

Regenerate after an intentional Python change:  python3 tests/evals/kelvin_parity/generate_golden.py
The corpus lands in Kelvin/tests/golden/crossref_parity.json (shared HS + KH).
Determinism: only pure functions are called — no network, no LLM, no clock.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_KELVIN_GOLDEN = _REPO.parent / "Kelvin" / "tests" / "golden" / "crossref_parity.json"

from heaviside.pipeline.crossref_pipeline import (  # noqa: E402
    _footprint_area_mm2,
    _operating_point_magnetic_rescue,
)
from heaviside.pipeline.scoring import (  # noqa: E402
    over_dimensioning_penalty,
    score_primary_value,
)
from heaviside.pipeline.stress import required_inductance  # noqa: E402


def _round(x: Any, n: int = 10) -> Any:
    return round(x, n) if isinstance(x, (int, float)) else x


# ── score_primary_value: the 4-mode value engine (kills 330nH-for-1.5µH) ──────
_PV_CASES = [
    ("magnetic", 1.5e-6, 330e-9),   # the screenshot: hard FAIL
    ("magnetic", 1.5e-6, 1.5e-6),   # exact
    ("magnetic", 1.5e-6, 1.2e-6),   # 0.8x accept floor -> WARN
    ("magnetic", 1.5e-6, 1.8e-6),   # 1.2x within tight
    ("magnetic", 1.5e-6, 2.0e-6),   # 1.33x -> beyond accept hi (1.25) -> FAIL
    ("magnetic", 4.7e-6, 10e-6),    # 2.1x -> FAIL (in-kind), rescue territory
    ("resistor", 47000.0, 47000.0),
    ("resistor", 47000.0, 46800.0),  # 0.4% -> within tight (0.99/1.01? -> WARN)
    ("resistor", 47000.0, 10000.0),  # far -> FAIL
    ("capacitor", 1e-6, 1e-6),
    ("capacitor", 1e-6, 2e-6),       # 2x in accept window -> WARN
    ("capacitor", 1e-6, 5e-6),       # 5x -> beyond accept (4x) -> FAIL
    ("capacitor", 1e-6, 0.7e-6),     # 0.7x -> below accept (0.8) -> FAIL
    ("chipBead", 600.0, 800.0),      # higher-better impedance
    ("chipBead", 600.0, 300.0),      # deficit
    ("mosfet", 100.0, 80.0),         # no primary-value spec -> None
]


def _gen_primary_value() -> list[dict[str, Any]]:
    out = []
    for cat, orig, sub in _PV_CASES:
        r = score_primary_value(cat, orig, sub)
        expect = None if r is None else {"verdict": r.verdict, "penalty": _round(r.penalty)}
        out.append({"category": cat, "original": orig, "substitute": sub, "expect": expect})
    return out


# ── over_dimensioning_penalty: right-sizing tie-breaker ───────────────────────
_OVERDIM_CASES = [(5.0, 5.0), (5.0, 6.0), (5.0, 10.0), (5.0, 40.0), (5.0, 4.0), (0.0, 5.0)]


def _gen_overdim() -> list[dict[str, Any]]:
    return [
        {"required": req, "actual": act, "expect": _round(over_dimensioning_penalty(req, act))}
        for req, act in _OVERDIM_CASES
    ]


# ── required_inductance: ripple-derived sizing (buck + boost; None otherwise) ──
_BUCK = {
    "inputVoltage": {"minimum": 20.0, "maximum": 28.0},
    "currentRippleRatio": 0.3,
    "operatingPoints": [{"outputVoltages": [5.0], "outputCurrents": [3.0], "switchingFrequency": 500_000.0}],
}
_BOOST = {
    "inputVoltage": {"minimum": 9.0, "maximum": 15.0},
    "currentRippleRatio": 0.4,
    "operatingPoints": [{"outputVoltages": [24.0], "outputCurrents": [2.0], "switchingFrequency": 150_000.0}],
}
_BUCK_NO_FSW = {**_BUCK, "operatingPoints": [{"outputVoltages": [5.0], "outputCurrents": [3.0]}]}


def _gen_required_inductance() -> list[dict[str, Any]]:
    cases = [("buck", _BUCK), ("boost", _BOOST), ("buck", _BUCK_NO_FSW),
             ("flyback", _BUCK), ("cuk", _BUCK)]
    out = []
    for topo, spec in cases:
        L = required_inductance(topo, spec)
        out.append({"topology": topo, "spec": spec, "expect": _round(L, 12) if L is not None else None})
    return out


# ── footprint_area_mm2: right-sizing + bad-dimension plausibility guard ────────
_FOOT_CASES = [
    {"saturation_current": 5.5, "rated_current": 4.5, "dimensions_mm": {"length": 6.0, "width": 6.0, "height": 3.0}},
    {"saturation_current": 7.5, "rated_current": 5.0, "dimensions_mm": {"length": 3.2, "width": 2.5}},  # bad data -> inf
    {"dimensions_mm": {"length": 1.2, "width": 1.2, "height": 8.0}},  # tall leaded -> inf
    {"dimensions_mm": {"length": 1.0, "width": 1.0, "height": 0.5}},  # tiny -> inf
    {},  # unknown -> inf
    {"saturation_current": 3.0, "dimensions_mm": {"length": 4.1, "width": 4.1, "height": 3.1}},
]


def _gen_footprint() -> list[dict[str, Any]]:
    out = []
    for s in _FOOT_CASES:
        a = _footprint_area_mm2(s)
        out.append({"summary": s, "expect": ("inf" if a == float("inf") else _round(a, 4))})
    return out


# ── operating-point magnetic rescue: pick the right part from a fixed pool ─────
# Fixed candidate pool (no catalogue/network) so the golden is fully reproducible
# in C++: the rescue must right-size to the compact, in-margin, closest-L part.
_RESCUE_POOL = [
    {"mpn": "OVERSIZED_12A_13MM", "value_si": 10e-6, "saturation_current": 12.0, "rated_current": 12.0,
     "dimensions_mm": {"length": 13.0, "width": 12.8, "height": 6.2}},
    {"mpn": "RIGHT_10UH_6MM", "value_si": 10e-6, "saturation_current": 5.5, "rated_current": 5.0,
     "dimensions_mm": {"length": 6.0, "width": 6.0, "height": 3.0}},
    {"mpn": "THIN_IR_10UH", "value_si": 10e-6, "saturation_current": 5.0, "rated_current": 3.05,
     "dimensions_mm": {"length": 5.0, "width": 5.0, "height": 2.0}},  # IR<1.25x -> rejected
    {"mpn": "OVER_L_22UH_4MM", "value_si": 22e-6, "saturation_current": 6.0, "rated_current": 5.0,
     "dimensions_mm": {"length": 4.1, "width": 4.1, "height": 3.1}},  # L>1.5x -> tier 1
    {"mpn": "LOW_ISAT_10UH", "value_si": 10e-6, "saturation_current": 2.0, "rated_current": 2.0,
     "dimensions_mm": {"length": 4.0, "width": 4.0, "height": 2.0}},  # Isat<1.15x peak -> rejected
]


class _Stress:
    def __init__(self, l_required, i_peak, i_rms):
        self.l_required, self.i_peak, self.i_rms = l_required, i_peak, i_rms


def _gen_rescue() -> list[dict[str, Any]]:
    # buck 24->5@3A operating point: l_req~9.13uH, i_peak 3.45, i_rms 3.0.
    from types import SimpleNamespace

    import heaviside.pipeline.crossref_pipeline as cp

    cases = [{"l_required": 9.126984e-6, "i_peak": 3.45, "i_rms": 3.0}]
    out = []
    for st in cases:
        stress = SimpleNamespace(**st)
        # Monkeypatch the candidate loader to return the FIXED pool (deterministic).
        orig = cp._target_manufacturer_envelopes
        cp._target_manufacturer_envelopes = lambda mfr, cat, cache, _p=_RESCUE_POOL: [
            {"magnetic": {"manufacturerInfo": {}}, "_fixed": c} for c in _p
        ]
        # The pool entries are already summaries; bypass _summarize_candidate too.
        orig_summ = cp._summarize_candidate
        cp._summarize_candidate = lambda env, cat: env["_fixed"]
        orig_ext = cp._extract_value
        cp._extract_value = lambda env, cat: env["_fixed"].get("value_si")
        try:
            r = _operating_point_magnetic_rescue("Würth Elektronik", stress, {})
        finally:
            cp._target_manufacturer_envelopes = orig
            cp._summarize_candidate = orig_summ
            cp._extract_value = orig_ext
        out.append({"stress": st, "pool": _RESCUE_POOL,
                    "expect": {"mpn": None if r is None else r["summary"]["mpn"]}})
    return out


def main() -> int:
    corpus = {
        "_comment": "GOLDEN crossref corpus generated from the Python engine. Python reproduces it "
                    "(test_crossref_golden.py); Kelvin C++ must reproduce it as the port lands.",
        "score_primary_value": _gen_primary_value(),
        "over_dimensioning_penalty": _gen_overdim(),
        "required_inductance": _gen_required_inductance(),
        "footprint_area_mm2": _gen_footprint(),
        "operating_point_rescue": _gen_rescue(),
    }
    _KELVIN_GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    _KELVIN_GOLDEN.write_text(json.dumps(corpus, indent=1) + "\n")
    counts = {k: len(v) for k, v in corpus.items() if isinstance(v, list)}
    print(f"wrote {_KELVIN_GOLDEN}\n  cases: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
