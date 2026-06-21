"""_rank_candidates: larger-package parts are a LAST RESORT.

When any candidate fits the original's board space, oversize candidates are
dropped from the ranked list entirely (they neither pre-empt a real drop-in nor
churn the downstream LLM/Otto stages). When NOTHING fits, the oversize part is
kept as the only option (later surfaced as a `partial` with a footprint caveat).
"""
from __future__ import annotations

from heaviside.pipeline.crossref_pipeline import _eia_dims_from_case, _rank_candidates


def _cap(mpn, case, cap_f=1e-6, v=50):
    return {"capacitor": {"manufacturerInfo": {
        "reference": mpn, "name": "Würth", "datasheetInfo": {
            "electrical": {"capacitance": {"nominal": cap_f}, "ratedVoltage": v},
            "part": {"case": case, "technology": "ceramic-class-2"}}}}}


def _refs(cands):
    return [c["capacitor"]["manufacturerInfo"]["reference"] for c in cands]


def _comp_0402():
    small = _eia_dims_from_case("0402")
    return {"value": "1uF", "voltage": "50V", "package": "0402",
            "_source_dims_m": (small[0], small[1], None)}


def test_oversize_dropped_when_fitting_exists():
    ranked = _rank_candidates(_comp_0402(), "capacitor",
                              [_cap("BIG2220", "2220"), _cap("FIT0402", "0402")])
    assert _refs(ranked) == ["FIT0402"]   # 2220 overflow dropped


def test_oversize_kept_when_nothing_fits():
    ranked = _rank_candidates(_comp_0402(), "capacitor", [_cap("BIG2220", "2220")])
    assert _refs(ranked) == ["BIG2220"]   # only option → fallback


def test_unknown_source_dims_keeps_all():
    # No source footprint → cannot enforce fit → no filtering (all retained).
    comp = {"value": "1uF", "voltage": "50V", "package": "0402", "_source_dims_m": None}
    ranked = _rank_candidates(comp, "capacitor",
                              [_cap("A0402", "0402"), _cap("B2220", "2220")])
    assert set(_refs(ranked)) == {"A0402", "B2220"}
