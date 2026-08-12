"""Heaviside MCP server — the design pipeline as MCP tools, over stdio OR HTTP.

Tools:
  list_topologies   — the registered converter topologies
  design_magnetic   — magnetic candidates for a topology + spec       [widget]
  design_bom        — real parts for every line of a design            [widget]
  cross_reference   — a whole BOM re-sourced to another manufacturer   [widget]
  reverse_engineer  — extract a reference design's spec + BOM and beat it
  query_lessons     — the teacher's lesson store

Two transports, one tool surface:

  heaviside serve --mcp             # stdio  (a host that spawns us as a subprocess)
  heaviside serve --mcp --http      # streamable HTTP on 127.0.0.1:8405/mcp
  python -m heaviside.mcp_server [--http]

Moebius registers pipelines by URL and health-checks them, so a stdio-only server
would have to be respawned per session; the HTTP transport is what makes Heaviside
a pipeline rather than a subprocess (ABT #667). The transport-security allowlisting
mirrors Kirchhoff/mcp/server.py — behind a tunnel the Host header is the public
name, and the SDK's DNS-rebinding protection rejects an unrecognised one with a bare
421 that hosts surface as a misleading sign-in error.

Results travel on two channels: a compact digest for the model in `content`, and the
full payload in `structuredContent` for the widget. Returning one big JSON blob as
text — what this server used to do — puts an entire BOM into the context window and
leaves a widget nothing to render.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent

logger = logging.getLogger(__name__)

# --- MCP Apps wire constants (from @modelcontextprotocol/ext-apps 1.7.5) -----
# One widget for every tool that returns a list of things to choose between: magnetic
# candidates, BOM lines and cross-referenced substitutes are the same shape and the
# same human decision. (ABT #663 asks websharedcomponents for one such component
# across the ecosystem; this one is built to be replaced by it.)
UI_RESOURCE_MIME = "text/html;profile=mcp-app"
UI_RESULTS_URI = "ui://heaviside/results.html"
_WIDGET_DIR = Path(__file__).resolve().parent.parent / "mcp" / "dist"
UI_BUNDLES = {UI_RESULTS_URI: _WIDGET_DIR / "results.html"}

DEFAULT_HTTP_PORT = 8405          # Hertz 8400, Kirchhoff 8401, Kelvin 8402, Moebius 8404


def _ui_meta(uri: str) -> dict:
    """registerAppTool() emits BOTH the flat key and the nested object, so hosts
    reading either form find it. Mirror that exactly."""
    return {"ui/resourceUri": uri, "ui": {"resourceUri": uri}}


UI_RESULTS_META = _ui_meta(UI_RESULTS_URI)


def assert_widgets_resolve() -> None:
    """Every ui:// this server advertises must have a bundle behind it.

    A tool that advertises a widget the host cannot fetch renders as a broken panel
    and nothing server-side complains — OpenMagnetics ships a curves URI with no
    bundle, so eight tools have advertised an unfetchable chart ever since (ABT
    #651). Refuse to start instead, on either transport: the tool metadata is the
    same on both.
    """
    missing = [f"{uri} -> {path}" for uri, path in UI_BUNDLES.items() if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "widget bundle(s) missing, so these tools would advertise a UI the host cannot "
            "fetch: " + "; ".join(missing) + " -- build them: cd mcp && npm install && npm run build"
        )


def _transport_security() -> TransportSecuritySettings:
    """Host/origin allowlist for the HTTP transport.

    The SDK rejects an unrecognised Host with a bare "421 Invalid Host header".
    Behind a tunnel or reverse proxy the Host is the PUBLIC name, so every request
    dies at 421 — and a remote host that cannot speak MCP typically falls back to
    probing for OAuth, surfacing as "couldn't register with the sign-in service".
    Name the public host in HEAVISIDE_PUBLIC_HOST, or set HEAVISIDE_ALLOW_ANY_HOST=1
    for a throwaway tunnel whose name changes per run.
    """
    public = os.environ.get("HEAVISIDE_PUBLIC_HOST", "").strip()
    if "://" in public:                 # a pasted URL is fine; a Host carries no scheme/path
        public = public.split("://", 1)[1]
    public = public.split("/", 1)[0].strip()
    if os.environ.get("HEAVISIDE_ALLOW_ANY_HOST") == "1":
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    port = _http_port()
    allowed = [f"127.0.0.1:{port}", f"localhost:{port}", "127.0.0.1", "localhost"]
    if public:
        allowed += [public, f"{public}:443"]
    # allowed_origins is matched EXACTLY (or with a trailing ":*" port wildcard) — a
    # bare "*" is a literal that never matches, so it reads as "allow everything"
    # while 403-ing every browser-resident host. Name the origins that actually call.
    origins = ["https://claude.ai", "https://www.claude.ai",
               "http://localhost:*", "http://127.0.0.1:*"]
    if public:
        origins.append(f"https://{public}")
    origins += [o.strip() for o in
                os.environ.get("HEAVISIDE_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    return TransportSecuritySettings(allowed_hosts=allowed, allowed_origins=origins)


def _http_port() -> int:
    raw = os.environ.get("HEAVISIDE_MCP_PORT", "").strip()
    return int(raw) if raw else DEFAULT_HTTP_PORT


mcp = FastMCP("Heaviside", host=os.environ.get("HEAVISIDE_MCP_HOST", "127.0.0.1"),
              port=_http_port(), transport_security=_transport_security())


# --- helpers ----------------------------------------------------------------

def _json_safe(value: Any) -> Any:
    """Make a payload survive JSON with its meaning intact.

    JSON has no infinity, and the SDK's serialiser turns a non-finite float into
    `null` — which would report the selector's "no ripple-current constraint, so the
    headroom is unbounded" (a real margin of +inf) as "the record does not state it".
    Those are different facts and the whole codebase treats them as different, so the
    unbounded case travels as a word rather than as a silent absence. NaN genuinely is
    no value, and becomes null.
    """
    import math

    if isinstance(value, float):
        if math.isnan(value):
            return None
        if math.isinf(value):
            return "unbounded" if value > 0 else "-unbounded"
        return value
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _result(summary: str, payload: dict) -> CallToolResult:
    """Two channels: a compact digest for the model, the payload for the widget.

    Returning a plain dict from a FastMCP tool emits NO structuredContent and
    serialises the WHOLE payload into `content`.
    """
    return CallToolResult(content=[TextContent(type="text", text=summary)],
                          structuredContent=_json_safe(payload))


def _eng(value: Any, unit: str = "") -> str:
    """Engineering notation, e.g. 4.7 µH / 250 mW. Absent stays '-', never 0."""
    if not isinstance(value, (int, float)):
        return "-"
    value = float(value)
    if value == 0.0:
        return f"0 {unit}".strip()
    a = abs(value)
    for factor, prefix in ((1e-12, "p"), (1e-9, "n"), (1e-6, "µ"), (1e-3, "m"), (1.0, "")):
        if a < factor * 1000.0:
            return f"{value / factor:.4g} {prefix}{unit}".strip()
    if a < 1e6:
        return f"{value / 1e3:.4g} k{unit}".strip()
    return f"{value / 1e6:.4g} M{unit}".strip()


def _dig(obj: Any, *path: str) -> Any:
    """A nested lookup that returns None rather than raising on a missing branch.

    Used ONLY for display fields read out of a MAS document (losses, gap, wire).
    Absent must reach the digest as "-" — never as 0, which would read as a real
    measurement of zero loss.
    """
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _component_kind(comp: dict) -> str | None:
    """What kind of part a TAS component is.

    A Kirchhoff-built TAS carries the kind as the key of the component's `data`
    object (`{"semiconductor": …, "inputs": …}`); `inputs` is the excitation that
    travels with every component, not a kind. An older stencil TAS carried a
    catalogue filename there as a plain string.
    """
    data = comp.get("data")
    if isinstance(data, dict):
        kinds = [k for k in data if k != "inputs"]
        return kinds[0] if len(kinds) == 1 else ("/".join(sorted(kinds)) or None)
    if isinstance(data, str):
        return Path(data).stem or None
    return None


def _magnetic_summary(design: Any, rank: int) -> dict:
    """One magnetic candidate, flattened for the digest and the widget."""
    mas = design.mas
    outputs = (mas.get("outputs") or [{}])[0] if isinstance(mas.get("outputs"), list) else {}
    windings = design.windings
    return {
        "rank": rank,
        "scoring": design.scoring,
        "core_shape": design.core_shape_name,
        "core_material": design.core_material_name,
        "windings": [
            {"name": w.get("name") or f"winding_{i}", "turns": w.get("numberTurns")}
            for i, w in enumerate(windings)
        ],
        "turns": [w.get("numberTurns") for w in windings],
        "core_losses_W": _dig(outputs, "coreLosses", "coreLosses"),
        "winding_losses_W": _dig(outputs, "windingLosses", "windingLosses"),
        # outputs[].inductance.magnetizingInductance.magnetizingInductance is a
        # dimensionWithTolerance; MKF states the nominal here.
        "magnetizing_inductance_H": _dig(outputs, "inductance", "magnetizingInductance",
                                         "magnetizingInductance", "nominal"),
        "elapsed_s": design.elapsed_s,
    }


# --- tools ------------------------------------------------------------------

@mcp.tool(
    title="List converter topologies",
    description="Every converter topology Heaviside has a registered designer for.",
    structured_output=False,
)
def list_topologies() -> CallToolResult:
    """The topologies design_magnetic and design_bom accept."""
    from heaviside.topologies.registry import CONVERTERS

    entries = [{"name": e.name, "family": e.family} for e in CONVERTERS]
    by_family: dict[str, list[str]] = {}
    for e in entries:
        by_family.setdefault(e["family"], []).append(e["name"])
    lines = [f"  {family}: {', '.join(sorted(names))}" for family, names in sorted(by_family.items())]
    return _result(f"{len(entries)} topologies:\n" + "\n".join(lines),
                   {"topologies": entries, "count": len(entries)})


@mcp.tool(
    title="Design a magnetic",
    description=(
        "Magnetic candidates for a topology + converter spec, designed by MKF through "
        "Heaviside's fast Pareto path: real core shapes and materials, turns, and the "
        "losses each candidate carries. Returns a ranked list to choose from."
    ),
    meta=UI_RESULTS_META,
    structured_output=False,
)
def design_magnetic(topology: str, spec: dict, max_results: int = 3,
                    include_mas: bool = False) -> CallToolResult:
    """Ranked magnetic designs.

    Args:
        topology: one of list_topologies() -- 'buck', 'flyback', ...
        spec: the converter spec (designRequirements + operatingPoints), SI units.
        max_results: how many candidates to design.
        include_mas: attach each candidate's full MAS document (large; needed to hand
            a design on to another tool, not to read it).
    """
    from heaviside.bridge import design_magnetics_fast

    designs = design_magnetics_fast(topology, spec, max_results=max(1, int(max_results)))
    candidates = [_magnetic_summary(d, i + 1) for i, d in enumerate(designs)]
    if include_mas:
        for candidate, design in zip(candidates, designs):
            candidate["mas"] = design.mas
    lines = [
        f"  {c['rank']}. {c['core_shape']} / {c['core_material']}, "
        f"turns {'+'.join(str(t) for t in c['turns'] if t is not None) or '-'}"
        f" | core {_eng(c['core_losses_W'], 'W')}, winding {_eng(c['winding_losses_W'], 'W')}"
        f" | score {c['scoring']:.4g}"
        for c in candidates
    ]
    return _result(
        f"{len(candidates)} magnetic candidate(s) for a {topology}, in the engine's own "
        f"order:\n" + "\n".join(lines)
        + ("\n(the full MAS of each is in the structured output)" if include_mas else ""),
        {"mode": "magnetics", "topology": topology, "candidates": candidates})


@mcp.tool(
    title="Source a BOM",
    description=(
        "Real orderable parts for every fillable line of a design (Kelvin selection over "
        "the TAS catalogue, driven by the stresses Heaviside derives). Returns one line "
        "per component with the part chosen and why."
    ),
    meta=UI_RESULTS_META,
    structured_output=False,
)
def design_bom(topology: str, spec: dict, tas: dict) -> CallToolResult:
    """A sourced BOM.

    Args:
        tas: the topology document to fill (from Kirchhoff's design_converter, or
            Heaviside's own pipeline).
    """
    from heaviside.catalogue import assemble_bom_from_tas

    result = assemble_bom_from_tas(tas, topology=topology, spec=spec)
    # EVERY component in the design gets a line, filled or not. Returning only the
    # sourced ones makes a BOM that sourced 1 of 6 read like a complete 1-line BOM —
    # "nothing else needed a part" and "nothing else got one" must not look the same.
    bom = []
    for stage in (result.get("topology") or {}).get("stages", []):
        for comp in (stage.get("circuit") or {}).get("components", []):
            if not isinstance(comp, dict):
                continue
            provenance = comp.get("selection_provenance")
            line = {"ref_des": comp.get("name"), "category": _component_kind(comp),
                    "filled": isinstance(provenance, dict)}
            if isinstance(provenance, dict):
                line.update(provenance)
                line["ref_des"] = comp.get("name") or provenance.get("ref_des")
            bom.append(line)

    filled = [l for l in bom if l["filled"]]
    unfilled = [l for l in bom if not l["filled"]]
    lines = [
        f"  {l.get('ref_des') or '?'} ({l.get('category') or '?'}): "
        + (f"{l.get('mpn') or l.get('part_number')}"
           + (f" — {l['manufacturer']}" if l.get("manufacturer") else "")
           if l["filled"] else
           "UNFILLED"
           + (" — magnetics are designed by design_magnetic, not sourced from the catalogue"
              if l.get("category") == "magnetic" else ""))
        for l in bom
    ]
    return _result(
        f"{len(filled)}/{len(bom)} line(s) sourced for the {topology}:\n" + "\n".join(lines)
        + (f"\n{len(unfilled)} line(s) came back with no part. That is not necessarily "
           f"'nothing fits': a component the selector does not recognise as a placeholder is "
           f"never looked at, and reports the same way."
           if unfilled else ""),
        {"mode": "bom", "topology": topology, "candidates": bom,
         "lineCount": len(bom), "filledCount": len(filled)})


@mcp.tool(
    title="Cross-reference a BOM",
    description=(
        "Re-source a whole BOM to a target manufacturer: a substitute per line with its "
        "status, plus the diagnostics behind any line that could not be matched."
    ),
    meta=UI_RESULTS_META,
    structured_output=False,
)
def cross_reference(source_bom: list[dict], target_manufacturer: str,
                    circuit_context: str | None = None) -> CallToolResult:
    """Substitutes for a whole BOM.

    Args:
        source_bom: the BOM to re-source, one entry per component.
        target_manufacturer: the vendor to source into.
        circuit_context: what the board does, when it helps judge a substitution.
    """
    from heaviside.pipeline.crossref_pipeline import run_crossref_pipeline

    outcome = run_crossref_pipeline(source_bom, target_manufacturer,
                                    circuit_context=circuit_context)
    components = [
        {"ref_des": c.ref_des, "original_mpn": c.original_mpn, "mpn": c.substitute_mpn,
         "manufacturer": target_manufacturer, "status": c.status.value}
        for c in outcome.components
    ]
    matched = sum(1 for c in components if c["mpn"])
    lines = [
        f"  {c['ref_des']}: {c['original_mpn']} -> {c['mpn'] or 'NO SUBSTITUTE'} ({c['status']})"
        for c in components
    ]
    return _result(
        f"{matched}/{len(components)} line(s) re-sourced to {target_manufacturer} "
        f"({'passed' if outcome.passed else 'did not pass'}):\n" + "\n".join(lines)
        + ("\n" + "\n".join(f"  ! {d}" for d in list(outcome.diagnostics)[:10])
           if outcome.diagnostics else ""),
        {"mode": "crossref", "target_manufacturer": target_manufacturer,
         "passed": outcome.passed, "candidates": components,
         "diagnostics": list(outcome.diagnostics)})


@mcp.tool(
    title="Reverse-engineer a reference design",
    description=(
        "Extract a reference design's spec and BOM, design a competing converter against "
        "it, and review the result."
    ),
    structured_output=False,
)
def reverse_engineer(reference: str, pdf_path: str | None = None) -> CallToolResult:
    """Spec + BOM of a reference design, and how Heaviside's own design compares."""
    from heaviside.pipeline.re_pipeline import run_re_pipeline

    outcome = run_re_pipeline(reference, pdf_path=Path(pdf_path) if pdf_path else None)
    payload = {
        "reference": outcome.reference,
        "passed": outcome.passed,
        "ref_spec": outcome.ref_spec.__dict__ if outcome.ref_spec else None,
        "bom_count": len(outcome.ref_bom),
        "diagnostics": list(outcome.diagnostics),
    }
    return _result(
        f"{outcome.reference}: {'passed' if outcome.passed else 'did not pass'}, "
        f"{payload['bom_count']} BOM line(s) extracted"
        + (f", {len(payload['diagnostics'])} diagnostic(s):\n"
           + "\n".join(f"  {d}" for d in payload["diagnostics"][:10])
           if payload["diagnostics"] else "."),
        payload)


@mcp.tool(
    title="Query design lessons",
    description=(
        "The teacher's lesson store — what previous designs got wrong, filterable by "
        "topology, severity and age."
    ),
    structured_output=False,
)
def query_lessons(topology: str | None = None, severity: str | None = None,
                  max_age_days: int = 90) -> CallToolResult:
    """Recorded design lessons.

    Args:
        severity: 'error', 'warning' or 'info'.
    """
    from heaviside.pipeline.teacher import load_lessons, summarize_lessons

    lessons = load_lessons(topology=topology, severity=severity, max_age_days=max_age_days)
    entries = [
        {"topology": l.topology, "category": l.category, "severity": l.severity,
         "detail": l.detail, "suggestion": l.suggestion}
        for l in lessons
    ]
    lines = [f"  [{e['severity']}] {e['topology']}/{e['category']}: {e['detail']}"
             for e in entries[:20]]
    return _result(
        f"{len(entries)} lesson(s). {summarize_lessons(lessons)}\n" + "\n".join(lines)
        + (f"\n(+{len(entries) - 20} more in the structured output)" if len(entries) > 20 else ""),
        {"count": len(entries), "summary": summarize_lessons(lessons), "lessons": entries})


# --- the MCP Apps UI resource -----------------------------------------------

@mcp.resource(
    UI_RESULTS_URI,
    name="heaviside-results",
    title="Heaviside results",
    mime_type=UI_RESOURCE_MIME,
)
def results_widget() -> str:
    """Ranked magnetic candidates, BOM lines and cross-referenced substitutes — one
    table with a per-row detail panel and a 'use this' action that reports the choice
    back to the model.

    MCP App resources render in a deny-by-default CSP iframe, so the widget is built
    as ONE self-contained file (vite-plugin-singlefile).
    """
    bundle = UI_BUNDLES[UI_RESULTS_URI]
    if not bundle.exists():                                        # pragma: no cover
        raise FileNotFoundError(
            f"{bundle} missing -- build the widget first: cd mcp && npm install && npm run build")
    return bundle.read_text(encoding="utf-8")


# --- transports -------------------------------------------------------------

def build_app():
    """Starlette app for the streamable-HTTP transport, with CORS.

    Browser-resident MCP hosts fetch /mcp from page JavaScript, so without these
    headers the connection dies at the preflight — and the streamable transport
    additionally needs to READ `Mcp-Session-Id` off the response, which cross-origin
    JS cannot do unless the header is explicitly exposed.
    """
    from starlette.middleware.cors import CORSMiddleware

    assert_widgets_resolve()
    app = mcp.streamable_http_app()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],           # tighten to your host origins in production
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["Mcp-Session-Id"],
    )
    return app


def serve_http(host: str | None = None, port: int | None = None) -> None:
    """Run the streamable-HTTP transport (the one Moebius registers by URL)."""
    import uvicorn

    app = build_app()
    uvicorn.run(app, host=host or mcp.settings.host, port=port or mcp.settings.port)


async def main() -> None:
    """Run the stdio transport (a host that spawns this process itself)."""
    assert_widgets_resolve()
    await mcp.run_stdio_async()


if __name__ == "__main__":
    if "--http" in sys.argv:
        serve_http()
    else:
        import asyncio

        asyncio.run(main())
