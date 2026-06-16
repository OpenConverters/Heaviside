"""resonant_freq — switching frequency for resonant converters (master-plan B6).

For a hard-switched converter, fsw is the argmin of total loss (stage
``frequency_sweep``). For a *resonant* converter (LLC / SRC / CLLC / CLLLC)
that is the WRONG model: fsw is not a loss free-variable, it is set by the
**tank gain law** — the frequency at which the resonant tank delivers the
required voltage gain. Running a loss argmin here would push fsw to the EMI
ceiling (switching loss ≈ 0 under ZVS, so "lower loss" always means "higher
fsw"), which is exactly the runaway this stage exists to prevent.

So:

* fsw comes from the gain law within MKF's ``[minSwitchingFrequency,
  maxSwitchingFrequency]`` window (the window ``converter_spec_build`` centres
  on the resonant frequency ``fr = sqrt(fmin·fmax)``).
* switching loss is ZVS ≈ 0 (correct — soft switching), so it is NOT swept.
* magnetic loss still comes from MKF (read off the MAS, same as everywhere).

The gain law is the standard First-Harmonic-Approximation (FHA) LLC model. The
tank (Lr, Cr, Lm, Rac) comes from MKF's resonant design when available; without
a tank the unity-gain resonant point fsw = fr is returned (correct for an
LLC/SRC operating at resonance, M = 1 independent of Q).
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_RESONANT_FAMILIES = frozenset({"resonant"})


class ResonantFrequencyError(ValueError):
    """The required gain cannot be met inside MKF's fsw window, or the spec
    lacks the window needed to place the resonant frequency. Raised loudly —
    never clamps fsw to a window edge and pretends the gain is met."""


@dataclass(frozen=True, slots=True)
class ResonantTank:
    """The resonant tank MKF designs (SI units). ``lm`` is the magnetising
    inductance, ``lr``/``cr`` the series resonant elements, ``rac`` the
    FHA-reflected load resistance."""

    lr_h: float
    cr_f: float
    lm_h: float
    rac_ohm: float

    @property
    def fr_hz(self) -> float:
        """Series resonant frequency 1/(2π√(Lr·Cr))."""
        return 1.0 / (2.0 * math.pi * math.sqrt(self.lr_h * self.cr_f))

    @property
    def m_ratio(self) -> float:
        """Inductance ratio m = (Lr + Lm) / Lr (>1)."""
        return (self.lr_h + self.lm_h) / self.lr_h

    @property
    def q_factor(self) -> float:
        """Quality factor Q = √(Lr/Cr) / Rac."""
        return math.sqrt(self.lr_h / self.cr_f) / self.rac_ohm


@dataclass(frozen=True, slots=True)
class ResonantOperatingPoint:
    fsw_hz: float
    fr_hz: float
    gain: float  # the FHA voltage gain at fsw (1.0 at resonance)
    in_window: bool
    switching_loss_model: str = "ZVS"  # P_sw ≈ 0; NOT a sweep variable
    switching_loss_w: float = 0.0


def is_resonant(topology: str) -> bool:
    """True for the frequency-modulated resonant family (uses this stage, not
    the loss sweep)."""
    try:
        from heaviside.topologies import get

        return get(topology).family in _RESONANT_FAMILIES
    except Exception:
        return False


def fha_gain_llc(fn: float, m: float, q: float) -> float:
    """First-Harmonic-Approximation LLC voltage gain magnitude.

    ``fn = fsw / fr`` (normalised frequency), ``m = (Lr+Lm)/Lr``, ``q`` the
    quality factor. At ``fn = 1`` this is exactly 1.0 for any Q (the defining
    property of the series resonant frequency). Above resonance the gain falls
    monotonically (the buck region LLC converters operate in)."""
    if fn <= 0:
        raise ResonantFrequencyError(f"normalised frequency must be > 0, got {fn}")
    fn2 = fn * fn
    num = fn2 * (m - 1.0)
    real = m * fn2 - 1.0
    imag = fn * (fn2 - 1.0) * (m - 1.0) * q
    denom = math.sqrt(real * real + imag * imag)
    return num / denom


def _window(spec: Mapping[str, Any]) -> tuple[float, float]:
    lo = spec.get("minSwitchingFrequency")
    hi = spec.get("maxSwitchingFrequency")
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)) or not (0 < lo < hi):
        raise ResonantFrequencyError(
            "spec needs minSwitchingFrequency < maxSwitchingFrequency to place the "
            "resonant frequency (converter_spec_build sets these for the resonant "
            f"family); got min={lo!r} max={hi!r}"
        )
    return float(lo), float(hi)


def resonant_frequency(spec: Mapping[str, Any]) -> float:
    """The tank resonant frequency MKF designs to — the geometric mean of its
    fsw window (``sqrt(fmin·fmax)``), which ``converter_spec_build`` centres on
    the design's nominal operating frequency."""
    lo, hi = _window(spec)
    return math.sqrt(lo * hi)


