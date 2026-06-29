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
        # della-Pollock cutover (abt #48): the magnetic REQUIREMENT (turns ratios + L) comes from
        # KIRCHHOFF's per-topology seed for EVERY topology — the MKF process_converter / design_
        # magnetics_from_converter converter models are retired; MKF designs only the geometry.
        # select_fast_by_isat_margin designs candidates from that seed (via design_magnetics_fast,
        # now Kirchhoff-seeded) and applies the realism gate's gap-aware Isat >= 1.2*Ipeak_worst
        # post-filter so the picked core clears the gate. (The fast-path worker needs diodeVoltageDrop
        # / efficiency / duty seeds the REST endpoint may not carry, so augment first — throw on
        # missing per CLAUDE.md, never default.)
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
# stage3_realize takes a Stage 2 TopologyPick and runs it end to end via the
# Kirchhoff-native realize path (della-Pollock cutover — the MKF
# design_converter_components / decompose_from_spec / assemble_bom_from_tas flow
# was removed):
#   - kirchhoff_adapter.design_from_hs_spec → k_tas (the single TAS the gate reads)
#   - fill_kirchhoff_bom → real FET/diode/cap/controller from the internal DB
#   - design each magnetic GEOMETRY from k_tas's own seeds (ABT #34) + stamp
#   - simulate_regulated + stamp_simulation_results
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
    components: Any,  # vestigial (ABT #34): the magnetic now comes from the k_tas seed,
                      # not components.main_magnetic. Retired with the rest in ABT #36.
    first_op: Mapping[str, Any],
    vout_target: float | None,
) -> None:
    """Kirchhoff backend (cutover Architecture A): Kirchhoff designs + simulates
    the circuit from the real parts HS fills against Kirchhoff's per-component
    requirements + per-magnetic MKF GEOMETRY designed from Kirchhoff's own magnetic
    seed (MKF_MODEL, topology-agnostic — ABT #34), closed-loop REGULATED; the
    regulated operating point is stamped into HS's TAS so the realism gate sees the
    same shape as the MKF path. Fail-loud throughout — an unregulated / non-finite
    point is refused, never stamped."""
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

    try:
        k_tas = _ka.design_from_hs_spec(topology, spec_dict)
        fill_records = fill_kirchhoff_bom(k_tas, topology=topology)
        # ABT #34: design every magnetic GEOMETRY from Kirchhoff's per-component
        # magnetic seed (data.inputs = designRequirements + one excitation per winding),
        # TOPOLOGY-AGNOSTICALLY via calculate_advised_magnetics_fast, and stamp each as
        # MKF_MODEL. MKF is magnetics-GEOMETRY-only here — no topology-specific
        # design_converter_components magnetic (which fails for sepic/cuk/zeta/fsbb,
        # the topologies whose seeds Kirchhoff emits but MKF's topology path cannot
        # design). One design+stamp per magnetic component (transformer, output/resonant
        # inductors), targeted by name so each slot gets its own core.
        _pyom_vendor = _bridge._import_pyom_vendor()
        n_mag = 0
        for st in k_tas.get("topology", {}).get("stages", []):
            for comp in st.get("circuit", {}).get("components", []):
                data = comp.get("data")
                if not isinstance(data, dict) or "magnetic" not in data:
                    continue
                seed = data.get("inputs")
                if not isinstance(seed, Mapping):
                    raise RealizeError(
                        f"kirchhoff backend: magnetic {comp.get('name')!r} in {topology} "
                        f"carries no inputs seed to design from (ABT #34 expects a complete "
                        f"magnetic_inputs envelope on every magnetic)."
                    )
                designs = _bridge.design_magnetic_from_mas_inputs(seed, max_results=1)
                # The fast geometry-advise selects a core + turns but leaves the coil
                # un-wound (no turnsDescription); autocomplete fills the derived coil
                # geometry so MKF can export it as a SPICE subcircuit (MKF_MODEL).
                magnetic = _pyom_vendor.magnetic_autocomplete(designs[0].magnetic, dict(seed))
                stamp_mkf_magnetic(
                    k_tas, magnetic, pyom=_pyom_vendor,
                    component_name=comp.get("name"),
                )
                n_mag += 1
        if n_mag == 0:
            raise RealizeError(f"kirchhoff backend: {topology} k_tas has no magnetic to design")
        # Unify: the gate validates exactly the parts the Kirchhoff sim used
        # (Kirchhoff's requirement is the single selection authority) — power
        # semiconductors (fail-loud) + power capacitors (lenient; aux caps kept).
        unify_hs_tas_semiconductors(tas, fill_records)
        unify_hs_tas_capacitors(tas, fill_records)
        op = _ka.simulate_regulated(k_tas, float(vout_target), topology, fidelity="DATASHEET")
    except (KirchhoffUnavailable, KirchhoffTopologyUnsupported, KirchhoffFillError,
            SimError, _bridge.BridgeError) as exc:
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


