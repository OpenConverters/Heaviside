"""Render a CrossRefOutcome as a standalone HTML report (Proteus-style)."""

from __future__ import annotations

import html
import re
from collections import Counter
from typing import Any

_CSS = """
@page { size: A4; margin: 22mm 20mm; }
body { font-family: Georgia, 'Times New Roman', serif; font-size: 10.5pt;
       max-width: 820px; margin: 2rem auto; padding: 0 1.5rem; color: #111; line-height: 1.45; }
h1 { font-size: 1.45em; font-weight: bold; margin: 0 0 0.2em; border: none; }
h2 { font-size: 1.05em; font-weight: bold; margin: 1.8em 0 0.5em; }
h3 { font-size: 0.97em; font-weight: bold; font-style: italic; margin: 1.4em 0 0.35em; }
.subtitle { font-size: 1.1em; font-weight: bold; margin: 0.15em 0; }
.context  { font-style: italic; margin: 0.2em 0 0.4em; }
.intro-meta { font-size: 0.92em; }
hr.sep { width: 55%; margin: 1.4em auto; border: none; border-top: 1px solid #111; }
/* Tables */
table { border-collapse: collapse; width: 100%; margin: 0.7em 0; font-size: 0.88em;
        table-layout: fixed; }
th { border-top: 1.5px solid #111; border-bottom: 1px solid #111;
     padding: 0.28em 0.5em; text-align: left; font-weight: normal;
     background: none; font-style: italic; }
td { padding: 0.28em 0.5em; vertical-align: top;
     overflow-wrap: break-word; word-break: break-word; }
td.no-wrap { white-space: nowrap; }
tr.data-row td { border-bottom: 1px solid #d8d8d8; }
tr.last-row td { border-bottom: 1.5px solid #111; }
tr.total-row td { border-top: 1.5px solid #111; border-bottom: 1.5px solid #111;
                  font-weight: bold; }
/* Summary table */
.summary-tbl { width: auto; min-width: 55%; }
.summary-tbl td:first-child { width: 22em; }
/* Substitution table column widths */
.sub-tbl col.c-ref  { width: 7%;  }
.sub-tbl col.c-orig { width: 17%; }
.sub-tbl col.c-sub  { width: 17%; }
.sub-tbl col.c-st   { width: 20%; }
.sub-tbl col.c-conf { width: 9%;  }
.sub-tbl col.c-note { width: 30%; }
/* Engineering compare table */
.cmp-tbl col.c-param { width: 28%; }
.cmp-tbl col.c-orig2 { width: 33%; }
.cmp-tbl col.c-sub2  { width: 33%; }
.cmp-tbl col.c-verdict { width: 6%; }
.cmp-tbl td.verdict { text-align: center; font-weight: 700; }
.cmp-tbl td.v-fail { color: #c0392b; }
.cmp-tbl td.v-warn, .cmp-tbl td.v-lower, .cmp-tbl td.v-differs { color: #d68910; }
.cmp-tbl td.v-pass, .cmp-tbl td.v-same, .cmp-tbl td.v-exact, .cmp-tbl td.v-exceeds { color: #1e8449; }
.cmp-tbl td.v-unverified { color: #7f8c8d; }
/* Not-replaced table */
.nr-tbl col.c-ref2  { width: 10%; }
.nr-tbl col.c-orig2 { width: 30%; }
.nr-tbl col.c-why   { width: 60%; }
/* Monospace for PNs */
.mpn { font-family: 'Courier New', Courier, monospace; font-size: 0.84em; word-break: break-all; }
.conf-high   { font-weight: bold; }
.conf-caveat { font-weight: bold; font-style: italic; }
.conf-muted  { color: #666; }
/* Recommendations */
.rec-list { margin: 0.5em 0; padding-left: 1.5em; }
.rec-list li { margin-bottom: 0.4em; }
.rec-label { font-weight: bold; }
/* Coverage callout */
.coverage-line { font-style: italic; font-size: 0.88em; margin: 0.6em 0; color: #333; }
/* Footer */
.footer { color: #888; font-size: 0.78em; margin-top: 3em;
          border-top: 1px solid #ccc; padding-top: 0.5em; text-align: center; }
@media print { body { margin: 0; max-width: none; }
               .no-print { display: none; } }
"""

_CAT_ORDER = ["capacitor", "resistor", "magnetic", "mosfet", "diode", "ic"]


def _e(text: Any) -> str:
    return html.escape(str(text))


