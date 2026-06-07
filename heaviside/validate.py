"""TAS-conformance gate.

Phase B deliverable: a validator that checks whether a TAS document
conforms to the layered schema stack (TAS root + per-component PEAS +
URI shape), producing an actionable violation list.

The gate has two modes:

* **strict** (default): TAS root validation, PEAS root validation for
  every component whose ``data`` is an inline object, and URI shape
  check for every component whose ``data`` is a string. This is the
  full two-phase contract.
* **tas-only** (``strict=False``): TAS root validation only, skipping
  PEAS conformance and URI shape. Useful while the producer side
  (stencils) is still emitting placeholder URIs.

The validator does **not** dereference data URIs (i.e. it does not
load ``TAS/data/mosfets.ndjson`` and check that the referenced part
exists). That belongs to a separate "binding gate" downstream of the
component-librarian agent.

Per CLAUDE.md "no fallbacks, throw": tooling errors (schema load
failures, unreadable input, malformed JSON) raise loudly rather than
producing a soft pass.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable

# ---------------------------------------------------------------------------
# Schema discovery
# ---------------------------------------------------------------------------

# Roots we load $id-bearing schemas from. The vendor copy of MAS
# (``vendor/PyOpenMagnetics/_mas_local``) is excluded — the real MAS
# submodule at ``MAS/`` is the source of truth; including both causes
# $id collisions.
_SCHEMA_ROOTS: tuple[str, ...] = (
    "TAS/schemas",
    "PEAS/schemas",
    "MAS/schemas",
    "CAS/schemas",
    "SAS/schemas",
    "RAS/schemas",
    "COAS/schemas",
)

# Repo root resolved relative to this file (``heaviside/validate.py``).
_REPO_ROOT = Path(__file__).resolve().parent.parent

# TAS root + PEAS root $ids — looked up once at module load.
_TAS_ROOT_ID = "https://psma.com/tas/TAS.json"
_PEAS_ROOT_ID = "https://psma.com/peas/peas.json"

# URI shape for component.data string form, e.g.
# ``TAS/data/mosfets.ndjson?partNumber=C3M0032120K``.
_DATA_URI_RE = re.compile(r"^TAS/data/(?P<file>[a-zA-Z0-9_]+\.ndjson)(\?[A-Za-z0-9._=&%~+\-/:]*)?$")


class ValidatorError(RuntimeError):
    """Raised on tooling errors (cannot load schemas, malformed input)."""


# ---------------------------------------------------------------------------
# Registry construction
# ---------------------------------------------------------------------------


def _iter_schema_files() -> Iterable[Path]:
    for root in _SCHEMA_ROOTS:
        root_path = _REPO_ROOT / root
        if not root_path.is_dir():
            continue
        yield from root_path.rglob("*.json")


def _build_registry() -> tuple[Registry, dict[str, Any], dict[str, Any]]:
    """Load every schema with an ``$id`` into a ``referencing.Registry``.

    Returns ``(registry, tas_root_schema, peas_root_schema)`` so the
    callers can hand the two roots to ``Draft202012Validator`` without
    re-walking the registry.

    Raises
    ------
    ValidatorError
        If TAS or PEAS root schema is not found, or if any schema file
        fails to parse.
    """
    resources: list[tuple[str, Resource]] = []
    seen: dict[str, Path] = {}
    tas_root: Any = None
    peas_root: Any = None

    for path in _iter_schema_files():
        try:
            doc = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise ValidatorError(
                f"failed to parse schema {path.relative_to(_REPO_ROOT)}: {e}"
            ) from e
        if not isinstance(doc, dict):
            continue
        sid = doc.get("$id")
        if not isinstance(sid, str):
            continue
        if sid in seen:
            # First registration wins; this preferes the canonical
            # submodule path over vendor copies because _SCHEMA_ROOTS
            # lists the submodules first.
            continue
        seen[sid] = path
        resources.append((sid, Resource.from_contents(doc)))
        if sid == _TAS_ROOT_ID:
            tas_root = doc
        elif sid == _PEAS_ROOT_ID:
            peas_root = doc

    if tas_root is None:
        raise ValidatorError(f"TAS root schema not found (expected $id={_TAS_ROOT_ID!r})")
    if peas_root is None:
        raise ValidatorError(f"PEAS root schema not found (expected $id={_PEAS_ROOT_ID!r})")

    registry: Registry = Registry().with_resources(resources)
    return registry, tas_root, peas_root


# ---------------------------------------------------------------------------
# Violations
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Violation:
    """A single conformance failure."""

    path: str  # JSON-pointer-ish path, e.g. "stages[0].circuit.components[2]"
    code: str  # short machine-readable code, e.g. "tas_root", "peas_root", "uri_shape"
    message: str  # human-readable explanation
    component_name: str | None = None
    component_index: tuple[int, int] | None = None  # (stage_idx, comp_idx) if relevant

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "path": self.path,
            "code": self.code,
            "message": self.message,
        }
        if self.component_name is not None:
            out["component"] = self.component_name
        if self.component_index is not None:
            out["index"] = list(self.component_index)
        return out


@dataclass(frozen=True, slots=True)
class Report:
    """Result of one ``validate_tas`` invocation."""

    violations: tuple[Violation, ...] = ()
    strict: bool = True

    @property
    def ok(self) -> bool:
        return not self.violations

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.ok

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "strict": self.strict,
            "violation_count": len(self.violations),
            "violations": [v.as_dict() for v in self.violations],
        }


# ---------------------------------------------------------------------------
# Core validation
# ---------------------------------------------------------------------------


# Module-level cached registry so repeated CLI invocations / tests don't
# re-walk the filesystem. Rebuilt lazily.
_REGISTRY_CACHE: tuple[Registry, Any, Any] | None = None


def _registry() -> tuple[Registry, Any, Any]:
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is None:
        _REGISTRY_CACHE = _build_registry()
    return _REGISTRY_CACHE


def _format_jsonpath(prefix: str, abs_path: Iterable[Any]) -> str:
    """Format a ``jsonschema`` error's ``absolute_path`` as a readable path."""
    out = prefix
    for elem in abs_path:
        if isinstance(elem, int):
            out += f"[{elem}]"
        else:
            out += f".{elem}"
    return out