# ---------------------------------------------------------------------------
# Stage 3 — Kirchhoff-native realize (ABT #36): k_tas is the SINGLE TAS the gate
# consumes. No design_converter_components (MKF topology path) / decompose_from_spec
# / assemble_bom_from_tas / unify. Kirchhoff designs k_tas; HS fills real parts +
# designs the magnetics from k_tas's own seeds (ABT #34); the realism gate reads k_tas.
# ---------------------------------------------------------------------------


def _seed_worst_peak_current(seed: Mapping[str, Any]) -> float | None:
    """Worst-case |peak| winding current from a Kirchhoff magnetic seed's
    excitations (ABT #34) — the saturation driver for the isat gate.

    For a TRANSFORMER, secondary winding currents are REFERRED to winding 0 (the
    primary) via the turns ratio before taking the worst case — the flux (hence
    saturation) is set by the primary-referred ampere-turns, not the raw secondary
    current (abt #12). turnsRatios[i-1] = N_w0/N_wi, so I_wi referred to w0 is
    I_wi / turnsRatios[i-1]. A single-winding inductor has no turns ratios → the
    bare peak. Without this, a step-down transformer's large secondary current
    inflates Ipeak and the isat margin reads far too low."""
    ops = seed.get("operatingPoints")
    if not isinstance(ops, list) or not ops:
        return None
    excs = ops[0].get("excitationsPerWinding") if isinstance(ops[0], Mapping) else None
    if not isinstance(excs, list) or not excs:
        return None

    def _resolve(x: Any) -> float | None:
        if isinstance(x, (int, float)):
            return float(x)
        if isinstance(x, Mapping):
            for k in ("nominal", "maximum", "minimum"):
                v = x.get(k)
                if isinstance(v, (int, float)):
                    return float(v)
        return None

    trs = [_resolve(t) for t in ((seed.get("designRequirements") or {}).get("turnsRatios") or [])]
    peaks: list[float] = []
    for i, e in enumerate(excs):
        p = (((e or {}).get("current") or {}).get("processed") or {}).get("peak")
        if not isinstance(p, (int, float)):
            continue
        p = abs(float(p))
        if i >= 1 and i - 1 < len(trs) and isinstance(trs[i - 1], (int, float)) and trs[i - 1] > 0:
            p = p / trs[i - 1]  # refer secondary current to winding 0 (primary)
        peaks.append(p)
    return max(peaks) if peaks else None


def _seed_with_isat_margin(seed: Mapping[str, Any], margin: float) -> dict[str, Any]:
    """A copy of the magnetic seed with every winding's CURRENT PEAK scaled by ``margin``
    so the advised core is sized for ``margin``×Ipeak — i.e. it has the saturation headroom
    the realism gate requires (Isat >= margin·Ipeak). Only the peak (the saturation driver)
    is scaled; rms/voltage (loss/flux) are left real so the core is not over-sized for loss.
    The gate is still stamped with the REAL Ipeak (from the unscaled seed)."""
    import copy
    s = copy.deepcopy(dict(seed))
    for op in s.get("operatingPoints", []):
        if not isinstance(op, dict):
            continue
        for exc in op.get("excitationsPerWinding", []):
            proc = ((exc or {}).get("current") or {}).get("processed")
            if isinstance(proc, dict) and isinstance(proc.get("peak"), (int, float)):
                proc["peak"] = abs(float(proc["peak"])) * margin
    return s


