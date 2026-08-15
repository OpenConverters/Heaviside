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

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

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


def _lesson_text(lesson: Any) -> str:
    """A lesson as readable text, whatever shape its `detail` is in.

    `Lesson.detail` is DECLARED `str` and is a dict on 6,440 of the 7,436
    records in the store — the crossref_objection lessons carry
    {ref_des, issue, details}. Nothing caught it because every existing reader
    interpolates it into an f-string, which stringifies anything; the first
    code to do `detail + "..."` was this one, and it raised.

    Rendered field by field rather than as `str(dict)`: a passage is meant to
    be read, and a Python repr with quotes and braces is not reading material.
    The type inconsistency itself is reported separately — this function copes
    with it, it does not excuse it.
    """
    detail = lesson.detail
    if isinstance(detail, dict):
        body = "\n".join(f"{k}: {v}" for k, v in detail.items() if v not in (None, ""))
    elif isinstance(detail, (list, tuple)):
        body = "\n".join(f"- {item}" for item in detail)
    else:
        body = str(detail)
    if lesson.suggestion:
        body += f"\n\nSuggestion: {lesson.suggestion}"
    return body


class DimensionWithTolerance(BaseModel):
    """The {nominal, minimum, maximum} shape used across PEAS/MAS/MKF.

    All three are optional individually, but a block with none of them carries
    no value at all — which the engine rejects rather than defaulting.
    """
    nominal: float | None = None
    minimum: float | None = None
    maximum: float | None = None


class OperatingPoint(BaseModel):
    """One operating point of the converter.

    `outputVoltages` and `outputCurrents` are parallel LISTS, one entry per
    output rail, and that is not a stylistic choice — a flyback with three
    secondaries has three of each, and a scalar would silently describe only
    the first.
    """
    switchingFrequency: float = Field(description="Hz.")
    outputVoltages: list[float] = Field(
        description="V, one per output rail. A single-output converter is a "
                    "list of one — [12], not 12.")
    outputCurrents: list[float] = Field(
        description="A, one per rail, same order and length as outputVoltages.")
    ambientTemperature: float | None = Field(
        default=None, description="degC. Ambient the magnetic must survive.")


class ConverterSpec(BaseModel):
    """What a magnetic has to be designed against.

    TYPED BECAUSE AN UNTYPED ONE COSTS TWO MINUTES A QUESTION. `spec: dict`
    published an input schema of "object, any keys". Watched live, an assistant
    asked a routine design question spent 147 seconds and EIGHT calls on this
    tool, guessing its way through the shape — input_voltage, then
    inputVoltage:{nominal}, then operatingPoints:[{outputVoltage}] — and in the
    middle of it searched the KNOWLEDGE CORPUS for this tool's own schema,
    because the tool would not say. Six of those eight calls were wasted, and
    each one mounted a widget the reader had to scroll past.

    An input schema is documentation the caller cannot fail to read. This is
    the same defect, and the same fix, as BomLine above.
    """
    inputVoltage: DimensionWithTolerance = Field(
        description="V. A {nominal, minimum, maximum} block — a converter is "
                    "designed across its input range, not at one point.")
    efficiency: float = Field(
        description="Target efficiency as a fraction, e.g. 0.95. Required: it "
                    "sets the input power the magnetic is sized for.")
    operatingPoints: list[OperatingPoint] = Field(
        min_length=1,
        description="At least one. The magnetic is designed against all of them.")
    currentRippleRatio: float | None = Field(
        default=None,
        description="Inductor ripple as a fraction of DC current, e.g. 0.3. "
                    "Left out, the engine's own default applies.")
    diodeVoltageDrop: float | None = Field(
        default=None, description="V, for a non-synchronous rectifier.")


