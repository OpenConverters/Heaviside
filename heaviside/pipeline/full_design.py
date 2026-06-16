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

import contextlib
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from heaviside.agents.magnetic_picker import (
    PARETO_CRITERIA,
    pick_best_pareto,
)
from heaviside.bridge import (
    BridgeError,
    MagneticDesign,
    design_magnetics,
    select_fast_by_isat_margin,
)
from heaviside.pipeline.topology_screen import (
    TopologyReconciliation,
    reconcile_topology_choices,
)

# Designer composes the reusable stages (Phase 2): topology feasibility and
# the final realism verdict go through heaviside.stages, the single tested
# interface. These are behaviour-identical aliases of the underlying engines.
from heaviside.stages.topology_id import feasible as feasible_topology_names
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
            f"topology-selector agent reply has no JSON block. Response: {text!r}"
        )
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise FullDesignError(
            f"topology-selector JSON parse failed: {exc}. Block: {match.group(1)!r}"
        ) from exc
    viable = payload.get("viable")
    if not isinstance(viable, list) or not all(isinstance(n, str) for n in viable):
        raise FullDesignError(f"topology-selector JSON missing or malformed 'viable': {payload!r}")
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
        static_names,
        agent_names,
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


def _is_transformer_topology(topology_name: str) -> bool:
    """Transformer/isolated topologies need the converter-design adviser
    (it derives turns ratios + magnetising L); the fast flux-density adviser
    returns no core for them (powerMean 0)."""
    try:
        fam = get(topology_name).family
    except Exception:
        return False
    return fam.startswith("isolated") or fam == "resonant"


def _augment_converter_spec(spec: dict[str, Any], topology: str | None = None) -> dict[str, Any]:
    """Add the converter-level design constraints MKF's models require
    (duty-cycle ceiling + FET Vds budget + per-model operating-point keys).

    Thin wrapper over the deterministic ``converter_spec_build`` PEAS stage,
    which owns the BASE-schema construction. Kept as a module-local name so the
    existing in-pipeline call sites stay unchanged. See
    ``heaviside/stages/converter_spec_build.py`` and
    ``docs/converter_designer_master_plan.md`` (step B0).
    """
    from heaviside.stages import converter_spec_build

    return converter_spec_build.build(spec, topology)