def _iter_components(
    tas: Mapping[str, Any],
) -> Iterable[tuple[int, int, Mapping[str, Any]]]:
    topology = tas.get("topology")
    if not isinstance(topology, Mapping):
        return
    stages = topology.get("stages")
    if not isinstance(stages, list):
        return
    for si, stage in enumerate(stages):
        if not isinstance(stage, Mapping):
            continue
        circuit = stage.get("circuit")
        if not isinstance(circuit, Mapping):
            continue
        comps = circuit.get("components")
        if not isinstance(comps, list):
            continue
        for ci, c in enumerate(comps):
            if isinstance(c, Mapping):
                yield si, ci, c


def _validate_tas_root(
    tas: Mapping[str, Any],
    registry: Registry,
    tas_root: Any,
) -> list[Violation]:
    validator = Draft202012Validator(tas_root, registry=registry)
    out: list[Violation] = []
    try:
        errors = sorted(validator.iter_errors(tas), key=lambda e: list(e.absolute_path))
    except (Unresolvable, SchemaError) as exc:
        # TAS root cross-references PEAS (via component.data oneOf),
        # which in turn cross-references MAS / CAS / SAS / RAS via
        # filesystem-relative $refs. If those fail to resolve through
        # the registry, surface as one ``schema_ref`` violation rather
        # than crashing.
        return [
            Violation(
                path="tas",
                code="schema_ref",
                message=(
                    f"TAS root validation could not complete: schema reference unresolvable ({exc})"
                ),
            )
        ]
    for err in errors:
        out.append(
            Violation(
                path=_format_jsonpath("tas", err.absolute_path),
                code="tas_root",
                message=err.message,
            )
        )
    return out


