"""Tests for ``heaviside.librarian.repair``.

Covers:

* :func:`sources_for_field` — distributor-first vs datasheet-first
  selection, including the empirically-derived
  :data:`DATASHEET_PREFERRED_FIELDS` set.
* :func:`tasks_from_component_audit` — translation of
  :class:`ComponentAudit` (critical_failures + required_failures)
  into :class:`RepairTask` objects, with correct
  ``missing_field`` dotted path, ``priority`` assignment, and
  ``line`` carry-through.
* :func:`recipe_from_category_audit` — ordering, summary counts,
  and the inclusion of both failures and warnings_only entries.
* :func:`recipe_from_audit_all` — multi-category aggregation +
  deterministic sort.
* JSON round-trip — tuples↔lists, ``schema_version`` enforcement,
  malformed-input rejection.
* :func:`record_attempt` / :func:`record_failure` — succeeded vs
  failed paths, source demotion, exhaustion semantics, error
  shapes when caller misuses the API.
* :func:`filter_by_category` / :func:`filter_by_first_source` —
  membership and counts.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from heaviside.librarian.auditor import (
    CategoryAudit,
    ComponentAudit,
    FieldGap,
    FieldStatus,
)
from heaviside.librarian.fetcher.base import IncompleteSourceError
from heaviside.librarian.repair import (
    DATASHEET_FIRST_ORDER,
    DATASHEET_PREFERRED_FIELDS,
    DEFAULT_SOURCE_ORDER,
    KNOWN_SOURCES,
    PRIORITY_CRITICAL,
    PRIORITY_WARNING,
    RECIPE_SCHEMA_VERSION,
    RepairRecipe,
    RepairTask,
    filter_by_category,
    filter_by_first_source,
    is_exhausted,
    record_attempt,
    record_failure,
    recipe_from_audit_all,
    recipe_from_category_audit,
    recipe_from_json,
    recipe_to_json,
    sources_for_field,
    tasks_from_component_audit,
)
from heaviside.librarian.safe_access import LibrarianError


# ---------------------------------------------------------------------------
# Source preference
# ---------------------------------------------------------------------------


def test_sources_for_field_default() -> None:
    # A field NOT in DATASHEET_PREFERRED_FIELDS gets the default
    # distributor-first ordering.
    assert "drainSourceVoltage" not in DATASHEET_PREFERRED_FIELDS
    assert sources_for_field("drainSourceVoltage") == DEFAULT_SOURCE_ORDER


def test_sources_for_field_datasheet_preferred() -> None:
    # Empirically: Digi-Key rarely exposes Qrr.  Datasheet first.
    assert "reverseRecoveryCharge" in DATASHEET_PREFERRED_FIELDS
    assert sources_for_field("reverseRecoveryCharge") == DATASHEET_FIRST_ORDER


def test_known_sources_are_consistent() -> None:
    # Every entry in the two ordering tuples must also be in
    # KNOWN_SOURCES — otherwise filter_by_first_source / record_*
    # would refuse a source we just emitted.
    for order in (DEFAULT_SOURCE_ORDER, DATASHEET_FIRST_ORDER):
        for s in order:
            assert s in KNOWN_SOURCES


def test_datasheet_preferred_subset_of_known_fields() -> None:
    # Sanity: a few hand-picked entries are the ones documented in
    # the module docstring.
    for f in ("reverseRecoveryCharge", "bodyDiodeForwardVoltage",
              "esr", "rippleCurrent", "junctionTemperatureMax"):
        assert f in DATASHEET_PREFERRED_FIELDS


# ---------------------------------------------------------------------------
# tasks_from_component_audit
# ---------------------------------------------------------------------------


def _mosfet_audit(
    *, critical: list[FieldGap] = None, required: list[FieldGap] = None,
    mpn: str = "TESTFET", line: int | None = 42,
) -> ComponentAudit:
    return ComponentAudit(
        mpn=mpn,
        category="mosfets",
        line=line,
        critical_failures=list(critical or []),
        required_failures=list(required or []),
    )


def test_tasks_from_component_audit_critical_only() -> None:
    audit = _mosfet_audit(
        critical=[FieldGap("reverseRecoveryCharge", FieldStatus.MISSING_KEY)],
    )
    tasks = tasks_from_component_audit(audit)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.category == "mosfets"
    assert task.mpn == "TESTFET"
    assert task.field == "reverseRecoveryCharge"
    assert task.missing_field == "electrical.reverseRecoveryCharge"
    assert task.priority == PRIORITY_CRITICAL
    assert task.sources == DATASHEET_FIRST_ORDER  # Qrr is datasheet-preferred
    assert task.line == 42
    assert task.status == FieldStatus.MISSING_KEY


def test_tasks_from_component_audit_mixed_priority() -> None:
    audit = _mosfet_audit(
        critical=[
            FieldGap("drainSourceVoltage", FieldStatus.MISSING_KEY),
            FieldGap("onResistance", FieldStatus.NULL),
        ],
        required=[
            FieldGap("bodyDiodeForwardVoltage", FieldStatus.MISSING_KEY),
        ],
    )
    tasks = tasks_from_component_audit(audit)
    assert len(tasks) == 3
    priorities = [t.priority for t in tasks]
    assert priorities.count(PRIORITY_CRITICAL) == 2
    assert priorities.count(PRIORITY_WARNING) == 1
    # The body-diode Vf task is datasheet-first.
    bdv = next(t for t in tasks if t.field == "bodyDiodeForwardVoltage")
    assert bdv.sources == DATASHEET_FIRST_ORDER
    assert bdv.priority == PRIORITY_WARNING


def test_tasks_from_component_audit_empty_when_clean() -> None:
    audit = _mosfet_audit()
    assert tasks_from_component_audit(audit) == []


def test_tasks_from_component_audit_rejects_unknown_category() -> None:
    audit = ComponentAudit(mpn="X", category="transistors")
    with pytest.raises(LibrarianError, match="not auditable"):
        tasks_from_component_audit(audit)


def test_tasks_from_component_audit_refuses_present_gap() -> None:
    # The auditor should never put a PRESENT FieldGap into a
    # failure list, but if someone hand-constructs one this must
    # raise — silently producing a "repair task" for an already-
    # present field would corrupt the librarian's queue.
    audit = _mosfet_audit(
        critical=[FieldGap("drainSourceVoltage", FieldStatus.PRESENT)],
    )
    with pytest.raises(LibrarianError, match="PRESENT"):
        tasks_from_component_audit(audit)


# ---------------------------------------------------------------------------
# recipe_from_category_audit
# ---------------------------------------------------------------------------


def _category_audit_with(
    *failure_specs: tuple[str, list[FieldGap], list[FieldGap]],
    warnings_only_specs: list[tuple[str, list[FieldGap]]] = None,
) -> CategoryAudit:
    """Build a CategoryAudit from compact (mpn, criticals, requireds) tuples."""
    report = CategoryAudit(category="mosfets")
    for mpn, crit, req in failure_specs:
        report.failures.append(_mosfet_audit(
            critical=crit, required=req, mpn=mpn, line=None,
        ))
        report.total += 1
    for mpn, req in warnings_only_specs or []:
        report.warnings_only.append(_mosfet_audit(
            critical=[], required=req, mpn=mpn, line=None,
        ))
        report.total += 1
        report.passed += 1
    return report


def test_recipe_from_category_audit_orders_deterministically() -> None:
    # Insertion order intentionally reversed; sorted output must be
    # stable on (priority, mpn, field).
    audit = _category_audit_with(
        ("Z-MPN", [FieldGap("drainSourceVoltage", FieldStatus.MISSING_KEY)], []),
        ("A-MPN", [FieldGap("onResistance", FieldStatus.NULL)], []),
    )
    recipe = recipe_from_category_audit(audit)
    assert [t.mpn for t in recipe.tasks] == ["A-MPN", "Z-MPN"]


def test_recipe_from_category_audit_includes_warnings_only() -> None:
    audit = _category_audit_with(
        ("FAIL", [FieldGap("drainSourceVoltage", FieldStatus.MISSING_KEY)], []),
        warnings_only_specs=[
            ("WARN", [FieldGap("bodyDiodeForwardVoltage", FieldStatus.NULL)]),
        ],
    )
    recipe = recipe_from_category_audit(audit)
    mpns = [t.mpn for t in recipe.tasks]
    assert "FAIL" in mpns
    assert "WARN" in mpns
    # Criticals before warnings, regardless of MPN ordering.
    fail_task = next(t for t in recipe.tasks if t.mpn == "FAIL")
    warn_task = next(t for t in recipe.tasks if t.mpn == "WARN")
    assert fail_task.priority == PRIORITY_CRITICAL
    assert warn_task.priority == PRIORITY_WARNING


def test_recipe_summary_counts_per_category() -> None:
    audit = _category_audit_with(
        ("A", [FieldGap("drainSourceVoltage", FieldStatus.MISSING_KEY)], []),
        ("B", [FieldGap("onResistance", FieldStatus.NULL)], []),
    )
    recipe = recipe_from_category_audit(audit)
    assert recipe.summary == {"mosfets": 2}


def test_recipe_explicit_generated_at_is_preserved() -> None:
    audit = _category_audit_with(
        ("A", [FieldGap("onResistance", FieldStatus.MISSING_KEY)], []),
    )
    recipe = recipe_from_category_audit(audit, generated_at="2026-05-21T00:00:00+00:00")
    assert recipe.generated_at == "2026-05-21T00:00:00+00:00"
    assert recipe.schema_version == RECIPE_SCHEMA_VERSION


def test_recipe_from_audit_all_spans_categories() -> None:
    audits = {
        "mosfets": _category_audit_with(
            ("FET1", [FieldGap("drainSourceVoltage", FieldStatus.MISSING_KEY)], []),
        ),
    }
    # Hand-build a diodes audit since _category_audit_with hardcodes mosfets.
    diode_report = CategoryAudit(category="diodes")
    diode_report.failures.append(ComponentAudit(
        mpn="D1", category="diodes",
        critical_failures=[FieldGap("reverseRecoveryCharge", FieldStatus.MISSING_KEY)],
    ))
    diode_report.total = 1
    audits["diodes"] = diode_report

    recipe = recipe_from_audit_all(audits)
    cats = {t.category for t in recipe.tasks}
    assert cats == {"mosfets", "diodes"}
    assert recipe.summary == {"mosfets": 1, "diodes": 1}


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------


def test_recipe_json_round_trip() -> None:
    audit = _category_audit_with(
        ("FET-1", [FieldGap("drainSourceVoltage", FieldStatus.MISSING_KEY)],
         [FieldGap("bodyDiodeForwardVoltage", FieldStatus.NULL)]),
    )
    original = recipe_from_category_audit(
        audit, generated_at="2026-05-21T12:00:00+00:00",
    )
    text = recipe_to_json(original)
    restored = recipe_from_json(text)
    assert restored.generated_at == original.generated_at
    assert restored.schema_version == original.schema_version
    assert restored.summary == original.summary
    assert restored.tasks == original.tasks
    # Tuples must survive the round trip (json lists -> tuples).
    assert all(isinstance(t.sources, tuple) for t in restored.tasks)


def test_recipe_to_json_is_indented_by_default() -> None:
    audit = _category_audit_with(
        ("X", [FieldGap("onResistance", FieldStatus.MISSING_KEY)], []),
    )
    text = recipe_to_json(recipe_from_category_audit(audit))
    assert "\n" in text and "  " in text


def test_recipe_to_json_compact_form() -> None:
    audit = _category_audit_with(
        ("X", [FieldGap("onResistance", FieldStatus.MISSING_KEY)], []),
    )
    text = recipe_to_json(recipe_from_category_audit(audit), indent=None)
    assert "\n" not in text


def test_recipe_from_json_rejects_wrong_schema_version() -> None:
    payload = {
        "schema_version": "999",
        "generated_at": "2026-05-21T00:00:00+00:00",
        "summary": {},
        "tasks": [],
    }
    with pytest.raises(LibrarianError, match="schema_version"):
        recipe_from_json(json.dumps(payload))


def test_recipe_from_json_rejects_non_json() -> None:
    with pytest.raises(LibrarianError, match="not valid JSON"):
        recipe_from_json("definitely not json {")


def test_recipe_from_json_rejects_non_object_top() -> None:
    with pytest.raises(LibrarianError, match="top-level"):
        recipe_from_json("[1, 2, 3]")


def test_recipe_from_json_rejects_missing_top_keys() -> None:
    with pytest.raises(LibrarianError, match="missing keys"):
        recipe_from_json(json.dumps({"schema_version": RECIPE_SCHEMA_VERSION}))


def test_recipe_from_json_rejects_malformed_task() -> None:
    payload = {
        "schema_version": RECIPE_SCHEMA_VERSION,
        "generated_at": "x",
        "summary": {},
        "tasks": [{"category": "mosfets"}],  # missing most fields
    }
    with pytest.raises(LibrarianError, match="missing fields"):
        recipe_from_json(json.dumps(payload))


def test_recipe_from_json_rejects_non_string_source() -> None:
    payload = {
        "schema_version": RECIPE_SCHEMA_VERSION,
        "generated_at": "x",
        "summary": {"mosfets": 1},
        "tasks": [{
            "category": "mosfets", "mpn": "X", "field": "onResistance",
            "missing_field": "electrical.onResistance",
            "status": FieldStatus.MISSING_KEY,
            "sources": ["digikey", 42],  # bad
            "priority": 0,
            "line": None,
        }],
    }
    with pytest.raises(LibrarianError, match="sources"):
        recipe_from_json(json.dumps(payload))


# ---------------------------------------------------------------------------
# filter_by_*
# ---------------------------------------------------------------------------


def _two_cat_recipe() -> RepairRecipe:
    audits = {
        "mosfets": _category_audit_with(
            ("FET-A", [FieldGap("drainSourceVoltage", FieldStatus.MISSING_KEY)], []),
        ),
    }
    diode_report = CategoryAudit(category="diodes")
    diode_report.failures.append(ComponentAudit(
        mpn="D-A", category="diodes",
        critical_failures=[FieldGap("reverseRecoveryCharge", FieldStatus.MISSING_KEY)],
    ))
    diode_report.total = 1
    audits["diodes"] = diode_report
    return recipe_from_audit_all(audits)


def test_filter_by_category_selects_only_that_category() -> None:
    recipe = _two_cat_recipe()
    fets = filter_by_category(recipe, "mosfets")
    assert {t.category for t in fets.tasks} == {"mosfets"}
    assert fets.summary == {"mosfets": 1}


def test_filter_by_category_rejects_unknown() -> None:
    recipe = _two_cat_recipe()
    with pytest.raises(LibrarianError, match="not auditable"):
        filter_by_category(recipe, "transistors")


def test_filter_by_first_source_picks_distributor_first_tasks() -> None:
    recipe = _two_cat_recipe()
    # FET-A's drainSourceVoltage uses DEFAULT_SOURCE_ORDER → first = digikey.
    # D-A's reverseRecoveryCharge uses DATASHEET_FIRST_ORDER → first = datasheet.
    dk_only = filter_by_first_source(recipe, "digikey")
    ds_only = filter_by_first_source(recipe, "datasheet")
    assert [t.mpn for t in dk_only.tasks] == ["FET-A"]
    assert [t.mpn for t in ds_only.tasks] == ["D-A"]


def test_filter_by_first_source_rejects_unknown() -> None:
    recipe = _two_cat_recipe()
    with pytest.raises(LibrarianError, match="unknown source"):
        filter_by_first_source(recipe, "octopart")


# ---------------------------------------------------------------------------
# record_attempt / record_failure / is_exhausted
# ---------------------------------------------------------------------------


def _single_task_recipe() -> RepairRecipe:
    audit = _category_audit_with(
        ("FET-X", [FieldGap("onResistance", FieldStatus.MISSING_KEY)], []),
    )
    return recipe_from_category_audit(audit)


def test_record_attempt_success_removes_task() -> None:
    recipe = _single_task_recipe()
    after = record_attempt(
        recipe, mpn="FET-X", field_name="onResistance",
        source="digikey", succeeded=True,
    )
    assert after.tasks == ()
    assert after.summary == {}


def test_record_attempt_failure_demotes_source() -> None:
    recipe = _single_task_recipe()
    after = record_attempt(
        recipe, mpn="FET-X", field_name="onResistance",
        source="digikey", succeeded=False,
    )
    assert len(after.tasks) == 1
    task = after.tasks[0]
    assert "digikey" not in task.sources
    # Mouser & datasheet remain in the original order.
    assert task.sources == ("mouser", "datasheet")
    assert not is_exhausted(task)


def test_record_attempt_exhausts_after_all_sources_fail() -> None:
    recipe = _single_task_recipe()
    for src in DEFAULT_SOURCE_ORDER:
        recipe = record_attempt(
            recipe, mpn="FET-X", field_name="onResistance",
            source=src, succeeded=False,
        )
    assert len(recipe.tasks) == 1
    assert is_exhausted(recipe.tasks[0])
    assert recipe.tasks[0].sources == ()


def test_record_attempt_rejects_unknown_task() -> None:
    recipe = _single_task_recipe()
    with pytest.raises(LibrarianError, match="no task"):
        record_attempt(
            recipe, mpn="NOPE", field_name="onResistance",
            source="digikey", succeeded=True,
        )


def test_record_attempt_rejects_unknown_source() -> None:
    recipe = _single_task_recipe()
    with pytest.raises(LibrarianError, match="unknown source"):
        record_attempt(
            recipe, mpn="FET-X", field_name="onResistance",
            source="octopart", succeeded=False,
        )


def test_record_attempt_rejects_demotion_of_non_candidate_source() -> None:
    # A task with sources=('datasheet',) cannot have 'digikey' demoted —
    # digikey was never a candidate.
    recipe = _single_task_recipe()
    only_ds = replace(recipe.tasks[0], sources=("datasheet",))
    recipe = replace(recipe, tasks=(only_ds,))
    with pytest.raises(LibrarianError, match="not in the task"):
        record_attempt(
            recipe, mpn="FET-X", field_name="onResistance",
            source="digikey", succeeded=False,
        )


def test_record_attempt_success_does_not_check_candidate_membership() -> None:
    # When a source unexpectedly succeeds (rare but legitimate —
    # the librarian agent could have a third-party enrichment path
    # we haven't documented yet), the task is still removed.  The
    # candidate-membership check applies only to demotions.
    recipe = _single_task_recipe()
    only_ds = replace(recipe.tasks[0], sources=("datasheet",))
    recipe = replace(recipe, tasks=(only_ds,))
    after = record_attempt(
        recipe, mpn="FET-X", field_name="onResistance",
        source="digikey", succeeded=True,
    )
    assert after.tasks == ()


def test_record_failure_translates_incomplete_source_error() -> None:
    recipe = _single_task_recipe()
    exc = IncompleteSourceError(
        source="digikey", mpn="FET-X",
        missing_field="electrical.onResistance",
    )
    after = record_failure(recipe, exc)
    assert "digikey" not in after.tasks[0].sources


def test_record_failure_rejects_unprefixed_field() -> None:
    recipe = _single_task_recipe()
    # Hand-construct an error whose missing_field doesn't carry the
    # ``electrical.`` prefix (e.g. a future ``part.*`` failure).
    exc = IncompleteSourceError(
        source="digikey", mpn="FET-X", missing_field="part.partNumber",
    )
    with pytest.raises(LibrarianError, match="does not start with"):
        record_failure(recipe, exc)


# ---------------------------------------------------------------------------
# Immutability invariants
# ---------------------------------------------------------------------------


def test_record_attempt_returns_new_recipe_does_not_mutate() -> None:
    recipe = _single_task_recipe()
    original_tasks = recipe.tasks
    record_attempt(
        recipe, mpn="FET-X", field_name="onResistance",
        source="digikey", succeeded=False,
    )
    assert recipe.tasks is original_tasks
    assert recipe.tasks[0].sources == DEFAULT_SOURCE_ORDER


def test_repair_task_is_hashable() -> None:
    # Hashable means we can use tasks as dict keys / set members,
    # which the librarian agent's batching logic relies on.
    recipe = _single_task_recipe()
    s = {recipe.tasks[0]}
    assert recipe.tasks[0] in s