def _stage2_pick_one(args: tuple[str, dict, int, str, str]) -> dict[str, Any]:
    """Worker for the ProcessPoolExecutor. Pure data in / out so the
    pool can pickle it. Returns a payload dict the parent reassembles
    into a TopologyPick (or surfaces as a failure)."""
    topology_name, spec, n_candidates, criteria, core_mode = args
    try:
        if _is_transformer_topology(topology_name):
            # Slow converter-design adviser: MKF derives turns ratios + L.
            aug = _augment_converter_spec(dict(spec), topology_name)
            try:
                candidates = design_magnetics(
                    topology_name,
                    aug,
                    max_results=n_candidates,
                    core_mode=core_mode,
                )
            except BridgeError as exc:
                # Tier-2: MKF's MagneticFilterSaturation has no derating
                # headroom, so with coreAdviserSaturationMargin=1.5 (set by
                # the bridge) the stock-only catalogue (~1.5K cores) can leave
                # zero candidates for high-step-down isolated topologies even
                # though the full 10K-core catalogue has many. This mirrors
                # the documented tier-2 fallback in
                # bridge.design_converter_components: when stock-only yields
                # zero designs, retry against the full catalogue (the same
                # real cores stage 3 will design against). Only widens the
                # search — no fabricated values. Re-raise anything else.
                if "zero designs" not in str(exc):
                    raise
                # Widen the requested pool too: MKF's CoreAdviser prunes its
                # top scorers in MagneticFilterSaturation, so a max_results=1
                # request can still surface zero passers even against the full
                # catalogue. Asking for a larger pool lets passing candidates
                # appear; we keep the top n_candidates afterwards.
                fallback_pool = max(int(n_candidates), 50)
                candidates = design_magnetics(
                    topology_name,
                    aug,
                    max_results=fallback_pool,
                    core_mode=core_mode,
                    use_only_cores_in_stock=False,
                )
                candidates = candidates[: max(int(n_candidates), 1)]
        else:
            # Fast path: apply the slow path's Isat post-filter so the
            # picked core clears gap-aware Isat >= 1.2*Ipeak_worst (the
            # realism gate's criterion). Without this the fast adviser's
            # lowest-loss top scorer can be undersized against worst-case
            # peak current and fail inductor_isat_margin downstream.
            candidates = select_fast_by_isat_margin(
                topology_name,
                spec,
                n_candidates=n_candidates,
                core_mode=core_mode,
            )
        idx = pick_best_pareto(candidates, criteria=criteria)
        return {
            "ok": True,
            "topology": topology_name,
            "criteria": criteria,
            "candidates": [
                {"scoring": c.scoring, "mas": c.mas, "elapsed_s": c.elapsed_s} for c in candidates
            ],
            "picked_index": idx,
            "reason": (
                f"{criteria}: candidate[{idx}] "
                f"{candidates[idx].core_shape_name} "
                f"score={candidates[idx].scoring:.3f}"
            ),
        }
    except Exception as exc:
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
    arg_list = [(t, spec_dict, int(n_candidates), pick_criteria, core_mode) for t in topologies]

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
        picks.append(
            TopologyPick(
                topology=entry,
                main_magnetic=candidates[p["picked_index"]],
                candidates=tuple(candidates),
                pick_reason=p["reason"],
                pick_criteria=p["criteria"],
            )
        )

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
    *,
    pinned_main: "MagneticDesign | None" = None,
    spice_config: Mapping[str, Any] | None = None,
) -> DesignOutcome:
    """Take a Stage 2 TopologyPick and run it through the full pipeline.

    ``pinned_main`` (closed-loop designer): the main magnetic the frequency
    sweep already chose. When given, ``design_converter_components`` uses it
    verbatim instead of re-designing the magnetic, so the real converter (BOM,
    netlist, SPICE sim, realism) is built around exactly that magnetic.

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
    from heaviside.pipeline import enrich_tas_for_realism
    from heaviside.pipeline.analyst import AnalystError, run_analyst
    from heaviside.sim import (
        SimError,
        simulate_closed_loop,
        simulate_steady_state,
        stamp_simulation_results,
    )
    from heaviside.sim.parasitics import inject_parasitics
    from heaviside.stages.realism_gate import evaluate as evaluate_tas

    topology = pick.topology.name
    spec_dict = _augment_converter_spec(dict(spec), topology)
    diagnostics: list[str] = []

    # Bridge / resonant families model their switching cell as a single
    # behavioural PULSE source by default, which leaves no real MOSFETs for
    # the TAS decomposer's bridge stencils to bind (they require SA/SB/SC/SD,
    # S1/S2, etc.). Request the "switch" deck so MKF emits real switches.
    try:
        _fam = pick.topology.family
    except Exception:
        _fam = ""
    bridge_mode = "switch" if _fam in ("isolated_bridge", "resonant") else ""

    try:
        components = _bridge.design_converter_components(
            topology,
            spec_dict,
            max_results=1,
            use_ngspice=False,
            pinned_main=pinned_main,
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
    # Make the MKF-derived turns ratios available to BOM assembly / analyst /
    # stress (they read spec.desiredTurnsRatios for isolated topologies).
    if turns_ratios:
        spec_dict["desiredTurnsRatios"] = turns_ratios

    try:
        netlist, tas = decompose_from_spec(
            topology,
            spec_dict,
            turns_ratios=turns_ratios,
            magnetizing_inductance=magnetizing_inductance,
            bridge_simulation_mode=bridge_mode,
            spice_config=dict(spice_config) if spice_config else None,
        )
    except DecomposerError as exc:
        return DesignOutcome(
            pick=pick,
            diagnostics=(f"decompose failed: {exc}",),
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
            pick=pick,
            tas=tas,
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
            with contextlib.suppress(SimError):
                sim_result = simulate_closed_loop(
                    realistic_netlist,
                    vout_target=vout_target,
                )
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
            f"{n_unavail}/{n_total} checks UNAVAILABLE — design is only partially validated"
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
                    f"{k}={v:.2f}"
                    for k, v in margins.items()
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
        lines.append(
            f"- pass={s.get('pass', 0)} fail={s.get('fail', 0)} "
            f"unavailable={s.get('unavailable', 0)} n/a={s.get('not_applicable', 0)}"
        )
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
    progress_cb: Callable[[str, int], None] | None = None,
    restrict_topologies: list[str] | None = None,
) -> tuple[Stage1Result, Stage2Result, tuple[DesignOutcome, ...]]:
    """Run Stage 1 → Stage 2 → Stage 3 → rank by verdict.

    Stage 1: dual-path topology screen (static + LLM).
    Stage 2: parallel fast-Pareto magnetic pick per topology.
    Stage 3: realize each pick (decompose → simulate → realism gate).
    Stage 4: rank survivors by realism verdict + scoring.

    `progress_cb`, if given, is called with (human_message, percent) at coarse
    stage boundaries — the pipeline has no finer-grained hook. Failures in the
    callback never affect the design run.
    """

    def _emit(msg: str, pct: int) -> None:
        if progress_cb is not None:
            with contextlib.suppress(Exception):
                progress_cb(msg, pct)

    # Add converter-level constraints transformer topologies need (duty/Vds);
    # ignored by non-isolated process_converter. Flows to stage 2 + stage 3.
    spec = _augment_converter_spec(dict(spec))

    _emit("Screening feasible topologies", 5)
    stage1 = stage1_topology_screen(spec, selector_fn=selector_fn)

    # Query lesson store: use training lessons to reorder topologies
    from heaviside.pipeline.teacher import load_lessons

    prior_failures = load_lessons(category="realism_fail", severity="error", max_age_days=30)
    prior_design_failures = load_lessons(
        category="design_failure", severity="error", max_age_days=30
    )
    training_topo = load_lessons(category="training_topology_match", max_age_days=90)
    # TODO(training-loop): training_verdict lessons are loaded but not yet
    # applied to topology ordering/selection — the verdict feedback signal
    # is unfinished (see project-next-steps / docs/BACKLOG). `_`-prefixed to
    # mark intentionally-unused until it is wired in (do not silently drop).
    _training_verdict = load_lessons(category="training_verdict", max_age_days=90)
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
    # Hard restriction (opt-in): a caller that pins specific topologies wants
    # ONLY those designed, not the screen's union. `selector_fn` merely
    # *suggests*; `restrict_topologies` is an intersection.
    if restrict_topologies:
        wanted = {t.lower().replace(" ", "_") for t in restrict_topologies}
        restricted = [c for c in chosen if c in wanted]
        # If the screen rejected every pinned topology, honour the pin anyway
        # so the user sees that topology's specific failure, not silence.
        chosen = (
            restricted if restricted else [t.lower().replace(" ", "_") for t in restrict_topologies]
        )
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
            "Teacher: reordered topologies from training lessons — preferred: %s",
            ", ".join(preferred_topos[:5]),
        )
    if warned_topos:
        logger.warning(
            "Teacher: %d topologies have recent failure lessons: %s",
            len(warned_topos),
            ", ".join(sorted(warned_topos)),
        )

    # Log training efficiency insights
    for l in training_eta[:5]:
        logger.info("Teacher (training): %s", l.detail)

    _emit(f"Sizing magnetics for {len(chosen)} topologies", 15)
    stage2 = stage2_pick_magnetics(
        spec,
        tuple(chosen),
        n_candidates=n_candidates_per_topology,
        pick_criteria=pick_criteria,
        core_mode=core_mode,
        parallel=parallel,
        max_workers=max_workers,
    )

    outcomes: list[DesignOutcome] = []
    n_picks = len(stage2.picks) or 1
    for i, pick in enumerate(stage2.picks):
        # Stage 3 spans 25%→90% across the picks.
        pct = 25 + int(65 * i / n_picks)
        _emit(f"Realizing & simulating {pick.topology.name} ({i + 1}/{len(stage2.picks)})", pct)
        logger.info("Stage 3: realizing %s", pick.topology.name)
        outcome = stage3_realize(pick, spec)
        v = outcome.verdict_dict
        verdict = v["verdict"] if v else "no_verdict"
        logger.info("Stage 3: %s → %s", pick.topology.name, verdict)

        gk = stage3b_gatekeeper(outcome)
        gk_status = "APPROVED" if gk.approved else "BLOCKED"
        logger.info(
            "Stage 3b: %s → %s (%d objections, %d warnings)",
            pick.topology.name,
            gk_status,
            len(gk.objections),
            len(gk.warnings),
        )

        outcome = DesignOutcome(
            pick=outcome.pick,
            tas=outcome.tas,
            verdict_dict=outcome.verdict_dict,
            gatekeeper=gk,
            report=generate_report(
                DesignOutcome(
                    pick=outcome.pick,
                    tas=outcome.tas,
                    verdict_dict=outcome.verdict_dict,
                    gatekeeper=gk,
                    diagnostics=outcome.diagnostics,
                )
            ),
            fsw_optimal=outcome.fsw_optimal,
            diagnostics=outcome.diagnostics,
        )
        outcomes.append(outcome)

    outcomes.sort(key=_outcome_sort_key)

    # Stage 4: Ray + Nicola adversarial review of best design
    if outcomes and outcomes[0].verdict_dict:
        _emit("Final review (Ray + Nicola)", 95)
        outcomes[0] = _stage4_adversarial_review(outcomes[0])
    _emit("Done", 100)

    # Stage 5: Teacher — analyze failures and store lessons
    from heaviside.pipeline.teacher import review_design_run, summarize_lessons

    lessons = review_design_run(outcomes, spec)
    if lessons:
        logger.info("Stage 5 (teacher): %s", summarize_lessons(lessons))

    return stage1, stage2, tuple(outcomes)


def _stage4_adversarial_review(outcome: DesignOutcome) -> DesignOutcome:
    """Run Ray (engineering) and Nicola (quality) on the best design.

    Per CLAUDE.md "no silent fallbacks": a reviewer agent that cannot produce
    a verdict (LLM unreachable, timeout, or unparseable output even after
    retries) is a HARD failure — a design without its adversarial review is not
    a valid result, so we raise rather than quietly recording "0 reviews". A
    reviewer that runs and returns a negative verdict is a valid review (not a
    failure); that is recorded and surfaced, not raised.
    """
    from heaviside.agents.llm_call import LLMCallError
    from heaviside.stages.reviewer_panel import review as panel_review

    review_input = {
        "verdict": outcome.verdict_dict,
        "topology": outcome.pick.topology.name,
        "diagnostics": list(outcome.diagnostics),
    }
    if outcome.report:
        review_input["report"] = outcome.report[:10000]

    try:
        panel = panel_review(
            review_input,
            scope=(
                "POWER-STAGE AUTO-DESIGN — topology selection, magnetics sizing, "
                "component selection/BOM, steady-state simulation, and realism "
                "checks. Control loop, gate drive, protection, EMI filter, and PCB "
                "layout are OUT OF SCOPE for this automated stage."
            ),
            title="CONVERTER DESIGN REVIEW",
        )
    except LLMCallError as exc:
        raise FullDesignError(
            f"Stage 4 adversarial review: a reviewer could not produce a valid "
            f"verdict ({exc}). A design without its Ray+Nicola review is not a "
            f"valid result — aborting (no silent fallback)."
        ) from exc

    for v in panel.verdicts:
        logger.info("Stage 4 (%s): %s", v.reviewer, v.verdict)

    return DesignOutcome(
        pick=outcome.pick,
        tas=outcome.tas,
        verdict_dict=outcome.verdict_dict,
        gatekeeper=outcome.gatekeeper,
        report=outcome.report,
        fsw_optimal=outcome.fsw_optimal,
        diagnostics=(*outcome.diagnostics, f"ray+nicola: {len(panel.verdicts)} reviews"),
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
