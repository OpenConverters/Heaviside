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

import math
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

    vds_stress: float | None  # MOSFET drain-source max
    id_stress: float | None  # MOSFET drain current max (continuous)
    vr_stress: float | None  # Diode reverse-voltage max
    if_avg_stress: float | None  # Diode average forward current
    v_working: float | None  # Capacitor steady-state voltage
    i_ripple: float | None  # Capacitor RMS ripple current


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
    return _op_at(spec, 0, where)


def _op_at(spec: Mapping[str, Any], op_index: int, where: str) -> Mapping[str, Any]:
    ops = spec.get("operatingPoints")
    if not isinstance(ops, list) or not ops:
        raise StressDerivationError(f"{where}.operatingPoints: required non-empty list of objects")
    if not (0 <= op_index < len(ops)):
        raise StressDerivationError(
            f"{where}.operatingPoints[{op_index}]: out of range ({len(ops)} ops total)"
        )
    op = ops[op_index]
    if not isinstance(op, Mapping):
        raise StressDerivationError(
            f"{where}.operatingPoints[{op_index}]: expected object, got {type(op).__name__}"
        )
    return op


def _vout_iout(
    spec: Mapping[str, Any],
    where: str,
    *,
    op_index: int = 0,
) -> tuple[float, float]:
    op = _op_at(spec, op_index, where)
    vouts = op.get("outputVoltages")
    iouts = op.get("outputCurrents")
    if not (isinstance(vouts, list) and vouts and isinstance(vouts[0], (int, float))):
        raise StressDerivationError(
            f"{where}.operatingPoints[{op_index}].outputVoltages[0]: required positive number"
        )
    if not (isinstance(iouts, list) and iouts and isinstance(iouts[0], (int, float))):
        raise StressDerivationError(
            f"{where}.operatingPoints[{op_index}].outputCurrents[0]: required positive number"
        )
    return float(vouts[0]), float(iouts[0])


def _num_operating_points(spec: Mapping[str, Any]) -> int:
    ops = spec.get("operatingPoints")
    return len(ops) if isinstance(ops, list) else 0


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


def buck_stresses(spec: Mapping[str, Any], *, op_index: int = 0) -> ComponentStresses:
    """Stresses for ``operatingPoints[op_index]`` of a buck converter,
    evaluated at Vin_max for worst-case voltage stress. Use
    :func:`derive_stresses` for a worst-case-across-ops result."""
    where = "buck spec"
    vmax = _vmax(spec, where)
    vout, iout = _vout_iout(spec, where, op_index=op_index)
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
        i_ripple=iout * ripple / (2.0 * 3.0**0.5),
    )


def _fsw(op: Mapping[str, Any]) -> float | None:
    f = op.get("switchingFrequency")
    return float(f) if isinstance(f, (int, float)) and f > 0 else None


def required_inductance(topology: str, spec: Mapping[str, Any], *, op_index: int = 0) -> float | None:
    """Minimum main-inductor inductance (H) needed to hold the peak-to-peak
    current ripple within the spec's ``currentRippleRatio`` — the value a
    designer sizes L to. Worst case is taken at Vin_max for a buck (ripple grows
    with Vin). Returns None when the topology isn't supported here or a required
    field (fsw, ripple, V/I) is missing — the caller then does NOT size the part
    (no fabricated inductance).

    Buck:   L = Vout·(Vin_max − Vout) / (Vin_max · fsw · ripple·Iout)  [worst at Vin_max]
    Boost:  L = Vin²·(Vout − Vin) / (ripple·Iout · Vout² · fsw), the inductor
            being on the INPUT and carrying Iin = Iout·Vout/Vin. The ripple grows
            with Vin²(Vout−Vin), peaking at Vin = 2·Vout/3, so L is sized at the
            worst Vin across the input range (endpoints + that interior maximum).

    Only single-power-inductor topologies are sized here. Flyback / Cuk /
    SEPIC use a COUPLED inductor or transformer (or two inductors); a 2-terminal
    power inductor from the magnetics catalogue is the WRONG PART TYPE for them,
    so this returns None and the inductor rescue does not fire (no wrong-type
    pick — the no-fallback rule).
    """
    # Delegated to Kelvin (the deterministic engine); golden-parity-locked. The
    # spec is passed through as JSON; Kelvin returns the buck/boost inductance or
    # None (coupled/multi-inductor topologies). op_index != 0 isn't used by any
    # caller and isn't in the C++ engine's binding.
    if op_index != 0:
        raise StressDerivationError("required_inductance: op_index != 0 not supported (Kelvin engine)")
    from heaviside.pipeline._kelvin_primitives import required_inductance as _kv

    return _kv(topology, spec)


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


def boost_stresses(spec: Mapping[str, Any], *, op_index: int = 0) -> ComponentStresses:
    """Stresses for ``operatingPoints[op_index]`` of a boost converter,
    evaluated at Vin_min for worst-case current stress."""
    where = "boost spec"
    vmin = _require_positive(spec, ("inputVoltage", "minimum"), where)
    vmax = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where, op_index=op_index)
    ripple = _ripple_pp(spec)
    if vout <= vmax:
        raise StressDerivationError(
            f"{where}: Vout ({vout}) must be > Vin_max ({vmax}); boost cannot step down"
        )
    d_max = 1.0 - vmin / vout  # worst at Vin_min
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
        i_ripple=iout * (d_max**0.5) / ((1.0 - d_max) ** 0.5),
    )


