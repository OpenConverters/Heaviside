"""Render a ConverterDesign (or legacy DesignOutcome) as a standalone HTML report.

Produces a single self-contained HTML file with embedded CSS and inline SVG.
Suitable for download, archival, or embedding in the Jobs viewer.

The report leads with the converter-level story:
  1. Header: topology, verdict, switching frequency
  2. Operating point: Vin / Vout / Iout / power
  3. Frequency sweep (loss-vs-fsw spark line)
  4. Inductor summary (core, L, Bpk, scoring)
  5. Realism checks
  6. BOM
  7. Reviewer panel (Ray / Nicola)
  8. Waveforms
  9. Diagnostics
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from typing import Any

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
  border-bottom: 2px solid #111; padding-bottom: 0.6em; margin-bottom: 0.8em;
}
.rpt-title { font-size: 1.4em; font-weight: bold; margin: 0; }
.rpt-subtitle { font-size: 0.95em; color: #555; margin: 0.15em 0 0; }
.verdict-badge {
  display: inline-block;
  padding: 0.2em 0.7em; border-radius: 3px;
  font-family: 'Courier New', Courier, monospace;
  font-size: 0.92em; font-weight: bold; letter-spacing: 0.05em;
}
.v-pass  { background: #d1fae5; color: #065f46; border: 1px solid #6ee7b7; }
.v-fail  { background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5; }
.v-warn  { background: #fef3c7; color: #92400e; border: 1px solid #fcd34d; }

/* ── Sections ── */
h2 {
  font-size: 1.05em; font-weight: bold; text-transform: uppercase;
  letter-spacing: 0.06em; border-bottom: 1px solid #ccc;
  margin: 1.8em 0 0.6em; padding-bottom: 0.2em; color: #222;
}

/* ── Summary strip ── */
.kv-strip {
  display: flex; flex-wrap: wrap; gap: 0 2.2em;
  margin: 0.5em 0 1.2em; font-size: 0.93em;
}
.kv-strip .kv { display: flex; flex-direction: column; }
.kv-strip .kv-label { font-size: 0.75em; text-transform: uppercase;
  letter-spacing: 0.06em; color: #666; margin-bottom: 0.1em; }
.kv-strip .kv-val { font-family: 'Courier New', Courier, monospace;
  font-weight: bold; font-size: 1.05em; }

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
.num { font-family: 'Courier New', Courier, monospace; }

/* ── Realism checks ── */
.ck-pass   { color: #059669; font-weight: bold; }
.ck-fail   { color: #dc2626; font-weight: bold; }
.ck-warn   { color: #d97706; }
.ck-na     { color: #9ca3af; }
.ck-tight  { background: #fef3c7; }

/* ── Reviewer ── */
.reviewer-block {
  border-left: 3px solid #ccc; padding: 0.4em 0.8em; margin: 0.5em 0;
}
.reviewer-block.approved { border-color: #059669; }
.reviewer-block.rejected { border-color: #dc2626; }
.reviewer-name { font-weight: bold; font-size: 0.95em; }
.reviewer-verdict { font-size: 0.82em; font-style: italic; margin-left: 0.5em; }
.reviewer-block ul { margin: 0.3em 0 0; padding-left: 1.3em; font-size: 0.88em; }
.reviewer-block li { margin-bottom: 0.2em; }

/* ── Sweep sparkline ── */
.sweep-svg { display: block; width: 100%; max-width: 680px;
             border: 1px solid #ddd; border-radius: 4px; margin: 0.4em 0; }

/* ── Waveform ── */
.wf-svg { display: block; width: 100%; max-width: 680px;
          background: #06100f; border-radius: 6px;
          border: 1px solid rgba(60,224,200,.3); margin: 0.4em 0; }

/* ── Diagnostics ── */
.diag-row { background: #fff5f5; border-left: 3px solid #dc2626;
            padding: 0.3em 0.6em; margin: 0.2em 0; font-size: 0.87em; }
"""


def _e(text: Any) -> str:
    return html.escape(str(text))


def _fmt_hz(hz: float) -> str:
    if hz >= 1e6:
        return f"{hz / 1e6:.3g} MHz"
    if hz >= 1e3:
        return f"{hz / 1e3:.3g} kHz"
    return f"{hz:.0f} Hz"


