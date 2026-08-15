"""Every MCP tool result must satisfy Moebius's pipeline contract (ABT #741).

Moebius validates each payload crossing the boundary against a closed schema
and RAISES on a violation — its /api/tool returns 502, which means a
non-conforming tool's widget cannot mount at all. Before this work all six
Heaviside tools were rejected on `mode is a required property`, so the three
widget-bearing ones (design_magnetic, design_bom, cross_reference) were
unreachable from the GUI while the conversational path carried on working.
That asymmetry is easy to miss by hand, hence a test.

The agent-driven tools are exercised with their pipelines mocked. That is the
point rather than a shortcut: the SHAPE of the payload is this module's
responsibility, and a test that needed a 15-minute LLM run (measured, see ABT
#758) to check a dict's keys would never be run.

The schema lives in the sibling moebius-orchestrator checkout. When it is not
present the structural assertions still run — the contract's rules are restated
here as explicit checks rather than being skipped entirely, because "the schema
file was missing so we asserted nothing" is how a suite goes quietly green.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from heaviside import mcp_server as ms

_SCHEMA = (Path(__file__).resolve().parents[2]
           / "moebius-orchestrator" / "contracts" / "pipeline_result.json")
_SCHEMA_ALT = (Path.home() / "wuerth" / "moebius-orchestrator"
               / "contracts" / "pipeline_result.json")


def _validate(payload: dict) -> None:
    """Against the real schema when it is reachable; structurally always."""
    assert isinstance(payload.get("mode"), str) and payload["mode"], (
        "every result needs a `mode` discriminator — consumers branch on it "
        "rather than sniffing which fields happen to be present"
    )
    path = _SCHEMA if _SCHEMA.exists() else _SCHEMA_ALT
    if not path.exists():
        pytest.skip(f"contract schema not found at {_SCHEMA} or {_SCHEMA_ALT}")
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(path.read_text(encoding="utf-8"))
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(payload),
                    key=lambda e: list(e.path))
    assert not errors, "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:4])


def _structured(result) -> dict:
    assert result.structuredContent is not None, (
        "a plain dict return gives FastMCP no output schema and emits NO "
        "structuredContent — the tool looks healthy while every consumer gets prose"
    )
    return result.structuredContent


def test_list_topologies_is_a_catalogue() -> None:
    payload = _structured(ms.list_topologies())
    assert payload["mode"] == "catalogue"
    _validate(payload)


def test_query_lessons_is_cited_passages() -> None:
    payload = _structured(ms.query_lessons(severity="error"))
    assert payload["mode"] == "passages"
    _validate(payload)
    for p in payload["passages"]:
        # The citation is the whole value of a retrieval result: quoted without
        # its source, a lesson is indistinguishable from a design rule.
        assert p["source"] and isinstance(p["line"], int) and p["line"] >= 1
        assert p["tier"] == "lesson", "a lesson is one run on one date, never a rule"


def test_lesson_detail_may_be_a_dict_and_still_renders(monkeypatch) -> None:
    """`Lesson.detail` is declared `str` and is a dict on 6,440 of 7,436 stored
    records (the crossref_objection lessons carry {ref_des, issue, details}).
    Every previous reader interpolated it into an f-string, which stringifies
    anything, so nothing caught it — the first code to concatenate it raised."""
    lesson = SimpleNamespace(
        id="L1", topology="buck", category="crossref_objection", severity="error",
        detail={"ref_des": "C1", "issue": "voltage", "details": "50V vs 25V"},
        suggestion="raise the rating",
    )
    text = ms._lesson_text(lesson)
    assert "ref_des: C1" in text and "raise the rating" in text
    assert "{" not in text, "a passage is meant to be read, not to be a Python repr"


def test_cross_reference_is_a_bom_not_a_ranked_list(monkeypatch) -> None:
    """A whole-BOM re-source is N questions with one answer each, so it cannot
    be a `crossref` result — that branch carries ONE `original`."""
    outcome = SimpleNamespace(
        passed=False,
        diagnostics=["otto challenge batch failed: no API key"],
        components=[
            SimpleNamespace(ref_des="Q1", original_mpn="IRFZ44N",
                            substitute_mpn="WSM-1", status=SimpleNamespace(value="recommended")),
            SimpleNamespace(ref_des="D1", original_mpn="STPS3045",
                            substitute_mpn=None, status=SimpleNamespace(value="no_substitute")),
        ],
    )
    monkeypatch.setattr(
        "heaviside.pipeline.crossref_pipeline.run_crossref_pipeline",
        lambda *a, **k: outcome,
    )
    payload = _structured(ms.cross_reference(
        source_bom=[ms.BomLine(ref_des="Q1", original_mpn="IRFZ44N"),
                    ms.BomLine(ref_des="D1", original_mpn="STPS3045")],
        target_manufacturer="Wurth"))

    assert payload["mode"] == "bom"
    _validate(payload)
    assert payload["total"] == 2 and payload["sourced"] == 1, (
        "sourced counts lines that got a part; a BOM that sourced 1 of 2 must "
        "never read as a complete 1-line BOM"
    )
    unsourced = [ln for ln in payload["lines"] if ln["mpn"] is None]
    assert unsourced and unsourced[0]["manufacturer"] is None, (
        "a line with no part must not carry a manufacturer for the part it has not got"
    )
    assert all(ln["ref"] for ln in payload["lines"]), (
        "the designator is the line's identity; without it a BOM is a bag of parts"
    )


def test_design_bom_distinguishes_unsourced_from_no_substitute(monkeypatch) -> None:
    """`unsourced` and `no_substitute` are different claims: a placeholder the
    selector does not recognise is never LOOKED at, and reporting that as
    'nothing fits' asserts a negative result nobody established."""
    result = {"topology": {"stages": [{"circuit": {"components": [
        {"name": "L1", "selection_provenance": None},
        {"name": "C1", "selection_provenance": {"mpn": "WCAP-1", "manufacturer": "Wurth"}},
    ]}}]}}
    # Patch the catalogue call the tool imports, so the REAL payload-building
    # code below it runs. Asserting a hand-written dict against the schema
    # would prove the schema accepts that dict, not that this module emits it.
    monkeypatch.setattr("heaviside.catalogue.assemble_bom_from_tas",
                        lambda tas, topology=None, spec=None: result)

    # A real ConverterSpec, not {}: the tool declares the shape now, so the
    # test has to build one — which is the point of typing it. `spec={}` used
    # to be accepted silently and told a caller nothing about what was needed.
    spec = ms.ConverterSpec(
        inputVoltage=ms.DimensionWithTolerance(nominal=48.0),
        efficiency=0.95,
        operatingPoints=[ms.OperatingPoint(
            switchingFrequency=300_000, outputVoltages=[12.0], outputCurrents=[5.0])],
    )
    payload = _structured(ms.design_bom(topology="buck", spec=spec, tas={}))

    assert payload["mode"] == "bom"
    _validate(payload)
    assert payload["total"] == 2 and payload["sourced"] == 1
    by_ref = {ln["ref"]: ln for ln in payload["lines"]}
    assert by_ref["L1"]["status"] == "unsourced" and by_ref["L1"]["mpn"] is None
    assert by_ref["C1"]["status"] == "recommended" and by_ref["C1"]["mpn"] == "WCAP-1"
    assert payload["diagnostics"], (
        "a line with no part is not self-explaining — 'nothing fits' and 'this "
        "was never looked at' are different facts and only diagnostics separates them"
    )
