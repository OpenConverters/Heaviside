"""Map an MKF converter spec to a TAS ``inputs`` block.

The TAS root schema (``TAS/schemas/inputs.json``) requires
``inputs.designRequirements`` (with efficiency, inputType, inputVoltage,
outputs[]) and ``inputs.operatingPoints`` (with name, scalar inputVoltage,
ambientTemperature, per-output current/power).

MKF converter specs are looser:

* ``inputVoltage`` is a ``dimensionWithTolerance`` (``{nominal, minimum?,
  maximum?}``).
* ``efficiency`` is a scalar.
* ``operatingPoints[]`` carry ``switchingFrequency, ambientTemperature,
  outputVoltages: [Vs...], outputCurrents: [Is...]`` (parallel lists, one
  entry per output rail).
* No ``inputType``, no per-output ``name``, no ``regulation``.

This module bridges that gap. Per CLAUDE.md "no fallbacks, throw":

* Missing required source fields (``inputVoltage``, ``efficiency``,
  ``operatingPoints[]``, output voltage/current lists) raise
  :class:`InputsMappingError`.
* ``inputType`` is **inferred** from the spec (presence of
  ``lineFrequency`` / ``powerFactorMinimum`` / ``holdUpTimeMinimum``
  signals AC). This is detection, not a default.
* Output ``name`` is synthesised deterministically (``out0``, ``out1``,
  ...) from the list index.
* Output ``regulation`` is fixed to ``"voltage"`` — every topology
  Heaviside currently emits is a voltage regulator. If/when CC/CV charger
  topologies land, the mapper will need a per-topology hook.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


class InputsMappingError(ValueError):
    """Raised when an MKF spec cannot be mapped to a valid TAS inputs block."""


_AC_SIGNAL_KEYS = ("lineFrequency", "powerFactorMinimum", "holdUpTimeMinimum")


def _infer_input_type(spec: Mapping[str, Any]) -> str:
    """Infer ``inputType`` from spec contents.

    Signals AC if any of ``lineFrequency``, ``powerFactorMinimum``, or
    ``holdUpTimeMinimum`` is present (these are AC-only per the TAS
    schema's conditional require). Otherwise DC. Three-phase detection
    is not attempted — caller must pass ``inputType`` explicitly via
    spec for three-phase systems.
    """
    explicit = spec.get("inputType")
    if isinstance(explicit, str):
        if explicit not in ("dc", "acSinglePhase", "acThreePhase"):
            raise InputsMappingError(
                f"spec.inputType must be 'dc' | 'acSinglePhase' | "
                f"'acThreePhase', got {explicit!r}"
            )
        return explicit
    return "acSinglePhase" if any(k in spec for k in _AC_SIGNAL_KEYS) else "dc"


def _normalise_input_voltage(raw: Any) -> dict[str, float]:
    """Coerce an MKF ``inputVoltage`` to a TAS ``dimensionWithTolerance``."""
    if not isinstance(raw, Mapping):
        raise InputsMappingError(
            f"spec.inputVoltage must be a dict with at least one of "
            f"nominal/minimum/maximum, got {type(raw).__name__}"
        )
    out: dict[str, float] = {}
    for key in ("nominal", "minimum", "maximum"):
        if key in raw:
            try:
                out[key] = float(raw[key])
            except (TypeError, ValueError) as e:
                raise InputsMappingError(
                    f"spec.inputVoltage.{key} must be numeric, got {raw[key]!r}"
                ) from e
    if not out:
        raise InputsMappingError(
            "spec.inputVoltage requires at least one of nominal/minimum/maximum"
        )
    return out


def _scalar_input_voltage(spec_iv: Mapping[str, float], op_iv_override: Any) -> float:
    """Pick the scalar Vin for an operating point.

    If the MKF operating point carries ``inputVoltage`` (scalar or
    ``{nominal: ...}``), use it. Otherwise fall back to the nominal /
    minimum / maximum of the spec-level inputVoltage in that order.
    """
    if isinstance(op_iv_override, (int, float)):
        return float(op_iv_override)
    if isinstance(op_iv_override, Mapping) and "nominal" in op_iv_override:
        return float(op_iv_override["nominal"])
    for key in ("nominal", "minimum", "maximum"):
        if key in spec_iv:
            return float(spec_iv[key])
    raise InputsMappingError(
        "operating point has no inputVoltage and spec.inputVoltage has no "
        "nominal/minimum/maximum to derive a scalar from"
    )


def _build_output_requirements(
    output_voltages: Sequence[float],
    names: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Build ``designRequirements.outputs[]`` from the first operating point.

    The TAS schema requires every output to have a stable ``name``
    (used to join with ``operatingPoints[*].outputs[*].name``). We
    synthesise ``out0`` / ``out1`` / ... unless the caller supplies
    explicit names.
    """
    if not output_voltages:
        raise InputsMappingError(
            "operating points must list at least one output voltage"
        )
    if names is not None and len(names) != len(output_voltages):
        raise InputsMappingError(
            f"output name count ({len(names)}) does not match output count "
            f"({len(output_voltages)})"
        )
    out: list[dict[str, Any]] = []
    for i, v in enumerate(output_voltages):
        try:
            v_f = float(v)
        except (TypeError, ValueError) as e:
            raise InputsMappingError(
                f"output voltage at index {i} is not numeric: {v!r}"
            ) from e
        out.append(
            {
                "name": names[i] if names is not None else f"out{i}",
                "voltage": {"nominal": v_f},
                "regulation": "voltage",
            }
        )
    return out


def _build_operating_point(
    op: Mapping[str, Any],
    *,
    op_index: int,
    spec_iv: Mapping[str, float],
    output_names: Sequence[str],
) -> dict[str, Any]:
    if "ambientTemperature" not in op:
        raise InputsMappingError(
            f"operatingPoints[{op_index}] missing ambientTemperature"
        )
    voltages = op.get("outputVoltages")
    currents = op.get("outputCurrents")
    if not isinstance(voltages, list) or not voltages:
        raise InputsMappingError(
            f"operatingPoints[{op_index}].outputVoltages must be a non-empty list"
        )
    if not isinstance(currents, list) or len(currents) != len(voltages):
        raise InputsMappingError(
            f"operatingPoints[{op_index}].outputCurrents must be a list of "
            f"length {len(voltages)} (one per output voltage)"
        )
    if len(output_names) != len(voltages):
        raise InputsMappingError(
            f"operatingPoints[{op_index}] has {len(voltages)} outputs but "
            f"designRequirements.outputs has {len(output_names)}"
        )

    op_outputs: list[dict[str, Any]] = []
    for i, (v, current) in enumerate(zip(voltages, currents)):
        try:
            v_f = float(v)
            i_f = float(current)
        except (TypeError, ValueError) as e:
            raise InputsMappingError(
                f"operatingPoints[{op_index}].outputVoltages[{i}] / outputCurrents[{i}] "
                f"must be numeric"
            ) from e
        if i_f <= 0:
            raise InputsMappingError(
                f"operatingPoints[{op_index}].outputCurrents[{i}] must be > 0 "
                f"(got {i_f})"
            )
        op_outputs.append(
            {
                "name": output_names[i],
                "voltage": v_f,
                "current": i_f,
            }
        )

    return {
        "name": op.get("name", f"op{op_index}"),
        "inputVoltage": _scalar_input_voltage(spec_iv, op.get("inputVoltage")),
        "ambientTemperature": float(op["ambientTemperature"]),
        "outputs": op_outputs,
    }


def build_tas_inputs(
    spec: Mapping[str, Any],
    *,
    output_names: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Map an MKF converter spec to a TAS ``inputs`` block.

    Parameters
    ----------
    spec
        The MKF converter spec dict (the same dict ``decompose_from_spec``
        passes to PyOpenMagnetics).
    output_names
        Optional explicit output rail names. If omitted, synthesised as
        ``out0``, ``out1``, ... matching the first operating point's
        ``outputVoltages`` length.

    Returns
    -------
    dict
        A TAS-conformant ``inputs`` block: ``{designRequirements,
        operatingPoints}``.

    Raises
    ------
    InputsMappingError
        On any missing or malformed required field.
    """
    if not isinstance(spec, Mapping):
        raise InputsMappingError(
            f"spec must be a mapping, got {type(spec).__name__}"
        )

    if "efficiency" not in spec:
        raise InputsMappingError("spec.efficiency is required by TAS schema")
    try:
        efficiency = float(spec["efficiency"])
    except (TypeError, ValueError) as e:
        raise InputsMappingError(
            f"spec.efficiency must be numeric, got {spec['efficiency']!r}"
        ) from e
    if not (0.0 < efficiency <= 1.0):
        raise InputsMappingError(
            f"spec.efficiency must be in (0, 1], got {efficiency}"
        )

    if "inputVoltage" not in spec:
        raise InputsMappingError("spec.inputVoltage is required by TAS schema")
    input_voltage = _normalise_input_voltage(spec["inputVoltage"])

    input_type = _infer_input_type(spec)

    raw_ops = spec.get("operatingPoints")
    if not isinstance(raw_ops, list) or not raw_ops:
        raise InputsMappingError(
            "spec.operatingPoints must be a non-empty list (TAS requires "
            "at least one operating point)"
        )

    # The first operating point seeds designRequirements.outputs (rail
    # identity + nominal voltages). All subsequent operating points must
    # have matching rail count.
    first_voltages = raw_ops[0].get("outputVoltages")
    if not isinstance(first_voltages, list) or not first_voltages:
        raise InputsMappingError(
            "operatingPoints[0].outputVoltages must be a non-empty list "
            "to seed designRequirements.outputs"
        )
    design_outputs = _build_output_requirements(first_voltages, output_names)
    output_name_list = [o["name"] for o in design_outputs]

    design_requirements: dict[str, Any] = {
        "efficiency": efficiency,
        "inputType": input_type,
        "inputVoltage": input_voltage,
        "outputs": design_outputs,
    }

    # AC inputs require lineFrequency.
    if input_type != "dc":
        line_freq = spec.get("lineFrequency")
        if line_freq is None:
            raise InputsMappingError(
                f"inputType={input_type!r} requires spec.lineFrequency"
            )
        design_requirements["lineFrequency"] = (
            line_freq
            if isinstance(line_freq, Mapping)
            else {"nominal": float(line_freq)}
        )

    # Pass-through optional spec fields that map 1:1 onto designRequirements.
    for key in ("powerFactorMinimum", "holdUpTimeMinimum", "isolationVoltage",
                "bidirectional"):
        if key in spec:
            design_requirements[key] = spec[key]

    operating_points = [
        _build_operating_point(
            op, op_index=i, spec_iv=input_voltage, output_names=output_name_list,
        )
        for i, op in enumerate(raw_ops)
    ]

    return {
        "designRequirements": design_requirements,
        "operatingPoints": operating_points,
    }


__all__ = ["InputsMappingError", "build_tas_inputs"]