def _sort_key(ref_des: str) -> tuple[str, int]:
    m = re.match(r"([A-Z]+)(\d+)", ref_des)
    return (m.group(1), int(m.group(2))) if m else (ref_des, 0)


def _status_label(status: str, target: str) -> tuple[str, str]:
    """(display_text, css_class). ``exact`` covers both an identical part and a
    part kept as-is (already the target manufacturer) — the notes column carries
    the 'why'."""
    return {
        "exact": ("[OK] Exact", ""),
        "recommended": ("[OK] Recommended", ""),
        "partial": ("~ Partial", ""),
        "no_substitute": ("- No substitute", "conf-muted"),
    }.get(status, (status, ""))


def _confidence(comp: dict[str, Any]) -> tuple[str, str]:
    """(label, css_class) — confidence is driven by the DETERMINISTIC verdicts,
    never by the LLM's free-text prose. The FAE finding: an ``ORIGINAL_UNVERIFIED``
    row (matched on value/voltage/package only, its dielectric/temp/current
    unknown) still showed a bold "HIGH" badge because the LLM note said "exact
    match" — a badge contradicting its own guardrail."""
    status = comp.get("status", "")
    fires = comp.get("guardrail_fires") or []
    notes = (comp.get("notes") or "").lower()
    if status == "no_substitute":
        return "-", "conf-muted"
    if status == "exact":
        return "HIGH", "conf-high"
    # An unidentified original can never be HIGH — its specs weren't verified.
    if "ORIGINAL_UNVERIFIED" in fires:
        return "UNVERIFIED", "conf-caveat"
    md = comp.get("match_detail") or {}
    params = md.get("params", [])
    # Any unverified / failing / deviating parameter blocks a HIGH badge.
    not_good = {"unverified", "fail", "differs", "lower", "warn"}
    if any(str(p.get("verdict", "")).lower() in not_good for p in params):
        return "MED", ""
    # CAVEAT: package upsize/downsize or an explicit verify note.
    pkg_keywords = ("upsize", "downsize", "size class", "footprint", "verify", "caveat")
    if any(k in notes for k in pkg_keywords):
        return "CAVEAT", "conf-caveat"
    # HIGH only when EVERY deterministic parameter is good (no LLM-prose override).
    good = {"exact", "exceeds", "same", "pass"}
    if params and all(str(p.get("verdict", "")).lower() in good for p in params):
        return "HIGH", "conf-high"
    return "MED", ""


def _short_note(comp: dict[str, Any]) -> str:
    """Compact one-liner note from match_detail params + notes text."""
    md = comp.get("match_detail") or {}
    note = (comp.get("notes") or "").strip()
    bits: list[str] = []
    for p in md.get("params", []):
        o, s = p.get("original") or "", p.get("substitute") or ""
        p.get("verdict", "")
        name = p.get("name", "")
        if o and s and o != s:
            bits.append(f"{name}: {o}→{s}")
        elif o:
            bits.append(f"{name}: {o}")
    param_str = ", ".join(bits)
    # Trim note to first sentence to keep cells short
    first_sentence = re.split(r"(?<=[.!?])\s", note)[0] if note else ""
    if param_str and first_sentence and first_sentence not in param_str:
        return f"{first_sentence} ({param_str})" if len(first_sentence) < 60 else first_sentence
    return first_sentence or param_str or note[:80] or "—"


def _tr(cells: list[str], *, cls: str = "data-row") -> str:
    inner = "".join(f"<td>{c}</td>" for c in cells)
    return f'<tr class="{cls}">{inner}</tr>'


