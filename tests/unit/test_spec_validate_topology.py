"""Tests for ``heaviside.spec.validate_topology``.

Coverage: universal baseline + per-topology rules + error message quality.
"""

from __future__ import annotations

import pytest

from heaviside.spec.validate_topology import (
    SpecValidationError,
    validate_spec_for_topology,
)

_BUCK_OK = {
    "inputVoltage": {"nominal": 48.0, "minimum": 36.0, "maximum": 60.0},
    "currentRippleRatio": 0.4,
    "desiredInductance": 22e-6,
    "operatingPoints": [
        {
            "switchingFrequency": 200000.0,
            "outputVoltages": [12.0],
            "outputCurrents": [5.0],
            "ambientTemperature": 25.0,
        }
    ],
}


def test_buck_minimum_spec_passes() -> None:
    # Buck has no per-topology rules beyond the universal baseline.
    validate_spec_for_topology("buck", _BUCK_OK)


def test_missing_input_voltage_reports_helpful_message() -> None:
    spec = {k: v for k, v in _BUCK_OK.items() if k != "inputVoltage"}
    with pytest.raises(SpecValidationError) as excinfo:
        validate_spec_for_topology("buck", spec)
    msg = str(excinfo.value)
    assert "inputVoltage" in msg
    assert "{nominal: 48, minimum: 36, maximum: 60}" in msg


def test_missing_operating_points_reported() -> None:
    spec = {k: v for k, v in _BUCK_OK.items() if k != "operatingPoints"}
    with pytest.raises(SpecValidationError) as excinfo:
        validate_spec_for_topology("buck", spec)
    assert "operatingPoints" in str(excinfo.value)


def test_flyback_requires_maximum_duty_cycle() -> None:
    spec = {**_BUCK_OK, "desiredTurnsRatios": [5.0]}
    with pytest.raises(SpecValidationError) as excinfo:
        validate_spec_for_topology("flyback", spec)
    assert "maximumDutyCycle" in str(excinfo.value)
    assert "0.55" in str(excinfo.value)  # The hint mentions a starting value


def test_dab_requires_magnetizing_inductance_and_turns() -> None:
    spec = dict(_BUCK_OK)  # Missing desiredMagnetizingInductance and desiredTurnsRatios
    with pytest.raises(SpecValidationError) as excinfo:
        validate_spec_for_topology("dual_active_bridge", spec)
    msg = str(excinfo.value)
    assert "desiredMagnetizingInductance" in msg
    assert "desiredTurnsRatios" in msg


def test_cllc_powerflow_must_be_nested() -> None:
    spec = {
        **_BUCK_OK,
        "desiredMagnetizingInductance": 1e-3,
        "desiredTurnsRatios": [1.0],
        "powerFlow": "forward",  # WRONG — must be inside operatingPoints
    }
    with pytest.raises(SpecValidationError) as excinfo:
        validate_spec_for_topology("cllc", spec)
    msg = str(excinfo.value)
    assert "powerFlow" in msg
    assert "operating point" in msg
    assert "NOT at root level" in msg


def test_cllc_with_nested_powerflow_passes() -> None:
    spec = {
        **_BUCK_OK,
        "desiredMagnetizingInductance": 1e-3,
        "desiredTurnsRatios": [1.0],
        "operatingPoints": [
            {**_BUCK_OK["operatingPoints"][0], "powerFlow": "forward"},
        ],
    }
    validate_spec_for_topology("cllc", spec)


def test_vienna_requires_l_l_voltage_and_dc_bus() -> None:
    with pytest.raises(SpecValidationError) as excinfo:
        validate_spec_for_topology("vienna", _BUCK_OK)
    msg = str(excinfo.value)
    assert "lineToLineVoltage" in msg
    assert "outputDcVoltage" in msg
    assert "sqrt(2)" in msg.lower() or "1.414" in msg


def test_pfc_requires_output_voltage_power_line_freq() -> None:
    with pytest.raises(SpecValidationError) as excinfo:
        validate_spec_for_topology("power_factor_correction", _BUCK_OK)
    msg = str(excinfo.value)
    for field in ("outputVoltage", "outputPower", "lineFrequency"):
        assert field in msg, f"missing hint for {field!r}: {msg}"


def test_unknown_topology_runs_universal_only() -> None:
    # Topology not in _TOPOLOGY_VALIDATORS map -> only universal baseline.
    validate_spec_for_topology("totally_made_up_topology", _BUCK_OK)


def test_problems_are_aggregated_not_first_only() -> None:
    """Users should see EVERY missing field at once, not have to round-trip."""
    spec = {}  # Empty - missing inputVoltage AND operatingPoints AND DAB-specific fields
    with pytest.raises(SpecValidationError) as excinfo:
        validate_spec_for_topology("dual_active_bridge", spec)
    msg = str(excinfo.value)
    assert "inputVoltage" in msg
    assert "operatingPoints" in msg
    assert "desiredMagnetizingInductance" in msg
    assert "desiredTurnsRatios" in msg
