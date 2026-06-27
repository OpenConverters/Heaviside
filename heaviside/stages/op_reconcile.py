"""op_reconcile — re-validate the ONE chosen design across ALL operating points
(master-plan step B7, the verification spine).

The designer picks a single magnetic + fsw* from the worst-case sweep, but a
real converter must be feasible at *every* operating point (wide Vin, multi-rail
loads). This stage re-checks the chosen ``(magnetic, fsw*)`` at each OP for:

* **saturation** — the OP's worst-case peak current vs the magnetic's MKF isat;
* **thermal** — the OP's junction temperature vs the part's Tj_max.

It identifies the **binding OP** (the least-margin one), reports
``feasible_all_ops``, and emits machine-readable ``constraint_feedback`` the
refinement loop (B8) re-seeds from. A corner OP that saturates raises
:class:`InfeasibleAtOP` (surface, never silently keep an infeasible design).

For quasi-resonant / DCM topologies fsw legitimately varies with load — there
``fsw_load_law`` emits a per-load fsw law (one magnetic feasible across the
whole law) instead of forcing a single scalar fsw.

The pure core (:func:`reconcile_margins`) operates on already-evaluated per-OP
numbers so it is fully unit-testable without MKF; :func:`reconcile` is the thin
convenience that builds those numbers from MKF isat + the stress engine.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


class InfeasibleAtOP(RuntimeError):
    """The chosen design fails (saturation or thermal) at one or more operating
    points. Carries the binding OP and the per-OP margins so the caller can
    re-seed or surface — never silently accept the design."""

    def __init__(self, report: "ReconciliationReport", reason: str) -> None:
        self.report = report
        self.binding_op_index = report.binding_op_index
        super().__init__(
            f"{reason} (binding OP {report.binding_op_index}). "
            f"feedback={report.constraint_feedback}"
        )


@dataclass(frozen=True, slots=True)
class OpEstimate:
    """Already-evaluated per-OP numbers for the chosen design."""

    op_index: int
    ipeak_a: float
    isat_a: float
    tj_c: float | None = None
    tj_max_c: float | None = None
    label: str = ""


@dataclass(frozen=True, slots=True)
class OpMargin:
    op_index: int
    isat_ratio: float  # isat / ipeak
    sat_feasible: bool
    thermal_ratio: float | None  # tj_max / tj (>1 = headroom)
    thermal_feasible: bool | None
    label: str = ""

    @property
    def feasible(self) -> bool:
        return self.sat_feasible and (self.thermal_feasible is not False)


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    per_op: list[OpMargin]
    binding_op_index: int
    feasible_all_ops: bool
    constraint_feedback: dict[str, Any] = field(default_factory=dict)


def reconcile_margins(
    estimates: Sequence[OpEstimate],
    *,
    min_isat_ratio: float = 1.2,
    raise_on_infeasible: bool = True,
) -> ReconciliationReport:
    """Pure reconciliation: per-OP saturation + thermal margins, binding OP,
    feasibility, constraint feedback.

    The binding OP is the one with the least saturation margin (the dominant
    sizing constraint for the magnetic); thermal infeasibility also fails the
    design. Raises :class:`InfeasibleAtOP` when any OP is infeasible (unless
    ``raise_on_infeasible`` is False, used to inspect the report)."""
    if not estimates:
        raise ValueError("reconcile_margins: no operating-point estimates")
    margins: list[OpMargin] = []
    for e in estimates:
        if not (isinstance(e.ipeak_a, (int, float)) and e.ipeak_a > 0):
            raise ValueError(f"OP {e.op_index}: ipeak must be > 0, got {e.ipeak_a!r}")
        if not (isinstance(e.isat_a, (int, float)) and e.isat_a > 0):
            raise ValueError(f"OP {e.op_index}: isat must be > 0, got {e.isat_a!r}")
        isat_ratio = e.isat_a / e.ipeak_a
        sat_ok = isat_ratio >= min_isat_ratio
        if isinstance(e.tj_c, (int, float)) and isinstance(e.tj_max_c, (int, float)) and e.tj_c > 0:
            thermal_ratio: float | None = e.tj_max_c / e.tj_c
            thermal_ok: bool | None = e.tj_c <= e.tj_max_c
        else:
            thermal_ratio = None
            thermal_ok = None  # thermal not evaluable at this OP
        margins.append(OpMargin(
            op_index=e.op_index, isat_ratio=isat_ratio, sat_feasible=sat_ok,
            thermal_ratio=thermal_ratio, thermal_feasible=thermal_ok, label=e.label,
        ))

    binding = min(margins, key=lambda m: m.isat_ratio)
    feasible_all = all(m.feasible for m in margins)

    feedback: dict[str, Any] = {
        "binding_op_index": binding.op_index,
        "binding_op_label": binding.label,
        "binding_isat_ratio": round(binding.isat_ratio, 4),
        "min_isat_ratio": min_isat_ratio,
        "feasible_all_ops": feasible_all,
    }
    sat_fail = [m.op_index for m in margins if not m.sat_feasible]
    therm_fail = [m.op_index for m in margins if m.thermal_feasible is False]
    if sat_fail:
        feedback["saturation_infeasible_ops"] = sat_fail
        # how much more isat (or less ripple) is needed at the binding OP
        feedback["isat_shortfall_factor"] = round(min_isat_ratio / binding.isat_ratio, 4)
    if therm_fail:
        feedback["thermal_infeasible_ops"] = therm_fail

    report = ReconciliationReport(
        per_op=margins, binding_op_index=binding.op_index,
        feasible_all_ops=feasible_all, constraint_feedback=feedback,
    )
    if raise_on_infeasible and not feasible_all:
        why = []
        if sat_fail:
            why.append(f"saturation at OP(s) {sat_fail}")
        if therm_fail:
            why.append(f"thermal at OP(s) {therm_fail}")
        raise InfeasibleAtOP(report, "chosen design infeasible: " + "; ".join(why))
    return report


def reconcile(
    topology: str,
    spec: Mapping[str, Any],
    mas: Mapping[str, Any],
    *,
    min_isat_ratio: float = 1.2,
    raise_on_infeasible: bool = True,
) -> ReconciliationReport:
    """Build per-OP estimates from MKF isat + the stress engine, then reconcile.

    The magnetic's saturation current (MKF) is fixed; the worst-case peak
    current is the stress deriver's ``id_stress`` at each OP. Thermal is left
    None here (the analyst stamps Tj onto the realised TAS, not the bare MAS) —
    the realism gate's thermal_limit check covers it; pass thermal via the pure
    core when a Tj-per-OP estimate exists."""
    from heaviside import bridge
    from heaviside.pipeline.stress import derive_stresses_per_op

    per_op = derive_stresses_per_op(topology, spec)
    if per_op is None:
        raise InfeasibleAtOP(
            ReconciliationReport([], -1, False, {"error": "no stress deriver"}),
            f"no stress deriver for {topology!r}; cannot reconcile per-OP saturation",
        )
    L = bridge._harvest_authoritative_inductance(mas)
    magnetic = mas.get("magnetic") if isinstance(mas, Mapping) else None
    isat = bridge._isat_from_mas(magnetic, L) if magnetic is not None else None
    if not isinstance(isat, (int, float)) or isat <= 0:
        raise InfeasibleAtOP(
            ReconciliationReport([], -1, False, {"error": "isat unavailable"}),
            "MKF could not evaluate the chosen magnetic's saturation current",
        )
    # The saturating current is the peak MAGNETIZING current (the flux driver), read from the designed
    # magnetic's MAS — NOT the stress deriver's id_stress, which is the SWITCH/load current and over-
    # states saturation several-fold for transformers whose primary also carries the reflected load
    # (push_pull/forward/bridge). Fall back to id_stress (conservative) only when the MAS carries no
    # magnetizing current for that OP. Inductors are unchanged (magnetizing current == winding current).
    mag_peaks = bridge.magnetizing_peaks_per_op(mas)
    estimates = []
    for i, s in enumerate(per_op):
        if s.id_stress is None or s.id_stress <= 0:
            continue
        imag = mag_peaks[i] if i < len(mag_peaks) else None
        ipeak = imag if (isinstance(imag, (int, float)) and imag > 0) else float(s.id_stress)
        estimates.append(OpEstimate(op_index=i, ipeak_a=ipeak, isat_a=float(isat), label=f"op{i}"))
    return reconcile_margins(
        estimates, min_isat_ratio=min_isat_ratio, raise_on_infeasible=raise_on_infeasible
    )


@dataclass(frozen=True, slots=True)
class LoadPoint:
    load_fraction: float  # 0..1 of full load
    fsw_hz: float


def fsw_load_law(points: Sequence[LoadPoint]) -> list[LoadPoint]:
    """For QR/DCM topologies fsw rises as load drops. Validate the per-load fsw
    law is **monotone non-increasing in load** (higher fsw at lighter load) and
    return it sorted by load. Raises if the law is non-monotone (a sign the
    underlying design is not a single consistent QR/DCM operating mode)."""
    if not points:
        raise ValueError("fsw_load_law: no load points")
    ordered = sorted(points, key=lambda p: p.load_fraction)
    for a, b in zip(ordered, ordered[1:]):
        # as load increases, fsw must not increase (QR/DCM: light load → high fsw)
        if b.fsw_hz > a.fsw_hz + 1e-6:
            raise ValueError(
                f"fsw-load law non-monotone: load {a.load_fraction:.2f}→{b.load_fraction:.2f} "
                f"fsw {a.fsw_hz:.0f}→{b.fsw_hz:.0f} Hz rose with load (QR/DCM fsw should "
                f"fall as load rises)"
            )
    return ordered