def _engineering_notes(comps: list[dict[str, Any]], target: str) -> str:
    """Generate per-notable-component engineering analysis paragraphs."""
    parts: list[str] = []
    for comp in comps:
        status = comp.get("status", "")
        if status == "no_substitute":
            continue
        md = comp.get("match_detail") or {}
        params = md.get("params", [])
        if not params:
            continue
        # Only include components with detailed params (value, voltage, package)
        ref = comp.get("ref_des", "?")
        orig = comp.get("original_mpn", "?")
        sub = comp.get("substitute_mpn", "?")
        note = (comp.get("notes") or "").strip()
        cat = comp.get("component_type", "")
        # Build compare table. The verdict marker makes a parameter that falls
        # outside the allowed margin (e.g. ESR/ripple/Isat) visible at a glance.
        _MARK = {
            "pass": "✓",
            "same": "✓",
            "exact": "✓",
            "exceeds": "✓",
            "warn": "⚠",
            "lower": "⚠",
            "differs": "⚠",
            "fail": "✗",
            "unverified": "?",
        }
        rows: list[str] = []
        for p in params:
            o = _e(p.get("original") or "—")
            s = _e(p.get("substitute") or "—")
            nm = _e(p.get("name") or "")
            verd = p.get("verdict", "")
            mark = _MARK.get(verd, "")
            title = _e(p.get("note") or "")
            rows.append(
                f'<tr class="data-row"><td>{nm}</td>'
                f'<td class="mpn">{o}</td><td class="mpn">{s}</td>'
                f'<td class="verdict v-{_e(verd)}" title="{title}">{mark}</td></tr>'
            )
        if not rows:
            continue
        cat_label = {
            "capacitor": "Capacitor",
            "resistor": "Resistor",
            "magnetic": "Inductor/Magnetic",
            "mosfet": "MOSFET",
            "diode": "Diode",
            "connector": "Connector",
            "analog": "Analog IC",
            "timeBase": "Crystal/Oscillator",
        }.get(cat, cat.title())
        parts.append(
            f'<h3>{_e(ref)} ({cat_label}) — <span class="mpn">{_e(orig)}</span>'
            f' → <span class="mpn">{_e(sub)}</span></h3>'
        )
        parts.append(
            '<table class="cmp-tbl"><colgroup>'
            '<col class="c-param"><col class="c-orig2"><col class="c-sub2">'
            '<col class="c-verdict">'
            "</colgroup>"
            f"<tr><th>Parameter</th><th>Original ({_e(orig)})</th>"
            f"<th>Substitute ({_e(sub)})</th><th></th></tr>"
        )
        parts.extend(rows)
        parts.append("</table>")
        if note:
            # Bold first sentence if it looks like a label
            first, *rest = re.split(r"(?<=[:.])\s", note, maxsplit=1)
            if rest and len(first) < 40 and first.endswith(":"):
                parts.append(f"<p><strong>{_e(first)}</strong> {_e(' '.join(rest))}</p>")
            else:
                parts.append(f"<p>{_e(note)}</p>")
    return "\n".join(parts)


def _recommendations(comps: list[dict[str, Any]]) -> list[str]:
    """Derive actionable recommendations from substitution patterns."""
    recs: list[str] = []
    # Package upsizes
    upsized = [
        c
        for c in comps
        if c.get("status") in ("recommended", "partial")
        and any(k in (c.get("notes") or "").lower() for k in ("upsize", "size class", "larger"))
    ]
    if upsized:
        refs = ", ".join(c["ref_des"] for c in upsized[:6])
        if len(upsized) > 6:
            refs += f" (+{len(upsized) - 6} more)"
        recs.append(
            f"<li><span class='rec-label'>Package upsizes ({refs}):</span> "
            "Verify PCB keepout clearances and component height against adjacent parts.</li>"
        )
    # Package downsizes
    downsized = [
        c
        for c in comps
        if c.get("status") in ("recommended", "partial")
        and "downsize" in (c.get("notes") or "").lower()
    ]
    if downsized:
        refs = ", ".join(c["ref_des"] for c in downsized[:4])
        recs.append(
            f"<li><span class='rec-label'>Package downsizes ({refs}):</span> "
            "Verify power dissipation and pad geometry before committing to smaller footprint.</li>"
        )
    # THT → SMD conversions
    tht = [
        c
        for c in comps
        if "tht" in (c.get("notes") or "").lower()
        or "through-hole" in (c.get("notes") or "").lower()
    ]
    if tht:
        refs = ", ".join(c["ref_des"] for c in tht[:4])
        recs.append(
            f"<li><span class='rec-label'>THT→SMD conversion ({refs}):</span> "
            "Verify mechanical requirements, vibration tolerance, and gate waveform integrity where applicable.</li>"
        )
    # No substitute components
    no_sub = [c for c in comps if c.get("status") == "no_substitute"]
    if no_sub:
        refs = ", ".join(c["ref_des"] for c in no_sub[:6])
        if len(no_sub) > 6:
            refs += f" (+{len(no_sub) - 6} more)"
        recs.append(
            f"<li><span class='rec-label'>Not replaced ({refs}):</span> "
            "Source from original manufacturer or request targeted catalogue search.</li>"
        )
    return recs