def _pinned_magnetic_constraints(pinned_main: Any, bridge_mod: Any) -> tuple[float | None, list[float]]:
    """Extract the (magnetizing inductance, turns-ratios) constraint from the
    frequency-swept main magnetic for della-Pollock Pass 2. The turns ratios are the
    REALIZED ratios computed from the coil's integer turns (N_w0 / N_wi) — name-agnostic,
    so it works for any winding-naming scheme. Lm is the magnetic's achieved inductance."""
    mas = getattr(pinned_main, "mas", None)
    if not isinstance(mas, Mapping):
        return None, []
    lm: float | None
    try:
        lm = float(bridge_mod._harvest_authoritative_inductance(mas))
        if lm <= 0:
            lm = None
    except Exception:  # noqa: BLE001 - Lm is best-effort; absence just means KH derives it
        lm = None
    trs: list[float] = []
    coil = ((mas.get("magnetic") or {}).get("coil") or {}).get("functionalDescription")
    if isinstance(coil, list) and len(coil) >= 2:
        n0 = coil[0].get("numberTurns") if isinstance(coil[0], Mapping) else None
        if isinstance(n0, (int, float)) and n0 > 0:
            for w in coil[1:]:
                ni = w.get("numberTurns") if isinstance(w, Mapping) else None
                if isinstance(ni, (int, float)) and ni > 0:
                    trs.append(float(n0) / float(ni))
    return lm, trs


def _design_ktas_magnetics(
    k_tas: dict[str, Any],
    *,
    bridge_mod: Any,
    pyom_vendor: Any,
    stamp_fn: Any,
    main_name: str | None = None,
    pinned_main: Any = None,
) -> int:
    """Design every magnetic GEOMETRY in ``k_tas`` from its own seed (topology-agnostic,
    ABT #34), wind the coil, stamp it MKF_MODEL, and stamp the gate's ``isat`` /
    ``ipeak_worst`` flat fields (isat from the designed core, ipeak from the seed
    excitation). The core is designed for a SATURATION MARGIN (Isat >= _GATE_ISAT_MARGIN·Ipeak)
    so it passes the realism gate's isat check AND has the headroom to deliver full output
    without saturating (a core sized right at Ipeak caps Vout below target). Returns the count
    designed. Fail-loud on a seed-less magnetic.

    della-Pollock Pass 2: when ``pinned_main`` (a ``MagneticDesign``) and ``main_name`` are
    given, the MAIN magnetic is NOT re-designed — the frequency-swept magnetic is FIXED and
    stamped as-is (the converter is built around it). SECONDARY magnetics (output/resonant
    inductors) are still designed fresh from their KH seeds."""
    n = 0
    for st in k_tas.get("topology", {}).get("stages", []):
        for comp in st.get("circuit", {}).get("components", []):
            data = comp.get("data")
            if not isinstance(data, dict) or "magnetic" not in data:
                continue
            seed = data.get("inputs")
            if not isinstance(seed, Mapping):
                raise RealizeError(
                    f"kirchhoff-native: magnetic {comp.get('name')!r} has no inputs seed "
                    f"(ABT #34 expects a complete magnetic_inputs envelope)."
                )
            if pinned_main is not None and main_name is not None and comp.get("name") == main_name:
                # della-Pollock: FIX the frequency-swept main magnetic, don't re-design it.
                d = pinned_main
            else:
                designs = bridge_mod.design_magnetic_from_mas_inputs(
                    _seed_with_isat_margin(seed, _GATE_ISAT_MARGIN), max_results=1)
                d = designs[0]
            # isat from the designed core at its achieved L; ipeak from the SATURATION DRIVER.
            try:
                L = float(bridge_mod._harvest_authoritative_inductance(d.mas))
                isat = bridge_mod._isat_from_mas(d.magnetic, L)
            except Exception:  # noqa: BLE001 - isat is best-effort; the gate marks it unavailable if absent
                isat = None
            # For a TRANSFORMER the flux (hence saturation) is set by the MAGNETIZING current, not the
            # winding LOAD current (which is balanced by the secondary ampere-turns) — abt #12. Use the
            # realized magnetizing-current peak from the designed MAS; fall back to the seed winding peak.
            # A single-winding INDUCTOR has no turns ratios, so its winding current IS the driver.
            is_transformer = bool((seed.get("designRequirements") or {}).get("turnsRatios"))
            ipk = (bridge_mod._ipeak_from_mas(d) if is_transformer else None) or _seed_worst_peak_current(seed)
            if isinstance(isat, (int, float)) and isat > 0 and ipk is not None:
                comp["isat"] = float(isat)
                comp["ipeak_worst"] = float(ipk)
            # The fast advise leaves the coil un-wound; autocomplete fills the coil
            # geometry so it can be exported as a SPICE subcircuit (MKF_MODEL).
            magnetic = pyom_vendor.magnetic_autocomplete(d.magnetic, dict(seed))
            # Fail-loud: the MKF fast advise sizes the core on the primary area-product
            # then sets inductance-driven turns AFTER, with no windability gate — so a
            # high-Lm/low-current transformer can get a core whose window cannot hold the
            # turns. autocomplete then SILENTLY returns a coil with no turnsDescription,
            # which only blows up later in export. Surface it here, at the magnetic, with
            # the core context. (Root fix is MKF-side; see the abt fast-advise issue.)
            if not (magnetic.get("coil") or {}).get("turnsDescription"):
                raise RealizeError(
                    f"kirchhoff-native: magnetic {comp.get('name')!r} ({d.core_shape_name}) "
                    f"could not be wound — MKF fast-advise picked a core whose window cannot "
                    f"hold the inductance-driven turns (un-windable transformer). MKF fast-advise "
                    f"needs a windability/fill-factor gate."
                )
            stamp_fn(k_tas, magnetic, pyom=pyom_vendor, component_name=comp.get("name"))
            n += 1
    return n


