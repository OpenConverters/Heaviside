"""Integration test for ``heaviside.bridge`` against real PyOpenMagnetics.

Slow (~10–30 s per topology) — opt in with ``-m integration``. Skipped
automatically when PyOpenMagnetics is unavailable.

Exercises the full closed loop:

    spec → decompose → bridge.design_magnetics → attach_magnetics_to_tas
    → assert TAS has a populated MAS magnetic.

Single-magnetic topologies only — multi-magnetic mapping is covered by
unit tests.
"""

from __future__ import annotations

import pytest

pyom = pytest.importorskip("PyOpenMagnetics", reason="PyOpenMagnetics not installed")

from heaviside import bridge  # noqa: E402
from heaviside.decomposer import decompose_from_spec  # noqa: E402


BUCK_SPEC: dict = {
    "inputVoltage": {"minimum": 36, "maximum": 60, "nominal": 48},
    "desiredInductance": 22e-6,
    "currentRippleRatio": 0.4,
    "diodeVoltageDrop": 0.7,
    "efficiency": 0.95,
    "operatingPoints": [{
        "outputVoltages": [12.0],
        "outputCurrents": [5.0],
        "switchingFrequency": 200_000,
        "ambientTemperature": 25,
    }],
}


@pytest.mark.integration
def test_buck_end_to_end_bridge() -> None:
    """Full spec → MKF deck + TAS → PyOM magnetic design → annotated TAS."""
    # 1. Decompose to (deck, tas).
    _, tas = decompose_from_spec(
        "buck",
        BUCK_SPEC,
        turns_ratios=[],
        magnetizing_inductance=BUCK_SPEC["desiredInductance"],
    )

    # 2. Ask PyOM to design the buck inductor.
    designs = bridge.design_magnetics(
        "buck", BUCK_SPEC, max_results=1, use_ngspice=False,
    )
    assert len(designs) == 1
    top = designs[0]
    assert top.scoring > 0
    assert top.core_shape_name  # any non-empty string
    assert top.core_material_name
    assert len(top.winding_names) == 1, (
        f"buck has one winding, got {top.winding_names}"
    )

    # 3. Attach into TAS.
    bridge.attach_magnetics_to_tas(tas, designs)

    # 4. The L1 component must now carry the resolved MAS.
    magnetics = [
        c
        for s in tas["stages"]
        for c in s.get("circuit", {}).get("components", [])
        if c.get("category") == "magnetic"
    ]
    assert len(magnetics) == 1
    l1 = magnetics[0]
    assert l1["name"] == "L1"
    assert "data" not in l1
    assert l1["mas"]["core"]["functionalDescription"]["shape"]
    assert l1["mas"]["coil"]["functionalDescription"]
    assert l1["mas_scoring"] == top.scoring


ACF_SPEC: dict = {
    "inputVoltage": {"minimum": 36.0, "nominal": 48.0, "maximum": 60.0},
    "desiredInductance": 1e-3,
    "desiredTurnsRatios": [4.0],
    "currentRippleRatio": 0.4,
    "diodeVoltageDrop": 0.5,
    "maximumDutyCycle": 0.45,
    "efficiency": 0.9,
    "operatingPoints": [{
        "outputVoltages": [12.0],
        "outputCurrents": [5.0],
        "switchingFrequency": 250_000,
        "ambientTemperature": 25,
    }],
}


@pytest.mark.integration
def test_acf_multi_magnetic_end_to_end_bridge() -> None:
    """Full Phase A + Phase B for ACF: main transformer + output inductor.

    Verifies the multi-magnetic orchestrator:
      1. ``design_converter_components`` runs Phase A (transformer) and
         Phase B (outputInductor magnetic + clampCapacitor spec).
      2. ``attach_components_to_tas`` binds main→T1 and
         outputInductor→L_out0 via the registry's ``magnetic_binding``.

    This is slow (~2–3 minutes) because Phase A and Phase B each run
    the full PyOM design loop. Opt in with ``-m integration``.
    """
    _, tas = decompose_from_spec(
        "active_clamp_forward",
        ACF_SPEC,
        turns_ratios=ACF_SPEC["desiredTurnsRatios"],
        magnetizing_inductance=ACF_SPEC["desiredInductance"],
    )

    components = bridge.design_converter_components(
        "active_clamp_forward", ACF_SPEC, max_results=1, use_ngspice=False,
    )
    assert components.main_magnetic.scoring > 0
    assert len(components.main_magnetic.winding_names) >= 2, \
        "ACF transformer must have ≥2 windings"
    assert "outputInductor" in components.extra_magnetics
    assert components.extra_magnetics["outputInductor"].scoring > 0
    # Capacitor extras are spec-only — bridge doesn't design them.
    cap_names = [c.name for c in components.extra_capacitors]
    assert "clampCapacitor" in cap_names

    bridge.attach_components_to_tas(tas, components, topology="active_clamp_forward")

    magnetics = [
        c
        for s in tas["stages"]
        for c in s.get("circuit", {}).get("components", [])
        if c.get("category") == "magnetic"
    ]
    by_name = {c["name"]: c for c in magnetics}
    assert "T1" in by_name and "L_out0" in by_name
    assert by_name["T1"]["mas"]["core"]["functionalDescription"]["shape"]
    assert by_name["L_out0"]["mas"]["core"]["functionalDescription"]["shape"]
    # Distinct designs.
    assert (
        by_name["T1"]["mas_scoring"]
        != by_name["L_out0"]["mas_scoring"]
        or by_name["T1"]["mas"]["core"]["functionalDescription"]["shape"]
        != by_name["L_out0"]["mas"]["core"]["functionalDescription"]["shape"]
    )
