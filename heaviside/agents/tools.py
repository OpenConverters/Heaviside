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
)
from heaviside.librarian import (
    add_component as _add_component,
)
from heaviside.librarian import (
    audit_all as _audit_all,
)
from heaviside.librarian import (
    audit_category as _audit_category,
)
from heaviside.librarian import (
    audit_component as _audit_component,
)
from heaviside.librarian import (
    component_exists as _component_exists,
)
from heaviside.librarian import (
    validate_component as _validate_component,
)

__all__ = [
    "AGENT_TOOLS",
    "RAW_FUNCTIONS",
    "TOOL_REGISTRY",
    "add_component",
    "audit_all",
    "audit_category",
    "audit_component",
    "component_exists",
    "crossref_capacitor",
    "crossref_magnetic",
    "crossref_resistor",
    "list_categories",
    "read_knowledge",
    "resolve_tools",
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
        raise ValueError(f"validate_component: not valid JSON: {exc.msg}") from exc
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
        raise ValueError(f"audit_component: not valid JSON: {exc.msg}") from exc
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
        category,
        sample=sample,
        on_corruption=on_corruption,
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
        "failures": [_serialize_component_audit(f) for f in report.failures[:failure_cap]],
        "failures_truncated": max(0, len(report.failures) - failure_cap),
        "corrupt_lines": [_serialize_corrupt_line(c) for c in report.corrupt_lines],
    }


# ---------------------------------------------------------------------------
# Magnetic Pareto-front exploration
# ---------------------------------------------------------------------------


def _get_pareto_magnetics_impl(
    topology: str,
    spec_json: str,
    n_candidates: int = 5,
) -> str:
    """Return a Pareto front of fast-mode magnetic candidates for a converter spec.

    Calls PyOM's ``calculate_advised_magnetics_fast`` (analytical
    gap/turns + area-product filtering + Steinmetz losses — much
    faster than the full design loop, suitable for design-space
    exploration). The result is a JSON-encoded summary table the
    calling agent can read without parsing MAS directly.

    Args:
        topology: Canonical topology name (e.g. ``"buck"``, ``"flyback"``).
        spec_json: Converter spec as a JSON string (the same shape that
            ``heaviside design`` consumes — ``inputVoltage``,
            ``operatingPoints``, etc.).
        n_candidates: Number of Pareto candidates to return (default 5,
            cap 20 — beyond that PyOM is back into the slow regime).

    Returns:
        JSON string of ``{"candidates": [...]}`` where each candidate
        carries ``index``, ``scoring`` (ascending losses), ``shape``,
        ``material``, ``has_gap``, ``n_windings``, ``n_turns_primary``,
        ``effective_area_m2``, ``effective_volume_m3``. The agent picks
        one by index and then calls a separate tool (or the bridge
        directly) to commit it as the design's main magnetic.
    """
    from heaviside.agents.magnetic_picker import pareto_summary
    from heaviside.bridge import design_magnetics_fast

    spec = json.loads(spec_json)
    n = max(1, min(int(n_candidates), 20))
    designs = design_magnetics_fast(topology, spec, max_results=n)
    return json.dumps({"candidates": pareto_summary(designs)})


# ---------------------------------------------------------------------------
# Cross-reference TAS queries (Otto's no-substitute challenges)
# ---------------------------------------------------------------------------


def _capacitor_technology_family(technology: str | None) -> str | None:
    """Collapse a CAS technology string to a chemistry FAMILY. Canonical
    implementation lives in crossref_pipeline (shared with the prefetch
    ranker); re-exported here for the crossref tools."""
    from heaviside.pipeline.crossref_pipeline import (
        _capacitor_technology_family as _impl,
    )

    return _impl(technology)


def _extract_capacitor_technology(env: dict[str, Any]) -> str | None:
    try:
        return env["capacitor"]["manufacturerInfo"]["datasheetInfo"]["part"].get(
            "technology"
        )
    except (KeyError, TypeError, AttributeError):
        return None