def _validate_component_peas(
    comp: Mapping[str, Any],
    si: int,
    ci: int,
    registry: Registry,
    peas_root: Any,
) -> list[Violation]:
    """Validate one component whose ``data`` is an inline PEAS document.

    Components whose ``data`` is a URI string are skipped here (URI
    shape is checked separately) — there is no PEAS doc to validate.
    Components with no ``data`` at all are reported as a violation in
    strict mode because TAS schema requires the field.
    """
    name = comp.get("name") if isinstance(comp.get("name"), str) else None
    path_prefix = f"topology.stages[{si}].circuit.components[{ci}]"

    data = comp.get("data")
    if data is None:
        return [
            Violation(
                path=f"{path_prefix}.data",
                code="missing_data",
                message="component.data is required by TAS schema",
                component_name=name,
                component_index=(si, ci),
            )
        ]
    if isinstance(data, str):
        # URI form — PEAS validation N/A. Caller handles URI shape.
        return []
    if not isinstance(data, Mapping):
        return [
            Violation(
                path=f"{path_prefix}.data",
                code="peas_root",
                message=f"component.data must be an inline PEAS document or URI string, got {type(data).__name__}",
                component_name=name,
                component_index=(si, ci),
            )
        ]

    validator = Draft202012Validator(peas_root, registry=registry)
    out: list[Violation] = []
    try:
        errors = sorted(
            validator.iter_errors(dict(data)),
            key=lambda e: list(e.absolute_path),
        )
    except (Unresolvable, SchemaError) as exc:
        # The PEAS root cross-references MAS / CAS / SAS / RAS schemas
        # via filesystem-relative $refs (e.g. ``../../MAS/schemas/...``).
        # If those refs cannot be resolved through the registry (currently
        # a known schema-wiring bug: PEAS uses relative paths while MAS
        # publishes ``$id`` under a different host), surface it as a
        # single ``schema_ref`` violation rather than crashing — the gate
        # is meant to be diagnostic.
        return [
            Violation(
                path=f"{path_prefix}.data",
                code="schema_ref",
                message=(
                    "PEAS root validation could not complete: schema "
                    f"reference unresolvable ({exc})"
                ),
                component_name=name,
                component_index=(si, ci),
            )
        ]
    for err in errors:
        out.append(
            Violation(
                path=_format_jsonpath(f"{path_prefix}.data", err.absolute_path),
                code="peas_root",
                message=err.message,
                component_name=name,
                component_index=(si, ci),
            )
        )
    return out


def _validate_uri_shape(comp: Mapping[str, Any], si: int, ci: int) -> list[Violation]:
    data = comp.get("data")
    if not isinstance(data, str):
        return []
    name = comp.get("name") if isinstance(comp.get("name"), str) else None
    path = f"topology.stages[{si}].circuit.components[{ci}].data"

    # The TAS schema says the URI form is a path into TAS/data/<file>.ndjson
    # plus an optional query string identifying the part. ``?placeholder``
    # is the stencil's pre-bridge sentinel and is a violation in strict
    # mode — the gate is meant to catch exactly that condition.
    if "?placeholder" in data:
        return [
            Violation(
                path=path,
                code="placeholder_uri",
                message=(
                    "component.data is still the stencil placeholder URI "
                    f"({data!r}) — bridge / librarian attach phase has not run"
                ),
                component_name=name,
                component_index=(si, ci),
            )
        ]

    if not _DATA_URI_RE.match(data):
        return [
            Violation(
                path=path,
                code="uri_shape",
                message=(
                    "component.data string does not match expected shape "
                    f"'TAS/data/<file>.ndjson[?<query>]', got {data!r}"
                ),
                component_name=name,
                component_index=(si, ci),
            )
        ]

    # Sanity check the URI is parseable.
    try:
        urlparse(data)
    except ValueError as e:
        return [
            Violation(
                path=path,
                code="uri_shape",
                message=f"component.data URI failed to parse: {e}",
                component_name=name,
                component_index=(si, ci),
            )
        ]

    return []


def validate_tas(
    tas: Mapping[str, Any] | dict[str, Any],
    *,
    strict: bool = True,
) -> Report:
    """Validate a TAS document.

    Parameters
    ----------
    tas : Mapping
        Parsed TAS document.
    strict : bool
        If True (default) run all three layers (TAS root + per-component
        PEAS + URI shape). If False, run only the TAS root layer.

    Returns
    -------
    Report
        Snapshot of all violations found. Empty ``violations`` means the
        document conforms.
    """
    if not isinstance(tas, Mapping):
        raise ValidatorError(f"validate_tas: tas must be a mapping, got {type(tas).__name__}")

    registry, tas_root, peas_root = _registry()

    violations: list[Violation] = []
    violations.extend(_validate_tas_root(tas, registry, tas_root))

    if strict:
        for si, ci, comp in _iter_components(tas):
            violations.extend(_validate_component_peas(comp, si, ci, registry, peas_root))
            violations.extend(_validate_uri_shape(comp, si, ci))

    return Report(violations=tuple(violations), strict=strict)


def validate_tas_file(
    path: Path | str,
    *,
    strict: bool = True,
) -> Report:
    """Convenience wrapper: read a TAS JSON file and validate."""
    p = Path(path)
    try:
        raw = p.read_text()
    except OSError as e:
        raise ValidatorError(f"cannot read {p}: {e}") from e
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValidatorError(f"{p} is not valid JSON: {e}") from e
    return validate_tas(doc, strict=strict)


__all__ = [
    "Report",
    "ValidatorError",
    "Violation",
    "validate_tas",
    "validate_tas_file",
]
