"""TAS librarian: strict-mode writer for ``TAS/data/*.ndjson``.

This is the **only** sanctioned path for *appending* validated rows
to a TAS NDJSON file (see ``AGENTS.md`` §6: "TAS writes go through
the librarian, always" and the project guardrail "Never edit
``TAS/data/*.ndjson`` by hand").

Scope of this v0.1 port
-----------------------

Ported from ``Proteus/scripts/librarian_tas.py``:

* :func:`load_validator` — builds a ``Draft202012Validator`` for a
  TAS category with the surrounding ``$ref`` registry hydrated from
  the local schema directory plus ``PEAS/schemas/utils.json``.
* :func:`validate_component` — runs the validator and *throws* on
  the first batch of errors (strict mode; Proteus returned a
  ``(bool, list)`` and let callers ignore the result).
* :func:`component_exists` — MPN lookup across every envelope
  variant the legacy database is known to use.
* :func:`add_component` — validate-then-:func:`safe_append`.

**Explicitly out of scope** for v0.1 (will land in dedicated
modules / agents per ``AGENTS.md`` rule 8):

* Digi-Key / Mouser API fetchers, OAuth, rate limiting
* Datasheet PDF parsing
* Web scraping / search wrappers
* Bulk import / batch campaigns
* The ``main()`` CLI

Strict-mode differences from the Proteus prototype
--------------------------------------------------

Per ``CLAUDE.md`` ("no fallbacks, no defaults, no silent shortcuts
— throw"):

* No silent ``except Exception`` while loading sub-schemas.  A
  malformed schema in the SAS/CAS/RAS/MAS submodule is a real bug
  that must surface, not a thing to swallow.
* No "WARNING: jsonschema not installed, skipping validation"
  escape hatch.  ``jsonschema`` + ``referencing`` are hard
  dependencies; if they are missing, *import* fails — not
  validation.
* No five-error truncation.  Every validator error is reported.
* :func:`component_exists` throws :class:`LibrarianError` on a
  corrupt NDJSON line instead of silently ``continue``-ing past
  it.  Corruption in the canonical database is a stop-the-line
  event.
* :func:`add_component` throws on duplicate (the legacy code
  silently re-appended).

The schema map
--------------

Mirrors the Proteus map.  ``controllers``, ``converters`` and
``quarantine`` are deliberately absent — those categories have no
schema in the current submodule layout and writing them through
this module is therefore rejected (we will not silently accept
unvalidated data).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from heaviside.librarian import safe_access as _sa
from heaviside.librarian.guards import guard_component
from heaviside.librarian.safe_access import (
    LibrarianError,
    safe_append,
)

__all__ = [
    "SCHEMA_MAP",
    "DuplicateComponentError",
    "SchemaNotFoundError",
    "ValidationError",
    "add_component",
    "component_exists",
    "load_validator",
    "validate_component",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SchemaNotFoundError(LibrarianError):
    """Raised when a category has no schema registered in :data:`SCHEMA_MAP`."""


class ValidationError(LibrarianError):
    """Raised when a component fails JSON-schema validation.

    The ``errors`` attribute is a list of ``(path, message)`` tuples,
    where ``path`` is a dotted JSON pointer (or ``"(root)"``) and
    ``message`` is the raw ``jsonschema`` message string.
    """

    def __init__(self, category: str, mpn: str, errors: list[tuple[str, str]]):
        self.category = category
        self.mpn = mpn
        self.errors = errors
        formatted = "\n".join(f"  [{p}] {m}" for p, m in errors)
        super().__init__(f"schema validation failed for {category}/{mpn!r}:\n{formatted}")


class DuplicateComponentError(LibrarianError):
    """Raised by :func:`add_component` when the MPN is already present."""


# ---------------------------------------------------------------------------
# Schema map
# ---------------------------------------------------------------------------


# Resolved at import time.  Tests retarget _sa.TAS_DATA_DIR via
# monkeypatch but the schema map itself is keyed by submodule layout
# and rarely needs to move.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _unwrap_top(key: str):
    """Build an unwrapper that descends one envelope key."""

    def _u(rec: dict[str, Any]) -> Any:
        if not isinstance(rec, dict) or key not in rec:
            raise ValidationError(
                "<envelope>",
                _extract_mpn(rec),
                [
                    (
                        "(root)",
                        f"missing envelope key {key!r}; "
                        f"top-level keys: {sorted(rec) if isinstance(rec, dict) else type(rec).__name__}",
                    )
                ],
            )
        return rec[key]

    return _u


def _unwrap_two(outer: str, inner: str):
    """Build an unwrapper that descends two envelope keys."""

    def _u(rec: dict[str, Any]) -> Any:
        if not isinstance(rec, dict) or outer not in rec:
            raise ValidationError(
                "<envelope>",
                _extract_mpn(rec),
                [
                    (
                        "(root)",
                        f"missing envelope key {outer!r}; "
                        f"top-level keys: {sorted(rec) if isinstance(rec, dict) else type(rec).__name__}",
                    )
                ],
            )
        mid = rec[outer]
        if not isinstance(mid, dict) or inner not in mid:
            raise ValidationError(
                f"{outer}.<envelope>",
                _extract_mpn(rec),
                [
                    (
                        outer,
                        f"missing nested envelope key {inner!r}; "
                        f"keys: {sorted(mid) if isinstance(mid, dict) else type(mid).__name__}",
                    )
                ],
            )
        return mid[inner]

    return _u


# Per-category: (schema_path, unwrap_callable).
#
# Why each is shaped this way (verified by sampling the live NDJSON
# files in TAS/data/, 2 002 records sampled per category, May 2026):
#
#   mosfets    : {"mosfet":    {...}}                  → validate inner
#   diodes     : {"semiconductor": {"diode": {...}}}   → validate diode
#   igbts      : {"semiconductor": {"igbt":  {...}}}   → validate igbt
#   capacitors : {"capacitor": {...}}
#   resistors  : {"resistor":  {...}}
#   magnetics  : {"magnetic":  {...}}
#
# Categories without a schema (controllers, converters, quarantine)
# are intentionally absent — strict-mode policy refuses to write
# unvalidated rows through the librarian.
SCHEMA_MAP: dict[str, tuple[Path, Any]] = {
    "mosfets": (
        _REPO_ROOT / "SAS" / "schemas" / "mosfet.json",
        _unwrap_two("semiconductor", "mosfet"),
    ),
    "diodes": (
        _REPO_ROOT / "SAS" / "schemas" / "diode.json",
        _unwrap_two("semiconductor", "diode"),
    ),
    "igbts": (_REPO_ROOT / "SAS" / "schemas" / "igbt.json", _unwrap_two("semiconductor", "igbt")),
    "capacitors": (_REPO_ROOT / "CAS" / "schemas" / "capacitor.json", _unwrap_top("capacitor")),
    "resistors": (_REPO_ROOT / "RAS" / "schemas" / "resistor.json", _unwrap_top("resistor")),
    "varistors": (_REPO_ROOT / "RAS" / "schemas" / "varistor.json", _unwrap_top("varistor")),
    "magnetics": (_REPO_ROOT / "MAS" / "schemas" / "magnetic.json", _unwrap_top("magnetic")),
}


# ---------------------------------------------------------------------------
# Validator construction
# ---------------------------------------------------------------------------


# Memoise compiled validators — schema loading walks the submodule
# tree and is non-trivial.  Guarded by a lock because validators
# are sometimes built from a worker thread (Strands tool calls).
_VALIDATOR_CACHE: dict[str, Draft202012Validator] = {}
_VALIDATOR_LOCK = threading.Lock()


def _read_schema(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SchemaNotFoundError(f"cannot read schema at {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LibrarianError(
            f"schema at {path} is not valid JSON: {exc.msg} (line {exc.lineno}, col {exc.colno})"
        ) from exc


def _build_registry(schema_path: Path) -> Registry:
    """Hydrate a ``referencing.Registry`` for ``schema_path``.

    Mirrors Proteus's three-pass strategy:

    1. Walk the schema-file's directory (recursively) and register
       every sibling ``*.json`` that carries a ``$id``.
    2. Register the local ``utils.json`` (if any) at both its
       ``$id`` AND under the relative URI ``./utils.json`` (which
       the SAS/CAS schemas literally use in their ``$ref``).
    3. Fall back to ``PEAS/schemas/utils.json`` for the shared
       definitions, registering it at ``./utils.json`` only if the
       local utils didn't already claim that name.

    Unlike the Proteus version we do NOT swallow ``JSONDecodeError``
    on sub-schemas — a malformed schema is a real bug.
    """
    registry = Registry()
    schema_dir = schema_path.parent

    # Pass 1: every sibling *.json with a $id (skip the top schema
    # itself; that's the validator's main resource).
    for sub_path in sorted(schema_dir.rglob("*.json")):
        if sub_path == schema_path:
            continue
        sub = _read_schema(sub_path)
        sid = sub.get("$id")
        if not sid:
            continue
        resource = Resource.from_contents(sub, default_specification=DRAFT202012)
        registry = registry.with_resource(sid, resource)

    # Pass 2: local utils.json under ./utils.json
    local_utils_path = schema_dir / "utils.json"
    local_utils_registered = False
    if local_utils_path.exists():
        local_utils = _read_schema(local_utils_path)
        sid = local_utils.get("$id")
        if sid:
            resource = Resource.from_contents(
                local_utils,
                default_specification=DRAFT202012,
            )
            registry = registry.with_resource(sid, resource)
            registry = registry.with_resource("./utils.json", resource)
            local_utils_registered = True

    # Pass 3: PEAS utils as the shared fallback.
    peas_utils_path = _REPO_ROOT / "PEAS" / "schemas" / "utils.json"
    if peas_utils_path.exists():
        peas_utils = _read_schema(peas_utils_path)
        sid = peas_utils.get("$id")
        if sid:
            resource = Resource.from_contents(
                peas_utils,
                default_specification=DRAFT202012,
            )
            registry = registry.with_resource(sid, resource)
            if not local_utils_registered:
                registry = registry.with_resource("./utils.json", resource)

    return registry


def load_validator(category: str) -> Draft202012Validator:
    """Return a memoised :class:`Draft202012Validator` for ``category``.

    Raises
    ------
    UnknownCategoryError
        If ``category`` is not in :data:`safe_access.CATEGORIES`.
    SchemaNotFoundError
        If ``category`` has no entry in :data:`SCHEMA_MAP` or the
        schema file is missing on disk.
    """
    _sa._validate_category(category)  # whitelist gate first
    if category not in SCHEMA_MAP:
        raise SchemaNotFoundError(
            f"no JSON schema registered for category {category!r}.  "
            f"Available: {sorted(SCHEMA_MAP)}.  Categories without a "
            "schema (controllers, converters, quarantine) cannot be "
            "written through the librarian — strict-mode policy."
        )
    schema_path, _unwrap = SCHEMA_MAP[category]
    if not schema_path.exists():
        raise SchemaNotFoundError(
            f"schema for {category!r} not found at {schema_path}.  "
            "Did the submodule fail to initialise?"
        )

    with _VALIDATOR_LOCK:
        cached = _VALIDATOR_CACHE.get(category)
        if cached is not None:
            return cached
        schema = _read_schema(schema_path)
        registry = _build_registry(schema_path)
        validator = Draft202012Validator(schema, registry=registry)
        _VALIDATOR_CACHE[category] = validator
        return validator


def _clear_validator_cache() -> None:
    """Test hook: drop memoised validators (used when ``SCHEMA_MAP`` is
    monkeypatched)."""
    with _VALIDATOR_LOCK:
        _VALIDATOR_CACHE.clear()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _extract_mpn(component: dict[str, Any]) -> str:
    """Best-effort MPN extraction for error messages.

    Mirrors the envelope traversal in :func:`component_exists`.
    Returns ``"UNKNOWN"`` if no MPN can be located — never throws,
    because this is used only to label error messages.
    """
    for envelope in ("resistor", "capacitor", "magnetic", "mosfet", "controller"):
        inner = component.get(envelope)
        if isinstance(inner, dict):
            mi = inner.get("manufacturerInfo")
            if isinstance(mi, dict):
                ref = mi.get("reference")
                if ref:
                    return str(ref)
                ds = mi.get("datasheetInfo")
                if isinstance(ds, dict):
                    part = ds.get("part")
                    if isinstance(part, dict):
                        pn = part.get("partNumber")
                        if pn:
                            return str(pn)
    semi = component.get("semiconductor")
    if isinstance(semi, dict):
        for nested in ("diode", "igbt", "bjt", "mosfet"):
            sub = semi.get(nested)
            if isinstance(sub, dict):
                mi = sub.get("manufacturerInfo")
                if isinstance(mi, dict):
                    ref = mi.get("reference")
                    if ref:
                        return str(ref)
                    ds = mi.get("datasheetInfo")
                    if isinstance(ds, dict):
                        part = ds.get("part")
                        if isinstance(part, dict):
                            pn = part.get("partNumber")
                            if pn:
                                return str(pn)
    mi = component.get("manufacturerInfo")
    if isinstance(mi, dict):
        ref = mi.get("reference")
        if ref:
            return str(ref)
    inputs = component.get("inputs")
    if isinstance(inputs, dict):
        pn = inputs.get("partNumber")
        if pn:
            return str(pn)
    return "UNKNOWN"


def validate_component(category: str, component: dict[str, Any]) -> None:
    """Validate ``component`` against the schema for ``category``.

    The full envelope (as it will be written to disk) is passed in;
    the per-category unwrap callable in :data:`SCHEMA_MAP` extracts
    the payload the schema actually describes.

    Raises :class:`ValidationError` on the first failing payload.
    """
    validator = load_validator(category)
    _, unwrap = SCHEMA_MAP[category]
    payload = unwrap(component)  # may raise ValidationError on bad envelope

    errors = list(validator.iter_errors(payload))
    if not errors:
        return

    formatted: list[tuple[str, str]] = []
    for err in errors:
        path = ".".join(str(p) for p in err.path) if err.path else "(root)"
        formatted.append((path, err.message))
    raise ValidationError(category, _extract_mpn(component), formatted)


# ---------------------------------------------------------------------------
# Existence check
# ---------------------------------------------------------------------------


def _envelope_mpn(record: dict[str, Any]) -> str | None:
    """Extract the MPN from any known envelope variant, or ``None``."""
    # resistor
    inner = record.get("resistor")
    if isinstance(inner, dict):
        mi = inner.get("manufacturerInfo")
        if isinstance(mi, dict):
            ds = mi.get("datasheetInfo", {})
            if isinstance(ds, dict):
                part = ds.get("part", {})
                if isinstance(part, dict):
                    pn = part.get("partNumber")
                    if pn:
                        return str(pn)
    # capacitor — reference first, fall back to datasheetInfo.part.partNumber
    inner = record.get("capacitor")
    if isinstance(inner, dict):
        mi = inner.get("manufacturerInfo")
        if isinstance(mi, dict):
            ref = mi.get("reference")
            if ref:
                return str(ref)
            ds = mi.get("datasheetInfo", {})
            if isinstance(ds, dict):
                part = ds.get("part", {})
                if isinstance(part, dict):
                    pn = part.get("partNumber")
                    if pn:
                        return str(pn)
    # magnetic / semiconductor.diode / semiconductor.igbt / semiconductor / controller / mosfet
    inner = record.get("semiconductor")
    if isinstance(inner, dict):
        for nested in ("diode", "igbt", "bjt", "mosfet"):
            sub = inner.get(nested)
            if isinstance(sub, dict):
                mi = sub.get("manufacturerInfo")
                if isinstance(mi, dict):
                    ref = mi.get("reference")
                    if ref:
                        return str(ref)
        mi = inner.get("manufacturerInfo")
        if isinstance(mi, dict):
            ref = mi.get("reference")
            if ref:
                return str(ref)
    for envelope in ("magnetic", "controller"):
        inner = record.get(envelope)
        if isinstance(inner, dict):
            mi = inner.get("manufacturerInfo")
            if isinstance(mi, dict):
                ref = mi.get("reference")
                if ref:
                    return str(ref)
    # legacy top-level manufacturerInfo
    mi = record.get("manufacturerInfo")
    if isinstance(mi, dict):
        ref = mi.get("reference")
        if ref:
            return str(ref)
    # legacy inputs.partNumber
    inputs = record.get("inputs")
    if isinstance(inputs, dict):
        pn = inputs.get("partNumber")
        if pn:
            return str(pn)
    return None


def component_exists(category: str, part_number: str) -> bool:
    """Return ``True`` iff a row with the given MPN exists in ``category``.

    Comparison is case-insensitive (MPNs are canonically upper-case
    in datasheets but the database has both forms historically).

    Raises
    ------
    UnknownCategoryError
        If ``category`` is not in the whitelist.
    LibrarianError
        If the NDJSON file contains a line that is not valid JSON
        or that does not decode to a JSON object.  Corruption in
        the canonical database is a stop-the-line event.
    """
    _sa._validate_category(category)
    if not part_number:
        raise LibrarianError("component_exists: part_number must be a non-empty string")
    target = part_number.upper()

    path = _sa.TAS_DATA_DIR / f"{category}.ndjson"
    if not path.exists():
        return False

    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise LibrarianError(
                    f"component_exists({category!r}): corrupt JSON at "
                    f"{path}:{lineno}: {exc.msg} (col {exc.colno}).  "
                    "Investigate before continuing — TAS NDJSON is "
                    "append-only and corruption indicates a librarian "
                    "or filesystem bug."
                ) from exc
            if not isinstance(record, dict):
                raise LibrarianError(
                    f"component_exists({category!r}): {path}:{lineno} "
                    f"decodes to {type(record).__name__}, expected JSON object."
                )
            mpn = _envelope_mpn(record)
            if mpn is not None and mpn.upper() == target:
                return True
    return False


# ---------------------------------------------------------------------------
# Add
# ---------------------------------------------------------------------------


def add_component(category: str, component: dict[str, Any]) -> None:
    """Validate ``component`` and append it to ``TAS/data/<category>.ndjson``.

    Steps:

    1. Whitelist + schema-availability gate (via :func:`load_validator`).
    2. :func:`heaviside.librarian.guards.guard_component` — insert-time
       integrity guard (synthetic series taxonomy, placeholder /
       value-encoding MPNs, partNumber == series stubs, junk datasheet
       URLs, telemetry-shaped objects) **plus** schema validation via
       :func:`validate_component`.  Throws
       :class:`heaviside.librarian.guards.GuardRejectionError` /
       :class:`ValidationError`.
    3. :func:`component_exists` — throws
       :class:`DuplicateComponentError` if the MPN is already present.
    4. :func:`safe_append` — atomic line write under the category lock.

    Compact JSON is written (``separators=(',', ':')``) to match the
    on-disk format the rest of the codebase already uses.

    Raises
    ------
    UnknownCategoryError, SchemaNotFoundError, ValidationError,
    GuardRejectionError, DuplicateComponentError, LibrarianError
    """
    if not isinstance(component, dict):
        raise LibrarianError(
            f"add_component: component must be a dict, got {type(component).__name__}"
        )

    guard_component(category, component)

    mpn = _extract_mpn(component)
    if mpn == "UNKNOWN":
        raise LibrarianError(
            f"add_component({category!r}): component has no extractable "
            "MPN (manufacturerInfo.reference or .datasheetInfo.part."
            "partNumber).  The librarian will not write anonymous rows."
        )
    if component_exists(category, mpn):
        raise DuplicateComponentError(
            f"add_component({category!r}): MPN {mpn!r} already present in "
            f"{category}.ndjson.  Refusing to append a duplicate."
        )

    line = json.dumps(component, separators=(",", ":")) + "\n"
    with safe_append(category) as fh:
        fh.write(line)
