"""Closed-loop converter designer — builds the real converter around the magnetic
the frequency sweep chose, then re-simulates and reviews it.

This is the piece that closes the loop the master-plan stages only prepared: it
takes the swept-and-picked main magnetic (NOT re-designed), assembles the real
converter around it, runs MKF's SPICE simulation, the realism gate, and the
Ray + Nicola adversarial review — i.e. it reaches the same end state as the
legacy ``full_design`` pipeline, but with the new fsw-from-magnetic core and the
magnetic PINNED.

Flow (each step reuses an existing, tested stage):

  B2 topology_constraints.propose   maxDutyCycle / maxVds          (LLM, bounded)
  B0 converter_spec_build.build     BASE-schema spec
  B4 frequency_sweep.sweep          fsw* + feasible magnetic front (MKF per fsw)
  B5 magnetic_picker.pick_*         choose ONE magnetic            (LLM suitability)
  B7 op_reconcile.reconcile         cross-OP saturation/thermal
  -- realize (magnetic PINNED) --
     stage3_realize(pinned_main)    real BOM (TAS FET/diode/cap/ctrl) → decompose
                                    → inject parasitics → MKF SPICE sim → realism
     stage3b_gatekeeper             analytical tight-margin gate
     _stage4_adversarial_review     Ray + Nicola (LLM)

The magnetic is fixed throughout; everything else (the switch, rectifier,
capacitors, controller, snubber, and their values) is selected/realized here and
may be iterated.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True, slots=True)
class ConverterDesign:
    """The finished converter: the realized design (TAS + BOM + sim + realism
    verdict + review) plus the sweep/reconcile provenance behind the magnetic."""

    topology: str
    fsw_hz: float
    outcome: Any  # full_design.DesignOutcome (tas, verdict_dict, gatekeeper, …)
    sweep: Any    # frequency_sweep.FrequencySweepResult
    reconcile: Any  # op_reconcile.ReconciliationReport
    review: Any = None  # reviewer_panel.PanelResult | None
    bom: list[dict[str, Any]] = field(default_factory=list)
    notes: tuple[str, ...] = ()

    @property
    def verdict(self) -> str | None:
        vd = getattr(self.outcome, "verdict_dict", None)
        return vd.get("verdict") if isinstance(vd, Mapping) else None


def _extract_bom(tas: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Pull the real selected parts out of the realized TAS — one row per
    component that carries an MPN (FET / diode / cap / controller / resistor /
    the magnetic), with its selection provenance."""
    if not isinstance(tas, Mapping):
        return []
    rows: list[dict[str, Any]] = []
    topo = tas.get("topology")
    stages = topo.get("stages") if isinstance(topo, Mapping) else None
    if not isinstance(stages, list):
        return rows
    seen: set[str] = set()
    for stage in stages:
        circuit = stage.get("circuit") if isinstance(stage, Mapping) else None
        comps = circuit.get("components") if isinstance(circuit, Mapping) else None
        if not isinstance(comps, list):
            continue
        for c in comps:
            if not isinstance(c, Mapping):
                continue
            mpn = c.get("mpn")
            prov = c.get("selection_provenance")
            if not (isinstance(mpn, str) and mpn) and not isinstance(prov, Mapping):
                continue
            ref = str(c.get("name", mpn or "?"))
            if ref in seen:
                continue
            seen.add(ref)
            rows.append({
                "ref": ref,
                "mpn": mpn or (prov.get("mpn") if isinstance(prov, Mapping) else None),
                "manufacturer": (prov.get("manufacturer") if isinstance(prov, Mapping) else None),
                "category": (prov.get("category") if isinstance(prov, Mapping) else None),
            })
    return rows