def select_resonant_fsw(
    spec: Mapping[str, Any],
    *,
    tank: ResonantTank | None = None,
    required_gain: float | None = None,
) -> ResonantOperatingPoint:
    """Pick the operating fsw for a resonant converter from the gain law.

    * ``tank`` + ``required_gain`` given: solve the FHA gain law for fsw in the
      above-resonance (monotone) region and validate it lies in MKF's window.
    * otherwise: the unity-gain resonant point fsw = fr (M = 1).

    Raises :class:`ResonantFrequencyError` if the required gain is unachievable
    inside the window (never clamps to an edge)."""
    lo, hi = _window(spec)
    fr = resonant_frequency(spec)

    if tank is None or required_gain is None:
        fsw = fr
        gain = 1.0
    else:
        if required_gain <= 0:
            raise ResonantFrequencyError(f"required_gain must be > 0, got {required_gain}")
        m, q = tank.m_ratio, tank.q_factor
        fr_tank = tank.fr_hz
        # Gain = 1 at fn=1; for a step-down (gain<1) operate above resonance,
        # where FHA gain is monotone decreasing — bisect fn in [1, hi/fr_tank].
        # For gain>1 operate below resonance (boost region), monotone in
        # [lo/fr_tank, 1].
        if required_gain <= 1.0:
            fn_a, fn_b = 1.0, hi / fr_tank
        else:
            fn_a, fn_b = lo / fr_tank, 1.0
        ga, gb = fha_gain_llc(fn_a, m, q), fha_gain_llc(fn_b, m, q)
        if not (min(ga, gb) <= required_gain <= max(ga, gb)):
            raise ResonantFrequencyError(
                f"required gain {required_gain:.3f} unachievable in window "
                f"[{lo/1e3:.0f},{hi/1e3:.0f}]kHz (tank gain spans "
                f"[{min(ga,gb):.3f},{max(ga,gb):.3f}] with m={m:.2f} Q={q:.2f}); "
                f"re-design the tank, do not clamp."
            )
        # bisection on the monotone branch
        for _ in range(60):
            fn_mid = 0.5 * (fn_a + fn_b)
            gm = fha_gain_llc(fn_mid, m, q)
            # keep the bracket that still contains required_gain
            if (ga - required_gain) * (gm - required_gain) <= 0:
                fn_b, gb = fn_mid, gm
            else:
                fn_a, ga = fn_mid, gm
        fn = 0.5 * (fn_a + fn_b)
        fsw = fn * fr_tank
        gain = fha_gain_llc(fn, m, q)

    in_window = lo <= fsw <= hi
    if not in_window:
        raise ResonantFrequencyError(
            f"resonant fsw {fsw/1e3:.1f}kHz fell outside MKF's window "
            f"[{lo/1e3:.0f},{hi/1e3:.0f}]kHz — the gain law and the window "
            f"disagree; re-derive the window or the tank (no clamp)."
        )
    return ResonantOperatingPoint(fsw_hz=fsw, fr_hz=fr, gain=gain, in_window=True)