def power_factor_correction_stresses(
    spec: Mapping[str, Any], *, op_index: int = 0
) -> ComponentStresses:
    """Stresses for a single-phase boost PFC's power stage.

    A PFC pre-regulator IS a boost converter: the input bridge rectifies the AC
    line and the boost cell (switch + boost diode) steps the rectified line up to
    the regulated DC bus (``outputVoltages[0]``). The voltage stresses are the
    boost stresses — the switch and boost diode block the DC bus, and the bridge
    rectifier diodes block at most the line peak (< the bus), so ``Vout`` is the
    conservative blocking voltage for every semiconductor. We delegate to
    :func:`boost_stresses`, evaluating the worst-case current at ``Vin_min`` (the
    RMS line minimum — conservative, since the instantaneous rectified input
    only ever rises toward the peak). This gives the realism gate the TRUE
    operating stress (~the bus) so the voltage-derating checks evaluate the real
    margin, instead of falling back to the Kirchhoff requirement (already derated,
    which would double-count and read as a failure)."""
    return boost_stresses(spec, op_index=op_index)


def vienna_stresses(spec: Mapping[str, Any], *, op_index: int = 0) -> ComponentStresses:
    """Stresses for a 3-phase Vienna rectifier's power stage.

    A Vienna is a 3-phase boost rectifier feeding a SPLIT DC bus (±Vout/2). The voltage stresses SPLIT by
    component class, so a single boost-style ``Vout`` blocking voltage would be wrong:
      * each phase's bidirectional midpoint switch steers the boost-inductor current to the bus midpoint, so
        it blocks only the HALF link ``Vout/2`` (never the full bus);
      * each rail rectifier diode blocks the FULL link ``Vout`` — when a phase sits at one rail the opposite
        rail's diode sees ``busP-busN = Vout`` in reverse.
    The phase (inductor/switch) current is the per-phase line current of a 3-phase boost; the rail diodes
    carry the DC output current. Worst-case current is taken at ``Vin_min`` (eta=1 is the conservative upper
    bound on the inductor/switch current)."""
    where = "vienna spec"
    vmin = _require_positive(spec, ("inputVoltage", "minimum"), where)
    vout, iout = _vout_iout(spec, where, op_index=op_index)
    ripple = _ripple_pp(spec)
    pout = vout * iout
    iphase_rms = pout / (3.0 * vmin)  # per-phase line current of a balanced 3-phase input
    iphase_pk = (2.0**0.5) * iphase_rms
    return ComponentStresses(
        vds_stress=vout / 2.0,  # midpoint switch blocks the HALF bus
        id_stress=iphase_pk * (1.0 + ripple / 2.0),  # per-phase boost-inductor peak
        vr_stress=vout,  # rail diode blocks the FULL bus
        if_avg_stress=iout,  # rail diode delivers the DC output current
        v_working=vout,  # output cap across the full bus
        i_ripple=iout * ripple / (2.0 * 3.0**0.5),
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


def cuk_stresses(spec: Mapping[str, Any], *, op_index: int = 0) -> ComponentStresses:
    """Stresses for ``operatingPoints[op_index]`` of a Cuk converter."""
    where = "cuk spec"
    vmin = _require_positive(spec, ("inputVoltage", "minimum"), where)
    _ = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where, op_index=op_index)
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


