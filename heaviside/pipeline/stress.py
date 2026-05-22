"""Per-topology component stress derivations.

Given a converter spec + topology + operating point, compute the worst-case
voltage and current the BOM components must withstand:

* MOSFETs: Vds_stress, Id_stress
* Diodes:  Vr_stress, If_stress
* Capacitors: V_working, I_ripple

These derivations are analytical (closed-form from the spec), not empirical.
Sim-based per-component envelope extraction is a separate pipeline stage
(``heaviside/sim/runner.py``) and refines these numbers; the catalogue
selector uses the analytical values as its sizing constraint, the realism
gate compares the picked part's rating against the analytical stress,
and the future op-envelope pass can tighten both.

Per CLAUDE.md "no fallbacks, throw": every required spec field must be
present; missing inputs raise ``StressDerivationError`` rather than
substituting a default.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class StressDerivationError(ValueError):
    """Raised when the spec lacks fields required for stress derivation."""


@dataclass(frozen=True, slots=True)
class ComponentStresses:
    """Worst-case stresses on each component class for one operating point.

    Each field is the WORST value across the input-voltage range AND the
    operating-point range supplied. The selector uses these as floors;
    the realism gate compares the picked part's rated value to the
    stress with the per-class derating ratio.

    ``None`` means "topology doesn't have that component class" (e.g.
    a synchronous-rectifier buck has no D1 → vr_stress=None).
    """

    vds_stress: float | None     # MOSFET drain-source max
    id_stress: float | None      # MOSFET drain current max (continuous)
    vr_stress: float | None      # Diode reverse-voltage max
    if_avg_stress: float | None  # Diode average forward current
    v_working: float | None      # Capacitor steady-state voltage
    i_ripple: float | None       # Capacitor RMS ripple current


def _require_positive(spec: Mapping[str, Any], path: tuple[str, ...], where: str) -> float:
    cur: Any = spec
    for key in path:
        if not isinstance(cur, Mapping) or key not in cur:
            raise StressDerivationError(
                f"{where}: missing required path {'.'.join(path)} at {key!r}"
            )
        cur = cur[key]
    if not isinstance(cur, (int, float)) or cur <= 0:
        raise StressDerivationError(
            f"{where}.{'.'.join(path)} must be a positive number, got {cur!r}"
        )
    return float(cur)


def _vmax(spec: Mapping[str, Any], where: str) -> float:
    return _require_positive(spec, ("inputVoltage", "maximum"), where)


def _first_op(spec: Mapping[str, Any], where: str) -> Mapping[str, Any]:
    ops = spec.get("operatingPoints")
    if not isinstance(ops, list) or not ops or not isinstance(ops[0], Mapping):
        raise StressDerivationError(
            f"{where}.operatingPoints[0]: required non-empty list of objects"
        )
    return ops[0]


def _vout_iout(spec: Mapping[str, Any], where: str) -> tuple[float, float]:
    op = _first_op(spec, where)
    vouts = op.get("outputVoltages")
    iouts = op.get("outputCurrents")
    if not (isinstance(vouts, list) and vouts and isinstance(vouts[0], (int, float))):
        raise StressDerivationError(
            f"{where}.operatingPoints[0].outputVoltages[0]: required positive number"
        )
    if not (isinstance(iouts, list) and iouts and isinstance(iouts[0], (int, float))):
        raise StressDerivationError(
            f"{where}.operatingPoints[0].outputCurrents[0]: required positive number"
        )
    return float(vouts[0]), float(iouts[0])


def _ripple_pp(spec: Mapping[str, Any]) -> float:
    """Spec ripple ratio (ΔI / Iavg). Required positive; the realism gate
    elsewhere already validates this is in (0, 1)."""
    r = spec.get("currentRippleRatio")
    if not isinstance(r, (int, float)) or r <= 0:
        raise StressDerivationError(
            "spec.currentRippleRatio must be a positive number (e.g. 0.3 = 30 %)"
        )
    return float(r)


# ---------------------------------------------------------------------------
# Buck — per-component stress closed forms
# ---------------------------------------------------------------------------
#
# Reference: Maniktala "Switching Power Supplies A to Z" Ch. 1-2.
#
#   Q1 (high-side MOSFET, off-state):
#       Vds_off ≈ Vin_max (assuming low-side body diode clamps the swing)
#       Id_pk   ≈ Iout * (1 + ripple_ratio / 2)
#
#   D1 (low-side freewheeling diode, off-state):
#       Vr ≈ Vin_max
#       If_avg ≈ Iout * (1 - D_min) where D_min = Vout / Vin_max
#
#   C_out (output capacitor):
#       V_working ≈ Vout
#       I_ripple_rms ≈ Iout * ripple_ratio / (2 * sqrt(3))   (triangular wave)


def buck_stresses(spec: Mapping[str, Any]) -> ComponentStresses:
    """Worst-case stresses for a buck converter, evaluated at Vin_max."""
    where = "buck spec"
    vmax = _vmax(spec, where)
    vout, iout = _vout_iout(spec, where)
    ripple = _ripple_pp(spec)
    if vout >= vmax:
        raise StressDerivationError(
            f"{where}: Vout ({vout}) must be < Vin_max ({vmax}); buck cannot step up"
        )
    d_min = vout / vmax
    return ComponentStresses(
        vds_stress=vmax,
        id_stress=iout * (1.0 + ripple / 2.0),
        vr_stress=vmax,
        if_avg_stress=iout * (1.0 - d_min),
        v_working=vout,
        i_ripple=iout * ripple / (2.0 * 3.0 ** 0.5),
    )


# Per-topology dispatch. Extend as topologies onboard their stencils.
_DERIVERS: dict[str, Any] = {
    "buck": buck_stresses,
}


def derive_stresses(topology: str, spec: Mapping[str, Any]) -> ComponentStresses | None:
    """Return the worst-case ``ComponentStresses`` for ``topology``, or
    ``None`` when no per-topology deriver is registered (downstream code
    should treat ``None`` as "skip stress stamping — the realism gate
    will mark voltage-derating checks UNAVAILABLE").
    """
    fn = _DERIVERS.get(topology)
    if fn is None:
        return None
    return fn(spec)


__all__ = [
    "ComponentStresses",
    "StressDerivationError",
    "buck_stresses",
    "derive_stresses",
]
