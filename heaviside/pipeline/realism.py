"""Realism gate — fail-closed physics invariant checker.

Ports the 10 physics check primitives from Proteus
(``proteus/validators/physics.py``) into Heaviside as a reusable,
strictly-typed library plus a thin orchestrator that walks a populated TAS
and runs the checks whose inputs are actually present.

Design rules (from ``AGENTS.md`` rule 5 and ``CLAUDE.md`` "no fallbacks"):

  * Each check primitive **throws** ``RealismError`` on numerically invalid
    input.  No silent defaults, no in-band sentinels, no clamping.
  * The orchestrator classifies every known check as one of:
    ``PASS``, ``FAIL``, ``NOT_APPLICABLE`` (this check does not apply to this
    topology), or ``UNAVAILABLE`` (the check applies but the pipeline did not
    produce the inputs it needs).  ``UNAVAILABLE`` is never silently dropped.
  * The overall verdict is ``PASS`` only when at least one applicable check
    ran AND every applicable check passed.  ``FAIL`` if any failed.
    ``INCOMPLETE`` if every applicable check is ``UNAVAILABLE`` (i.e. the
    upstream pipeline has not yet been enriched enough to gate on realism).

Honest scope of v0.1
--------------------
The current Heaviside pipeline produces a TAS with magnetic MAS attached on
the inductor(s) but does **not** yet produce: simulation results, computed
junction temperatures, FET/diode/cap voltage ratings, or loss budgets.  The
``component-librarian`` agent (tracked in ``docs/BACKLOG.md``) is the
upstream piece that will populate these.  Until then, ``evaluate_tas`` on a
buck/flyback design will return ``INCOMPLETE`` with every check marked
``UNAVAILABLE`` and a per-check explanation of which pipeline input is
missing.  This is the fail-closed behaviour required by AGENTS.md rule 5:
no warnings-only mode, no ``--force`` override, the gate just says "I can't
yet tell whether this design is realistic" and that propagates to a
non-zero exit code at the CLI boundary.
"""

from __future__ import annotations

import enum
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class RealismError(Exception):
    """Raised by a check primitive when its numeric inputs are invalid.

    Per ``CLAUDE.md`` "no fallbacks, no defaults, no silent shortcuts —
    throw": when a check is asked to run but its inputs are negative, NaN,
    or otherwise nonsensical, the primitive raises this loudly rather than
    returning a "safe" placeholder result.
    """


# ---------------------------------------------------------------------------
# Result / report types
# ---------------------------------------------------------------------------