def flyback_stresses(spec: Mapping[str, Any], *, op_index: int = 0) -> ComponentStresses:
    """Stresses for ``operatingPoints[op_index]`` of a flyback converter.

    Requires ``desiredTurnsRatios``, ``maximumDutyCycle``,
    ``efficiency``, and ``desiredMagnetizingInductance`` on the spec
    (the last two needed for the accurate primary peak current
    formula that includes magnetizing ripple — must match the formula
    in heaviside/pipeline/extract.py:_enrich_flyback so the
    post-filter and realism gate agree).
    """
    where = "flyback spec"
    vmin = _require_positive(spec, ("inputVoltage", "minimum"), where)
    vmax = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where, op_index=op_index)
    op = _op_at(spec, op_index, where)
    fsw = op.get("switchingFrequency")
    if not isinstance(fsw, (int, float)) or fsw <= 0:
        raise StressDerivationError(
            f"{where}.operatingPoints[{op_index}].switchingFrequency required"
        )
    ratios = spec.get("desiredTurnsRatios")
    if not (isinstance(ratios, list) and ratios and isinstance(ratios[0], (int, float))):
        raise StressDerivationError(
            f"{where}.desiredTurnsRatios[0] required (N_pri / N_sec turns ratio)"
        )
    n = float(ratios[0])
    d_max = spec.get("maximumDutyCycle")
    if not isinstance(d_max, (int, float)) or not (0.0 < d_max < 1.0):
        raise StressDerivationError(f"{where}.maximumDutyCycle must be in (0, 1)")
    eff = spec.get("efficiency")
    if not isinstance(eff, (int, float)) or not (0.0 < eff <= 1.0):
        raise StressDerivationError(f"{where}.efficiency required in (0, 1] for accurate ipeak")
    Pout = vout * iout
    Lm = spec.get("desiredMagnetizingInductance")
    if not isinstance(Lm, (int, float)) or Lm <= 0:
        # On the closed-loop frequency sweep the magnetic is a sweep OUTPUT, so Lm is not yet
        # known when the FET envelope / saturation stresses are first computed. Derive a
        # representative magnetizing inductance from the target current-ripple ratio so the
        # pre-sweep stresses can be evaluated; the realism gate re-checks against the
        # actually-designed core's Lm afterwards. Inverting the ripple definition used below:
        #   r = ΔIm / I_Lm,avg,  I_Lm,avg = I_in_max / D_max,  ΔIm = Vmin·D_max / (0.8·Lm·fsw)
        #   ⇒ Lm = Vmin²·D_max²·eff / (0.8·r·Pout·fsw)
        r = spec.get("currentRippleRatio")
        if not isinstance(r, (int, float)) or r <= 0:
            raise StressDerivationError(
                f"{where}.desiredMagnetizingInductance or currentRippleRatio required "
                f"(henries / ratio) for ipeak"
            )
        Lm = (vmin * vmin * float(d_max) * float(d_max) * float(eff)) / (
            0.8 * float(r) * Pout * float(fsw)
        )

    # Match extract.py:_enrich_flyback closed form so post-filter and
    # realism gate agree on Ipeak_worst:
    #   I_in_max = Pout / (eff * Vmin)
    #   ripple_worst = Vmin * D_max / (0.8 * Lm * fsw)
    #   ipeak_worst = I_in_max / D_max + ripple_worst / 2
    I_in_max = Pout / (float(eff) * vmin)
    Lm_worst = 0.8 * float(Lm)
    ripple_worst = vmin * float(d_max) / (Lm_worst * float(fsw))
    ipri_pk = I_in_max / float(d_max) + ripple_worst / 2.0

    # Primary switch off-state voltage: Vin_max + reflected secondary
    vds = vmax + n * vout
    # Secondary diode reverse voltage during ON state
    vr = vout + vmax / n
    if_avg = iout / (1.0 - float(d_max))
    return ComponentStresses(
        vds_stress=vds,
        id_stress=ipri_pk,
        vr_stress=vr,
        if_avg_stress=if_avg,
        v_working=vout,
        # Flyback output-cap RMS ripple: the diode feeds the cap for the
        # (1-D) off fraction, so I_Crms = Iout*sqrt(D/(1-D)) — same as boost
        # (see boost_stresses). The reciprocal sqrt((1-D)/D) undersizes the cap
        # for D > 0.5, exactly where flyback ripple is worst.
        i_ripple=iout * (float(d_max) / (1.0 - float(d_max))) ** 0.5,
    )


# ---------------------------------------------------------------------------
# Helpers for isolated topologies
# ---------------------------------------------------------------------------


def _turns_ratio(spec: Mapping[str, Any], where: str) -> float:
    """Extract desiredTurnsRatios[0] (N_pri / N_sec)."""
    ratios = spec.get("desiredTurnsRatios")
    if not (isinstance(ratios, list) and ratios and isinstance(ratios[0], (int, float))):
        raise StressDerivationError(
            f"{where}.desiredTurnsRatios[0] required (N_pri / N_sec turns ratio)"
        )
    return float(ratios[0])


def _duty_max(spec: Mapping[str, Any], where: str) -> float:
    """Extract maximumDutyCycle in (0, 1)."""
    d_max = spec.get("maximumDutyCycle")
    if not isinstance(d_max, (int, float)) or not (0.0 < d_max < 1.0):
        raise StressDerivationError(f"{where}.maximumDutyCycle must be in (0, 1)")
    return float(d_max)


def _efficiency(spec: Mapping[str, Any], where: str) -> float:
    """Extract efficiency in (0, 1]."""
    eff = spec.get("efficiency")
    if not isinstance(eff, (int, float)) or not (0.0 < eff <= 1.0):
        raise StressDerivationError(
            f"{where}.efficiency required in (0, 1] for accurate current calc"
        )
    return float(eff)


def _switching_freq(spec: Mapping[str, Any], op_index: int, where: str) -> float:
    """Extract switchingFrequency from the given operating point."""
    op = _op_at(spec, op_index, where)
    fsw = op.get("switchingFrequency")
    if not isinstance(fsw, (int, float)) or fsw <= 0:
        raise StressDerivationError(
            f"{where}.operatingPoints[{op_index}].switchingFrequency required"
        )
    return float(fsw)


# ---------------------------------------------------------------------------
# SEPIC stresses (single-ended primary-inductor converter)
# ---------------------------------------------------------------------------
#
# Similar to Cuk: Vds = Vin + Vout, Vr = Vin + Vout
# Switch current = sum of L1 (input) and L2 (output) currents.


def sepic_stresses(spec: Mapping[str, Any], *, op_index: int = 0) -> ComponentStresses:
    where = "sepic spec"
    vmin = _require_positive(spec, ("inputVoltage", "minimum"), where)
    vmax = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where, op_index=op_index)
    ripple = _ripple_pp(spec)
    vds = vmax + vout
    iin = iout * vout / vmin  # worst at Vin_min
    id_pk = (iin + iout) * (1.0 + ripple / 2.0)
    return ComponentStresses(
        vds_stress=vds,
        id_stress=id_pk,
        vr_stress=vds,
        if_avg_stress=iout,
        v_working=vout,
        i_ripple=0.05 * iout,
    )


