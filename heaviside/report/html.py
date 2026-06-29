"""Render a ConverterDesign (or legacy DesignOutcome) as a standalone HTML report.

Produces a single self-contained HTML file with embedded CSS and inline SVG,
suitable for download, archival, or embedding in the Jobs viewer.

This renderer consumes the **same** shared, format-agnostic
:class:`heaviside.report.model.ReportModel` as the LaTeX/PDF renderer
(:mod:`heaviside.report.latex`) and emits the **same report** — a power-
electronics eval-board document, not a pipeline narration:

  0. Header / one-line spec / "validated" badge (+ optional block diagram)
  1. Key Specifications        (Parameter / Symbol / Min / Typ / Max / Unit)
  2. Theory of Operation       (topology-templated)
  3. Design Calculations       (quantity -> relation -> numbers -> result)
  4. Magnetics Design          (core / windings / Lm / Isat / Bpk, from MAS)
  5. Bill of Materials         (power stage + control/bias)
  6. Operating Waveforms       (winding current + voltage, inline SVG)
  7. Power-Loss Budget         (per-component W + %, table + bar)
  8. Design Margins            (applied vs rated, affirmative, from the gate)

The pipeline-internal sections the old report carried (frequency sweep, realism
checks, gatekeeper, reviewer panel, raw diagnostics) are deliberately dropped, so
the HTML report matches the LaTeX/PDF one section-for-section. Nothing is
fabricated: a genuinely-absent value renders ``n/a`` / ``—`` or the row is
omitted (CLAUDE.md no-fallback rule); all magnetics numbers come from the
MAS/MKF.
"""

from __future__ import annotations

import html
import re
from typing import Any

from heaviside.report.model import ReportModel, _CAT_DESC, _POWER_CATS, _g, sym_html

_CSS = """
@page { size: A4; margin: 20mm 18mm; }
*, *::before, *::after { box-sizing: border-box; }
body {
  font-family: Georgia, 'Times New Roman', serif;
  font-size: 10.5pt;
  max-width: 860px;
  margin: 2rem auto;
  padding: 0 1.5rem;
  color: #111;
  line-height: 1.5;
  background: #fff;
}

/* ── Header ── */
.rpt-header {
  display: flex; align-items: flex-start; justify-content: space-between;
  border-bottom: 2px solid #111; padding-bottom: 0.6em; margin-bottom: 0.4em;
}
.rpt-title { font-size: 1.4em; font-weight: bold; margin: 0; }
.rpt-subtitle { font-size: 0.95em; color: #555; margin: 0.15em 0 0; }
.rpt-oneline { font-size: 1.05em; font-weight: bold; margin: 0.5em 0 0.2em;
  font-family: 'Courier New', Courier, monospace; }
.verdict-badge {
  display: inline-block;
  padding: 0.2em 0.7em; border-radius: 3px;
  font-family: 'Courier New', Courier, monospace;
  font-size: 0.92em; font-weight: bold; letter-spacing: 0.05em;
}
.v-pass  { background: #d1fae5; color: #065f46; border: 1px solid #6ee7b7; }
.v-fail  { background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5; }
.v-warn  { background: #fef3c7; color: #92400e; border: 1px solid #fcd34d; }
.validated-note { color: #065f46; font-size: 0.9em; margin: 0.2em 0 0.6em; }

/* ── Block diagram ── */
.block-diagram { display: flex; flex-wrap: wrap; align-items: center; gap: 0.3em;
  margin: 0.8em 0 1.2em; }
.bd-box { border: 1.5px solid #1e3a5a; border-radius: 4px; padding: 0.35em 0.7em;
  font-size: 0.85em; background: #f4f7fb; }
.bd-box.bd-ctrl { background: #e7edf5; }
.bd-arrow { color: #1e3a5a; font-weight: bold; }
.bd-port { font-family: 'Courier New', Courier, monospace; font-size: 0.85em; }

/* ── Sections ── */
h2 {
  font-size: 1.05em; font-weight: bold; text-transform: uppercase;
  letter-spacing: 0.06em; border-bottom: 1px solid #ccc;
  margin: 1.8em 0 0.6em; padding-bottom: 0.2em; color: #222;
}
h3 { font-size: 0.96em; font-weight: bold; color: #1e3a5a; margin: 1.1em 0 0.3em; }

/* ── Tables ── */
table {
  border-collapse: collapse; width: 100%; margin: 0.6em 0 1em;
  font-size: 0.87em;
}
th {
  border-top: 1.5px solid #111; border-bottom: 1px solid #111;
  padding: 0.3em 0.55em; text-align: left; font-weight: normal;
  font-style: italic; background: none;
}
td {
  padding: 0.3em 0.55em; vertical-align: top;
  border-bottom: 1px solid #ddd;
}
tr:last-child td { border-bottom: 1.5px solid #111; }
.mpn { font-family: 'Courier New', Courier, monospace; font-size: 0.84em; }
.num { font-family: 'Courier New', Courier, monospace; text-align: right; }
td.num, th.num { text-align: right; }
tr.total-row td { font-weight: bold; }

/* ── Theory / relations ── */
.theory { margin: 0.4em 0 0.8em; }
.relation { font-style: italic; }

/* ── Loss bar ── */
.loss-bars { margin: 0.6em 0 1em; }
.loss-bar-row { display: flex; align-items: center; gap: 0.6em; margin: 0.25em 0; }
.loss-bar-label { width: 16%; font-size: 0.84em; font-family: 'Courier New', monospace; }
.loss-bar-track { flex: 1; background: #eef1f5; border-radius: 3px; height: 1.1em; }
.loss-bar-fill { background: #1e3a5a; height: 100%; border-radius: 3px; }
.loss-bar-val { width: 16%; font-size: 0.82em; font-family: 'Courier New', monospace;
  text-align: right; }

/* ── Waveform ── */
.wf-svg { display: block; width: 100%; max-width: 680px;
          background: #06100f; border-radius: 6px;
          border: 1px solid rgba(60,224,200,.3); margin: 0.4em 0; }
"""