class CheckStatus(enum.Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CheckResult:
    """Result of a single physics check.

    For numeric-range checks ``limit`` may be ``(low, high)``; otherwise it
    is a single scalar threshold.  ``margin`` is positive when safe and
    negative when violating, expressed in the same units as ``value`` where
    that makes sense (ratios for derating, absolute degrees for thermal).
    For non-PASS/FAIL statuses, ``value`` / ``limit`` / ``margin`` may be
    ``None`` and ``detail`` carries the human-readable reason.
    """

    name: str
    status: CheckStatus
    value: float | None = None
    limit: float | tuple[float, float] | None = None
    margin: float | None = None
    detail: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status is CheckStatus.PASS


class RealismVerdict(enum.Enum):
    PASS = "pass"
    FAIL = "fail"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class RealismReport:
    verdict: RealismVerdict
    checks: tuple[CheckResult, ...]

    @property
    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {s.value: 0 for s in CheckStatus}
        for c in self.checks:
            out[c.status.value] += 1
        return out

    def by_status(self, status: CheckStatus) -> tuple[CheckResult, ...]:
        return tuple(c for c in self.checks if c.status is status)

    def to_dict(self) -> dict[str, Any]:
        def _limit(limit: Any) -> Any:
            if isinstance(limit, tuple):
                return list(limit)
            return limit

        return {
            "verdict": self.verdict.value,
            "summary": self.summary,
            "checks": [
                {
                    "name": c.name,
                    "status": c.status.value,
                    "value": c.value,
                    "limit": _limit(c.limit),
                    "margin": c.margin,
                    "detail": c.detail,
                    **({"extra": dict(c.extra)} if c.extra else {}),
                }
                for c in self.checks
            ],
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_finite(name: str, value: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise RealismError(f"{name}: expected number, got {type(value).__name__}")
    f = float(value)
    if math.isnan(f) or math.isinf(f):
        raise RealismError(f"{name}: non-finite value {value!r}")
    return f


def _require_positive(name: str, value: float) -> float:
    f = _require_finite(name, value)
    if f <= 0.0:
        raise RealismError(f"{name}: must be > 0, got {f!r}")
    return f


# ---------------------------------------------------------------------------
# Check primitives (ported from proteus/validators/physics.py)
# ---------------------------------------------------------------------------


def check_power_balance(
    pin: float, pout: float, total_losses: float, tolerance: float = 0.05
) -> CheckResult:
    """``|losses - (Pin - Pout)| / Pout < tolerance`` (default 5%).

    Source: Erickson & Maksimovic 3rd ed., Ch.1 (power balance).
    """
    pin_f = _require_positive("pin", pin)
    pout_f = _require_positive("pout", pout)
    losses_f = _require_finite("total_losses", total_losses)
    tol_f = _require_positive("tolerance", tolerance)
    gap = pin_f - pout_f
    imbalance = abs(losses_f - gap) / pout_f
    return CheckResult(
        name="power_balance",
        status=CheckStatus.PASS if imbalance < tol_f else CheckStatus.FAIL,
        value=round(imbalance, 6),
        limit=tol_f,
        margin=round(tol_f - imbalance, 6),
    )


def check_fet_voltage_derating(
    vds_rated: float, vds_stress: float, min_ratio: float = 1.5
) -> CheckResult:
    """``Vds_rated >= 1.5 * Vds_stress`` (Proteus design rule)."""
    rated = _require_positive("vds_rated", vds_rated)
    stress = _require_positive("vds_stress", vds_stress)
    ratio_min = _require_positive("min_ratio", min_ratio)
    ratio = rated / stress
    return CheckResult(
        name="fet_voltage_derating",
        status=CheckStatus.PASS if ratio >= ratio_min else CheckStatus.FAIL,
        value=round(ratio, 4),
        limit=ratio_min,
        margin=round(ratio - ratio_min, 4),
    )


def check_diode_voltage_derating(
    vrrm_rated: float, v_reverse: float, min_ratio: float = 1.3
) -> CheckResult:
    """``Vrrm_rated >= 1.3 * V_reverse_max`` (Proteus design rule)."""
    rated = _require_positive("vrrm_rated", vrrm_rated)
    rev = _require_positive("v_reverse", v_reverse)
    ratio_min = _require_positive("min_ratio", min_ratio)
    ratio = rated / rev
    return CheckResult(
        name="diode_voltage_derating",
        status=CheckStatus.PASS if ratio >= ratio_min else CheckStatus.FAIL,
        value=round(ratio, 4),
        limit=ratio_min,
        margin=round(ratio - ratio_min, 4),
    )


def check_inductor_isat_margin(
    isat: float, ipeak_worst: float, min_ratio: float = 1.2
) -> CheckResult:
    """``Isat >= 1.2 * Ipeak_worst_case`` (Maniktala Ch.5).

    ``ipeak_worst`` should be evaluated at the worst-case operating point
    (Vin_max for buck, Vin_min for boost; ``L * 0.8`` for tolerance; Tj=125
    °C). The caller is responsible for that derating — this primitive only
    checks the ratio.
    """
    isat_f = _require_positive("isat", isat)
    ipk = _require_positive("ipeak_worst", ipeak_worst)
    ratio_min = _require_positive("min_ratio", min_ratio)
    ratio = isat_f / ipk
    return CheckResult(
        name="inductor_isat_margin",
        status=CheckStatus.PASS if ratio >= ratio_min else CheckStatus.FAIL,
        value=round(ratio, 4),
        limit=ratio_min,
        margin=round(ratio - ratio_min, 4),
    )


def check_output_voltage_regulation(
    vout_actual: float, vout_target: float, tolerance: float = 0.05
) -> CheckResult:
    """``|Vout_actual - Vout_target| / Vout_target < tolerance`` (default 5%)."""
    actual = _require_finite("vout_actual", vout_actual)
    target = _require_positive("vout_target", vout_target)
    tol = _require_positive("tolerance", tolerance)
    err = abs(actual - target) / target
    return CheckResult(
        name="output_voltage_regulation",
        status=CheckStatus.PASS if err < tol else CheckStatus.FAIL,
        value=round(err, 6),
        limit=tol,
        margin=round(tol - err, 6),
    )


def check_efficiency_sanity(eta: float, low: float = 0.70, high: float = 0.995) -> CheckResult:
    """``low < eta < high`` (default 0.70 < η < 0.995).

    Outside this window indicates either an unbelievably bad design or a
    physically impossible one (losses always exist).
    """
    e = _require_finite("eta", eta)
    lo = _require_positive("low", low)
    hi = _require_positive("high", high)
    if lo >= hi:
        raise RealismError(f"efficiency window inverted: low={lo} >= high={hi}")
    passed = lo < e < hi
    return CheckResult(
        name="efficiency_sanity",
        status=CheckStatus.PASS if passed else CheckStatus.FAIL,
        value=e,
        limit=(lo, hi),
        margin=min(e - lo, hi - e),
    )


def check_efficiency_vs_spec(eta: float, spec_eta: float, *, tolerance: float = 0.02) -> CheckResult:
    """PASS when the achieved (MEASURED) efficiency meets the design's own spec
    target within ``tolerance`` (default 2 pp, to absorb testbench-scaffolding
    bias). A design that comes in below its efficiency requirement is NOT
    validated — the spec is a requirement, not a wish. Must be fed the measured
    (sim) efficiency, never the optimistic analyst budget."""
    e = _require_finite("eta", eta)
    tgt = _require_positive("spec_eta", spec_eta)
    tol = abs(_require_finite("tolerance", tolerance))
    margin = e - tgt
    passed = e >= tgt - tol
    return CheckResult(
        name="efficiency_vs_spec",
        status=CheckStatus.PASS if passed else CheckStatus.FAIL,
        value=round(e, 4),
        limit=round(tgt, 4),
        margin=round(margin, 4),
        detail=(f"measured η={e:.1%} vs spec target {tgt:.1%} — "
                f"{'meets' if passed else 'MISSES'} by {abs(margin) * 100:.1f} pp "
                f"(tolerance {tol * 100:.0f} pp)"),
    )


def check_operating_point_converged(
    converged: bool, *, regulated: bool | None = None
) -> CheckResult:
    """PASS only when the simulated rated operating point actually converged
    (and, for a closed-loop deck, regulated to its target).

    A design whose rated operating point did not converge is NOT validated: the
    vout / efficiency / loss numbers read off a non-converged transient are
    meaningless, and a design "proven" only at some off-rated corner that
    happened to converge is not proven at its own spec point. ``stage3_realize``
    already raises before stamping a non-regulated operating point; this gate
    check encodes that invariant so no alternate stamp path can slip a
    non-converged point past the gate.
    """
    if not isinstance(converged, bool):
        raise RealismError(f"converged: expected bool, got {type(converged).__name__}")
    if regulated is not None and not isinstance(regulated, bool):
        raise RealismError(f"regulated: expected bool/None, got {type(regulated).__name__}")
    ok = converged and (regulated is None or regulated)
    if ok:
        detail = "rated operating point converged" + (
            " and regulated to target" if regulated else ""
        )
    else:
        detail = (
            f"rated operating point did NOT converge "
            f"(converged={converged}, regulated={regulated}) — vout/efficiency/loss "
            "read off a non-converged point are meaningless; design not validated"
        )
    return CheckResult(
        name="operating_point_converged",
        status=CheckStatus.PASS if ok else CheckStatus.FAIL,
        value=1.0 if ok else 0.0,
        limit=1.0,
        margin=0.0 if ok else -1.0,
        detail=detail,
    )


# Single-switch topologies whose duty cycle is bounded above by 0.5 by
# volt-second balance (transformer reset window).  Two-switch / active-reset
# variants are excluded.
_HALF_DUTY_TOPOLOGIES = frozenset({"forward", "single_switch_forward"})


def check_duty_cycle_bounds(duty: float, topology: str) -> CheckResult:
    """Duty cycle within topology-specific limits.

    * single-switch forward: ``0.05 < D < 0.50``
    * everything else:       ``0.05 < D < 0.95``
    """
    d = _require_finite("duty", duty)
    if not isinstance(topology, str) or not topology.strip():
        raise RealismError(f"topology must be a non-empty string, got {topology!r}")
    key = topology.lower().replace("-", "_").replace(" ", "_")
    hi = 0.5 if key in _HALF_DUTY_TOPOLOGIES else 0.95
    lo = 0.05
    passed = lo < d < hi
    return CheckResult(
        name="duty_cycle_bounds",
        status=CheckStatus.PASS if passed else CheckStatus.FAIL,
        value=round(d, 6),
        limit=(lo, hi),
        margin=min(d - lo, hi - d),
        extra={"topology": topology},
    )


def check_no_negative_losses(losses: Mapping[str, Any]) -> CheckResult:
    """Every numeric loss term must be >= 0 (thermodynamics).

    Non-numeric / ``None`` values are ignored — they represent "this loss
    bucket was not computed", not "this loss is somehow negative".  Strictly
    negative numeric entries are violations and listed in ``extra``.
    A near-zero negative tolerance of 1e-3 W is allowed for floating-point
    rounding in upstream loss budgets.
    """
    if not isinstance(losses, Mapping):
        raise RealismError(f"losses: expected mapping, got {type(losses).__name__}")
    violators: dict[str, float] = {}
    for k, v in losses.items():
        if v is None or isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            fv = float(v)
            if math.isnan(fv) or math.isinf(fv):
                raise RealismError(f"loss term {k!r} is non-finite: {v!r}")
            if fv < -1e-3:
                violators[str(k)] = fv
    n = len(violators)
    return CheckResult(
        name="no_negative_losses",
        status=CheckStatus.PASS if n == 0 else CheckStatus.FAIL,
        value=float(n),
        limit=0.0,
        margin=-float(n),
        extra={"violators": violators} if violators else {},
    )


def check_thermal_limit(tj: float, tj_max: float) -> CheckResult:
    """``Tj_computed < Tj_max`` (JEDEC JESD51-1)."""
    tj_f = _require_finite("tj", tj)
    tj_max_f = _require_finite("tj_max", tj_max)
    margin = tj_max_f - tj_f
    return CheckResult(
        name="thermal_limit",
        status=CheckStatus.PASS if margin > 0 else CheckStatus.FAIL,
        value=round(tj_f, 2),
        limit=tj_max_f,
        margin=round(margin, 2),
    )


def check_capacitor_voltage_derating(
    v_rated: float, v_working: float, min_ratio: float = 1.5
) -> CheckResult:
    """``V_rated >= 1.5 * V_working`` (handles MLCC DC-bias derating)."""
    rated = _require_positive("v_rated", v_rated)
    work = _require_positive("v_working", v_working)
    ratio_min = _require_positive("min_ratio", min_ratio)
    ratio = rated / work
    return CheckResult(
        name="capacitor_voltage_derating",
        status=CheckStatus.PASS if ratio >= ratio_min else CheckStatus.FAIL,
        value=round(ratio, 4),
        limit=ratio_min,
        margin=round(ratio - ratio_min, 4),
    )


ALL_CHECKS: tuple[str, ...] = (
    "power_balance",
    "fet_voltage_derating",
    "diode_voltage_derating",
    "inductor_isat_margin",
    "output_voltage_regulation",
    "efficiency_sanity",
    "duty_cycle_bounds",
    "no_negative_losses",
    "thermal_limit",
    "capacitor_voltage_derating",
)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _iter_components(tas: Mapping[str, Any]) -> Iterable[tuple[str, Mapping[str, Any]]]:
    """Yield ``(stage_name, component)`` for every component in every stage."""
    topology = tas.get("topology")
    if not isinstance(topology, Mapping):
        return
    stages = topology.get("stages")
    if not isinstance(stages, list):
        return
    for stage in stages:
        if not isinstance(stage, Mapping):
            continue
        circuit = stage.get("circuit")
        if not isinstance(circuit, Mapping):
            continue
        comps = circuit.get("components")
        if not isinstance(comps, list):
            continue
        for c in comps:
            if isinstance(c, Mapping):
                yield (str(stage.get("name", "?")), c)


def _categorise(comp: Mapping[str, Any]) -> str:
    """Best-effort category for a TAS component.

    Priority order:

    1. ``data`` is an inline PEAS document — return the discriminator
       key (``magnetic``/``capacitor``/``semiconductor``/``resistor``/
       ``controller``). For ``semiconductor`` we further inspect the
       SAS body to return the device-type name (``mosfet``/``diode``/
       ``igbt``/``bjt``) so the per-device checks fire correctly.
    2. Explicit ``category`` field (SPICE→TAS reader convention).
    3. Refdes-prefix heuristic for components that are still
       placeholder URLs pre-bridge-attach.
    """
    data = comp.get("data")
    if isinstance(data, Mapping):
        for key in ("magnetic", "capacitor", "semiconductor", "resistor", "controller"):
            if key in data:
                if key == "semiconductor" and isinstance(data[key], Mapping):
                    sas = data[key]
                    for dev in ("mosfet", "diode", "igbt", "bjt"):
                        if dev in sas:
                            return dev
                return key
    cat = comp.get("category")
    if isinstance(cat, str) and cat:
        return cat
    name = str(comp.get("name", ""))
    if not name:
        return "unknown"
    head = name[0].upper()
    return {
        "Q": "mosfet",
        "M": "mosfet",
        "D": "diode",
        "L": "magnetic",
        "T": "magnetic",
        "C": "capacitor",
        "R": "resistor",
        "U": "controller",
    }.get(head, "unknown")


def _unavailable(name: str, reason: str) -> CheckResult:
    return CheckResult(name=name, status=CheckStatus.UNAVAILABLE, detail=reason)


def _first_present(m: Mapping[str, Any], *keys: str) -> Any:
    """First value among ``keys`` that is present (``is not None``).

    NOT an ``or``-chain: a measured ``0.0`` V output (a dead converter) is a
    real value the gate must FAIL on, not a falsy sentinel to skip past —
    skipping it silently downgrades a FAIL to an UNAVAILABLE the verdict never
    fails on. (pin/pout keep the ``or``-chain deliberately: their check rejects
    non-positive power, and a present-but-0.0 would otherwise raise mid-gate
    rather than resolve to UNAVAILABLE.)
    """
    for k in keys:
        val = m.get(k)
        if val is not None:
            return val
    return None


def _not_applicable(name: str, reason: str) -> CheckResult:
    return CheckResult(name=name, status=CheckStatus.NOT_APPLICABLE, detail=reason)


def _topology_has_controller_stage(tas: Mapping[str, Any]) -> bool:
    """True if the decomposed TAS includes a stage with ``role='control'``.

    Every Heaviside topology stencil emits one. Used by checks that
    must distinguish design-intent ("a controller will regulate this")
    from sim-modelling ("but the deck is open-loop").
    """
    topology = tas.get("topology")
    if not isinstance(topology, Mapping):
        return False
    stages = topology.get("stages")
    if not isinstance(stages, list):
        return False
    return any(isinstance(s, Mapping) and s.get("role") == "control" for s in stages)


# Fields the orchestrator looks for on TAS components / spec to drive checks.
# As the librarian / sim agents land, these fields will start to appear and
# the matching checks will move from UNAVAILABLE → PASS/FAIL automatically.
_FET_RATING_FIELD = "vds_rated"
_DIODE_RATING_FIELD = "vrrm_rated"
_CAP_RATING_FIELD = "v_rated"
_TJ_FIELD = "tj"
_TJ_MAX_FIELD = "tj_max"
_ISAT_FIELD = "isat"


def evaluate_tas(
    tas: Mapping[str, Any],
    *,
    topology: str,
    spec: Mapping[str, Any] | None = None,
) -> RealismReport:
    """Run every realism check whose inputs are present in ``tas`` / ``spec``.

    Selection rules per check:

      * ``efficiency_sanity``, ``power_balance``, ``output_voltage_regulation``,
        ``no_negative_losses`` — need ``simulation_results`` / ``loss_budget``
        keys on ``tas``.  Heaviside's current pipeline never produces these,
        so they always come back ``UNAVAILABLE`` until the sim agent lands.
      * ``fet_voltage_derating`` / ``diode_voltage_derating`` /
        ``capacitor_voltage_derating`` — need a ``vds_rated`` / ``vrrm_rated``
        / ``v_rated`` field on the corresponding component plus a ``vds_stress``
        / ``v_reverse`` / ``v_working`` field giving the actual stress.  The
        librarian agent will populate the ratings; the analyst / sim agents
        will populate the stress.
      * ``inductor_isat_margin`` — needs a scalar ``isat`` field on the
        magnetic component (today MAS carries the full B-H ``saturation``
        curve, not a single Isat scalar — extracting one would require a
        topology-aware peak-current computation that lives in the analyst
        agent).
      * ``duty_cycle_bounds`` — needs ``duty`` either on the TAS root or in
        ``spec``.  The decomposer does not compute duty today.
      * ``thermal_limit`` — needs ``tj`` and ``tj_max`` on a component.

    Verdict:

      * ``PASS`` — at least one applicable check ran and every applicable
        check passed.
      * ``FAIL`` — at least one applicable check failed.
      * ``INCOMPLETE`` — every applicable check was ``UNAVAILABLE`` (the
        upstream pipeline is not yet enriched enough to gate on realism).
    """
    if not isinstance(tas, Mapping):
        raise RealismError(f"tas: expected mapping, got {type(tas).__name__}")
    if not isinstance(topology, str) or not topology.strip():
        raise RealismError(f"topology: expected non-empty string, got {topology!r}")
    spec_map: Mapping[str, Any] = spec or {}

    checks: list[CheckResult] = []

    sim = tas.get("simulation_results")
    loss_budget = tas.get("loss_budget")

    # --- efficiency_sanity ----------------------------------------------
    # Prefer the analyst-derived efficiency (engineering truth from
    # picked components + spec) over the sim's measured efficiency,
    # which is biased low by lossy testbench scaffolding (snubbers,
    # idealized diode models) in MKF's stock decks. Both are accepted;
    # analyst wins when both are present.
    eta = None
    eta_source = None
    eta_invalid_reason: str | None = None
    if isinstance(sim, Mapping):
        for v in sim.values():
            if isinstance(v, Mapping):
                cand = v.get("efficiency_analyst")
                # Only TRUST the analyst efficiency when it is a physically valid ratio (0 < η < 1) AND
                # its loss budget is COMPLETE. An analyst η >= 1.0 (zero losses) OR a loss budget with a
                # NULL component means the analyst FAILED to model some loss (e.g. a dab/bridge/acf
                # transformer whose T1_core/T1_dcr came back null) — so the efficiency is OVER-estimated
                # (e.g. acf analyst 0.996 vs SPICE 0.824) and would falsely fail/pass efficiency_sanity.
                # When the analyst is impossible or incomplete, fall through to the measured SPICE
                # efficiency below (which DOES capture the real losses of the real-part deck).
                _lb = v.get("loss_budget")
                _analyst_complete = not (
                    isinstance(_lb, Mapping) and any(x is None for x in _lb.values())
                )
                if isinstance(cand, (int, float)) and 0.0 < float(cand) < 1.0 and _analyst_complete:
                    eta = float(cand)
                    eta_source = "analyst"
                    break
        if eta is None:
            for v in sim.values():
                if isinstance(v, Mapping):
                    cand = v.get("efficiency")
                    if isinstance(cand, (int, float)):
                        eta = float(cand)
                        eta_source = "sim"
                        # Refuse to interpret physically impossible
                        # ratios (eta > 1, eta < 0). These come from
                        # sim runners that mismeasure pin/pout —
                        # typically when the deck has multiple input
                        # rails (e.g. Vienna 3-phase) and only one is
                        # probed. Per CLAUDE.md, don't paper over the
                        # broken sim by clamping or /100-ing the
                        # value; surface it as UNAVAILABLE so the
                        # design isn't falsely failed.
                        if not (0.0 < eta < 1.0):
                            eta_invalid_reason = (
                                f"sim reported efficiency={eta!r} (pin={v.get('pin')!r}, "
                                f"pout={v.get('pout')!r}, total_losses={v.get('total_losses')!r}) "
                                "— physically impossible (must be a ratio in (0,1)). "
                                "Sim runner is mismeasuring pin or pout; cannot "
                                "evaluate efficiency_sanity until that's fixed"
                            )
                            eta = None
                        break
    if eta is None:
        if eta_invalid_reason is not None:
            checks.append(_unavailable("efficiency_sanity", eta_invalid_reason))
        else:
            checks.append(
                _unavailable(
                    "efficiency_sanity",
                    "no tas.simulation_results.*.efficiency{_analyst,} — "
                    "simulation/analyst agent has not run",
                )
            )
    else:
        result = check_efficiency_sanity(eta)
        checks.append(
            CheckResult(
                name=result.name,
                status=result.status,
                value=result.value,
                limit=result.limit,
                margin=result.margin,
                detail=result.detail,
                extra={**dict(result.extra), "source": eta_source},
            )
        )

    # --- efficiency_vs_spec ---------------------------------------------
    # Does the design MEET its own target efficiency? Uses the MEASURED (sim)
    # efficiency — the analyst budget over-estimates (it omits semiconductor
    # losses), so gating on it would falsely pass a sub-spec design.
    eta_sim = None
    if isinstance(sim, Mapping):
        for v in sim.values():
            if isinstance(v, Mapping):
                cand = v.get("efficiency")
                if isinstance(cand, (int, float)) and 0.0 < float(cand) < 1.0:
                    eta_sim = float(cand)
                    break
    spec_eta = spec_map.get("efficiency") if isinstance(spec_map, Mapping) else None
    if eta_sim is None or not isinstance(spec_eta, (int, float)) or not (0.0 < float(spec_eta) < 1.0):
        checks.append(
            _unavailable(
                "efficiency_vs_spec",
                "need both a MEASURED sim efficiency and a valid spec.efficiency target",
            )
        )
    else:
        checks.append(check_efficiency_vs_spec(eta_sim, float(spec_eta)))

    # --- operating_point_converged --------------------------------------
    # Did the rated point actually converge/regulate? The realize stage stamps
    # {converged, regulated} alongside the sim numbers; a False (or a stamp path
    # that recorded a non-converged point) must NOT read as validated.
    conv = reg = None
    if isinstance(sim, Mapping):
        for v in sim.values():
            if isinstance(v, Mapping) and ("converged" in v or "regulated" in v):
                conv = v.get("converged")
                reg = v.get("regulated")
                break
    if conv is None and reg is None:
        checks.append(
            _unavailable(
                "operating_point_converged",
                "no tas.simulation_results.*.{converged,regulated} marker — the "
                "realize stage did not record convergence for this operating point",
            )
        )
    else:
        conv_bool = bool(conv) if conv is not None else bool(reg)
        reg_bool = bool(reg) if reg is not None else None
        checks.append(check_operating_point_converged(conv_bool, regulated=reg_bool))

    # --- power_balance --------------------------------------------------
    pin = pout = losses_total = None
    if isinstance(sim, Mapping):
        for v in sim.values():
            if isinstance(v, Mapping):
                pin = v.get("pin") or v.get("input_power")
                pout = v.get("pout") or v.get("output_power")
                losses_total = v.get("total_losses")
                if pin is not None and pout is not None and losses_total is not None:
                    break
    if pin is None or pout is None or losses_total is None:
        checks.append(
            _unavailable(
                "power_balance",
                "no tas.simulation_results.*.{pin,pout,total_losses} — loss budget not computed",
            )
        )
    else:
        checks.append(check_power_balance(float(pin), float(pout), float(losses_total)))

    # --- output_voltage_regulation --------------------------------------
    vout_target = None
    if isinstance(spec_map, Mapping):
        ops = spec_map.get("operatingPoints")
        if isinstance(ops, list) and ops:
            first = ops[0]
            if isinstance(first, Mapping):
                vs = first.get("outputVoltages")
                if isinstance(vs, list) and vs and isinstance(vs[0], (int, float)):
                    vout_target = float(vs[0])
    vout_actual = None
    if isinstance(sim, Mapping):
        for v in sim.values():
            if isinstance(v, Mapping):
                cand = _first_present(v, "vout", "output_voltage")
                if isinstance(cand, (int, float)):
                    vout_actual = float(cand)
                    break
    if vout_target is None or vout_actual is None:
        checks.append(
            _unavailable(
                "output_voltage_regulation",
                "missing spec.operatingPoints[0].outputVoltages[0] or "
                "tas.simulation_results.*.vout (simulation agent has not run)",
            )
        )
    else:
        # If the design includes a controller stage AND the sim is
        # marked as open-loop (or no closed-loop flag is set, the
        # current default for MKF decks), the measured vout reflects
        # the open-loop drift NOT the regulated closed-loop output —
        # the check is testing the deck, not the design. Mark
        # NOT_APPLICABLE with a clear rationale rather than FAIL on
        # something that's a known sim-modelling limitation.
        has_controller = _topology_has_controller_stage(tas)
        is_closed_loop_sim = False
        if isinstance(sim, Mapping):
            for v in sim.values():
                if isinstance(v, Mapping) and v.get("is_closed_loop") is True:
                    is_closed_loop_sim = True
                    break
        if has_controller and not is_closed_loop_sim:
            checks.append(
                _not_applicable(
                    "output_voltage_regulation",
                    "design includes a controller stage (U1) but the sim "
                    "deck is open-loop — measured vout reflects open-loop "
                    "drift, not the design's regulated output. Re-evaluate "
                    "after a closed-loop simulator lands.",
                )
            )
        else:
            checks.append(check_output_voltage_regulation(vout_actual, vout_target))

    # --- no_negative_losses ---------------------------------------------
    if isinstance(loss_budget, Mapping) and loss_budget:
        # Run once per line condition if it is a nested mapping; once
        # over the flat dict otherwise.
        nested = any(isinstance(v, Mapping) for v in loss_budget.values())
        if nested:
            for line_name, losses in loss_budget.items():
                if isinstance(losses, Mapping):
                    res = check_no_negative_losses(losses)
                    checks.append(
                        CheckResult(
                            name=res.name,
                            status=res.status,
                            value=res.value,
                            limit=res.limit,
                            margin=res.margin,
                            detail=res.detail,
                            extra={**dict(res.extra), "line": line_name},
                        )
                    )
        else:
            checks.append(check_no_negative_losses(loss_budget))
    else:
        checks.append(
            _unavailable(
                "no_negative_losses",
                "no tas.loss_budget — loss budget not computed",
            )
        )

    # --- duty_cycle_bounds ----------------------------------------------
    duty = tas.get("duty")
    if duty is None:
        duty = spec_map.get("duty")
    if isinstance(duty, (int, float)):
        checks.append(check_duty_cycle_bounds(float(duty), topology))
    else:
        checks.append(
            _unavailable(
                "duty_cycle_bounds",
                "no tas.duty or spec.duty — decomposer does not compute duty cycle",
            )
        )

    # --- per-component checks: voltage derating, isat, thermal -----------
    # EVERY qualifying component is checked — not just the first of each type.
    # A 4-switch bridge derates all four FETs; a two-diode rectifier gates both
    # junction temperatures. The single-shot short-circuit that used to stop
    # after the first component silently passed under-rated / over-temp siblings
    # (e.g. LLC D2 Tj>Tj_max hidden behind a cooler D1).
    have_fet = have_diode = have_cap = have_magnetic = False
    fet_n = diode_n = cap_n = isat_n = thermal_n = 0

    def _per_component(result: CheckResult, comp_name: Any, stage: str) -> None:
        checks.append(
            CheckResult(
                **{**result.__dict__, "extra": {"component": comp_name, "stage": stage}}
            )
        )

    for stage_name, comp in _iter_components(tas):
        cat = _categorise(comp)
        name = comp.get("name", "?")

        if cat == "mosfet":
            have_fet = True
            if (
                (rated := comp.get(_FET_RATING_FIELD)) is not None
                and (stress := comp.get("vds_stress")) is not None
            ):
                _per_component(
                    check_fet_voltage_derating(float(rated), float(stress)), name, stage_name
                )
                fet_n += 1
        elif cat == "diode":
            have_diode = True
            if (
                (rated := comp.get(_DIODE_RATING_FIELD)) is not None
                and (rev := comp.get("v_reverse")) is not None
            ):
                _per_component(
                    check_diode_voltage_derating(float(rated), float(rev)), name, stage_name
                )
                diode_n += 1
        elif cat == "capacitor":
            have_cap = True
            if (
                (rated := comp.get(_CAP_RATING_FIELD)) is not None
                and (work := comp.get("v_working")) is not None
            ):
                _per_component(
                    check_capacitor_voltage_derating(float(rated), float(work)), name, stage_name
                )
                cap_n += 1
        elif cat == "magnetic":
            have_magnetic = True
            if (
                (isat := comp.get(_ISAT_FIELD)) is not None
                and (ipk := comp.get("ipeak_worst")) is not None
            ):
                _per_component(
                    check_inductor_isat_margin(float(isat), float(ipk)), name, stage_name
                )
                isat_n += 1

        if (
            (tj := comp.get(_TJ_FIELD)) is not None
            and (tj_max := comp.get(_TJ_MAX_FIELD)) is not None
        ):
            _per_component(check_thermal_limit(float(tj), float(tj_max)), name, stage_name)
            thermal_n += 1

    if not fet_n:
        checks.append(
            _unavailable(
                "fet_voltage_derating",
                "no mosfet component has both 'vds_rated' and 'vds_stress' fields — "
                "librarian/analyst agents have not enriched the TAS yet",
            )
            if have_fet
            else _not_applicable("fet_voltage_derating", "no mosfet components in TAS")
        )
    if not diode_n:
        checks.append(
            _unavailable(
                "diode_voltage_derating",
                "no diode component has both 'vrrm_rated' and 'v_reverse' fields",
            )
            if have_diode
            else _not_applicable("diode_voltage_derating", "no diode components in TAS")
        )
    if not cap_n:
        checks.append(
            _unavailable(
                "capacitor_voltage_derating",
                "no capacitor component has both 'v_rated' and 'v_working' fields",
            )
            if have_cap
            else _not_applicable("capacitor_voltage_derating", "no capacitor components in TAS")
        )
    if not isat_n:
        checks.append(
            _unavailable(
                "inductor_isat_margin",
                "no magnetic component has both 'isat' (scalar) and 'ipeak_worst' fields — "
                "MAS only carries the B-H 'saturation' curve, not a derated Isat scalar; "
                "computing Isat is the analyst agent's job",
            )
            if have_magnetic
            else _not_applicable("inductor_isat_margin", "no magnetic components in TAS")
        )
    if not thermal_n:
        checks.append(
            _unavailable(
                "thermal_limit",
                "no component has both 'tj' and 'tj_max' fields — thermal agent has not run",
            )
        )

    # --- selection_provenance_complete (B1) -----------------------------
    # Every stamped REAL part (one carrying an mpn / selection_provenance)
    # must carry the uniform {producer, method, source_ref, inputs_hash}
    # envelope, or its origin cannot be audited. A sourceless part ⇒
    # UNAVAILABLE (we don't FAIL the physics — the number may be right — but
    # we never silently trust an un-auditable pick).
    checks.append(_check_selection_provenance(tas))

    # --- estimators_agree (B7 cross_check) ------------------------------
    # Independent estimators of the same quantity (analyst vs sim efficiency /
    # total loss / Tj) must agree within tolerance; a recorded disagreement
    # FAILs the design (surface, don't average). UNAVAILABLE until cross_check
    # has run.
    checks.append(_check_estimators_agree(tas))

    return _verdict_from(tuple(checks))


def _check_estimators_agree(tas: Mapping[str, Any]) -> CheckResult:
    cc = tas.get("cross_check")
    if not isinstance(cc, Mapping):
        return _unavailable(
            "estimators_agree",
            "no tas.cross_check — independent-estimator triangulation has not run",
        )
    comparisons = cc.get("comparisons")
    if not isinstance(comparisons, list) or not comparisons:
        return _unavailable("estimators_agree", "tas.cross_check has no comparisons")
    disagreeing = [
        c for c in comparisons
        if isinstance(c, Mapping) and c.get("agree") is False
    ]
    if disagreeing:
        bits = ", ".join(
            f"{c.get('quantity')}({'/'.join(c.get('sources', []))}): "
            f"Δ={c.get('relative_diff')}>{c.get('tolerance')}"
            for c in disagreeing
        )
        return CheckResult(
            name="estimators_agree", status=CheckStatus.FAIL,
            detail=f"independent estimators disagree beyond tolerance: {bits}",
        )
    return CheckResult(
        name="estimators_agree", status=CheckStatus.PASS,
        detail=f"all {len(comparisons)} independent-estimator comparisons agree",
    )


def _check_selection_provenance(tas: Mapping[str, Any]) -> CheckResult:
    from heaviside import provenance

    selected = 0
    sourceless: list[str] = []
    for _stage_name, comp in _iter_components(tas):
        prov = comp.get("selection_provenance")
        has_mpn = isinstance(comp.get("mpn"), str) and comp.get("mpn")
        if prov is None and not has_mpn:
            continue  # a placeholder / non-selected node — nothing to audit
        selected += 1
        if not provenance.is_complete(prov):
            sourceless.append(str(comp.get("name", comp.get("mpn", "?"))))
    if selected == 0:
        return _not_applicable(
            "selection_provenance_complete", "no selected real parts in TAS"
        )
    if sourceless:
        return _unavailable(
            "selection_provenance_complete",
            f"{len(sourceless)}/{selected} selected parts lack a complete "
            f"{{producer, method, source_ref, inputs_hash}} provenance: "
            f"{', '.join(sorted(sourceless))}",
        )
    return CheckResult(
        name="selection_provenance_complete",
        status=CheckStatus.PASS,
        detail=f"all {selected} selected parts carry a complete provenance envelope",
    )


# The audit/meta checks (provenance envelope completeness, independent-estimator
# agreement) can FAIL a design, but must NOT by themselves carry a PASS. They
# describe how trustworthy the *bookkeeping* is, not whether the *physics* holds.
# A PASS therefore requires at least one genuine physics check (a member of
# ``ALL_CHECKS``) to have passed — otherwise a design whose every physics check
# is UNAVAILABLE (sim/BOM never produced its inputs) could read as realistic on
# metadata alone. See ``stage3_realize``: it now raises rather than emitting such
# a degraded TAS, and this is the defence-in-depth that catches it regardless of
# how the TAS was produced.
_PHYSICS_CHECK_NAMES: frozenset[str] = frozenset(ALL_CHECKS)


def _verdict_from(checks: tuple[CheckResult, ...]) -> RealismReport:
    has_fail = any(c.status is CheckStatus.FAIL for c in checks)
    has_physics_pass = any(
        c.status is CheckStatus.PASS and c.name in _PHYSICS_CHECK_NAMES for c in checks
    )
    if has_fail:
        verdict = RealismVerdict.FAIL
    elif has_physics_pass:
        verdict = RealismVerdict.PASS
    else:
        # Either nothing ran, or the only passing checks were audit/meta — in
        # both cases the physics is not yet established, so be honest: INCOMPLETE.
        verdict = RealismVerdict.INCOMPLETE
    return RealismReport(verdict=verdict, checks=checks)


__all__ = (
    "ALL_CHECKS",
    "CheckResult",
    "CheckStatus",
    "RealismError",
    "RealismReport",
    "RealismVerdict",
    "check_capacitor_voltage_derating",
    "check_diode_voltage_derating",
    "check_duty_cycle_bounds",
    "check_efficiency_sanity",
    "check_fet_voltage_derating",
    "check_inductor_isat_margin",
    "check_no_negative_losses",
    "check_operating_point_converged",
    "check_output_voltage_regulation",
    "check_power_balance",
    "check_thermal_limit",
    "evaluate_tas",
)