# ---------------------------------------------------------------------------
# Zeta stresses (coupled-inductor buck-boost, similar to SEPIC/Cuk)
# ---------------------------------------------------------------------------


def zeta_stresses(spec: Mapping[str, Any], *, op_index: int = 0) -> ComponentStresses:
    where = "zeta spec"
    vmin = _require_positive(spec, ("inputVoltage", "minimum"), where)
    vmax = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where, op_index=op_index)
    ripple = _ripple_pp(spec)
    vds = vmax + vout
    iin = iout * vout / vmin
    id_pk = (iin + iout) * (1.0 + ripple / 2.0)
    return ComponentStresses(
        vds_stress=vds,
        id_stress=id_pk,
        vr_stress=vds,
        if_avg_stress=iout,
        v_working=vout,
        i_ripple=0.05 * iout,
    )


# ---------------------------------------------------------------------------
# Four-switch buck-boost
# ---------------------------------------------------------------------------
#
# Two half-bridges: Vds = max(Vin_max, Vout) for each FET.
# Switch current = max(Iin, Iout) since it operates in buck or boost mode.
# Synchronous rectification — no diode.


def four_switch_buck_boost_stresses(
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> ComponentStresses:
    where = "four_switch_buck_boost spec"
    vmin = _require_positive(spec, ("inputVoltage", "minimum"), where)
    vmax = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where, op_index=op_index)
    ripple = _ripple_pp(spec)
    vds = max(vmax, vout)
    iin = iout * vout / vmin  # worst at Vin_min
    id_pk = max(iin, iout) * (1.0 + ripple / 2.0)
    return ComponentStresses(
        vds_stress=vds,
        id_stress=id_pk,
        vr_stress=None,  # synchronous rectification, no diode
        if_avg_stress=None,
        v_working=max(vmax, vout),
        i_ripple=iout * ripple / (2.0 * 3.0**0.5),
    )


# ---------------------------------------------------------------------------
# Single-switch forward (Vds = 2×Vin due to demagnetization reset)
# ---------------------------------------------------------------------------


def single_switch_forward_stresses(
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> ComponentStresses:
    where = "single_switch_forward spec"
    _require_positive(spec, ("inputVoltage", "minimum"), where)  # validate completeness
    vmax = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where, op_index=op_index)
    n = _turns_ratio(spec, where)
    _duty_max(spec, where)  # validate duty-cycle spec; Vds here is 2·Vin_max, not D
    ripple = _ripple_pp(spec)
    # Switch sees 2×Vin during demagnetisation (1:1 reset winding)
    vds = 2.0 * vmax
    # Primary current: I_pri = Iout / n during D_on
    ipri = iout / n * (1.0 + ripple / 2.0)
    # Secondary diode reverse voltage: Vout + Vin_max/n * D/(1-D)
    vr = vout + vmax / n
    if_avg = iout
    return ComponentStresses(
        vds_stress=vds,
        id_stress=ipri,
        vr_stress=vr,
        if_avg_stress=if_avg,
        v_working=vout,
        i_ripple=iout * ripple / (2.0 * 3.0**0.5),
    )


# ---------------------------------------------------------------------------
# Two-switch forward (Vds = Vin, clamped by body diodes)
# ---------------------------------------------------------------------------


def two_switch_forward_stresses(
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> ComponentStresses:
    where = "two_switch_forward spec"
    _require_positive(spec, ("inputVoltage", "minimum"), where)  # validate completeness
    vmax = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where, op_index=op_index)
    n = _turns_ratio(spec, where)
    ripple = _ripple_pp(spec)
    vds = vmax  # clamped to Vin by two-switch topology
    ipri = iout / n * (1.0 + ripple / 2.0)
    vr = vout + vmax / n
    return ComponentStresses(
        vds_stress=vds,
        id_stress=ipri,
        vr_stress=vr,
        if_avg_stress=iout,
        v_working=vout,
        i_ripple=iout * ripple / (2.0 * 3.0**0.5),
    )


# ---------------------------------------------------------------------------
# Active-clamp forward (Vds ≈ Vin + Vclamp; Vclamp ≈ n*Vout)
# ---------------------------------------------------------------------------
#
# The clamp capacitor resets the transformer; Vds on the main switch
# is Vin + V_clamp where V_clamp ≈ Vin*D/(1-D). Worst case ≈ Vin/(1-D).


def active_clamp_forward_stresses(
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> ComponentStresses:
    where = "active_clamp_forward spec"
    _require_positive(spec, ("inputVoltage", "minimum"), where)  # validate completeness
    vmax = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where, op_index=op_index)
    n = _turns_ratio(spec, where)
    d_max = _duty_max(spec, where)
    ripple = _ripple_pp(spec)
    # Vds on main switch: Vin / (1 - D_max)
    vds = vmax / (1.0 - d_max)
    ipri = iout / n * (1.0 + ripple / 2.0)
    vr = vout + vmax / n
    return ComponentStresses(
        vds_stress=vds,
        id_stress=ipri,
        vr_stress=vr,
        if_avg_stress=iout,
        v_working=vout,
        i_ripple=iout * ripple / (2.0 * 3.0**0.5),
    )


