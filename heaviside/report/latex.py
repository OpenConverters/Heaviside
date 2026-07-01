"""Render a :class:`ConverterDesign` (or legacy ``DesignOutcome``) as a
professional power-electronics **design report**, emitted as LaTeX and compiled
to PDF with ``pdflatex``.

Both this renderer and :mod:`heaviside.report.html` consume the same shared,
format-agnostic :class:`heaviside.report.model.ReportModel` and emit the **same
report** — written from a **power-electronics engineer's** point of view, in the
shape of a vendor eval-board application note (the pipeline-internal framing —
frequency sweep, realism checks, gatekeeper, diagnostics — is deliberately
dropped):

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
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from heaviside.report.model import (
    ReportModel,
    _CAT_DESC,
    _POWER_CATS,
    _resolve,
    sym_tex,
)

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


def _si_area(value_m2: float | None, sig: int = 4) -> str:
    """Area in mm² (base SI is m²). A metric prefix cannot be applied to a
    squared unit via _si — 'µm²' means (µm)², off by 1e6 — so render mm² directly
    (1 mm² = 1e-6 m²)."""
    if value_m2 is None:
        return r"\textit{n/a}"
    return rf"\SI{{{_num(value_m2 * 1e6, sig)}}}{{\milli\meter\squared}}"


def _si_volume(value_m3: float | None, sig: int = 4) -> str:
    """Volume in cm³ (base SI is m³). Same reason as _si_area — 'µm³' would be
    (µm)³, off by 1e12 — so render cm³ directly (1 cm³ = 1e-6 m³)."""
    if value_m3 is None:
        return r"\textit{n/a}"
    return rf"\SI{{{_num(value_m3 * 1e6, sig)}}}{{\centi\meter\cubed}}"


def _pct(ratio: float | None, sig: int = 3) -> str:
    if ratio is None:
        return r"\textit{n/a}"
    return rf"\SI{{{_num(ratio * 100.0, sig)}}}{{\percent}}"


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


def _cover(m: ReportModel) -> list[str]:
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


def _block_diagram(m: ReportModel) -> list[str]:
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


# siunitx unit macro per Key-Specifications unit token.
_SPEC_UNIT_TEX = {
    "V": r"\si{\volt}", "A": r"\si{\ampere}", "W": r"\si{\watt}",
    "kHz": r"\si{\kilo\hertz}", "%": r"\si{\percent}", "--": "--",
}
# Base-unit macro for auto-prefixed design-calc results.
_CALC_UNIT_TEX = {"H": r"\henry", "A": r"\ampere", "V": r"\volt", "W": r"\watt"}


def _key_specs(m: ReportModel) -> list[str]:
    lines = [
        r"\section{Key Specifications}",
        r"\begin{center}",
        r"\begin{tabular}{l l "
        r"S[table-format=4.2] S[table-format=4.2] S[table-format=4.2] l l}",
        r"\toprule",
        r"Parameter & Symbol & {Min} & {Typ} & {Max} & Unit & Conditions \\",
        r"\midrule",
    ]

    def c(x):
        return _num(x) if isinstance(x, (int, float)) else "{--}"

    for r in m.key_spec_rows():
        lines.append(
            f"{_esc(r['param'])} & {sym_tex(r['sym'])} & {c(r['min'])} & {c(r['typ'])} & "
            f"{c(r['max'])} & {_SPEC_UNIT_TEX[r['unit']]} & {_esc(r['cond'])} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{center}"]
    return lines


def _theory(m: ReportModel) -> list[str]:
    return [
        r"\section{Theory of Operation}",
        m.theory_text(),
    ]


def _calc_result_tex(result: tuple) -> str:
    """Format a design-calc result tuple from the model as LaTeX."""
    kind = result[0]
    if kind == "num":
        return _num(result[1], result[2])
    if kind == "si":
        return _si(result[1], _CALC_UNIT_TEX[result[2]])
    return str(result[1])  # "text"


def _design_calcs(m: ReportModel) -> list[str]:
    """Named quantity -> equation -> substituted numbers -> result."""
    lines = [r"\section{Design Calculations}",
             "The governing relations below are evaluated at the nominal operating "
             "point with the values Heaviside selected for this design."]
    items = m.design_calc_items()
    if not items:
        lines.append(r"\textit{Design-calculation inputs were not available for this "
                     r"topology in the current pipeline output.}")
        return lines
    lines.append(r"\begin{center}")
    lines.append(r"\begin{tabular}{>{\raggedright}p{0.34\linewidth} c >{\raggedleft}p{0.28\linewidth}}")
    lines.append(r"\toprule")
    lines.append(r"Quantity & Relation & {Result} \tabularnewline")
    lines.append(r"\midrule")
    for it in items:
        lines.append(rf"{_esc(it['name'])} & $\displaystyle {it['eq_tex']}$ & "
                     rf"{_calc_result_tex(it['result'])} \tabularnewline")
    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    lines.append(r"\end{center}")
    return lines


def _magnetics(m: ReportModel) -> list[str]:
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
            ("Effective area $A_e$", _si_area(mag.get("Ae"))),
            ("Effective length $l_e$", _si(mag.get("le"), r"\meter")),
            ("Effective volume $V_e$", _si_volume(mag.get("Ve"))),
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


def _bom(m: ReportModel) -> list[str]:
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

    mag_rows = m.magnetic_bom_rows()

    def emit(title: str, rows: list[dict[str, Any]], magnetics: list[dict[str, Any]] | None = None) -> None:
        if not rows and not magnetics:
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
        # Custom (designed) magnetics — no MPN; summarised by core + turns.
        for mr in magnetics or []:
            desc = f"Custom magnetic --- {mr['summary']}"
            lines.append(
                f"{_esc(mr['ref'])} & 1 & {_esc(desc)} & -- & "
                r"Custom (designed) & {\small\texttt{designed}} \\")
        lines.append(r"\bottomrule")
        lines.append(r"\end{longtable}")
        lines.append(r"\end{center}")

    emit("Power Stage", power_rows, mag_rows)
    emit("Control \\& Bias", other_rows)
    if not power_rows and not other_rows and not mag_rows:
        lines.append(r"\textit{No selected parts with MPNs were available in the design.}")
    return lines


def _coords(xs: Sequence[float], ys: Sequence[float], *, xscale: float = 1.0) -> str:
    return " ".join(f"({x * xscale:.6g},{y:.6g})" for x, y in zip(xs, ys))


def _waveforms(m: ReportModel) -> list[str]:
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


def _efficiency_regulation(m: ReportModel) -> list[str]:
    """Phase-2: efficiency-vs-load curve + load/line-regulation tables, from
    closed-loop re-sims of the SAME realized design (no re-design)."""
    el = m.efficiency_load_points()
    lr = m.line_regulation_points()
    pts = el["points"]
    lpts = lr["points"]
    if not pts and not lpts:
        return []
    lines = [r"\section{Efficiency \& Regulation}",
             "The realized design is re-simulated closed-loop (same parts, same "
             "magnetic) across load and line; the curves below are measured operating "
             "points, not re-designs."]

    # Efficiency vs load (pgfplots).
    if pts:
        coords = " ".join(f"({p['iout']:.6g},{p['eff'] * 100:.6g})" for p in pts)
        lines += [
            r"\subsection*{Efficiency vs Load}",
            r"\begin{center}",
            r"\begin{tikzpicture}",
            r"\begin{axis}[width=0.85\linewidth, height=5.2cm, "
            r"xlabel={Output current $I_{out}$ (\si{\ampere})}, "
            r"ylabel={Efficiency (\si{\percent})}, "
            r"grid=both, grid style={gray!20}, tick label style={font=\small}, "
            r"label style={font=\small}, mark=*, ymajorgrids]",
            rf"\addplot[thick, accent, mark=*] coordinates {{{coords}}};",
            r"\end{axis}",
            r"\end{tikzpicture}",
            r"\end{center}",
        ]
        # Load-regulation table (Vout vs Iout) from the same points.
        lines += [
            r"\begin{center}",
            r"\begin{tabular}{S[table-format=1.3] S[table-format=2.3] "
            r"S[table-format=2.3] S[table-format=2.2]}",
            r"\toprule",
            r"{$I_{out}$ (\si{\ampere})} & {$V_{out}$ (\si{\volt})} & "
            r"{$P_{out}$ (\si{\watt})} & {$\eta$ (\si{\percent})} \\",
            r"\midrule",
        ]
        for p in pts:
            lines.append(
                rf"{_num(p['iout'], 4)} & {_num(p['vout'], 4)} & "
                rf"{_num(p['pout'], 4)} & {_num(p['eff'] * 100, 4)} \\")
        lines += [r"\bottomrule", r"\end{tabular}", r"\end{center}"]
    if el["note"]:
        lines.append(rf"\small\textit{{{_esc(el['note'])}}}")

    # Line-regulation table (Vout vs Vin at full load).
    if lpts:
        lines += [
            r"\subsection*{Line Regulation (full load)}",
            r"\begin{center}",
            r"\begin{tabular}{S[table-format=3.2] S[table-format=2.3] "
            r"S[table-format=2.2]}",
            r"\toprule",
            r"{$V_{in}$ (\si{\volt})} & {$V_{out}$ (\si{\volt})} & "
            r"{$\eta$ (\si{\percent})} \\",
            r"\midrule",
        ]
        for p in lpts:
            lines.append(
                rf"{_num(p['vin'], 4)} & {_num(p['vout'], 4)} & "
                rf"{_num(p['eff'] * 100, 4)} \\")
        lines += [r"\bottomrule", r"\end{tabular}", r"\end{center}"]
        if lr["note"]:
            lines.append(rf"\small\textit{{{_esc(lr['note'])}}}")
    return lines


def _thermal(m: ReportModel) -> list[str]:
    """Phase-2: per-device junction temperature table."""
    th = m.thermal_rows()
    rows = th["rows"]
    if not rows:
        return []
    lines = [r"\section{Thermal (Junction Temperature)}",
             r"Estimated junction temperature $T_j = P_{loss}\cdot R_{\theta JA} + T_{amb}$ "
             "per power device, with headroom to the rated $T_{j,max}$. The thermal "
             "resistance and $T_{j,max}$ are from the selected part's datasheet; a device "
             "without a datasheet $R_{\\theta JA}$ shows n/a (no value is fabricated)."]
    lines += [
        r"\begin{center}",
        r"\begin{tabular}{l S[table-format=2.4] S[table-format=3.1] "
        r"S[table-format=3.1] S[table-format=3.1] S[table-format=3.1]}",
        r"\toprule",
        r"Device & {$P_{loss}$ (\si{\watt})} & {$R_{\theta JA}$ (\si{\kelvin\per\watt})} & "
        r"{$T_j$ (\si{\celsius})} & {$T_{j,max}$ (\si{\celsius})} & "
        r"{Margin (\si{\celsius})} \\",
        r"\midrule",
    ]

    def c(x, sig=4):
        return _num(x, sig) if isinstance(x, (int, float)) else r"{\textit{n/a}}"

    for r in rows:
        lines.append(
            f"{_esc(r['ref'])} & {_num(r['p_loss'], 4)} & {c(r['rth_ja'], 3)} & "
            f"{c(r['tj'], 4)} & {c(r['tj_max'], 4)} & {c(r['margin_c'], 3)} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{center}"]
    if isinstance(th["ambient_c"], (int, float)):
        lines.append(rf"\small Ambient $T_{{amb}} = {_num(th['ambient_c'], 3)}$\,\si{{\celsius}}.")
    if th["note"]:
        lines.append(rf"\small\textit{{{_esc(th['note'])}}}")
    return lines


def _schematic(m: ReportModel) -> list[str]:
    """Phase-2: realized netlist (Ref / type / net connections) — the honest
    interim for a schematic image."""
    rows = m.schematic_rows()
    if not rows:
        return []
    lines = [r"\section{Schematic (Netlist)}",
             "The realized circuit as a connection table (a rendered schematic image "
             "is out of scope; the netlist is the honest interim). Each row lists a "
             "component and the nets its pins connect to."]
    lines += [
        r"\begin{center}",
        r"\begin{longtable}{l l p{0.5\linewidth}}",
        r"\toprule",
        r"Ref & Type & Net connections (pin $\rightarrow$ net) \\",
        r"\midrule \endhead",
    ]
    for r in rows:
        if r["nets"]:
            nets = "; ".join(
                (f"{_esc(pin)} $\\rightarrow$ {_esc(net)}" if pin else _esc(net))
                for pin, net in r["nets"])
        else:
            nets = "--"
        lines.append(
            f"{_esc(r['ref'])} & {_esc(r['type'] or '--')} & {nets} \\\\")
    lines += [r"\bottomrule", r"\end{longtable}", r"\end{center}"]
    return lines


def _loss_budget(m: ReportModel) -> list[str]:
    lines = [r"\section{Power-Loss Budget}"]
    rows, comp_total, total = m.loss_rows()
    sim_total = m.sim_total_losses()

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

    # Phase-2: analyst-vs-sim reconciliation (surface the delta, don't hide it).
    recon = m.loss_reconciliation()
    if recon is not None:
        lines += [
            r"\subsection*{Analyst vs Simulation Reconciliation}",
            r"\begin{center}",
            r"\begin{tabular}{l S[table-format=2.4] l}",
            r"\toprule",
            r"Source & {Total loss (\si{\watt})} & Method \\",
            r"\midrule",
            rf"Analyst budget & {_num(recon['analyst_total'], 4)} & closed-form per-mechanism + MKF magnetic \\",
            rf"Simulation & {_num(recon['sim_total'], 4)} & measured $P_{{in}}-P_{{out}}$ (regulated) \\",
            r"\midrule",
            rf"\textbf{{Delta}} & {_num(recon['delta_w'], 4)} & "
            + (rf"{_num(recon['delta_pct'], 3)}\,\% of sim" if recon["delta_pct"] is not None else "--")
            + r" \\",
            r"\bottomrule",
            r"\end{tabular}",
            r"\end{center}",
            rf"\small\textit{{{_esc(recon['note'])}}}",
        ]

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


def _margins(m: ReportModel) -> list[str]:
    lines = [r"\section{Design Margins / Component Stress}",
             "Applied stress versus device rating for each power component, with the "
             "headroom expressed affirmatively. Stresses are stamped from the simulated "
             "operating point; ratings are from the selected parts' datasheets."]

    # Primary source: BOM stress view (applied vs rated, per component).
    stress_rows: list[str] = []
    for s in m.stress_rows():
        cat = s["cat"]
        if s["kind"] == "V":
            param = {"mosfet": "$V_{ds}$", "diode": "$V_R$",
                     "capacitor": "Working V"}.get(cat, "Voltage")
            unit = r"\si{\volt}"
        else:
            param = {"capacitor": "Ripple $I_{rms}$"}.get(cat, "Current")
            unit = r"\si{\ampere}"
        stress_rows.append(
            f"{_esc(s['ref'])} & {param} & {_num(s['applied'], 4)} & "
            f"{_num(s['rated'], 4)} & {_num(s['margin'] * 100, 3)} & {unit} \\\\")

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
    good = m.validated_checks()
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

    if not stress_rows and not good:
        lines.append(r"\textit{Component-stress data was not available for this design.}")
    return lines


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def render_latex(design_or_outcome: Any) -> str:
    """Render a :class:`ConverterDesign` (or legacy ``DesignOutcome``) as LaTeX
    source for a power-electronics design report. Returns the ``.tex`` string."""
    m = ReportModel(design_or_outcome)
    parts: list[str] = [_preamble(), r"\begin{document}"]
    parts += _cover(m)
    parts += _key_specs(m)
    parts += _theory(m)
    parts += _design_calcs(m)
    parts += _magnetics(m)
    parts += _bom(m)
    parts += _waveforms(m)
    parts += _efficiency_regulation(m)
    parts += _loss_budget(m)
    parts += _thermal(m)
    parts += _margins(m)
    parts += _schematic(m)
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
                cwd=tmp_dir, capture_output=True, text=True, errors="replace",
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