def _bump_ktas_semiconductor_requirements(k_tas: dict[str, Any], stresses: Any) -> None:
    """Raise each semiconductor's voltage requirement in ``k_tas`` to the HS realism gate's
    worst-case stress × the gate's per-class derating (FET 1.5×, diode 1.3×), so the BOM fill
    picks a part that passes the gate even when Kirchhoff's design-time stress estimate is lower
    than the gate's worst-case (abt #52 stress reconciliation). Only ever RAISES a requirement
    (``max``); never lowers KH's value and never touches current ratings. The gate stamps the same
    worst-case ``vds_stress`` / ``vr_stress`` on every device of a class, so this matches exactly
    what the gate will later check."""
    vds = getattr(stresses, "vds_stress", None)
    vr = getattr(stresses, "vr_stress", None)
    vw = getattr(stresses, "v_working", None)
    fet_floor = float(vds) * _GATE_FET_DERATING_RATIO if isinstance(vds, (int, float)) and vds > 0 else None
    dio_floor = float(vr) * _GATE_DIODE_DERATING_RATIO if isinstance(vr, (int, float)) and vr > 0 else None
    cap_floor = float(vw) * _GATE_CAP_DERATING_RATIO if isinstance(vw, (int, float)) and vw > 0 else None
    if fet_floor is None and dio_floor is None and cap_floor is None:
        return
    for st in k_tas.get("topology", {}).get("stages", []):
        for comp in st.get("circuit", {}).get("components", []):
            data = comp.get("data")
            if not isinstance(data, dict):
                continue
            req = data.get("inputs", {}).get("designRequirements")
            if not isinstance(req, dict):
                continue
            semi = data.get("semiconductor")
            if isinstance(semi, dict) and "mosfet" in semi and fet_floor is not None:
                cur = req.get("ratedDrainSourceVoltage")
                req["ratedDrainSourceVoltage"] = max(float(cur), fet_floor) if isinstance(cur, (int, float)) else fet_floor
            elif isinstance(semi, dict) and "diode" in semi and dio_floor is not None:
                cur = req.get("ratedReverseVoltage")
                req["ratedReverseVoltage"] = max(float(cur), dio_floor) if isinstance(cur, (int, float)) else dio_floor
            elif "capacitor" in data and cap_floor is not None:
                cur = req.get("ratedVoltage")
                req["ratedVoltage"] = max(float(cur), cap_floor) if isinstance(cur, (int, float)) else cap_floor


