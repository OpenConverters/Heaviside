"""Utility-curve scoring for cross-reference parameter comparison (crossref v2).

Why this module exists
----------------------
The declarative :mod:`param_check` engine gives every secondary parameter a
*binary* verdict — pass / warn / fail against a single tolerance step. That is
the right shape for a gate, but it says nothing about *how good* a passing part
is, and it has no notion of the primary electrical value (R / L / C / Z) at all.
Two real failures followed from that gap:

  * a 330 nH inductor was accepted as a "partial" substitute for a 1.5 µH
    original — the primary value was never compared to anything that could
    reject it (it only surfaced as descriptive prose);
  * a 12.4 A-Isat part scored identically to a 3.5 A one against a 3.25 A
    requirement — massive over-dimensioning was invisible, so a bulky, costly,
    higher-parasitic part could win on a coin toss.

This module adds the missing layer: a *continuous penalty* per parameter on top
of the discrete verdict. The penalty encodes engineering preference —

  * closest-to-target wins when there is no design context (proximity),
  * a small deficit on a critical rating is a soft, compensable penalty
    (a near-miss, not a rejection) until it crosses a hard physics gate,
  * gross over-dimensioning is penalised with **diminishing returns** — 2×
    costs a little, 10× a little more, but a 10× part can never beat an
    otherwise-equal 1.2× part — so the ranker prefers a right-sized substitute.

The four modes mirror the engineering direction of every real parameter:

  ``EXACT``          value must equal the original (dielectric class, pitch,
                     crystal frequency) — handled by param_check, not here.
  ``HIGHER_BETTER``  substitute should be ≥ original (V rating, Isat, Irms,
                     power, Vrrm, SRF, impedance): surplus good (diminishing),
                     deficit bad (steep, gated).
  ``LOWER_BETTER``   substitute should be ≤ original (DCR, ESR, Rds(on), Qg,
                     Qrr, trr, TCR): mirror of higher-better.
  ``RANGE``          value should sit in a window and as close to nominal as
                     possible (the primary passive value; a design-derived L/C
                     window): zero penalty in the tight band, rising outside,
                     failing past the accept band.

Everything is computed in log-ratio space ``x = ln(s / o)`` so the curves are
unit-free, symmetric in the multiplicative sense (½× and 2× are equidistant),
and naturally compress large ratios. Penalties are pure numbers (0 = ideal);
callers combine them with their own weights. Verdicts are the same four strings
param_check already uses, so the two engines interoperate.

No fallbacks: a value that cannot be parsed yields ``None`` from the caller and
an ``UNVERIFIED`` verdict here — never a silent pass (per the house rule).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

# ── Verdicts (shared vocabulary with param_check) ────────────────────────────
PASS = "pass"
WARN = "warn"
FAIL = "fail"
UNVERIFIED = "unverified"


class Mode(str, Enum):
    """Comparison direction for a parameter."""

    EXACT = "exact"
    HIGHER_BETTER = "higher_better"
    LOWER_BETTER = "lower_better"
    RANGE = "range"


# ── Curve tuning constants ───────────────────────────────────────────────────
# Over-dimensioning (surplus on a HIGHER_BETTER rating, or being *below* target
# on a LOWER_BETTER parasitic): a gentle, saturating penalty that is CONCAVE in
# log-ratio (√ of the surplus) so each successive doubling adds *less* than the
# previous one — genuine diminishing returns — and the cap freezes it past ~8×,
# so a hugely-oversized part is "a bit worse", never "infinitely worse". It must
# still beat *nothing*, but it always loses to a right-sized part.
_K_OVER = 0.6
_X_OVER_CAP = math.log(8.0)  # surplus (in log-ratio) at which the penalty freezes

# Boundary epsilon (log space): absorbs floating-point noise on window edges so
# a ratio that is 0.8 in exact arithmetic (e.g. 1.2e-6 / 1.5e-6 == 0.79999…)
# counts as inside an 0.8× accept bound rather than one ULP outside it.
_EDGE_EPS = 1e-9


def _over_penalty(surplus: float) -> float:
    """Concave, capped over-dimensioning penalty for a log-ratio ``surplus``≥0."""
    return _K_OVER * math.sqrt(min(surplus, _X_OVER_CAP))


def over_dimensioning_penalty(required: float | None, actual: float | None, *, weight: float = 1.0) -> float:
    """Ranking penalty (≥0) for a rating that EXCEEDS its requirement, with
    diminishing returns (concave, capped): 2× costs a little, 10× a little more,
    but the marginal cost shrinks and freezes past ~8×. Returns 0 when the part
    is at-or-under the requirement or an input is missing/invalid.

    Use as a SMALL tie-breaker in candidate ranking so that, among candidates
    that all meet a requirement, the right-sized one outranks a grossly-
    oversized (bulkier / costlier / worse-parasitics) one — never large enough
    to override value-proximity or footprint fit.
    """
    # Delegated to Kelvin (the deterministic engine); golden-parity-locked.
    from heaviside.pipeline._kelvin_primitives import (
        over_dimensioning_penalty as _kv_over,
    )

    return _kv_over(required, actual, weight)

# Deficit (a HIGHER_BETTER rating that falls short, or a LOWER_BETTER parasitic
# that overshoots): a steep exponential. A few percent short is a small,
# compensable penalty (WARN); past the gate it is a hard FAIL. The exponential
# means "a little short" stays cheap while "a lot short" explodes.
_K_DEF = 4.0
_S_DEF = 3.0

# RANGE proximity: penalty per unit of log-distance from the nearest tight
# bound, inside the accept window. Outside the accept window it is a FAIL and
# the penalty grows with the steeper deficit weight so a far-off value is
# clearly the worst option.
_K_PROX = 2.0


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """Continuous penalty + discrete verdict + human note for one comparison."""

    penalty: float  # 0 = ideal; larger = worse. Callers apply their own weight.
    verdict: str  # PASS / WARN / FAIL / UNVERIFIED
    note: str
    ratio: float | None = None  # substitute / original, when both are known


def _fmt(x: float, unit: str) -> str:
    return f"{x:g}{unit}"


def score_directional(
    original: float | None,
    substitute: float | None,
    mode: Mode,
    *,
    warn_factor: float,
    gate_factor: float,
    label: str = "value",
    unit: str = "",
) -> ScoreResult:
    """Score a HIGHER_BETTER or LOWER_BETTER parameter.

    ``warn_factor`` / ``gate_factor`` are multiples of the original marking the
    WARN and FAIL thresholds on the *deficient* side:

      * HIGHER_BETTER: PASS if s ≥ o; WARN if warn·o ≤ s < o; FAIL if s < gate·o
        (warn_factor, gate_factor < 1, e.g. 0.9 warn / 0.8 gate).
      * LOWER_BETTER : PASS if s ≤ o; WARN if o < s ≤ warn·o; FAIL if s > gate·o
        (warn_factor, gate_factor > 1, e.g. 1.5 warn / 2.0 gate).

    The surplus side always PASSes but accrues the diminishing over-dimensioning
    penalty, so the ranker can prefer a right-sized part.
    """
    if original is None and substitute is None:
        return ScoreResult(0.0, UNVERIFIED, f"{label}: not specified on either part")
    if substitute is None:
        return ScoreResult(0.0, UNVERIFIED, f"{label}: substitute value unknown")
    if original is None:
        return ScoreResult(0.0, UNVERIFIED, f"{label}: original value unknown")
    if original <= 0 or substitute <= 0:
        # Log-ratio is undefined; fall back to a direct compare with no penalty
        # gradient (these are ratings/parasitics — non-positive is unusual).
        ok = (substitute >= original) if mode is Mode.HIGHER_BETTER else (substitute <= original)
        return ScoreResult(
            0.0,
            PASS if ok else FAIL,
            f"{label}: {_fmt(substitute, unit)} vs {_fmt(original, unit)}",
        )

    ratio = substitute / original
    x = math.log(ratio)

    if mode is Mode.HIGHER_BETTER:
        surplus = x  # >0 means substitute exceeds requirement
    elif mode is Mode.LOWER_BETTER:
        surplus = -x  # <0 log-ratio (smaller substitute) is the "good" direction
    else:  # pragma: no cover - guarded by callers
        raise ValueError(f"score_directional does not handle mode {mode}")

    if surplus >= 0:
        # Meets or beats the requirement — PASS, with a capped over-dimensioning
        # penalty that grows with diminishing returns.
        penalty = _over_penalty(surplus)
        rel = "≥" if mode is Mode.HIGHER_BETTER else "≤"
        return ScoreResult(
            penalty,
            PASS,
            f"{label}: {_fmt(substitute, unit)} {rel} {_fmt(original, unit)}",
            ratio,
        )

    # Deficient side: steep exponential penalty, WARN then FAIL past the gate.
    deficit = -surplus  # positive magnitude of the shortfall in log space
    penalty = _K_DEF * (math.exp(deficit * _S_DEF) - 1.0)
    warn_edge = abs(math.log(warn_factor)) if warn_factor not in (None, 0) else 0.0
    gate_edge = abs(math.log(gate_factor)) if gate_factor not in (None, 0) else warn_edge
    if deficit <= warn_edge:
        verdict = WARN
        tail = "within margin"
    elif deficit <= gate_edge:
        verdict = WARN
        tail = "near the limit"
    else:
        verdict = FAIL
        tail = "beyond the allowed margin"
    cmp = "<" if mode is Mode.HIGHER_BETTER else ">"
    return ScoreResult(
        penalty,
        verdict,
        f"{label}: {_fmt(substitute, unit)} {cmp} {_fmt(original, unit)} ({tail})",
        ratio,
    )


def score_range(
    original: float | None,
    substitute: float | None,
    *,
    tight_lo: float,
    tight_hi: float,
    accept_lo: float,
    accept_hi: float,
    label: str = "value",
    unit: str = "",
) -> ScoreResult:
    """Score a RANGE / proximity parameter (the primary passive value).

    Windows are multiples of the original:

      * inside [tight_lo, tight_hi]  → PASS  (as good as the same value)
      * inside [accept_lo, accept_hi] but outside tight → WARN (deviates, but a
        defensible substitute — capped at 'partial')
      * outside [accept_lo, accept_hi] → FAIL (a different value, not this part:
        the 330 nH-for-1.5 µH case)

    The penalty is proportional to the log-distance from the nearest tight bound,
    so the closest in-window candidate always ranks first, and an out-of-window
    value carries the steep weight to sink it below every real option.
    """
    if original is None and substitute is None:
        return ScoreResult(0.0, UNVERIFIED, f"{label}: not specified on either part")
    if substitute is None:
        return ScoreResult(0.0, UNVERIFIED, f"{label}: substitute value unknown")
    if original is None:
        return ScoreResult(0.0, UNVERIFIED, f"{label}: original value unknown")
    if original <= 0 or substitute <= 0:
        return ScoreResult(
            0.0,
            PASS if substitute == original else FAIL,
            f"{label}: {_fmt(substitute, unit)} vs {_fmt(original, unit)}",
        )

    ratio = substitute / original
    x = math.log(ratio)
    lo_t, hi_t = math.log(tight_lo), math.log(tight_hi)
    lo_a, hi_a = math.log(accept_lo), math.log(accept_hi)

    # Distance outside the tight window (0 when inside it).
    if x < lo_t:
        dist_tight = lo_t - x
    elif x > hi_t:
        dist_tight = x - hi_t
    else:
        dist_tight = 0.0

    if (lo_a - _EDGE_EPS) <= x <= (hi_a + _EDGE_EPS):
        penalty = _K_PROX * dist_tight
        verdict = PASS if dist_tight <= _EDGE_EPS else WARN
        tail = "on target" if dist_tight <= _EDGE_EPS else "in-window, off nominal"
    else:
        # Outside the accept window — a different value. Penalise with the steep
        # deficit weight so it sinks below any in-window candidate.
        dist_accept = (lo_a - x) if x < lo_a else (x - hi_a)
        penalty = _K_PROX * dist_tight + _K_DEF * (math.exp(dist_accept * _S_DEF) - 1.0)
        verdict = FAIL
        tail = "out of range"
    return ScoreResult(
        penalty,
        verdict,
        f"{label}: {_fmt(substitute, unit)} vs {_fmt(original, unit)} ({tail})",
        ratio,
    )


# ── Primary-value specification per category ─────────────────────────────────
@dataclass(frozen=True, slots=True)
class PrimaryValueSpec:
    """How to compare the PRIMARY electrical value of a category.

    Windows are multipliers of the original value. ``mode`` is RANGE for the
    passives whose value is a two-sided target (R / L / C) and HIGHER_BETTER for
    a chip-bead's impedance (more suppression is acceptable, less is not).
    """

    category: str
    label: str
    unit: str
    mode: Mode
    # RANGE windows (multipliers of the original)
    tight_lo: float = 1.0
    tight_hi: float = 1.0
    accept_lo: float = 1.0
    accept_hi: float = 1.0
    # HIGHER/LOWER thresholds (multipliers) for the impedance case
    warn_factor: float = 0.9
    gate_factor: float = 0.8


# Defaults are documented engineering windows (manufacturer cross guides;
# Bourns' published "electrical specs within 10%" for inductors, resistor
# E-series spacing, capacitor bypass-tolerance practice). Tune here; the trap
# fixtures pin the behaviour.
PRIMARY_VALUE_SPECS: dict[str, PrimaryValueSpec] = {
    # Resistors: the value IS the part. Match near-exactly — a 39 Ω is not a
    # 47 Ω. ±1 % tight (E96), ±5 % accept (absorbs E24 rounding), fail beyond.
    "resistor": PrimaryValueSpec(
        "resistor", "Resistance", "Ω", Mode.RANGE,
        tight_lo=0.99, tight_hi=1.01, accept_lo=0.95, accept_hi=1.05,
    ),
    # Capacitors: asymmetric. A shortfall loses filtering/holdup (tight from
    # 0.9×, fail below 0.8×); a surplus is usually tolerable for bypass/bulk so
    # the accept ceiling is generous, but the RANGE penalty (and the ranker's
    # over-cap term) still prefer the closest value. Effective-C at DC bias is a
    # separate, stronger check (mlcc_bias_param) when an operating voltage is
    # known.
    "capacitor": PrimaryValueSpec(
        "capacitor", "Capacitance", "F", Mode.RANGE,
        tight_lo=0.90, tight_hi=1.50, accept_lo=0.80, accept_hi=4.00,
    ),
    # Inductors/transformers: L within ±10 % is a clean match; accept 0.8–1.25×
    # (Bourns' 10 % + headroom); fail outside — this is the band that turns the
    # 330 nH-for-1.5 µH pick (0.22×) into a hard no_substitute.
    "magnetic": PrimaryValueSpec(
        "magnetic", "Inductance", "H", Mode.RANGE,
        tight_lo=0.90, tight_hi=1.10, accept_lo=0.80, accept_hi=1.25,
    ),
    # Chip beads: impedance @ 100 MHz — more is acceptable, less is not.
    "chipBead": PrimaryValueSpec(
        "chipBead", "Z@100MHz", "Ω", Mode.HIGHER_BETTER,
        warn_factor=0.8, gate_factor=0.7,
    ),
}


def score_primary_value(
    category: str,
    original: float | None,
    substitute: float | None,
) -> ScoreResult | None:
    """Score the primary electrical value for a category, or None if the
    category has no primary-value spec (mosfet/diode/connector/analog/timeBase
    are matched on other axes). Values are SI base units.

    Returns ``UNVERIFIED`` (never a silent pass) when a value is missing — the
    caller decides how to treat an unverifiable primary value.
    """
    spec = PRIMARY_VALUE_SPECS.get(category)
    if spec is None:
        return None
    # The DECISION (verdict + penalty) is Kelvin's — the deterministic engine,
    # golden-parity-locked. The note is display glue kept in Python.
    from heaviside.pipeline._kelvin_primitives import score_primary_value as _kv

    d = _kv(category, original, substitute)
    if d is None:
        return None
    if original is None or substitute is None:
        note = f"{spec.label}: {'original' if original is None else 'substitute'} value not specified"
        ratio = None
    else:
        ratio = (substitute / original) if original else None
        note = f"{spec.label}: {_fmt(substitute, spec.unit)} vs {_fmt(original, spec.unit)}"
    return ScoreResult(penalty=d["penalty"], verdict=d["verdict"], note=note, ratio=ratio)