def _extract_capacitor_voltage(env: dict[str, Any]) -> float | None:
    try:
        v = env["capacitor"]["manufacturerInfo"]["datasheetInfo"]["electrical"].get(
            "ratedVoltage"
        )
        return float(v) if isinstance(v, (int, float)) else None
    except (KeyError, TypeError, AttributeError):
        return None


def _crossref_search_impl(
    category: str,
    target_manufacturer: str,
    value: float | None = None,
    value_tolerance_pct: float = 50.0,
    max_results: int = 50,
    technology: str | None = None,
    min_voltage: float | None = None,
    max_esr: float | None = None,
) -> str:
    """Shared TAS query behind the per-category crossref tools.

    Scans ``TAS/data/<category>.ndjson`` for parts from the target
    manufacturer, optionally filtered to ``value`` ± ``value_tolerance_pct``
    (candidates whose value cannot be read are kept — dropping them would
    hide exactly the parts Otto exists to surface). Results are the same
    per-category summaries the cross-referencer sees, sorted by value
    proximity when a value is given. Truncation is reported explicitly.
    """
    import os
    from pathlib import Path

    from heaviside.catalogue._reader import iter_envelopes
    from heaviside.pipeline.crossref_pipeline import (
        _extract_manufacturer,
        _extract_value,
        _normalize_manufacturer,
        _summarize_candidate,
    )

    category_files = {
        "capacitor": "capacitors.ndjson",
        "resistor": "resistors.ndjson",
        "magnetic": "magnetics.ndjson",
        "connector": "connectors.ndjson",
        "analog": "analog_ics.ndjson",
    }
    if category not in category_files:
        raise ValueError(
            f"crossref search: unknown category {category!r}; known: {sorted(category_files)}"
        )

    tas_dir = Path(
        os.environ.get(
            "HEAVISIDE_TAS_DATA_DIR",
            str(Path(__file__).resolve().parents[2] / "TAS" / "data"),
        )
    )
    path = tas_dir / category_files[category]

    target = _normalize_manufacturer(target_manufacturer)
    matches: list[tuple[float | None, dict[str, Any]]] = []
    for _lineno, env in iter_envelopes(path):
        mfr = _extract_manufacturer(env, category)
        if not mfr or target not in _normalize_manufacturer(mfr):
            continue
        cand_value = _extract_value(env, category)
        if value is not None and cand_value is not None:
            lo = value * (1.0 - value_tolerance_pct / 100.0)
            hi = value * (1.0 + value_tolerance_pct / 100.0)
            if not (lo <= cand_value <= hi):
                continue
        if technology is not None and category == "capacitor":
            want = _capacitor_technology_family(technology)
            cand_fam = _capacitor_technology_family(_extract_capacitor_technology(env))
            # drop only readable, DIFFERENT-family candidates (e.g. supercap
            # / electrolytic when a ceramic was asked for); keep same-family
            # and unreadable ones so nothing real is hidden.
            if want is not None and cand_fam is not None and cand_fam != want:
                continue
        if min_voltage is not None and category == "capacitor":
            cand_v = _extract_capacitor_voltage(env)
            if cand_v is not None and cand_v < min_voltage:
                continue
        if max_esr is not None and category == "capacitor":
            try:
                cand_esr = env["capacitor"]["manufacturerInfo"]["datasheetInfo"]["electrical"].get("esr")
                if cand_esr is not None and float(cand_esr) > max_esr:
                    continue
            except (KeyError, TypeError, ValueError):
                pass  # keep candidates whose ESR is unknown
        matches.append((cand_value, env))

    if value is not None:
        matches.sort(key=lambda m: abs(m[0] - value) if m[0] is not None else float("inf"))

    n = max(1, min(int(max_results), 200))
    summaries = [_summarize_candidate(env, category) for _v, env in matches[:n]]
    return json.dumps(
        {
            "category": category,
            "target_manufacturer": target_manufacturer,
            "total_matches": len(matches),
            "returned": len(summaries),
            "truncated": len(matches) > n,
            "candidates": summaries,
        }
    )


