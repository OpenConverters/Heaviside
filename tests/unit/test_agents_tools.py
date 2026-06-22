"""Unit tests for :mod:`heaviside.agents.tools`.

Covers the Strands ``@tool`` wrappers around librarian / knowledge
primitives.  The point is to verify:

* the registry exposes every advertised tool,
* each wrapper carries a Strands ``tool_spec`` with the documented
  inputSchema,
* :func:`resolve_tools` rejects typos loudly (no silent drops),
* the JSON-payload wrappers serialize / deserialize correctly and
  re-raise underlying librarian exceptions unchanged.
"""

from __future__ import annotations

import json

import pytest

from heaviside.agents.tools import (
    AGENT_TOOLS,
    RAW_FUNCTIONS,
    TOOL_REGISTRY,
    resolve_tools,
)

EXPECTED_TOOL_NAMES = {
    "add_component",
    "validate_component",
    "component_exists",
    "list_categories",
    "audit_component",
    "audit_category",
    "audit_all",
    "read_knowledge",
    "get_pareto_magnetics",
    "crossref_capacitor",
    "crossref_resistor",
    "crossref_magnetic",
    "crossref_connector",
}


def test_registry_advertises_expected_tools() -> None:
    assert set(TOOL_REGISTRY) == EXPECTED_TOOL_NAMES


def test_agent_tools_list_matches_registry() -> None:
    assert len(AGENT_TOOLS) == len(TOOL_REGISTRY)
    # Same identity, registration order
    for tool, (_name, registered) in zip(AGENT_TOOLS, TOOL_REGISTRY.items(), strict=False):
        assert tool is registered


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOL_NAMES))
def test_every_tool_has_strands_spec(name: str) -> None:
    tool = TOOL_REGISTRY[name]
    assert hasattr(tool, "tool_spec"), f"{name} missing tool_spec"
    spec = tool.tool_spec
    assert spec["name"] == name
    assert spec.get("description"), f"{name} has empty description"
    schema = spec["inputSchema"]["json"]
    assert schema["type"] == "object"


def test_resolve_tools_returns_callables_in_order() -> None:
    resolved = resolve_tools(["component_exists", "add_component"])
    assert len(resolved) == 2
    assert resolved[0] is TOOL_REGISTRY["component_exists"]
    assert resolved[1] is TOOL_REGISTRY["add_component"]


def test_resolve_tools_raises_on_unknown_name() -> None:
    with pytest.raises(KeyError, match="unknown tool name"):
        resolve_tools(["component_exists", "no_such_tool"])


def test_list_categories_reports_writable_and_auditable() -> None:
    raw = RAW_FUNCTIONS["list_categories"]()
    assert set(raw["writable"]) >= {
        "mosfets",
        "diodes",
        "igbts",
        "capacitors",
        "resistors",
        "magnetics",
    }
    assert set(raw["auditable"]) >= {
        "mosfets",
        "diodes",
        "capacitors",
        "magnetics",
    }


def test_validate_component_rejects_bad_json() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        RAW_FUNCTIONS["validate_component"]("mosfets", "{not json")


def test_validate_component_runs_real_schema(tmp_path, monkeypatch) -> None:
    # A bare envelope should be rejected by the SAS schema for missing
    # required electrical fields.
    from heaviside.librarian import ValidationError

    payload = json.dumps({"mosfet": {"manufacturerInfo": {}}})
    with pytest.raises(ValidationError):
        RAW_FUNCTIONS["validate_component"]("mosfets", payload)


def test_audit_component_serializes_dataclass() -> None:
    payload = json.dumps(
        {
            "mosfet": {
                "manufacturerInfo": {
                    "name": "TestMfr",
                    "reference": "TEST-FET-001",
                    "datasheetInfo": {
                        "part": {"partNumber": "TEST-FET-001"},
                        "electrical": {},
                    },
                },
            },
        }
    )
    out = RAW_FUNCTIONS["audit_component"]("mosfets", payload)
    assert out["mpn"] == "TEST-FET-001"
    assert out["passed"] is False
    assert {g["field"] for g in out["critical_failures"]} >= {
        "outputCapacitance",
        "totalGateCharge",
        "gateThresholdVoltage",
    }


def test_read_knowledge_wrapper_returns_text() -> None:
    text = RAW_FUNCTIONS["read_knowledge"]("peas-schema")
    assert text.strip().startswith("#")


def test_add_component_rejects_non_json_payload() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        RAW_FUNCTIONS["add_component"]("mosfets", "{not-json")


def test_component_exists_wrapper_delegates() -> None:
    # Use a category whose NDJSON is known to be free of corrupt lines —
    # mosfets.ndjson has known pre-existing merge-conflict markers at
    # L2802/L2806/L2810 (gated via strict-xfail tests) which would
    # legitimately raise from component_exists.  resistors is clean.
    result = RAW_FUNCTIONS["component_exists"](
        "resistors",
        "ZZZ-NOT-A-REAL-MPN-XYZ-9999",
    )
    assert result is False


def test_raw_functions_matches_registry_keys() -> None:
    assert set(RAW_FUNCTIONS) == set(TOOL_REGISTRY)


def test_crossref_capacitor_filters_by_manufacturer_and_value(tmp_path, monkeypatch) -> None:
    """The crossref tools query TAS NDJSON: manufacturer substring match,
    optional value window, explicit truncation flag."""
    import json as _json

    def cap_row(mfr: str, mpn: str, farads: float) -> str:
        return _json.dumps(
            {
                "capacitor": {
                    "manufacturerInfo": {
                        "name": mfr,
                        "reference": mpn,
                        "datasheetInfo": {
                            "electrical": {
                                "capacitance": {"nominal": farads},
                                "ratedVoltage": 50.0,
                            },
                        },
                    },
                }
            }
        )

    (tmp_path / "capacitors.ndjson").write_text(
        "\n".join(
            [
                cap_row("Würth Elektronik", "WCAP-1", 22e-6),
                cap_row("Würth Elektronik", "WCAP-2", 47e-6),
                cap_row("Murata", "GRM-1", 22e-6),
            ]
        )
        + "\n"
    )
    monkeypatch.setenv("HEAVISIDE_TAS_DATA_DIR", str(tmp_path))

    out = _json.loads(
        RAW_FUNCTIONS["crossref_capacitor"]("wurth", capacitance=22e-6, value_tolerance_pct=20.0)
    )
    assert out["total_matches"] == 1
    assert out["candidates"][0]["mpn"] == "WCAP-1"
    assert out["truncated"] is False

    # No value filter -> both Würth rows, Murata still excluded.
    out = _json.loads(RAW_FUNCTIONS["crossref_capacitor"]("wurth"))
    assert {c["mpn"] for c in out["candidates"]} == {"WCAP-1", "WCAP-2"}


def test_crossref_search_rejects_unknown_category(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HEAVISIDE_TAS_DATA_DIR", str(tmp_path))
    from heaviside.agents.tools import _crossref_search_impl

    with pytest.raises(ValueError, match="unknown category"):
        _crossref_search_impl("flux_capacitor", "wurth")