def design_converter(
    topology: str,
    spec: Mapping[str, Any],
    *,
    use_llm: bool = True,
    with_reviewers: bool = True,
    sweep_kwargs: Mapping[str, Any] | None = None,
    progress: Any = None,
) -> ConverterDesign:
    """Design a full converter for ``topology`` with the fsw-from-magnetic core.

    ``spec`` is a minimal electrical spec (Vin window + rails + currentRippleRatio).
    Returns a :class:`ConverterDesign` with the realized TAS, real-part BOM, MKF
    SPICE sim results, realism verdict, and (if ``with_reviewers``) the Ray +
    Nicola panel. Raises on a genuinely infeasible design — never papers over.
    """
    from heaviside import bridge
    from heaviside.pipeline.full_design import (
        TopologyPick,
        _stage4_adversarial_review,
        stage3_realize,
        stage3b_gatekeeper,
    )
    from heaviside.stages import (
        converter_spec_build,
        frequency_sweep,
        op_reconcile,
        topology_constraints,
    )
    from heaviside.topologies import get as get_topology

    def _say(msg: str, pct: int) -> None:
        if progress is not None:
            try:
                progress(msg, pct)
            except Exception:
                pass

    entry = get_topology(topology)
    notes: list[str] = []

    # B2 — converter-level constraints (LLM, bounded + TAS-class-checked).
    _say("Proposing converter constraints", 5)
    constraints = topology_constraints.propose(spec, topology, use_llm=use_llm, check_tas=True)

    # B0 — BASE-schema spec.
    base = converter_spec_build.build(dict(spec), topology, constraints=constraints)

    # B4 — frequency sweep: fsw* + feasible magnetic front (MKF derives L per fsw).
    _say("Sweeping switching frequency vs magnetic total loss", 20)
    result = frequency_sweep.sweep(topology, base, **dict(sweep_kwargs or {}))

    # B5 — pick ONE magnetic from the loss-annotated front.
    _say("Picking the magnetic", 55)
    from heaviside.agents import magnetic_picker

    if use_llm:
        picked = magnetic_picker.pick_magnetic_from_sweep_llm(result, base)
        idx = picked["index"]
        pick_reason = picked.get("reason", "")
    else:
        idx = magnetic_picker.pick_best_from_sweep(result)
        pick_reason = "deterministic total-loss argmin"
    chosen = result.front[idx]
    md = bridge.MagneticDesign(scoring=float(chosen.scoring), mas=chosen.mas, elapsed_s=0.0)

    # The converter operates at fsw* — stamp it on every operating point so the
    # realize/sim/stress all use the chosen frequency.
    spec_at = dict(base)
    spec_at["operatingPoints"] = [
        {**o, "switchingFrequency": float(result.fsw_star_hz)} if isinstance(o, Mapping) else o
        for o in (base.get("operatingPoints") or [])
    ]

    # B7 — cross-OP reconcile of the chosen (magnetic, fsw*) (don't raise; record).
    try:
        recon = op_reconcile.reconcile(
            topology, spec_at, chosen.mas, min_isat_ratio=1.2, raise_on_infeasible=False
        )
        if not recon.feasible_all_ops:
            notes.append(f"op_reconcile: infeasible at OP(s) — feedback={recon.constraint_feedback}")
    except op_reconcile.InfeasibleAtOP as exc:
        recon = exc.report
        notes.append(f"op_reconcile: {exc}")

    # --- realize the real converter around the PINNED magnetic ---
    _say("Building the converter: real BOM + SPICE sim + realism", 70)
    pick = TopologyPick(
        topology=entry, main_magnetic=md, candidates=(md,),
        pick_reason=pick_reason, pick_criteria="frequency_sweep+suitability",
    )
    outcome = stage3_realize(pick, spec_at, pinned_main=md)
    outcome = replace(outcome, gatekeeper=stage3b_gatekeeper(outcome),
                      fsw_optimal=float(result.fsw_star_hz))

    # --- Ray + Nicola adversarial review ---
    review = None
    if with_reviewers and use_llm:
        _say("Adversarial review (Ray + Nicola)", 90)
        outcome = _stage4_adversarial_review(outcome)

    _say("Done", 100)
    return ConverterDesign(
        topology=topology,
        fsw_hz=float(result.fsw_star_hz),
        outcome=outcome,
        sweep=result,
        reconcile=recon,
        review=review,
        bom=_extract_bom(outcome.tas),
        notes=tuple(notes),
    )
