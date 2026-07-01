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

    @property
    def tas(self) -> Any:
        """The realized TAS (from the inner DesignOutcome). Mirrors ``verdict``
        so consumers — the ``heaviside design`` CLI, reports — can read
        ``design.tas`` without reaching through ``.outcome``."""
        return getattr(self.outcome, "tas", None)


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


# Per-category map of the stamped TAS component fields → the report's
# topology-agnostic {port_voltage, port_current, rated_voltage, rated_current}
# view. The stamping happens in catalogue.assemble._stamp_* /
# full_design._stamp_ktas_gate_stresses; here we only READ what was stamped, so
# the BOM table can show the actual voltage + current each component's port sees
# (operating stress) against what it is rated for. Missing fields stay None — no
# fabrication (CLAUDE.md: surface gaps, never invent a number).
_STRESS_FIELDS: dict[str, dict[str, str]] = {
    "mosfet": {
        "port_voltage": "vds_stress", "rated_voltage": "vds_rated",
        "port_current": "id_stress", "rated_current": "id_rated",
    },
    "diode": {
        "port_voltage": "v_reverse", "rated_voltage": "vrrm_rated",
        "port_current": "if_avg_stress", "rated_current": "if_avg_rated",
    },
    "capacitor": {
        "port_voltage": "v_working", "rated_voltage": "v_rated",
        "port_current": "ripple_current_stress", "rated_current": "ripple_current_rated",
    },
}


def _stress_view(comp: Mapping[str, Any], category: str | None) -> dict[str, float | None]:
    """Read the stamped operating-stress + rating fields off one TAS component
    for ``category`` (mosfet / diode / capacitor). The capacitor row is what the
    user means by "voltage and current through the port of a passive": working
    voltage + RMS ripple current. Returns all-None when the category has no
    stress stencil (e.g. controller / resistor / the magnetic — the magnetic's
    port waveform is shown in the waveform plots instead)."""
    fields = _STRESS_FIELDS.get((category or "").lower())
    out: dict[str, float | None] = {
        "port_voltage": None, "rated_voltage": None,
        "port_current": None, "rated_current": None,
    }
    if not fields:
        return out
    for view_key, comp_key in fields.items():
        v = comp.get(comp_key)
        if isinstance(v, (int, float)):
            out[view_key] = float(v)
    return out