def render_crossref_html(
    outcome: dict[str, Any],
    *,
    title: str = "",
    circuit_context: str = "",
) -> str:
    """Render a crossref outcome dict as a Proteus-style standalone HTML report."""
    comps = sorted(outcome.get("components", []), key=lambda c: _sort_key(c["ref_des"]))
    target = outcome.get("target_manufacturer", "?")

    # ── Stats ────────────────────────────────────────────────────────────────
    total = len(comps)
    status_cnt = Counter(c["status"] for c in comps)
    # 'exact' now includes parts kept as-is (already the target manufacturer /
    # not fitted) — their substitute IS the original, so they count as covered.
    already = status_cnt.get("exact", 0)
    newly_repl = status_cnt.get("recommended", 0) + status_cnt.get("partial", 0)
    no_sub = status_cnt.get("no_substitute", 0)
    coverage = already + newly_repl
    cov_pct = f"{100 * coverage / total:.0f}%" if total else "N/A"
    # VERIFIED drop-ins: exact/recommended where the original was identified (no
    # ORIGINAL_UNVERIFIED). Surfaced in the human-readable summary so a reader
    # can't take the headline coverage as "the whole BOM is proven-swappable"
    # when every row is actually an unverified-original match (the FAE finding).
    verified = sum(
        1
        for c in comps
        if c.get("status") in ("exact", "recommended")
        and "ORIGINAL_UNVERIFIED" not in (c.get("guardrail_fires") or [])
    )
    ver_pct = f"{100 * verified / total:.0f}%" if total else "N/A"

    parts: list[str] = []
    parts.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
    parts.append(f"<title>{_e(title or f'{target} Cross-Reference Report')}</title>")
    parts.append(f"<style>{_CSS}</style></head><body>")

    # ── Header ───────────────────────────────────────────────────────────────
    parts.append("<h1>OpenConverters Cross-Reference Report</h1>")
    if title:
        parts.append(f'<div class="subtitle">{_e(title)}</div>')
    parts.append(
        f'<p class="intro-meta context">'
        f"Target supplier: <strong>{_e(target)}</strong>"
        + (f"&nbsp;&nbsp;{_e(circuit_context)}" if circuit_context else "")
        + "</p>"
    )
    parts.append('<hr class="sep">')

    # ── Executive Summary ────────────────────────────────────────────────────
    parts.append("<h2>Executive Summary</h2>")
    parts.append('<table class="summary-tbl"><colgroup><col><col></colgroup>')
    rows_summary = [
        ("Components reviewed", str(total)),
        (f"Exact / already {target} (no change required)", str(already)),
        ("Newly substituted (recommended + partial)", str(newly_repl)),
        (f"{target} coverage", f"{coverage} ({cov_pct} of components reviewed)"),
        ("Verified drop-ins (original identified, all specs met)", f"{verified} ({ver_pct})"),
        ("No equivalent available", str(no_sub)),
    ]
    for i, (metric, value) in enumerate(rows_summary):
        cls = "last-row" if i == len(rows_summary) - 1 else "data-row"
        parts.append(_tr([_e(metric), _e(value)], cls=cls))
    parts.append("</table>")
    parts.append(
        '<p class="coverage-line"><em>Coverage counts parts that are exact (kept as-is because '
        f"they are already {_e(target)}) plus parts newly substituted from the {_e(target)} "
        "catalogue.</em></p>"
    )
    parts.append('<hr class="sep">')

    # ── Substitution Table ───────────────────────────────────────────────────
    parts.append("<h2>Substitution Table</h2>")
    parts.append(
        '<table class="sub-tbl"><colgroup>'
        '<col class="c-ref"><col class="c-orig"><col class="c-sub">'
        '<col class="c-st"><col class="c-conf"><col class="c-note">'
        "</colgroup>"
    )
    parts.append(
        f"<tr><th>Ref</th><th>Original PN</th><th>{_e(target)} PN</th>"
        "<th>Status</th><th>Conf.</th><th>Notes</th></tr>"
    )
    for i, c in enumerate(comps):
        sl, _ = _status_label(c["status"], target)
        cl, cc = _confidence(c)
        sub = c.get("substitute_mpn") or "—"
        orig = c.get("original_mpn") or "—"
        note = _short_note(c)
        cls = "last-row" if i == len(comps) - 1 else "data-row"
        parts.append(
            f'<tr class="{cls}">'
            f"<td>{_e(c['ref_des'])}</td>"
            f'<td class="mpn">{_e(orig)}</td>'
            f'<td class="mpn">{_e(sub)}</td>'
            f'<td class="no-wrap">{_e(sl)}</td>'
            f'<td class="no-wrap {cc}">{_e(cl)}</td>'
            f"<td>{_e(note)}</td>"
            "</tr>"
        )
    parts.append("</table>")
    parts.append('<hr class="sep">')

    # ── Engineering Notes ────────────────────────────────────────────────────
    eng_html = _engineering_notes(comps, target)
    if eng_html:
        parts.append("<h2>Engineering Notes</h2>")
        parts.append(eng_html)
        parts.append('<hr class="sep">')

    # ── Components Not Replaced ──────────────────────────────────────────────
    not_replaced = [c for c in comps if c.get("status") == "no_substitute"]
    if not_replaced:
        parts.append("<h2>Components Not Replaced</h2>")
        parts.append(
            '<table class="nr-tbl"><colgroup>'
            '<col class="c-ref2"><col class="c-orig2"><col class="c-why">'
            "</colgroup>"
        )
        parts.append("<tr><th>Ref</th><th>Original PN</th><th>Reason</th></tr>")
        for i, c in enumerate(not_replaced):
            cls = "last-row" if i == len(not_replaced) - 1 else "data-row"
            note = (c.get("notes") or "—").strip()
            # Use first sentence only for brevity
            reason = re.split(r"(?<=[.!?])\s", note)[0]
            parts.append(
                f'<tr class="{cls}">'
                f"<td>{_e(c['ref_des'])}</td>"
                f'<td class="mpn">{_e(c.get("original_mpn") or "—")}</td>'
                f"<td>{_e(reason)}</td>"
                "</tr>"
            )
        parts.append("</table>")
        parts.append('<hr class="sep">')

    # ── Recommendations ──────────────────────────────────────────────────────
    recs = _recommendations(comps)
    if recs:
        parts.append("<h2>Recommendations</h2>")
        parts.append(f'<ul class="rec-list">{"".join(recs)}</ul>')
        parts.append('<hr class="sep">')

    # ── Appendix: Guardrails / Otto / Reviewer (collapsed for print) ─────────
    guardrails = outcome.get("guardrail_log", [])
    otto = outcome.get("otto_log", {})
    verdicts = outcome.get("review_verdicts", [])
    diags = outcome.get("diagnostics", [])

    if guardrails or otto.get("challenges") or verdicts or diags:
        parts.append('<div class="no-print">')
        if guardrails:
            parts.append("<h2>Guardrail Fires</h2>")
            for g in guardrails:
                parts.append(
                    f"<p>G{_e(g.get('guardrail_id', '?'))} "
                    f"{_e(g.get('ref_des', '?'))}: {_e(g.get('reason', ''))}</p>"
                )
        if otto.get("challenges"):
            parts.append("<h2>Otto Challenge Log</h2>")
            parts.append(
                "<p><em>Otto is a Würth Elektronik sales agent that challenges every "
                "no_substitute verdict.</em></p>"
            )
            parts.append(
                "<table><tr><th>Ref</th><th>Verdict</th>"
                "<th>Counter-Proposal</th><th>Verified?</th></tr>"
            )
            verified = {v["ref_des"] for v in otto.get("verified", [])}
            rejected = {r["ref_des"] for r in otto.get("rejected", [])}
            for i, ch in enumerate(otto["challenges"]):
                ref = ch.get("ref_des", "?")
                verdict = ch.get("verdict", "?")
                proposal = ch.get("counter_proposal") or "—"
                check = (
                    "Verified"
                    if ref in verified
                    else "Rejected"
                    if ref in rejected
                    else "N/A"
                    if verdict == "CONFIRMED"
                    else "—"
                )
                cls = "last-row" if i == len(otto["challenges"]) - 1 else "data-row"
                parts.append(
                    f'<tr class="{cls}"><td>{_e(ref)}</td><td>{_e(verdict)}</td>'
                    f"<td>{_e(proposal)}</td><td>{_e(check)}</td></tr>"
                )
            parts.append("</table>")
        for v in verdicts:
            vc = v.get("verdict", "?").upper()
            parts.append(f"<h2>Reviewer Verdict: {_e(vc)}</h2>")
            if v.get("summary"):
                parts.append(f"<p>{_e(v['summary'])}</p>")
            for obj in v.get("objections", []):
                parts.append(f"<p>⚠ {_e(obj)}</p>")
        if diags:
            parts.append("<h2>Diagnostics</h2>")
            for d in diags:
                parts.append(f"<p>{_e(d)}</p>")
        parts.append("</div>")

    parts.append('<div class="footer">Generated by Heaviside CR Pipeline · OpenConverters</div>')
    parts.append("</body></html>")
    return "\n".join(parts)


__all__ = ["render_crossref_html"]
