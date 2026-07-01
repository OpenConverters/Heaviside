"""Format-agnostic **report model** — the single source of truth for what a
design report contains, shared by the LaTeX/PDF renderer
(:mod:`heaviside.report.latex`) and the HTML renderer
(:mod:`heaviside.report.html`).

This module performs all of the per-section *data extraction* — specs table,
design-calculation rows, magnetics table, BOM rows, power-loss budget,
component-stress / margins, the waveform series, the topology label and the
theory-of-operation text — and returns it as plain Python values
(dicts / tuples / floats). It contains **no** LaTeX or HTML markup; each renderer
formats the same model into its own output so the two reports carry identical
sections and identical numbers.

House rules (CLAUDE.md): nothing is fabricated. A genuinely-absent value comes
back as ``None`` (the renderer shows ``n/a`` or omits the row); we never invent a
"typical" number. ``dimensionWithTolerance`` values are collapsed with
:func:`_resolve` (mirroring ``PEAS::resolve_dimensional_values``), never by
hand-reading nominal/min/max. All magnetics numbers come from the MAS/MKF.
"""
from __future__ import annotations

import copy
import html as _html
import os
from collections.abc import Mapping, Sequence
from typing import Any

# Phase-2 efficiency-vs-load: load fractions (of rated output) re-simulated
# CLOSED-LOOP on the SAME realized design. Bounded to <=5 points (CLAUDE.md /
# task: keep the multi-load re-sim cheap). Set HEAVISIDE_REPORT_NO_RESIM=1 to
# skip every re-sim (e.g. a fast HTML preview that must not pay the ngspice cost).
_LOAD_SWEEP_FRACS: tuple[float, ...] = (0.2, 0.4, 0.6, 0.8, 1.0)

# ─────────────────────────────────────────────────────────────────────────────
# Dimensional resolver + plain number formatting (format-agnostic)
# ─────────────────────────────────────────────────────────────────────────────


def _resolve(dim: Any, prefer: str = "nominal") -> float | None:
    """Collapse a ``dimensionWithTolerance`` ({nominal,minimum,maximum}) — or a
    bare scalar — to one number, mirroring ``PEAS::resolve_dimensional_values``.

    Semantics (default NOMINAL): nominal -> (min+max)/2 -> max -> min.  MAXIMUM /
    MINIMUM pick that end first.  Returns ``None`` when nothing is present
    (the caller decides whether that is an n/a row or a hard error) — we never
    invent a value.
    """
    if isinstance(dim, (int, float)):
        return float(dim)
    if not isinstance(dim, Mapping):
        return None
    nom = dim.get("nominal")
    lo = dim.get("minimum")
    hi = dim.get("maximum")
    nom = float(nom) if isinstance(nom, (int, float)) else None
    lo = float(lo) if isinstance(lo, (int, float)) else None
    hi = float(hi) if isinstance(hi, (int, float)) else None
    if prefer == "maximum":
        order = [hi, nom, lo]
    elif prefer == "minimum":
        order = [lo, nom, hi]
    else:
        mid = (lo + hi) / 2.0 if (lo is not None and hi is not None) else None
        order = [nom, mid, hi, lo]
    for v in order:
        if v is not None:
            return v
    return None


def _g(value: float | None, sig: int = 4) -> str:
    """Format a number to ``sig`` significant figures (plain digits, valid in
    both LaTeX and HTML), trimming trailing zeros."""
    if value is None:
        return "n/a"
    if value == 0:
        return "0"
    return f"{value:.{sig}g}"


# ─────────────────────────────────────────────────────────────────────────────
# Symbol formatting — one notation, two outputs
# ─────────────────────────────────────────────────────────────────────────────
#
# Symbols are passed around as small tokens ("V_in", "f_sw", "I_out[0]", "eta",
# "--"). Each renderer turns a token into its own markup so both reports show the
# same symbol. ``base_sub`` -> subscript; greek names map to a glyph.

_GREEK: dict[str, tuple[str, str]] = {
    # token: (LaTeX, HTML/unicode)
    "eta": (r"\eta", "η"),
}


def sym_tex(token: str) -> str:
    """Render a symbol token as inline LaTeX math (e.g. ``$V_{in}$``)."""
    if token == "--":
        return "--"
    if token in _GREEK:
        return f"${_GREEK[token][0]}$"
    if "_" in token:
        base, sub = token.split("_", 1)
        return f"${base}_{{{sub}}}$"
    return f"${token}$"


def sym_html(token: str) -> str:
    """Render a symbol token as HTML (e.g. ``V<sub>in</sub>``)."""
    if token == "--":
        return "--"
    if token in _GREEK:
        return _GREEK[token][1]
    if "_" in token:
        base, sub = token.split("_", 1)
        return f"{_html.escape(base)}<sub>{_html.escape(sub)}</sub>"
    return _html.escape(token)


# ─────────────────────────────────────────────────────────────────────────────
# Topology metadata
# ─────────────────────────────────────────────────────────────────────────────

_TOPO_LABEL: dict[str, str] = {
    "buck": "Buck DC-DC Converter",
    "boost": "Boost DC-DC Converter",
    "buck_boost": "Buck-Boost DC-DC Converter",
    "flyback": "Flyback Isolated DC-DC Converter",
    "forward": "Forward Isolated DC-DC Converter",
    "push_pull": "Push-Pull Isolated DC-DC Converter",
    "half_bridge": "Half-Bridge Isolated DC-DC Converter",
    "full_bridge": "Full-Bridge Isolated DC-DC Converter",
    "psfb": "Phase-Shifted Full-Bridge Converter",
    "llc": "LLC Resonant Half-Bridge Converter",
    "cllc": "CLLC Bidirectional Resonant Converter",
    "pfc": "Single-Phase Boost PFC Pre-Regulator",
    "vienna": "Three-Phase Vienna Rectifier",
}