class BomLine(BaseModel):
    """One line of a BOM to re-source.

    Typed at the MCP boundary on purpose. `source_bom: list[dict]` published an
    input schema of "array of object, any keys", which tells a caller nothing
    about what a line must contain — and a tool whose input cannot be
    constructed is a tool that never gets called. Measured, not guessed: asked
    to re-source a three-line BOM, an assistant with this tool available
    ignored it and made fourteen single-part cross_reference calls to another
    pipeline instead, losing the per-line status and diagnostics that are the
    whole point of this one.

    The internal normaliser (_normalize_bom) stays as tolerant as it is — it
    accepts `mpn`/`part`, `type`/`category`, `location`/`designator`, because a
    real BOM export is messy and that leniency is correct for a FILE. It is not
    correct for an interface: here there is one name per concept, and a caller
    who has to guess between four spellings has no contract at all.
    """
    ref_des: str = Field(description="Reference designator, e.g. 'Q1', 'C12'. "
                                     "Must be unique within the BOM.")
    original_mpn: str = Field(description="The manufacturer part number to replace.")
    component_type: str | None = Field(
        default=None,
        description="Category: mosfet, diode, capacitor, resistor, inductor, "
                    "connector, ic. Inferred from the description when omitted; "
                    "a line whose category cannot be determined is reported "
                    "unmatched rather than guessed at.")
    description: str | None = Field(
        default=None,
        description="Free text from the BOM row. Used to infer component_type "
                    "and to judge whether a substitution is sensible.")
    quantity: int | None = Field(default=None, description="Placements on the board.")


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
                   # `catalogue` (Moebius contract, ABT #741): what this
                   # pipeline can answer about. `families` is the contract's
                   # name for that set — here they are topologies, not
                   # component families, but the question is the same one.
                   {"mode": "catalogue", "families": entries,
                    "units": f"{len(entries)} topologies this build can design"})


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
def design_magnetic(topology: str, spec: ConverterSpec, max_results: int = 3,
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

    # The engine works in plain dicts; the model exists for the INTERFACE.
    # exclude_none so an omitted optional stays absent and the engine's own
    # default applies, rather than arriving as an explicit null.
    designs = design_magnetics_fast(topology, spec.model_dump(exclude_none=True),
                                    max_results=max(1, int(max_results)))
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
        # `design` (ABT #741), NOT `recommend`. These are magnetics the engine
        # SIZED from a spec; none of them has an MPN because none of them is a
        # part you can order. The contract's `candidate` requires one precisely
        # so that "here is a component you can buy" and "here is a thing you
        # would have to have made" cannot be confused.
        {"mode": "design", "kind": "magnetic", "topology": topology,
         "tiebreaker": "the engine's own scoring",
         "designs": [
             {"rank": c["rank"],
              "label": f"{c.get('core_shape')} / {c.get('core_material')}",
              "score": c.get("scoring"),
              "document": c.get("mas"),
              "properties": {k: v for k, v in c.items()
                             if k not in ("rank", "scoring", "mas")}}
             for c in candidates
         ]})


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
def design_bom(topology: str, spec: ConverterSpec, tas: dict) -> CallToolResult:
    """A sourced BOM.

    Args:
        topology: one of list_topologies() -- 'buck', 'flyback', ...
        spec: the converter spec, same shape design_magnetic takes. It sets the
            electrical stresses each line is selected against, so a part chosen
            without it would be chosen against nothing.
        tas: the TAS document to fill -- a whole converter design, as returned
            by kirchhoff__design_converter or by Heaviside's own pipeline. Pass
            it through UNCHANGED; do not attempt to compose one by hand.

            Deliberately left as an object rather than declared field by field.
            TAS is an external schema with its own repo and its own governance,
            and re-declaring its shape here would create a second definition
            that drifts from the first. A caller never authors this value, it
            only forwards one, so an inline schema would be documentation for
            something nobody types.
    """
    from heaviside.catalogue import assemble_bom_from_tas

    # The catalogue works in plain dicts; the model is for the INTERFACE.
    result = assemble_bom_from_tas(tas, topology=topology,
                                   spec=spec.model_dump(exclude_none=True))
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
        # `bom` (ABT #741). `unsourced`, not `no_substitute`: a placeholder the
        # selector does not recognise is never LOOKED at, and reporting that as
        # "nothing fits" asserts a negative result nobody established. That
        # distinction is the entire point of the digest sentence above it.
        {"mode": "bom", "topology": topology,
         "total": len(bom), "sourced": len(filled),
         # Optional fields are OMITTED when absent, never sent as null or "".
         # A component whose category could not be determined has no `kind`;
         # saying `kind: null` claims the category is known to be nothing.
         "lines": [
             {k: v for k, v in {
                 "ref": l.get("ref_des") or "?",
                 "kind": l.get("category"),
                 "status": "recommended" if l["filled"] else "unsourced",
                 "mpn": (l.get("mpn") or l.get("part_number")) if l["filled"] else None,
                 "manufacturer": l.get("manufacturer") if l["filled"] else None,
                 "notes": ("magnetics are designed by design_magnetic, not sourced "
                           "from the catalogue"
                           if not l["filled"] and l.get("category") == "magnetic" else ""),
             }.items() if k == "mpn" or (v is not None and v != "")}
             for l in bom
         ],
         "diagnostics": ([f"{len(unfilled)} line(s) were not sourced; a component the "
                          f"selector does not recognise as a placeholder is never "
                          f"looked at and reports the same way"] if unfilled else [])})


