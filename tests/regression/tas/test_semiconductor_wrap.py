"""Strict schema-envelope invariant for the SAS rows in TAS/data/.

Mirrors the TAS-side migration at commit `44f7716` ("data: wrap SAS
device rows in `{semiconductor: {}}` envelope (Finding B)").  PEAS's
semiconductor discriminator branch expects:

    {"semiconductor": {"mosfet" | "diode" | "igbt" | "bjt": {...}}}

mirroring SAS.json, which is itself a oneOf wrapper over the four
device families.  The historical NDJSON shape was one level flatter:

    {"mosfet": {...}}   {"diode": {...}}   {"igbt": {...}}

After 44f7716 every row in mosfets/diodes/igbts.ndjson was supposed
to live in the wrapped envelope.  In practice the wrap has drifted:
several post-44f7716 merges (Vishay, TI, OpenConverters/TAS main)
re-introduced flat rows.  The realism gate at realism.py:444-449
accepts both shapes so the pipeline continues to work, but the
schema invariant is the canonical contract and any flat row is a
regression that must be repaired by the `component-librarian` agent
(per Proteus AGENTS.md guardrails an assistant must never edit
`TAS/data/*.ndjson` directly).

These tests therefore FAIL today by design — they are the CI gate
that surfaces the drift and pins the invariant for the agent work
that will repair it.  When the librarian repair lands, every row
must be wrapped and these tests must go green in the same
reviewed commit.

Diagnostic surface: each test that fails enumerates the first
five offending rows by their MPN / reference (when extractable),
so the librarian agent has actionable input rather than a bare
"N rows failed" count.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

# Locate TAS/data relative to the repo root (this file lives at
# tests/regression/tas/test_semiconductor_wrap.py).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DATA_DIR = _REPO_ROOT / "TAS" / "data"

# Expected (category, inner-key) per ndjson file.
_SAS_FILES: tuple[tuple[str, str], ...] = (
    ("mosfets.ndjson", "mosfet"),
    ("diodes.ndjson", "diode"),
    ("igbts.ndjson", "igbt"),
)


def _iter_rows(path: Path) -> Iterator[tuple[int, dict | None, str | None]]:
    """Yield ``(1-indexed line number, parsed JSON object | None, error | None)``
    for every non-empty line.  Malformed lines (corrupt JSON, unresolved
    merge-conflict markers, etc.) are surfaced as ``(lineno, None, msg)``
    rather than aborting iteration — so the caller can build a complete
    diagnostic report instead of stopping at the first bad row."""
    with path.open("r", encoding="utf-8") as fh:
        for i, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            # Catch unresolved git-merge conflict markers explicitly so
            # the report names them (they would otherwise show up as
            # bare 'Expecting value' JSON errors).
            if stripped.startswith(("<<<<<<<", "=======", ">>>>>>>")):
                yield i, None, f"unresolved merge-conflict marker: {stripped[:40]!r}"
                continue
            try:
                yield i, json.loads(stripped), None
            except json.JSONDecodeError as exc:
                yield i, None, f"malformed JSON: {exc.msg} at col {exc.colno}"


def _row_label(row: dict, inner_key: str) -> str:
    """Best-effort MPN extraction for diagnostic output.  Looks in
    both wrapped and flat shapes."""
    candidates = [
        row.get("semiconductor", {}).get(inner_key, {})
        if isinstance(row.get("semiconductor"), dict)
        else {},
        row.get(inner_key, {}) if isinstance(row.get(inner_key), dict) else {},
    ]
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        info = cand.get("manufacturerInfo")
        if isinstance(info, dict):
            ref = info.get("reference") or info.get("partNumber")
            name = info.get("name")
            if ref:
                return f"{name or '?'}/{ref}"
    return "<unknown>"


def _classify(row: dict, inner_key: str) -> str:
    """Return one of: 'wrapped', 'flat', 'other'."""
    if isinstance(row.get("semiconductor"), dict) and isinstance(
        row["semiconductor"].get(inner_key), dict
    ):
        return "wrapped"
    if isinstance(row.get(inner_key), dict):
        return "flat"
    return "other"


@pytest.fixture(scope="module")
def repo_layout_check() -> None:
    """Skip the entire module (don't fail) only if TAS/data is missing
    from the checkout — that's a workspace-setup issue, not a schema
    regression.  Missing individual files inside TAS/data still fails
    because that IS a regression."""
    if not _DATA_DIR.exists():
        pytest.skip(f"TAS/data not present at {_DATA_DIR} — submodule not initialised")


@pytest.mark.parametrize("filename,inner_key", _SAS_FILES)
def test_every_row_uses_semiconductor_envelope(
    repo_layout_check, filename: str, inner_key: str
) -> None:
    """Every SAS row in mosfets/diodes/igbts.ndjson MUST be in the
    {semiconductor: {<inner>: {...}}} envelope per PEAS's
    discriminator (mirrors TAS commit 44f7716).

    If this test fails: do NOT edit TAS/data/*.ndjson by hand
    (guardrails violation).  Invoke the `component-librarian` agent
    to re-wrap the offending rows.  The librarian's
    `wrap_semiconductor_data.py` script is idempotent and creates
    .pre_semiconductor_wrap.bak backups.
    """
    path = _DATA_DIR / filename
    assert path.exists(), (
        f"{filename}: missing from TAS/data/ — librarian regression or stale checkout"
    )

    counts = {"wrapped": 0, "flat": 0, "other": 0}
    flat_samples: list[str] = []
    other_samples: list[str] = []
    malformed: list[str] = []

    for lineno, row, err in _iter_rows(path):
        if err is not None:
            if len(malformed) < 5:
                malformed.append(f"L{lineno} {err}")
            continue
        assert row is not None
        kind = _classify(row, inner_key)
        counts[kind] += 1
        if kind == "flat" and len(flat_samples) < 5:
            flat_samples.append(f"L{lineno} {_row_label(row, inner_key)}")
        elif kind == "other" and len(other_samples) < 5:
            other_samples.append(f"L{lineno} {sorted(row.keys())[:3]}")

    total = sum(counts.values())
    assert total > 0, f"{filename}: empty file — librarian / LFS regression"

    bad = counts["flat"] + counts["other"]
    if bad == 0 and not malformed:
        return  # all rows wrapped and well-formed — invariant holds

    msg = [
        f"{filename}: {bad}/{total} rows violate the semiconductor-wrap "
        f"invariant ({counts['wrapped']} wrapped, {counts['flat']} flat, "
        f"{counts['other']} other) + {len(malformed)} malformed lines.",
        "",
        "Per TAS commit 44f7716 every SAS row must be in the "
        "{semiconductor: {" + inner_key + ": {...}}} envelope.  The realism "
        "gate accepts both shapes for backwards compatibility, but the "
        "schema contract is the wrapped form and any flat row is a "
        "regression that must be repaired.",
        "",
        "Repair path: invoke the component-librarian agent.  Do NOT edit "
        "TAS/data/*.ndjson directly (Proteus AGENTS.md guardrails).",
        "",
    ]
    if malformed:
        msg.append("First malformed lines (likely unresolved merge conflicts):")
        msg.extend(f"  - {s}" for s in malformed)
        msg.append("")
    if flat_samples:
        msg.append("First flat rows (line / MPN):")
        msg.extend(f"  - {s}" for s in flat_samples)
        msg.append("")
    if other_samples:
        msg.append("First 'other'-shape rows (line / top-level keys):")
        msg.extend(f"  - {s}" for s in other_samples)
    pytest.fail("\n".join(msg))


def test_inner_key_matches_file_category(repo_layout_check) -> None:
    """A row in mosfets.ndjson must not carry a 'diode' or 'igbt'
    inner key (in either the wrapped or flat shape) — cross-category
    leakage indicates an importer bug, not just a missed wrap."""
    foreign_keys = {"mosfet", "diode", "igbt", "bjt"}
    offenders: list[str] = []
    for filename, inner_key in _SAS_FILES:
        path = _DATA_DIR / filename
        if not path.exists():
            continue
        unexpected = foreign_keys - {inner_key}
        for lineno, row, err in _iter_rows(path):
            if err is not None or row is None:
                continue
            wrapped = row.get("semiconductor") if isinstance(row.get("semiconductor"), dict) else {}
            for fk in unexpected:
                if fk in wrapped or fk in row:
                    offenders.append(
                        f"{filename}:L{lineno} contains foreign key {fk!r} "
                        f"(expected only {inner_key!r})"
                    )
                    if len(offenders) >= 10:
                        break
            if len(offenders) >= 10:
                break
        if len(offenders) >= 10:
            break

    if offenders:
        pytest.fail(
            "Cross-category SAS leakage detected — importer bug, "
            "do NOT hand-edit, invoke component-librarian:\n  " + "\n  ".join(offenders)
        )