# Topology-templated theory-of-operation paragraphs (generic but correct per
# family). One short paragraph; the design numbers live in later sections. The
# text carries inline LaTeX math ($...$); the HTML renderer converts it for the
# browser so both reports describe the topology identically.
_TOPO_THEORY: dict[str, str] = {
    "buck": (
        "The converter is a step-down (buck) topology. During the on-time the high-side "
        "switch connects the input to the output inductor, ramping its current up; during "
        "the off-time the current freewheels through the rectifier. The output voltage is "
        "set by the duty cycle, $V_{out} = D\\,V_{in}$, and is regulated by a feedback loop "
        "that modulates $D$. The design targets continuous-conduction mode (CCM) at full "
        "load, with the inductor sized for the specified peak-to-peak current ripple."),
    "boost": (
        "The converter is a step-up (boost) topology. The switch periodically shorts the "
        "input inductor to ground, storing energy; when it opens, the inductor current is "
        "delivered to the output through the rectifier at a higher voltage, "
        "$V_{out} = V_{in}/(1-D)$. The loop regulates the output by modulating $D$; the "
        "inductor is sized for CCM operation at the rated load."),
    "flyback": (
        "The converter is an isolated flyback. During the on-time energy is stored in the "
        "coupled-inductor (transformer) magnetizing inductance; during the off-time it is "
        "transferred to the secondary and the output. The conversion ratio is "
        "$V_{out} = \\frac{N_s}{N_p}\\frac{D}{1-D}V_{in}$. Galvanic isolation is provided "
        "by the transformer; the magnetizing inductance is sized for the chosen "
        "conduction mode and peak current."),
    "forward": (
        "The converter is an isolated single-switch forward. Energy is transferred to the "
        "secondary during the switch on-time through the transformer (which carries no DC "
        "energy storage); the output inductor filters the rectified secondary voltage. "
        "$V_{out} = \\frac{N_s}{N_p} D\\,V_{in}$. A reset winding or clamp recovers the "
        "magnetizing energy each cycle."),
    "push_pull": (
        "The converter is an isolated push-pull. Two switches alternately drive the two "
        "halves of a center-tapped primary, so the transformer is excited symmetrically in "
        "both flux polarities and the core is fully utilised. The rectified, filtered "
        "secondary gives $V_{out} = \\frac{N_s}{N_p} D\\,V_{in}$ (with $D$ per switch). "
        "Symmetric drive keeps the average flux near zero, avoiding staircase saturation."),
    "half_bridge": (
        "The converter is an isolated half-bridge. Two switches drive the transformer "
        "primary from a capacitor-divided rail, applying $\\pm V_{in}/2$ across the "
        "primary. The rectified secondary is filtered to the output, "
        "$V_{out} = \\frac{N_s}{N_p} D\\,V_{in}$."),
    "full_bridge": (
        "The converter is an isolated full-bridge. Four switches in two legs apply the full "
        "$\\pm V_{in}$ across the transformer primary, giving the highest power capability "
        "of the bridge family. The rectified secondary is filtered to the output."),
    "llc": (
        "The converter is an LLC resonant half-bridge. The half-bridge drives a series "
        "resonant tank (resonant inductor $L_r$, resonant capacitor $C_r$) in series with "
        "the transformer magnetizing inductance $L_m$. Output regulation is achieved by "
        "varying the switching frequency around the series-resonant frequency "
        "$f_r = 1/(2\\pi\\sqrt{L_r C_r})$, which lets the primary switches turn on at zero "
        "voltage (ZVS) and the secondary rectifiers turn off at zero current (ZCS) over a "
        "wide load range, for high efficiency."),
    "pfc": (
        "The stage is a single-phase boost power-factor-correction (PFC) pre-regulator. The "
        "boost inductor current is shaped by the controller to follow the rectified line "
        "voltage, drawing near-unity-power-factor sinusoidal input current while regulating "
        "the bulk output to a fixed DC voltage above the line peak."),
}


def _theory_for(topology: str, isolated: bool) -> str:
    if topology in _TOPO_THEORY:
        return _TOPO_THEORY[topology]
    iso = ("It provides galvanic isolation through a transformer."
           if isolated else "It is a non-isolated topology.")
    return (f"The converter is a {topology.replace('_', ' ')} topology. {iso} "
            "Output regulation is provided by the control loop modulating the switching "
            "duty cycle (or frequency for resonant families).")


# Friendly descriptions per BOM category.
_CAT_DESC: dict[str, str] = {
    "mosfet": "Power MOSFET",
    "diode": "Rectifier / freewheel diode",
    "capacitor": "Capacitor",
    "inductor": "Power inductor",
    "transformer": "Power transformer",
    "controller": "PWM / control IC",
    "resistor": "Resistor",
}
_POWER_CATS: set[str] = {
    "mosfet", "diode", "capacitor", "inductor", "transformer", "magnetic"}


# Loss-mechanism suffixes used by the analyst's loss-budget keys
# ("Q1_conduction", "C_out_esr", "L1_core", ...). The refdes is whatever
# precedes a recognised mechanism suffix.
_LOSS_MECH: dict[str, str] = {
    "conduction": "Conduction",
    "switching": "Switching",
    "core": "Core",
    "dcr": "Winding (DCR)",
    "copper": "Winding",
    "winding": "Winding",
    "esr": "ESR",
    "gate": "Gate drive",
    "reverse": "Reverse recovery",
}


def _split_loss_key(key: str) -> tuple[str, str]:
    """Split a loss-budget key into ``(refdes, mechanism_label)``."""
    idx = key.rfind("_")
    if idx > 0:
        suffix = key[idx + 1:].lower()
        if suffix in _LOSS_MECH:
            return key[:idx], _LOSS_MECH[suffix]
    return key, "Total"