def _run_crossref(source_bom: list[BomLine], target_manufacturer: str,
                  circuit_context: str | None,
                  progress=None) -> tuple[str, dict]:
    """The cross-reference, as (digest, payload).

    Factored out so the blocking tool and the job share ONE code path. Two
    copies of this mapping would drift, and the version a user reached would
    depend on which tool they happened to call.
    """
    from heaviside.pipeline.crossref_pipeline import run_crossref_pipeline

    # The pipeline works in plain dicts and normalises them itself; the typed
    # model exists for the INTERFACE, so it is unwrapped here rather than
    # threaded through. exclude_none so an omitted component_type stays absent
    # and gets inferred, instead of arriving as an explicit null that reads as
    # "the caller says there is no type".
    kwargs = {}
    if progress is not None:
        # The pipeline calls progress(message, pct) before each stage. Only the
        # message is kept: "CR stage 6: Otto" tells an engineer whether to wait,
        # and a percentage does not.
        kwargs["progress"] = lambda message, pct=None: progress(str(message))
    outcome = run_crossref_pipeline(
        [line.model_dump(exclude_none=True) for line in source_bom],
        target_manufacturer, circuit_context=circuit_context, **kwargs)
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
    digest = (
        f"{matched}/{len(components)} line(s) re-sourced to {target_manufacturer} "
        f"({'passed' if outcome.passed else 'did not pass'}):\n" + "\n".join(lines)
        + ("\n" + "\n".join(f"  ! {d}" for d in list(outcome.diagnostics)[:10])
           if outcome.diagnostics else "")
    )
    # `bom`, NOT `crossref` (ABT #741). The crossref branch carries ONE
    # `original` — it is built for kelvin__cross_reference, which answers
    # "substitutes for this part". This tool answers N questions with one
    # answer each, and every line has its own original. Flattening it into
    # `candidates` would lose which line each part belongs to.
    payload = {
        "mode": "bom", "targetManufacturer": target_manufacturer,
        "passed": outcome.passed,
        "total": len(components), "sourced": matched,
        "lines": [
            {"ref": c["ref_des"], "originalMpn": c["original_mpn"],
             "mpn": c["mpn"], "manufacturer": c["manufacturer"] if c["mpn"] else None,
             "status": c["status"]}
            for c in components
        ],
        "diagnostics": list(outcome.diagnostics),
    }
    return digest, payload


@mcp.tool(
    title="Cross-reference a BOM (blocking)",
    description=(
        "Re-source a whole BOM to a target manufacturer: a substitute per line with its "
        "status, plus the diagnostics behind any line that could not be matched. "
        "BLOCKS UNTIL DONE, and this pipeline takes MINUTES — 234 s for a single line, "
        "about fifteen minutes for two. Most callers time out long before it returns. "
        "Use submit_crossref instead unless you control the timeout and know it is "
        "longer than the run."
    ),
    meta=UI_RESULTS_META,
    structured_output=False,
)
def cross_reference(source_bom: list[BomLine], target_manufacturer: str,
                    circuit_context: str | None = None) -> CallToolResult:
    """Substitutes for a whole BOM.

    Args:
        source_bom: the BOM to re-source, one entry per component.
        target_manufacturer: the vendor to source into.
        circuit_context: what the board does, when it helps judge a substitution.
    """
    digest, payload = _run_crossref(source_bom, target_manufacturer, circuit_context)
    return _result(digest, payload)



# --- the asynchronous surface (ABT #758) ------------------------------------
#
# cross_reference takes MINUTES — 234 s measured for one BOM line, ~15 for two —
# and every consumer in front of it has a shorter timeout. A blocking tool that
# outlives its callers is unreachable, not slow: the user sees a turn hang and
# fail, and the work may well have completed after they stopped listening.
#
# Same shape OMFEM uses for FEA, on purpose: two long-running pipelines with two
# different job envelopes would make every consumer learn both.


def _job_result(job, *, with_result: bool = False) -> CallToolResult:
    from heaviside.mcp_jobs import STATES  # noqa: F401  (documents the closed set)

    bits = [f"job {job.id}: {job.state}"]
    if job.label:
        bits.append(f"({job.label})")
    if job.state == "running" and job.phase:
        bits.append(f"— {job.phase}")
    if job.error:
        bits.append(f"— {job.error}")
    return _result(" ".join(bits), job.envelope(with_result=with_result))