def _crossref_capacitor_impl(
    target_manufacturer: str,
    capacitance: float | None = None,
    value_tolerance_pct: float = 50.0,
    max_results: int = 50,
    technology: str | None = None,
    min_voltage: float | None = None,
    max_esr: float | None = None,
) -> str:
    """Query TAS capacitors from a target manufacturer.

    Args:
        target_manufacturer: Manufacturer to search (substring match,
            case/punctuation-insensitive — "Würth", "wurth elektronik"
            and "WURTH" all hit the same rows).
        capacitance: Optional centre capacitance in farads (SI). When
            given, results are limited to ±``value_tolerance_pct`` and
            sorted by proximity.
        value_tolerance_pct: Half-width of the value window, percent.
        max_results: Cap on returned candidates (truncation is flagged
            in the response, never silent).
        technology: Optional chemistry family of the ORIGINAL part so the
            search stays in-kind — pass the original's technology
            ("ceramic"/"X7R"/"ceramic-class-2", "aluminum-electrolytic",
            "tantalum", "film", ...). Candidates of a different, readable
            family are dropped; same-family and unreadable ones are kept.
            ALWAYS pass this when the original technology is known — it is
            what prevents ceramic queries returning supercaps.
        min_voltage: Optional minimum rated voltage (V). Candidates below
            it are dropped (a substitute must meet the working voltage).
        max_esr: Optional maximum ESR in ohms (SI). Candidates whose ESR
            is known and exceeds this limit are dropped. Candidates with
            no ESR data are kept (never silently exclude unknowns).
            Derive this from the original part: look up the original MPN
            via ``component_exists``, read its ``esr`` field, then pass
            ``max_esr = original_esr * 1.2`` (20% headroom per the
            cross-reference spec).

    Returns:
        JSON string with ``total_matches``, ``candidates`` (mpn,
        capacitance, voltage, esr, package, technology) and a
        ``truncated`` flag.
    """
    return _crossref_search_impl(
        "capacitor",
        target_manufacturer,
        value=capacitance,
        value_tolerance_pct=value_tolerance_pct,
        max_results=max_results,
        technology=technology,
        min_voltage=min_voltage,
        max_esr=max_esr,
    )


def _crossref_resistor_impl(
    target_manufacturer: str,
    resistance: float | None = None,
    value_tolerance_pct: float = 50.0,
    max_results: int = 50,
) -> str:
    """Query TAS resistors from a target manufacturer.

    Args:
        target_manufacturer: Manufacturer to search (substring match,
            case/punctuation-insensitive).
        resistance: Optional centre resistance in ohms (SI). When given,
            results are limited to ±``value_tolerance_pct`` and sorted
            by proximity.
        value_tolerance_pct: Half-width of the value window, percent.
        max_results: Cap on returned candidates (truncation is flagged
            in the response, never silent).

    Returns:
        JSON string with ``total_matches``, ``candidates`` (mpn,
        resistance, tolerance, power_rating, package) and a
        ``truncated`` flag.
    """
    return _crossref_search_impl(
        "resistor",
        target_manufacturer,
        value=resistance,
        value_tolerance_pct=value_tolerance_pct,
        max_results=max_results,
    )


def _crossref_magnetic_impl(
    target_manufacturer: str,
    inductance: float | None = None,
    value_tolerance_pct: float = 50.0,
    max_results: int = 50,
) -> str:
    """Query TAS magnetics (inductors) from a target manufacturer.

    Args:
        target_manufacturer: Manufacturer to search (substring match,
            case/punctuation-insensitive).
        inductance: Optional centre inductance in henries (SI). When
            given, results are limited to ±``value_tolerance_pct`` and
            sorted by proximity.
        value_tolerance_pct: Half-width of the value window, percent.
        max_results: Cap on returned candidates (truncation is flagged
            in the response, never silent).

    Returns:
        JSON string with ``total_matches``, ``candidates`` (mpn,
        inductance, saturation_current, dcr, package) and a
        ``truncated`` flag.
    """
    return _crossref_search_impl(
        "magnetic",
        target_manufacturer,
        value=inductance,
        value_tolerance_pct=value_tolerance_pct,
        max_results=max_results,
    )


