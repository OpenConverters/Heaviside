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
