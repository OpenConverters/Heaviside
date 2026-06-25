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
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
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


class RealizeError(FullDesignError):
    """Raised when a Stage 3 realize step (component design, decompose,
    attach, BOM assembly, enrichment, simulation, analyst) fails.

    Per CLAUDE.md "no fallbacks, no silent shortcuts — throw": a realize
    step that cannot produce its output is a HARD failure, not a diagnostic
    to swallow. Swallowing it lets a degraded TAS reach the realism gate,
    where missing physics inputs become ``UNAVAILABLE`` (never ``FAIL``) and
    a non-physics check (e.g. selection_provenance_complete) can carry a
    PASS verdict — i.e. a design whose physics was never evaluated reads as
    realistic. So every failed step raises, carrying which step and which
    topology failed and chaining the native exception."""


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
            # Augment the spec the same way as the transformer path — the
            # fast-path worker also needs diodeVoltageDrop / efficiency / duty
            # seeds that the REST endpoint doesn't carry (CLAUDE.md: throw on
            # missing, so seed before handing to process_converter).
            aug = _augment_converter_spec(dict(spec), topology_name)
            candidates = select_fast_by_isat_margin(
                topology_name,
                aug,
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
        try:
            with ProcessPoolExecutor(max_workers=max_workers) as pool:
                futures = [pool.submit(_stage2_pick_one, a) for a in arg_list]
                payloads = [f.result() for f in as_completed(futures)]
        except Exception as _pool_exc:
            # Worker processes were killed (OOM on low-memory hosts, or C++
            # extension crash). Fall back to in-process sequential execution so
            # the design still runs; it will be slower but correct.
            logger.warning(
                "stage2: ProcessPool failed (%s) — retrying sequentially",
                type(_pool_exc).__name__,
            )
            payloads = [_stage2_pick_one(a) for a in arg_list]
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
# Stage 3 — realize (decompose → BOM → simulate → realism gate)
# ---------------------------------------------------------------------------
#
# stage3_realize takes a Stage 2 TopologyPick and runs it end to end:
#   - design_converter_components (MKF magnetic + extras)
#   - decompose_from_spec → TAS + ideal netlist
#   - assemble_bom_from_tas → real FET/diode/cap/controller from the internal DB
#   - enrich_tas_for_realism + inject_parasitics
#   - simulate_closed_loop (or steady_state) + run_analyst
#   - realism_gate.evaluate → verdict
# Every step is a hard failure: it raises RealizeError rather than returning a
# degraded outcome (a degraded TAS would slip a non-physics PASS past the gate).
#
# Stage 3b (stage3b_gatekeeper) is the analytical tight-margin gate; Stage 4
# (_stage4_adversarial_review) is the Ray + Nicola LLM panel run on the best
# ranked survivor.


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


def _simulate_kirchhoff_backend(
    tas: dict[str, Any],
    *,
    topology: str,
    spec_dict: dict[str, Any],
    components: Any,
    first_op: Mapping[str, Any],
    vout_target: float | None,
) -> None:
    """Kirchhoff backend (cutover Architecture A): Kirchhoff designs + simulates
    the circuit from the real parts HS fills against Kirchhoff's per-component
    requirements + the della-Pollock MKF magnetic (MKF_MODEL), closed-loop
    REGULATED; the regulated operating point is stamped into HS's TAS so the
    realism gate sees the same shape as the MKF path. Fail-loud throughout — an
    unregulated / non-finite point is refused, never stamped."""
    import math

    from heaviside import bridge as _bridge
    from heaviside.catalogue.kirchhoff_fill import (
        KirchhoffFillError,
        fill_kirchhoff_bom,
        stamp_mkf_magnetic,
        unify_hs_tas_capacitors,
        unify_hs_tas_semiconductors,
    )
    from heaviside.decomposer import kirchhoff_adapter as _ka
    from heaviside.decomposer.kirchhoff_adapter import (
        KirchhoffTopologyUnsupported,
        KirchhoffUnavailable,
    )
    from heaviside.sim import SimError
    from heaviside.sim.runner import SimResult, stamp_simulation_results

    if vout_target is None:
        raise RealizeError(f"kirchhoff backend requires a regulation target (vout) for {topology}")
    vin = first_op.get("inputVoltage")
    if not isinstance(vin, (int, float)):
        iv = spec_dict.get("inputVoltage")
        if isinstance(iv, Mapping):
            vin = iv.get("nominal") or iv.get("minimum") or iv.get("maximum")
    if not isinstance(vin, (int, float)) or vin <= 0:
        raise RealizeError(f"kirchhoff backend: no input voltage resolved for {topology}")

    magnetic_obj = (getattr(components.main_magnetic, "mas", None) or {}).get("magnetic")
    if magnetic_obj is None:
        raise RealizeError(f"kirchhoff backend: no MKF magnetic to stamp for {topology}")
    try:
        k_tas = _ka.design_from_hs_spec(topology, spec_dict)
        fill_records = fill_kirchhoff_bom(k_tas, topology=topology)
        stamp_mkf_magnetic(k_tas, magnetic_obj, pyom=_bridge._import_pyom_vendor())
        # Unify: the gate validates exactly the parts the Kirchhoff sim used
        # (Kirchhoff's requirement is the single selection authority) — power
        # semiconductors (fail-loud) + power capacitors (lenient; aux caps kept).
        unify_hs_tas_semiconductors(tas, fill_records)
        unify_hs_tas_capacitors(tas, fill_records)
        op = _ka.simulate_regulated(k_tas, float(vout_target), topology, fidelity="DATASHEET")
    except (KirchhoffUnavailable, KirchhoffTopologyUnsupported, KirchhoffFillError, SimError) as exc:
        raise RealizeError(f"kirchhoff simulation failed for {topology}: {exc}") from exc

    if not op.get("regulated"):
        raise RealizeError(
            f"kirchhoff backend: {topology} did not regulate to {vout_target} V "
            f"(converged={op.get('converged')}, vout={op.get('vout')}) — refusing an "
            "unregulated operating point for the realism gate"
        )
    vout_m, pin, pout, eff = (
        float(op["vout"]), float(op["pin"]), float(op["pout"]), float(op["efficiency"])
    )
    if not all(math.isfinite(x) for x in (vout_m, pin, pout, eff)) or pin <= 0:
        raise RealizeError(f"kirchhoff backend: non-finite/zero operating point for {topology} (op={op})")
    stamp_simulation_results(
        tas,
        SimResult(
            vin=float(vin),
            iin=pin / vin,
            vout=vout_m,
            iout=(pout / vout_m if vout_m else 0.0),
            pin=pin,
            pout=pout,
            total_losses=pin - pout,
            efficiency=eff,
        ),
    )


def stage3_realize(
    pick: TopologyPick,
    spec: Mapping[str, Any],
    *,
    pinned_main: "MagneticDesign | None" = None,
    spice_config: Mapping[str, Any] | None = None,
    sim_backend: str = "mkf",
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
    from heaviside.pipeline.extract import EnrichmentError
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

    # Bridge / resonant families model their switching cell as a single
    # behavioural PULSE source by default, which leaves no real MOSFETs for
    # the TAS decomposer's bridge stencils to bind (they require SA/SB/SC/SD,
    # S1/S2, etc.). Request the "switch" deck so MKF emits real switches.
    _fam = pick.topology.family
    bridge_mode = "switch" if _fam in ("isolated_bridge", "resonant") else ""

    try:
        components = _bridge.design_converter_components(
            topology,
            spec_dict,
            max_results=1,
            use_ngspice=False,
            pinned_main=pinned_main,
        )
    except BridgeError as exc:
        raise RealizeError(f"component design failed for {topology}: {exc}") from exc

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
        raise RealizeError(f"decompose failed for {topology}: {exc}") from exc

    try:
        _bridge.attach_components_to_tas(tas, components, topology=topology)
    except BridgeError as exc:
        raise RealizeError(f"attach failed for {topology}: {exc}") from exc

    # --- Component selection: stamp real FET/diode/cap from TAS DB ---
    # A partial BOM is a hard failure: it leaves the realism gate without the
    # ratings/stress its physics checks need (they go UNAVAILABLE, not FAIL),
    # so a degraded design can pass on metadata alone. Surface it, don't note it.
    try:
        assemble_bom_from_tas(tas, topology=topology, spec=spec_dict)
    except SelectionError as exc:
        raise RealizeError(f"BOM selection failed for {topology}: {exc}") from exc

    try:
        tas = enrich_tas_for_realism(tas, topology=topology, spec=spec_dict)
    except EnrichmentError as exc:
        raise RealizeError(f"enrichment failed for {topology}: {exc}") from exc

    ops = spec_dict.get("operatingPoints") or [{}]
    first_op = ops[0] if isinstance(ops[0], dict) else {}
    vouts = first_op.get("outputVoltages")
    vout_target = (
        float(vouts[0])
        if isinstance(vouts, list) and vouts and isinstance(vouts[0], (int, float))
        else None
    )
    # Backend selection. "mkf" (default) = MKF netlist + parasitic injection + HS's
    # duty-search sim — unchanged. "kirchhoff" = Kirchhoff designs+sims the circuit
    # (real BOM HS fills + the della-Pollock MKF magnetic as MKF_MODEL), closed-loop
    # regulated; HS keeps its TAS + realism gate. Either way the gate consumes the
    # SAME stamped operating point. No silent fallback between backends.
    if sim_backend == "mkf":
        # --- Inject real parasitics into the netlist ---
        realistic_netlist = inject_parasitics(netlist, tas)
        # A vout target ⇒ closed-loop sim is the right model; if it fails, that is
        # a hard failure — do NOT silently fall back to the open-loop steady-state
        # sim (which measures a different, unregulated quantity). Only run the
        # steady-state path when there is genuinely no regulation target.
        try:
            if vout_target is not None:
                sim_result = simulate_closed_loop(realistic_netlist, vout_target=vout_target)
            else:
                sim_result = simulate_steady_state(realistic_netlist)
            stamp_simulation_results(tas, sim_result)
        except (SimError, DecomposerError) as exc:
            raise RealizeError(f"simulation failed for {topology}: {exc}") from exc
    elif sim_backend == "kirchhoff":
        _simulate_kirchhoff_backend(
            tas, topology=topology, spec_dict=spec_dict, components=components,
            first_op=first_op, vout_target=vout_target,
        )
    else:
        raise RealizeError(f"unknown sim_backend {sim_backend!r} (expected 'mkf' or 'kirchhoff')")

    try:
        run_analyst(topology, tas, spec_dict)
    except AnalystError as exc:
        raise RealizeError(f"analyst failed for {topology}: {exc}") from exc

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


# Topologies whose converter sim runs through the Kirchhoff backend (closed-loop
# regulated, real BOM HS fills + the della-Pollock MKF magnetic as MKF_MODEL).
# EMPTY by default → every topology uses the MKF backend (zero behaviour change).
# Opt in per topology via the HEAVISIDE_KIRCHHOFF_TOPOLOGIES env var (comma-
# separated, "*" = all), or by adding to this set once a topology's full realize
# path is validated end-to-end.
_KIRCHHOFF_TOPOLOGIES: frozenset[str] = frozenset()


def _sim_backend_for(topology: str) -> str:
    """Pick the sim backend for ``topology`` — "kirchhoff" if opted in (env var or
    the registry), else "mkf" (the default; unchanged pipeline)."""
    env = os.environ.get("HEAVISIDE_KIRCHHOFF_TOPOLOGIES")
    if env is not None:
        enabled = {t.strip() for t in env.split(",") if t.strip()}
        if "*" in enabled or topology in enabled:
            return "kirchhoff"
        return "mkf"
    return "kirchhoff" if topology in _KIRCHHOFF_TOPOLOGIES else "mkf"


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
    # training_verdict: severity "info" = the designer produced a PASS design
    # for this topology against a real reference (strong prefer signal);
    # "error" = it could not (deprioritise). Both are applied to ordering below.
    training_verdict = load_lessons(category="training_verdict", max_age_days=90)
    training_eta = load_lessons(category="training_efficiency_gap", max_age_days=90)

    # Build topology preference from training. Positive signals (prefer):
    #   * training_topology_match (info) — matched a reference design;
    #   * training_verdict (info)        — designer PASSed it vs a reference.
    # Negative signals (deprioritise): recent realism/design failures, or a
    # training_verdict error (designer could not pass this topology).
    preferred_topos: list[str] = []
    warned_topos: set[str] = set()
    for l in training_topo:
        if l.severity == "info" and l.topology:  # info = matched
            preferred_topos.append(l.topology)
    for l in training_verdict:
        if not l.topology:
            continue
        if l.severity == "info":
            preferred_topos.append(l.topology)
        elif l.severity == "error":
            warned_topos.add(l.topology)
    for l in (*prior_failures, *prior_design_failures):
        if l.topology in stage1.reconciliation.chosen:
            warned_topos.add(l.topology)

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

    # Reorder by teacher lessons: preferred first, warned last (see helper).
    chosen = _order_topologies_by_lessons(
        chosen, preferred=preferred_topos, warned=warned_topos
    )
    if preferred_topos:
        logger.info(
            "Teacher: reordered topologies from training lessons — preferred: %s",
            ", ".join(preferred_topos[:5]),
        )
    if warned_topos:
        logger.warning(
            "Teacher: %d topologies deprioritised (recent failure lessons): %s",
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
        try:
            outcome = stage3_realize(
                pick, spec, sim_backend=_sim_backend_for(pick.topology.name)
            )
        except RealizeError as exc:
            # Multi-topology screen: one topology that cannot be realized must
            # not abort the whole batch, but it is NOT silently dropped either —
            # it is recorded with no verdict (sorts last via _outcome_sort_key)
            # and surfaced in diagnostics so the failure stays visible.
            logger.warning("Stage 3: %s → realize FAILED: %s", pick.topology.name, exc)
            outcomes.append(DesignOutcome(pick=pick, diagnostics=(f"realize failed: {exc}",)))
            continue
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


def _stage4_adversarial_review(outcome: DesignOutcome, *, progress: Any = None) -> DesignOutcome:
    """Run Ray (engineering) and Nicola (quality) on the best design.

    ``progress`` (optional) is forwarded to the reviewer panel so a caller can
    surface per-reviewer stage progress (Ray, then Nicola).

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
            progress=progress,
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


def _order_topologies_by_lessons(
    chosen: Sequence[str],
    *,
    preferred: Sequence[str],
    warned: Iterable[str],
) -> list[str]:
    """Reorder ``chosen`` topologies by teacher lessons: preferred first, warned
    last, neutral in between.

    Topology names are matched case-insensitively with spaces normalised to
    underscores. A topology that is BOTH preferred and warned (a recent pass AND
    a recent failure) keeps neutral priority — the mixed signal cancels. The
    sort is stable, so ``preferred`` order is preserved within the prefer bucket
    and ``chosen`` order is preserved within the neutral and warned buckets. No
    topology is added or dropped — this is a pure reordering of ``chosen``."""

    def _norm(t: str) -> str:
        return t.lower().replace(" ", "_")

    pref_rank = {_norm(t): i for i, t in enumerate(preferred)}
    warned_norm = {_norm(t) for t in warned}

    def _priority(c: str) -> tuple[int, int]:
        is_pref = c in pref_rank
        is_warned = c in warned_norm
        if is_pref and not is_warned:
            return (0, pref_rank[c])
        if is_warned and not is_pref:
            return (2, 0)
        return (1, 0)

    return sorted(chosen, key=_priority)


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