# ---------------------------------------------------------------------------
# Push-pull (Vds = 2×Vin, centre-tapped primary)
# ---------------------------------------------------------------------------


def push_pull_stresses(
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> ComponentStresses:
    where = "push_pull spec"
    _require_positive(spec, ("inputVoltage", "minimum"), where)  # validate completeness
    vmax = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where, op_index=op_index)
    n = _turns_ratio(spec, where)
    ripple = _ripple_pp(spec)
    vds = 2.0 * vmax
    ipri = iout / n * (1.0 + ripple / 2.0)
    # Secondary diode sees 2×Vout (centre-tapped secondary)
    vr = 2.0 * vout
    return ComponentStresses(
        vds_stress=vds,
        id_stress=ipri,
        vr_stress=vr,
        if_avg_stress=iout,
        v_working=vout,
        i_ripple=iout * ripple / (2.0 * 3.0**0.5),
    )


# ---------------------------------------------------------------------------
# Asymmetric half-bridge (AHB) — Vds = Vin per switch
# ---------------------------------------------------------------------------


def asymmetric_half_bridge_stresses(
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> ComponentStresses:
    where = "asymmetric_half_bridge spec"
    _require_positive(spec, ("inputVoltage", "minimum"), where)  # validate completeness
    vmax = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where, op_index=op_index)
    n = _turns_ratio(spec, where)
    ripple = _ripple_pp(spec)
    vds = vmax
    ipri = iout / n * (1.0 + ripple / 2.0)
    vr = vout + vmax / n
    return ComponentStresses(
        vds_stress=vds,
        id_stress=ipri,
        vr_stress=vr,
        if_avg_stress=iout,
        v_working=vout,
        i_ripple=iout * ripple / (2.0 * 3.0**0.5),
    )


# ---------------------------------------------------------------------------
# Phase-shifted full bridge (PSFB) — Vds = Vin per switch
# ---------------------------------------------------------------------------


def phase_shifted_full_bridge_stresses(
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> ComponentStresses:
    where = "phase_shifted_full_bridge spec"
    _require_positive(spec, ("inputVoltage", "minimum"), where)  # validate completeness
    vmax = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where, op_index=op_index)
    n = _turns_ratio(spec, where)
    ripple = _ripple_pp(spec)
    vds = vmax  # full bridge: each switch sees Vin
    ipri = iout / n * (1.0 + ripple / 2.0)
    # Secondary rectifier (centre-tapped): Vr = 2 * Vout
    vr = 2.0 * vout
    return ComponentStresses(
        vds_stress=vds,
        id_stress=ipri,
        vr_stress=vr,
        if_avg_stress=iout,
        v_working=vout,
        i_ripple=iout * ripple / (2.0 * 3.0**0.5),
    )


# ---------------------------------------------------------------------------
# Phase-shifted half bridge (PSHB) — Vds = Vin per switch
# ---------------------------------------------------------------------------


def phase_shifted_half_bridge_stresses(
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> ComponentStresses:
    where = "phase_shifted_half_bridge spec"
    _require_positive(spec, ("inputVoltage", "minimum"), where)  # validate completeness
    vmax = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where, op_index=op_index)
    n = _turns_ratio(spec, where)
    ripple = _ripple_pp(spec)
    vds = vmax
    ipri = iout / n * (1.0 + ripple / 2.0)
    vr = vout + vmax / n
    return ComponentStresses(
        vds_stress=vds,
        id_stress=ipri,
        vr_stress=vr,
        if_avg_stress=iout,
        v_working=vout,
        i_ripple=iout * ripple / (2.0 * 3.0**0.5),
    )


# ---------------------------------------------------------------------------
# Weinberg (push-pull primary) — Vds = 2×Vin
# ---------------------------------------------------------------------------


def weinberg_stresses(
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> ComponentStresses:
    where = "weinberg spec"
    _require_positive(spec, ("inputVoltage", "minimum"), where)  # validate completeness
    vmax = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where, op_index=op_index)
    n = _turns_ratio(spec, where)
    ripple = _ripple_pp(spec)
    vds = 2.0 * vmax  # push-pull primary
    ipri = iout / n * (1.0 + ripple / 2.0)
    vr = 2.0 * vout
    return ComponentStresses(
        vds_stress=vds,
        id_stress=ipri,
        vr_stress=vr,
        if_avg_stress=iout,
        v_working=vout,
        i_ripple=iout * ripple / (2.0 * 3.0**0.5),
    )


# ---------------------------------------------------------------------------
# LLC resonant half-bridge — Vds ≈ Vin
# ---------------------------------------------------------------------------
#
# Half-bridge: each FET sees Vin. Resonant tank means near-sinusoidal
# current; peak ≈ π/2 × Iout/n for fundamental approximation.
# No rectifier diode stress if synchronous rectification; use diode
# stress for legacy non-SR designs.


def llc_stresses(
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> ComponentStresses:
    where = "llc spec"
    _require_positive(spec, ("inputVoltage", "minimum"), where)  # validate completeness
    vmax = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where, op_index=op_index)
    n = _turns_ratio(spec, where)
    vds = vmax  # half-bridge: each switch sees Vin
    # Resonant sinusoidal peak ≈ π/2 × average
    ipri_pk = (math.pi / 2.0) * iout / n
    # Secondary rectifier (full-bridge or centre-tapped)
    vr = 2.0 * vout
    return ComponentStresses(
        vds_stress=vds,
        id_stress=ipri_pk,
        vr_stress=vr,
        if_avg_stress=iout,
        v_working=vout,
        # Resonant: low ripple on output cap
        i_ripple=0.05 * iout,
    )