def _stamp_ktas_gate_stresses(
    k_tas: dict[str, Any],
    fill_records: list[dict[str, Any]],
    stresses: Any,
) -> None:
    """Stamp the realism gate's flat rating+stress fields onto k_tas power
    semiconductors / capacitors: ratings from the Kirchhoff-fill SELECTION, operating
    stress from the analytical worst-case ``ComponentStresses``. This replaces the
    decompose→assemble→unify path. INTERIM stress source: ABT #35 will swap the
    analytical stress for the per-component SIMULATED excitation (the correct source).
    Falls back to the requirement's rated value (conservative: derating ratio→~1) when
    a topology has no analytical stress for that class."""
    from heaviside.catalogue.assemble import _stamp_capacitor, _stamp_diode, _stamp_mosfet

    def _pick(stress: Any, rating: Any) -> float | None:
        """Operating stress for the gate: prefer the analytical stress; fall back to the
        Kirchhoff requirement's rated value (conservative — derating ratio→~1); None if
        neither is a positive number (then the field is left unstamped and the gate
        marks that derating check UNAVAILABLE rather than crashing on a bad value)."""
        if isinstance(stress, (int, float)) and stress > 0:
            return float(stress)
        if isinstance(rating, (int, float)) and rating > 0:
            return float(rating)
        return None

    by_name = {
        r["name"]: r
        for r in fill_records
        if r.get("filled") and r.get("selection") is not None and r.get("requirement") is not None
    }
    for st in k_tas.get("topology", {}).get("stages", []):
        for comp in st.get("circuit", {}).get("components", []):
            rec = by_name.get(comp.get("name"))
            if rec is None:
                continue
            sel, req, fam, kind = rec["selection"], rec["requirement"], rec["family"], rec["kind"]
            if fam == "semiconductor" and kind == "mosfet":
                vds = _pick(getattr(stresses, "vds_stress", None), req.get("ratedDrainSourceVoltage"))
                ids = _pick(getattr(stresses, "id_stress", None), req.get("ratedContinuousDrainCurrent"))
                if vds is not None and ids is not None:
                    _stamp_mosfet(comp, sel, stress_vds=vds, stress_id=ids)
            elif fam == "semiconductor" and kind == "diode":
                vr = _pick(getattr(stresses, "vr_stress", None), req.get("ratedReverseVoltage"))
                ifa = _pick(getattr(stresses, "if_avg_stress", None), req.get("ratedForwardCurrent"))
                if vr is not None and ifa is not None:
                    _stamp_diode(comp, sel, stress_vr=vr, stress_if_avg=ifa)
            elif fam == "capacitor":
                vw = _pick(getattr(stresses, "v_working", None), req.get("ratedVoltage"))
                ir = _pick(getattr(stresses, "i_ripple", None), req.get("minimumRippleCurrent"))
                if vw is not None and ir is not None:
                    _stamp_capacitor(comp, sel, stress_v=vw, stress_ripple=ir)


# The HS realism gate's per-device-class voltage-derating rules
# (realism.check_fet/diode/capacitor_voltage_derating): FET vds_rated >= 1.5*stress,
# diode vrrm_rated >= 1.3*v_reverse, cap v_rated >= 1.5*v_working. Kirchhoff's per-class
# derating knobs are aligned to 1/these so filled parts meet the gate by class.
_GATE_FET_DERATING_RATIO = 1.5
_GATE_DIODE_DERATING_RATIO = 1.3
_GATE_CAP_DERATING_RATIO = 1.5

# The realism gate's inductor saturation rule (Isat >= 1.2*Ipeak,
# heaviside/pipeline/realism.check_inductor_isat_margin), with a little headroom so the
# discrete core the advise picks clears 1.2x AND has room to deliver full output without
# saturating. The magnetic is designed for this margin × the real peak current.
_GATE_ISAT_MARGIN = 1.3


