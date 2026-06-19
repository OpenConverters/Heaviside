"""Deterministic per-parameter rationale (why exact/recommended/partial)."""

from __future__ import annotations

from heaviside.pipeline.crossref_pipeline import build_match_detail


def _params(d):
    return {p["name"]: p["verdict"] for p in d["params"]}


def test_recommended_voltage_exceeds():
    d = build_match_detail({
        "component_type": "capacitor", "status": "recommended",
        "original_value": "10uF", "substitute_value": "10uF",
        "original_voltage": "16V", "substitute_voltage": "25V",
        "original_package": "0402", "substitute_package": "0402",
    })
    p = _params(d)
    assert p["value"] == "exact"
    assert p["voltage"] == "exceeds"
    assert p["package"] == "same"
    assert "exceeds on voltage" in d["why"]


def test_partial_package_deviates():
    d = build_match_detail({
        "component_type": "capacitor", "status": "partial",
        "original_value": "10uF", "substitute_value": "10uF",
        "original_voltage": "16V", "substitute_voltage": "16V",
        "original_package": "0402", "substitute_package": "0603",
    })
    assert _params(d)["package"] == "differs"
    assert "deviates on package" in d["why"]


def test_voltage_downgrade_is_lower():
    d = build_match_detail({
        "component_type": "capacitor", "status": "partial",
        "original_voltage": "25V", "substitute_voltage": "16V",
    })
    assert _params(d)["voltage"] == "lower"


def test_exact_and_keep_original_have_human_why():
    assert "identical" in build_match_detail({"status": "exact"})["why"]
    d = build_match_detail({"status": "keep_original", "notes": "already Würth"})
    assert d["why"] == "already Würth"


def test_unparseable_values_are_na_not_crash():
    d = build_match_detail({
        "component_type": "capacitor", "status": "partial",
        "original_value": "?", "substitute_value": "10uF",
    })
    assert _params(d)["value"] == "n/a"


def test_magnetic_electrical_list_does_not_break_match_score():
    """Regression: TAS v2 stores magnetic `electrical` as a LIST. The match
    scorer used to call `.get()` on it directly, raising "'list' object has no
    attribute 'get'" and failing match scoring for every inductor crossref
    (surfaced live on the LPS5030-223MRC → Würth run)."""
    from heaviside.pipeline.match_score import _extract_electrical, compute_match_score

    env = {
        "magnetic": {
            "manufacturerInfo": {
                "reference": "7847709220",
                "datasheetInfo": {
                    "part": {"caseCode": "x"},
                    "electrical": [
                        {
                            "subtype": "inductor",
                            "inductance": {"nominal": 22e-6},
                            "saturationCurrentPeak": 3.0,
                            "ratedCurrents": [2.0],
                        }
                    ],
                },
            }
        }
    }
    elec = _extract_electrical(env)
    assert isinstance(elec, dict)
    assert elec.get("inductance") == {"nominal": 22e-6}
    # full scorer must not raise on the list-shaped electrical
    comp = {"ref_des": "L1", "type": "magnetic", "substitute_pn": "7847709220"}
    src = {"ref_des": "L1", "type": "magnetic", "value": "22uH"}
    score = compute_match_score(comp, src, env)
    assert isinstance(score, dict)