def _fmt_h(h_val: float) -> str:
    if h_val >= 1e-3:
        return f"{h_val * 1e3:.3g} mH"
    if h_val >= 1e-6:
        return f"{h_val * 1e6:.3g} µH"
    return f"{h_val * 1e9:.3g} nH"


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


def _sweep_svg(sweep: Any, *, fsw_star_hz: float, w: int = 620, h: int = 100) -> str:
    """Small loss-vs-fsw sparkline. Marks the chosen fsw* with a tick."""
    curve = getattr(sweep, "loss_curve", None)
    if not curve:
        return ""
    xs = [p.fsw_hz for p in curve]
    ys_raw = [p.total_loss_w for p in curve]
    feasible = [p.feasible for p in curve]
    if not any(y is not None for y in ys_raw):
        return ""
    ys = [y if y is not None else float("nan") for y in ys_raw]
    pad = 22
    x0, x1 = min(xs), max(xs)
    y_vals = [y for y in ys if y == y]
    y0, y1 = (min(y_vals), max(y_vals)) if y_vals else (0.0, 1.0)
    xr = (x1 - x0) or 1.0
    yr = (y1 - y0) or 1.0

    def px(x: float) -> float:
        return pad + (x - x0) / xr * (w - 2 * pad)

    def py_fn(y: float) -> float:
        return h - pad - (y - y0) / yr * (h - 2 * pad)

    parts = [
        f"<svg class='sweep-svg' viewBox='0 0 {w} {h}' width='100%' "
        f"style='height:{h}px' xmlns='http://www.w3.org/2000/svg'>",
        f"<rect width='{w}' height='{h}' fill='#fafafa'/>",
    ]
    # Segments — green for feasible, grey for infeasible
    prev_x = prev_y = prev_ok = None
    for x, y, ok in zip(xs, ys, feasible):
        if y != y:
            prev_x = prev_y = prev_ok = None
            continue
        cx, cy = px(x), py_fn(y)
        if prev_x is not None:
            col = "#059669" if prev_ok else "#bbb"
            parts.append(f"<line x1='{prev_x:.1f}' y1='{prev_y:.1f}' "
                         f"x2='{cx:.1f}' y2='{cy:.1f}' stroke='{col}' stroke-width='1.6'/>")
        prev_x, prev_y, prev_ok = cx, cy, ok

    # Mark fsw*
    star_x = px(fsw_star_hz)
    parts.append(f"<line x1='{star_x:.1f}' y1='4' x2='{star_x:.1f}' y2='{h - 4}' "
                 f"stroke='#2563eb' stroke-width='1' stroke-dasharray='3,2'/>")
    # Axis labels
    parts.append(f"<text x='{pad}' y='{h - 5}' font-size='8' fill='#888' "
                 f"font-family='monospace'>{_fmt_hz(x0)}</text>")
    parts.append(f"<text x='{w - pad}' y='{h - 5}' font-size='8' fill='#888' "
                 f"font-family='monospace' text-anchor='end'>{_fmt_hz(x1)}</text>")
    if y_vals:
        parts.append(f"<text x='{pad}' y='14' font-size='8' fill='#888' "
                     f"font-family='monospace'>{min(y_vals):.2f} W</text>")
    parts.append("</svg>")
    return "".join(parts)


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


def render_html(design_or_outcome: Any) -> str:
    """Render a ``ConverterDesign`` or legacy ``DesignOutcome`` as HTML."""
    from heaviside.pipeline.converter_designer import ConverterDesign

    if isinstance(design_or_outcome, ConverterDesign):
        return _render_converter_design(design_or_outcome)
    return _render_legacy_outcome(design_or_outcome)


# ─────────────────────────────────────────────────────────────────────────────
# Full ConverterDesign report
# ─────────────────────────────────────────────────────────────────────────────

