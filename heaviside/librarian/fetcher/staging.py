"""Two-stage write pipeline: distributor fetch → staging file → TAS.

Why staging?  Distributor payloads vary in quality: Digi-Key
typically delivers all six SAS-required electrical fields, but
Mouser frequently omits Vgs(th) or Coss, and both occasionally
return obviously bogus numbers (e.g. Rds(on) of 0 Ω for parts
without a value reported).  The staging layer is the formal
hand-off where the ``component-auditor`` agent inspects converted
payloads before they enter :data:`heaviside.librarian.TAS_DATA_DIR`.

Workflow
--------

1. The fetcher converts a Digi-Key / Mouser payload via
   :func:`heaviside.librarian.fetcher.convert.convert_digikey_to_tas_mosfet`
   (or its sibling).
2. The converted envelope is written to
   ``<STAGING_DIR>/<category>/<source>-<mpn>.json`` via
   :func:`stage_fetch` — this never touches ``TAS/data/``.
3. The ``component-auditor`` reviews the staging file (manually or
   via tooling) and either approves (calls :func:`apply_staged`,
   which schema-validates and appends to TAS) or rejects (deletes
   the staging file or moves it to ``rejected/``).
4. Applied files are archived to ``<STAGING_DIR>/<category>/applied/``
   so the audit trail is preserved.

The staging directory is controlled by ``HEAVISIDE_STAGING_DIR``
(falls back to ``<repo>/staging/``).  Tests retarget it via
:func:`monkeypatch.setattr(staging, "STAGING_DIR", tmp_path)`.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from heaviside.librarian import safe_access as _sa
from heaviside.librarian.fetcher.base import FetcherError
from heaviside.librarian.guards import guard_component
from heaviside.librarian.tas import add_component

__all__ = [
    "STAGING_DIR",
    "StagedRecord",
    "StagingError",
    "apply_staged",
    "list_staged",
    "stage_fetch",
]


_REPO_ROOT = Path(__file__).resolve().parents[3]
STAGING_DIR: Path = Path(os.environ.get("HEAVISIDE_STAGING_DIR") or (_REPO_ROOT / "staging"))

_KNOWN_SOURCES = {"digikey", "mouser", "datasheet", "manual"}


class StagingError(FetcherError):
    """A staging-layer operation failed (path, validation, or move)."""


@dataclass(frozen=True)
class StagedRecord:
    """A single staging-file payload."""

    path: Path
    category: str
    source: str
    mpn: str
    component: dict[str, Any]
    staged_at: float

    @classmethod
    def from_path(cls, path: Path) -> StagedRecord:
        if not path.exists():
            raise StagingError(f"staging file does not exist: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StagingError(f"{path}: invalid JSON: {exc.msg} (line {exc.lineno})") from exc
        if not isinstance(payload, dict):
            raise StagingError(
                f"{path}: top-level JSON must be an object, got {type(payload).__name__}"
            )
        try:
            category = payload["category"]
            source = payload["source"]
            mpn = payload["mpn"]
            component = payload["component"]
            staged_at = payload["staged_at"]
        except KeyError as exc:
            raise StagingError(
                f"{path}: staging file missing required key: {exc.args[0]!r}"
            ) from exc
        if not isinstance(component, dict):
            raise StagingError(
                f"{path}: 'component' must be an object, got {type(component).__name__}"
            )
        return cls(
            path=path,
            category=str(category),
            source=str(source),
            mpn=str(mpn),
            component=component,
            staged_at=float(staged_at),
        )


# ---------------------------------------------------------------------------
# Filename hygiene
# ---------------------------------------------------------------------------


# Per IEC 60062 part numbers may contain slashes, plus signs, and
# other awkward characters.  Sanitise aggressively for the file
# name; the original MPN lives inside the JSON payload, so this
# transformation is one-way only.
_MPN_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_mpn_filename(mpn: str) -> str:
    cleaned = _MPN_UNSAFE.sub("_", mpn).strip("_")
    return cleaned or "unknown"


# ---------------------------------------------------------------------------
# stage_fetch
# ---------------------------------------------------------------------------


def stage_fetch(
    category: str,
    mpn: str,
    component: dict[str, Any],
    *,
    source: str,
    raw_response: dict[str, Any] | None = None,
    staging_root: Path | None = None,
) -> Path:
    """Persist a converted distributor payload to the staging area.

    Args:
        category: One of :data:`heaviside.librarian.CATEGORIES`.
        mpn: Manufacturer part number (used for the filename + audit trail).
        component: The converted TAS envelope (e.g. ``{"mosfet": {...}}``).
            *Not* schema-validated at this point — staging accepts
            partial payloads on purpose so the auditor sees the
            problems.
        source: ``"digikey"``, ``"mouser"``, ``"datasheet"``, or
            ``"manual"``.  Anything else raises.
        raw_response: Optional raw distributor JSON, preserved for
            audit / repro.
        staging_root: Override :data:`STAGING_DIR` (test hook).

    Returns:
        The path the staging record was written to.
    """
    if category not in _sa.CATEGORIES:
        raise StagingError(f"unknown category {category!r}.  Known: {sorted(_sa.CATEGORIES)}")
    if source not in _KNOWN_SOURCES:
        raise StagingError(f"unknown source {source!r}.  Known: {sorted(_KNOWN_SOURCES)}")
    if not isinstance(mpn, str) or not mpn.strip():
        raise StagingError(f"mpn must be a non-empty string, got {mpn!r}")
    if not isinstance(component, dict) or not component:
        raise StagingError("component must be a non-empty dict")

    # Insert-time integrity guard, pattern checks only.  Schema AND physics
    # validation AND the anonymous-row check are deliberately deferred to
    # apply_staged → add_component because staging accepts partial payloads for
    # the auditor by contract (a partial part has no values to physics-check
    # yet).  A synthetic series, placeholder MPN, or junk datasheet URL can
    # never become valid, so those are rejected before they even reach staging.
    guard_component(
        category, component, validate_schema=False, validate_physics=False, require_mpn=False
    )

    root = staging_root if staging_root is not None else STAGING_DIR
    target_dir = root / category
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{source}-{_safe_mpn_filename(mpn)}.json"
    target = target_dir / filename

    payload = {
        "category": category,
        "source": source,
        "mpn": mpn,
        "staged_at": time.time(),
        "component": component,
    }
    if raw_response is not None:
        payload["raw_response"] = raw_response

    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, target)
    return target


# ---------------------------------------------------------------------------
# apply_staged
# ---------------------------------------------------------------------------


def apply_staged(
    staging_path: Path,
    *,
    archive: bool = True,
) -> dict[str, Any]:
    """Schema-validate a staged record and append it to TAS.

    On success the staging file is moved to ``<category>/applied/``
    (set ``archive=False`` to leave it in place — useful for
    re-application during tests).  On failure the staging file is
    untouched and the underlying validation / lock error propagates.

    Returns:
        A summary dict ``{category, mpn, source, applied_path,
        archive_path}`` (the latter ``None`` when ``archive=False``).
    """
    record = StagedRecord.from_path(staging_path)

    # Delegate strict validation + atomic write to the librarian
    # writer; it raises ValidationError / DuplicateComponentError /
    # LockTimeoutError on failure, none of which we suppress.
    add_component(record.category, record.component)

    archive_path: Path | None = None
    if archive:
        applied_dir = staging_path.parent / "applied"
        applied_dir.mkdir(parents=True, exist_ok=True)
        # Suffix with the staging timestamp so re-applies do not
        # clobber prior history.
        ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime(record.staged_at))
        archive_path = applied_dir / f"{ts}-{staging_path.name}"
        os.replace(staging_path, archive_path)

    return {
        "category": record.category,
        "mpn": record.mpn,
        "source": record.source,
        "applied_path": str(record.path),
        "archive_path": str(archive_path) if archive_path else None,
    }


# ---------------------------------------------------------------------------
# list_staged
# ---------------------------------------------------------------------------


def list_staged(
    category: str | None = None,
    *,
    staging_root: Path | None = None,
) -> list[StagedRecord]:
    """Return every pending staging record (excluding ``applied/`` archives)."""
    root = staging_root if staging_root is not None else STAGING_DIR
    if category is not None and category not in _sa.CATEGORIES:
        raise StagingError(f"unknown category {category!r}.  Known: {sorted(_sa.CATEGORIES)}")
    if not root.exists():
        return []

    if category is None:
        category_dirs = [root / cat for cat in _sa.CATEGORIES if (root / cat).is_dir()]
    else:
        category_dir = root / category
        category_dirs = [category_dir] if category_dir.is_dir() else []

    records: list[StagedRecord] = []
    for cat_dir in category_dirs:
        for entry in sorted(cat_dir.iterdir()):
            # Skip the ``applied/`` archive and any other subdirs.
            if entry.is_dir():
                continue
            if entry.suffix != ".json":
                continue
            records.append(StagedRecord.from_path(entry))
    return records