# ─────────────────────────────────────────────────────────────────────────────
# The report model
# ─────────────────────────────────────────────────────────────────────────────


class ReportModel:
    """A flat, render-ready, format-agnostic view of the design — every report
    section reads from here."""

    def __init__(self, src: Any) -> None:
        from heaviside.pipeline.converter_designer import (
            ConverterDesign,
            magnetic_waveforms,
        )

        self.is_design = isinstance(src, ConverterDesign)
        outcome = src.outcome if self.is_design else src
        self.outcome = outcome
        tas = getattr(outcome, "tas", None)
        self.tas: Mapping[str, Any] = tas if isinstance(tas, Mapping) else {}

        # Topology + label
        if self.is_design:
            self.topology = src.topology
        else:
            topo = getattr(getattr(outcome, "pick", None), "topology", None)
            self.topology = getattr(topo, "name", None) or "converter"
        self.topo_label = _TOPO_LABEL.get(
            self.topology, self.topology.replace("_", " ").title() + " Converter")

        self.family = self._topology_family()
        fam = self.family or ""
        # NB: "non_isolated" *contains* "isolated" — match the prefix, not a substring.
        self.isolated = fam.startswith("isolated") or fam == "resonant"

        # Verdict
        vd = getattr(outcome, "verdict_dict", None)
        self.verdict_dict: Mapping[str, Any] = vd if isinstance(vd, Mapping) else {}
        self.verdict = self.verdict_dict.get("verdict")
        self.passed = self.verdict == "pass"

        # Spec — lives at tas.inputs.{designRequirements,operatingPoints}
        self.req: Mapping[str, Any] = {}
        self.ops: list[Mapping[str, Any]] = []
        inputs = self.tas.get("inputs")
        if isinstance(inputs, Mapping):
            dr = inputs.get("designRequirements")
            if isinstance(dr, Mapping):
                self.req = dr
            ops = inputs.get("operatingPoints")
            if isinstance(ops, list):
                self.ops = [o for o in ops if isinstance(o, Mapping)]

        # fsw*
        self.fsw_hz = float(src.fsw_hz) if self.is_design else (
            getattr(outcome, "fsw_optimal", None) or self._fsw_from_req())

        # Simulation results (first op block)
        self.sim_op: Mapping[str, Any] = self._first_sim_op()

        # BOM
        if self.is_design and getattr(src, "bom", None):
            self.bom = list(src.bom)
        else:
            self.bom = self._extract_bom()

        # Loss budget (flat worst-case)
        lb = self.tas.get("loss_budget")
        self.loss_budget: Mapping[str, Any] = lb if isinstance(lb, Mapping) else {}

        # Magnetics
        self.sweep_front = None
        sweep = getattr(src, "sweep", None) if self.is_design else None
        front = getattr(sweep, "front", None) if sweep is not None else None
        if isinstance(front, Sequence) and front:
            self.sweep_front = front[0]
        self.magnetics = self._extract_magnetics()

        # Waveforms
        wfs = getattr(src, "waveforms", None) if self.is_design else None
        if not wfs and self.magnetics:
            try:
                wfs = magnetic_waveforms(self.magnetics[0]["mas"], max_points=200)
            except Exception:
                wfs = []
        self.waveforms = wfs or []

        # Stage names for the block diagram
        self.stages = [
            s.get("name") for s in self._stages() if isinstance(s.get("name"), str)
        ]

        # Phase-2 closed-loop re-sim caches (populated lazily; each is computed
        # at most once per model instance — the re-sim is the expensive part).
        self._eff_load: dict[str, Any] | None = None
        self._line_reg: dict[str, Any] | None = None
        self._full_load_op: Mapping[str, Any] | None = None

    # -- helpers ----------------------------------------------------------------

    def _topology_family(self) -> str | None:
        try:
            from heaviside.topologies import get as get_topology
            return get_topology(self.topology).family
        except Exception:
            return None

    def _stages(self) -> list[Mapping[str, Any]]:
        topo = self.tas.get("topology")
        stages = topo.get("stages") if isinstance(topo, Mapping) else None
        return [s for s in stages if isinstance(s, Mapping)] if isinstance(stages, list) else []

    def _fsw_from_req(self) -> float | None:
        return _resolve(self.req.get("switchingFrequency")) if self.req else None

    def _first_sim_op(self) -> Mapping[str, Any]:
        sim = self.tas.get("simulation_results")
        if isinstance(sim, Mapping):
            for v in sim.values():
                if isinstance(v, Mapping) and ("efficiency" in v or "pout" in v):
                    return v
        return {}

    def _extract_bom(self) -> list[dict[str, Any]]:
        from heaviside.pipeline.converter_designer import _extract_bom
        return _extract_bom(self.tas)

    def _extract_magnetics(self) -> list[dict[str, Any]]:
        """One entry per magnetic, read from the MAS. The main magnetic comes
        from the pick; isat/ipeak/L/total-loss come from the swept candidate when
        present (the chosen design point)."""
        out: list[dict[str, Any]] = []
        mag = getattr(getattr(self.outcome, "pick", None), "main_magnetic", None)
        mas = getattr(mag, "mas", None)
        if not isinstance(mas, Mapping):
            return out
        m = mas.get("magnetic") or {}
        core = m.get("core") or {}
        coil = m.get("coil") or {}
        fd = core.get("functionalDescription") or {}
        pd = core.get("processedDescription") or {}
        eff = pd.get("effectiveParameters") or {}
        mat = fd.get("material") or {}
        shape = fd.get("shape") or {}
        dr = (mas.get("inputs") or {}).get("designRequirements") or {}

        windings = []
        for w in (coil.get("functionalDescription") or []):
            if not isinstance(w, Mapping):
                continue
            wire = w.get("wire") or {}
            windings.append({
                "name": w.get("name"),
                "side": w.get("isolationSide"),
                "turns": w.get("numberTurns"),
                "parallels": w.get("numberParallels"),
                "wire_d": _resolve((wire.get("conductingDiameter")) or {}),
            })

        turns_ratios = []
        for tr in (dr.get("turnsRatios") or []):
            r = _resolve(tr)
            if r is not None:
                turns_ratios.append(r)

        # Core + winding loss from the MAS outputs (authoritative magnetics math).
        outputs = mas.get("outputs")
        core_loss = winding_loss = bpk = None
        if isinstance(outputs, list) and outputs and isinstance(outputs[0], Mapping):
            o0 = outputs[0]
            cl = o0.get("coreLosses")
            if isinstance(cl, Mapping):
                v = cl.get("coreLosses")
                core_loss = float(v) if isinstance(v, (int, float)) else None
                mfd = cl.get("magneticFluxDensity")
                proc = mfd.get("processed") if isinstance(mfd, Mapping) else None
                if isinstance(proc, Mapping) and isinstance(proc.get("peak"), (int, float)):
                    bpk = float(proc["peak"])
            wl = o0.get("windingLosses")
            if isinstance(wl, Mapping) and isinstance(wl.get("windingLosses"), (int, float)):
                winding_loss = float(wl["windingLosses"])

        front = self.sweep_front
        out.append({
            "mas": mas,
            "refdes": self._magnetic_refdes(),
            "role": "Transformer" if (turns_ratios or self.isolated) else "Inductor",
            "core_name": core.get("name"),
            "shape": shape.get("name"),
            "core_type": fd.get("type"),
            "material": mat.get("name"),
            "gapping": fd.get("gapping") or [],
            "Ae": eff.get("effectiveArea"),
            "le": eff.get("effectiveLength"),
            "Ve": eff.get("effectiveVolume"),
            "windings": windings,
            "turns_ratios": turns_ratios,
            "Lm": dr.get("magnetizingInductance"),
            "Llk": dr.get("leakageInductance"),
            "inductance_h": getattr(front, "inductance_h", None),
            "isat_a": getattr(front, "isat_a", None),
            "ipeak_a": getattr(front, "ipeak_worst_a", None),
            "total_loss_w": getattr(front, "magnetic_loss_w", None),
            "core_loss_w": core_loss,
            "winding_loss_w": winding_loss,
            "bpk_t": bpk,
        })
        return out

    def _magnetic_refdes(self) -> str:
        for stage in self._stages():
            for c in (stage.get("circuit") or {}).get("components") or []:
                if isinstance(c, Mapping) and (c.get("category") or "").lower() in (
                    "inductor", "transformer", "magnetic", "coupled_inductor"):
                    name = c.get("name")
                    if isinstance(name, str):
                        return name
        return "L1"

    # -- derived spec values ----------------------------------------------------

    def vin(self) -> dict[str, float | None]:
        d = self.req.get("inputVoltage") if self.req else None
        d = d if isinstance(d, Mapping) else {}
        return {
            "min": _resolve(d, "minimum"),
            "nom": _resolve(d, "nominal"),
            "max": _resolve(d, "maximum"),
        }

    def outputs(self) -> list[dict[str, Any]]:
        """Per-rail (voltage, current, power) from the spec, cross-checked with sim."""
        rails = []
        outs = self.req.get("outputs") if self.req else None
        op0 = self.ops[0] if self.ops else {}
        op_outs = op0.get("outputs") if isinstance(op0, Mapping) else None
        if isinstance(outs, list):
            for i, o in enumerate(outs):
                if not isinstance(o, Mapping):
                    continue
                v = _resolve(o.get("voltage"))
                p = None
                if isinstance(op_outs, list) and i < len(op_outs) and isinstance(op_outs[i], Mapping):
                    p = op_outs[i].get("power")
                    p = float(p) if isinstance(p, (int, float)) else None
                cur = (p / v) if (p is not None and v) else None
                rails.append({"name": o.get("name"), "v": v, "i": cur, "p": p,
                              "regulation": o.get("regulation")})
        return rails

    def pout(self) -> float | None:
        v = self.sim_op.get("pout")
        if isinstance(v, (int, float)):
            return float(v)
        tot = sum(r["p"] for r in self.outputs() if isinstance(r.get("p"), (int, float)))
        return tot or None

    def eta_target(self) -> float | None:
        v = self.req.get("efficiency") if self.req else None
        return float(v) if isinstance(v, (int, float)) else None

    def eta_sim(self) -> float | None:
        v = self.sim_op.get("efficiency")
        return float(v) if isinstance(v, (int, float)) else None

    def theory_text(self) -> str:
        """Theory-of-operation paragraph (carries inline ``$...$`` LaTeX math)."""
        return _theory_for(self.topology, self.isolated)

    # -- shared section data ----------------------------------------------------

    def key_spec_rows(self) -> list[dict[str, Any]]:
        """Rows for the Key Specifications (Min/Typ/Max) table. ``unit`` is a
        token ("V","A","W","kHz","%","--"); ``sym`` is a symbol token."""
        vin = self.vin()
        rails = self.outputs()
        rows: list[dict[str, Any]] = []

        def row(param, sym, mn, ty, mx, unit, cond=""):
            rows.append({"param": param, "sym": sym, "min": mn, "typ": ty,
                         "max": mx, "unit": unit, "cond": cond})

        row("Input voltage", "V_in", vin["min"], vin["nom"], vin["max"], "V", "DC")
        for i, r in enumerate(rails):
            tag = "" if len(rails) == 1 else f"[{i}]"
            row(f"Output voltage{tag}", f"V_out{tag}", None, r["v"], None, "V",
                str(r.get("regulation") or ""))
            if r["i"] is not None:
                row(f"Output current{tag}", f"I_out{tag}", None, r["i"], None, "A",
                    "full load")
            if r["p"] is not None:
                row(f"Output power{tag}", f"P_out{tag}", None, r["p"], None, "W", "")
        if self.fsw_hz:
            row("Switching frequency", "f_sw", None, self.fsw_hz / 1e3, None, "kHz", "")
        et = self.eta_target()
        es = self.eta_sim()
        if et is not None:
            row("Efficiency (target)", "eta", et * 100, None, None, "%", "design min")
        if es is not None:
            row("Efficiency (full load)", "eta", None, es * 100, None, "%", "simulated")
        row("Isolation", "--", None, None, None, "--", "yes" if self.isolated else "no")
        return rows

    def design_calc_items(self) -> list[dict[str, Any]]:
        """Design-calculation rows: named quantity -> relation -> result.

        Each item carries ``eq_tex`` (inline LaTeX), ``eq_html`` (HTML) and a
        ``result`` tuple — one of ``("num", value, sig)``, ``("si", value,
        unit_token)`` or ``("text", str)`` — so each renderer formats the number
        with its own number/SI formatting. Only relations whose inputs are
        present are emitted (no fabricated values)."""
        vin = self.vin()
        rails = self.outputs()
        r0 = rails[0] if rails else {}
        vout = r0.get("v")
        mag = self.magnetics[0] if self.magnetics else None
        items: list[dict[str, Any]] = []

        def add(name, eq_tex, eq_html, result):
            items.append({"name": name, "eq_tex": eq_tex, "eq_html": eq_html,
                          "result": result})

        # Duty cycle
        duty = self.tas.get("duty")
        if isinstance(duty, (int, float)) and vin["nom"]:
            if self.isolated:
                # Isolated: D is set by the transformer turns ratio as well as the
                # voltages (e.g. D = V_out·n / V_in), so the bare V_out/V_in
                # substitution is WRONG — it prints 5/48 = 0.10 next to a regulated
                # D of 0.36. Show the regulated operating-point duty as the measured
                # value and leave the topology-specific closed form out.
                add("Duty cycle (regulated)",
                    r"D\ \text{(regulated operating point)}",
                    "D (regulated operating point)",
                    ("num", duty, 3))
            else:
                add("Duty cycle",
                    r"D = \frac{V_{out}}{V_{in}}" + (
                        rf" = \frac{{{_g(vout)}}}{{{_g(vin['nom'])}}}" if vout else ""),
                    "D = V<sub>out</sub> / V<sub>in</sub>" + (
                        f" = {_g(vout)} / {_g(vin['nom'])}" if vout else ""),
                    ("num", duty, 3))
        elif vout and vin["nom"] and not self.isolated:
            add("Duty cycle (approx.)",
                r"D \approx \frac{V_{out}}{V_{in}} = "
                rf"\frac{{{_g(vout)}}}{{{_g(vin['nom'])}}}",
                f"D ≈ V<sub>out</sub> / V<sub>in</sub> = {_g(vout)} / {_g(vin['nom'])}",
                ("num", vout / vin["nom"], 3))

        # Turns ratio (transformer) — full primary-referred ratio list; the
        # effective step-down ratio is the largest.
        if mag and mag["turns_ratios"]:
            eff_n = max(mag["turns_ratios"])
            add("Primary-referred turns ratio",
                r"n = \frac{N_p}{N_s}", "n = N<sub>p</sub> / N<sub>s</sub>",
                ("text", f"{_g(eff_n, 3)} : 1"))
        if mag and mag["windings"]:
            turn_list = [w.get("turns") for w in mag["windings"]
                         if isinstance(w.get("turns"), (int, float))]
            if turn_list and mag["role"] == "Transformer":
                add("Winding turns",
                    r"N_{1..k} = " + ",\\,".join(str(int(t)) for t in turn_list),
                    "N<sub>1..k</sub> = " + ", ".join(str(int(t)) for t in turn_list),
                    ("text", f"{len(turn_list)} windings"))
            elif turn_list:
                add("Inductor turns", rf"N = {int(turn_list[0])}",
                    f"N = {int(turn_list[0])}", ("text", f"{int(turn_list[0])} turns"))

        # Magnetizing / main inductance (from MAS designRequirements)
        if mag and mag.get("Lm") is not None:
            lm = _resolve(mag["Lm"])
            label = "Magnetizing inductance" if mag["role"] == "Transformer" else "Output inductance"
            sym = "L_m" if mag["role"] == "Transformer" else "L"
            add(label, sym + r" = \text{(MKF magnetic design)}",
                f"{sym_html(sym)} = (MKF magnetic design)", ("si", lm, "H"))
        elif mag and mag.get("inductance_h") is not None:
            add("Output inductance", r"L = \text{(MKF magnetic design)}",
                "L = (MKF magnetic design)", ("si", mag["inductance_h"], "H"))

        # Peak inductor / winding current
        if mag and mag.get("ipeak_a") is not None:
            add("Peak winding current (worst OP)",
                r"I_{pk} = I_{out} + \tfrac{1}{2}\Delta I_L",
                "I<sub>pk</sub> = I<sub>out</sub> + ½ ΔI<sub>L</sub>",
                ("si", mag["ipeak_a"], "A"))

        # Output capacitor ripple (if we have a cap with stress)
        cap = next((b for b in self.bom if (b.get("category") or "") == "capacitor"), None)
        if cap and isinstance(cap.get("port_current"), (int, float)):
            add("Output-cap RMS ripple current",
                r"I_{C,rms}\ \text{(from triangular inductor ripple)}",
                "I<sub>C,rms</sub> (from triangular inductor ripple)",
                ("si", cap["port_current"], "A"))
        return items

    def loss_rows(self) -> tuple[list[tuple[str, str, float]], dict[str, float], float]:
        """Per-(refdes, mechanism) loss rows from the analyst budget, with
        magnetic core/winding loss filled from the MAS when the analyst left it
        null. Returns ``(rows, comp_total, total)`` — all numeric, no markup."""
        rows: list[tuple[str, str, float]] = []
        comp_total: dict[str, float] = {}
        for key, val in self.loss_budget.items():
            if not isinstance(val, (int, float)):
                continue
            refdes, mech = _split_loss_key(key)
            rows.append((refdes, mech, float(val)))
            comp_total[refdes] = comp_total.get(refdes, 0.0) + float(val)

        for mag in self.magnetics:
            ref = mag["refdes"]
            if comp_total.get(ref):
                continue  # analyst already has it
            if mag.get("core_loss_w") is not None:
                rows.append((ref, "Core (MKF)", mag["core_loss_w"]))
                comp_total[ref] = comp_total.get(ref, 0.0) + mag["core_loss_w"]
            if mag.get("winding_loss_w") is not None:
                rows.append((ref, "Winding (MKF)", mag["winding_loss_w"]))
                comp_total[ref] = comp_total.get(ref, 0.0) + mag["winding_loss_w"]

        total = sum(v for _, _, v in rows)
        return rows, comp_total, total

    def sim_total_losses(self) -> float | None:
        v = self.sim_op.get("total_losses")
        return float(v) if isinstance(v, (int, float)) else None

    def stress_rows(self) -> list[dict[str, Any]]:
        """Applied-vs-rated stress per power component. ``kind`` is "V" or "I";
        the renderer chooses the parameter label and unit. Margin is a fraction."""
        out: list[dict[str, Any]] = []
        for r in self.bom:
            cat = (r.get("category") or "").lower()
            pv, rv = r.get("port_voltage"), r.get("rated_voltage")
            pc, rc = r.get("port_current"), r.get("rated_current")
            ref = r.get("ref") or "?"
            if isinstance(pv, (int, float)) and isinstance(rv, (int, float)) and rv:
                out.append({"ref": ref, "cat": cat, "kind": "V", "applied": float(pv),
                            "rated": float(rv), "margin": (rv - pv) / rv})
            if isinstance(pc, (int, float)) and isinstance(rc, (int, float)) and rc:
                out.append({"ref": ref, "cat": cat, "kind": "I", "applied": float(pc),
                            "rated": float(rc), "margin": (rc - pc) / rc})
        return out

    def validated_checks(self) -> list[Mapping[str, Any]]:
        """The named physics checks that passed with a numeric margin."""
        checks = self.verdict_dict.get("checks") if self.verdict_dict else None
        if not isinstance(checks, list):
            return []
        return [c for c in checks if isinstance(c, Mapping)
                and c.get("status") == "pass" and isinstance(c.get("margin"), (int, float))]

    # ── Phase-2: closed-loop re-simulation (efficiency / regulation) ───────────
    #
    # The realized TAS Heaviside produced is a regulatable Kirchhoff deck
    # (``simulate_regulated`` bisects the control variable to a target Vout). We
    # RE-SIMULATE the SAME design — same BOM, same magnetic — at several loads
    # (and several Vin) to produce efficiency-vs-load and load/line-regulation
    # curves. We never RE-DESIGN. The load is scaled by editing the operating
    # point's output power on a COPY of the TAS (Kirchhoff renders ``Rload =
    # Vout^2/Pout`` from it), so a lighter load is a higher Rload.

    def _can_resim(self) -> bool:
        """True iff this is a real, regulatable Kirchhoff TAS we can re-simulate
        (so fake/minimal outcomes — and the ``HEAVISIDE_REPORT_NO_RESIM`` opt-out
        — quietly skip the expensive sim and fall back to the single design point)."""
        if os.environ.get("HEAVISIDE_REPORT_NO_RESIM"):
            return False
        if not self.topology:
            return False
        ops = self.tas.get("inputs")
        ops = ops.get("operatingPoints") if isinstance(ops, Mapping) else None
        if not (isinstance(ops, list) and ops and isinstance(ops[0], Mapping)):
            return False
        outs = ops[0].get("outputs")
        if not (isinstance(outs, list) and outs):
            return False
        if not any(isinstance(o, Mapping) and isinstance(o.get("power"), (int, float))
                   for o in outs):
            return False
        rails = self.outputs()
        return bool(rails and rails[0].get("v"))

    def _resim_regulated(self, *, power_scale: float = 1.0,
                         vin: float | None = None) -> Mapping[str, Any] | None:
        """One CLOSED-LOOP regulated operating point of the SAME design at a
        scaled load (and/or a different Vin), via Kirchhoff ``simulate_regulated``.
        Returns the op dict on a regulated point, else ``None`` (non-convergence
        / Kirchhoff unavailable / bad data) — the caller degrades, never throws."""
        rails = self.outputs()
        if not rails or not rails[0].get("v"):
            return None
        vout_target = rails[0]["v"]
        try:
            from heaviside.decomposer import kirchhoff_adapter as ka
        except Exception:
            return None
        t = copy.deepcopy(dict(self.tas))
        try:
            op0 = t["inputs"]["operatingPoints"][0]
            for o in op0.get("outputs", []):
                if isinstance(o, Mapping) and isinstance(o.get("power"), (int, float)):
                    o["power"] = float(o["power"]) * power_scale
            if vin is not None:
                op0["inputVoltage"] = float(vin)
        except Exception:
            return None
        try:
            op = ka.simulate_regulated(t, float(vout_target), self.topology, fidelity="DATASHEET")
        except Exception:
            return None
        if not isinstance(op, Mapping) or not op.get("regulated"):
            return None
        for k in ("vout", "pin", "pout", "efficiency"):
            if not isinstance(op.get(k), (int, float)):
                return None
        return op

    @staticmethod
    def _op_point(op: Mapping[str, Any], *, frac: float | None = None,
                  vin: float | None = None) -> dict[str, Any]:
        vo = float(op["vout"]); pin = float(op["pin"]); pout = float(op["pout"])
        return {
            "frac": frac, "vin": vin, "vout": vo, "pin": pin, "pout": pout,
            "eff": float(op["efficiency"]),
            "iout": (pout / vo) if vo else None,
        }

    def efficiency_load_points(self) -> dict[str, Any]:
        """Efficiency-vs-load curve (<=5 points) from CLOSED-LOOP re-sims of the
        realized design at fractions of rated output. Returns ``{"points": [...],
        "note": str|None}`` where each point is ``{frac, iout, vout, pin, pout,
        eff}``. Degrades to the converged subset (with a note) rather than
        failing; returns no points when re-sim is unavailable."""
        if self._eff_load is not None:
            return self._eff_load
        result: dict[str, Any] = {"points": [], "note": None}
        if not self._can_resim():
            self._eff_load = result
            return result
        pts: list[dict[str, Any]] = []
        for frac in _LOAD_SWEEP_FRACS:
            op = self._resim_regulated(power_scale=frac)
            if op is None:
                continue
            if abs(frac - 1.0) < 1e-9:
                self._full_load_op = op  # reuse as the nominal full-load anchor
            pts.append(self._op_point(op, frac=frac))
        n_missing = len(_LOAD_SWEEP_FRACS) - len(pts)
        if not pts:
            result["note"] = ("Multi-load re-simulation did not converge at any tested "
                              "load, so the efficiency-vs-load curve is unavailable.")
        elif n_missing:
            result["note"] = (
                f"{n_missing} of {len(_LOAD_SWEEP_FRACS)} load points did not converge and "
                "were dropped; the curve shows the converged points only.")
        result["points"] = pts
        self._eff_load = result
        return result

    def line_regulation_points(self) -> dict[str, Any]:
        """Vout-vs-Vin at full load (line regulation) from re-sims at the spec's
        distinct min / nominal / max input voltages. Returns ``{"points": [...],
        "note": str|None}`` with each point ``{vin, vout, eff, pout, iout}``.
        Reuses the full-load nominal point from :meth:`efficiency_load_points`."""
        if self._line_reg is not None:
            return self._line_reg
        result: dict[str, Any] = {"points": [], "note": None}
        if not self._can_resim():
            self._line_reg = result
            return result
        vin = self.vin()
        # Distinct numeric Vin values, in ascending order (min/nom/max may coincide).
        seen: set[float] = set()
        vins: list[float] = []
        for key in ("min", "nom", "max"):
            v = vin.get(key)
            if isinstance(v, (int, float)) and round(v, 6) not in seen:
                seen.add(round(v, 6))
                vins.append(float(v))
        vins.sort()
        vnom = vin.get("nom")
        pts: list[dict[str, Any]] = []
        n_target = len(vins)
        for v in vins:
            # Reuse the cached nominal full-load op when Vin == nominal.
            if (vnom is not None and abs(v - vnom) < 1e-9
                    and self._full_load_op is not None):
                pts.append(self._op_point(self._full_load_op, vin=v))
                continue
            op = self._resim_regulated(power_scale=1.0, vin=v)
            if op is None:
                continue
            pts.append(self._op_point(op, vin=v))
        n_missing = n_target - len(pts)
        if not pts:
            result["note"] = ("Line-regulation re-simulation did not converge, so the "
                              "Vout-vs-Vin sweep is unavailable.")
        elif n_missing:
            result["note"] = (f"{n_missing} of {n_target} line points did not converge and "
                              "were dropped.")
        result["points"] = pts
        self._line_reg = result
        return result

    # ── Phase-2: power-loss reconciliation ─────────────────────────────────────

    def loss_reconciliation(self) -> dict[str, Any] | None:
        """Analyst per-component loss budget total vs the closed-loop simulation
        total, with the delta SURFACED (not hidden). Returns ``{analyst_total,
        sim_total, delta_w, delta_pct, note}`` or ``None`` when either side is
        missing. The note explains WHY the two differ (different models — the
        analyst sums closed-form per-mechanism terms incl. the independently
        MKF-computed core+winding loss; the sim measures Pin-Pout at the regulated
        operating point)."""
        rows, _comp_total, analyst_total = self.loss_rows()
        sim_total = self.sim_total_losses()
        if not rows or sim_total is None:
            return None
        delta = analyst_total - sim_total
        pct = (delta / sim_total * 100.0) if sim_total else None
        # Does the analyst budget include the MKF magnetic loss? (it was filled in
        # loss_rows when the analyst left it null) — name it in the explanation.
        has_mag = any(mech.endswith("(MKF)") for _ref, mech, _v in rows)
        note = (
            "The two totals come from DIFFERENT models and are not expected to be "
            "bit-identical. The analyst budget sums closed-form per-mechanism losses "
            "(MOSFET conduction/switching, rectifier conduction/recovery, capacitor ESR"
            + (", and the core+winding loss computed independently by MKF from the "
               "design's flux/current waveforms" if has_mag else "")
            + "). The simulation total is the measured Pin-Pout at the regulated "
            "operating point, where the SPICE inductor subcircuit captures winding DCR "
            "and the saturable magnetizing inductance but models core loss and diode Vf "
            "differently. The gap is dominated by those magnetic-loss and rectifier "
            "models; it is shown here rather than reconciled away.")
        return {"analyst_total": analyst_total, "sim_total": sim_total,
                "delta_w": delta, "delta_pct": pct, "note": note}

    # ── Phase-2: magnetic BOM rows ─────────────────────────────────────────────

    def magnetic_bom_rows(self) -> list[dict[str, Any]]:
        """The main magnetic(s) as BOM rows — a designed (custom) part with no
        MPN, summarised by core + turns. Lets the BOM be complete (the magnetic
        otherwise appears only in the Magnetics section). One row per magnetic
        whose refdes is not already a catalogued BOM line."""
        bom_refs = {(r.get("ref") or "") for r in self.bom}
        rows: list[dict[str, Any]] = []
        for mag in self.magnetics:
            ref = mag.get("refdes") or "L1"
            if ref in bom_refs:
                continue
            core = mag.get("core_name") or mag.get("shape")
            material = mag.get("material")
            turn_list = [w.get("turns") for w in mag.get("windings") or []
                         if isinstance(w.get("turns"), (int, float))]
            summary_parts = []
            if core:
                summary_parts.append(str(core))
            if material:
                summary_parts.append(str(material))
            if turn_list:
                summary_parts.append(
                    "N=" + ":".join(str(int(t)) for t in turn_list))
            rows.append({
                "ref": ref,
                "role": mag.get("role") or "Inductor",
                "category": (mag.get("role") or "inductor").lower(),
                "summary": ", ".join(summary_parts) or "custom magnetic",
            })
        return rows

    # ── Phase-2: thermal (junction temperature) ────────────────────────────────

    def _device_thermal(self) -> dict[str, dict[str, float | None]]:
        """Per-refdes ``{rth_ja, tj_max}`` stamped from the selected part (only
        devices that carry at least one thermal field)."""
        out: dict[str, dict[str, float | None]] = {}
        for stage in self._stages():
            for c in (stage.get("circuit") or {}).get("components") or []:
                if not isinstance(c, Mapping):
                    continue
                ref = c.get("name")
                if not isinstance(ref, str):
                    continue
                rth = c.get("rth_ja")
                tjm = c.get("tj_max")
                if isinstance(rth, (int, float)) or isinstance(tjm, (int, float)):
                    out[ref] = {
                        "rth_ja": float(rth) if isinstance(rth, (int, float)) else None,
                        "tj_max": float(tjm) if isinstance(tjm, (int, float)) else None,
                    }
        return out

    def ambient_c(self) -> float | None:
        """Ambient temperature [°C] from the first operating point, else ``None``."""
        op = self.ops[0] if self.ops else {}
        a = op.get("ambientTemperature") if isinstance(op, Mapping) else None
        return float(a) if isinstance(a, (int, float)) else None

    def thermal_rows(self) -> dict[str, Any]:
        """Per-device junction temperature ``Tj = P_loss·Rθ_JA + T_amb`` with the
        headroom to ``Tj,max``. Rθ_JA and Tj,max come from the selected part — a
        device that lacks Rθ (or an unknown ambient) renders ``n/a``; NEVER a
        fabricated Rθ. Returns ``{rows, ambient_c, note}``; ``rows`` is one entry
        per power device that carries a loss number."""
        _rows, comp_total, _total = self.loss_rows()
        th = self._device_thermal()
        amb = self.ambient_c()
        rows: list[dict[str, Any]] = []
        any_na = False
        for ref in sorted(comp_total, key=lambda r: -comp_total[r]):
            p = comp_total[ref]
            info = th.get(ref) or {}
            rth = info.get("rth_ja")
            tjm = info.get("tj_max")
            tj = margin = None
            if rth is not None and amb is not None:
                tj = p * rth + amb
                if tjm is not None:
                    margin = tjm - tj  # °C of headroom
            else:
                any_na = True
            rows.append({"ref": ref, "p_loss": p, "rth_ja": rth, "t_amb": amb,
                         "tj": tj, "tj_max": tjm, "margin_c": margin})
        note = None
        if amb is None and rows:
            note = ("Ambient temperature is not specified in the operating point, so "
                    "junction temperatures cannot be estimated.")
        elif any_na:
            note = ("Devices without a datasheet thermal resistance (Rth,JA) show n/a -- "
                    "no thermal resistance was fabricated.")
        return {"rows": rows, "ambient_c": amb, "note": note}

    # ── Phase-2: schematic / connection table ──────────────────────────────────

    def _comp_type(self, c: Mapping[str, Any]) -> str | None:
        prov = c.get("selection_provenance")
        if isinstance(prov, Mapping) and isinstance(prov.get("category"), str):
            return prov["category"]
        name = c.get("name")
        for mag in self.magnetics:
            if mag.get("refdes") == name:
                return (mag.get("role") or "magnetic").lower()
        data = c.get("data")
        if isinstance(data, Mapping) and ("control" in data or "controller" in data):
            return "controller"
        return None

    def schematic_rows(self) -> list[dict[str, Any]]:
        """The realized netlist as a table: ``{ref, type, nets}`` where ``nets``
        is a list of ``(pin, net)`` tuples (``pin`` may be ``None``) — each
        renderer formats the pin→net arrow in its own markup (no raw glyph leaks
        into LaTeX). Built from each stage circuit's ``connections``; empty when
        the TAS carries no connection data."""
        comp_type: dict[str, str | None] = {}
        comp_nets: dict[str, list[tuple[str | None, str | None]]] = {}
        for stage in self._stages():
            circ = stage.get("circuit")
            if not isinstance(circ, Mapping):
                continue
            for c in circ.get("components") or []:
                if isinstance(c, Mapping) and isinstance(c.get("name"), str):
                    comp_type.setdefault(c["name"], self._comp_type(c))
            for net in circ.get("connections") or []:
                if not isinstance(net, Mapping):
                    continue
                net_name = net.get("name")
                for ep in net.get("endpoints") or []:
                    if not isinstance(ep, Mapping):
                        continue
                    comp = ep.get("component")
                    if isinstance(comp, str):
                        pin = ep.get("pin")
                        comp_nets.setdefault(comp, []).append(
                            (pin if isinstance(pin, str) else None,
                             net_name if isinstance(net_name, str) else None))
        if not comp_nets:
            return []
        return [{"ref": ref, "type": comp_type.get(ref), "nets": comp_nets.get(ref, [])}
                for ref in comp_type]


# Backwards-compatible alias (the class used to live in latex.py as _ReportModel).
_ReportModel = ReportModel
