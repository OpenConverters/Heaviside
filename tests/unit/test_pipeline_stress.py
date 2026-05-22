"""Tests for ``heaviside.pipeline.stress``: analytical stress derivations."""

from __future__ import annotations

import pytest

from heaviside.pipeline.stress import (
    StressDerivationError,
    buck_stresses,
    derive_stresses,
)

_BUCK_OK = {
    "inputVoltage": {"nominal": 48.0, "minimum": 36.0, "maximum": 60.0},
    "currentRippleRatio": 0.4,
    "desiredInductance": 22e-6,
    "operatingPoints": [{
        "outputVoltages": [12.0],
        "outputCurrents": [5.0],
        "switchingFrequency": 200_000.0,
        "ambientTemperature": 25.0,
    }],
}


def test_buck_stresses_match_hand_calc() -> None:
    """Buck 48->12@5A with ripple ratio 0.4:
      Vds_off = Vin_max = 60 V
      Id_peak = Iout * (1 + ripple/2) = 5 * 1.2 = 6 A
      Vr     = Vin_max = 60 V
      D_min  = Vout/Vin_max = 12/60 = 0.2
      If_avg = Iout * (1 - D_min) = 5 * 0.8 = 4 A
      V_working = Vout = 12 V
      I_ripple_rms = Iout * ripple / (2*sqrt(3)) = 5 * 0.4 / 3.464 ~= 0.577 A
    """
    s = buck_stresses(_BUCK_OK)
    assert s.vds_stress == 60.0
    assert s.id_stress == pytest.approx(6.0)
    assert s.vr_stress == 60.0
    assert s.if_avg_stress == pytest.approx(4.0)
    assert s.v_working == 12.0
    assert s.i_ripple == pytest.approx(0.5773, abs=1e-3)


def test_buck_stresses_throws_on_step_up_spec() -> None:
    bad = {**_BUCK_OK, "operatingPoints": [{
        **_BUCK_OK["operatingPoints"][0],
        "outputVoltages": [80.0],  # Vout > Vin_min, cannot step up
    }]}
    with pytest.raises(StressDerivationError, match="step up"):
        buck_stresses(bad)


@pytest.mark.parametrize("bad,where", [
    ({}, "inputVoltage"),
    ({**_BUCK_OK, "inputVoltage": {"nominal": 48}}, "maximum"),
    ({**_BUCK_OK, "operatingPoints": []}, "operatingPoints"),
])
def test_buck_stresses_throws_on_missing_spec_fields(
    bad: dict, where: str,
) -> None:
    with pytest.raises(StressDerivationError, match=where):
        buck_stresses(bad)


def test_derive_stresses_dispatches_per_topology() -> None:
    s = derive_stresses("buck", _BUCK_OK)
    assert s is not None
    assert s.vds_stress == 60.0


def test_derive_stresses_returns_none_for_unported_topology() -> None:
    # No deriver registered yet for boost — caller must treat None as
    # "skip stress stamping; gate will mark voltage_derating UNAVAILABLE".
    assert derive_stresses("boost", _BUCK_OK) is None
