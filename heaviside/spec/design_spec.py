"""User-facing design specification.

This is **the** ``pydantic.BaseModel`` in ``heaviside.spec``. Every other
typed schema payload in Heaviside is a quicktype-generated class derived
from the MAS / PEAS / SAS / CAS / RAS schemas (``heaviside.types``; the
``_generated`` tree is produced by ``make types``, never committed).

A ``DesignSpec`` is what the user (or the agent loop) hands to Heaviside.
It is intentionally minimal — it does not encode topology-specific
parameters. Topology selection and per-topology inputs are derived
downstream from this spec.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

InputType = Literal["dc", "ac_single_phase", "ac_three_phase"]
Regulation = Literal["voltage", "current"]


class _Range(BaseModel):
    """A min / nominal / max triple in SI units."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum: Annotated[float, Field(gt=0)]
    nominal: Annotated[float, Field(gt=0)]
    maximum: Annotated[float, Field(gt=0)]

    @model_validator(mode="after")
    def _ordered(self) -> _Range:
        if not (self.minimum <= self.nominal <= self.maximum):
            raise ValueError(
                f"Range must satisfy minimum ≤ nominal ≤ maximum; "
                f"got {self.minimum} / {self.nominal} / {self.maximum}"
            )
        return self


class Output(BaseModel):
    """A single regulated output rail."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, Field(min_length=1, max_length=32)]
    voltage_v: Annotated[float, Field(gt=0)]
    current_a: Annotated[float, Field(gt=0)]
    regulation: Regulation = "voltage"
    isolated: bool = False


class DesignSpec(BaseModel):
    """Top-level Heaviside design specification.

    All values are SI units (V, A, W, Hz, °C). No millis, no mAs.

    A DesignSpec is *intent*: "I want a 48 V → 12 V / 5 A converter, 200 kHz,
    isolated, > 92% efficiency at 25 °C ambient". Heaviside chooses the
    topology (or accepts a user override), then translates the spec into the
    per-topology MAS schema for PyOpenMagnetics.

    This is the **only** BaseModel in ``heaviside.spec``. Every other typed
    payload — MAS topology inputs, CAS capacitor records, SAS MOSFET records,
    RAS resistor records, MAS magnetic results — is a quicktype-generated
    class from ``heaviside.types``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_type: InputType
    input_voltage: _Range
    outputs: Annotated[tuple[Output, ...], Field(min_length=1, max_length=8)]
    switching_frequency_hz: Annotated[float, Field(gt=0, le=10e6)]
    ambient_temperature_c: Annotated[float, Field(ge=-55, le=125)] = 25.0
    target_efficiency: Annotated[float, Field(gt=0.5, le=0.999)] = 0.92
    topology_hint: str | None = None  # canonical name; ``None`` ⇒ Heaviside chooses

    # --- derived helpers ------------------------------------------------

    @property
    def total_output_power_w(self) -> float:
        return sum(o.voltage_v * o.current_a for o in self.outputs)

    @property
    def is_isolated(self) -> bool:
        return any(o.isolated for o in self.outputs)
