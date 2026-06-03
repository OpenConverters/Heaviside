"""Heaviside MCP server — expose the design pipeline as MCP tools.

Tools:
  design_magnetic   — magnetic-only design for a topology + spec
  design_bom        — BOM selection from TAS DB
  list_topologies   — enumerate registered topologies
  query_lessons     — query the teacher's lesson store

Launch:
  heaviside serve --mcp
  python -m heaviside.mcp_server
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)

server = Server("heaviside", version="0.1.0")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="design_magnetic",
            description="Design magnetic components for a given topology and spec. Returns core, windings, scoring.",
            inputSchema={
                "type": "object",
                "properties": {
                    "topology": {"type": "string", "description": "Topology name (e.g. 'buck', 'flyback')"},
                    "spec": {"type": "object", "description": "Converter spec JSON"},
                    "max_results": {"type": "integer", "default": 3},
                },
                "required": ["topology", "spec"],
            },
        ),
        Tool(
            name="design_bom",
            description="Select real MOSFET/diode/capacitor from TAS DB for a topology + spec + TAS.",
            inputSchema={
                "type": "object",
                "properties": {
                    "topology": {"type": "string"},
                    "spec": {"type": "object"},
                    "tas": {"type": "object"},
                },
                "required": ["topology", "spec", "tas"],
            },
        ),
        Tool(
            name="list_topologies",
            description="List all registered converter topologies with their families.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="query_lessons",
            description="Query the teacher's lesson store for design lessons.",
            inputSchema={
                "type": "object",
                "properties": {
                    "topology": {"type": "string", "description": "Filter by topology"},
                    "severity": {"type": "string", "enum": ["error", "warning", "info"]},
                    "max_age_days": {"type": "integer", "default": 90},
                },
            },
        ),
        Tool(
            name="reverse_engineer",
            description="Reverse-engineer a reference design: extract specs + BOM, design competing converter, review.",
            inputSchema={
                "type": "object",
                "properties": {
                    "reference": {"type": "string", "description": "Reference design name"},
                    "pdf_path": {"type": "string", "description": "Path to PDF file (optional)"},
                },
                "required": ["reference"],
            },
        ),
        Tool(
            name="cross_reference",
            description="Cross-reference a BOM to a target manufacturer.",
            inputSchema={
                "type": "object",
                "properties": {
                    "source_bom": {"type": "array", "description": "Source BOM components"},
                    "target_manufacturer": {"type": "string"},
                    "circuit_context": {"type": "string"},
                },
                "required": ["source_bom", "target_manufacturer"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "design_magnetic":
        return [TextContent(type="text", text=_design_magnetic(arguments))]
    elif name == "design_bom":
        return [TextContent(type="text", text=_design_bom(arguments))]
    elif name == "list_topologies":
        return [TextContent(type="text", text=_list_topologies())]
    elif name == "query_lessons":
        return [TextContent(type="text", text=_query_lessons(arguments))]
    elif name == "reverse_engineer":
        return [TextContent(type="text", text=_reverse_engineer(arguments))]
    elif name == "cross_reference":
        return [TextContent(type="text", text=_cross_reference(arguments))]
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


def _design_magnetic(args: dict[str, Any]) -> str:
    from heaviside.bridge import BridgeError, design_magnetics_fast

    topology = args["topology"]
    spec = args["spec"]
    max_results = args.get("max_results", 3)

    try:
        candidates = design_magnetics_fast(
            topology, spec, max_results=max_results,
        )
    except BridgeError as exc:
        return json.dumps({"error": str(exc)})

    return json.dumps({
        "topology": topology,
        "candidates": [
            {
                "scoring": c.scoring,
                "core_shape": c.core_shape_name,
                "elapsed_s": c.elapsed_s,
            }
            for c in candidates
        ],
    }, indent=2)


def _design_bom(args: dict[str, Any]) -> str:
    from heaviside.catalogue import SelectionError, assemble_bom_from_tas
    from heaviside.pipeline.stress import StressDerivationError

    topology = args["topology"]
    spec = args["spec"]
    tas = args["tas"]

    try:
        result = assemble_bom_from_tas(tas, topology=topology, spec=spec)
    except (SelectionError, StressDerivationError) as exc:
        return json.dumps({"error": str(exc)})

    bom = []
    for stage in result.get("topology", {}).get("stages", []):
        for comp in stage.get("circuit", {}).get("components", []):
            prov = comp.get("selection_provenance")
            if isinstance(prov, dict):
                bom.append(prov)
    return json.dumps({"topology": topology, "bom": bom}, indent=2)


def _list_topologies() -> str:
    from heaviside.topologies.registry import CONVERTERS

    return json.dumps([
        {"name": e.name, "family": e.family}
        for e in CONVERTERS
    ], indent=2)


def _query_lessons(args: dict[str, Any]) -> str:
    from heaviside.pipeline.teacher import load_lessons, summarize_lessons

    lessons = load_lessons(
        topology=args.get("topology"),
        severity=args.get("severity"),
        max_age_days=args.get("max_age_days"),
    )
    return json.dumps({
        "count": len(lessons),
        "summary": summarize_lessons(lessons),
        "lessons": [
            {
                "topology": l.topology,
                "category": l.category,
                "severity": l.severity,
                "detail": l.detail,
                "suggestion": l.suggestion,
            }
            for l in lessons[:20]
        ],
    }, indent=2)


def _reverse_engineer(args: dict[str, Any]) -> str:
    from pathlib import Path
    from heaviside.pipeline.cre_pipeline import run_cre_pipeline

    reference = args["reference"]
    pdf_path = Path(args["pdf_path"]) if args.get("pdf_path") else None

    try:
        outcome = run_cre_pipeline(reference, pdf_path=pdf_path)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    return json.dumps({
        "reference": outcome.reference,
        "passed": outcome.passed,
        "ref_spec": outcome.ref_spec.__dict__ if outcome.ref_spec else None,
        "bom_count": len(outcome.ref_bom),
        "diagnostics": list(outcome.diagnostics),
    }, indent=2)


def _cross_reference(args: dict[str, Any]) -> str:
    from heaviside.pipeline.crossref_pipeline import run_crossref_pipeline

    try:
        outcome = run_crossref_pipeline(
            args["source_bom"],
            args["target_manufacturer"],
            circuit_context=args.get("circuit_context"),
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    return json.dumps({
        "target_manufacturer": outcome.target_manufacturer,
        "passed": outcome.passed,
        "components": [
            {
                "ref_des": c.ref_des,
                "original_mpn": c.original_mpn,
                "substitute_mpn": c.substitute_mpn,
                "status": c.status.value,
            }
            for c in outcome.components
        ],
        "diagnostics": list(outcome.diagnostics),
    }, indent=2)


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
