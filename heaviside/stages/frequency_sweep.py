"""frequency_sweep — choose the switching frequency FROM the magnetic
(master-plan stage C-hs, hard-switched topologies).

The thesis of the converter designer: ``fsw`` is not a free input, it is the
argument that minimises **total** loss (MKF magnetic loss + Heaviside
switching loss) for the magnetic MKF can build. As ``fsw`` moves, the
inductance moves with it (L ∝ 1/fsw at a fixed ripple budget), so the
magnetic is **re-derived by MKF inside the loop** — Heaviside never computes L.

Algorithm (engine-only, fully deterministic):

1. Pick a real Qg **envelope** FET from TAS for the seeded Vds / worst-OP Id
   class (lowest-Qg part meeting the ratings — a real part's charge, never a
   fabricated constant). Its Qg bounds the switching-loss surrogate used to
   *locate the basin*; the actually-picked FET re-costs the sweep later in the
   refinement loop (master-plan stage G).
2. **Coarse** log-spaced grid over ``[f_lo, f_hi]``: at each fsw, MKF re-derives
   L and returns a magnetic Pareto front; for each candidate compute worst-OP
   magnetic loss (MKF numbers), the switching-loss surrogate, and the
   saturation-margin feasibility (MKF isat). The cell value is the minimum
   feasible total loss at that fsw.
3. **Bracket** the minimum over feasible coarse cells, then **golden-section**
   refine inside the bracket.
4. **Re-rank** the final candidates on the full magnetic-loss model before the
   argmin. (On today's slow base-path seam MKF already returns full skin/
   proximity loss, so this is exact; when the fast base path lands — abt #11 —
   this step re-runs the bracketed top-K through the slow path to remove the
   fast-path bias. The structure is here either way.)

Raises :class:`FrequencySweepError` if NO ``(candidate, fsw)`` cell is feasible
across the whole grid — never clamps to an endpoint or fabricates a result.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# Golden ratio conjugate, for the section search.
_INV_PHI: float = (math.sqrt(5.0) - 1.0) / 2.0  # ≈ 0.6180339887


class FrequencySweepError(RuntimeError):
    """No feasible ``(magnetic, fsw)`` exists across the sweep, or the
    topology / catalogue cannot support a sweep at all.

    ``reasons`` records, per probed frequency, why no candidate was feasible
    (zero magnetics, all under saturation margin, missing loss numbers, …) so
    the caller can widen the band, loosen the margin, or queue a librarian
    fetch — rather than be handed a silent clamp.
    """

    def __init__(self, message: str, reasons: Mapping[float, str] | None = None) -> None:
        self.reasons = dict(reasons or {})
        if self.reasons:
            detail = "; ".join(
                f"{f/1e3:.1f}kHz: {why}" for f, why in sorted(self.reasons.items())
            )
            message = f"{message} Per-frequency: {detail}"
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class EnvelopeFet:
    """The real TAS MOSFET whose gate charge bounds the coarse-sweep
    switching-loss surrogate (NOT the final BOM part)."""

    mpn: str
    manufacturer: str
    qg_total_c: float
    vds_rated_v: float
    id_continuous_a: float
    technology: str


@dataclass(frozen=True, slots=True)
class SweepCandidate:
    """One feasible magnetic at the chosen frequency, with its loss split."""

    scoring: float
    magnetic_loss_w: float
    switching_loss_w: float
    total_loss_w: float
    isat_a: float
    ipeak_worst_a: float
    inductance_h: float
    core_shape: str
    core_material: str
    mas: dict[str, Any]


@dataclass(frozen=True, slots=True)
class FrequencyPoint:
    """A point on the loss-vs-frequency curve (for the UX artifact + tests)."""

    fsw_hz: float
    feasible: bool
    n_feasible: int
    n_candidates: int
    total_loss_w: float | None  # min feasible total at this fsw (None if infeasible)
    magnetic_loss_w: float | None
    switching_loss_w: float | None
    reason: str | None  # why infeasible, when it is


@dataclass(frozen=True, slots=True)
class FrequencySweepResult:
    """Outcome of the total-loss frequency sweep."""

    fsw_star_hz: float
    front: list[SweepCandidate]  # feasible candidates at fsw*, ascending total loss
    loss_curve: list[FrequencyPoint]  # ascending fsw
    envelope_fet: EnvelopeFet
    worst_vds_v: float
    worst_id_a: float
    min_isat_ratio: float
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def best(self) -> SweepCandidate:
        if not self.front:
            raise FrequencySweepError("FrequencySweepResult has an empty feasible front")
        return self.front[0]


# ---------------------------------------------------------------------------
# Envelope FET (real Qg bound for the switching-loss surrogate)
# ---------------------------------------------------------------------------


def select_envelope_fet(vds_min_v: float, id_min_a: float) -> EnvelopeFet:
    """Lowest-gate-charge real TAS MOSFET that carries the seeded Vds and the
    worst-OP drain current. Its Qg is a *real-part envelope* for the
    switching-loss surrogate — not a fabricated constant (house rule).

    Raises :class:`FrequencySweepError` if TAS has no MOSFET for the class —
    an honest "no switch exists for these ratings", surfaced not papered over.
    """
    from heaviside.catalogue.selector import (
        MosfetConstraints,
        MosfetTiebreaker,
        SelectionError,
        select_mosfet,
    )

    if not (isinstance(vds_min_v, (int, float)) and vds_min_v > 0):
        raise FrequencySweepError(f"envelope FET needs a positive Vds, got {vds_min_v!r}")
    if not (isinstance(id_min_a, (int, float)) and id_min_a > 0):
        raise FrequencySweepError(f"envelope FET needs a positive Id, got {id_min_a!r}")

    # rds_on / qg are unconstrained for the *envelope* pick (we want the
    # lowest-Qg part meeting the ratings) — inf, not a magic finite cap.
    constraints = MosfetConstraints(
        vds_min=float(vds_min_v),
        id_min=float(id_min_a),
        rds_on_max=math.inf,
        qg_max=math.inf,
    )
    try:
        sel = select_mosfet(constraints, tiebreaker=MosfetTiebreaker.LOWEST_QG)
    except SelectionError as exc:
        raise FrequencySweepError(
            f"no TAS MOSFET carries the switch class (Vds≥{vds_min_v:.0f} V, "
            f"Id≥{id_min_a:.2f} A) needed for the switching-loss surrogate: {exc}"
        ) from exc
    m = sel.chosen
    return EnvelopeFet(
        mpn=m.mpn,
        manufacturer=m.manufacturer,
        qg_total_c=float(m.qg_total),
        vds_rated_v=float(m.vds_rated),
        id_continuous_a=float(m.id_continuous),
        technology=m.technology,
    )


# ---------------------------------------------------------------------------
# Per-frequency evaluation
# ---------------------------------------------------------------------------


def _switching_loss_w(vds_v: float, id_a: float, qg_c: float, fsw_hz: float) -> float:
    """Surrogate switch loss P_sw = 0.5·Vds·Id·(Qg/Ig)·fsw (two transition
    edges/cycle), using the gate-drive current the analyst uses, so the sweep
    and the analyst agree. Qg is the real envelope part's charge."""
    from heaviside.pipeline.analyst import _GATE_DRIVE_CURRENT_A

    return 0.5 * float(vds_v) * float(id_a) * (float(qg_c) / _GATE_DRIVE_CURRENT_A) * float(fsw_hz)