def _extract_bom(tas: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Pull the real selected parts out of the realized TAS — one row per
    component that carries an MPN (FET / diode / cap / controller / resistor /
    the magnetic), with its selection provenance and the per-port operating
    stress (voltage + current the component sees) vs. its rating."""
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
            category = prov.get("category") if isinstance(prov, Mapping) else None
            rows.append({
                "ref": ref,
                "mpn": mpn or (prov.get("mpn") if isinstance(prov, Mapping) else None),
                "manufacturer": (prov.get("manufacturer") if isinstance(prov, Mapping) else None),
                "category": category,
                **_stress_view(c, category),
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

    # B4/B5 — choose the operating frequency and the MAIN magnetic.
    #
    # Hard-switched topologies: fsw is a free loss-optimisation variable, so sweep it and pick
    # the lowest-loss feasible magnetic from the front.
    # Resonant topologies (LLC/SRC/CLLC/CLLLC): fsw is set by the tank/gain law, NOT a loss sweep —
    # design the main transformer at the operating frequency and let Kirchhoff design the tank
    # (Lr/Cr) around it at realize. No loss sweep, no _IPEAK_WORST computer needed.
    if entry.family == "resonant":
        _say("Resonant: designing the main transformer at the operating (gain-law) frequency", 30)
        ops0 = (base.get("operatingPoints") or [{}])[0]
        fsw_star_hz = ops0.get("switchingFrequency") if isinstance(ops0, Mapping) else None
        if not isinstance(fsw_star_hz, (int, float)) or fsw_star_hz <= 0:
            lo, hi = base.get("minSwitchingFrequency"), base.get("maximumSwitchingFrequency") or base.get("maxSwitchingFrequency")
            if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo > 0 and hi > 0:
                fsw_star_hz = (float(lo) * float(hi)) ** 0.5  # geometric-mean of the resonant window
        if not isinstance(fsw_star_hz, (int, float)) or fsw_star_hz <= 0:
            raise ValueError(
                f"resonant topology {topology!r} needs an operating switchingFrequency (or a "
                f"min/max switching-frequency window) in the spec to design the tank."
            )
        cands = bridge.design_magnetics_at_fsw(topology, base, float(fsw_star_hz), max_results=5)
        md = cands[0]
        pick_reason = "resonant: main transformer designed at the operating (gain-law) fsw; no loss sweep"
        result = None
        _say(f"Resonant main magnetic designed at {fsw_star_hz / 1e3:.0f} kHz", 52)
    elif entry.family == "ac_dc":
        # Self-regulating AC-input topologies (PFC, Vienna): fsw is FIXED (set by the
        # controller / the spec), NOT a free loss-optimisation variable, and there is
        # no output-voltage CONTROL variable — the closed-loop controller lives inside
        # the Kirchhoff deck (simulate_regulated runs it over whole line cycles and
        # measures the regulated point). So there is no frequency sweep, no
        # switching-loss surrogate, and no pre-designed/pinned MKF magnetic: MKF has no
        # converter model for PFC, so the boost inductor is designed from Kirchhoff's
        # OWN magnetic seed at realize (ABT #34). The AC line context (inputType +
        # lineFrequency) rides on the spec straight through to design_from_hs_spec.
        _say("AC self-regulating input: fixed fsw, boost inductor from the Kirchhoff seed (no loss sweep)", 30)
        ops0 = (base.get("operatingPoints") or [{}])[0]
        fsw_star_hz = ops0.get("switchingFrequency") if isinstance(ops0, Mapping) else None
        if not isinstance(fsw_star_hz, (int, float)) or fsw_star_hz <= 0:
            raise ValueError(
                f"self-regulating AC topology {topology!r} needs an operating "
                f"switchingFrequency in the spec (fsw is fixed by the controller, "
                f"not swept) to design the boost inductor."
            )
        md = None  # the boost inductor is designed from the KH seed at realize, not pinned here
        result = None
        pick_reason = (
            "self-regulating AC input: fixed fsw, boost inductor designed from the "
            "Kirchhoff seed (no loss sweep)"
        )
        _say(f"AC input at fsw = {float(fsw_star_hz) / 1e3:.0f} kHz", 52)
    else:
        _say("Sweeping switching frequency vs magnetic total loss", 20)
        _check_cancel = getattr(progress, "check_cancelled", None)
        result = frequency_sweep.sweep(
            topology, base, check_cancel=_check_cancel, **dict(sweep_kwargs or {})
        )
        _say(
            f"Sweep done: fsw* = {result.fsw_star_hz / 1e3:.0f} kHz, "
            f"{len(result.front)} feasible magnetics",
            52,
        )
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
        fsw_star_hz = float(result.fsw_star_hz)

    # The converter operates at fsw* — stamp it on every operating point so the
    # realize/sim/stress all use the chosen frequency.
    spec_at = dict(base)
    spec_at["operatingPoints"] = [
        {**o, "switchingFrequency": float(fsw_star_hz)} if isinstance(o, Mapping) else o
        for o in (base.get("operatingPoints") or [])
    ]

    # B7 — cross-OP reconcile of the chosen (magnetic, fsw*). A single-topology
    # designer returns ONE design; if that design saturates or overheats at any
    # operating point it is not a valid result, so reconcile raises
    # (InfeasibleAtOP) rather than returning an infeasible design with a note.
    # The analytical stress engine (used by op_reconcile) needs the transformer turns ratio for
    # isolated/resonant topologies. The sweep path seeds it via converter_spec_build; the resonant
    # path doesn't, so harvest the REALIZED ratio from the chosen main transformer's coil.
    if md is not None and "desiredTurnsRatios" not in spec_at:
        from heaviside.pipeline.full_design import _pinned_magnetic_constraints
        _lm, _trs = _pinned_magnetic_constraints(md, bridge)
        if _trs:
            spec_at["desiredTurnsRatios"] = _trs

    if md is not None:
        _say("Reconciling the magnetic across all operating points", 64)
        recon = op_reconcile.reconcile(topology, spec_at, md.mas, min_isat_ratio=1.2)
    else:
        # Self-regulating AC: there is no pre-designed magnetic to reconcile here. The
        # boost inductor is designed AND saturation-checked inside the Kirchhoff realize
        # (_design_ktas_magnetics sizes it for the isat margin and the realism gate's
        # inductor_isat_margin check verifies it). The same saturation physics still
        # runs — just one stage later — so no feasibility gate is silently skipped.
        recon = None

    # --- realize the real converter around the PINNED magnetic ---
    pick = TopologyPick(
        topology=entry, main_magnetic=md, candidates=(md,),
        pick_reason=pick_reason, pick_criteria="frequency_sweep+suitability",
    )
    # della-Pollock cutover (abt #48): EVERY topology realizes ENTIRELY through Kirchhoff — KH
    # designs the rest of the converter around the pinned main magnetic, HS fills the BOM, and it
    # simulates with REAL component models (DATASHEET) in a single closed-loop pass. MKF's converter
    # models (process_converter) are gone; MKF designs only magnetic geometry.
    _say("Realizing via Kirchhoff (della-Pollock: parts + real-model sim around the pinned magnetic)", 70)
    outcome = stage3_realize(pick, spec_at, pinned_main=md)
    notes.append("realized via Kirchhoff della-Pollock path (BOM + DATASHEET sim)")
    _say("Realism gate + gatekeeper on the simulated waveforms", 86)
    outcome = replace(outcome, gatekeeper=stage3b_gatekeeper(outcome),
                      fsw_optimal=float(fsw_star_hz))

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
        fsw_hz=float(fsw_star_hz),
        outcome=outcome,
        sweep=result,  # None for resonant (no loss sweep); FrequencySweepResult otherwise
        reconcile=recon,
        review=review,
        bom=_extract_bom(outcome.tas),
        # Self-regulating AC (md is None): the magnetic was designed inside realize, so
        # there is no pre-design MAS to read PyOM excitation traces from here. Leave the
        # waveform list empty (no fabrication) rather than inventing traces.
        waveforms=magnetic_waveforms(md.mas) if md is not None else [],
        notes=tuple(notes),
    )
