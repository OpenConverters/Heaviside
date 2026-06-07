"""Render a CrossRefOutcome as a standalone HTML report."""

from __future__ import annotations

import html
import re
from collections import Counter
from typing import Any

_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       max-width: 1000px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }
h1 { border-bottom: 2px solid #c8102e; padding-bottom: 0.5rem; }
h2 { color: #c8102e; margin-top: 2rem; }
h3 { color: #333; margin-top: 1.5rem; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: 0.9rem; }
th, td { border: 1px solid #d1d5db; padding: 0.4rem 0.6rem; text-align: left; }
th { background: #f3f4f6; font-weight: 600; }
.exact { color: #059669; font-weight: 600; }
.replaced { color: #2563eb; font-weight: 600; }
.partial { color: #d97706; font-weight: 600; }
.no-sub { color: #9ca3af; }
.keep { color: #6b7280; }
.summary-box { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px;
               padding: 1rem 1.5rem; margin: 1rem 0; }
.coverage { font-size: 1.3rem; font-weight: 700; color: #c8102e; }
.guardrail { background: #fef3c7; padding: 0.3rem 0.6rem; border-left: 3px solid #d97706;
             margin: 0.2rem 0; font-size: 0.85rem; }
.diagnostic { background: #fef2f2; padding: 0.3rem 0.6rem; border-left: 3px solid #dc2626;
              margin: 0.2rem 0; font-size: 0.85rem; }
.mpn { font-family: 'SF Mono', SFMono-Regular, Consolas, monospace; font-size: 0.85rem; }
.footer { color: #9ca3af; font-size: 0.8rem; margin-top: 3rem; border-top: 1px solid #e5e7eb;
          padding-top: 0.5rem; }
@media print { body { margin: 0; } .no-print { display: none; } }
"""

_CAT_ORDER = ["capacitor", "resistor", "magnetic", "mosfet", "diode", "ic"]
_CAT_LABELS = {
    "capacitor": "Capacitors",
    "resistor": "Resistors",
    "magnetic": "Magnetics",
    "mosfet": "MOSFETs",
    "diode": "Diodes",
    "ic": "ICs",
}
_STATUS_LABELS = {
    "exact": ("EXACT", "exact"),
    "recommended": ("REPLACED", "replaced"),
    "partial": ("PARTIAL", "partial"),
    "no_substitute": ("NOT REPLACED", "no-sub"),
    "keep_original": ("KEEP", "keep"),
}


def _e(text: Any) -> str:
    return html.escape(str(text))


def _sort_key(ref_des: str) -> tuple[str, int]:
    m = re.match(r"([A-Z]+)(\d+)", ref_des)
    return (m.group(1), int(m.group(2))) if m else (ref_des, 0)


def render_crossref_html(
    outcome: dict[str, Any],
    *,
    title: str = "",
    circuit_context: str = "",
) -> str:
    """Render a crossref outcome dict as standalone HTML."""
    comps = sorted(outcome.get("components", []), key=lambda c: _sort_key(c["ref_des"]))
    target = outcome.get("target_manufacturer", "?")

    total = len(comps)
    status_counts = Counter(c["status"] for c in comps)
    replaced = status_counts.get("recommended", 0) + status_counts.get("partial", 0)
    exact = status_counts.get("exact", 0)
    coverage = exact + replaced
    no_sub = status_counts.get("no_substitute", 0)
    keep = status_counts.get("keep_original", 0)

    cat_stats: dict[str, dict[str, int]] = {}
    for c in comps:
        cat = c.get("component_type", "other")
        if cat not in cat_stats:
            cat_stats[cat] = {"total": 0, "exact": 0, "replaced": 0, "no_sub": 0, "keep": 0}
        cat_stats[cat]["total"] += 1
        if c["status"] == "exact":
            cat_stats[cat]["exact"] += 1
        elif c["status"] in ("recommended", "partial"):
            cat_stats[cat]["replaced"] += 1
        elif c["status"] == "no_substitute":
            cat_stats[cat]["no_sub"] += 1
        else:
            cat_stats[cat]["keep"] += 1

    parts: list[str] = []
    parts.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
    parts.append(f"<title>{_e(title or f'{target} Cross-Reference Report')}</title>")
    parts.append(f"<style>{_CSS}</style></head><body>")

    # Header
    parts.append(f"<h1>{_e(target)} Cross-Reference Report</h1>")
    if title:
        parts.append(f"<h2>{_e(title)}</h2>")
    if circuit_context:
        parts.append(f"<p><em>{_e(circuit_context)}</em></p>")

    # Summary box
    parts.append('<div class="summary-box">')
    parts.append(
        f'<p class="coverage">{_e(target)} coverage: {coverage} / {total} = {100 * coverage / total:.0f}%</p>'
    )
    parts.append(
        f"<p>Already {_e(target)}: {exact} · Newly replaced: {replaced} · "
        f"Not replaced: {no_sub} · Keep/NC: {keep}</p>"
    )
    parts.append("</div>")

    # Summary table
    parts.append("<h2>1. Outcome Summary</h2>")
    parts.append(
        "<table><tr><th>Category</th><th>Fitted</th><th>Already Würth</th>"
        "<th>Newly Replaced</th><th>Not Replaced</th></tr>"
    )
    for cat in _CAT_ORDER:
        if cat not in cat_stats:
            continue
        s = cat_stats[cat]
        label = _CAT_LABELS.get(cat, cat.title())
        parts.append(
            f"<tr><td>{_e(label)}</td><td>{s['total']}</td><td>{s['exact']}</td>"
            f"<td>{s['replaced']}</td><td>{s['no_sub'] + s['keep']}</td></tr>"
        )
    parts.append(
        f"<tr><td><strong>Total</strong></td><td><strong>{total}</strong></td>"
        f"<td><strong>{exact}</strong></td><td><strong>{replaced}</strong></td>"
        f"<td><strong>{no_sub + keep}</strong></td></tr>"
    )
    parts.append("</table>")

    # Per-category tables
    parts.append("<h2>2. Crossing Table</h2>")
    for cat in _CAT_ORDER:
        cat_comps = [c for c in comps if c.get("component_type") == cat]
        if not cat_comps:
            continue
        label = _CAT_LABELS.get(cat, cat.title())
        parts.append(f"<h3>{_e(label)}</h3>")
        parts.append("<table>")
        parts.append(
            "<tr><th>Ref</th><th>Original MPN</th><th>Würth PN</th>"
            "<th>Status</th><th>Notes</th></tr>"
        )
        for c in cat_comps:
            sl, sc = _STATUS_LABELS.get(c["status"], (c["status"], ""))
            sub = c.get("substitute_mpn") or "—"
            orig = c.get("original_mpn") or "—"
            notes = _e(c.get("notes", "") or "")
            parts.append(
                f"<tr><td>{_e(c['ref_des'])}</td>"
                f'<td class="mpn">{_e(orig)}</td>'
                f'<td class="mpn"><strong>{_e(sub)}</strong></td>'
                f'<td class="{sc}">{_e(sl)}</td>'
                f"<td>{notes}</td></tr>"
            )
        parts.append("</table>")

    # Guardrails
    guardrails = outcome.get("guardrail_log", [])
    if guardrails:
        parts.append("<h2>3. Guardrail Fires</h2>")
        for g in guardrails:
            parts.append(
                f'<div class="guardrail">G{_e(g.get("guardrail_id", "?"))} '
                f"{_e(g.get('ref_des', '?'))}: {_e(g.get('reason', ''))}</div>"
            )

    # Otto challenge log
    otto = outcome.get("otto_log", {})
    challenges = otto.get("challenges", [])
    if challenges:
        parts.append("<h2>4. Otto Challenge Log</h2>")
        parts.append(
            "<p><em>Otto is a Würth Elektronik sales agent that challenges every "
            "no_substitute verdict. Proposals are verified against Digi-Key before acceptance.</em></p>"
        )
        parts.append(
            "<table><tr><th>Ref</th><th>Verdict</th><th>Diagnosis</th>"
            "<th>Counter-Proposal</th><th>Verified?</th></tr>"
        )
        verified_refs = {v["ref_des"] for v in otto.get("verified", [])}
        rejected_refs = {r["ref_des"] for r in otto.get("rejected", [])}
        for ch in challenges:
            ref = ch.get("ref_des", "?")
            verdict = ch.get("verdict", "?")
            diag = _e(ch.get("diagnosis", ""))
            proposal = _e(ch.get("counter_proposal") or "—")
            vc = "exact" if verdict == "OVERTURNED" else "no-sub"
            if ref in verified_refs:
                check = '<span class="exact">Verified</span>'
            elif ref in rejected_refs:
                check = '<span class="partial">Rejected</span>'
            elif verdict == "CONFIRMED":
                check = '<span class="no-sub">N/A</span>'
            else:
                check = "—"
            parts.append(
                f'<tr><td>{_e(ref)}</td><td class="{vc}">{_e(verdict)}</td>'
                f"<td>{diag}</td><td>{proposal}</td><td>{check}</td></tr>"
            )
        parts.append("</table>")
        summary = otto.get("summary", {})
        if summary:
            parts.append(
                f"<p>Otto summary: {_e(summary.get('overturned', 0))} overturned, "
                f"{_e(summary.get('confirmed', 0))} confirmed out of "
                f"{_e(summary.get('total_challenged', 0))} challenged.</p>"
            )

    # Reviewer verdict
    verdicts = outcome.get("review_verdicts", [])
    reviewer_log = outcome.get("reviewer_log", "")
    if verdicts or reviewer_log:
        parts.append("<h2>5. Reviewer Report (Nicola — Quality Mode)</h2>")
        for v in verdicts:
            vc = v.get("verdict", "?").upper()
            css = "exact" if vc == "APPROVED" else ("partial" if vc == "PROCEED" else "no-sub")
            parts.append(
                f'<p>Verdict: <span class="{css}" style="font-size:1.2em">{_e(vc)}</span></p>'
            )
            summary_text = v.get("summary", "")
            if summary_text:
                parts.append(f"<p>{_e(summary_text)}</p>")
            objections = v.get("objections", [])
            if objections:
                parts.append("<h3>Objections</h3><ul>")
                for obj in objections:
                    parts.append(f"<li>{_e(obj)}</li>")
                parts.append("</ul>")
            warnings = v.get("warnings", [])
            if warnings:
                parts.append("<h3>Warnings</h3><ul>")
                for w in warnings:
                    parts.append(f'<li class="warn">{_e(w)}</li>')
                parts.append("</ul>")

    # Diagnostics
    diags = outcome.get("diagnostics", [])
    if diags:
        parts.append("<h2>6. Diagnostics</h2>")
        for d in diags:
            parts.append(f'<div class="diagnostic">{_e(d)}</div>')

    parts.append('<div class="footer">Generated by Heaviside CR Pipeline</div>')
    parts.append("</body></html>")
    return "\n".join(parts)


__all__ = ["render_crossref_html"]