@mcp.tool(
    title="Submit a BOM cross-reference",
    description=(
        "Start a whole-BOM re-source and return a job id immediately. Poll with "
        "job_status and fetch with job_result. This is the tool to use: the "
        "blocking cross_reference takes minutes and most callers time out. "
        "job_status reports the pipeline's current stage by name, so progress "
        "can be shown rather than guessed at."
    ),
    structured_output=False,
)
def submit_crossref(source_bom: list[BomLine], target_manufacturer: str,
                    circuit_context: str | None = None) -> CallToolResult:
    """Queue a cross-reference. Returns a job id, not a result.

    Args:
        source_bom: the BOM to re-source, one entry per component.
        target_manufacturer: the vendor to source into.
        circuit_context: what the board does, when it helps judge a substitution.
    """
    from heaviside.mcp_jobs import registry

    label = f"{len(source_bom)} line(s) -> {target_manufacturer}"

    def work(progress):
        _digest, payload = _run_crossref(source_bom, target_manufacturer,
                                         circuit_context, progress=progress)
        return payload

    job = registry().submit(label, work)
    return _result(
        f"job {job.id} queued: {label}. Poll job_status({job.id!r}); the result "
        f"is ready when its state is 'done'. This typically takes minutes.",
        job.envelope())


@mcp.tool(
    title="Job status",
    description=(
        "queued | running | done | failed | cancelled, with the pipeline's current "
        "stage. NOTE that 'failed' can mean this server restarted: the queue lives "
        "in its process, so a restart fails running jobs. Read the error before "
        "resubmitting — minutes of work is not a free retry."
    ),
    structured_output=False,
)
def job_status(job: str) -> CallToolResult:
    """Where a submitted job has got to."""
    from heaviside.mcp_jobs import registry

    return _job_result(registry().get(job))


@mcp.tool(
    title="Job result",
    description=(
        "The finished outcome of a job. Fails if the job is not done yet — it does "
        "not wait, because waiting is what the job pattern exists to avoid."
    ),
    # The widget belongs HERE, not only on the blocking tool. job_result is the
    # path a caller is told to use, so hanging the UI off cross_reference alone
    # meant the recommended route rendered nothing while the discouraged one
    # rendered fine. The widget unwraps the job envelope and shows the nested
    # result, so it needs no knowledge of jobs beyond that.
    meta=UI_RESULTS_META,
    structured_output=False,
)
def job_result(job: str) -> CallToolResult:
    """The result of a finished job."""
    from heaviside.mcp_jobs import registry

    handle = registry().get(job)
    if handle.state != "done":
        raise ValueError(
            f"job {job} is {handle.state}, not done"
            + (f" — {handle.error}" if handle.error else "")
            + ". Poll job_status until it reports 'done'."
        )
    return _job_result(handle, with_result=True)


@mcp.tool(
    title="Cancel a job",
    description=(
        "Cancel a QUEUED job. A running one cannot be cancelled — the pipeline has "
        "no safe interruption point — and this refuses rather than pretending."
    ),
    structured_output=False,
)
def cancel_job(job: str) -> CallToolResult:
    """Cancel a job that has not started."""
    from heaviside.mcp_jobs import registry

    return _job_result(registry().cancel(job))


