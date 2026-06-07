"""Typed NDJSON reader for TAS component categories.

Streams one envelope (e.g. ``{"semiconductor": {"mosfet": {...}}}``) per
line. Per CLAUDE.md "no silent fallbacks": malformed lines raise; the
caller decides whether to skip a single row (catch the exception) or
abort the whole sweep.

Kept separate from ``heaviside.librarian.auditor._iter_records`` so the
selector's read path has no dependency on the auditor's audit-trail
plumbing.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any


class CatalogueReadError(RuntimeError):
    """Raised for I/O or JSON-decode failures on a TAS NDJSON file."""

    def __init__(self, path: Path, lineno: int, detail: str) -> None:
        super().__init__(f"{path}:{lineno}: {detail}")
        self.path = path
        self.lineno = lineno
        self.detail = detail


def iter_envelopes(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield ``(lineno, envelope)`` pairs from a TAS NDJSON file.

    ``envelope`` is the raw decoded JSON object — for mosfets that's
    ``{"semiconductor": {"mosfet": {...}}}``, for capacitors it's
    ``{"capacitor": {...}}``, etc. Callers narrow into the typed
    sub-document themselves (the schema varies per category).
    """
    if not path.is_file():
        raise CatalogueReadError(path, 0, "TAS catalogue file does not exist")
    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                env = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise CatalogueReadError(
                    path,
                    lineno,
                    f"JSON decode error: {exc.msg} at col {exc.colno}",
                ) from exc
            if not isinstance(env, dict):
                raise CatalogueReadError(
                    path,
                    lineno,
                    f"top-level value is {type(env).__name__}, expected object",
                )
            yield lineno, env


__all__ = ["CatalogueReadError", "iter_envelopes"]