def _crossref_connector_impl(
    target_manufacturer: str,
    rated_current_a: float | None = None,
    value_tolerance_pct: float = 50.0,
    max_results: int = 50,
    min_voltage: float | None = None,
    family: str | None = None,
) -> str:
    """Query the internal connector DB from a target manufacturer.

    Args:
        target_manufacturer: Manufacturer to search (substring match,
            case/punctuation-insensitive — "Würth", "wurth elektronik"
            and "WURTH" all hit the same rows).
        rated_current_a: Optional rated current per contact in amperes
            (SI). When given, results are limited to ±``value_tolerance_pct``
            and sorted by proximity.
        value_tolerance_pct: Half-width of the current window, percent.
        max_results: Cap on returned candidates (truncation is flagged
            in the response, never silent).
        min_voltage: Optional minimum rated voltage (V). Candidates
            rated below this are dropped.
        family: Optional connector family filter — one of "terminalBlock",
            "pinHeaderSocket", "boardToBoard", "wireToBoard", "fpcFfc",
            "cardEdge", "circular", "rf", "dataInterface", "power".
            When given, only that family is returned.

    Returns:
        JSON string with ``total_matches``, ``candidates`` (mpn, family,
        positions, pitch_mm, rated_current_A, rated_voltage_V, mounting,
        polarity) and a ``truncated`` flag.
    """
    import json as _json
    import os
    from pathlib import Path

    from heaviside.catalogue._reader import iter_envelopes
    from heaviside.pipeline.crossref_pipeline import (
        _extract_manufacturer,
        _extract_value,
        _normalize_manufacturer,
        _summarize_candidate,
    )

    tas_dir = Path(
        os.environ.get(
            "HEAVISIDE_TAS_DATA_DIR",
            str(Path(__file__).resolve().parents[2] / "TAS" / "data"),
        )
    )
    path = tas_dir / "connectors.ndjson"

    target = _normalize_manufacturer(target_manufacturer)
    matches: list[tuple[float | None, dict]] = []
    for _lineno, env in iter_envelopes(path):
        mfr = _extract_manufacturer(env, "connector")
        if not mfr or target not in _normalize_manufacturer(mfr):
            continue
        # Family filter
        if family is not None:
            try:
                cand_family = (
                    env["connector"]["manufacturerInfo"]["datasheetInfo"]
                    ["familyDetails"]["family"]
                )
                if cand_family != family:
                    continue
            except (KeyError, TypeError):
                pass
        # Voltage filter
        if min_voltage is not None:
            try:
                cand_v = float(
                    env["connector"]["manufacturerInfo"]["datasheetInfo"]
                    ["electrical"]["ratedVoltage"]
                )
                if cand_v < min_voltage:
                    continue
            except (KeyError, TypeError, ValueError):
                pass
        # Current window filter
        cand_current = _extract_value(env, "connector")
        if rated_current_a is not None and cand_current is not None:
            lo = rated_current_a * (1.0 - value_tolerance_pct / 100.0)
            hi = rated_current_a * (1.0 + value_tolerance_pct / 100.0)
            if not (lo <= cand_current <= hi):
                continue
        matches.append((cand_current, env))

    if rated_current_a is not None:
        matches.sort(
            key=lambda m: abs(m[0] - rated_current_a)
            if m[0] is not None
            else float("inf")
        )

    n = max(1, min(int(max_results), 200))
    summaries = [_summarize_candidate(env, "connector") for _v, env in matches[:n]]
    return _json.dumps(
        {
            "category": "connector",
            "target_manufacturer": target_manufacturer,
            "total_matches": len(matches),
            "returned": len(summaries),
            "truncated": len(matches) > n,
            "candidates": summaries,
        }
    )


