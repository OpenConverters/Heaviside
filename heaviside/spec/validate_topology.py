"""Per-topology spec validators with actionable errors.

PyMKF rejects underspecified converter JSON at various stages of the
``generate_ngspice_circuit`` / ``design_magnetics_from_converter`` calls;
when it does, the message is a deep C++ stack frame ("key 'X' not found"
or "Required dutyCycle Y exceeds maximumDutyCycle Z"). Surfacing those
mid-pipeline forces every user to learn PyMKF's schema by trial and error.

This module captures the empirically-confirmed required fields for each
topology and validates them at CLI spec-load time. The errors point at
the spec file, name the missing field, and where possible suggest a
starting value. Per CLAUDE.md "no silent fallbacks" — we never inject a
default, only inform.

Authoritative source for what's required: ``docs/mkf-handoff.md``
2026-05-22 update + empirical probes against the cp312 PyMKF build.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


class SpecValidationError(ValueError):
    """Raised by :func:`validate_spec_for_topology` for missing or
    invalid topology-required fields. Includes a per-error list rather
    than failing on the first one — users can fix the spec in one round
    instead of fixing-running-fixing-rerunning.
    """

    def __init__(self, topology: str, problems: list[str]) -> None:
        self.topology = topology
        self.problems = problems
        msg = (
            f"spec is missing or invalid for topology {topology!r}:\n  - "
            + "\n  - ".join(problems)
        )
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Field-presence helpers (return a problem string or None)
# ---------------------------------------------------------------------------


def _require_root_field(
    spec: Mapping[str, Any], field: str, hint: str
) -> str | None:
    if field not in spec or spec[field] is None:
        return f"missing root-level {field!r} — {hint}"
    return None


def _require_positive_number(
    spec: Mapping[str, Any], field: str, hint: str
) -> str | None:
    val = spec.get(field)
    if val is None:
        return f"missing root-level {field!r} — {hint}"
    if not isinstance(val, (int, float)) or val <= 0:
        return f"{field!r} must be a positive number, got {val!r}"
    return None


def _require_voltage_range(
    spec: Mapping[str, Any], field: str, hint: str
) -> str | None:
    v = spec.get(field)
    if v is None:
        return f"missing root-level {field!r} — {hint}"
    if not isinstance(v, Mapping):
        return f"{field!r} must be an object like {{minimum, nominal, maximum}}"
    nom = v.get("nominal")
    if not isinstance(nom, (int, float)) or nom <= 0:
        return f"{field!r}.nominal must be a positive number"
    return None


def _require_operating_point_field(
    spec: Mapping[str, Any], op_field: str, hint: str
) -> str | None:
    ops = spec.get("operatingPoints")
    if not isinstance(ops, list) or not ops:
        return "missing 'operatingPoints' (at least one operating point required)"
    op = ops[0]
    if not isinstance(op, Mapping):
        return "operatingPoints[0] must be an object"
    if op_field not in op or op[op_field] is None:
        return (
            f"missing operatingPoints[0].{op_field!r} — {hint}"
        )
    return None


# ---------------------------------------------------------------------------
# Per-topology rule sets
# ---------------------------------------------------------------------------


def _check_flyback(spec: Mapping[str, Any]) -> list[str]:
    return [p for p in (
        _require_positive_number(
            spec, "maximumDutyCycle",
            "flyback's required duty grows with Vin_min; PyMKF needs an upper "
            "bound. Try 0.55 for a 36-60V→12V design.",
        ),
        _require_root_field(
            spec, "desiredTurnsRatios",
            "flyback transformer turns ratio (e.g. [5.0] for 48→12V).",
        ),
    ) if p]


def _check_dab(spec: Mapping[str, Any]) -> list[str]:
    return [p for p in (
        _require_positive_number(
            spec, "desiredMagnetizingInductance",
            "DAB transformer Lm in henries (try 1e-3 = 1 mH as a starting point).",
        ),
        _require_root_field(
            spec, "desiredTurnsRatios",
            "DAB transformer turns ratio (e.g. [1.6] for 400→250V).",
        ),
    ) if p]


def _check_cllc(spec: Mapping[str, Any]) -> list[str]:
    problems = [p for p in (
        _require_positive_number(
            spec, "desiredMagnetizingInductance",
            "CLLC transformer Lm in henries (try 1e-3).",
        ),
        _require_root_field(
            spec, "desiredTurnsRatios",
            "CLLC turns ratio (e.g. [1.0] for a symmetric 1:1 tank).",
        ),
    ) if p]
    # powerFlow lives inside operatingPoints[0], not at root.
    op_problem = _require_operating_point_field(
        spec, "powerFlow",
        "CLLC needs powerFlow ('forward' or 'reverse') inside the "
        "operating point, NOT at root level.",
    )
    if op_problem:
        problems.append(op_problem)
    return problems


def _check_clllc(spec: Mapping[str, Any]) -> list[str]:
    return [p for p in (
        _require_positive_number(
            spec, "desiredMagnetizingInductance",
            "CLLLC transformer Lm in henries (try 1e-3).",
        ),
        _require_root_field(
            spec, "desiredTurnsRatios",
            "CLLLC turns ratios — symmetric tank takes [n_pri_sec, n_pri_pri] "
            "(e.g. [8.0, 1.0] for 400→48V).",
        ),
        _require_root_field(
            spec, "powerFlow",
            "CLLLC needs powerFlow ('forward' or 'reverse') at root level.",
        ),
    ) if p]


def _check_vienna(spec: Mapping[str, Any]) -> list[str]:
    return [p for p in (
        _require_voltage_range(
            spec, "lineToLineVoltage",
            "Vienna needs the 3-phase L-L voltage range (e.g. "
            "{minimum: 380, nominal: 400, maximum: 440}).",
        ),
        _require_positive_number(
            spec, "outputDcVoltage",
            "Vienna DC bus voltage MUST exceed sqrt(2) * V_LL_max "
            "(try 800 V for a 400 V L-L input).",
        ),
        _require_positive_number(
            spec, "switchingFrequency",
            "Vienna requires root-level switchingFrequency (distinct from "
            "operatingPoints[*].switchingFrequency).",
        ),
    ) if p]


def _check_pfc(spec: Mapping[str, Any]) -> list[str]:
    return [p for p in (
        _require_positive_number(
            spec, "outputVoltage",
            "PFC boost output voltage (try 400 V).",
        ),
        _require_positive_number(
            spec, "outputPower",
            "PFC rated output power (try 300 W).",
        ),
        _require_positive_number(
            spec, "lineFrequency",
            "PFC line frequency (50 Hz EU / 60 Hz US).",
        ),
        _require_positive_number(
            spec, "switchingFrequency",
            "PFC requires root-level switchingFrequency.",
        ),
    ) if p]


# Map topology canonical name -> validator. Topologies with no extra
# requirements (buck/boost/cuk/sepic/zeta/llc/...) use the empty-list
# fallthrough.
_TOPOLOGY_VALIDATORS: dict[str, Any] = {
    "flyback": _check_flyback,
    "dual_active_bridge": _check_dab,
    "cllc": _check_cllc,
    "clllc": _check_clllc,
    "vienna": _check_vienna,
    "power_factor_correction": _check_pfc,
}


# ---------------------------------------------------------------------------
# Universal baseline (every topology needs these to be usable)
# ---------------------------------------------------------------------------


def _check_universal(spec: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    # inputVoltage — every dc-input topology needs {minimum, maximum} for the
    # decomposer's worst-case derivations (duty, ripple, peak current).
    iv = spec.get("inputVoltage")
    if iv is None:
        problems.append(
            "missing root-level 'inputVoltage' (needed for every dc-input "
            "topology). Try {nominal: 48, minimum: 36, maximum: 60}."
        )
    elif isinstance(iv, Mapping):
        if "nominal" not in iv or not isinstance(iv["nominal"], (int, float)):
            problems.append(
                "inputVoltage.nominal must be a positive number"
            )

    # operatingPoints[0] must carry outputVoltages, outputCurrents, fsw
    ops = spec.get("operatingPoints")
    if not isinstance(ops, list) or not ops:
        problems.append(
            "missing 'operatingPoints' (at least one with outputVoltages, "
            "outputCurrents, switchingFrequency)."
        )
    elif isinstance(ops[0], Mapping):
        op = ops[0]
        for field, hint in (
            ("outputVoltages", "list of output rail voltages, e.g. [12.0]"),
            ("outputCurrents", "list of output rail currents, e.g. [5.0]"),
            ("switchingFrequency", "positive number in Hz, e.g. 200000"),
        ):
            if field not in op or op[field] is None:
                problems.append(
                    f"missing operatingPoints[0].{field!r} ({hint})"
                )
    return problems


def validate_spec_for_topology(
    topology: str, spec: Mapping[str, Any],
) -> None:
    """Validate ``spec`` against ``topology``'s requirements.

    Combines the universal baseline (inputVoltage + operatingPoints) with
    the topology-specific rules. Collects every problem before raising so
    users can fix the spec in one round.

    Raises:
        SpecValidationError: if any required field is missing or invalid.
    """
    problems = _check_universal(spec)
    validator = _TOPOLOGY_VALIDATORS.get(topology)
    if validator is not None:
        problems.extend(validator(spec))
    if problems:
        raise SpecValidationError(topology, problems)


__all__ = ["SpecValidationError", "validate_spec_for_topology"]