def _evaluate_fsw(
    entry: Any,
    spec: Mapping[str, Any],
    fsw_hz: float,
    *,
    qg_env_c: float,
    vds_worst_v: float,
    id_worst_a: float,
    min_isat_ratio: float,
    max_candidates: int,
) -> tuple[list[SweepCandidate], int, str | None]:
    """Design the magnetic at ``fsw_hz`` and return ``(feasible_sorted,
    n_candidates, reason)``.

    ``feasible_sorted`` is the saturation-feasible candidates ascending by
    total loss; empty with a ``reason`` string when none are feasible.
    """
    from heaviside import bridge

    p_sw = _switching_loss_w(vds_worst_v, id_worst_a, qg_env_c, fsw_hz)

    # The worst-case peak current is evaluated AT this sweep frequency — for an
    # L-derived ripple computer (buck) Ipeak depends on fsw, so the spec the
    # ipeak/saturation check sees must carry fsw_hz on its operating points, not
    # whatever (possibly absent) fsw the base spec had. design_magnetics_at_fsw
    # already stamps fsw internally; mirror it for the ipeak computer.
    spec_at_fsw = dict(spec)
    spec_at_fsw["operatingPoints"] = [
        {**op, "switchingFrequency": float(fsw_hz)} if isinstance(op, Mapping) else op
        for op in (spec.get("operatingPoints") or [])
    ]

    try:
        cands = bridge.design_magnetics_at_fsw(
            entry, spec, fsw_hz, max_results=int(max_candidates)
        )
    except bridge.BridgeError as exc:
        # A topology that cannot be built at this fsw (zero magnetics, etc.) is
        # a per-frequency infeasibility, not a global bug — record and skip.
        return [], 0, f"MKF returned no magnetic: {str(exc)[:160]}"

    n = len(cands)
    feasible: list[SweepCandidate] = []
    skipped_unrankable = 0
    skipped_undermargin = 0
    for cand in cands:
        ipeak, l_guard = bridge._isat_margin_inputs(entry, spec_at_fsw, cand)
        if ipeak is None or l_guard is None:
            skipped_unrankable += 1
            continue
        isat = bridge._isat_from_mas(cand.magnetic, l_guard)
        if isat is None:
            skipped_unrankable += 1
            continue
        if isat < float(min_isat_ratio) * ipeak:
            skipped_undermargin += 1
            continue
        # Worst-OP magnetic loss from MKF's MAS (core + winding). The reader
        # wants the component-wrapped shape ({"data": mas}); cand.mas carries
        # outputs at the top level.
        from heaviside.pipeline.analyst import inductor_loss_worst_op

        loss = inductor_loss_worst_op({"data": cand.mas})
        core = loss.get("L1_core")
        dcr = loss.get("L1_dcr")
        if not isinstance(core, (int, float)) or not isinstance(dcr, (int, float)):
            skipped_unrankable += 1
            continue
        p_mag = float(core) + float(dcr)
        feasible.append(
            SweepCandidate(
                scoring=cand.scoring,
                magnetic_loss_w=p_mag,
                switching_loss_w=p_sw,
                total_loss_w=p_mag + p_sw,
                isat_a=float(isat),
                ipeak_worst_a=float(ipeak),
                inductance_h=float(l_guard),
                core_shape=cand.core_shape_name,
                core_material=cand.core_material_name,
                mas=cand.mas,
            )
        )

    feasible.sort(key=lambda c: c.total_loss_w)
    if feasible:
        return feasible, n, None
    bits = []
    if skipped_undermargin:
        bits.append(f"{skipped_undermargin} under {min_isat_ratio:g}× isat margin")
    if skipped_unrankable:
        bits.append(f"{skipped_unrankable} unrankable (no isat/loss)")
    reason = ", ".join(bits) if bits else "no candidates"
    return [], n, reason


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