# ---------------------------------------------------------------------------
# CLLC resonant (bidirectional LLC) — same half-bridge stress model
# ---------------------------------------------------------------------------


def cllc_stresses(
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> ComponentStresses:
    where = "cllc spec"
    _require_positive(spec, ("inputVoltage", "minimum"), where)  # validate completeness
    vmax = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where, op_index=op_index)
    n = _turns_ratio(spec, where)
    vds = vmax
    ipri_pk = (math.pi / 2.0) * iout / n
    vr = 2.0 * vout
    return ComponentStresses(
        vds_stress=vds,
        id_stress=ipri_pk,
        vr_stress=vr,
        if_avg_stress=iout,
        v_working=vout,
        i_ripple=0.05 * iout,
    )


# ---------------------------------------------------------------------------
# CLLLC resonant — same half-bridge stress model as LLC/CLLC
# ---------------------------------------------------------------------------


def clllc_stresses(
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> ComponentStresses:
    where = "clllc spec"
    _require_positive(spec, ("inputVoltage", "minimum"), where)  # validate completeness
    vmax = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where, op_index=op_index)
    n = _turns_ratio(spec, where)
    vds = vmax
    ipri_pk = (math.pi / 2.0) * iout / n
    vr = 2.0 * vout
    return ComponentStresses(
        vds_stress=vds,
        id_stress=ipri_pk,
        vr_stress=vr,
        if_avg_stress=iout,
        v_working=vout,
        i_ripple=0.05 * iout,
    )


# ---------------------------------------------------------------------------
# Series resonant converter (SRC) — same half-bridge stress model as LLC
# ---------------------------------------------------------------------------
#
# SRC shares LLC's half-bridge switching cell: each primary FET sees Vin
# (Vds = Vmax), the resonant tank carries near-sinusoidal current so the
# primary peak ≈ π/2 × Iout/n, and the per-rail full-bridge diode
# rectifier blocks ≈ 2·Vout. (SRC has no Lm output branch, so there is no
# additional magnetizing-current term to add to the switch stress — the
# FHA-reflected load current dominates.)


def series_resonant_stresses(
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> ComponentStresses:
    where = "series_resonant spec"
    _require_positive(spec, ("inputVoltage", "minimum"), where)  # validate completeness
    vmax = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where, op_index=op_index)
    n = _turns_ratio(spec, where)
    vds = vmax  # half-bridge: each switch sees Vin
    ipri_pk = (math.pi / 2.0) * iout / n
    vr = 2.0 * vout  # full-bridge diode rectifier reverse blocking
    return ComponentStresses(
        vds_stress=vds,
        id_stress=ipri_pk,
        vr_stress=vr,
        if_avg_stress=iout,
        v_working=vout,
        # Resonant: low ripple on output cap
        i_ripple=0.05 * iout,
    )


# ---------------------------------------------------------------------------
# Dual active bridge (DAB) — Vds = Vin (primary), full bridge both sides
# ---------------------------------------------------------------------------


def dual_active_bridge_stresses(
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> ComponentStresses:
    where = "dual_active_bridge spec"
    _require_positive(spec, ("inputVoltage", "minimum"), where)  # validate completeness
    vmax = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where, op_index=op_index)
    n = _turns_ratio(spec, where)
    ripple = _ripple_pp(spec)
    vds = vmax  # full bridge: each primary switch sees Vin
    ipri = iout / n * (1.0 + ripple / 2.0)
    # Secondary bridge: synchronous rectification, no diode
    return ComponentStresses(
        vds_stress=vds,
        id_stress=ipri,
        vr_stress=None,  # active bridge — synchronous rectification
        if_avg_stress=None,
        v_working=vout,
        i_ripple=iout * ripple / (2.0 * 3.0**0.5),
    )


# ---------------------------------------------------------------------------
# Isolated buck — half-bridge primary, Vds = Vin
# ---------------------------------------------------------------------------


