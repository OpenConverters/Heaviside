"""Zero-token unit tests for the value-integrity invariant checker.

The checker is the machine-graded half of the FAE eval: it flags a substitution
that violates a physically-required invariant (wrong value, dielectric
downgrade) without any datasheet lookup. These tests pin it against synthetic
result rows so the FAE orchestrator can trust it to auto-grade live output.
"""

from __future__ import annotations

from heaviside.pipeline.crossref_invariants import check_result, check_row


def test_330nH_for_1p5uH_is_flagged():
    row = {
        "ref_des": "L1",
        "component_type": "magnetic",
        "original_value": "1.5uH",
        "substitute_pn": "744383560R33",
        "substitute_value": "330nH",
        "status": "partial",
    }
    inv = {
        "category": "magnetic",
        "original_value_si": 1.5e-6,
        "primary_value_accept_lo": 0.8,
        "primary_value_accept_hi": 1.25,
    }
    v = check_row(row, inv)
    assert len(v) == 1 and v[0].parameter == "primary_value"
    assert "below" in v[0].detail


def test_matching_value_is_clean():
    row = {
        "ref_des": "L1",
        "component_type": "magnetic",
        "original_value": "1.5uH",
        "substitute_pn": "74438356015",
        "substitute_value": "1.5uH",
        "status": "recommended",
    }
    inv = {"category": "magnetic", "original_value_si": 1.5e-6,
           "primary_value_accept_lo": 0.8, "primary_value_accept_hi": 1.25}
    assert check_row(row, inv) == []


def test_no_substitute_is_not_a_violation():
    # The tool declining to substitute is not an invariant violation.
    row = {"ref_des": "L1", "component_type": "magnetic", "original_value": "1.5uH",
           "substitute_pn": None, "status": "no_substitute"}
    inv = {"category": "magnetic", "original_value_si": 1.5e-6,
           "primary_value_accept_lo": 0.8, "primary_value_accept_hi": 1.25}
    assert check_row(row, inv) == []


def test_resistor_wrong_value_flagged():
    row = {"ref_des": "R1", "component_type": "resistor", "original_value": "47k",
           "substitute_pn": "X", "substitute_value": "10k", "status": "partial"}
    inv = {"category": "resistor", "original_value_si": 47000.0,
           "primary_value_accept_lo": 0.95, "primary_value_accept_hi": 1.05}
    v = check_row(row, inv)
    assert len(v) == 1 and v[0].parameter == "primary_value"


def test_over_ceiling_capacitor_flagged():
    row = {"ref_des": "C1", "component_type": "capacitor", "original_value": "1uF",
           "substitute_pn": "X", "substitute_value": "10uF", "status": "recommended"}
    inv = {"category": "capacitor", "original_value_si": 1e-6,
           "primary_value_accept_lo": 0.8, "primary_value_accept_hi": 4.0}
    v = check_row(row, inv)
    assert len(v) == 1 and "above" in v[0].detail


def test_dielectric_downgrade_flagged():
    row = {"ref_des": "C1", "component_type": "capacitor", "original_value": "0.1uF",
           "substitute_pn": "X", "substitute_value": "0.1uF",
           "substitute_dielectric": "Y5V", "status": "recommended"}
    inv = {"category": "capacitor", "original_value_si": 1e-7, "dielectric_class_min": "X7R"}
    v = check_row(row, inv)
    assert any(x.parameter == "dielectric_class" for x in v)


def test_check_result_matches_by_ref():
    rows = [
        {"ref_des": "L1", "component_type": "magnetic", "original_value": "1.5uH",
         "substitute_pn": "bad", "substitute_value": "330nH", "status": "partial"},
        {"ref_des": "R1", "component_type": "resistor", "original_value": "47k",
         "substitute_pn": "ok", "substitute_value": "47k", "status": "exact"},
    ]
    invs = {
        "L1": {"category": "magnetic", "original_value_si": 1.5e-6,
               "primary_value_accept_lo": 0.8, "primary_value_accept_hi": 1.25},
        "R1": {"category": "resistor", "original_value_si": 47000.0,
               "primary_value_accept_lo": 0.95, "primary_value_accept_hi": 1.05},
    }
    v = check_result(rows, invs)
    assert len(v) == 1 and v[0].ref_des == "L1"
