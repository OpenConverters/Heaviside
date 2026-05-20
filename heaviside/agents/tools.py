"""Strands-Agent tool registry for Heaviside.

Each callable here is a thin ``@strands.tool`` wrapper around a
function from :mod:`heaviside.librarian` or :mod:`heaviside.knowledge`.

Why the indirection?

* ``@strands.tool`` builds the JSON-schema input spec from the
  wrapped function's type hints + docstring, then attaches a
  ``tool_spec`` descriptor that ``strands.Agent`` discovers at
  construction time.  We must not let Strands see the raw librarian
  primitives — they accept ``dict[str, Any]`` payloads with no
  field-level types, which would surface as opaque JSON blobs in
  the agent UI.  The wrappers serialize/deserialize so the agent
  sees a clean schema, and the librarian still gets validated dicts.
* It keeps the librarian importable without ``strands`` installed
  (tests / batch jobs / CI), and confines the Strands dependency
  to this file plus :mod:`heaviside.agents.factory`.

The raw, un-decorated wrapper functions are exposed via
:data:`RAW_FUNCTIONS` for testing — Strands' ``DecoratedFunctionTool``
hides the underlying callable, so tests that want to assert on the
business logic call ``RAW_FUNCTIONS[name](...)`` directly.

Adding a tool: write a plain ``_xxx_impl`` function below, decorate
it into ``xxx``, and register both in :data:`TOOL_REGISTRY` and
:data:`RAW_FUNCTIONS`.  The factory rejects unknown tool names at
agent-construction time.
"""

from __future__ import annotations

import json
from typing import Any

from strands import tool

from heaviside.knowledge import read_knowledge as _read_knowledge
from heaviside.librarian import (
    AUDITABLE_CATEGORIES,
    CATEGORIES,
    SCHEMA_MAP,
    add_component as _add_component,
    audit_all as _audit_all,
    audit_category as _audit_category,
    audit_component as _audit_component,
    component_exists as _component_exists,
    validate_component as _validate_component,
)

__all__ = [
    "AGENT_TOOLS",
    "RAW_FUNCTIONS",
    "TOOL_REGISTRY",
    "resolve_tools",
    "add_component",
    "audit_all",
    "audit_category",
    "audit_component",
    "component_exists",
    "list_categories",
    "read_knowledge",
    "validate_component",
]


# ---------------------------------------------------------------------------
# Raw wrapper implementations (testable; decorated below into Strands tools)
# ---------------------------------------------------------------------------


def _add_component_impl(category: str, component_json: str) -> str:
    """Validate and append a component to ``TAS/data/<category>.ndjson``.

    Args:
        category: One of the writable TAS categories
            (``mosfets``, ``diodes``, ``igbts``, ``capacitors``,
            ``resistors``, ``magnetics``).
        component_json: The full component record as a JSON string,
            including its discriminator envelope
            (e.g. ``{"mosfet": {"manufacturerInfo": {...}}}``).
            All numeric fields must be SI base units (V, A, F, H, Ω).

    Returns:
        A short status string. Raises on validation failure,
        duplicate MPN, or anonymous component (no extractable MPN).
    """
    try:
        component = json.loads(component_json)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"add_component: component_json is not valid JSON: {exc.msg} "
            f"(line {exc.lineno}, col {exc.colno})"
        ) from exc
    _add_component(category, component)
    return f"add_component: appended one row to {category}.ndjson"


def _validate_component_impl(category: str, component_json: str) -> str:
    """Run schema validation against a candidate component without writing.

    Use this before :func:`add_component` whenever you are iterating
    on field extraction — it returns a structured pass/fail without
    locking the NDJSON.

    Args:
        category: One of the writable TAS categories.
        component_json: Candidate record as a JSON string.

    Returns:
        ``"valid"`` if the schema accepts the record; otherwise
        raises :class:`heaviside.librarian.ValidationError` with the
        offending JSON-pointer path.
    """
    try:
        component = json.loads(component_json)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"validate_component: not valid JSON: {exc.msg}"
        ) from exc
    _validate_component(category, component)
    return "valid"


