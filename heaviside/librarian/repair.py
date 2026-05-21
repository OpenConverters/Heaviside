"""Repair-recipe artifact: auditor → librarian handoff.

The auditor (:mod:`heaviside.librarian.auditor`) produces
:class:`CategoryAudit` reports describing which pipeline-critical
fields are missing on which TAS components.  The fetcher
(:mod:`heaviside.librarian.fetcher`) and datasheet reader
(:mod:`heaviside.librarian.datasheet`) consume
:class:`IncompleteSourceError`-shaped failures that say *which*
field on *which* MPN failed to enrich.

The repair-recipe sits between them: a structured, serialisable
"to-do list" of (MPN, field, preferred-source-order) tuples that
the ``component-librarian`` agent walks one task at a time, trying
each preferred source in order, and recording attempts so the next
agent session can resume mid-recipe.

Design properties
-----------------

* **Single shape across both enrichment paths.**  ``missing_field``
  is the dotted ``electrical.<name>`` form that both
  :class:`IncompleteSourceError` and
  :class:`IncompleteDatasheetError` already emit — so callers don't
  re-translate.
* **Deterministic source ordering** per :data:`DATASHEET_PREFERRED_FIELDS`.
  Fields that Digi-Key / Mouser rarely populate (Qrr,
  body-diode Vf, ripple current, junction-T max) get the datasheet
  reader first; everything else uses distributor APIs first because
  they're faster and more deterministic.
* **JSON round-trip.**  :func:`recipe_to_json` /
  :func:`recipe_from_json` so the librarian agent can persist a
  recipe between runs (long backfill campaigns frequently span
  multiple sessions).
* **No silent state mutation.**  Every "I tried source X for task Y"
  must call :func:`record_attempt`, which returns a *new* recipe.
  Recipes are frozen at the task level for the same reason
  :class:`FieldGap` is frozen — passing them around must not let
  one consumer's changes leak into another's view.

Strict-mode departures from any Proteus equivalent
--------------------------------------------------

There is no Proteus precedent — Proteus's auditor printed to stdout
and the librarian scripts read no structured input.  This module
formalises the contract that was previously implicit in the
adversarial dynamic between auditor and librarian agents.

* :func:`record_attempt` raises :class:`LibrarianError` if the
  caller claims a source that was never in the task's source list
  (you can't "demote" a source that wasn't a candidate).
* JSON loaders raise :class:`LibrarianError` on shape mismatch —
  not ``TypeError`` / ``KeyError`` — so the agent's recipe-load
  step can be wrapped in one ``except``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from heaviside.librarian.auditor import (
    AUDITABLE_CATEGORIES,
    CategoryAudit,
    ComponentAudit,
    FieldGap,
    FieldStatus,
)
from heaviside.librarian.fetcher.base import IncompleteSourceError
from heaviside.librarian.safe_access import LibrarianError


__all__ = [
    "DATASHEET_PREFERRED_FIELDS",
    "DEFAULT_SOURCE_ORDER",
    "DATASHEET_FIRST_ORDER",
    "KNOWN_SOURCES",
    "PRIORITY_CRITICAL",
    "PRIORITY_WARNING",
    "RECIPE_SCHEMA_VERSION",
    "RepairTask",
    "RepairRecipe",
    "filter_by_category",
    "filter_by_first_source",
    "is_exhausted",
    "record_attempt",
    "record_failure",
    "recipe_from_audit_all",
    "recipe_from_category_audit",
    "recipe_from_json",
    "recipe_to_json",
    "sources_for_field",
    "tasks_from_component_audit",
]


# ---------------------------------------------------------------------------
# Source-preference rules
# ---------------------------------------------------------------------------


# Enrichment-source identifiers.  These match the
# :attr:`IncompleteSourceError.source` strings emitted by the
# fetcher converters (``"digikey"``, ``"mouser"``) and the
# datasheet reader (``"datasheet"``) so the librarian agent's
# error-handler can `record_failure(recipe, exc)` directly.
KNOWN_SOURCES: frozenset[str] = frozenset({"digikey", "mouser", "datasheet"})


# Fields where the manufacturer datasheet typically beats distributor
# APIs.  Empirically derived from the May 2026 audit:
#
# * Qrr / trr appear in Digi-Key's catalog on <2% of Si diodes.
# * Body-diode Vf is a MOSFET datasheet-only spec.
# * Ripple current / ESR are present on Digi-Key for aluminum
#   electrolytics but absent for ceramics and films; we default to
#   datasheet to avoid the per-technology branching.
# * Junction-T max is reported inconsistently across distributors
#   (sometimes °C, sometimes K, sometimes absent).
DATASHEET_PREFERRED_FIELDS: frozenset[str] = frozenset({
    # MOSFETs
    "reverseRecoveryCharge",
    "bodyDiodeForwardVoltage",
    "reverseRecoveryTime",
    "reverseTransferCapacitance",
    "gateDrainCharge",
    "gateSourceCharge",
    # IGBTs
    "turnOnEnergy",
    "turnOffEnergy",
    "gateEmitterThreshold",
    "switchingEnergyOn",
    "switchingEnergyOff",
    # Capacitors
    "esr",
    "rippleCurrent",
    "dissipationFactor",
    "leakageCurrent",
    "lifetimeHours",
    # Resistors
    "temperatureCoefficient",
    "maximumVoltage",
    # Thermal (any category)
    "junctionTemperatureMax",
})


DEFAULT_SOURCE_ORDER: tuple[str, ...] = ("digikey", "mouser", "datasheet")
DATASHEET_FIRST_ORDER: tuple[str, ...] = ("datasheet", "digikey", "mouser")


def sources_for_field(field_name: str) -> tuple[str, ...]:
    """Return the preferred source order for ``field_name``.

    A pure function (no I/O, no state).  The librarian agent calls
    this once per task; callers that want a different order should
    construct :class:`RepairTask` objects directly rather than
    monkeypatching.
    """
    if field_name in DATASHEET_PREFERRED_FIELDS:
        return DATASHEET_FIRST_ORDER
    return DEFAULT_SOURCE_ORDER


# ---------------------------------------------------------------------------
# Priority levels
# ---------------------------------------------------------------------------


# A "critical" task came from :attr:`ComponentAudit.critical_failures`
# (pipeline-blocking gap).  A "warning" task came from
# :attr:`ComponentAudit.required_failures` (useful-but-not-blocking
# field).  The librarian agent typically drains all criticals
# before touching warnings.
PRIORITY_CRITICAL: int = 0
PRIORITY_WARNING: int = 1


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------


# Bump this whenever :class:`RepairTask` or :class:`RepairRecipe`
# gain or remove a field, or when an enum's allowed values change.
# :func:`recipe_from_json` refuses to load a different version so
# stale on-disk recipes don't silently misalign with newer code.
RECIPE_SCHEMA_VERSION: str = "1"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepairTask:
    """One unit of librarian work.

    Attributes
    ----------
    category :
        TAS category (mosfets / diodes / igbts / capacitors /
        resistors / magnetics).
    mpn :
        Manufacturer part number — the value the librarian agent
        will search distributors for.
    field :
        Bare schema field name (e.g. ``"reverseRecoveryCharge"``).
    missing_field :
        Dotted form (``"electrical.reverseRecoveryCharge"``)
        matching :attr:`IncompleteSourceError.missing_field`.
    status :
        One of :class:`~heaviside.librarian.auditor.FieldStatus`
        values — explains *why* the field needs repair (missing
        key, null, or zero).
    sources :
        Ordered tuple of enrichment sources still worth trying.
        Empty when every source has been attempted and failed.
    priority :
        :data:`PRIORITY_CRITICAL` or :data:`PRIORITY_WARNING`.
    line :
        Source NDJSON line for the original row, if known.  Carried
        through so the librarian agent can write the enriched row
        back to the same line on the safe-access transaction.
    """
    category: str
    mpn: str
    field: str
    missing_field: str
    status: str
    sources: tuple[str, ...]
    priority: int
    line: int | None = None


@dataclass(frozen=True)
class RepairRecipe:
    """A collection of :class:`RepairTask` objects plus metadata.

    Frozen so handing a recipe to a consumer can't leak the
    consumer's mutations back; mutation helpers
    (:func:`record_attempt`, :func:`record_failure`) return new
    recipes.
    """
    generated_at: str
    schema_version: str
    tasks: tuple[RepairTask, ...]
    summary: Mapping[str, int]


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def _missing_field_path(field_name: str) -> str:
    """Mirror the dotted convention used by ``IncompleteSourceError``."""
    return f"electrical.{field_name}"


def _validate_gap(gap: FieldGap) -> None:
    """Ensure the gap describes an actually-missing field."""
    if gap.status == FieldStatus.PRESENT:
        raise LibrarianError(
            f"_validate_gap: FieldGap with status PRESENT cannot become "
            f"a repair task; gap={gap!r}"
        )


def tasks_from_component_audit(audit: ComponentAudit) -> list[RepairTask]:
    """Convert one :class:`ComponentAudit` into a list of
    :class:`RepairTask` objects.

    Returns an empty list when the audit has no failures (the
    component is already complete enough for the pipeline).
    """
    if audit.category not in AUDITABLE_CATEGORIES:
        raise LibrarianError(
            f"tasks_from_component_audit: category {audit.category!r} "
            f"is not auditable; known: {sorted(AUDITABLE_CATEGORIES)}"
        )

    tasks: list[RepairTask] = []
    for gap in audit.critical_failures:
        _validate_gap(gap)
        tasks.append(
            RepairTask(
                category=audit.category,
                mpn=audit.mpn,
                field=gap.field,
                missing_field=_missing_field_path(gap.field),
                status=gap.status,
                sources=sources_for_field(gap.field),
                priority=PRIORITY_CRITICAL,
                line=audit.line,
            )
        )
    for gap in audit.required_failures:
        _validate_gap(gap)
        tasks.append(
            RepairTask(
                category=audit.category,
                mpn=audit.mpn,
                field=gap.field,
                missing_field=_missing_field_path(gap.field),
                status=gap.status,
                sources=sources_for_field(gap.field),
                priority=PRIORITY_WARNING,
                line=audit.line,
            )
        )
    return tasks


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _summary(tasks: Iterable[RepairTask]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task.category] = counts.get(task.category, 0) + 1
    return counts


def recipe_from_category_audit(
    audit: CategoryAudit, *, generated_at: str | None = None,
) -> RepairRecipe:
    """Build a recipe from a single category audit.

    Tasks are ordered by ``(priority, mpn, field)`` so the output
    is reproducible across runs (the auditor's task ordering is
    insertion order, which can shift if the underlying NDJSON is
    re-sorted; the recipe normalises that out).

    Includes only :attr:`CategoryAudit.failures` (components with
    critical gaps) plus :attr:`CategoryAudit.warnings_only`
    (components that passed criticals but have required-field gaps).
    Components fully complete on both axes contribute zero tasks.
    """
    rows: list[ComponentAudit] = list(audit.failures) + list(audit.warnings_only)
    tasks: list[RepairTask] = []
    for row in rows:
        tasks.extend(tasks_from_component_audit(row))
    tasks.sort(key=lambda t: (t.priority, t.mpn, t.field))
    return RepairRecipe(
        generated_at=generated_at or _now_iso(),
        schema_version=RECIPE_SCHEMA_VERSION,
        tasks=tuple(tasks),
        summary=_summary(tasks),
    )


def recipe_from_audit_all(
    audits: Mapping[str, CategoryAudit],
    *,
    generated_at: str | None = None,
) -> RepairRecipe:
    """Build a single recipe spanning every audited category.

    Tasks are sorted by ``(priority, category, mpn, field)`` —
    callers that want to walk one category at a time can use
    :func:`filter_by_category`.
    """
    tasks: list[RepairTask] = []
    for cat in sorted(audits):
        single = recipe_from_category_audit(audits[cat])
        tasks.extend(single.tasks)
    tasks.sort(key=lambda t: (t.priority, t.category, t.mpn, t.field))
    return RepairRecipe(
        generated_at=generated_at or _now_iso(),
        schema_version=RECIPE_SCHEMA_VERSION,
        tasks=tuple(tasks),
        summary=_summary(tasks),
    )


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------


def recipe_to_json(recipe: RepairRecipe, *, indent: int | None = 2) -> str:
    """Serialise a recipe as deterministic JSON.

    ``indent=2`` by default so diffs are reviewable; pass
    ``indent=None`` for the most compact form (the librarian
    agent's on-disk artifact uses indented form).
    """
    payload = {
        "schema_version": recipe.schema_version,
        "generated_at": recipe.generated_at,
        "summary": dict(recipe.summary),
        "tasks": [asdict(task) for task in recipe.tasks],
    }
    # Convert source tuples to lists for JSON (json refuses tuples
    # to differentiate from lists; we restore on read).
    for task in payload["tasks"]:
        task["sources"] = list(task["sources"])
    return json.dumps(payload, indent=indent, sort_keys=True)


def _coerce_task(raw: Any) -> RepairTask:
    if not isinstance(raw, dict):
        raise LibrarianError(
            f"recipe_from_json: task entries must be JSON objects; got "
            f"{type(raw).__name__}"
        )
    required = {
        "category", "mpn", "field", "missing_field", "status",
        "sources", "priority",
    }
    missing = required - set(raw)
    if missing:
        raise LibrarianError(
            f"recipe_from_json: task is missing fields {sorted(missing)}; "
            f"got keys {sorted(raw)}"
        )
    sources = raw["sources"]
    if not isinstance(sources, list) or any(
        not isinstance(s, str) for s in sources
    ):
        raise LibrarianError(
            f"recipe_from_json: 'sources' must be a list of strings; "
            f"got {sources!r}"
        )
    line = raw.get("line")
    if line is not None and not isinstance(line, int):
        raise LibrarianError(
            f"recipe_from_json: 'line' must be int or null; got "
            f"{type(line).__name__}"
        )
    return RepairTask(
        category=raw["category"],
        mpn=raw["mpn"],
        field=raw["field"],
        missing_field=raw["missing_field"],
        status=raw["status"],
        sources=tuple(sources),
        priority=int(raw["priority"]),
        line=line,
    )


def recipe_from_json(text: str) -> RepairRecipe:
    """Parse ``text`` as a previously-emitted recipe.

    Raises :class:`LibrarianError` on any shape mismatch (wrong
    schema version, missing top-level keys, malformed task entries)
    — strict-mode: no silent ``{}`` fallback when the input is bad.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LibrarianError(
            f"recipe_from_json: not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise LibrarianError(
            f"recipe_from_json: top-level value must be a JSON object; "
            f"got {type(payload).__name__}"
        )
    required_top = {"schema_version", "generated_at", "summary", "tasks"}
    missing = required_top - set(payload)
    if missing:
        raise LibrarianError(
            f"recipe_from_json: payload missing keys {sorted(missing)}"
        )
    if payload["schema_version"] != RECIPE_SCHEMA_VERSION:
        raise LibrarianError(
            f"recipe_from_json: schema_version "
            f"{payload['schema_version']!r} does not match current "
            f"{RECIPE_SCHEMA_VERSION!r} — regenerate the recipe from "
            f"a fresh audit"
        )
    if not isinstance(payload["tasks"], list):
        raise LibrarianError(
            f"recipe_from_json: 'tasks' must be a JSON array; got "
            f"{type(payload['tasks']).__name__}"
        )
    if not isinstance(payload["summary"], dict):
        raise LibrarianError(
            f"recipe_from_json: 'summary' must be a JSON object; got "
            f"{type(payload['summary']).__name__}"
        )
    tasks = tuple(_coerce_task(t) for t in payload["tasks"])
    return RepairRecipe(
        generated_at=str(payload["generated_at"]),
        schema_version=str(payload["schema_version"]),
        tasks=tasks,
        summary={str(k): int(v) for k, v in payload["summary"].items()},
    )


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def filter_by_category(recipe: RepairRecipe, category: str) -> RepairRecipe:
    """Return a new recipe containing only ``category`` tasks."""
    if category not in AUDITABLE_CATEGORIES:
        raise LibrarianError(
            f"filter_by_category: {category!r} is not auditable; "
            f"known: {sorted(AUDITABLE_CATEGORIES)}"
        )
    selected = tuple(t for t in recipe.tasks if t.category == category)
    return RepairRecipe(
        generated_at=recipe.generated_at,
        schema_version=recipe.schema_version,
        tasks=selected,
        summary=_summary(selected),
    )


def filter_by_first_source(recipe: RepairRecipe, source: str) -> RepairRecipe:
    """Return tasks whose first preferred source is ``source``.

    Use this when the librarian agent is about to make a batch of
    Digi-Key requests and wants to pre-group all distributor-first
    tasks together.
    """
    if source not in KNOWN_SOURCES:
        raise LibrarianError(
            f"filter_by_first_source: unknown source {source!r}; "
            f"known: {sorted(KNOWN_SOURCES)}"
        )
    selected = tuple(
        t for t in recipe.tasks if t.sources and t.sources[0] == source
    )
    return RepairRecipe(
        generated_at=recipe.generated_at,
        schema_version=recipe.schema_version,
        tasks=selected,
        summary=_summary(selected),
    )


# ---------------------------------------------------------------------------
# State mutations (functional)
# ---------------------------------------------------------------------------


def is_exhausted(task: RepairTask) -> bool:
    """``True`` iff every preferred source has been tried & failed."""
    return not task.sources


def _replace_task(
    recipe: RepairRecipe,
    mpn: str,
    field_name: str,
    new_task: RepairTask | None,
) -> RepairRecipe:
    """Helper: substitute or remove a task matching (mpn, field_name).

    ``new_task=None`` removes the matched task; non-None replaces
    it.  Raises :class:`LibrarianError` if no task matches — the
    caller is claiming to have acted on something that wasn't in
    the recipe.
    """
    new_tasks: list[RepairTask] = []
    matched = False
    for t in recipe.tasks:
        if t.mpn == mpn and t.field == field_name and not matched:
            matched = True
            if new_task is not None:
                new_tasks.append(new_task)
        else:
            new_tasks.append(t)
    if not matched:
        raise LibrarianError(
            f"recipe has no task for (mpn={mpn!r}, field={field_name!r})"
        )
    tasks = tuple(new_tasks)
    return RepairRecipe(
        generated_at=recipe.generated_at,
        schema_version=recipe.schema_version,
        tasks=tasks,
        summary=_summary(tasks),
    )


def record_attempt(
    recipe: RepairRecipe,
    *,
    mpn: str,
    field_name: str,
    source: str,
    succeeded: bool,
) -> RepairRecipe:
    """Record that ``source`` was tried for ``(mpn, field_name)``.

    * On ``succeeded=True`` the task is removed from the recipe.
    * On ``succeeded=False`` ``source`` is removed from the task's
      preferred sources list; if that list becomes empty the task
      remains in the recipe (with empty ``sources``) so callers
      can audit "we tried everything and nothing worked" via
      :func:`is_exhausted`.

    Raises :class:`LibrarianError` if no task matches the
    ``(mpn, field_name)`` pair or if ``source`` was never a
    candidate for this task — the librarian agent's caller shouldn't
    be reporting attempts on sources that weren't on the menu.
    """
    if source not in KNOWN_SOURCES:
        raise LibrarianError(
            f"record_attempt: unknown source {source!r}; "
            f"known: {sorted(KNOWN_SOURCES)}"
        )
    # Locate the task first so we can validate the source claim.
    for t in recipe.tasks:
        if t.mpn == mpn and t.field == field_name:
            if not succeeded and source not in t.sources:
                raise LibrarianError(
                    f"record_attempt: source {source!r} was not in the "
                    f"task's preferred-source list "
                    f"{list(t.sources)} for ({mpn!r}, {field_name!r}); "
                    f"the agent cannot demote a source that wasn't a "
                    f"candidate"
                )
            break
    else:
        raise LibrarianError(
            f"record_attempt: no task for (mpn={mpn!r}, "
            f"field={field_name!r})"
        )
    if succeeded:
        return _replace_task(recipe, mpn, field_name, None)
    new_sources = tuple(s for s in t.sources if s != source)
    return _replace_task(recipe, mpn, field_name, replace(t, sources=new_sources))


def record_failure(
    recipe: RepairRecipe, error: IncompleteSourceError,
) -> RepairRecipe:
    """Shortcut for the common case: the librarian agent caught an
    :class:`IncompleteSourceError` (or its subclass
    :class:`IncompleteDatasheetError`) and wants to demote that
    source for the matching task.

    Translates ``error.missing_field`` (which is dotted
    ``electrical.<name>``) back to a bare field name and calls
    :func:`record_attempt` with ``succeeded=False``.
    """
    if not error.missing_field.startswith("electrical."):
        raise LibrarianError(
            f"record_failure: error.missing_field {error.missing_field!r} "
            f"does not start with 'electrical.'; cannot map to a "
            f"RepairTask.field"
        )
    bare = error.missing_field[len("electrical."):]
    return record_attempt(
        recipe,
        mpn=error.mpn,
        field_name=bare,
        source=error.source,
        succeeded=False,
    )
