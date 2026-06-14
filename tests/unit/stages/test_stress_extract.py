"""Unit tests for the stress_extract stage (pure, no LLM)."""
from __future__ import annotations

import pytest

from heaviside.stages.stress_extract import analytical, analytical_per_op

_BUCK = {
    "inputVoltage": {"nominal": 48.0, "minimum": 36.0, "maximum": 60.0},
    "currentRippleRatio": 0.4,
    "desiredInductance": 22e-6,
    "operatingPoints": [{
        "outputVoltages": [12.0], "outputCurrents": [5.0],
        "switchingFrequency": 200_000.0, "ambientTemperature": 25.0,
    }],
}


def test_analytical_buck_worst_case():
    s = analytical("buck", _BUCK)
    assert s is not None
    assert s.vds_stress == 60.0  # switch sees Vin_max
    assert s.id_stress == pytest.approx(6.0)  # Iout * (1 + ripple/2)
    assert s.vr_stress == 60.0
    assert s.v_working == 12.0


def test_analytical_unported_topology_is_none():
    assert analytical("totally_made_up", _BUCK) is None


def test_analytical_per_op_returns_one_per_point():
    per = analytical_per_op("buck", _BUCK)
    assert per is not None
    assert len(per) == 1
    assert per[0].vds_stress == 60.0


def test_analytical_per_op_unported_is_none():
    assert analytical_per_op("totally_made_up", _BUCK) is None