def _render_converter_design(d: Any) -> str:
    from heaviside.pipeline.converter_designer import ConverterDesign

    outcome = d.outcome
    sweep = d.sweep
    reconcile = d.reconcile
    review = d.review  # PanelResult | None

    verdict = d.verdict or "?"
    verdict_cls = "v-pass" if verdict == "pass" else "v-fail"

    lines = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>",
        f"<title>Converter Design: {_e(d.topology.replace('_', ' ').title())}</title>",
        f"<style>{_CSS}</style></head><body>",
    ]

    # ── Header ────────────────────────────────────────────────────────────────
    lines.append("<div class='rpt-header'>")
    lines.append("<div>")
    lines.append(f"<h1 class='rpt-title'>{_e(d.topology.replace('_', ' ').title())} Converter</h1>")
    lines.append(f"<p class='rpt-subtitle'>Automated converter design report</p>")
    lines.append("</div>")
    lines.append(f"<span class='verdict-badge {verdict_cls}'>{_e(verdict.upper())}</span>")
    lines.append("</div>")

    # ── Operating point ───────────────────────────────────────────────────────
    spec = _spec_from_outcome(outcome)
    op = ((spec.get("operatingPoints") or [{}])[0]) if spec else {}
    vouts = op.get("outputVoltages") or []
    iouts = op.get("outputCurrents") or []
    vin_nom = (spec.get("inputVoltage") or {}).get("nominal") if spec else None
    vin_min = (spec.get("inputVoltage") or {}).get("minimum") if spec else None
    vin_max = (spec.get("inputVoltage") or {}).get("maximum") if spec else None
    total_pout = sum(float(v) * float(i) for v, i in zip(vouts, iouts)) if vouts and iouts else None

    lines.append("<h2>Operating Point</h2><div class='kv-strip'>")
    if vin_nom is not None:
        vin_str = f"{vin_nom:g} V"
        if vin_min is not None and vin_max is not None:
            vin_str += f" ({vin_min:g}–{vin_max:g} V)"
        lines.append(f"<div class='kv'><span class='kv-label'>V_in</span>"
                     f"<span class='kv-val'>{_e(vin_str)}</span></div>")
    for i, (vout, iout) in enumerate(zip(vouts, iouts)):
        tag = f"V_out{'[' + str(i) + ']' if len(vouts) > 1 else ''}"
        lines.append(f"<div class='kv'><span class='kv-label'>{_e(tag)}</span>"
                     f"<span class='kv-val'>{float(vout):g} V / {float(iout):g} A</span></div>")
    if total_pout is not None:
        lines.append(f"<div class='kv'><span class='kv-label'>P_out</span>"
                     f"<span class='kv-val'>{total_pout:.1f} W</span></div>")
    lines.append(f"<div class='kv'><span class='kv-label'>f_sw*</span>"
                 f"<span class='kv-val'>{_fmt_hz(d.fsw_hz)}</span></div>")
    lines.append("</div>")

    # ── Frequency sweep ───────────────────────────────────────────────────────
    if sweep is not None:
        curve = getattr(sweep, "loss_curve", None)
        front = getattr(sweep, "front", [])
        if curve:
            lines.append("<h2>Frequency Sweep — Total Loss vs f<sub>sw</sub></h2>")
            lines.append(_sweep_svg(sweep, fsw_star_hz=d.fsw_hz))
            n_f = sum(1 for p in curve if p.feasible)
            lines.append(
                f"<p style='font-size:0.85em;color:#555'>"
                f"Swept {len(curve)} frequencies · {n_f} feasible · "
                f"optimum {_fmt_hz(d.fsw_hz)} "
                f"({front[0].total_loss_w:.2f} W total loss if available)"
                f"</p>"
            ) if front else None

    # ── Inductor ──────────────────────────────────────────────────────────────
    mag = getattr(getattr(outcome, "pick", None), "main_magnetic", None)
    if mag is not None:
        core = mag.mas.get("magnetic", {}).get("core", {})
        coil = mag.mas.get("magnetic", {}).get("coil", {})
        core_name = core.get("name", "?")
        shape = (core.get("functionalDescription") or {}).get("shape", "?")
        material = (core.get("functionalDescription") or {}).get("material", "?")
        windings = coil.get("functionalDescription", [])
        turns = windings[0].get("numberTurns") if windings else None

        lines.append("<h2>Inductor</h2><div class='kv-strip'>")
        lines.append(f"<div class='kv'><span class='kv-label'>Core</span>"
                     f"<span class='kv-val'>{_e(core_name)}</span></div>")
        if shape and shape != "?":
            lines.append(f"<div class='kv'><span class='kv-label'>Shape</span>"
                         f"<span class='kv-val'>{_e(shape)}</span></div>")
        if material and material != "?":
            lines.append(f"<div class='kv'><span class='kv-label'>Material</span>"
                         f"<span class='kv-val'>{_e(material)}</span></div>")
        if turns is not None:
            lines.append(f"<div class='kv'><span class='kv-label'>Turns</span>"
                         f"<span class='kv-val'>{_e(turns)}</span></div>")

        # L value from the sweep candidate if available
        front = getattr(sweep, "front", []) if sweep else []
        if front:
            l_h = front[0].inductance_h
            lines.append(f"<div class='kv'><span class='kv-label'>L</span>"
                         f"<span class='kv-val'>{_fmt_h(l_h)}</span></div>")
            lines.append(f"<div class='kv'><span class='kv-label'>I_sat</span>"
                         f"<span class='kv-val'>{front[0].isat_a:.2f} A</span></div>")
            lines.append(f"<div class='kv'><span class='kv-label'>I_peak (worst)</span>"
                         f"<span class='kv-val'>{front[0].ipeak_worst_a:.2f} A</span></div>")
            lines.append(f"<div class='kv'><span class='kv-label'>Loss</span>"
                         f"<span class='kv-val'>{front[0].magnetic_loss_w:.2f} W</span></div>")
        lines.append("</div>")

    # ── Cross-OP reconciliation ───────────────────────────────────────────────
    if reconcile is not None:
        per_op = getattr(reconcile, "per_op", [])
        if per_op:
            lines.append("<h2>Operating-Point Margins</h2>")
            lines.append("<table><tr><th>OP</th><th>I_sat / I_peak</th>"
                         "<th>Sat margin</th><th>Thermal margin</th></tr>")
            for m in per_op:
                sat_ok = "PASS" if m.sat_feasible else "FAIL"
                sat_cls = "ck-pass" if m.sat_feasible else "ck-fail"
                thr_str = thrc = ""
                if m.thermal_ratio is not None:
                    thr_ok = m.thermal_feasible is not False
                    thrc = "ck-pass" if thr_ok else "ck-fail"
                    thr_str = f"<span class='{thrc}'>{m.thermal_ratio:.2f}×</span>"
                label = getattr(m, "label", "") or f"OP{m.op_index}"
                lines.append(
                    f"<tr><td>{_e(label)}</td>"
                    f"<td class='num'>{m.isat_ratio:.2f}×</td>"
                    f"<td><span class='{sat_cls}'>{sat_ok}</span></td>"
                    f"<td>{thr_str or '—'}</td></tr>"
                )
            lines.append("</table>")

    # ── Realism checks ────────────────────────────────────────────────────────
    lines += _realism_section(outcome)

    # ── BOM ───────────────────────────────────────────────────────────────────
    lines += _bom_section(d.bom, outcome)

    # ── Reviewer panel ────────────────────────────────────────────────────────
    if review is not None:
        lines.append("<h2>Design Review</h2>")
        decision = getattr(review, "decision", "?")
        d_cls = "v-pass" if getattr(review, "approved", False) else "v-fail"
        lines.append(f"<p><span class='verdict-badge {d_cls}'>{_e(decision)}</span></p>")
        for verd in getattr(review, "verdicts", []):
            block_cls = "approved" if verd.verdict == "APPROVED" else "rejected"
            lines.append(f"<div class='reviewer-block {block_cls}'>")
            lines.append(f"<span class='reviewer-name'>{_e(verd.reviewer)}</span>"
                         f"<span class='reviewer-verdict'>{_e(verd.verdict)}</span>")
            objs = verd.objections or []
            warns = (verd.raw or {}).get("warnings") or []
            if objs:
                lines.append("<ul>")
                for obj in objs:
                    lines.append(f"<li>{_e(str(obj))}</li>")
                lines.append("</ul>")
            if warns:
                lines.append("<ul style='color:#92400e'>")
                for w in warns:
                    lines.append(f"<li>{_e(str(w))}</li>")
                lines.append("</ul>")
            lines.append("</div>")
    elif hasattr(getattr(outcome, "gatekeeper", None), "approved"):
        # Legacy gatekeeper fallback
        lines += _gatekeeper_section(outcome.gatekeeper)

    # ── Waveforms ─────────────────────────────────────────────────────────────
    wfs = getattr(d, "waveforms", []) or []
    if not wfs and mag is not None:
        try:
            from heaviside.pipeline.converter_designer import magnetic_waveforms
            wfs = magnetic_waveforms(mag.mas, max_points=300)
        except Exception:
            pass
    if wfs:
        lines.append("<h2>Simulation Waveforms (primary winding)</h2>")
        lines.append(_waveform_svg(wfs))

    # ── Diagnostics ───────────────────────────────────────────────────────────
    if getattr(outcome, "diagnostics", None):
        lines.append("<h2>Diagnostics</h2>")
        for diag in outcome.diagnostics:
            lines.append(f"<div class='diag-row'>{_e(diag)}</div>")

    lines.append("</body></html>")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Legacy DesignOutcome report (old full_design pipeline)