def _harvest_magnetic_design_into_spec(k_tas: dict[str, Any], spec_dict: dict[str, Any]) -> dict[str, Any]:
    """Return spec_dict with desiredTurnsRatios / desiredMagnetizingInductance / desiredInductance
    filled from k_tas's transformer magnetic seed (the one with turnsRatios — not a filter inductor),
    if absent. The analytical stress deriver needs these for isolated topologies; in the native path
    Kirchhoff's own seed is the source (no design_converter_components)."""
    main_dr = None
    for st in k_tas.get("topology", {}).get("stages", []):
        for comp in st.get("circuit", {}).get("components", []):
            data = comp.get("data")
            if isinstance(data, dict) and "magnetic" in data:
                dr = data.get("inputs", {}).get("designRequirements", {})
                if isinstance(dr, dict) and dr.get("turnsRatios"):
                    main_dr = dr
                    break
        if main_dr is not None:
            break
    if main_dr is None:
        return spec_dict   # non-isolated: no turns ratio to supply
    out = dict(spec_dict)
    if "desiredTurnsRatios" not in out:
        trs = []
        for tr in main_dr.get("turnsRatios", []):
            v = tr.get("nominal") if isinstance(tr, dict) else tr
            if isinstance(v, (int, float)):
                trs.append(float(v))
        if trs:
            out["desiredTurnsRatios"] = trs
    lm = main_dr.get("magnetizingInductance")
    lm = lm.get("nominal") if isinstance(lm, dict) else lm
    if isinstance(lm, (int, float)) and lm > 0:
        out.setdefault("desiredMagnetizingInductance", float(lm))
        out.setdefault("desiredInductance", float(lm))
    return out