@mcp.tool(
    title="List jobs",
    description="Every job this server knows about, newest first.",
    structured_output=False,
)
def list_jobs() -> CallToolResult:
    """What this server has been asked to do."""
    from heaviside.mcp_jobs import registry

    jobs = registry().list()
    lines = [f"  {j.id}: {j.state}" + (f" — {j.label}" if j.label else "") for j in jobs]
    return _result(
        f"{len(jobs)} job(s):\n" + "\n".join(lines) if jobs else "No jobs.",
        # A listing is a job-shaped answer about the SERVER rather than about one
        # job, so it carries a synthetic handle and its own state.
        {"mode": "job", "job": "listing", "state": "done",
         "jobs": [j.summary() for j in jobs],
         "caveat": ("The queue lives in this process: a restart loses queued jobs "
                    "and fails running ones. Finished results survive on disk.")})


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
    # `verdict` (Moebius contract, ABT #741): this tool judges Heaviside's own
    # design against a named reference, which is a pass/fail against a
    # criterion rather than a list of anything.
    #
    # `provisional` is REQUIRED and true: the comparison is against a spec and
    # BOM EXTRACTED from documentation, not against a measured board. A reader
    # must not take "passed" as "this design beats that product on the bench".
    payload = {
        "mode": "verdict",
        "verdict": "pass" if outcome.passed else "fail",
        "criterion": f"reference design {outcome.reference}",
        "provisional": True,
        "measurements": {
            "bomLines": {"value": len(outcome.ref_bom),
                         "label": "BOM lines extracted from the reference"},
        },
        "caveat": ("Compared against a spec and BOM extracted from documentation, "
                   "not against measured hardware."
                   + (" " + "; ".join(list(outcome.diagnostics)[:5])
                      if outcome.diagnostics else "")),
    }
    # Kept OUT of the payload: the contract has no home for a free-form spec
    # dump, and inventing a key on a closed envelope is the thing the contract
    # exists to prevent. It stays in the digest, where a reader can see it and
    # no consumer can mistake it for a typed field.
    _ref_spec = outcome.ref_spec.__dict__ if outcome.ref_spec else None
    return _result(
        f"{outcome.reference}: {'passed' if outcome.passed else 'did not pass'}, "
        f"{len(outcome.ref_bom)} BOM line(s) extracted"
        + (f"\nspec: {_ref_spec}" if _ref_spec else "")
        + (f", {len(outcome.diagnostics)} diagnostic(s):\n"
           + "\n".join(f"  {d}" for d in list(outcome.diagnostics)[:10])
           if outcome.diagnostics else "."),
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

    from heaviside.pipeline.teacher import _DEFAULT_LESSON_PATH

    lessons = load_lessons(topology=topology, severity=severity, max_age_days=max_age_days)
    entries = [
        {"topology": l.topology, "category": l.category, "severity": l.severity,
         "detail": l.detail, "suggestion": l.suggestion}
        for l in lessons
    ]

    # Where each lesson physically is, so a claim built on one can be checked.
    # The store is append-only NDJSON — one record per line — so the line number
    # is a real, stable citation rather than a number invented to satisfy a
    # schema. Built by id because load_lessons filters and re-orders, so a
    # position in ITS output says nothing about a position in the file.
    line_of: dict[str, int] = {}
    try:
        for n, raw in enumerate(
                _DEFAULT_LESSON_PATH.read_text(encoding="utf-8").splitlines(), start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                lid = json.loads(raw).get("id")
            except json.JSONDecodeError:
                continue
            if lid:
                line_of[lid] = n
    except OSError:
        # No store, or unreadable. Lessons without a locatable source are
        # dropped below rather than cited to a line that does not exist —
        # a fabricated citation is worse than a missing lesson.
        line_of = {}
    lines = [f"  [{e['severity']}] {e['topology']}/{e['category']}: {e['detail']}"
             for e in entries[:20]]
    return _result(
        f"{len(entries)} lesson(s). {summarize_lessons(lessons)}\n" + "\n".join(lines)
        + (f"\n(+{len(entries) - 20} more in the structured output)" if len(entries) > 20 else ""),
        # `passages` (Moebius contract, ABT #741): retrieved records, each cited
        # to where it lives. A lesson is evidence from one run on one date, and
        # quoted without its source it is indistinguishable from a design rule —
        # which is exactly the confusion the tier/citation fields prevent.
        {"mode": "passages",
         "query": " ".join(filter(None, [topology, severity])) or "all lessons",
         "total": len(entries),
         "shown": sum(1 for l in lessons if l.id in line_of),
         "searched": len(line_of),
         "ranking": "store order, newest last (no relevance ranking is applied)",
         "filters": {"topology": topology, "severity": severity,
                     "max_age_days": max_age_days},
         "passages": [
             {"id": l.id,
              # Relative to the repo root — an absolute path on this machine
              # is not a citation anyone else can follow.
              "source": str(_DEFAULT_LESSON_PATH.relative_to(
                  Path(__file__).resolve().parent.parent)),
              "line": line_of[l.id],
              # 'lesson' is the honest tier: true of one run on one date,
              # never an established rule.
              "tier": "lesson",
              "title": f"{l.topology}/{l.category}",
              "breadcrumb": [l.topology, l.category, l.severity],
              "domain": l.category,
              "excerpt": _lesson_text(l),
              "truncated": False}
             for l in lessons if l.id in line_of
         ],
         "caveat": (summarize_lessons(lessons)
                    + (f" {len(entries) - sum(1 for l in lessons if l.id in line_of)} "
                       f"lesson(s) omitted: no locatable record in the store."
                       if any(l.id not in line_of for l in lessons) else ""))})


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