# ─────────────────────────────────────────────────────────────────────────────

def _render_legacy_outcome(outcome: Any) -> str:
    topo_name = getattr(getattr(outcome, "pick", None),
                        "topology", None)
    topo_str = getattr(topo_name, "name", None) or "?"

    lines = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>",
        f"<title>Converter Design: {_e(topo_str.replace('_', ' ').title())}</title>",
        f"<style>{_CSS}</style></head><body>",
    ]

    verdict = (outcome.verdict_dict or {}).get("verdict", "?") if outcome.verdict_dict else "?"
    verdict_cls = "v-pass" if verdict == "pass" else "v-fail"

    lines.append("<div class='rpt-header'>")
    lines.append("<div>")
    lines.append(f"<h1 class='rpt-title'>{_e(topo_str.replace('_', ' ').title())} Converter</h1>")
    lines.append(f"<p class='rpt-subtitle'>Automated converter design report</p>")
    lines.append("</div>")
    lines.append(f"<span class='verdict-badge {verdict_cls}'>{_e(verdict.upper())}</span>")
    lines.append("</div>")

    # Inductor summary
    mag = getattr(getattr(outcome, "pick", None), "main_magnetic", None)
    if mag is not None:
        core_name = (mag.mas.get("magnetic", {}).get("core", {})
                     .get("name", "?"))
        coil = mag.mas.get("magnetic", {}).get("coil", {})
        windings = coil.get("functionalDescription", [])
        lines.append("<h2>Inductor</h2><div class='kv-strip'>")
        lines.append(f"<div class='kv'><span class='kv-label'>Core</span>"
                     f"<span class='kv-val'>{_e(core_name)}</span></div>")
        if windings:
            turns = windings[0].get("numberTurns")
            if turns is not None:
                lines.append(f"<div class='kv'><span class='kv-label'>Turns</span>"
                             f"<span class='kv-val'>{_e(turns)}</span></div>")
        lines.append(f"<div class='kv'><span class='kv-label'>Loss score</span>"
                     f"<span class='kv-val'>{mag.scoring:.4f}</span></div>")
        lines.append("</div>")

        svg = _waveform_svg(_try_waveforms(mag.mas))
        if svg:
            lines.append("<h2>Simulation Waveforms (primary winding)</h2>")
            lines.append(svg)

    lines += _realism_section(outcome)
    lines += _bom_section([], outcome)

    if getattr(outcome, "gatekeeper", None) is not None:
        lines += _gatekeeper_section(outcome.gatekeeper)

    if getattr(outcome, "diagnostics", None):
        lines.append("<h2>Diagnostics</h2>")
        for diag in outcome.diagnostics:
            lines.append(f"<div class='diag-row'>{_e(diag)}</div>")

    lines.append("</body></html>")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Shared section renderers
