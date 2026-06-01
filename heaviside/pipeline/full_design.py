"""Multi-stage converter design orchestrator (della Pollock method).

After Jennifer della Pollock's approach to designing the first Tesla
charger: pick the topology first, then design a real magnetic for it
(with all parasitics), then design the converter around the magnetic
(including choosing the switching frequency that minimises total
magnetic + switch losses).

Stage 1 — Topology selection (dual-path, reconciled):
  Path A: heaviside.pipeline.topology_screen.feasible_topologies
  Path B: heaviside.agents.prompts.topology-selector (LLM)
  Reconcile: union, warn on Jaccard > 0.5.

Stage 2 — Per-topology fast Pareto magnetic pick (parallel):
  For each viable topology, ask MKF for N fast-mode candidates and
  pick one with magnetic_picker (deterministic or LLM).

Stage 3 — Realize:
  design_converter_components → decompose → attach magnetics →
  assemble_bom_from_tas (select real FET/diode/cap) →
  enrich → inject parasitics → simulate with real Rds_on/Vf/ESR →
  analyst + realism gate.

Stage 4 — Rank:
  Compose across survivors by (verdict, scoring).

Public surface
--------------
:class:`DesignOutcome`         per-topology outcome dataclass
:func:`full_design`            top-level orchestrator
:func:`stage1_topology_screen` dual-path topology selection
:func:`stage2_pick_magnetics`  parallel fast-Pareto magnetic pick

The agent-layer supervisor (``design-orchestrator.md``) calls these
through Strands tools — the merged pipeline below is the single
source of truth for the design logic.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

from heaviside.agents.magnetic_picker import (
    PARETO_CRITERIA,
    pareto_summary,
    pick_best_pareto,
)
from heaviside.bridge import MagneticDesign, design_magnetics_fast
from heaviside.pipeline.topology_screen import (
    TopologyReconciliation,
    feasible_topology_names,
    reconcile_topology_choices,
)
from heaviside.topologies.registry import TopologyEntry, get

__all__ = [
    "DesignOutcome",
    "FullDesignError",
    "Stage1Result",
    "Stage2Result",
    "full_design",
    "stage1_topology_screen",
    "stage2_pick_magnetics",
]

logger = logging.getLogger(__name__)


class FullDesignError(RuntimeError):
    """Raised when the orchestrator cannot proceed (e.g. no viable
    topology survives Stage 1)."""


# ---------------------------------------------------------------------------
# Stage 1 — topology selection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Stage1Result:
    """Outcome of the dual-path topology screen."""
    spec: Mapping[str, Any]
    reconciliation: TopologyReconciliation
    static_names: tuple[str, ...]
    agent_names: tuple[str, ...]
    agent_reasoning: str


# Type alias for the LLM-path injection point. Production wiring binds
# this to a Strands ``load_agent("topology-selector")`` invocation; tests
# inject a deterministic fake so they don't need real LLM credentials.
TopologySelectorFn = Callable[[Mapping[str, Any]], tuple[list[str], str]]


_AGENT_JSON_RE = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```",
    re.DOTALL,
)


def _default_topology_selector(spec: Mapping[str, Any]) -> tuple[list[str], str]:
    """Default LLM-path: call the topology-selector LLM agent.

    Uses the OpenAI-compatible API (Moonshot/Kimi by default). Falls
    back to the static screen if no API key is configured.
    """
    from heaviside.agents.topology_selector_llm import topology_selector_with_fallback
    return topology_selector_with_fallback(spec)


def _parse_topology_selector_response(text: str) -> tuple[list[str], str]:
    """Extract ``viable`` + ``reasoning`` from the agent's reply.

    The prompt requires a fenced JSON block; we accept any first
    JSON object we find. Per the no-fallback rule, an unparseable
    reply is a loud error — not a silent fallback to "all topologies
    viable".
    """
    match = _AGENT_JSON_RE.search(text)
    if not match:
        # Try a bare-dict fallback (some models drop the fences).
        match = re.search(r"(\{.*\})", text, re.DOTALL)
    if not match:
        raise FullDesignError(
            "topology-selector agent reply has no JSON block. "
            f"Response: {text!r}"
        )
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise FullDesignError(
            f"topology-selector JSON parse failed: {exc}. "
            f"Block: {match.group(1)!r}"
        ) from exc
    viable = payload.get("viable")
    if not isinstance(viable, list) or not all(isinstance(n, str) for n in viable):
        raise FullDesignError(
            f"topology-selector JSON missing or malformed 'viable': "
            f"{payload!r}"
        )
    reasoning = str(payload.get("reasoning", ""))
    return viable, reasoning


def stage1_topology_screen(
    spec: Mapping[str, Any],
    *,
    selector_fn: TopologySelectorFn | None = None,
    disagreement_threshold: float = 0.5,
) -> Stage1Result:
    """Run the dual-path topology screen.

    Both paths execute (static + LLM agent), and the results are
    reconciled. Tests inject ``selector_fn`` to avoid the real LLM.
    """
    static_names = feasible_topology_names(spec)
    if selector_fn is None:
        selector_fn = _default_topology_selector
    agent_names, agent_reasoning = selector_fn(spec)
    reconciliation = reconcile_topology_choices(
        static_names, agent_names,
        disagreement_threshold=disagreement_threshold,
    )
    if reconciliation.warning:
        logger.warning("Stage 1: %s", reconciliation.warning)
    if not reconciliation.chosen:
        raise FullDesignError(
            f"Stage 1: no topology survives the screen. "
            f"static={static_names!r} agent={agent_names!r}"
        )
    return Stage1Result(
        spec=spec,
        reconciliation=reconciliation,
        static_names=tuple(static_names),
        agent_names=tuple(agent_names),
        agent_reasoning=agent_reasoning,
    )


# ---------------------------------------------------------------------------
# Stage 2 — parallel magnetic pick
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TopologyPick:
    """One topology + its picked main magnetic, after Stage 2."""
    topology: TopologyEntry
    main_magnetic: MagneticDesign
    candidates: tuple[MagneticDesign, ...]
    pick_reason: str
    pick_criteria: str


@dataclass(frozen=True, slots=True)
class Stage2Result:
    """Outcome of the per-topology magnetic pick stage."""
    spec: Mapping[str, Any]
    picks: tuple[TopologyPick, ...]
    failures: tuple[tuple[str, str], ...]  # (topology, error) for topologies
                                            # where Pareto exploration failed


def _stage2_pick_one(args: tuple[str, dict, int, str, str]) -> dict[str, Any]:
    """Worker for the ProcessPoolExecutor. Pure data in / out so the
    pool can pickle it. Returns a payload dict the parent reassembles
    into a TopologyPick (or surfaces as a failure)."""
    topology_name, spec, n_candidates, criteria, core_mode = args
    try:
        candidates = design_magnetics_fast(
            topology_name, spec,
            max_results=n_candidates,
            core_mode=core_mode,
        )
        idx = pick_best_pareto(candidates, criteria=criteria)
        return {
            "ok": True,
            "topology": topology_name,
            "criteria": criteria,
            "candidates": [
                {"scoring": c.scoring, "mas": c.mas, "elapsed_s": c.elapsed_s}
                for c in candidates
            ],
            "picked_index": idx,
            "reason": (
                f"{criteria}: candidate[{idx}] "
                f"{candidates[idx].core_shape_name} "
                f"score={candidates[idx].scoring:.3f}"
            ),
        }
    except Exception as exc:  # noqa: BLE001 — surface PyOM errors verbatim
        return {
            "ok": False,
            "topology": topology_name,
            "error": f"{type(exc).__name__}: {exc}",
        }


def stage2_pick_magnetics(
    spec: Mapping[str, Any],
    topologies: Sequence[str],
    *,
    n_candidates: int = 5,
    pick_criteria: str = "lowest_losses",
    core_mode: str = "standard cores",
    parallel: bool = True,
    max_workers: int | None = None,
) -> Stage2Result:
    """Fast-Pareto magnetic pick for every topology in ``topologies``.

    Parallelism: defaults to ``ProcessPoolExecutor`` because PyMKF
    holds non-thread-safe C++ state per process. Each worker imports
    PyOM independently. On a 24-thread box this fans out cleanly.
    Set ``parallel=False`` to debug or to inspect any worker crash.
    """
    if pick_criteria not in PARETO_CRITERIA:
        raise FullDesignError(
            f"unknown pick_criteria {pick_criteria!r}; supported: {PARETO_CRITERIA}"
        )
    spec_dict = dict(spec)  # ensure picklable
    arg_list = [
        (t, spec_dict, int(n_candidates), pick_criteria, core_mode)
        for t in topologies
    ]

    payloads: list[dict[str, Any]]
    if parallel and len(arg_list) > 1:
        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_stage2_pick_one, a) for a in arg_list]
            payloads = [f.result() for f in as_completed(futures)]
    else:
        payloads = [_stage2_pick_one(a) for a in arg_list]

    picks: list[TopologyPick] = []
    failures: list[tuple[str, str]] = []
    for p in payloads:
        if not p.get("ok"):
            failures.append((p["topology"], p["error"]))
            continue
        candidates = [
            MagneticDesign(
                scoring=float(c["scoring"]),
                mas=dict(c["mas"]),
                elapsed_s=float(c["elapsed_s"]),
            )
            for c in p["candidates"]
        ]
        entry = get(p["topology"])
        picks.append(TopologyPick(
            topology=entry,
            main_magnetic=candidates[p["picked_index"]],
            candidates=tuple(candidates),
            pick_reason=p["reason"],
            pick_criteria=p["criteria"],
        ))

    # Sort picks by their pick scoring (lower = lower losses), keeping
    # the best-performing topologies near the front for downstream
    # ranking.
    picks.sort(key=lambda tp: tp.main_magnetic.scoring)
    return Stage2Result(
        spec=spec,
        picks=tuple(picks),
        failures=tuple(failures),
    )


# ---------------------------------------------------------------------------
# Stages 3 & 4 — placeholders, wired in a follow-up
# ---------------------------------------------------------------------------
#
# Stage 3 needs:
#   - Ideal-component ngspice deck (existing decomposer + generate_netlist)
#   - simulate_closed_loop → tuned duty
#   - fsw sweep around the spec's nominal, minimising total magnetic +
#     switch losses (Steinmetz core + I²·R copper + (1/2)·Coss·Vds²·fsw FET)
#   - design_magnetics (slow mode) seeded with the tuned operating point
#   - extras_components for outputInductor / resonant tank etc.
#   - heaviside.pipeline.realism.evaluate_tas for the final verdict
#
# Stage 4 ranks survivors by a composite of (efficiency, total volume,
# part count, BOM cost). The composite weighting belongs in the
# design-orchestrator agent prompt — Python here only assembles inputs.


@dataclass(frozen=True, slots=True)
class GatekeeperVerdict:
    """Analytical adversarial review of a realized design (Ray + Nicola)."""
    approved: bool
    objections: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DesignOutcome:
    """Full per-topology outcome after all stages."""
    pick: TopologyPick
    tas: dict[str, Any] | None = None
    verdict_dict: dict[str, Any] | None = None
    gatekeeper: GatekeeperVerdict | None = None
    report: str | None = None
    fsw_optimal: float | None = None
    diagnostics: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Stage 3 — realize (decompose → simulate → realism gate)
# ---------------------------------------------------------------------------


def stage3_realize(
    pick: TopologyPick,
    spec: Mapping[str, Any],
) -> DesignOutcome:
    """Take a Stage 2 TopologyPick and run it through the full pipeline.

    1. design_converter_components (slow-path magnetic + extras)
    2. decompose_from_spec → TAS + ideal netlist
    3. attach_components_to_tas (magnetics from MKF)
    4. assemble_bom_from_tas (select real FET/diode/cap from TAS DB)
    5. enrich_tas_for_realism
    6. inject_parasitics into netlist (Rds_on, Vf, ESR)
    7. simulate with real parasitics (closed-loop or steady-state)
    8. stamp_simulation_results + run_analyst
    9. evaluate_tas → verdict
    """
    from heaviside import bridge as _bridge
    from heaviside.bridge import BridgeError
    from heaviside.catalogue import SelectionError, assemble_bom_from_tas
    from heaviside.decomposer import decompose_from_spec
    from heaviside.decomposer.api import DecomposerError
    from heaviside.pipeline import enrich_tas_for_realism, evaluate_tas
    from heaviside.pipeline.analyst import AnalystError, run_analyst
    from heaviside.sim import SimError, simulate_closed_loop, simulate_steady_state, stamp_simulation_results
    from heaviside.sim.parasitics import inject_parasitics

    topology = pick.topology.name
    spec_dict = dict(spec)
    diagnostics: list[str] = []

    try:
        components = _bridge.design_converter_components(
            topology, spec_dict, max_results=1, use_ngspice=False,
        )
    except (BridgeError, Exception) as exc:
        return DesignOutcome(
            pick=pick,
            diagnostics=(f"component design failed: {exc}",),
        )

    magnetizing_inductance = components.L_authoritative
    spec_dict["desiredInductance"] = magnetizing_inductance
    spec_dict["desiredMagnetizingInductance"] = magnetizing_inductance

    turns_ratios: list[float] = []
    dr = components.main_magnetic.mas.get("inputs", {}).get("designRequirements", {})
    for tr in dr.get("turnsRatios", []):
        if isinstance(tr, dict):
            v = tr.get("nominal") or tr.get("minimum") or tr.get("maximum")
            if v is not None:
                turns_ratios.append(float(v))
        elif isinstance(tr, (int, float)):
            turns_ratios.append(float(tr))

    try:
        netlist, tas = decompose_from_spec(
            topology, spec_dict,
            turns_ratios=turns_ratios,
            magnetizing_inductance=magnetizing_inductance,
        )
    except DecomposerError as exc:
        return DesignOutcome(
            pick=pick, diagnostics=(f"decompose failed: {exc}",),
        )

    try:
        _bridge.attach_components_to_tas(tas, components, topology=topology)
    except Exception as exc:
        diagnostics.append(f"attach failed: {exc}")

    # --- Component selection: stamp real FET/diode/cap from TAS DB ---
    try:
        assemble_bom_from_tas(tas, topology=topology, spec=spec_dict)
    except SelectionError as exc:
        diagnostics.append(f"BOM selection partial: {exc}")
    except Exception as exc:
        diagnostics.append(f"BOM assembly skipped: {exc}")

    try:
        tas = enrich_tas_for_realism(tas, topology=topology, spec=spec_dict)
    except Exception as exc:
        return DesignOutcome(
            pick=pick, tas=tas,
            diagnostics=(*diagnostics, f"enrichment failed: {exc}"),
        )

    # --- Inject real parasitics into the netlist ---
    realistic_netlist = inject_parasitics(netlist, tas)

    try:
        ops = spec_dict.get("operatingPoints") or [{}]
        first_op = ops[0] if isinstance(ops[0], dict) else {}
        vouts = first_op.get("outputVoltages")
        vout_target = (
            float(vouts[0])
            if isinstance(vouts, list) and vouts and isinstance(vouts[0], (int, float))
            else None
        )
        sim_result = None
        if vout_target is not None:
            try:
                sim_result = simulate_closed_loop(
                    realistic_netlist, vout_target=vout_target,
                )
            except SimError:
                pass
        if sim_result is None:
            sim_result = simulate_steady_state(realistic_netlist)
        stamp_simulation_results(tas, sim_result)
    except (SimError, DecomposerError) as exc:
        diagnostics.append(f"sim skipped: {exc}")

    try:
        run_analyst(topology, tas, spec_dict)
    except AnalystError as exc:
        diagnostics.append(f"analyst skipped: {exc}")

    report = evaluate_tas(tas, topology=topology, spec=spec_dict)
    verdict_dict = {
        "verdict": report.verdict.value,
        "summary": report.summary,
        "checks": [
            {"name": c.name, "status": c.status.value, "value": c.value, "margin": c.margin}
            for c in report.checks
        ],
    }

    return DesignOutcome(
        pick=pick,
        tas=tas,
        verdict_dict=verdict_dict,
        diagnostics=tuple(diagnostics),
    )


# ---------------------------------------------------------------------------
# Stage 3b — Gatekeepers (analytical Ray + Nicola)
# ---------------------------------------------------------------------------


_TIGHT_MARGIN_CHECKS = {
    "inductor_isat_margin": 0.3,
    "efficiency_sanity": 0.05,
    "fet_voltage_derating": 0.2,
    "diode_voltage_derating": 0.2,
    "capacitor_voltage_derating": 0.5,
    "duty_cycle_bounds": 0.1,
    "output_voltage_regulation": 0.02,
    "thermal_limit": 20.0,
}


def stage3b_gatekeeper(outcome: DesignOutcome) -> GatekeeperVerdict:
    """Analytical adversarial review — challenges the design on tight margins.

    Ray's role: block on any FAIL or dangerously tight margin.
    Nicola's role: warn on missing checks (UNAVAILABLE) and cross-domain concerns.
    """
    if outcome.verdict_dict is None:
        return GatekeeperVerdict(
            approved=False,
            objections=("no realism verdict — design did not complete",),
            warnings=(),
        )

    checks = outcome.verdict_dict.get("checks", [])
    verdict = outcome.verdict_dict.get("verdict", "")
    objections: list[str] = []
    warnings: list[str] = []

    if verdict == "fail":
        fails = [c["name"] for c in checks if c["status"] == "fail"]
        objections.append(f"realism gate FAIL: {', '.join(fails)}")

    for c in checks:
        name = c["name"]
        margin = c.get("margin")
        status = c["status"]

        if status == "unavailable":
            warnings.append(f"{name}: UNAVAILABLE — not yet enriched")
            continue

        if status == "pass" and margin is not None:
            threshold = _TIGHT_MARGIN_CHECKS.get(name)
            if threshold is not None and margin < threshold:
                warnings.append(
                    f"{name}: margin={margin:.4f} < {threshold} — tight, "
                    "review under worst-case conditions"
                )

    n_unavail = sum(1 for c in checks if c["status"] == "unavailable")
    n_total = len(checks)
    if n_unavail > n_total * 0.5:
        warnings.append(
            f"{n_unavail}/{n_total} checks UNAVAILABLE — design is "
            "only partially validated"
        )

    return GatekeeperVerdict(
        approved=len(objections) == 0,
        objections=tuple(objections),
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Stage 4 — Report generation
# ---------------------------------------------------------------------------


def generate_report(outcome: DesignOutcome) -> str:
    """Generate a text report for a single design outcome."""
    lines: list[str] = []
    topo = outcome.pick.topology.name
    lines.append(f"# Design Report: {topo}")
    lines.append("")

    lines.append("## Magnetic")
    mag = outcome.pick.main_magnetic
    core_name = mag.mas.get("magnetic", {}).get("core", {}).get("name", "?")
    lines.append(f"- Core: {core_name}")
    coil = mag.mas.get("magnetic", {}).get("coil", {})
    for w in coil.get("functionalDescription", []):
        lines.append(f"- {w.get('name', '?')}: N={w.get('numberTurns', '?')}")
    lines.append(f"- Scoring (total losses): {mag.scoring:.4f}")
    lines.append("")

    # BOM section: show selected real components
    if outcome.tas:
        bom_entries: list[str] = []
        for stage in outcome.tas.get("topology", {}).get("stages", []):
            for comp in stage.get("circuit", {}).get("components", []):
                if not isinstance(comp, dict):
                    continue
                prov = comp.get("selection_provenance")
                if not isinstance(prov, dict):
                    continue
                cat = prov.get("category", "?")
                mpn = prov.get("mpn", "?")
                mfr = prov.get("manufacturer", "?")
                tb = prov.get("tiebreaker", "?")
                alts = prov.get("alternatives_considered", 0)
                margins = prov.get("margins", {})
                margin_str = ", ".join(
                    f"{k}={v:.2f}" for k, v in margins.items()
                    if isinstance(v, (int, float)) and v != float("inf")
                )
                bom_entries.append(
                    f"- [{cat.upper()}] {mpn} ({mfr}) — "
                    f"picked by {tb}, {alts} alternatives"
                    + (f", margins: {margin_str}" if margin_str else "")
                )
        if bom_entries:
            lines.append("## BOM (Selected Components)")
            lines.extend(bom_entries)
            lines.append("")

    if outcome.verdict_dict:
        v = outcome.verdict_dict
        lines.append(f"## Realism Gate: {v['verdict'].upper()}")
        s = v.get("summary", {})
        lines.append(f"- pass={s.get('pass',0)} fail={s.get('fail',0)} "
                      f"unavailable={s.get('unavailable',0)} n/a={s.get('not_applicable',0)}")
        lines.append("")
        for c in v.get("checks", []):
            status = c["status"]
            name = c["name"]
            val = c.get("value")
            margin = c.get("margin")
            detail = f"value={val} margin={margin}" if val is not None else ""
            lines.append(f"  [{status:4s}] {name}: {detail}")
        lines.append("")

    if outcome.gatekeeper:
        gk = outcome.gatekeeper
        status = "APPROVED" if gk.approved else "BLOCKED"
        lines.append(f"## Gatekeeper Review: {status}")
        for obj in gk.objections:
            lines.append(f"  [OBJECTION] {obj}")
        for w in gk.warnings:
            lines.append(f"  [WARNING]   {w}")
        lines.append("")

    if outcome.diagnostics:
        lines.append("## Diagnostics")
        for d in outcome.diagnostics:
            lines.append(f"  - {d}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


def full_design(
    spec: Mapping[str, Any],
    *,
    n_candidates_per_topology: int = 5,
    pick_criteria: str = "lowest_losses",
    core_mode: str = "standard cores",
    parallel: bool = True,
    max_workers: int | None = None,
    selector_fn: TopologySelectorFn | None = None,
) -> tuple[Stage1Result, Stage2Result, tuple[DesignOutcome, ...]]:
    """Run Stage 1 → Stage 2 → Stage 3 → rank by verdict.

    Stage 1: dual-path topology screen (static + LLM).
    Stage 2: parallel fast-Pareto magnetic pick per topology.
    Stage 3: realize each pick (decompose → simulate → realism gate).
    Stage 4: rank survivors by realism verdict + scoring.
    """
    stage1 = stage1_topology_screen(spec, selector_fn=selector_fn)

    # Query lesson store: use training lessons to reorder topologies
    from heaviside.pipeline.teacher import load_lessons
    prior_failures = load_lessons(category="realism_fail", severity="error", max_age_days=30)
    prior_design_failures = load_lessons(category="design_failure", severity="error", max_age_days=30)
    training_topo = load_lessons(category="training_topology_match", max_age_days=90)
    training_verdict = load_lessons(category="training_verdict", max_age_days=90)
    training_eta = load_lessons(category="training_efficiency_gap", max_age_days=90)

    # Build topology preference from training: topologies that matched
    # reference designs AND passed verdict get priority
    preferred_topos: list[str] = []
    warned_topos: set[str] = set()
    for l in training_topo:
        if l.severity == "info" and l.topology:  # info = matched
            preferred_topos.append(l.topology)
    for l in (*prior_failures, *prior_design_failures):
        if l.topology in stage1.reconciliation.chosen:
            warned_topos.add(l.topology)

    # Reorder: preferred topologies first, warned last
    chosen = list(stage1.reconciliation.chosen)
    if preferred_topos:
        seen = set()
        reordered = []
        for t in preferred_topos:
            t_norm = t.lower().replace(" ", "_")
            for c in chosen:
                if c == t_norm and c not in seen:
                    reordered.append(c)
                    seen.add(c)
        for c in chosen:
            if c not in seen:
                reordered.append(c)
                seen.add(c)
        chosen = reordered
        logger.info(
            "Teacher: reordered topologies from training lessons — "
            "preferred: %s", ", ".join(preferred_topos[:5]),
        )
    if warned_topos:
        logger.warning(
            "Teacher: %d topologies have recent failure lessons: %s",
            len(warned_topos), ", ".join(sorted(warned_topos)),
        )

    # Log training efficiency insights
    for l in training_eta[:5]:
        logger.info("Teacher (training): %s", l.detail)

    stage2 = stage2_pick_magnetics(
        spec, tuple(chosen),
        n_candidates=n_candidates_per_topology,
        pick_criteria=pick_criteria,
        core_mode=core_mode,
        parallel=parallel,
        max_workers=max_workers,
    )

    outcomes: list[DesignOutcome] = []
    for pick in stage2.picks:
        logger.info("Stage 3: realizing %s", pick.topology.name)
        outcome = stage3_realize(pick, spec)
        v = outcome.verdict_dict
        verdict = v["verdict"] if v else "no_verdict"
        logger.info("Stage 3: %s → %s", pick.topology.name, verdict)

        gk = stage3b_gatekeeper(outcome)
        gk_status = "APPROVED" if gk.approved else "BLOCKED"
        logger.info("Stage 3b: %s → %s (%d objections, %d warnings)",
                     pick.topology.name, gk_status, len(gk.objections), len(gk.warnings))

        outcome = DesignOutcome(
            pick=outcome.pick,
            tas=outcome.tas,
            verdict_dict=outcome.verdict_dict,
            gatekeeper=gk,
            report=generate_report(DesignOutcome(
                pick=outcome.pick, tas=outcome.tas,
                verdict_dict=outcome.verdict_dict, gatekeeper=gk,
                diagnostics=outcome.diagnostics,
            )),
            fsw_optimal=outcome.fsw_optimal,
            diagnostics=outcome.diagnostics,
        )
        outcomes.append(outcome)

    outcomes.sort(key=_outcome_sort_key)

    # Stage 4: Ray + Nicola adversarial review of best design
    if outcomes and outcomes[0].verdict_dict:
        outcomes[0] = _stage4_adversarial_review(outcomes[0])

    # Stage 5: Teacher — analyze failures and store lessons
    from heaviside.pipeline.teacher import review_design_run, summarize_lessons
    lessons = review_design_run(outcomes, spec)
    if lessons:
        logger.info("Stage 5 (teacher): %s", summarize_lessons(lessons))

    return stage1, stage2, tuple(outcomes)


def _stage4_adversarial_review(outcome: DesignOutcome) -> DesignOutcome:
    """Run Ray (engineering) and Nicola (quality) on the best design."""
    try:
        from heaviside.agents.llm_call import call_agent, extract_json_block, LLMCallError
    except ImportError:
        return outcome

    review_input = {
        "verdict": outcome.verdict_dict,
        "topology": outcome.pick.topology.name,
        "diagnostics": list(outcome.diagnostics),
    }
    if outcome.report:
        review_input["report"] = outcome.report[:10000]

    import json
    review_verdicts: list[dict] = []
    reviewer_log = ""

    for reviewer_name in ("ray", "nicola"):
        try:
            raw = call_agent(
                reviewer_name,
                f"CONVERTER DESIGN REVIEW\n\n{json.dumps(review_input, indent=2)}",
                max_tokens=8192,
            )
            reviewer_log += f"\n--- {reviewer_name.upper()} ---\n{raw}\n"
            try:
                verdict_data = extract_json_block(raw)
                verdict_data["reviewer"] = reviewer_name
                review_verdicts.append(verdict_data)
                verdict = verdict_data.get("verdict", "").upper()
                logger.info("Stage 4 (%s): %s", reviewer_name, verdict)
            except LLMCallError:
                logger.warning("Stage 4 (%s): could not parse verdict", reviewer_name)
        except LLMCallError as exc:
            logger.warning("Stage 4 (%s): failed: %s", reviewer_name, exc)

    return DesignOutcome(
        pick=outcome.pick,
        tas=outcome.tas,
        verdict_dict=outcome.verdict_dict,
        gatekeeper=outcome.gatekeeper,
        report=outcome.report,
        fsw_optimal=outcome.fsw_optimal,
        diagnostics=(*outcome.diagnostics, f"ray+nicola: {len(review_verdicts)} reviews"),
    )


def _outcome_sort_key(o: DesignOutcome) -> tuple[int, float]:
    """PASS first, then by magnetic scoring (lower = better losses)."""
    v = o.verdict_dict
    if v and v["verdict"] == "pass":
        rank = 0
    elif v and v["verdict"] == "fail":
        rank = 1
    else:
        rank = 2
    return (rank, o.pick.main_magnetic.scoring)
