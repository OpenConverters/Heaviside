"""converter_spec_build stage (master-plan step B0).

Fast unit tests for the deterministic BASE-schema converter-spec builder, plus
a guarded integration test capturing the verified finding that MKF derives the
magnetizing inductance itself and IGNORES an injected ``desiredInductance``.
"""

from __future__ import annotations

import copy

import pytest

from heaviside.stages import converter_spec_build


def _buck_spec() -> dict:
    return {
        "inputVoltage": {"minimum": 9, "nominal": 12, "maximum": 16},
        "operatingPoints": [
            {
                "outputVoltages": [3.3],
                "outputCurrents": [3],
                "switchingFrequency": 500000,
                "ambientTemperature": 25,
            }
        ],
        "currentRippleRatio": 0.3,
    }


def test_defaults_duty_and_vds():
    s = converter_spec_build.build(_buck_spec(), "buck")
    assert s["maximumDutyCycle"] == 0.5
    assert s["maximumDrainSourceVoltage"] == round(16 * 3.0, 1)  # 48.0
    assert s["operatingPoints"][0]["dutyCycle"] == 0.5  # per-OP duty seeded to ceiling


def test_existing_constraints_not_overwritten():
    s = _buck_spec()
    s["maximumDutyCycle"] = 0.42
    s["maximumDrainSourceVoltage"] = 100.0
    out = converter_spec_build.build(s, "buck")
    assert out["maximumDutyCycle"] == 0.42
    assert out["maximumDrainSourceVoltage"] == 100.0
    assert out["operatingPoints"][0]["dutyCycle"] == 0.42


def test_never_injects_desired_inductance():
    """BASE-schema invariant: the builder must NOT add desiredInductance /
    desiredMagnetizingInductance — MKF derives L from the operating point +
    currentRippleRatio (verified: an injected desiredInductance is ignored)."""
    s = converter_spec_build.build(_buck_spec(), "buck")
    assert "desiredInductance" not in s
    assert "desiredMagnetizingInductance" not in s
    # currentRippleRatio is load-bearing (MKF derives L by dividing by it) — kept.
    assert s["currentRippleRatio"] == 0.3


def test_ahb_rectifier_type():
    s = converter_spec_build.build(_buck_spec(), "asymmetric_half_bridge")
    assert s["rectifierType"] == "fullBridge"
    # non-AHB topologies do not get it
    assert "rectifierType" not in converter_spec_build.build(_buck_spec(), "buck")


def test_psfb_phase_shift():
    s = converter_spec_build.build(_buck_spec(), "phase_shifted_full_bridge")
    assert s["operatingPoints"][0]["phaseShift"] == pytest.approx(0.7 * 180.0)


def test_resonant_fsw_window():
    s = converter_spec_build.build(_buck_spec(), "llc")
    # only applied to the resonant family
    assert s.get("minSwitchingFrequency") == pytest.approx(500000 * 0.5)
    assert s.get("maxSwitchingFrequency") == pytest.approx(500000 * 2.0)
    assert "minSwitchingFrequency" not in converter_spec_build.build(_buck_spec(), "buck")


def test_clllc_bus_voltages():
    s = converter_spec_build.build(_buck_spec(), "clllc")
    assert s["highVoltageBusVoltage"] == {"minimum": 9, "nominal": 12, "maximum": 16}
    assert s["lowVoltageBusVoltage"]["nominal"] == pytest.approx(3.3)


def test_wrapper_delegates_identically():
    """full_design._augment_converter_spec must stay a behaviour-identical
    wrapper over the stage (the B0 extraction is a pure refactor)."""
    from heaviside.pipeline import full_design

    for topo in ("buck", "asymmetric_half_bridge", "phase_shifted_full_bridge", "llc", "clllc"):
        via_wrapper = full_design._augment_converter_spec(copy.deepcopy(_buck_spec()), topo)
        via_stage = converter_spec_build.build(copy.deepcopy(_buck_spec()), topo)
        assert via_wrapper == via_stage