# ─────────────────────────────────────────────────────────────────────────────

def _realism_section(outcome: Any) -> list[str]:
    vd = getattr(outcome, "verdict_dict", None)
    if not isinstance(vd, Mapping):
        return []
    verdict = vd.get("verdict", "?")
    s = vd.get("summary", {})
    verdict_cls = "v-pass" if verdict == "pass" else "v-fail"
    lines = ["<h2>Realism Checks</h2>"]
    lines.append(f"<p><span class='verdict-badge {verdict_cls}'>{_e(verdict.upper())}</span>"
                 f"&emsp;<span style='font-size:0.88em;color:#555'>"
                 f"pass={s.get('pass',0)} · fail={s.get('fail',0)} · "
                 f"unavailable={s.get('unavailable',0)} · n/a={s.get('not_applicable',0)}"
                 f"</span></p>")
    checks = vd.get("checks", [])
    if checks:
        lines.append("<table><tr><th>Check</th><th>Status</th><th>Value</th><th>Margin</th></tr>")
        for c in checks:
            status = c.get("status", "?")
            cls_map = {
                "pass": "ck-pass", "fail": "ck-fail",
                "unavailable": "ck-na", "not_applicable": "ck-na", "warn": "ck-warn",
            }
            css_cls = cls_map.get(status.replace(" ", "_"), "ck-na")
            val = c.get("value")
            margin = c.get("margin")
            tight = " class='ck-tight'" if (
                status == "pass" and isinstance(margin, (int, float)) and margin < 0.3
            ) else ""
            lines.append(
                f"<tr{tight}><td>{_e(c.get('name', '?'))}</td>"
                f"<td><span class='{css_cls}'>{_e(status.upper())}</span></td>"
                f"<td class='num'>{_e(f'{val:.4g}' if isinstance(val, float) else val or '')}</td>"
                f"<td class='num'>{_e(f'{margin:.4g}' if isinstance(margin, float) else margin or '')}</td></tr>"
            )
        lines.append("</table>")
    return lines