def _component_exists_impl(category: str, part_number: str) -> bool:
    """Return ``True`` iff an MPN is already present in ``category``.

    Case-insensitive MPN match. Always call this *before* searching
    online for a new candidate — it is the dedup gate that prevented
    the April 2026 924-duplicate incident.

    Args:
        category: One of the writable TAS categories.
        part_number: Manufacturer part number (case-insensitive).
    """
    return _component_exists(category, part_number)


def _list_categories_impl() -> dict[str, list[str]]:
    """List the categories the librarian can write and audit.

    Returns a dict with two keys:

    * ``"writable"`` — categories with a schema registered in
      :data:`heaviside.librarian.SCHEMA_MAP`.  Anything else is
      refused at write time.
    * ``"auditable"`` — categories the pipeline-critical-field
      auditor knows about.
    """
    return {
        "writable": sorted(SCHEMA_MAP.keys()),
        "auditable": sorted(AUDITABLE_CATEGORIES),
        "known": sorted(CATEGORIES),
    }


def _audit_component_impl(category: str, component_json: str) -> dict[str, Any]:
    """Audit a single component dict against pipeline-critical fields.

    Args:
        category: One of the auditable categories.
        component_json: Component record as a JSON string.

    Returns:
        Dict with keys ``mpn``, ``passed`` (bool), ``critical_failures``,
        ``required_failures`` (lists of ``{field, status, value}``).
        See :class:`heaviside.librarian.ComponentAudit`.
    """
    try:
        component = json.loads(component_json)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"audit_component: not valid JSON: {exc.msg}"
        ) from exc
    result = _audit_component(component, category)
    return _serialize_component_audit(result)


def _audit_category_impl(
    category: str,
    sample: int | None = None,
    on_corruption: str = "report",
) -> dict[str, Any]:
    """Audit every row of ``TAS/data/<category>.ndjson`` (or first ``sample``).

    Args:
        category: One of the auditable categories.
        sample: Limit to first N lines. ``None`` audits the full file.
        on_corruption: ``"raise"`` to stop on first corrupt line
            (strict mode, mirrors writer contract); ``"report"`` to
            surface every corrupt line as a structured entry and
            keep going. Default ``"report"`` so the agent can render
            a complete picture without a single bad line aborting
            the audit — surface every CorruptLine in your output.

    Returns:
        Dict with keys ``category``, ``total``, ``passed``,
        ``pass_pct``, ``critical_field_misses`` (field → count),
        ``required_field_misses``, ``failures`` (list, capped),
        ``corrupt_lines``.
    """
    report = _audit_category(
        category, sample=sample, on_corruption=on_corruption,
    )
    return _serialize_category_audit(report)


def _audit_all_impl(
    sample: int | None = None,
    on_corruption: str = "report",
) -> dict[str, dict[str, Any]]:
    """Run :func:`audit_category` across every auditable category.

    Same arguments as :func:`audit_category` — they are forwarded
    per-category.  Returns a dict keyed by category name.
    """
    reports = _audit_all(sample=sample, on_corruption=on_corruption)
    return {cat: _serialize_category_audit(rep) for cat, rep in reports.items()}


def _read_knowledge_impl(name: str) -> str:
    """Return the text of a distilled knowledge file by stem.

    Examples: ``"peas-schema"``, ``"sas-schema"``, ``"cas-schema"``,
    ``"ras-schema"``, ``"mas-schema-summary"``, ``"tas-structure"``.

    These are static reference documents used as agent context;
    they encode schema field names, envelope shapes, and
    historical-incident lessons.  Always preload the relevant
    schema knowledge before writing or auditing a category.
    """
    return _read_knowledge(name)


# ---------------------------------------------------------------------------
# Internal serializers (dataclass → JSON-friendly dict)
# ---------------------------------------------------------------------------


def _serialize_field_gap(gap: Any) -> dict[str, Any]:
    return {
        "field": gap.field,
        "status": gap.status,
    }


