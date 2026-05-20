"""TAS component-database integrity tests.

Read-only smoke / integrity checks on every `TAS/data/*.ndjson` file
that the pipeline actually consults.  These tests do NOT modify any
NDJSON (per AGENTS.md hard guardrail — only the `component-librarian`
agent may); they exist purely to surface drift early and pin invariants
the realism gate / extractors rely on.

Coverage:

  1. **Valid NDJSON** — every non-empty line parses as JSON, no
     unresolved git merge-conflict markers anywhere in the file.
  2. **Row-count regression band** — current row count compared against
     a committed baseline with a ±10 % tolerance.  Catches both
     accidental deletion (which the realism gate would silently absorb
     as UNAVAILABLE) and bulk untracked appends (which suggest a
     librarian campaign ran outside the agent envelope).  The
     baseline is updated in the same reviewed commit when the
     librarian legitimately grows the corpus.
  3. **Schema-envelope shape** — each row has the expected outer
     discriminator key (e.g. `{"semiconductor": {...}}` for SAS
     devices, `{"capacitor": {...}}` for CAS rows).  This is the
     contract every extractor and the realism gate's component readers
     depend on.

The existing `test_semiconductor_wrap.py` deep-dives the SAS wrap
specifically; this file is the broader sweep.  Failures must be
referred to the `component-librarian` agent for repair.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DATA_DIR = _REPO_ROOT / "TAS" / "data"

# Files actively consulted by the pipeline (excludes *.backup, *.quarantine,
# *_staged_*, and *.v1.backup files which are out-of-band archives).
# Per AGENTS.md component-database table, these are the seven primary
# component categories plus converters and controllers.
_ACTIVE_FILES: tuple[str, ...] = (
    "capacitors.ndjson",
    "connectors.ndjson",
    "controllers.ndjson",
    "converters.ndjson",
    "diodes.ndjson",
    "igbts.ndjson",
    "magnetics.ndjson",
    "mosfets.ndjson",
    "resistors.ndjson",
)

# Row-count baselines pinned at the date the test landed.
# Tolerance is ±10 % — a librarian campaign that doubles the corpus
# must update this baseline in the same reviewed commit (forcing the
# diff to surface in code review) rather than silently sliding it.
_ROW_COUNT_BASELINE: dict[str, int] = {
    "capacitors.ndjson": 134_956,
    "connectors.ndjson": 14,
    "controllers.ndjson": 1_667,
    "converters.ndjson": 48,
    "diodes.ndjson": 7_839,
    "igbts.ndjson": 3_587,
    "magnetics.ndjson": 50_553,
    "mosfets.ndjson": 7_607,
    "resistors.ndjson": 117_472,
}
_ROW_COUNT_TOLERANCE = 0.10

# Expected outer-envelope discriminator key for each file.  Connectors,
# controllers, and converters do not (yet) use a discriminator wrap
# at TAS layer — extractors read them flat.  When/if a schema migration
# adds wrappers, this dict must be updated in the same commit that
# migrates the data.
_ENVELOPE_KEY: dict[str, str | tuple[str, ...] | None] = {
    "capacitors.ndjson":   "capacitor",
    "connectors.ndjson":   None,
    "controllers.ndjson":  None,
    "converters.ndjson":   None,   # converters use inputs/topology at root
    "diodes.ndjson":       "semiconductor",
    "igbts.ndjson":        "semiconductor",
    "magnetics.ndjson":    "magnetic",
    "mosfets.ndjson":      "semiconductor",
    "resistors.ndjson":    "resistor",
}

# Lines that indicate an unresolved git merge conflict — must never
# appear in any committed NDJSON.
_CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")

# Known drift pinned pending component-librarian repair.  These are
# strict xfails: the day the librarian repairs the corpus the test
# will XPASS and pytest will fail the suite, forcing this marker to
# be removed in the same commit that lands the repair.
_KNOWN_DRIFT_CONFLICT = {"mosfets.ndjson"}
_KNOWN_DRIFT_ENVELOPE = {"mosfets.ndjson"}


def _mark_known_drift(filename: str, known_set: set[str], reason: str):
    """Build a parametrize value with a strict-xfail marker when the
    filename is on the known-drift list."""
    if filename in known_set:
        return pytest.param(
            filename,
            marks=pytest.mark.xfail(
                strict=True,
                reason=reason,
            ),
        )
    return filename


def _iter_lines(path: Path) -> Iterator[tuple[int, str]]:
    with path.open("r", encoding="utf-8") as fh:
        for i, raw in enumerate(fh, start=1):
            yield i, raw


# ---------------------------------------------------------------------------
# 1. Valid NDJSON + no merge-conflict markers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", _ACTIVE_FILES)
def test_file_exists_and_nonempty(filename: str) -> None:
    path = _DATA_DIR / filename
    assert path.is_file(), f"TAS/data/{filename} missing"
    assert path.stat().st_size > 0, f"TAS/data/{filename} is empty"


@pytest.mark.parametrize(
    "filename",
    [
        _mark_known_drift(
            f,
            _KNOWN_DRIFT_CONFLICT,
            "mosfets.ndjson carries unresolved git merge-conflict "
            "markers at L2802/L2806/L2810 — pinned by "
            "test_semiconductor_wrap.py; awaiting component-librarian "
            "repair (forbidden to fix by hand per AGENTS.md guardrail).",
        )
        for f in _ACTIVE_FILES
    ],
)
def test_no_merge_conflict_markers(filename: str) -> None:
    """Bare-line merge markers corrupt the file for every downstream
    consumer (extractor, librarian audit, downstream training).
    `mosfets.ndjson` is known to carry these markers today; that is
    flagged here too rather than only via the wrap test, so the
    failure mode is self-explanatory."""
    path = _DATA_DIR / filename
    offenders: list[str] = []
    for lineno, raw in _iter_lines(path):
        stripped = raw.rstrip("\n")
        # Require the marker to be at line start so we don't false-positive
        # on legitimate `>>>>>>>` inside JSON string values (rare).
        for marker in _CONFLICT_MARKERS:
            if stripped.startswith(marker):
                offenders.append(f"L{lineno}: {stripped[:60]!r}")
                break
        if len(offenders) >= 5:
            break
    assert not offenders, (
        f"TAS/data/{filename} contains git merge-conflict markers — "
        f"must be repaired by component-librarian.  First offenders:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("filename", _ACTIVE_FILES)
def test_every_line_parses_as_json(filename: str) -> None:
    """Every non-empty line must be valid JSON (NDJSON contract)."""
    path = _DATA_DIR / filename
    bad: list[str] = []
    for lineno, raw in _iter_lines(path):
        s = raw.strip()
        if not s:
            continue
        # Skip lines that are conflict markers — the dedicated test
        # above surfaces those with a clearer error.
        if any(s.startswith(m) for m in _CONFLICT_MARKERS):
            continue
        try:
            json.loads(s)
        except json.JSONDecodeError as exc:
            bad.append(f"L{lineno}: {exc.msg}")
            if len(bad) >= 5:
                break
    assert not bad, (
        f"TAS/data/{filename} has malformed JSON lines:\n  "
        + "\n  ".join(bad)
    )


# ---------------------------------------------------------------------------
# 2. Row-count regression band (±10 %)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", _ACTIVE_FILES)
def test_row_count_within_baseline_band(filename: str) -> None:
    """Catch silent bulk deletes (realism gate would mask them as
    UNAVAILABLE) and out-of-band librarian appends.  Baseline must be
    updated in the same reviewed commit when the corpus legitimately
    grows or shrinks."""
    path = _DATA_DIR / filename
    actual = sum(
        1 for _, raw in _iter_lines(path)
        if raw.strip() and not any(raw.lstrip().startswith(m) for m in _CONFLICT_MARKERS)
    )
    baseline = _ROW_COUNT_BASELINE[filename]
    lo = int(baseline * (1.0 - _ROW_COUNT_TOLERANCE))
    hi = int(baseline * (1.0 + _ROW_COUNT_TOLERANCE)) + 1
    assert lo <= actual <= hi, (
        f"TAS/data/{filename} row count {actual} outside ±10 % band "
        f"of baseline {baseline} (allowed: [{lo}, {hi}]).  "
        "Update _ROW_COUNT_BASELINE in this file in the same commit "
        "that legitimately changes the corpus."
    )


# ---------------------------------------------------------------------------
# 3. Schema-envelope shape (spot-check first N rows of each file)
# ---------------------------------------------------------------------------


_SPOT_CHECK_ROWS = 50


def _wrapped_files() -> list[str]:
    return [f for f in _ACTIVE_FILES if _ENVELOPE_KEY[f] is not None]


@pytest.mark.parametrize(
    "filename",
    [
        _mark_known_drift(
            f,
            _KNOWN_DRIFT_ENVELOPE,
            "mosfets.ndjson rows are flat (no {'semiconductor': {...}} "
            "wrap) — pinned by test_semiconductor_wrap.py; awaiting "
            "component-librarian schema-migration pass.",
        )
        for f in _wrapped_files()
    ],
)
def test_envelope_key_present_in_spot_check(filename: str) -> None:
    """Spot-check the first _SPOT_CHECK_ROWS rows of each wrapped
    file: every row must be a JSON object with the expected outer
    discriminator key as its sole (or dominant) key.

    Sampling rather than scanning every row keeps the test fast on
    100 k+-row files while still catching wholesale envelope drift
    (e.g. an entire merge that re-introduces the flat shape, which
    is what landed in mosfets.ndjson and is pinned redundantly by
    test_semiconductor_wrap.py).
    """
    path = _DATA_DIR / filename
    expected = _ENVELOPE_KEY[filename]
    assert isinstance(expected, str)   # parametrise guarantees non-None
    rows_checked = 0
    offenders: list[str] = []
    for lineno, raw in _iter_lines(path):
        s = raw.strip()
        if not s or any(s.startswith(m) for m in _CONFLICT_MARKERS):
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue  # already flagged by the parse test
        if not isinstance(obj, dict):
            offenders.append(f"L{lineno}: top-level is not a JSON object")
        elif expected not in obj:
            offenders.append(
                f"L{lineno}: missing outer key {expected!r}, keys = "
                f"{sorted(obj.keys())[:5]}"
            )
        rows_checked += 1
        if rows_checked >= _SPOT_CHECK_ROWS:
            break
    assert not offenders, (
        f"TAS/data/{filename} envelope drift (spot-check of first "
        f"{_SPOT_CHECK_ROWS} rows).  First offenders:\n  "
        + "\n  ".join(offenders[:5])
    )


# ---------------------------------------------------------------------------
# 4. converters.ndjson structural shape — every populated entry must
#    carry the inputs/topology pair the realism gate consumes.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "converters.ndjson L48 is a pipeline-run telemetry record "
        "({'id', 'status', 'tas': {...}}) accidentally appended to "
        "the converter corpus instead of the run log.  Awaiting "
        "component-librarian repair (forbidden to edit TAS/data/*.ndjson "
        "by hand per AGENTS.md guardrail)."
    ),
)
def test_converters_have_inputs_and_topology() -> None:
    """Every non-`_empty` converter must expose both `inputs` and
    `topology` at the root (per `TAS/data/converters.ndjson` v2 shape
    documented in AGENTS.md).  The empty placeholder is allowed for
    the regression-suite scaffold."""
    path = _DATA_DIR / "converters.ndjson"
    bad: list[str] = []
    for lineno, raw in _iter_lines(path):
        s = raw.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        name = obj.get("name") if isinstance(obj, dict) else None
        if name == "_empty":
            continue
        if not isinstance(obj, dict):
            bad.append(f"L{lineno}: not an object")
            continue
        missing = [k for k in ("inputs", "topology") if k not in obj]
        if missing:
            bad.append(
                f"L{lineno} name={name!r}: missing root keys {missing}"
            )
        if len(bad) >= 5:
            break
    assert not bad, (
        "converters.ndjson entries missing required root keys:\n  "
        + "\n  ".join(bad)
    )
