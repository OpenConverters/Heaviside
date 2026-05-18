"""Unit tests for ``heaviside.spec.DesignSpec``."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from heaviside.spec import DesignSpec, Output


def _valid_spec(**overrides: object) -> DesignSpec:
    defaults: dict[str, object] = {
        "input_type": "dc",
        "input_voltage": {"minimum": 36, "nominal": 48, "maximum": 60},
        "outputs": ({"name": "out1", "voltage_v": 12.0, "current_a": 5.0},),
        "switching_frequency_hz": 200_000.0,
    }
    defaults.update(overrides)
    return DesignSpec.model_validate(defaults)


@pytest.mark.unit
class TestDesignSpec:
    def test_minimal_valid(self) -> None:
        spec = _valid_spec()
        assert spec.total_output_power_w == 60.0
        assert spec.is_isolated is False
        assert spec.target_efficiency == 0.92

    def test_multi_output(self) -> None:
        spec = _valid_spec(
            outputs=(
                {"name": "5v", "voltage_v": 5.0, "current_a": 10.0},
                {"name": "12v", "voltage_v": 12.0, "current_a": 2.0, "isolated": True},
            )
        )
        assert spec.total_output_power_w == pytest.approx(74.0)
        assert spec.is_isolated is True

    def test_range_must_be_ordered(self) -> None:
        with pytest.raises(ValidationError):
            _valid_spec(input_voltage={"minimum": 60, "nominal": 48, "maximum": 36})

    def test_negative_voltage_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _valid_spec(input_voltage={"minimum": -5, "nominal": 48, "maximum": 60})

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DesignSpec.model_validate(
                {
                    "input_type": "dc",
                    "input_voltage": {"minimum": 36, "nominal": 48, "maximum": 60},
                    "outputs": ({"name": "out1", "voltage_v": 12.0, "current_a": 5.0},),
                    "switching_frequency_hz": 200_000.0,
                    "secret_field": "not allowed",
                }
            )

    def test_frozen(self) -> None:
        spec = _valid_spec()
        with pytest.raises(ValidationError):
            spec.target_efficiency = 0.99  # type: ignore[misc]

    def test_at_least_one_output_required(self) -> None:
        with pytest.raises(ValidationError):
            _valid_spec(outputs=())

    def test_excessive_frequency_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _valid_spec(switching_frequency_hz=20e6)


@pytest.mark.unit
class TestOutput:
    def test_voltage_regulation_default(self) -> None:
        o = Output(name="a", voltage_v=12.0, current_a=1.0)
        assert o.regulation == "voltage"
        assert o.isolated is False