def _serialize_component_audit(audit: Any) -> dict[str, Any]:
    return {
        "mpn": audit.mpn,
        "category": audit.category,
        "passed": audit.passed,
        "line": audit.line,
        "critical_failures": [_serialize_field_gap(g) for g in audit.critical_failures],
        "required_failures": [_serialize_field_gap(g) for g in audit.required_failures],
    }


def _serialize_corrupt_line(c: Any) -> dict[str, Any]:
    return {"line": c.line, "reason": c.reason}


def _serialize_category_audit(report: Any, *, failure_cap: int = 50) -> dict[str, Any]:
    total = report.total
    passed = report.passed
    pct = (100.0 * passed / total) if total else 0.0
    return {
        "category": report.category,
        "total": total,
        "passed": passed,
        "pass_pct": round(pct, 2),
        "critical_field_misses": dict(report.critical_field_misses),
        "required_field_misses": dict(report.required_field_misses),
        "failures": [
            _serialize_component_audit(f) for f in report.failures[:failure_cap]
        ],
        "failures_truncated": max(0, len(report.failures) - failure_cap),
        "corrupt_lines": [_serialize_corrupt_line(c) for c in report.corrupt_lines],
    }


# ---------------------------------------------------------------------------
# Strands decoration (the agent-facing surface)
# ---------------------------------------------------------------------------


add_component = tool(_add_component_impl, name="add_component")
validate_component = tool(_validate_component_impl, name="validate_component")
component_exists = tool(_component_exists_impl, name="component_exists")
list_categories = tool(_list_categories_impl, name="list_categories")
audit_component = tool(_audit_component_impl, name="audit_component")
audit_category = tool(_audit_category_impl, name="audit_category")
audit_all = tool(_audit_all_impl, name="audit_all")
read_knowledge = tool(_read_knowledge_impl, name="read_knowledge")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


#: Map of tool name (as referenced from agent prompt frontmatter) to the
#: ``@tool``-decorated callable. Agent prompts list a subset in
#: ``allowed_tools``; :func:`resolve_tools` validates the names.
TOOL_REGISTRY: dict[str, Any] = {
    "add_component": add_component,
    "validate_component": validate_component,
    "component_exists": component_exists,
    "list_categories": list_categories,
    "audit_component": audit_component,
    "audit_category": audit_category,
    "audit_all": audit_all,
    "read_knowledge": read_knowledge,
}


#: Plain (un-decorated) versions of the same wrappers — useful in tests
#: where Strands' ``DecoratedFunctionTool`` proxy makes it awkward to
#: invoke the underlying business logic directly.
RAW_FUNCTIONS: dict[str, Any] = {
    "add_component": _add_component_impl,
    "validate_component": _validate_component_impl,
    "component_exists": _component_exists_impl,
    "list_categories": _list_categories_impl,
    "audit_component": _audit_component_impl,
    "audit_category": _audit_category_impl,
    "audit_all": _audit_all_impl,
    "read_knowledge": _read_knowledge_impl,
}


#: Convenience: all tools, in registration order, as a flat list. Useful
#: for the rare agent that wants the kitchen sink (the factory still
#: routes through :data:`TOOL_REGISTRY` for name resolution).
AGENT_TOOLS: list[Any] = list(TOOL_REGISTRY.values())


def resolve_tools(tool_names: list[str]) -> list[Any]:
    """Resolve a list of tool names to their decorated callables.

    Raises
    ------
    KeyError
        If any name is not in :data:`TOOL_REGISTRY`.  We do not
        silently drop unknown names — a typo in agent frontmatter
        is a real bug that would otherwise produce an agent missing
        a tool it expects to call.
    """
    missing = [n for n in tool_names if n not in TOOL_REGISTRY]
    if missing:
        raise KeyError(
            f"resolve_tools: unknown tool name(s) {missing!r}.  "
            f"Registered: {sorted(TOOL_REGISTRY)}"
        )
    return [TOOL_REGISTRY[n] for n in tool_names]
