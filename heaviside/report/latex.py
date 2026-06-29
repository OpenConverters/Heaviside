"""Render a :class:`ConverterDesign` (or legacy ``DesignOutcome``) as a
professional power-electronics **design report**, emitted as LaTeX and compiled
to PDF with ``pdflatex``.

Unlike :mod:`heaviside.report.html` — which narrates the *pipeline* (frequency
sweep, realism checks, gatekeeper, diagnostics) — this report is written from a
**power-electronics engineer's** point of view, in the shape of a vendor
eval-board application note:

  0. Cover / title block (topology, one-line spec, "validated" badge)
  1. Key Specifications        (Parameter / Symbol / Min / Typ / Max / Unit)
  2. Theory of Operation       (topology-templated)
  3. Design Calculations       (quantity -> equation -> numbers -> result)
  4. Magnetics Design          (core / windings / Lm / Isat / Bpk, from MAS)
  5. Bill of Materials         (power stage + control/bias)
  6. Operating Waveforms       (winding current + voltage, from the MAS sim)
  7. Power-Loss Budget         (per-component W + %, from the analyst)
  8. Design Margins            (applied vs rated, affirmative, from the gate)

Every number is read from the design Heaviside actually produced — the spec, the
MAS (magnetics), the realized TAS (BOM + stamped stress), the analyst loss
budget, and the simulation results. Nothing is fabricated: a genuinely-absent
value renders ``n/a`` or the row is omitted (CLAUDE.md no-fallback rule). All
magnetics numbers come from the MAS/MKF, never a re-derived analytical formula.

Public API:

    render_latex(design_or_outcome) -> str          # the .tex source
    render_pdf(design_or_outcome, out_path) -> Path  # compiled PDF at out_path
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# LaTeX escaping + number/unit formatting
# ─────────────────────────────────────────────────────────────────────────────

_LATEX_SPECIAL = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def _esc(text: Any) -> str:
    """Escape a Python value for safe inclusion in LaTeX body text."""
    if text is None:
        return ""
    out = []
    for ch in str(text):
        out.append(_LATEX_SPECIAL.get(ch, ch))
    return "".join(out)


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


def _num(value: float | None, sig: int = 4) -> str:
    """Format a number to ``sig`` significant figures, trimming trailing zeros."""
    if value is None:
        return "n/a"
    if value == 0:
        return "0"
    s = f"{value:.{sig}g}"
    return s


# Metric prefixes for engineering notation, paired with their siunitx macro.
_PREFIXES = [
    (1e9, r"\giga"),
    (1e6, r"\mega"),
    (1e3, r"\kilo"),
    (1.0, ""),
    (1e-3, r"\milli"),
    (1e-6, r"\micro"),
    (1e-9, r"\nano"),
    (1e-12, r"\pico"),
]


def _si(value: float | None, unit_macro: str, *, sig: int = 4) -> str:
    """Return a ``\\SI{mantissa}{<prefix><unit>}`` string with the metric prefix
    chosen so the mantissa sits in [1, 1000).  ``value`` is in base SI units."""
    if value is None:
        return r"\textit{n/a}"
    if value == 0:
        return rf"\SI{{0}}{{{unit_macro}}}"
    av = abs(value)
    for scale, pfx in _PREFIXES:
        if av >= scale or scale == 1e-12:
            mant = value / scale
            return rf"\SI{{{_num(mant, sig)}}}{{{pfx}{unit_macro}}}"
    return rf"\SI{{{_num(value, sig)}}}{{{unit_macro}}}"


def _pct(ratio: float | None, sig: int = 3) -> str:
    if ratio is None:
        return r"\textit{n/a}"
    return rf"\SI{{{_num(ratio * 100.0, sig)}}}{{\percent}}"


# ─────────────────────────────────────────────────────────────────────────────
# Topology metadata
# ─────────────────────────────────────────────────────────────────────────────

_TOPO_LABEL: dict[str, str] = {
    "buck": "Synchronous Buck DC-DC Converter",
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
# family). One short paragraph; the design numbers live in later sections.
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
    return (f"The converter is a {_esc(topology.replace('_', ' '))} topology. {iso} "
            "Output regulation is provided by the control loop modulating the switching "
            "duty cycle (or frequency for resonant families).")


# ─────────────────────────────────────────────────────────────────────────────
# Model extraction — normalise ConverterDesign | DesignOutcome into one view
# ─────────────────────────────────────────────────────────────────────────────

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


class _ReportModel:
    """A flat, render-ready view of the design — every section reads from here."""

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


# ─────────────────────────────────────────────────────────────────────────────
# Section renderers
# ─────────────────────────────────────────────────────────────────────────────

def _preamble() -> str:
    return r"""\documentclass[11pt,a4paper]{article}