def _crossref_analog_impl(
    target_manufacturer: str,
    subtype: str | None = None,
    channels: int | None = None,
    min_supply_v: float | None = None,
    max_supply_v: float | None = None,
    max_results: int = 50,
) -> str:
    """Query the internal analog-IC DB from a target manufacturer.

    Args:
        target_manufacturer: Manufacturer to search (substring match,
            case/punctuation-insensitive).
        subtype: Optional FUNCTION filter — one of "operationalAmplifier",
            "comparator", "instrumentationAmplifier", "differenceAmplifier",
            "programmableGainAmplifier", "adc", "dac", "analogSwitch",
            "multiplexer", "multiplier". ALWAYS pass it when the original's
            function is known — an op-amp is never a comparator substitute.
        channels: Optional exact channel count (single=1, dual=2, quad=4).
        min_supply_v: Candidates whose MAXIMUM supply voltage is below this
            are dropped (the substitute must reach the original's rail).
        max_supply_v: Candidates whose MINIMUM supply voltage is above this
            are dropped (the substitute must still run at the low rail).
        max_results: Cap on returned candidates (truncation is flagged in
            the response, never silent).

    Returns:
        JSON string with ``total_matches``, ``candidates`` (mpn, subtype,
        channels, supply range, gbw, slew_rate, input_offset_voltage,
        package, …) and a ``truncated`` flag.
    """
    import json as _json
    import os
    from pathlib import Path

    from heaviside.catalogue._reader import iter_envelopes
    from heaviside.pipeline.crossref_pipeline import (
        _analog_attrs,
        _extract_manufacturer,
        _normalize_manufacturer,
        _summarize_candidate,
    )

    tas_dir = Path(
        os.environ.get(
            "HEAVISIDE_TAS_DATA_DIR",
            str(Path(__file__).resolve().parents[2] / "TAS" / "data"),
        )
    )
    path = tas_dir / "analog_ics.ndjson"

    target = _normalize_manufacturer(target_manufacturer)
    matches: list[dict] = []
    for _lineno, env in iter_envelopes(path):
        mfr = _extract_manufacturer(env, "analog")
        if not mfr or target not in _normalize_manufacturer(mfr):
            continue
        attrs = _analog_attrs(env)
        if subtype is not None and attrs.get("subtype") != subtype:
            continue
        if channels is not None and attrs.get("channels") not in (None, channels):
            continue
        if (
            min_supply_v is not None
            and attrs.get("supply_max") is not None
            and float(attrs["supply_max"]) < min_supply_v
        ):
            continue
        if (
            max_supply_v is not None
            and attrs.get("supply_min") is not None
            and float(attrs["supply_min"]) > max_supply_v
        ):
            continue
        matches.append(env)

    n = max(1, min(int(max_results), 200))
    summaries = [_summarize_candidate(env, "analog") for env in matches[:n]]
    return _json.dumps(
        {
            "category": "analog",
            "target_manufacturer": target_manufacturer,
            "total_matches": len(matches),
            "returned": len(summaries),
            "truncated": len(matches) > n,
            "candidates": summaries,
        }
    )


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
get_pareto_magnetics = tool(_get_pareto_magnetics_impl, name="get_pareto_magnetics")
crossref_capacitor = tool(_crossref_capacitor_impl, name="crossref_capacitor")
crossref_resistor = tool(_crossref_resistor_impl, name="crossref_resistor")
crossref_magnetic = tool(_crossref_magnetic_impl, name="crossref_magnetic")
crossref_connector = tool(_crossref_connector_impl, name="crossref_connector")
crossref_analog = tool(_crossref_analog_impl, name="crossref_analog")


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
    "get_pareto_magnetics": get_pareto_magnetics,
    "crossref_capacitor": crossref_capacitor,
    "crossref_resistor": crossref_resistor,
    "crossref_magnetic": crossref_magnetic,
    "crossref_connector": crossref_connector,
    "crossref_analog": crossref_analog,
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
    "get_pareto_magnetics": _get_pareto_magnetics_impl,
    "crossref_capacitor": _crossref_capacitor_impl,
    "crossref_resistor": _crossref_resistor_impl,
    "crossref_magnetic": _crossref_magnetic_impl,
    "crossref_connector": _crossref_connector_impl,
    "crossref_analog": _crossref_analog_impl,
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
            f"resolve_tools: unknown tool name(s) {missing!r}.  Registered: {sorted(TOOL_REGISTRY)}"
        )
    return [TOOL_REGISTRY[n] for n in tool_names]