def _realize_via_kirchhoff(
    topology: str,
    spec_dict: dict[str, Any],
    pick: TopologyPick,
    *,
    pinned_main: "MagneticDesign | None" = None,
) -> "DesignOutcome":
    """ABT #36 — single-TAS cutover. Kirchhoff designs ``k_tas`` (CIAS stages +
    per-component requirements); HS fills real parts (``fill_kirchhoff_bom``) and
    designs the magnetics from k_tas's own seeds (ABT #34, topology-agnostic — so
    sepic/cuk/zeta/fsbb work, which MKF's topology path cannot). The realism gate
    reads k_tas directly. NO design_converter_components / decompose_from_spec /
    assemble_bom_from_tas / unify. Fail-loud: an unregulated/non-finite point is refused."""
    import math

    from heaviside import bridge as _bridge
    from heaviside.catalogue.kirchhoff_fill import (
        KirchhoffFillError,
        fill_kirchhoff_bom,
        stamp_mkf_magnetic,
    )
    from heaviside.decomposer import kirchhoff_adapter as _ka
    from heaviside.decomposer.kirchhoff_adapter import (
        KirchhoffTopologyUnsupported,
        KirchhoffUnavailable,
    )
    from heaviside.pipeline.analyst import AnalystError, run_analyst
    from heaviside.pipeline.stress import StressDerivationError, derive_stresses
    from heaviside.sim import SimError
    from heaviside.sim.runner import SimResult, stamp_simulation_results
    from heaviside.stages.realism_gate import evaluate as evaluate_tas

    ops = spec_dict.get("operatingPoints") or [{}]
    first_op = ops[0] if isinstance(ops[0], dict) else {}
    vouts = first_op.get("outputVoltages")
    vout_target = (
        float(vouts[0])
        if isinstance(vouts, list) and vouts and isinstance(vouts[0], (int, float))
        else None
    )
    if vout_target is None:
        raise RealizeError(f"kirchhoff-native requires a regulation target (vout) for {topology}")
    vin = first_op.get("inputVoltage")
    if not isinstance(vin, (int, float)):
        iv = spec_dict.get("inputVoltage")
        if isinstance(iv, Mapping):
            vin = iv.get("nominal") or iv.get("minimum") or iv.get("maximum")
    if not isinstance(vin, (int, float)) or vin <= 0:
        raise RealizeError(f"kirchhoff-native: no input voltage resolved for {topology}")

    # Align Kirchhoff's component REQUIREMENT derating with the HS realism gate, PER DEVICE
    # CLASS (abt #36/#52). Kirchhoff's default vDerate=0.8 (IPC-9592, 1.25x) is looser than the
    # gate's rules, so parts filled to the 1.25x requirement fail the gate. Kirchhoff now exposes
    # per-class derating knobs (vDerateMosfet / vDerateDiode / vDerateCapacitor, falling back to
    # vDerate); set each to 1/(gate ratio) so the FET requirement is sized at 1.5x stress, the
    # diode at 1.3x, the capacitor at 1.5x — exactly the gate's per-class derating
    # (realism.check_*_voltage_derating). This affects only the rating requirements, not the
    # magnetic or the deck, so regulation is preserved. Respect explicit caller config.
    # NOTE (abt #52): this aligns the DERATING FACTOR. Residual gate fails (e.g. the acf diode)
    # are a separate STRESS-MODEL mismatch — KH's design-time component stress is lower than the
    # gate's measured worst-case (diode 30V vs 41.5V) — which a derating factor cannot close.
    _gate_derate = {
        "vDerateMosfet": 1.0 / _GATE_FET_DERATING_RATIO,        # 1/1.5
        "vDerateDiode": 1.0 / _GATE_DIODE_DERATING_RATIO,       # 1/1.3
        "vDerateCapacitor": 1.0 / _GATE_CAP_DERATING_RATIO,     # 1/1.5
    }
    _caller_cfg = spec_dict.get("config") if isinstance(spec_dict.get("config"), Mapping) else {}
    _cfg = dict(_caller_cfg)
    for _k, _v in _gate_derate.items():
        if _k not in _cfg and "vDerate" not in _cfg:
            _cfg[_k] = _v
    spec_dict = {**spec_dict, "config": _cfg}

    # della-Pollock Pass 2: pin the frequency-swept MAIN magnetic. Inject its realized
    # magnetizing inductance + turns ratio(s) into the spec so Kirchhoff sizes the REST of
    # the converter around the fixed magnetic (req::provided_inductance / provided_turns_ratio),
    # then below we stamp that exact magnetic instead of re-designing it.
    if pinned_main is not None:
        lm, trs = _pinned_magnetic_constraints(pinned_main, _bridge)
        spec_dict = dict(spec_dict)
        if lm is not None:
            spec_dict.setdefault("desiredInductance", lm)
            spec_dict.setdefault("desiredMagnetizingInductance", lm)
        if trs:
            spec_dict.setdefault("desiredTurnsRatios", trs)

    try:
        k_tas = _ka.design_from_hs_spec(topology, spec_dict)
        # The analytical stress deriver needs the transformer turns ratio + magnetizing
        # inductance for isolated topologies — harvest them from k_tas's OWN magnetic seed
        # (present right after design, before the magnetics are sized). Done BEFORE fill so the
        # gate-stress requirement bump below can run.
        spec_dict = _harvest_magnetic_design_into_spec(k_tas, spec_dict)
        # Stress reconciliation (abt #52): Kirchhoff sizes each semiconductor requirement from its
        # OWN design-time stress, which can be LOWER than the worst-case stress the HS realism gate
        # checks (e.g. acf FET: KH 91.7 V via Vin+Vreset vs gate 100.8 V via 2·Vin_max; acf diode:
        # KH 29.5 V vs gate 41.5 V). A part filled to KH's lower requirement then fails the gate even
        # though the derating FACTOR matches. Bump every semiconductor's voltage requirement to the
        # gate's worst-case stress × the gate's per-class derating BEFORE fill, so the picked part is
        # gate-compliant by construction. We never LOWER a requirement (max), and the gate is not
        # loosened. Best-effort: if the stress engine can't size the class, leave KH's requirement.
        try:
            _gate_stresses = derive_stresses(topology, spec_dict)
        except StressDerivationError:
            _gate_stresses = None
        if _gate_stresses is not None:
            _bump_ktas_semiconductor_requirements(k_tas, _gate_stresses)
        fill_records = fill_kirchhoff_bom(k_tas, topology=topology)
        # Identify the main magnetic component in k_tas so Pass 2 fixes IT (and designs the
        # secondary magnetics fresh). Registry None-binding key, structural fallback.
        main_name = None
        if pinned_main is not None:
            from heaviside.topologies import get as _get_topology
            main_name = _bridge._main_magnetic_seed_from_ktas(_get_topology(topology), k_tas)[0]
        if _design_ktas_magnetics(
            k_tas, bridge_mod=_bridge,
            pyom_vendor=_bridge._import_pyom_vendor(), stamp_fn=stamp_mkf_magnetic,
            main_name=main_name, pinned_main=pinned_main,
        ) == 0:
            raise RealizeError(f"kirchhoff-native: {topology} k_tas has no magnetic to design")
        _stamp_ktas_gate_stresses(k_tas, fill_records,
                                  _gate_stresses if _gate_stresses is not None else derive_stresses(topology, spec_dict))
        op = _ka.simulate_regulated(k_tas, float(vout_target), topology, fidelity="DATASHEET")
    except (KirchhoffUnavailable, KirchhoffTopologyUnsupported, KirchhoffFillError,
            SimError, StressDerivationError, _bridge.BridgeError) as exc:
        raise RealizeError(f"kirchhoff-native realize failed for {topology}: {exc}") from exc

    if not op.get("regulated"):
        raise RealizeError(
            f"kirchhoff-native: {topology} did not regulate to {vout_target} V "
            f"(converged={op.get('converged')}, vout={op.get('vout')}) — refusing an "
            "unregulated operating point for the realism gate"
        )
    vout_m, pin, pout, eff = (
        float(op["vout"]), float(op["pin"]), float(op["pout"]), float(op["efficiency"])
    )
    if not all(math.isfinite(x) for x in (vout_m, pin, pout, eff)) or pin <= 0:
        raise RealizeError(f"kirchhoff-native: non-finite/zero operating point for {topology} (op={op})")
    stamp_simulation_results(
        k_tas,
        SimResult(
            vin=float(vin), iin=pin / vin, vout=vout_m,
            iout=(pout / vout_m if vout_m else 0.0),
            pin=pin, pout=pout, total_losses=pin - pout, efficiency=eff,
        ),
    )
    # Regulated control variable → the gate's duty_cycle_bounds check.
    if op.get("control") == "duty" and isinstance(op.get("value"), (int, float)):
        k_tas["duty"] = float(op["value"])

    # Analyst is best-effort here (efficiency already comes from the regulated sim);
    # a topology the analyst can't model must not sink the design — the gate still runs.
    try:
        run_analyst(topology, k_tas, spec_dict)
    except AnalystError:
        pass

    report = evaluate_tas(k_tas, topology=topology, spec=spec_dict)
    return DesignOutcome(
        pick=pick,
        tas=k_tas,
        verdict_dict={
            "verdict": report.verdict.value,
            "summary": report.summary,
            "checks": [
                {"name": c.name, "status": c.status.value, "value": c.value, "margin": c.margin}
                for c in report.checks
            ],
        },
    )


def stage3_realize(
    pick: TopologyPick,
    spec: Mapping[str, Any],
    *,
    pinned_main: "MagneticDesign | None" = None,
) -> DesignOutcome:
    """Realize the converter via Kirchhoff (della-Pollock cutover).

    Kirchhoff designs the circuit from the spec; HS fills the BOM and designs the SECONDARY
    magnetics from Kirchhoff's per-component seeds (MKF magnetic geometry), then the realism
    gate reads the result. The MAIN magnetic (``pinned_main`` — the frequency-swept choice) is
    fixed; Kirchhoff sizes the rest of the stage around it.

    MKF's converter models (``process_converter``) are no longer used anywhere in HS — MKF
    designs only magnetic GEOMETRY. There is a single realize backend now (Kirchhoff); the old
    MKF decompose/assemble/parasitic-inject/spice-sim path has been removed (abt #48 cutover).
    """
    topology = pick.topology.name
    spec_dict = _augment_converter_spec(dict(spec), topology)
    return _realize_via_kirchhoff(topology, spec_dict, pick, pinned_main=pinned_main)


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
            outcome = stage3_realize(pick, spec)
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
