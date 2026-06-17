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

import contextlib
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
    # PyOM-ngspice excitation waveforms (winding current + voltage per OP) read
    # from the chosen magnetic's MAS — produced by PyOM's bundled libngspice
    # during design, so they ARE the design's simulation traces (no extra run).
    waveforms: list[dict[str, Any]] = field(default_factory=list)
    notes: tuple[str, ...] = ()

    @property
    def verdict(self) -> str | None:
        vd = getattr(self.outcome, "verdict_dict", None)
        return vd.get("verdict") if isinstance(vd, Mapping) else None


def spice_config_from_bom(tas: Mapping[str, Any] | None) -> dict[str, float]:
    """Build PyOM's ngspice ``spice_config`` knobs from the REAL selected parts,
    so the converter is simulated with the actual FET / diode values — every
    non-magnetic component configured from the BOM. The magnetic itself is never
    a knob (it is the pinned, chosen design).

    Knobs (MKF SpiceSimulationConfig): ``switchRON`` ← FET on-resistance,
    ``diodeRS`` ← diode dynamic series resistance (from Vf/If when present).
    snubR/snubC and diodeIS are left to the deck defaults unless the BOM carries
    a real value — we never fabricate a diode model fit. Returns only the knobs
    we can ground in a real datasheet number."""
    cfg: dict[str, float] = {}
    if not isinstance(tas, Mapping):
        return cfg
    topo = tas.get("topology")
    stages = topo.get("stages") if isinstance(topo, Mapping) else None
    if not isinstance(stages, list):
        return cfg
    for stage in stages:
        circuit = stage.get("circuit") if isinstance(stage, Mapping) else None
        comps = circuit.get("components") if isinstance(circuit, Mapping) else None
        if not isinstance(comps, list):
            continue
        for c in comps:
            if not isinstance(c, Mapping):
                continue
            # Main switch: real on-resistance drives switchRON (conduction loss).
            rds = c.get("rds_on")
            if "switchRON" not in cfg and isinstance(rds, (int, float)) and rds > 0:
                cfg["switchRON"] = float(rds)
            # Rectifier: a real dynamic resistance if the part carries one.
            drs = c.get("rs_dynamic") or c.get("diode_rs")
            if "diodeRS" not in cfg and isinstance(drs, (int, float)) and drs > 0:
                cfg["diodeRS"] = float(drs)
    return cfg


