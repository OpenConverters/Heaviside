"""Distilled knowledge files used as agent context.

Files under ``heaviside/knowledge/<topic>/<name>.md`` are loaded by
:func:`read_knowledge` and surfaced to Strands agents as a tool.

The set ported in v0.1 covers the component-database schemas the
``component-librarian`` and ``component-auditor`` agents need.  No
runtime logic — these are static reference documents.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["KNOWLEDGE_ROOT", "available_topics", "read_knowledge"]


KNOWLEDGE_ROOT: Path = Path(__file__).resolve().parent


def available_topics() -> dict[str, list[str]]:
    """Return ``{subdir: [name, ...]}`` for every knowledge file on disk."""
    out: dict[str, list[str]] = {}
    for sub in sorted(p for p in KNOWLEDGE_ROOT.iterdir() if p.is_dir()):
        if sub.name == "__pycache__":
            continue
        out[sub.name] = sorted(p.stem for p in sub.glob("*.md"))
    return out


def read_knowledge(name: str) -> str:
    """Return the text of a knowledge file by stem (e.g. ``"peas-schema"``).

    Searches every subdirectory under :data:`KNOWLEDGE_ROOT` for a
    ``<name>.md`` file.  Raises :class:`FileNotFoundError` if none
    matches (no silent empty string — a missing knowledge file is a
    real bug, not a transient).

    Raises
    ------
    ValueError
        If ``name`` is empty or contains a path separator.
    FileNotFoundError
        If no ``<name>.md`` exists under :data:`KNOWLEDGE_ROOT`.
    LookupError
        If more than one match is found (ambiguous — fix by
        renaming or qualifying the request).
    """
    if not name or "/" in name or "\\" in name:
        raise ValueError(
            f"read_knowledge: invalid name {name!r} — pass the bare "
            "file stem, e.g. 'peas-schema'."
        )
    matches = sorted(KNOWLEDGE_ROOT.glob(f"**/{name}.md"))
    if not matches:
        topics = available_topics()
        raise FileNotFoundError(
            f"read_knowledge({name!r}): no such knowledge file under "
            f"{KNOWLEDGE_ROOT}.  Available: {topics}"
        )
    if len(matches) > 1:
        raise LookupError(
            f"read_knowledge({name!r}): ambiguous — matched "
            f"{[str(m.relative_to(KNOWLEDGE_ROOT)) for m in matches]}."
        )
    return matches[0].read_text(encoding="utf-8")