def _e(text: Any) -> str:
    return html.escape(str(text))


# ─────────────────────────────────────────────────────────────────────────────
# Number / unit / math formatting (HTML side; mirrors latex._si / siunitx)
# ─────────────────────────────────────────────────────────────────────────────

_HTML_PREFIXES = [
    (1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""),
    (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p"),
]


def _fmt_si(value: float | None, unit: str, *, sig: int = 4) -> str:
    """Engineering-notation value+unit (e.g. ``10 µH``), value in base SI units.
    Mirrors ``latex._si`` so the two reports show the same magnitudes."""
    if value is None:
        return "n/a"
    if value == 0:
        return f"0 {unit}"
    av = abs(value)
    for scale, pfx in _HTML_PREFIXES:
        if av >= scale or scale == 1e-12:
            return f"{_g(value / scale, sig)} {pfx}{unit}"
    return f"{_g(value, sig)} {unit}"


_MATH_REPL = {
    r"\,": " ", r"\;": " ", r"\ ": " ", r"\pm": "±", r"\approx": "≈",
    r"\rightarrow": "→", r"\checkmark": "✓", r"\pi": "π", r"\eta": "η",
    r"\Delta": "Δ", r"\varnothing": "⌀", r"\times": "×", r"\cdot": "·",
}


def _math_to_html(expr: str) -> str:
    """Convert a small inline-LaTeX math expression to readable HTML."""
    s = expr
    # Fractions (non-nested): \frac{a}{b} / \tfrac{a}{b} -> (a)/(b)
    s = re.sub(r"\\t?frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", s)
    s = re.sub(r"\\sqrt\{([^{}]*)\}", r"√(\1)", s)
    s = re.sub(r"\\text\{([^{}]*)\}", r"\1", s)
    for k, v in _MATH_REPL.items():
        s = s.replace(k, v)
    s = re.sub(r"_\{([^{}]*)\}", r"<sub>\1</sub>", s)
    s = re.sub(r"_([A-Za-z0-9])", r"<sub>\1</sub>", s)
    s = re.sub(r"\^\{([^{}]*)\}", r"<sup>\1</sup>", s)
    s = re.sub(r"\^([A-Za-z0-9])", r"<sup>\1</sup>", s)
    return s.replace("{", "").replace("}", "")


def _tex_to_html(text: str) -> str:
    """Escape prose and convert any ``$...$`` math spans to inline HTML."""
    out: list[str] = []
    for part in re.split(r"(\$[^$]*\$)", text):
        if part.startswith("$") and part.endswith("$") and len(part) >= 2:
            out.append("<em>" + _math_to_html(part[1:-1]) + "</em>")
        else:
            out.append(html.escape(part))
    return "".join(out)


def _fmt_hz(hz: float) -> str:
    if hz >= 1e6:
        return f"{hz / 1e6:.3g} MHz"
    if hz >= 1e3:
        return f"{hz / 1e3:.3g} kHz"
    return f"{hz:.0f} Hz"


# ─────────────────────────────────────────────────────────────────────────────
# Waveform SVG (kept: also imported by stages.reporter / tests)
# ─────────────────────────────────────────────────────────────────────────────

def _poly(xs: list[float], ys: list[float], w: int, h: int, pad: int) -> str:
    if not xs or not ys or len(xs) != len(ys):
        return ""
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    xr = (x1 - x0) or 1.0
    yr = (y1 - y0) or 1.0
    pts = []
    for x, y in zip(xs, ys):
        px = pad + (x - x0) / xr * (w - 2 * pad)
        py = h - pad - (y - y0) / yr * (h - 2 * pad)
        pts.append(f"{px:.1f},{py:.1f}")
    return " ".join(pts)


def _waveform_svg(wfs: list[dict[str, Any]], *, w: int = 680, h: int = 200) -> str:
    if not wfs:
        return ""
    wf = wfs[0]
    t = wf.get("time_s") or []
    cur = wf.get("current_a") or []
    volt = wf.get("voltage_v")
    cur_pts = _poly(t, cur, w, h, 24)
    if not cur_pts:
        return ""
    parts = [
        f"<svg class='wf-svg' viewBox='0 0 {w} {h}' width='100%' "
        f"style='height:{h}px' xmlns='http://www.w3.org/2000/svg'>",
        f"<rect width='{w}' height='{h}' fill='#06100f'/>",
        f"<polyline fill='none' stroke='#3ce0c8' stroke-width='1.6' points='{cur_pts}'/>",
    ]
    if isinstance(volt, list) and len(volt) == len(t):
        v_pts = _poly(t, volt, w, h, 24)
        if v_pts:
            parts.append(
                f"<polyline fill='none' stroke='#ffb84d' stroke-width='1.2' "
                f"opacity='0.8' points='{v_pts}'/>")
    cmin, cmax = min(cur), max(cur)
    parts.append(
        f"<text x='26' y='14' fill='#3ce0c8' font-size='9' "
        f"font-family='monospace'>I (aqua) {cmin:.2f}..{cmax:.2f} A"
        f"{' · V (amber)' if isinstance(volt, list) else ''}</text>")
    parts.append("</svg>")
    return "".join(parts)


def _try_waveforms(mas: Any) -> list[dict[str, Any]]:
    try:
        from heaviside.pipeline.converter_designer import magnetic_waveforms
        return magnetic_waveforms(mas, max_points=300)
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def render_html(design_or_outcome: Any) -> str:
    """Render a ``ConverterDesign`` or legacy ``DesignOutcome`` as an HTML
    power-electronics design report (same sections as the LaTeX/PDF report)."""
    m = ReportModel(design_or_outcome)
    body: list[str] = []
    body += _header(m)
    body += _key_specs(m)
    body += _theory(m)
    body += _design_calcs(m)
    body += _magnetics(m)
    body += _bom(m)
    body += _waveforms(m)
    body += _loss_budget(m)
    body += _margins(m)

    return "\n".join([
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>",
        f"<title>{_e(m.topo_label)}</title>",
        f"<style>{_CSS}</style></head><body>",
        *body,
        "</body></html>",
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Section renderers
# ─────────────────────────────────────────────────────────────────────────────

def _header(m: ReportModel) -> list[str]:
    vin = m.vin()
    rails = m.outputs()
    vin_s = _g(vin["nom"]) if vin["nom"] is not None else "?"
    rail_parts = []
    for r in rails:
        if r["v"] is not None and r["i"] is not None:
            rail_parts.append(f"{_g(r['v'])} V / {_g(r['i'])} A")
        elif r["v"] is not None:
            rail_parts.append(f"{_g(r['v'])} V")
    rail_s = ", ".join(rail_parts) or "?"
    oneline = f"{vin_s} V → {rail_s}"
    pout = m.pout()
    if pout is not None:
        oneline += f", {_g(pout, 3)} W"

    verdict = str(m.verdict or "?")
    verdict_cls = "v-pass" if m.passed else ("v-fail" if m.verdict else "v-warn")

    lines = [
        "<div class='rpt-header'>",
        "<div>",
        f"<h1 class='rpt-title'>{_e(m.topo_label)}</h1>",
        "<p class='rpt-subtitle'>Power Converter Design Report</p>",
        "</div>",
        f"<span class='verdict-badge {verdict_cls}'>{_e(verdict.upper())}</span>",
        "</div>",
        f"<p class='rpt-oneline'>{_e(oneline)}</p>",
    ]
    if m.passed:
        lines.append("<p class='validated-note'>✓ Design validated — all applicable "
                     "physics checks passed.</p>")
    lines += _block_diagram(m)
    return lines


_STAGE_LABEL = {
    "control": "Control", "switchingCell": "Switching Cell",
    "switching_cell": "Switching Cell", "filter": "Output Filter",
    "inputFilter": "Input Filter", "input_filter": "Input Filter",
    "rectifier": "Rectifier", "transformer": "Transformer", "tank": "Resonant Tank",
}


def _humanise_stage(name: str) -> str:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).replace("_", " ")
    return " ".join(w.capitalize() for w in spaced.split())


def _block_diagram(m: ReportModel) -> list[str]:
    power = [s for s in m.stages if s != "control"]
    if not power:
        return []
    parts = ["<div class='block-diagram'>", "<span class='bd-port'>V<sub>in</sub></span>"]
    for s in power:
        parts.append("<span class='bd-arrow'>→</span>")
        parts.append(f"<span class='bd-box'>{_e(_STAGE_LABEL.get(s) or _humanise_stage(s))}</span>")
    parts.append("<span class='bd-arrow'>→</span>")
    parts.append("<span class='bd-port'>V<sub>out</sub></span>")
    if "control" in m.stages:
        parts.append("<span class='bd-box bd-ctrl'>Control</span>")
    parts.append("</div>")
    return parts


_SPEC_UNIT_HTML = {"V": "V", "A": "A", "W": "W", "kHz": "kHz", "%": "%", "--": "—"}


def _key_specs(m: ReportModel) -> list[str]:
    lines = ["<h2>Key Specifications</h2>",
             "<table><tr><th>Parameter</th><th>Symbol</th><th class='num'>Min</th>"
             "<th class='num'>Typ</th><th class='num'>Max</th><th>Unit</th>"
             "<th>Conditions</th></tr>"]

    def c(x: Any) -> str:
        return _g(x) if isinstance(x, (int, float)) else "—"

    for r in m.key_spec_rows():
        lines.append(
            f"<tr><td>{_e(r['param'])}</td><td>{sym_html(r['sym'])}</td>"
            f"<td class='num'>{c(r['min'])}</td><td class='num'>{c(r['typ'])}</td>"
            f"<td class='num'>{c(r['max'])}</td><td>{_e(_SPEC_UNIT_HTML[r['unit']])}</td>"
            f"<td>{_e(r['cond'])}</td></tr>")
    lines.append("</table>")
    return lines


def _theory(m: ReportModel) -> list[str]:
    return ["<h2>Theory of Operation</h2>",
            f"<p class='theory'>{_tex_to_html(m.theory_text())}</p>"]


def _calc_result_html(result: tuple) -> str:
    kind = result[0]
    if kind == "num":
        return _g(result[1], result[2])
    if kind == "si":
        return _fmt_si(result[1], result[2])
    return _e(result[1])  # "text"


def _design_calcs(m: ReportModel) -> list[str]:
    lines = ["<h2>Design Calculations</h2>",
             "<p>The governing relations below are evaluated at the nominal operating "
             "point with the values Heaviside selected for this design.</p>"]
    items = m.design_calc_items()
    if not items:
        lines.append("<p><em>Design-calculation inputs were not available for this "
                     "topology in the current pipeline output.</em></p>")
        return lines
    lines.append("<table><tr><th>Quantity</th><th>Relation</th>"
                 "<th class='num'>Result</th></tr>")
    for it in items:
        lines.append(
            f"<tr><td>{_e(it['name'])}</td>"
            f"<td class='relation'>{it['eq_html']}</td>"
            f"<td class='num'>{_calc_result_html(it['result'])}</td></tr>")
    lines.append("</table>")
    return lines


def _magnetics(m: ReportModel) -> list[str]:
    if not m.magnetics:
        return []
    lines = ["<h2>Magnetics Design</h2>",
             "<p>All magnetic quantities below are computed by the MKF magnetic engine "
             "(core geometry, flux density, saturation current and losses) — not by a "
             "re-derived analytical formula.</p>"]
    from heaviside.report.model import _resolve
    for mag in m.magnetics:
        lines.append(f"<h3>{_e(mag['role'])} ({_e(mag['refdes'])})</h3>")
        lines.append("<table>")

        def kv(k: str, v: str) -> None:
            # ``k`` may contain author HTML (subscripts); ``v`` is pre-formatted.
            lines.append(f"<tr><td>{k}</td><td>{v}</td></tr>")

        gap = "Distributed / ungapped" if not mag["gapping"] else (
            f"{len(mag['gapping'])} discrete gap(s)")
        kv("Core", _e(mag.get("core_name") or mag.get("shape") or "n/a"))
        kv("Core shape", _e(mag.get("shape") or "n/a"))
        kv("Core material", _e(mag.get("material") or "n/a"))
        kv("Effective area A<sub>e</sub>", _e(_fmt_si(mag.get("Ae"), "m²")))
        kv("Effective length l<sub>e</sub>", _e(_fmt_si(mag.get("le"), "m")))
        kv("Effective volume V<sub>e</sub>", _e(_fmt_si(mag.get("Ve"), "m³")))
        kv("Gapping", _e(gap))
        for i, w in enumerate(mag["windings"]):
            t = w.get("turns")
            label = w.get("name") or (w.get("side") or f"winding {i}")
            wd = _fmt_si(w.get("wire_d"), "m") if w.get("wire_d") else "n/a"
            tt = f"{int(t)} turns" if isinstance(t, (int, float)) else "n/a"
            kv(f"Winding: {_e(label)}", f"{_e(tt)}, ⌀ {_e(wd)}")
        if mag["turns_ratios"]:
            kv("Turns ratio(s)",
               _e(", ".join(f"{_g(r, 3)}:1" for r in mag["turns_ratios"])))
        if mag.get("Lm") is not None:
            sym = "L<sub>m</sub>" if mag["role"] == "Transformer" else "L"
            kv(sym, _e(_fmt_si(_resolve(mag["Lm"]), "H")))
        elif mag.get("inductance_h") is not None:
            kv("L", _e(_fmt_si(mag["inductance_h"], "H")))
        if mag.get("Llk") is not None:
            kv("Leakage L<sub>lk</sub>", _e(_fmt_si(_resolve(mag["Llk"]), "H")))
        if mag.get("isat_a") is not None:
            kv("Saturation current I<sub>sat</sub> (MKF)", _e(_fmt_si(mag["isat_a"], "A")))
        if mag.get("ipeak_a") is not None:
            kv("Peak current I<sub>pk</sub> (worst OP)", _e(_fmt_si(mag["ipeak_a"], "A")))
        if mag.get("isat_a") and mag.get("ipeak_a"):
            margin = (mag["isat_a"] - mag["ipeak_a"]) / mag["isat_a"]
            kv("Saturation margin", _e(f"{_g(margin * 100, 3)} %"))
        if mag.get("bpk_t") is not None:
            kv("Peak flux density B<sub>pk</sub> (MKF)", _e(_fmt_si(mag["bpk_t"], "T")))
        if mag.get("core_loss_w") is not None:
            kv("Core loss (MKF)", _e(_fmt_si(mag["core_loss_w"], "W")))
        if mag.get("winding_loss_w") is not None:
            kv("Winding loss (MKF)", _e(_fmt_si(mag["winding_loss_w"], "W")))
        if mag.get("total_loss_w") is not None:
            kv("Total magnetic loss", _e(_fmt_si(mag["total_loss_w"], "W")))
        lines.append("</table>")
    return lines


def _bom(m: ReportModel) -> list[str]:
    if not m.bom and not m.magnetics:
        return []
    lines = ["<h2>Bill of Materials</h2>"]

    def fmt_rating(r: dict[str, Any]) -> str:
        parts = []
        if isinstance(r.get("rated_voltage"), (int, float)):
            parts.append(f"{_g(r['rated_voltage'], 3)} V")
        if isinstance(r.get("rated_current"), (int, float)):
            parts.append(f"{_g(r['rated_current'], 3)} A")
        return " / ".join(parts) or "—"

    power_rows = [r for r in m.bom if (r.get("category") or "").lower() in _POWER_CATS]
    other_rows = [r for r in m.bom if (r.get("category") or "").lower() not in _POWER_CATS]

    def emit(title: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        lines.append(f"<h3>{_e(title)}</h3>")
        lines.append("<table><tr><th>Ref</th><th>Qty</th><th>Description</th>"
                     "<th>Rating</th><th>Manufacturer</th><th>MPN</th></tr>")
        for r in rows:
            cat = (r.get("category") or "").lower()
            desc = _CAT_DESC.get(cat, (r.get("category") or "Component"))
            lines.append(
                f"<tr><td>{_e(r.get('ref') or '?')}</td><td>1</td>"
                f"<td>{_e(desc)}</td><td>{_e(fmt_rating(r))}</td>"
                f"<td>{_e(r.get('manufacturer') or '—')}</td>"
                f"<td class='mpn'>{_e(r.get('mpn') or '—')}</td></tr>")
        lines.append("</table>")

    emit("Power Stage", power_rows)
    emit("Control & Bias", other_rows)
    if not power_rows and not other_rows:
        lines.append("<p><em>No selected parts with MPNs were available in the design.</em></p>")
    return lines


def _waveforms(m: ReportModel) -> list[str]:
    svg = _waveform_svg(m.waveforms)
    if not svg:
        return []
    return ["<h2>Operating Waveforms</h2>",
            "<p>Winding current and voltage of the main magnetic, taken from the "
            "design's own simulation (PyOM / ngspice excitation traces).</p>",
            svg]


def _loss_budget(m: ReportModel) -> list[str]:
    lines = ["<h2>Power-Loss Budget</h2>"]
    rows, comp_total, total = m.loss_rows()
    sim_total = m.sim_total_losses()

    if not rows:
        if sim_total is not None:
            lines.append(
                "<p>Per-component loss attribution was not produced by the analyst for "
                f"this design. The simulated total loss at full load is "
                f"{_e(_fmt_si(sim_total, 'W'))}; the per-component split is unavailable.</p>")
        else:
            lines.append("<p><em>Loss-budget data was not available for this design.</em></p>")
        return lines

    lines.append(
        "<p>Per-component loss attribution at full load, from the analyst stage "
        "(MOSFET conduction/switching, rectifier conduction/recovery, magnetic "
        "core/winding, capacitor ESR). Magnetic losses are taken from the MKF "
        "magnetic design.</p>")
    lines.append("<table><tr><th>Component</th><th>Loss mechanism</th>"
                 "<th class='num'>Loss (W)</th><th class='num'>% of total</th></tr>")
    for refdes, mech, val in sorted(rows, key=lambda r: (-comp_total[r[0]], r[0], r[1])):
        pct = (val / total * 100.0) if total else 0.0
        lines.append(f"<tr><td>{_e(refdes)}</td><td>{_e(mech)}</td>"
                     f"<td class='num'>{_g(val, 4)}</td><td class='num'>{_g(pct, 3)}</td></tr>")
    lines.append(f"<tr class='total-row'><td>Total</td><td></td>"
                 f"<td class='num'>{_g(total, 4)}</td><td class='num'>100</td></tr>")
    lines.append("</table>")

    if sim_total is not None:
        eta = m.eta_sim()
        eta_s = f"{_g(eta * 100, 3)} %" if eta is not None else "n/a"
        lines.append(
            f"<p style='font-size:0.85em;color:#555'>Cross-check: the closed-loop "
            f"simulation reports a total loss of {_e(_fmt_si(sim_total, 'W'))} at full "
            f"load (efficiency {_e(eta_s)}).</p>")

    # Per-component bar chart.
    if comp_total:
        items = sorted(comp_total.items(), key=lambda kv: -kv[1])
        peak = items[0][1] or 1.0
        lines.append("<div class='loss-bars'>")
        for name, val in items:
            frac = max(0.0, val / peak) * 100.0 if peak else 0.0
            lines.append(
                "<div class='loss-bar-row'>"
                f"<span class='loss-bar-label'>{_e(name)}</span>"
                "<span class='loss-bar-track'>"
                f"<span class='loss-bar-fill' style='width:{frac:.1f}%'></span></span>"
                f"<span class='loss-bar-val'>{_e(_fmt_si(val, 'W'))}</span></div>")
        lines.append("</div>")
    return lines


# Affirmative phrasing per known check name (HTML labels with subscripts).
_CHECK_LABEL = {
    "fet_voltage_derating": "MOSFET V<sub>ds</sub>",
    "diode_voltage_derating": "Diode V<sub>R</sub>",
    "capacitor_voltage_derating": "Cap working voltage",
    "inductor_isat_margin": "Inductor I<sub>sat</sub> margin",
    "duty_cycle_bounds": "Duty cycle",
    "efficiency_sanity": "Efficiency",
    "thermal_limit": "Junction temperature",
    "power_balance": "Power balance",
    "output_voltage_regulation": "Output regulation",
}


def _margins(m: ReportModel) -> list[str]:
    lines = ["<h2>Design Margins / Component Stress</h2>",
             "<p>Applied stress versus device rating for each power component, with the "
             "headroom expressed affirmatively. Stresses are stamped from the simulated "
             "operating point; ratings are from the selected parts' datasheets.</p>"]

    stress = m.stress_rows()
    if stress:
        lines.append("<table><tr><th>Component</th><th>Parameter</th>"
                     "<th class='num'>Applied</th><th class='num'>Rated</th>"
                     "<th class='num'>Margin %</th><th>Unit</th></tr>")
        for s in stress:
            cat = s["cat"]
            if s["kind"] == "V":
                param = {"mosfet": "V<sub>ds</sub>", "diode": "V<sub>R</sub>",
                         "capacitor": "Working V"}.get(cat, "Voltage")
                unit = "V"
            else:
                param = {"capacitor": "Ripple I<sub>rms</sub>"}.get(cat, "Current")
                unit = "A"
            lines.append(
                f"<tr><td>{_e(s['ref'])}</td><td>{param}</td>"
                f"<td class='num'>{_g(s['applied'], 4)}</td>"
                f"<td class='num'>{_g(s['rated'], 4)}</td>"
                f"<td class='num'>{_g(s['margin'] * 100, 3)}</td><td>{_e(unit)}</td></tr>")
        lines.append("</table>")

    good = m.validated_checks()
    if good:
        lines.append("<h3>Validated Physics Checks</h3>")
        lines.append("<table><tr><th>Check</th><th class='num'>Value</th>"
                     "<th class='num'>Margin</th></tr>")
        for c in good:
            name = c.get("name", "")
            label = _CHECK_LABEL.get(name) or _e(str(name).replace("_", " ").title())
            val = c.get("value")
            vstr = _g(val, 4) if isinstance(val, (int, float)) else "—"
            lines.append(f"<tr><td>{label}</td><td class='num'>{vstr}</td>"
                         f"<td class='num'>{_g(c.get('margin'), 4)}</td></tr>")
        lines.append("</table>")

    if not stress and not good:
        lines.append("<p><em>Component-stress data was not available for this design.</em></p>")
    return lines