def magnetic_waveforms(mas: Mapping[str, Any], *, max_points: int = 400) -> list[dict[str, Any]]:
    """Extract PyOM's ngspice excitation waveforms (primary-winding current +
    voltage per operating point) from the chosen magnetic's MAS, downsampled for
    plotting. These are computed by PyOM's bundled libngspice during design — the
    design's own simulation traces — so no extra simulator run is needed.

    Returns one entry per operating point: ``{op_index, label, time_s,
    current_a, voltage_v}``. Skips operating points without a usable waveform
    (no fabrication)."""
    out: list[dict[str, Any]] = []
    ops = (mas.get("inputs") or {}).get("operatingPoints") if isinstance(mas, Mapping) else None
    if not isinstance(ops, list):
        return out
    for i, op in enumerate(ops):
        if not isinstance(op, Mapping):
            continue
        excs = op.get("excitationsPerWinding")
        if not isinstance(excs, list) or not excs:
            continue
        exc = excs[0]  # primary winding (the inductor / main current)
        cur_wf = (exc.get("current") or {}).get("waveform") if isinstance(exc, Mapping) else None
        volt_wf = (exc.get("voltage") or {}).get("waveform") if isinstance(exc, Mapping) else None
        time = cur_wf.get("time") if isinstance(cur_wf, Mapping) else None
        cur = cur_wf.get("data") if isinstance(cur_wf, Mapping) else None
        volt = volt_wf.get("data") if isinstance(volt_wf, Mapping) else None
        if not (isinstance(time, list) and isinstance(cur, list) and time and cur):
            continue
        # ceil division so the result is guaranteed <= max_points
        step = max(1, -(-len(time) // max_points))
        sl = slice(None, None, step)
        out.append({
            "op_index": i,
            "label": (op.get("name") if isinstance(op.get("name"), str) else None) or f"op{i}",
            "time_s": time[sl],
            "current_a": cur[sl],
            "voltage_v": volt[sl] if isinstance(volt, list) and len(volt) == len(time) else None,
        })
    return out


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
            with contextlib.suppress(Exception):
                progress(msg, pct)

    entry = get_topology(topology)
    notes: list[str] = []

    # B2 — converter-level constraints (LLM, bounded + TAS-class-checked). When
    # reviewers are enabled, Ray + Nicola judge the proposal and it re-proposes
    # with their objections if rejected (on top of the deterministic guard).
    _say("Proposing converter constraints (duty ceiling + FET Vds class)", 5)
    constraints = topology_constraints.propose(
        spec, topology, use_llm=use_llm, check_tas=True,
        with_review=use_llm and with_reviewers, progress=_say,
    )

    # B0 — BASE-schema spec.
    _say("Building base converter spec (MKF design constraints)", 12)
    base = converter_spec_build.build(dict(spec), topology, constraints=constraints)

    # B4 — frequency sweep: fsw* + feasible magnetic front (MKF derives L per fsw).
    _say("Sweeping switching frequency vs magnetic total loss", 20)
    result = frequency_sweep.sweep(topology, base, **dict(sweep_kwargs or {}))
    _say(
        f"Sweep done: fsw* = {result.fsw_star_hz / 1e3:.0f} kHz, "
        f"{len(result.front)} feasible magnetics",
        52,
    )

    # B5 — pick ONE magnetic from the loss-annotated front.
    _say("Picking the magnetic from the loss-annotated front", 55)
    from heaviside.agents import magnetic_picker

    if use_llm:
        picked = magnetic_picker.pick_magnetic_from_sweep_llm(
            result, base, with_review=with_reviewers, progress=_say,
        )
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
    _say("Reconciling the magnetic across all operating points", 64)
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
    _say("Realizing converter: selecting real TAS parts + MKF SPICE netlist", 70)
    pick = TopologyPick(
        topology=entry, main_magnetic=md, candidates=(md,),
        pick_reason=pick_reason, pick_criteria="frequency_sweep+suitability",
    )
    outcome = stage3_realize(pick, spec_at, pinned_main=md)
    # Re-simulate with PyOM's ngspice knobs driven by the REAL selected parts
    # (switchRON ← FET, diodeRS ← rectifier) — the BOM is only known after the
    # first realize (decompose precedes selection), so configure-everything-but-
    # the-magnetic happens on this second pass. Magnetic stays pinned. Guarded:
    # if the configured pass fails, keep the first (no silent regression).
    knobs = spice_config_from_bom(outcome.tas)
    if knobs:
        _say("Re-simulating with SPICE knobs from the real parts (FET RON, diode RS)", 80)
        try:
            tuned = stage3_realize(pick, spec_at, pinned_main=md, spice_config=knobs)
            if tuned.tas is not None:
                outcome = tuned
                notes.append(f"sim configured from BOM: {knobs}")
        except Exception as exc:  # keep the first realize
            notes.append(f"BOM-configured re-sim skipped: {str(exc)[:120]}")
    _say("Realism gate + gatekeeper on the simulated waveforms", 86)
    outcome = replace(outcome, gatekeeper=stage3b_gatekeeper(outcome),
                      fsw_optimal=float(result.fsw_star_hz))

    # --- Ray + Nicola adversarial review ---
    review = None
    if with_reviewers and use_llm:
        _say("Adversarial review starting (Ray + Nicola)", 90)
        # Per-reviewer progress: Ray (engineering) then Nicola (quality), so the
        # UI shows each named reviewer as its own stage.
        _REVIEW_PCT = {"ray": 92, "nicola": 96}
        _REVIEW_LABEL = {"ray": "Ray (engineering)", "nicola": "Nicola (quality)"}

        def _review_progress(name: str, idx: int, total: int) -> None:
            label = _REVIEW_LABEL.get(name, name)
            _say(f"Reviewing — {label}", _REVIEW_PCT.get(name, 90 + idx))

        outcome = _stage4_adversarial_review(outcome, progress=_review_progress)

    _say("Done", 100)
    return ConverterDesign(
        topology=topology,
        fsw_hz=float(result.fsw_star_hz),
        outcome=outcome,
        sweep=result,
        reconcile=recon,
        review=review,
        bom=_extract_bom(outcome.tas),
        waveforms=magnetic_waveforms(chosen.mas),
        notes=tuple(notes),
    )