\usepackage[a4paper,margin=20mm]{geometry}
\usepackage{booktabs}
\usepackage{array}
\usepackage{longtable}
\usepackage{siunitx}
\usepackage{xcolor}
\usepackage{colortbl}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{pgfplots}
\usetikzlibrary{positioning}
\pgfplotsset{compat=1.16}
\usepackage[hidelinks]{hyperref}
\sisetup{detect-all, group-digits=integer}
\definecolor{passgreen}{RGB}{6,95,70}
\definecolor{passbg}{RGB}{209,250,229}
\definecolor{accent}{RGB}{30,58,90}
\definecolor{rulegray}{RGB}{120,120,120}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.5em}
\renewcommand{\arraystretch}{1.2}
\usepackage{titlesec}
\titleformat{\section}{\Large\bfseries\color{accent}}{\thesection.}{0.6em}{}
\titleformat{\subsection}{\large\bfseries\color{accent}}{\thesubsection}{0.6em}{}
"""


def _cover(m: _ReportModel) -> list[str]:
    vin = m.vin()
    rails = m.outputs()
    # one-line spec: "48 V -> 12 V / 3 A, 36 W"
    vin_s = _num(vin["nom"]) if vin["nom"] is not None else "?"
    rail_parts = []
    for r in rails:
        if r["v"] is not None and r["i"] is not None:
            rail_parts.append(f"{_num(r['v'])} V / {_num(r['i'])} A")
        elif r["v"] is not None:
            rail_parts.append(f"{_num(r['v'])} V")
    rail_s = ", ".join(rail_parts) or "?"
    pout = m.pout()
    oneline = f"{vin_s} V $\\rightarrow$ {rail_s}"
    if pout is not None:
        oneline += f", {_num(pout, 3)} W"

    lines = [
        r"\begin{titlepage}",
        r"\vspace*{2.5cm}",
        r"\begin{center}",
        r"{\color{rulegray}\rule{\linewidth}{0.4pt}}\\[0.4cm]",
        r"{\Huge\bfseries\color{accent} " + _esc(m.topo_label) + r"}\\[0.5cm]",
        r"{\Large Power Converter Design Report}\\[0.3cm]",
        r"{\color{rulegray}\rule{\linewidth}{0.4pt}}\\[1.0cm]",
        r"{\LARGE " + oneline + r"}\\[1.2cm]",
    ]
    if m.passed:
        lines.append(
            r"\colorbox{passbg}{\color{passgreen}\textbf{\;Design validated "
            r"$\checkmark$\; all applicable physics checks passed\;}}\\[1.0cm]")
    elif m.verdict:
        lines.append(r"\textbf{Verdict: " + _esc(str(m.verdict).upper()) + r"}\\[1.0cm]")
    lines += [
        r"\vfill",
        r"\begin{tabular}{rl}",
        r"\textbf{Topology} & " + _esc(m.topology.replace("_", " ")) + r" \\",
        r"\textbf{Isolation} & " + ("Isolated" if m.isolated else "Non-isolated") + r" \\",
        r"\textbf{Switching frequency} & " + _si(m.fsw_hz, r"\hertz") + r" \\",
        r"\textbf{Date} & \today \\",
        r"\end{tabular}\\[0.5cm]",
        r"{\small Generated by Heaviside --- automated converter design pipeline}",
        r"\end{center}",
        r"\end{titlepage}",
        r"\tableofcontents",
        r"\newpage",
    ]
    # Optional block diagram from the TAS stages.
    lines += _block_diagram(m)
    return lines


_STAGE_LABEL = {
    "control": "Control",
    "switchingCell": "Switching Cell",
    "switching_cell": "Switching Cell",
    "filter": "Output Filter",
    "inputFilter": "Input Filter",
    "input_filter": "Input Filter",
    "rectifier": "Rectifier",
    "transformer": "Transformer",
    "tank": "Resonant Tank",
}


def _humanise_stage(name: str) -> str:
    """Title-case a stage name, splitting both camelCase and snake_case."""
    import re
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).replace("_", " ")
    return " ".join(w.capitalize() for w in spaced.split())


def _block_diagram(m: _ReportModel) -> list[str]:
    # Order power-path stages left-to-right; "control" is drawn underneath.
    power = [s for s in m.stages if s != "control"]
    if not power:
        return []
    lines = [
        r"\subsection*{System Block Diagram}",
        r"\begin{center}",
        r"\begin{tikzpicture}[node distance=4mm and 10mm, "
        r"box/.style={draw=accent, thick, rounded corners, minimum height=10mm, "
        r"minimum width=20mm, align=center, font=\small}, >=latex]",
    ]
    prev = None
    lines.append(r"\node[font=\small] (vin) {$V_{in}$};")
    prev = "vin"
    for i, s in enumerate(power):
        label = _STAGE_LABEL.get(s) or _humanise_stage(s)
        node = f"s{i}"
        lines.append(rf"\node[box, right=of {prev}] ({node}) {{{_esc(label)}}};")
        lines.append(rf"\draw[->] ({prev}) -- ({node});")
        prev = node
    lines.append(rf"\node[font=\small, right=of {prev}] (vout) {{$V_{{out}}$}};")
    lines.append(rf"\draw[->] ({prev}) -- (vout);")
    if "control" in m.stages and power:
        mid = f"s{len(power) // 2}"
        lines.append(rf"\node[box, fill=accent!8, below=8mm of {mid}] (ctrl) {{Control}};")
        lines.append(rf"\draw[->, dashed] (ctrl) -- ({mid});")
    lines += [r"\end{tikzpicture}", r"\end{center}"]
    return lines


def _key_specs(m: _ReportModel) -> list[str]:
    vin = m.vin()
    rails = m.outputs()
    lines = [
        r"\section{Key Specifications}",
        r"\begin{center}",
        r"\begin{tabular}{l l "
        r"S[table-format=4.2] S[table-format=4.2] S[table-format=4.2] l l}",
        r"\toprule",
        r"Parameter & Symbol & {Min} & {Typ} & {Max} & Unit & Conditions \\",
        r"\midrule",
    ]

    def row(param: str, sym: str, mn, ty, mx, unit: str, cond: str = "") -> str:
        def c(x):
            return _num(x) if isinstance(x, (int, float)) else "{--}"
        return (f"{_esc(param)} & {sym} & {c(mn)} & {c(ty)} & {c(mx)} & "
                f"{unit} & {_esc(cond)} \\\\")

    lines.append(row("Input voltage", "$V_{in}$", vin["min"], vin["nom"], vin["max"],
                     r"\si{\volt}", "DC"))
    for i, r in enumerate(rails):
        tag = "" if len(rails) == 1 else f"[{i}]"
        lines.append(row(f"Output voltage{tag}", f"$V_{{out{tag}}}$", None, r["v"], None,
                         r"\si{\volt}", str(r.get("regulation") or "")))
        if r["i"] is not None:
            lines.append(row(f"Output current{tag}", f"$I_{{out{tag}}}$", None, r["i"], None,
                             r"\si{\ampere}", "full load"))
        if r["p"] is not None:
            lines.append(row(f"Output power{tag}", f"$P_{{out{tag}}}$", None, r["p"], None,
                             r"\si{\watt}", ""))
    # Switching frequency (kHz)
    if m.fsw_hz:
        lines.append(row("Switching frequency", "$f_{sw}$", None, m.fsw_hz / 1e3, None,
                         r"\si{\kilo\hertz}", ""))
    # Efficiency
    et = m.eta_target()
    es = m.eta_sim()
    if et is not None:
        lines.append(row("Efficiency (target)", r"$\eta$", et * 100, None, None,
                         r"\si{\percent}", "design min"))
    if es is not None:
        lines.append(row("Efficiency (full load)", r"$\eta$", None, es * 100, None,
                         r"\si{\percent}", "simulated"))
    lines.append(row("Isolation", "--", None, None, None, "--",
                     "yes" if m.isolated else "no"))
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{center}"]
    return lines


def _theory(m: _ReportModel) -> list[str]:
    return [
        r"\section{Theory of Operation}",
        _theory_for(m.topology, m.isolated),
    ]


def _design_calcs(m: _ReportModel) -> list[str]:
    """Named quantity -> equation -> substituted numbers -> result."""
    lines = [r"\section{Design Calculations}",
             "The governing relations below are evaluated at the nominal operating "
             "point with the values Heaviside selected for this design."]
    vin = m.vin()
    rails = m.outputs()
    r0 = rails[0] if rails else {}
    vout = r0.get("v")
    iout = r0.get("i")
    mag = m.magnetics[0] if m.magnetics else None

    items: list[tuple[str, str, str]] = []  # (name, equation, result)

    # Duty cycle
    duty = m.tas.get("duty")
    if isinstance(duty, (int, float)) and vin["nom"]:
        items.append((
            "Duty cycle",
            r"D = \frac{V_{out}}{V_{in}}" + (
                rf" = \frac{{{_num(vout)}}}{{{_num(vin['nom'])}}}" if vout else ""),
            _num(duty, 3)))
    elif vout and vin["nom"] and not m.isolated:
        items.append((
            "Duty cycle (approx.)", r"D \approx \frac{V_{out}}{V_{in}} = "
            rf"\frac{{{_num(vout)}}}{{{_num(vin['nom'])}}}", _num(vout / vin["nom"], 3)))

    # Turns ratio (transformer) — present the full primary-referred ratio list
    # rather than guessing which winding is the secondary (center-tapped windings
    # make windings[1] ambiguous). The effective step-down ratio is the largest.
    if mag and mag["turns_ratios"]:
        ratios = mag["turns_ratios"]
        eff_n = max(ratios)
        items.append((
            "Primary-referred turns ratio", r"n = \frac{N_p}{N_s}",
            f"{_num(eff_n, 3)} : 1"))
    if mag and mag["windings"]:
        turn_list = [w.get("turns") for w in mag["windings"]
                     if isinstance(w.get("turns"), (int, float))]
        if turn_list and mag["role"] == "Transformer":
            items.append(("Winding turns",
                          r"N_{1..k} = " + ",\\,".join(str(int(t)) for t in turn_list),
                          f"{len(turn_list)} windings"))
        elif turn_list:
            items.append(("Inductor turns", rf"N = {int(turn_list[0])}",
                          f"{int(turn_list[0])} turns"))

    # Magnetizing / main inductance (from MAS designRequirements)
    if mag and mag.get("Lm") is not None:
        lm = _resolve(mag["Lm"])
        label = "Magnetizing inductance" if mag["role"] == "Transformer" else "Output inductance"
        sym = "L_m" if mag["role"] == "Transformer" else "L"
        items.append((label, sym + r" = \text{(MKF magnetic design)}", _si(lm, r"\henry")))
    elif mag and mag.get("inductance_h") is not None:
        items.append(("Output inductance", r"L = \text{(MKF magnetic design)}",
                      _si(mag["inductance_h"], r"\henry")))

    # Peak inductor / winding current
    if mag and mag.get("ipeak_a") is not None:
        items.append((
            "Peak winding current (worst OP)",
            r"I_{pk} = I_{out} + \tfrac{1}{2}\Delta I_L",
            _si(mag["ipeak_a"], r"\ampere")))

    # Output capacitor ripple (if we have a cap with stress)
    cap = next((b for b in m.bom if (b.get("category") or "") == "capacitor"), None)
    if cap and isinstance(cap.get("port_current"), (int, float)):
        items.append((
            "Output-cap RMS ripple current",
            r"I_{C,rms}\ \text{(from triangular inductor ripple)}",
            _si(cap["port_current"], r"\ampere")))

    # Render as an aligned list
    if not items:
        lines.append(r"\textit{Design-calculation inputs were not available for this "
                     r"topology in the current pipeline output.}")
        return lines
    lines.append(r"\begin{center}")
    lines.append(r"\begin{tabular}{>{\raggedright}p{0.34\linewidth} c >{\raggedleft}p{0.28\linewidth}}")
    lines.append(r"\toprule")
    lines.append(r"Quantity & Relation & {Result} \tabularnewline")
    lines.append(r"\midrule")
    for name, eq, res in items:
        lines.append(rf"{_esc(name)} & $\displaystyle {eq}$ & {res} \tabularnewline")
    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(r"\end{center}")
    return lines


def _magnetics(m: _ReportModel) -> list[str]:
    if not m.magnetics:
        return []
    lines = [r"\section{Magnetics Design}",
             "All magnetic quantities below are computed by the MKF magnetic engine "
             "(core geometry, flux density, saturation current and losses) --- not by a "
             "re-derived analytical formula."]
    for mag in m.magnetics:
        lines.append(rf"\subsection{{{_esc(mag['role'])} ({_esc(mag['refdes'])})}}")
        lines.append(r"\begin{center}\begin{tabular}{l l}\toprule")

        def kv(k: str, v: str) -> str:
            # ``k`` is author-controlled LaTeX (may contain math) — do NOT escape it.
            return rf"{k} & {v} \\"

        gap = "Distributed / ungapped" if not mag["gapping"] else (
            f"{len(mag['gapping'])} discrete gap(s)")
        rows = [
            ("Core", _esc(mag.get("core_name") or mag.get("shape") or "n/a")),
            ("Core shape", _esc(mag.get("shape") or "n/a")),
            ("Core material", _esc(mag.get("material") or "n/a")),
            ("Effective area $A_e$", _si(mag.get("Ae"), r"\meter\squared")),
            ("Effective length $l_e$", _si(mag.get("le"), r"\meter")),
            ("Effective volume $V_e$", _si(mag.get("Ve"), r"\meter\cubed")),
            ("Gapping", gap),
        ]
        for k, v in rows:
            lines.append(kv(k, v))
        # Windings
        for i, w in enumerate(mag["windings"]):
            t = w.get("turns")
            label = w.get("name") or (w.get("side") or f"winding {i}")
            wd = _si(w.get("wire_d"), r"\meter") if w.get("wire_d") else r"\textit{n/a}"
            tt = f"{int(t)} turns" if isinstance(t, (int, float)) else "n/a"
            lines.append(kv(f"Winding: {_esc(label)}", f"{tt}, $\\varnothing$ {wd}"))
        if mag["turns_ratios"]:
            lines.append(kv("Turns ratio(s)",
                            ", ".join(f"{_num(r, 3)}:1" for r in mag["turns_ratios"])))
        # Inductance
        if mag.get("Lm") is not None:
            sym = "L_m" if mag["role"] == "Transformer" else "L"
            lines.append(kv(f"${sym}$", _si(_resolve(mag['Lm']), r'\henry')))
        elif mag.get("inductance_h") is not None:
            lines.append(kv("$L$", _si(mag["inductance_h"], r"\henry")))
        if mag.get("Llk") is not None:
            lines.append(kv("Leakage $L_{lk}$", _si(_resolve(mag['Llk']), r"\henry")))
        # Saturation / peak
        if mag.get("isat_a") is not None:
            lines.append(kv("Saturation current $I_{sat}$ (MKF)",
                            _si(mag["isat_a"], r"\ampere")))
        if mag.get("ipeak_a") is not None:
            lines.append(kv("Peak current $I_{pk}$ (worst OP)",
                            _si(mag["ipeak_a"], r"\ampere")))
        if mag.get("isat_a") and mag.get("ipeak_a"):
            margin = (mag["isat_a"] - mag["ipeak_a"]) / mag["isat_a"]
            lines.append(kv("Saturation margin", _pct(margin)))
        if mag.get("bpk_t") is not None:
            lines.append(kv("Peak flux density $B_{pk}$ (MKF)",
                            _si(mag["bpk_t"], r"\tesla")))
        # Losses
        if mag.get("core_loss_w") is not None:
            lines.append(kv("Core loss (MKF)", _si(mag["core_loss_w"], r"\watt")))
        if mag.get("winding_loss_w") is not None:
            lines.append(kv("Winding loss (MKF)", _si(mag["winding_loss_w"], r"\watt")))
        if mag.get("total_loss_w") is not None:
            lines.append(kv("Total magnetic loss", _si(mag["total_loss_w"], r"\watt")))
        lines.append(r"\bottomrule\end{tabular}\end{center}")
    return lines


# Friendly descriptions per BOM category.
_CAT_DESC = {
    "mosfet": "Power MOSFET",
    "diode": "Rectifier / freewheel diode",
    "capacitor": "Capacitor",
    "inductor": "Power inductor",
    "transformer": "Power transformer",
    "controller": "PWM / control IC",
    "resistor": "Resistor",
}
_POWER_CATS = {"mosfet", "diode", "capacitor", "inductor", "transformer", "magnetic"}


def _bom(m: _ReportModel) -> list[str]:
    if not m.bom and not m.magnetics:
        return []
    lines = [r"\section{Bill of Materials}"]

    def fmt_rating(r: dict[str, Any]) -> str:
        parts = []
        if isinstance(r.get("rated_voltage"), (int, float)):
            parts.append(f"{_num(r['rated_voltage'], 3)} V")
        if isinstance(r.get("rated_current"), (int, float)):
            parts.append(f"{_num(r['rated_current'], 3)} A")
        return " / ".join(parts) or "--"

    # Split power stage vs control/bias.
    power_rows = [r for r in m.bom if (r.get("category") or "").lower() in _POWER_CATS]
    other_rows = [r for r in m.bom if (r.get("category") or "").lower() not in _POWER_CATS]

    def emit(title: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        lines.append(rf"\subsection*{{{_esc(title)}}}")
        lines.append(r"\begin{center}")
        lines.append(r"\begin{longtable}{l l p{2.6cm} l l l}")
        lines.append(r"\toprule")
        lines.append(r"Ref & Qty & Description & Rating & Manufacturer & MPN \\")
        lines.append(r"\midrule \endhead")
        for r in rows:
            cat = (r.get("category") or "").lower()
            desc = _CAT_DESC.get(cat, (r.get("category") or "Component"))
            lines.append(
                f"{_esc(r.get('ref') or '?')} & 1 & {_esc(desc)} & "
                f"{_esc(fmt_rating(r))} & {_esc(r.get('manufacturer') or '--')} & "
                f"{{\\small\\texttt{{{_esc(r.get('mpn') or '--')}}}}} \\\\")
        lines.append(r"\bottomrule")
        lines.append(r"\end{longtable}")
        lines.append(r"\end{center}")

    emit("Power Stage", power_rows)
    emit("Control \\& Bias", other_rows)
    if not power_rows and not other_rows:
        lines.append(r"\textit{No selected parts with MPNs were available in the design.}")
    return lines


def _coords(xs: Sequence[float], ys: Sequence[float], *, xscale: float = 1.0) -> str:
    return " ".join(f"({x * xscale:.6g},{y:.6g})" for x, y in zip(xs, ys))


def _waveforms(m: _ReportModel) -> list[str]:
    if not m.waveforms:
        return []
    wf = m.waveforms[0]
    t = wf.get("time_s") or []
    cur = wf.get("current_a") or []
    volt = wf.get("voltage_v")
    if not (t and cur):
        return []
    # Normalise time to start at 0 and scale to microseconds.
    t0 = t[0]
    t_us = [(x - t0) for x in t]
    xscale = 1e6
    lines = [
        r"\section{Operating Waveforms}",
        "Winding current and voltage of the main magnetic, taken from the design's "
        "own simulation (PyOM / ngspice excitation traces).",
        r"\begin{center}",
        r"\begin{tikzpicture}",
        r"\begin{axis}[width=0.92\linewidth, height=5.2cm, "
        r"xlabel={Time (\si{\micro\second})}, ylabel={Winding current (\si{\ampere})}, "
        r"grid=both, grid style={gray!20}, tick label style={font=\small}, "
        r"label style={font=\small}, no markers]",
        rf"\addplot[thick, blue] coordinates {{{_coords(t_us, cur, xscale=xscale)}}};",
        r"\end{axis}",
        r"\end{tikzpicture}",
        r"\end{center}",
    ]
    if isinstance(volt, list) and len(volt) == len(t):
        lines += [
            r"\begin{center}",
            r"\begin{tikzpicture}",
            r"\begin{axis}[width=0.92\linewidth, height=5.2cm, "
            r"xlabel={Time (\si{\micro\second})}, ylabel={Winding voltage (\si{\volt})}, "
            r"grid=both, grid style={gray!20}, tick label style={font=\small}, "
            r"label style={font=\small}, no markers]",
            rf"\addplot[thick, orange] coordinates {{{_coords(t_us, volt, xscale=xscale)}}};",
            r"\end{axis}",
            r"\end{tikzpicture}",
            r"\end{center}",
        ]
    # TODO (Phase 2): switch-node / Vds waveforms once stamped by the sim.
    return lines


def _loss_budget(m: _ReportModel) -> list[str]:
    lines = [r"\section{Power-Loss Budget}"]
    # Build per-(refdes, mechanism) numeric rows from the analyst budget.
    rows: list[tuple[str, str, float]] = []
    comp_total: dict[str, float] = {}
    for key, val in m.loss_budget.items():
        if not isinstance(val, (int, float)):
            continue
        refdes, mech = _split_loss_key(key)
        rows.append((refdes, mech, float(val)))
        comp_total[refdes] = comp_total.get(refdes, 0.0) + float(val)

    # Fill magnetic core/winding loss from the MAS when the analyst left it null.
    for mag in m.magnetics:
        ref = mag["refdes"]
        if comp_total.get(ref):
            continue  # analyst already has it
        added = False
        if mag.get("core_loss_w") is not None:
            rows.append((ref, "Core (MKF)", mag["core_loss_w"]))
            comp_total[ref] = comp_total.get(ref, 0.0) + mag["core_loss_w"]
            added = True
        if mag.get("winding_loss_w") is not None:
            rows.append((ref, "Winding (MKF)", mag["winding_loss_w"]))
            comp_total[ref] = comp_total.get(ref, 0.0) + mag["winding_loss_w"]
            added = True
        if added:
            continue

    total = sum(v for _, _, v in rows)
    sim_total = m.sim_op.get("total_losses")

    if not rows:
        if isinstance(sim_total, (int, float)):
            lines.append(
                "Per-component loss attribution was not produced by the analyst for this "
                f"design. The simulated total loss at full load is {_si(float(sim_total), r'\watt')}; "
                "the per-component split is unavailable.")
        else:
            lines.append(r"\textit{Loss-budget data was not available for this design.}")
        return lines

    lines.append(
        "Per-component loss attribution at full load, from the analyst stage "
        "(MOSFET conduction/switching, rectifier conduction/recovery, magnetic "
        "core/winding, capacitor ESR). Magnetic losses are taken from the MKF "
        "magnetic design.")
    # Table
    lines.append(r"\begin{center}")
    lines.append(r"\begin{tabular}{l l S[table-format=2.4] S[table-format=3.1]}")
    lines.append(r"\toprule")
    lines.append(r"Component & Loss mechanism & {Loss (\si{\watt})} & {\% of total} \\")
    lines.append(r"\midrule")
    for refdes, mech, val in sorted(rows, key=lambda r: (-comp_total[r[0]], r[0], r[1])):
        pct = (val / total * 100.0) if total else 0.0
        lines.append(rf"{_esc(refdes)} & {_esc(mech)} & {_num(val, 4)} & {_num(pct, 3)} \\")
    lines.append(r"\midrule")
    lines.append(rf"\textbf{{Total}} & & {_num(total, 4)} & {{100.0}} \\")
    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(r"\end{center}")

    if isinstance(sim_total, (int, float)):
        lines.append(
            rf"\small Cross-check: the closed-loop simulation reports a total loss of "
            rf"{_si(float(sim_total), r'\watt')} at full load "
            rf"(efficiency {_pct(m.eta_sim())}).")

    # Bar chart of per-component totals.
    if comp_total:
        items = sorted(comp_total.items(), key=lambda kv: -kv[1])
        coords = " ".join(f"({i},{v:.6g})" for i, (_, v) in enumerate(items))
        labels = ",".join(_esc(k) for k, _ in items)
        lines += [
            r"\begin{center}",
            r"\begin{tikzpicture}",
            r"\begin{axis}[ybar, width=0.85\linewidth, height=5cm, "
            r"ylabel={Loss (\si{\watt})}, "
            rf"symbolic x coords={{{labels}}}, xtick=data, "
            r"x tick label style={font=\small}, ymin=0, "
            r"bar width=14pt, nodes near coords, "
            r"every node near coord/.append style={font=\tiny}]",
        ]
        # Use index->name mapping; pgfplots ybar with symbolic coords:
        bar = " ".join(f"({_esc(k)},{v:.6g})" for k, v in items)
        lines[-1] = lines[-1]  # noop
        lines.append(rf"\addplot[fill=accent!60, draw=accent] coordinates {{{bar}}};")
        lines += [r"\end{axis}", r"\end{tikzpicture}", r"\end{center}"]
    return lines


# Affirmative phrasing + applied/rated source per known check name.
_CHECK_LABEL = {
    "fet_voltage_derating": ("Q1", "MOSFET $V_{ds}$"),
    "diode_voltage_derating": ("D1", "Diode $V_R$"),
    "capacitor_voltage_derating": ("C", "Cap working voltage"),
    "inductor_isat_margin": ("L1", "Inductor $I_{sat}$ margin"),
    "duty_cycle_bounds": ("--", "Duty cycle"),
    "efficiency_sanity": ("--", "Efficiency"),
    "thermal_limit": ("--", "Junction temperature"),
    "power_balance": ("--", "Power balance"),
    "output_voltage_regulation": ("--", "Output regulation"),
}


def _margins(m: _ReportModel) -> list[str]:
    lines = [r"\section{Design Margins / Component Stress}",
             "Applied stress versus device rating for each power component, with the "
             "headroom expressed affirmatively. Stresses are stamped from the simulated "
             "operating point; ratings are from the selected parts' datasheets."]

    # Primary source: BOM stress view (applied vs rated, per component).
    stress_rows: list[str] = []
    for r in m.bom:
        cat = (r.get("category") or "").lower()
        pv, rv = r.get("port_voltage"), r.get("rated_voltage")
        pc, rc = r.get("port_current"), r.get("rated_current")
        param_v = {"mosfet": "$V_{ds}$", "diode": "$V_R$",
                   "capacitor": "Working V"}.get(cat, "Voltage")
        if isinstance(pv, (int, float)) and isinstance(rv, (int, float)) and rv:
            margin = (rv - pv) / rv
            stress_rows.append(
                f"{_esc(r.get('ref') or '?')} & {param_v} & {_num(pv, 4)} & "
                f"{_num(rv, 4)} & {_num(margin * 100, 3)} & \\si{{\\volt}} \\\\")
        if isinstance(pc, (int, float)) and isinstance(rc, (int, float)) and rc:
            param_i = {"capacitor": "Ripple $I_{rms}$"}.get(cat, "Current")
            margin = (rc - pc) / rc
            stress_rows.append(
                f"{_esc(r.get('ref') or '?')} & {param_i} & {_num(pc, 4)} & "
                f"{_num(rc, 4)} & {_num(margin * 100, 3)} & \\si{{\\ampere}} \\\\")

    if stress_rows:
        lines.append(r"\begin{center}")
        lines.append(r"\begin{tabular}{l l S[table-format=4.3] S[table-format=4.3] "
                     r"S[table-format=3.1] l}")
        lines.append(r"\toprule")
        lines.append(r"Component & Parameter & {Applied} & {Rated} & {Margin \%} & Unit \\")
        lines.append(r"\midrule")
        lines += stress_rows
        lines.append(r"\bottomrule")
        lines.append(r"\end{tabular}")
        lines.append(r"\end{center}")

    # Secondary: affirmative summary of the named physics checks that passed.
    checks = m.verdict_dict.get("checks") if m.verdict_dict else None
    if isinstance(checks, list):
        good = [c for c in checks if isinstance(c, Mapping)
                and c.get("status") == "pass" and isinstance(c.get("margin"), (int, float))]
        if good:
            lines.append(r"\subsection*{Validated Physics Checks}")
            lines.append(r"\begin{center}")
            lines.append(r"\begin{tabular}{l S[table-format=3.4] S[table-format=2.4]}")
            lines.append(r"\toprule")
            lines.append(r"Check & {Value} & {Margin} \\")
            lines.append(r"\midrule")
            for c in good:
                name = c.get("name", "")
                known = _CHECK_LABEL.get(name)
                # Known labels are author LaTeX (may contain math) — insert verbatim;
                # otherwise prettify the raw check name and escape it.
                label = known[1] if known else _esc(name.replace("_", " ").title())
                val = c.get("value")
                vstr = _num(val, 4) if isinstance(val, (int, float)) else "{--}"
                lines.append(rf"{label} & {vstr} & {_num(c.get('margin'), 4)} \\")
            lines.append(r"\bottomrule")
            lines.append(r"\end{tabular}")
            lines.append(r"\end{center}")

    if not stress_rows and not (isinstance(checks, list) and checks):
        lines.append(r"\textit{Component-stress data was not available for this design.}")

    # TODO (Phase 2): junction-temperature (Tj) table, efficiency-vs-load and
    # regulation curves, load-transient response.
    return lines


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def render_latex(design_or_outcome: Any) -> str:
    """Render a :class:`ConverterDesign` (or legacy ``DesignOutcome``) as LaTeX
    source for a power-electronics design report. Returns the ``.tex`` string."""
    m = _ReportModel(design_or_outcome)
    parts: list[str] = [_preamble(), r"\begin{document}"]
    parts += _cover(m)
    parts += _key_specs(m)
    parts += _theory(m)
    parts += _design_calcs(m)
    parts += _magnetics(m)
    parts += _bom(m)
    parts += _waveforms(m)
    parts += _loss_budget(m)
    parts += _margins(m)
    parts.append(r"\end{document}")
    return "\n".join(parts) + "\n"


class LatexCompileError(RuntimeError):
    """Raised when ``pdflatex`` fails to compile the report."""


def render_pdf(design_or_outcome: Any, out_path: str | Path) -> Path:
    """Render the design to LaTeX and compile it to a PDF at ``out_path``.

    Runs ``pdflatex`` twice (for the table of contents / cross-references) inside
    a temporary directory, then copies the resulting PDF to ``out_path``. Raises
    :class:`LatexCompileError` (with the tail of the LaTeX log) if compilation
    fails, or if ``pdflatex`` is not installed."""
    pdflatex = shutil.which("pdflatex")
    if pdflatex is None:
        raise LatexCompileError(
            "pdflatex not found on PATH; install a LaTeX distribution (TeX Live) "
            "to compile the design report to PDF.")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tex = render_latex(design_or_outcome)

    with tempfile.TemporaryDirectory(prefix="hs_report_") as tmp:
        tmp_dir = Path(tmp)
        tex_path = tmp_dir / "report.tex"
        tex_path.write_text(tex, encoding="utf-8")
        log_tail = ""
        for _ in range(2):  # twice for TOC / references
            proc = subprocess.run(
                [pdflatex, "-interaction=nonstopmode", "-halt-on-error",
                 "-file-line-error", "report.tex"],
                cwd=tmp_dir, capture_output=True, text=True,
            )
            log_path = tmp_dir / "report.log"
            if log_path.exists():
                log_tail = "\n".join(log_path.read_text(
                    encoding="utf-8", errors="replace").splitlines()[-40:])
            if proc.returncode != 0:
                raise LatexCompileError(
                    "pdflatex failed to compile the design report.\n"
                    f"--- LaTeX log (tail) ---\n{log_tail}\n"
                    f"--- stdout (tail) ---\n{proc.stdout[-2000:]}")
        pdf_path = tmp_dir / "report.pdf"
        if not pdf_path.exists():
            raise LatexCompileError(
                "pdflatex returned success but produced no PDF.\n"
                f"--- LaTeX log (tail) ---\n{log_tail}")
        shutil.copyfile(pdf_path, out_path)
    return out_path
