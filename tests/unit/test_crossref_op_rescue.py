"""Operating-point magnetic rescue: when a switching inductor's in-kind
(BOM-value) substitutes all fail the operating-current gate, the cross-ref
SIZES the inductor from the circuit and offers a different-value target part
that covers the ripple requirement AND the operating current — instead of a
false-negative "no substitute". This is the FAE finding on the reference-design
flow (a 4.7µH/low-A part can't serve a 3A buck; a 10µH/8A catalogue part can).

Uses the real Würth catalogue, so it asserts the OUTCOME (a valid rescue), not a
specific MPN which the catalogue may revise.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from heaviside.pipeline.crossref_pipeline import (
    _operating_point_magnetic_rescue,
    _stage_param_check,
    _summarize_candidate,
)
from heaviside.pipeline.stress import derive_stress_by_ref

pytestmark = pytest.mark.unit

_BUCK = {
    "inputVoltage": {"minimum": 20.0, "maximum": 28.0},
    "currentRippleRatio": 0.3,
    "operatingPoints": [
        {"outputVoltages": [5.0], "outputCurrents": [3.0], "switchingFrequency": 500_000.0}
    ],
}


def _stress_L1():
    return derive_stress_by_ref("buck", _BUCK, [{"ref_des": "L1", "component_type": "magnetic"}])["L1"]


def test_rescue_finds_adequate_wurth_inductor_from_operating_point() -> None:
    stress = _stress_L1()
    assert stress.l_required is not None and stress.i_peak is not None
    resc = _operating_point_magnetic_rescue("Würth Elektronik", stress, {})
    assert resc is not None, "expected a Würth inductor sized from the operating point"
    s = resc["summary"]
    # Covers the ripple requirement (>= L_required) without absurd over-sizing…
    assert resc["inductance"] >= stress.l_required
    assert resc["inductance"] <= 4.0 * stress.l_required
    # …and clears the operating peak current (won't saturate).
    assert s["saturation_current"] > stress.i_peak


def test_low_isat_inkind_sub_is_rescued_not_rejected() -> None:
    """A low-Isat 4.7µH Würth candidate for the buck's L1 must be REPLACED by an
    operating-point-sized part (partial + RESCUE fire), not dropped to
    no_substitute."""
    from heaviside.stages.component_match import find_candidates

    stress_by_ref = derive_stress_by_ref(
        "buck", _BUCK, [{"ref_des": "L1", "component_type": "magnetic", "value": "4.7uH"}]
    )
    # A genuinely under-rated in-kind candidate (Isat < the 3.45A peak).
    low = None
    for cc in find_candidates(
        category="magnetic", target_manufacturer="Würth Elektronik",
        value_si=4.7e-6, min_voltage=0, max_results=40,
    ):
        s = _summarize_candidate(cc.env, "magnetic")
        if s.get("saturation_current") and s["saturation_current"] < 3.0:
            low = s["mpn"]
            break
    if low is None:
        pytest.skip("no under-rated 4.7µH Würth candidate in the catalogue to bait the gate")

    row = {
        "ref_des": "L1", "component_type": "magnetic",
        "original_pn": "", "original_value": "4.7uH",
        "substitute_pn": low, "substitute_value": "4.7uH",
        "status": "recommended", "notes": "",
    }
    state = SimpleNamespace(
        crossref_result=[row], stress_by_ref=stress_by_ref,
        source_bom=[{"ref_des": "L1", "component_type": "magnetic", "value": "4.7uH"}],
        target_manufacturer="Würth Elektronik",
    )
    _stage_param_check(state)
    assert row["status"] == "partial"
    assert "RESCUE:operating_point" in row.get("guardrail_fires", [])
    assert row["substitute_pn"] != low  # a different, adequately-rated part
    # The note states the RIPPLE reason (not a false "can't carry current"), and
    # references the operating current the part meets.
    _n = row["notes"].lower()
    assert "ripple" in _n and "operating current" in _n
    assert "cannot carry" not in _n


def test_no_rescue_without_derivable_inductance() -> None:
    # No l_required (e.g. topology we can't size) → no rescue, returns None.
    stress = SimpleNamespace(l_required=None, i_peak=3.45, i_rms=3.0)
    assert _operating_point_magnetic_rescue("Würth Elektronik", stress, {}) is None


def test_rescue_right_sizes_compact_smd_not_a_high_current_block() -> None:
    """Over-dimensioning is not merit: among parts that meet the ripple + current,
    the rescue must pick a COMPACT SMD footprint, not the biggest high-current
    block that merely also fits (the FAE finding on the 13mm/12A pick)."""
    stress = _stress_L1()
    resc = _operating_point_magnetic_rescue("Würth Elektronik", stress, {})
    assert resc is not None
    from heaviside.pipeline.crossref_pipeline import _footprint_area_mm2

    area = _footprint_area_mm2(resc["summary"])
    # A right-sized buck inductor is a small chip, not a 13×12.8mm (~166mm²) block.
    assert area < 60.0, f"picked an over-sized {area:.0f}mm² part"
    # Still clears the operating peak with the saturation margin.
    assert resc["summary"]["saturation_current"] >= 1.15 * stress.i_peak


def test_footprint_metric_rejects_tall_leaded_and_tiny() -> None:
    from heaviside.pipeline.crossref_pipeline import _footprint_area_mm2

    # A flat SMD chip: real footprint.
    assert _footprint_area_mm2({"dimensions_mm": {"length": 6.0, "width": 6.0, "height": 3.0}}) == 36.0
    # A thin tall cylinder (leaded/axial choke): rejected → inf.
    assert _footprint_area_mm2({"dimensions_mm": {"length": 1.2, "width": 1.2, "height": 8.0}}) == float("inf")
    # Implausibly small footprint for a power inductor: rejected → inf.
    assert _footprint_area_mm2({"dimensions_mm": {"length": 1.0, "width": 1.0, "height": 0.5}}) == float("inf")
    # Unknown dimensions: inf (can't compare).
    assert _footprint_area_mm2({}) == float("inf")
