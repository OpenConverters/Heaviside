"""Manufacturer-agnostic saturation-current normalization.

Datasheets quote a magnetic's saturation current at a *roll-off criterion* — the
percentage the inductance has dropped from its small-signal value (|ΔL/L|). But
vendors pick different criteria: Würth Elektronik commonly lists I_sat at 10 % AND
30 %, Coilcraft and Vishay at 20 %, others at 5 % or 40 %. Comparing one vendor's
I_sat@10 % against another's I_sat@20 % is apples-to-oranges and manufactures a
*false* shortfall (the FAE finding: a WE-MAPI's 2.5 A @10 % looked like a "38 %
shortfall" versus a Vishay 3.25 A @20 %, when at a matched 20 % criterion the
WE part actually delivers ~3.6 A and is adequate).

This module normalizes both sides to a common criterion before comparing. It is
strictly **manufacturer-agnostic**: every function operates on the *stated
roll-off criterion of each datapoint*, never on the manufacturer's name. Würth is
merely the first catalogue we backfill with multi-point data; Coilcraft / Vishay /
TDK / Murata feed the identical structure.

Input shape (mirrors the proposed MAS `saturationCurrents` array — a list of
points per part):

    [{"percent_drop": 10.0, "current": 2.5}, {"percent_drop": 30.0, "current": 4.7}]

A single legacy scalar with no stated basis is represented as one point with
``percent_drop=None`` (basis unknown) — such a part can only be compared with an
explicit "verify at matched criterion" caveat, never a hard pass/fail.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

# The canonical criterion we normalize to when comparing two parts. 20 % is the
# most common industry convention (Coilcraft/Vishay/TDK), so it minimizes
# extrapolation for the majority of originals. It is only an internal reference —
# both sides are converted to it, so the choice does not favour any vendor.
CANONICAL_PERCENT_DROP = 20.0

# Two I_sat values whose ratio is within this band could be explained by a
# difference in roll-off criterion alone (e.g. @10 % vs @30 % on the same part is
# typically ~1.5–1.9×). When bases are unknown/unmatched and the ratio sits inside
# the band, we must NOT hard-fail — we emit a caveat. Outside it, a shortfall is
# real regardless of criterion.
BASIS_UNCERTAINTY_BAND = 1.9


@dataclass(frozen=True)
class IsatPoint:
    percent_drop: float | None  # |ΔL/L| criterion in %, or None if basis unknown
    current: float  # amperes


def _coerce_points(points: Iterable[dict] | None) -> list[IsatPoint]:
    """Parse the multi-point list (or a legacy single scalar) into IsatPoints,
    dropping malformed entries. Sorted by percent_drop (known bases first)."""
    out: list[IsatPoint] = []
    for p in points or []:
        if not isinstance(p, dict):
            continue
        cur = p.get("current")
        if not isinstance(cur, (int, float)) or cur <= 0:
            continue
        pd = p.get("percent_drop")
        pd = float(pd) if isinstance(pd, (int, float)) and pd >= 0 else None
        out.append(IsatPoint(pd, float(cur)))
    out.sort(key=lambda x: (x.percent_drop is None, x.percent_drop or 0.0))
    return out


def isat_at_percent_drop(points: Iterable[dict] | None, target_pct: float) -> float | None:
    """Return the part's saturation current AT ``target_pct`` inductance drop,
    interpolating between the datasheet points and extrapolating conservatively
    at the ends. Returns None when the part has no usably-based points (only a
    basis-unknown scalar), so the caller must fall back to a caveat.

    I_sat is monotonically increasing in the drop criterion (a larger allowed
    inductance drop admits more current), so we interpolate linearly in
    (percent_drop, current) and clamp beyond the measured range rather than
    extrapolate a slope off the end (conservative: never invent headroom)."""
    pts = [p for p in _coerce_points(points) if p.percent_drop is not None]
    if not pts:
        return None
    # Exact hit.
    for p in pts:
        if abs(p.percent_drop - target_pct) < 1e-9:
            return p.current
    below = [p for p in pts if p.percent_drop < target_pct]
    above = [p for p in pts if p.percent_drop > target_pct]
    if below and above:
        lo, hi = below[-1], above[0]
        frac = (target_pct - lo.percent_drop) / (hi.percent_drop - lo.percent_drop)
        return lo.current + frac * (hi.current - lo.current)
    # Only points on one side: clamp to the nearest measured value (don't
    # extrapolate a divergent slope — under-claim rather than over-claim).
    return (below[-1] if below else above[0]).current


# Comparison verdicts (strings, to slot into the existing param-result shape).
ADEQUATE = "adequate"      # substitute meets/exceeds original at a matched criterion
SHORTFALL = "shortfall"    # substitute is below original by more than any basis diff explains
UNCERTAIN = "uncertain"    # bases can't be matched — verify at a common criterion (caveat)


@dataclass(frozen=True)
class IsatComparison:
    verdict: str
    orig_at: float | None  # original I_sat normalized to the criterion (A)
    sub_at: float | None   # substitute I_sat normalized to the criterion (A)
    percent_drop: float | None  # criterion the comparison was made at, if matched
    note: str


def compare_isat(
    orig_points: Iterable[dict] | None,
    sub_points: Iterable[dict] | None,
    *,
    canonical_pct: float = CANONICAL_PERCENT_DROP,
    margin: float = 1.0,
) -> IsatComparison:
    """Compare a substitute's saturation current against the original's, matched
    to a common roll-off criterion. Manufacturer-agnostic: reads only the stated
    per-point criteria.

    - Both sides have based points → normalize both to ``canonical_pct`` and
      compare directly: sub ≥ margin·orig → ADEQUATE, else SHORTFALL.
    - One/both sides lack a based point (legacy scalar) → we cannot match criteria.
      Compare the raw headline currents: only call SHORTFALL when the ratio is so
      low that no plausible basis difference (BASIS_UNCERTAINTY_BAND) could explain
      it; otherwise UNCERTAIN with a "verify at matched %-drop" caveat. This is the
      key anti-false-fail rule.
    """
    o_at = isat_at_percent_drop(orig_points, canonical_pct)
    s_at = isat_at_percent_drop(sub_points, canonical_pct)

    if o_at is not None and s_at is not None:
        if s_at >= margin * o_at:
            return IsatComparison(
                ADEQUATE, o_at, s_at, canonical_pct,
                f"I_sat {s_at:.2f} A vs {o_at:.2f} A, both at {canonical_pct:g}% "
                "inductance drop — substitute meets the original.",
            )
        return IsatComparison(
            SHORTFALL, o_at, s_at, canonical_pct,
            f"I_sat {s_at:.2f} A is below the original's {o_at:.2f} A at a matched "
            f"{canonical_pct:g}% inductance-drop criterion.",
        )

    # At least one side lacks a stated basis — fall back to raw headline currents.
    o_raw = _headline(orig_points)
    s_raw = _headline(sub_points)
    if o_raw is None or s_raw is None:
        return IsatComparison(
            UNCERTAIN, o_at, s_at, None,
            "I_sat could not be compared — a saturation-current figure is missing.",
        )
    ratio = s_raw / o_raw
    if ratio * BASIS_UNCERTAINTY_BAND < margin:
        # Even giving the substitute the full benefit of a basis mismatch, it is
        # still short — a real shortfall.
        return IsatComparison(
            SHORTFALL, None, None, None,
            f"I_sat {s_raw:.2f} A is far below the original's {o_raw:.2f} A — the "
            "gap is too large to be a roll-off-criterion difference.",
        )
    return IsatComparison(
        UNCERTAIN, None, None, None,
        f"I_sat {s_raw:.2f} A vs {o_raw:.2f} A compared at the datasheet headline; "
        "the roll-off criteria (|ΔL/L| %) may differ between the parts — verify at a "
        "matched inductance-drop criterion before use.",
    )


def _headline(points: Iterable[dict] | None) -> float | None:
    """The single most representative I_sat when bases can't be matched: prefer a
    based point nearest the canonical criterion, else the lone scalar."""
    pts = _coerce_points(points)
    if not pts:
        return None
    based = [p for p in pts if p.percent_drop is not None]
    if based:
        return min(based, key=lambda p: abs((p.percent_drop or 0) - CANONICAL_PERCENT_DROP)).current
    return pts[0].current


def points_from_inductance_curve(inductance_points: Iterable[dict] | None) -> list[dict]:
    """Derive saturation-current points (with a real %-drop basis) from an L-vs-DC-
    bias-current curve — the existing `inductancePoints` field (1,455 catalogue
    parts already carry it). For each measured current the roll-off criterion is
    ``percent_drop = (L0 - L)/L0 · 100`` where L0 is the small-signal inductance
    (the value at the lowest measured bias). Manufacturer-agnostic — it reads a
    physical curve, not a vendor convention. Returns [] when there's no usable
    saturating curve, so the caller falls back to saturationCurrents / the scalar.

    A curve may carry several temperatures; we use a single temperature (prefer
    ~25 °C, else the coolest — the datasheet-nominal) so the derived criteria are
    self-consistent."""
    raw = []
    for p in inductance_points or []:
        if not isinstance(p, dict):
            continue
        L = p.get("inductance")
        I = p.get("current")
        if isinstance(L, (int, float)) and L > 0 and isinstance(I, (int, float)) and I >= 0:
            raw.append((float(I), float(L), p.get("temperature")))
    if len(raw) < 2:
        return []
    temps = {t for _, _, t in raw if isinstance(t, (int, float))}
    if temps:
        pick = min(temps, key=lambda t: abs(t - 25.0))
        raw = [(i, l, t) for i, l, t in raw if t == pick]
    raw.sort(key=lambda x: x[0])  # by bias current
    l0 = raw[0][1]  # small-signal inductance = value at the lowest bias
    # A rising-then-flat signal inductor never saturates in range → l0 is the max.
    l0 = max(l0, max(l for _, l, _ in raw))
    out = []
    for i, l, _ in raw:
        drop = (l0 - l) / l0 * 100.0
        if drop > 0.5:  # in the saturation region (ignore measurement noise near L0)
            out.append({"percent_drop": drop, "current": i})
    return out


def resolve_saturation_points(electrical: dict | None) -> list[dict]:
    """Best available saturation-current representation for one datasheet
    electrical entry, in priority order (all manufacturer-agnostic):
      1. explicit ``saturationCurrents`` table (carries the stated %-drop basis);
      2. derived from the ``inductancePoints`` L-vs-I curve;
      3. the legacy ``saturationCurrentPeak`` scalar as a single basis-unknown
         point (percent_drop=None → the honest-caveat path).
    Returns [] when the part states no saturation current at all."""
    if not isinstance(electrical, dict):
        return []
    sc = electrical.get("saturationCurrents")
    if isinstance(sc, list) and sc:
        return [
            {"percent_drop": p.get("percentInductanceDrop"), "current": p.get("current"),
             "temperature": p.get("temperature")}
            for p in sc if isinstance(p, dict)
        ]
    derived = points_from_inductance_curve(electrical.get("inductancePoints"))
    if derived:
        return derived
    peak = electrical.get("saturationCurrentPeak")
    if isinstance(peak, (int, float)) and peak > 0:
        return [{"percent_drop": None, "current": float(peak)}]
    return []
