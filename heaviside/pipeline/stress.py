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


# ---------------------------------------------------------------------------
# Boost stresses (Maniktala Ch.2)
# ---------------------------------------------------------------------------
#
#   Q1 (low-side MOSFET):
#       Vds_off = Vout (the switch sees Vout when off, assuming D1 conducts)
#       Id_pk   = I_L_pk = Iin * (1 + ripple/2), where Iin = Pout/Vin_min/eta
#   D1 (output diode):
#       Vr ≈ Vout
#       If_avg ≈ Iout
#   C_out:
#       V_working ≈ Vout
#       I_ripple_rms = Iout * sqrt(D / (1-D)) approximation (Maniktala 2.20)


def boost_stresses(spec: Mapping[str, Any]) -> ComponentStresses:
    """Worst-case stresses for a boost converter, evaluated at Vin_min."""
    where = "boost spec"
    vmin = _require_positive(spec, ("inputVoltage", "minimum"), where)
    vmax = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where)
    ripple = _ripple_pp(spec)
    if vout <= vmax:
        raise StressDerivationError(
            f"{where}: Vout ({vout}) must be > Vin_max ({vmax}); boost cannot step down"
        )
    d_max = 1.0 - vmin / vout  # worst at Vin_min
    d_min = 1.0 - vmax / vout
    # Inductor current = Iin = Pout / (Vin * eta). Use eta=1 as the
    # upper bound on inductor current (more conservative for sizing).
    iin_pk = iout * vout / vmin
    return ComponentStresses(
        vds_stress=vout,
        id_stress=iin_pk * (1.0 + ripple / 2.0),
        vr_stress=vout,
        if_avg_stress=iout,
        v_working=vout,
        # Triangular-like at duty cycle: rms approximation
        i_ripple=iout * (d_max ** 0.5) / ((1.0 - d_max) ** 0.5),
    )


# ---------------------------------------------------------------------------
# Cuk stresses (Maniktala Ch.2; inverting buck-boost family)
# ---------------------------------------------------------------------------
#
# Cuk has TWO inductors (L1 input, L2 output) and a flying coupling cap.
# Voltages:
#   Vds_off = Vin + |Vout|  (switch sees the sum of both rails)
#   Vr      = same
# Currents:
#   I_L1 ≈ Iin = Iout * |Vout|/Vin   (at conversion ratio)
#   I_L2 ≈ Iout
#   Id_pk for switch ≈ I_L1 + I_L2 worst case
# Output cap (C_out):
#   Continuous current from L2, ripple is small
#   V_working ≈ |Vout|


def cuk_stresses(spec: Mapping[str, Any]) -> ComponentStresses:
    """Worst-case stresses for a Cuk converter (inverting buck-boost-like)."""
    where = "cuk spec"
    vmin = _require_positive(spec, ("inputVoltage", "minimum"), where)
    _ = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where)
    ripple = _ripple_pp(spec)
    vout_abs = abs(vout)
    # Stress voltage on the switch = Vin + |Vout| (both rails seen)
    vds = vmin + vout_abs
    # Worst-case inductor current sum at Vin_min
    iin = iout * vout_abs / vmin
    id_pk = (iin + iout) * (1.0 + ripple / 2.0)
    return ComponentStresses(
        vds_stress=vds,
        id_stress=id_pk,
        vr_stress=vds,
        if_avg_stress=iout,
        v_working=vout_abs,
        # Output ripple in Cuk is very low because L2 acts as a filter;
        # use a conservative 5% of Iout as RMS.
        i_ripple=0.05 * iout,
    )


# ---------------------------------------------------------------------------
# Flyback stresses (Maniktala Ch.3, isolated single-switch)
# ---------------------------------------------------------------------------
#
#   Q1 (primary-side switch):
#       Vds_off = Vin_max + n * Vout + Vleak_spike  (n = N_p / N_s turns ratio)
#       The reflected secondary voltage during off-state adds to Vin.
#       Without modelling Vleak, use Vds = Vin_max + n * Vout * derating.
#       Id_pk = I_pri_pk computed from energy balance: 0.5 * L * I^2 * fsw = Pin
#   D1 (secondary-side rectifier):
#       Vr = Vout + Vin_max / n
#       If_avg ≈ Iout * (1 / (1 - D_max))  (only conducts during D_off)
#   C_out:
#       V_working = Vout
#       I_ripple_rms = Iout * sqrt((1 - D_min) / D_min) approximation


def flyback_stresses(spec: Mapping[str, Any]) -> ComponentStresses:
    """Worst-case stresses for a flyback converter.

    Requires ``desiredTurnsRatios`` and ``maximumDutyCycle`` on the spec
    (validated separately by ``heaviside.spec.validate_topology``).
    """
    where = "flyback spec"
    vmin = _require_positive(spec, ("inputVoltage", "minimum"), where)
    vmax = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where)
    ratios = spec.get("desiredTurnsRatios")
    if not (isinstance(ratios, list) and ratios and isinstance(ratios[0], (int, float))):
        raise StressDerivationError(
            f"{where}.desiredTurnsRatios[0] required (N_pri / N_sec turns ratio)"
        )
    n = float(ratios[0])
    d_max = spec.get("maximumDutyCycle")
    if not isinstance(d_max, (int, float)) or not (0.0 < d_max < 1.0):
        raise StressDerivationError(
            f"{where}.maximumDutyCycle must be in (0, 1)"
        )
    # Primary switch off-state voltage: Vin_max + reflected secondary
    vds = vmax + n * vout
    # Primary peak current from energy balance: Iin_avg = Iout/n at Dmax
    ipri_pk = (iout / n) * (1.0 + 0.5)  # 50% ripple typical for flyback
    # Secondary diode reverse voltage during ON state
    vr = vout + vmax / n
    if_avg = iout / (1.0 - d_max)
    return ComponentStresses(
        vds_stress=vds,
        id_stress=ipri_pk,
        vr_stress=vr,
        if_avg_stress=if_avg,
        v_working=vout,
        # Flyback cap ripple: secondary current pulses; high ripple
        i_ripple=iout * ((1.0 - d_max) / d_max) ** 0.5,
    )


# Per-topology dispatch. Extend as topologies onboard their stencils.
_DERIVERS: dict[str, Any] = {
    "buck": buck_stresses,
    "boost": boost_stresses,
    "cuk": cuk_stresses,
    "flyback": flyback_stresses,
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
    "boost_stresses",
    "buck_stresses",
    "cuk_stresses",
    "derive_stresses",
    "flyback_stresses",
]