def _bom_section(bom_rows: list[dict[str, Any]], outcome: Any) -> list[str]:
    """BOM from ConverterDesign.bom (preferred) or extracted from TAS."""
    rows = bom_rows
    if not rows:
        rows = _extract_bom_from_outcome(outcome)
    if not rows:
        return []
    lines = ["<h2>Bill of Materials</h2>"]
    lines.append("<table><tr><th>Ref</th><th>Category</th>"
                 "<th>MPN</th><th>Manufacturer</th></tr>")
    for r in rows:
        lines.append(
            f"<tr><td>{_e(r.get('ref') or r.get('category') or '?')}</td>"
            f"<td>{_e(r.get('category') or '?')}</td>"
            f"<td class='mpn'>{_e(r.get('mpn') or '?')}</td>"
            f"<td>{_e(r.get('manufacturer') or '?')}</td></tr>"
        )
    lines.append("</table>")
    return lines


def _gatekeeper_section(gk: Any) -> list[str]:
    status = "APPROVED" if gk.approved else "BLOCKED"
    cls = "approved" if gk.approved else "rejected"
    lines = [f"<h2>Gatekeeper</h2>",
             f"<div class='reviewer-block {cls}'>",
             f"<span class='reviewer-name'>Gatekeeper: "
             f"<span class='reviewer-verdict'>{status}</span></span>"]
    objs = getattr(gk, "objections", []) or []
    warns = getattr(gk, "warnings", []) or []
    if objs:
        lines.append("<ul>")
        for o in objs:
            lines.append(f"<li>{_e(str(o))}</li>")
        lines.append("</ul>")
    if warns:
        lines.append("<ul style='color:#92400e'>")
        for w in warns:
            lines.append(f"<li>{_e(str(w))}</li>")
        lines.append("</ul>")
    lines.append("</div>")
    return lines


def _spec_from_outcome(outcome: Any) -> dict[str, Any] | None:
    tas = getattr(outcome, "tas", None)
    if not isinstance(tas, Mapping):
        return None
    return tas.get("spec") or tas.get("inputSpec") or None


def _extract_bom_from_outcome(outcome: Any) -> list[dict[str, Any]]:
    tas = getattr(outcome, "tas", None)
    if not isinstance(tas, Mapping):
        return []
    rows: list[dict[str, Any]] = []
    stages = (tas.get("topology") or {}).get("stages") or []
    seen: set[str] = set()
    for stage in stages:
        comps = (stage.get("circuit") or {}).get("components") or []
        for c in comps:
            if not isinstance(c, Mapping):
                continue
            prov = c.get("selection_provenance") or {}
            mpn = c.get("mpn") or prov.get("mpn")
            ref = str(c.get("name") or mpn or "?")
            if ref in seen or not mpn:
                continue
            seen.add(ref)
            rows.append({
                "ref": ref,
                "mpn": mpn,
                "manufacturer": prov.get("manufacturer") or c.get("manufacturer"),
                "category": prov.get("category") or c.get("category"),
            })
    return rows


def _try_waveforms(mas: Any) -> list[dict[str, Any]]:
    try:
        from heaviside.pipeline.converter_designer import magnetic_waveforms
        return magnetic_waveforms(mas, max_points=300)
    except Exception:
        return []