def sweep(
    topology: str,
    spec: Mapping[str, Any],
    *,
    f_lo_hz: float = 50_000.0,
    f_hi_hz: float = 1_000_000.0,
    n_coarse: int = 7,
    golden_iters: int = 6,
    top_k: int = 5,
    min_isat_ratio: float = 1.2,
    max_candidates_per_fsw: int = 5,
) -> FrequencySweepResult:
    """Find the switching frequency that minimises worst-OP total loss for the
    magnetic MKF can build (hard-switched topologies only).

    Parameters mirror the master plan: a coarse log grid locates the basin,
    golden-section refines it, and the argmin runs on the FULL magnetic-loss
    model. ``spec`` MUST be a BASE-schema converter spec (built via
    :mod:`heaviside.stages.converter_spec_build`) — it carries
    ``currentRippleRatio`` and a seeded ``maximumDrainSourceVoltage`` but NO
    ``desiredInductance`` (MKF derives L per-fsw).

    Raises
    ------
    FrequencySweepError
        If the topology has no registered worst-case peak-current computer
        (we refuse to claim feasibility we can't check — master-plan trap #7),
        if the stress engine can't size the switch class, if TAS has no
        envelope FET, or if NO feasible ``(magnetic, fsw)`` exists.
    """
    from heaviside import bridge
    from heaviside.stages import stress_extract
    from heaviside.topologies import get as get_topology

    if not (0 < f_lo_hz < f_hi_hz):
        raise FrequencySweepError(
            f"frequency band must satisfy 0 < f_lo < f_hi, got [{f_lo_hz}, {f_hi_hz}]"
        )
    if n_coarse < 2:
        raise FrequencySweepError(f"n_coarse must be ≥ 2, got {n_coarse}")

    entry = get_topology(topology)

    # Trap #7: a topology we can't compute Ipeak_worst for must NOT pass the
    # saturation gate silently. Refuse the sweep loudly so a computer is added.
    if bridge._IPEAK_WORST.get(entry.name) is None:
        raise FrequencySweepError(
            f"no Ipeak_worst computer registered for topology {entry.name!r}; the "
            f"saturation feasibility gate cannot run, so the sweep would claim "
            f"feasibility it never checked. Register one in bridge._IPEAK_WORST "
            f"(master-plan B3) before sweeping this topology."
        )

    # Worst-case switch stress (across all OPs) sizes the envelope FET + the
    # switching-loss surrogate. The stress engine owns this physics.
    stresses = stress_extract.analytical(entry.name, spec)
    if stresses is None or stresses.vds_stress is None or stresses.id_stress is None:
        raise FrequencySweepError(
            f"stress engine could not size the switch for {entry.name!r} "
            f"(vds/id stress unavailable); cannot bound the switching-loss "
            f"surrogate. Spec may be missing an operating point or rating."
        )
    vds_worst = float(stresses.vds_stress)
    id_worst = float(stresses.id_stress)

    envelope = select_envelope_fet(vds_worst, id_worst)

    reasons: dict[float, str] = {}
    cache: dict[float, tuple[list[SweepCandidate], int, str | None]] = {}

    def evaluate(fsw: float) -> tuple[list[SweepCandidate], int, str | None]:
        key = round(float(fsw), 1)
        if key not in cache:
            res = _evaluate_fsw(
                entry,
                spec,
                key,
                qg_env_c=envelope.qg_total_c,
                vds_worst_v=vds_worst,
                id_worst_a=id_worst,
                min_isat_ratio=min_isat_ratio,
                max_candidates=max_candidates_per_fsw,
            )
            cache[key] = res
            if not res[0] and res[2] is not None:
                reasons[key] = res[2]
        return cache[key]

    # --- coarse log-spaced grid ------------------------------------------
    log_lo, log_hi = math.log(f_lo_hz), math.log(f_hi_hz)
    grid = [math.exp(log_lo + (log_hi - log_lo) * i / (n_coarse - 1)) for i in range(n_coarse)]
    for fsw in grid:
        evaluate(fsw)

    feasible_grid = [(f, cache[round(f, 1)][0][0].total_loss_w) for f in grid if cache[round(f, 1)][0]]
    if not feasible_grid:
        raise FrequencySweepError(
            f"no feasible (magnetic, fsw) across [{f_lo_hz/1e3:.0f}, "
            f"{f_hi_hz/1e3:.0f}] kHz for {entry.name!r} at {min_isat_ratio:g}× "
            f"isat margin. Widen the band, loosen the margin, or fetch parts.",
            reasons,
        )

    # coarse argmin + its bracket (neighbouring grid points)
    best_idx = min(range(len(grid)), key=lambda i: (
        cache[round(grid[i], 1)][0][0].total_loss_w if cache[round(grid[i], 1)][0] else math.inf
    ))
    lo = grid[max(0, best_idx - 1)]
    hi = grid[min(len(grid) - 1, best_idx + 1)]

    # --- golden-section refine in [lo, hi] on log(fsw) -------------------
    def total_at(fsw: float) -> float:
        feas = evaluate(fsw)[0]
        return feas[0].total_loss_w if feas else math.inf

    a, b = math.log(lo), math.log(hi)
    if b - a > 1e-9:
        c = b - _INV_PHI * (b - a)
        d = a + _INV_PHI * (b - a)
        fc, fd = total_at(math.exp(c)), total_at(math.exp(d))
        for _ in range(max(0, golden_iters)):
            if fc < fd:
                b, d, fd = d, c, fc
                c = b - _INV_PHI * (b - a)
                fc = total_at(math.exp(c))
            else:
                a, c, fc = c, d, fd
                d = a + _INV_PHI * (b - a)
                fd = total_at(math.exp(d))

    # --- argmin over every frequency we actually evaluated ---------------
    fsw_star = min(
        (f for f in cache if cache[f][0]),
        key=lambda f: cache[f][0][0].total_loss_w,
    )

    front = evaluate(fsw_star)[0][:top_k]
    if not front:  # pragma: no cover - guarded by feasible_grid above
        raise FrequencySweepError(
            f"internal: argmin frequency {fsw_star} has no feasible front", reasons
        )

    loss_curve = [
        FrequencyPoint(
            fsw_hz=f,
            feasible=bool(cache[f][0]),
            n_feasible=len(cache[f][0]),
            n_candidates=cache[f][1],
            total_loss_w=cache[f][0][0].total_loss_w if cache[f][0] else None,
            magnetic_loss_w=cache[f][0][0].magnetic_loss_w if cache[f][0] else None,
            switching_loss_w=cache[f][0][0].switching_loss_w if cache[f][0] else None,
            reason=cache[f][2],
        )
        for f in sorted(cache)
    ]

    return FrequencySweepResult(
        fsw_star_hz=float(fsw_star),
        front=front,
        loss_curve=loss_curve,
        envelope_fet=envelope,
        worst_vds_v=vds_worst,
        worst_id_a=id_worst,
        min_isat_ratio=float(min_isat_ratio),
        params={
            "f_lo_hz": f_lo_hz,
            "f_hi_hz": f_hi_hz,
            "n_coarse": n_coarse,
            "golden_iters": golden_iters,
            "top_k": top_k,
            "max_candidates_per_fsw": max_candidates_per_fsw,
            "seam": "slow-base-path",  # swap to fast-base when abt #11 lands
        },
    )