def isolated_buck_stresses(
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> ComponentStresses:
    """Stresses for the isolated buck (flybuck).

    The flybuck is a *synchronous buck* on the primary winding of the
    coupled inductor T1, with one or more magnetically-isolated secondary
    rails rectified open-loop. The primary winding IS the buck inductor —
    it carries its own buck load (``Iout_pri`` = outputCurrents[0]) PLUS
    every reflected secondary current (``Iout_i / N_i``), NOT a small
    reflected-only current like a flyback secondary.

      * Q1/Q2 see Vds ≈ Vin_max (half-bridge across the bus).
      * I_L_pri (avg) = Iout_pri + Σ_i Iout_i / N_i.
      * Id_stress = I_L_pri · (1 + ripple/2)  (peak primary winding current).
      * Each secondary diode i blocks Vout_i + Vin_max/N_i and carries
        Iout_i; the worst-case over rails sizes vr_stress / if_avg_stress.
    """
    where = "isolated_buck spec"
    _require_positive(spec, ("inputVoltage", "minimum"), where)  # validate completeness
    vmax = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout_pri = _vout_iout(spec, where, op_index=op_index)
    ripple = _ripple_pp(spec)

    op = _op_at(spec, op_index, where)
    iouts = op.get("outputCurrents") or []
    vouts = op.get("outputVoltages") or []
    ratios = spec.get("desiredTurnsRatios") or []
    # Fall back to the single-ratio helper when desiredTurnsRatios is absent
    # (single-secondary specs that only carry one ratio).
    n0 = _turns_ratio(spec, where)

    # Primary winding (= buck inductor) average current: own load plus
    # every reflected secondary current.
    i_l_pri = float(iout_pri)
    vr_worst = vout + vmax / n0
    if_worst = float(iout_pri)
    for i in range(1, len(iouts)):
        n_i = float(ratios[i - 1]) if (i - 1) < len(ratios) else n0
        if n_i <= 0:
            n_i = n0
        i_sec = float(iouts[i])
        i_l_pri += i_sec / n_i
        v_sec = float(vouts[i]) if i < len(vouts) else vout
        vr_worst = max(vr_worst, v_sec + vmax / n_i)
        if_worst = max(if_worst, i_sec)

    vds = vmax
    ipri_pk = i_l_pri * (1.0 + ripple / 2.0)
    return ComponentStresses(
        vds_stress=vds,
        id_stress=ipri_pk,
        vr_stress=vr_worst,
        if_avg_stress=if_worst,
        v_working=vout,
        i_ripple=i_l_pri * ripple / (2.0 * 3.0**0.5),
    )


# ---------------------------------------------------------------------------
# Isolated buck-boost — single-switch flyback-family, Vds = Vin + n*Vout
# ---------------------------------------------------------------------------
#
# Same stress profile as flyback but without the mag-inductance-based
# ipeak formula (that belongs to flyback's extract enrichment). Uses
# energy-balance peak current instead.


def isolated_buck_boost_stresses(
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> ComponentStresses:
    where = "isolated_buck_boost spec"
    vmin = _require_positive(spec, ("inputVoltage", "minimum"), where)
    vmax = _require_positive(spec, ("inputVoltage", "maximum"), where)
    vout, iout = _vout_iout(spec, where, op_index=op_index)
    n = _turns_ratio(spec, where)
    d_max = _duty_max(spec, where)
    ripple = _ripple_pp(spec)
    vds = vmax + n * vout
    # Primary average current at worst case (Vin_min)
    ipri_avg = iout * vout / (vmin * d_max)
    ipri_pk = ipri_avg * (1.0 + ripple / 2.0)
    vr = vout + vmax / n
    if_avg = iout / (1.0 - d_max)
    return ComponentStresses(
        vds_stress=vds,
        id_stress=ipri_pk,
        vr_stress=vr,
        if_avg_stress=if_avg,
        v_working=vout,
        # Output-cap RMS ripple: diode-fed cap over the (1-D) off fraction →
        # I_Crms = Iout*sqrt(D/(1-D)) (same as boost/flyback). The reciprocal
        # sqrt((1-D)/D) undersizes the cap for D > 0.5.
        i_ripple=iout * (d_max / (1.0 - d_max)) ** 0.5,
    )


# Per-topology dispatch. Extend as topologies onboard their stencils.
_DERIVERS: dict[str, Any] = {
    "buck": buck_stresses,
    "boost": boost_stresses,
    "cuk": cuk_stresses,
    "flyback": flyback_stresses,
    "sepic": sepic_stresses,
    "zeta": zeta_stresses,
    "four_switch_buck_boost": four_switch_buck_boost_stresses,
    "single_switch_forward": single_switch_forward_stresses,
    "two_switch_forward": two_switch_forward_stresses,
    "active_clamp_forward": active_clamp_forward_stresses,
    "push_pull": push_pull_stresses,
    "asymmetric_half_bridge": asymmetric_half_bridge_stresses,
    "phase_shifted_full_bridge": phase_shifted_full_bridge_stresses,
    "phase_shifted_half_bridge": phase_shifted_half_bridge_stresses,
    "weinberg": weinberg_stresses,
    "llc": llc_stresses,
    "cllc": cllc_stresses,
    "clllc": clllc_stresses,
    "series_resonant": series_resonant_stresses,
    "dual_active_bridge": dual_active_bridge_stresses,
    "isolated_buck": isolated_buck_stresses,
    "isolated_buck_boost": isolated_buck_boost_stresses,
    "power_factor_correction": power_factor_correction_stresses,
    "vienna": vienna_stresses,
}


def _worst_case_across_ops(
    fn: Any,
    spec: Mapping[str, Any],
) -> ComponentStresses:
    """Sweep every operatingPoints[*] and return the element-wise
    worst-case ``ComponentStresses`` across all ops.

    "Worst" for each field is the higher of the two (more stressful):
    Vds/Id/Vr/If/V_working/I_ripple are all upper-bound stresses, so
    ``max`` is the sizing-critical value. ``None`` fields propagate as
    None (no stress declared at any op = no stress to size against).
    """
    n_ops = _num_operating_points(spec)
    if n_ops == 0:
        return fn(spec, op_index=0)  # will raise StressDerivationError

    worst = fn(spec, op_index=0)
    for i in range(1, n_ops):
        try:
            s = fn(spec, op_index=i)
        except StressDerivationError:
            continue  # skip malformed ops; first op was valid
        worst = ComponentStresses(
            vds_stress=_max_or_none(worst.vds_stress, s.vds_stress),
            id_stress=_max_or_none(worst.id_stress, s.id_stress),
            vr_stress=_max_or_none(worst.vr_stress, s.vr_stress),
            if_avg_stress=_max_or_none(worst.if_avg_stress, s.if_avg_stress),
            v_working=_max_or_none(worst.v_working, s.v_working),
            i_ripple=_max_or_none(worst.i_ripple, s.i_ripple),
        )
    return worst


def _max_or_none(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def derive_stresses(topology: str, spec: Mapping[str, Any]) -> ComponentStresses | None:
    """Return worst-case ``ComponentStresses`` across every
    operatingPoints[*] for ``topology``. Returns ``None`` when no
    per-topology deriver is registered (downstream code treats None as
    "skip stress stamping — the realism gate will mark voltage-derating
    checks UNAVAILABLE").

    The selector + analyst use this single worst-case value to size
    components conservatively; per-op stresses and losses are available
    via :func:`derive_stresses_per_op` for callers (e.g. the realism
    gate sweep) that need them per operating point.
    """
    fn = _DERIVERS.get(topology)
    if fn is None:
        return None
    return _worst_case_across_ops(fn, spec)


def derive_stresses_per_op(
    topology: str,
    spec: Mapping[str, Any],
) -> list[ComponentStresses] | None:
    """Return one ``ComponentStresses`` per operating point.

    Used by the analyst's per-op loss budget and the realism gate's
    per-op simulation_results stamping. Returns ``None`` when the
    topology has no registered deriver.
    """
    fn = _DERIVERS.get(topology)
    if fn is None:
        return None
    n_ops = _num_operating_points(spec)
    if n_ops == 0:
        # Single fallback call so error message paths still trigger.
        return [fn(spec, op_index=0)]
    return [fn(spec, op_index=i) for i in range(n_ops)]


__all__ = [
    "ComponentStresses",
    "StressDerivationError",
    "active_clamp_forward_stresses",
    "asymmetric_half_bridge_stresses",
    "boost_stresses",
    "buck_stresses",
    "cllc_stresses",
    "clllc_stresses",
    "cuk_stresses",
    "derive_stresses",
    "derive_stresses_per_op",
    "dual_active_bridge_stresses",
    "flyback_stresses",
    "four_switch_buck_boost_stresses",
    "isolated_buck_boost_stresses",
    "isolated_buck_stresses",
    "llc_stresses",
    "phase_shifted_full_bridge_stresses",
    "phase_shifted_half_bridge_stresses",
    "power_factor_correction_stresses",
    "push_pull_stresses",
    "sepic_stresses",
    "single_switch_forward_stresses",
    "two_switch_forward_stresses",
    "vienna_stresses",
    "weinberg_stresses",
    "zeta_stresses",
]

def derive_stress_by_ref(
    topology: str,
    spec: Mapping[str, Any],
    bom: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Map a converter DESIGN's operating point to per-component electrical
    stress, keyed by ref_des — the "start from the reference design, not a bare
    BOM" path. Each BOM row is stamped with the worst-case V/I its class sees at
    the design's operating point (from :func:`derive_stresses`), so the
    cross-reference gates can judge a substitute's adequacy even when the
    ORIGINAL has no MPN — resolving the requirement from the circuit the way an
    FAE reads it off the schematic, instead of deferring to "unverified".

    Returns ``{ref_des: SimDerivedStress}``. Empty when the topology has no
    deriver or the spec is incomplete (fail-soft: the pipeline then runs
    stress-free, exactly as a bare-BOM crossref does).
    """
    from heaviside.pipeline.crossref import SimDerivedStress

    try:
        cs = derive_stresses(topology, spec)
    except StressDerivationError:
        return {}
    if cs is None:
        return {}
    try:
        _vout, iout = _vout_iout(spec, "design")
    except StressDerivationError:
        iout = None
    l_req = required_inductance(topology, spec)

    out: dict[str, Any] = {}
    for comp in bom:
        ref = str(comp.get("ref_des") or "").strip()
        cat = str(comp.get("component_type") or comp.get("category") or "").strip()
        if not ref:
            continue
        kw: dict[str, Any] = {"ref_des": ref, "role": str(comp.get("role") or "")}
        if cat == "mosfet":
            kw.update(v_peak=cs.vds_stress, i_peak=cs.id_stress)
        elif cat == "diode":
            kw.update(v_peak=cs.vr_stress, i_avg=cs.if_avg_stress)
        elif cat == "capacitor":
            kw.update(v_peak=cs.v_working, v_dc=cs.v_working, i_rms=cs.i_ripple)
        elif cat in ("magnetic", "inductor", "chipBead"):
            # The switching inductor carries the switch peak current (buck: the
            # high-side FET and L share the same peak); RMS ≈ the DC output.
            kw.update(i_peak=cs.id_stress)
            if iout is not None:
                kw.update(i_rms=iout)
            if l_req is not None:
                kw.update(l_required=l_req)
        else:
            continue
        out[ref] = SimDerivedStress(**kw)
    return out
