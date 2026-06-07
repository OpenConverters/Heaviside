"""Render a DesignOutcome as a standalone HTML report.

Produces a single self-contained HTML file with embedded CSS (no
external dependencies). Suitable for email, archival, or opening
in a browser.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from typing import Any

_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       max-width: 900px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }
h1 { border-bottom: 2px solid #2563eb; padding-bottom: 0.5rem; }
h2 { color: #2563eb; margin-top: 2rem; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
th, td { border: 1px solid #d1d5db; padding: 0.5rem 0.75rem; text-align: left; }
th { background: #f3f4f6; font-weight: 600; }
.pass { color: #059669; font-weight: 600; }
.fail { color: #dc2626; font-weight: 600; }
.unavailable { color: #9ca3af; }
.not_applicable { color: #6b7280; }
.warn { color: #d97706; }
.approved { color: #059669; font-weight: 700; font-size: 1.1em; }
.blocked { color: #dc2626; font-weight: 700; font-size: 1.1em; }
.bom-table td:first-child { font-weight: 600; text-transform: uppercase; }
.margin-tight { background: #fef3c7; }
.diagnostic { background: #fef2f2; padding: 0.5rem; border-left: 3px solid #dc2626; margin: 0.25rem 0; }
"""


def _e(text: Any) -> str:
    return html.escape(str(text))


def render_html(outcome: Any) -> str:
    """Render a ``DesignOutcome`` as standalone HTML."""
    lines = [
        "<!DOCTYPE html>",
        "<html lang='en'><head><meta charset='utf-8'>",
        f"<title>Design Report: {_e(outcome.pick.topology.name)}</title>",
        f"<style>{_CSS}</style>",
        "</head><body>",
        f"<h1>Design Report: {_e(outcome.pick.topology.name)}</h1>",
    ]

    # Magnetic section
    mag = outcome.pick.main_magnetic
    core = mag.mas.get("magnetic", {}).get("core", {})
    core_name = core.get("name", "?")
    coil = mag.mas.get("magnetic", {}).get("coil", {})
    windings = coil.get("functionalDescription", [])

    lines.append("<h2>Magnetic</h2>")
    lines.append(f"<p><strong>Core:</strong> {_e(core_name)}</p>")
    if windings:
        lines.append("<table><tr><th>Winding</th><th>Turns</th></tr>")
        for w in windings:
            lines.append(
                f"<tr><td>{_e(w.get('name', '?'))}</td>"
                f"<td>{_e(w.get('numberTurns', '?'))}</td></tr>"
            )
        lines.append("</table>")
    lines.append(f"<p><strong>Scoring (total losses):</strong> {mag.scoring:.4f}</p>")

    # BOM section
    if outcome.tas:
        bom = _extract_bom(outcome.tas)
        if bom:
            lines.append("<h2>BOM (Selected Components)</h2>")
            lines.append(
                "<table class='bom-table'>"
                "<tr><th>Type</th><th>MPN</th><th>Manufacturer</th>"
                "<th>Tiebreaker</th><th>Alternatives</th><th>Key Margins</th></tr>"
            )
            for b in bom:
                margins = b.get("margins", {})
                margin_str = ", ".join(
                    f"{k}={v:.2f}"
                    for k, v in margins.items()
                    if isinstance(v, (int, float)) and v != float("inf")
                )
                lines.append(
                    f"<tr><td>{_e(b.get('category', '?'))}</td>"
                    f"<td>{_e(b.get('mpn', '?'))}</td>"
                    f"<td>{_e(b.get('manufacturer', '?'))}</td>"
                    f"<td>{_e(b.get('tiebreaker', '?'))}</td>"
                    f"<td>{b.get('alternatives_considered', 0)}</td>"
                    f"<td>{_e(margin_str)}</td></tr>"
                )
            lines.append("</table>")

    # Realism gate
    if outcome.verdict_dict:
        v = outcome.verdict_dict
        verdict = v.get("verdict", "?")
        css = "pass" if verdict == "pass" else "fail"
        s = v.get("summary", {})
        lines.append(f"<h2>Realism Gate: <span class='{css}'>{_e(verdict.upper())}</span></h2>")
        lines.append(
            f"<p>pass={s.get('pass', 0)} fail={s.get('fail', 0)} "
            f"unavailable={s.get('unavailable', 0)} n/a={s.get('not_applicable', 0)}</p>"
        )
        lines.append("<table><tr><th>Check</th><th>Status</th><th>Value</th><th>Margin</th></tr>")
        for c in v.get("checks", []):
            status = c.get("status", "?")
            css_cls = status.replace(" ", "_")
            val = c.get("value")
            margin = c.get("margin")
            tight = ""
            if status == "pass" and isinstance(margin, (int, float)) and margin < 0.3:
                tight = " class='margin-tight'"
            lines.append(
                f"<tr{tight}><td>{_e(c.get('name', '?'))}</td>"
                f"<td class='{css_cls}'>{_e(status.upper())}</td>"
                f"<td>{_e(f'{val:.4f}' if isinstance(val, float) else val or '')}</td>"
                f"<td>{_e(f'{margin:.4f}' if isinstance(margin, float) else margin or '')}</td></tr>"
            )
        lines.append("</table>")

    # Gatekeeper
    if outcome.gatekeeper:
        gk = outcome.gatekeeper
        status = "APPROVED" if gk.approved else "BLOCKED"
        css = "approved" if gk.approved else "blocked"
        lines.append(f"<h2>Gatekeeper Review: <span class='{css}'>{status}</span></h2>")
        if gk.objections:
            lines.append("<h3>Objections</h3><ul>")
            for obj in gk.objections:
                lines.append(f"<li class='fail'>{_e(obj)}</li>")
            lines.append("</ul>")
        if gk.warnings:
            lines.append("<h3>Warnings</h3><ul>")
            for w in gk.warnings:
                lines.append(f"<li class='warn'>{_e(w)}</li>")
            lines.append("</ul>")

    # Diagnostics
    if outcome.diagnostics:
        lines.append("<h2>Diagnostics</h2>")
        for d in outcome.diagnostics:
            lines.append(f"<div class='diagnostic'>{_e(d)}</div>")

    lines.append("</body></html>")
    return "\n".join(lines)


def _extract_bom(tas: Mapping[str, Any]) -> list[dict[str, Any]]:
    bom = []
    for stage in tas.get("topology", {}).get("stages", []):
        for comp in stage.get("circuit", {}).get("components", []):
            if not isinstance(comp, Mapping):
                continue
            prov = comp.get("selection_provenance")
            if isinstance(prov, dict):
                bom.append(prov)
    return bom
