"""Strict-mode datasheet parameter extraction.

Pipeline
--------

1. :func:`extract_tables` opens the PDF via ``pdfplumber`` and
   returns every detected table (raises
   :class:`DatasheetParseError` on zero tables).
2. :func:`filter_electrical_tables` narrows to tables in (or
   immediately following) an "Electrical Characteristics" /
   "Static Characteristics" / "Dynamic Characteristics" / "Switching
   Characteristics" section.
3. :func:`extract_params` walks the filtered tables, matches the
   first cell of each row against the per-category regex patterns
   in :mod:`heaviside.librarian.datasheet.patterns`, and parses
   the value cell with :func:`parse_si_value`.

Strict-mode contract
--------------------

* No silent ``None`` returns.  Failure paths raise:
  :class:`DatasheetParseError` for "no tables", :class:`IncompleteDatasheetError`
  for "tables found but a required field is missing",
  :class:`MissingDependencyError` for "pdfplumber not installed".
* No value sanity-clamping (Proteus dropped Rds(on) values ≥1000 Ω
  silently on the assumption they were misparsed — this hid real
  parsing bugs).  When :func:`parse_si_value` succeeds, the value
  is emitted as-is.
* No table-text heuristics that fall back to "scan every table" when
  the section detector finds nothing.  If the Electrical
  Characteristics section can't be located, the extractor raises
  rather than scanning the whole document (which produced wildly
  wrong matches in Proteus when a "Drain Current" row appeared in
  the Applications section).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from heaviside.librarian.datasheet.base import (
    DatasheetParseError,
    IncompleteDatasheetError,
    MissingDependencyError,
)
from heaviside.librarian.datasheet.patterns import (
    CATEGORY_PATTERNS,
    REQUIRED_BY_CATEGORY,
)
from heaviside.librarian.fetcher.convert import parse_si_value

__all__ = [
    "ELECTRICAL_SECTION_HEADERS",
    "SECTION_TERMINATORS",
    "extract_params",
    "extract_tables",
    "filter_electrical_tables",
    "match_param_name",
    "pick_value_from_row",
]


# Section-detection headers — case-insensitive substring match against
# the joined text of the first 3 rows of each table.  A table that
# matches any of these is considered an Electrical Characteristics
# table.
ELECTRICAL_SECTION_HEADERS: tuple[str, ...] = (
    "electrical characteristics",
    "electrical specifications",
    "electrical parameter",
    "static characteristics",
    "dynamic characteristics",
    "switching characteristics",
    "absolute maximum ratings",
    "characteristics",  # generic catch-all; tested last
)


# Once an electrical table has been seen, subsequent tables continue
# to be considered electrical until one of these section terminators
# is found in their first rows.
SECTION_TERMINATORS: tuple[str, ...] = (
    "thermal characteristics",
    "mechanical characteristics",
    "package",
    "ordering information",
    "revision history",
    "marking",
    "outline",
)


# Type alias for pdfplumber's table shape: list of rows, each row
# is a list of cells, each cell is str or None.
Table = list[list[str | None]]


# Cell text that is purely a symbol column (e.g. "VDS", "RDS(ON)")
# rather than a description.  Used to skip the symbol column when
# searching for the value cell.
_SYMBOL_CELL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\(\)/\\,]*$")


# Numeric token recognisable as the start of an extractable value.
# We accept signed decimal, scientific notation, and the leading
# digit of a range ("20 to 30" → 20).
_NUMERIC_PREFIX_RE = re.compile(
    r"""
    ^[\s±]*
    [+-]?
    \d+
    (?:\.\d+)?
    (?:[eE][+-]?\d+)?
    """,
    re.VERBOSE,
)


# ---------------------------------------------------------------------------
# PDF → tables
# ---------------------------------------------------------------------------


def extract_tables(pdf_path: Path | str) -> list[Table]:
    """Open ``pdf_path`` and return every table ``pdfplumber`` finds.

    Raises
    ------
    MissingDependencyError
        ``pdfplumber`` is not installed.
    DatasheetParseError
        The PDF is unreadable, encrypted, or contains zero tables.
    """
    try:
        import pdfplumber
    except ImportError as exc:
        raise MissingDependencyError("pdfplumber") from exc

    path = Path(pdf_path)
    if not path.is_file():
        raise DatasheetParseError(f"PDF not found: {path}")

    tables: list[Table] = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables() or []:
                    if table and len(table) >= 2:
                        tables.append(table)
    except Exception as exc:  # pdfminer + pdfplumber raise a zoo of types
        raise DatasheetParseError(
            f"pdfplumber failed to parse {path!r}: {exc}",
        ) from exc

    if not tables:
        raise DatasheetParseError(
            f"no tables extracted from {path!r}; PDF may be image-only or encrypted",
        )
    return tables


# ---------------------------------------------------------------------------
# Table filtering
# ---------------------------------------------------------------------------


def _table_header_text(table: Table) -> str:
    head = table[:3] if len(table) >= 3 else table
    parts: list[str] = []
    for row in head:
        for cell in row:
            if cell:
                parts.append(str(cell))
    return " ".join(parts).lower()


def filter_electrical_tables(tables: Sequence[Table]) -> list[Table]:
    """Return only those tables in an Electrical / Static / Dynamic /
    Switching Characteristics section.

    The Proteus reader fell back to "scan every table" when this
    filter returned nothing.  Strict-mode does not: callers that
    receive an empty result should raise :class:`DatasheetParseError`
    so the librarian agent can flag the PDF for manual review or
    OCR.
    """
    result: list[Table] = []
    in_section = False
    for table in tables:
        if not table:
            continue
        header = _table_header_text(table)
        # Check terminators BEFORE header markers — "Thermal
        # Characteristics" would otherwise match the generic
        # "characteristics" catch-all in ELECTRICAL_SECTION_HEADERS.
        is_terminator = any(term in header for term in SECTION_TERMINATORS)
        if is_terminator:
            in_section = False
            continue
        if any(marker in header for marker in ELECTRICAL_SECTION_HEADERS):
            in_section = True
            result.append(table)
        elif in_section:
            result.append(table)
    return result


# ---------------------------------------------------------------------------
# Row → param name + value
# ---------------------------------------------------------------------------


def match_param_name(cell_text: str, category: str) -> str | None:
    """Return the standardised key for ``cell_text`` in ``category``.

    Tests the cell against every pattern in
    :data:`CATEGORY_PATTERNS[category]` (case-insensitive) and
    returns the *first* key whose pattern list matches.  Returns
    ``None`` when no pattern matches — this is the only non-raising
    return in the extractor and is part of its contract: the caller
    walks every row and most rows legitimately don't carry a
    parameter we recognise.
    """
    patterns = CATEGORY_PATTERNS.get(category)
    if patterns is None:
        raise ValueError(
            f"unknown category {category!r}; expected one of {sorted(CATEGORY_PATTERNS)}"
        )
    if not cell_text:
        return None
    # Two surface forms to test: as-given, and with whitespace
    # stripped (so "V\nDS" matches "VDS"-style patterns).  Lowercase
    # is handled by ``re.IGNORECASE`` per-pattern.
    candidates = (cell_text, re.sub(r"\s+", "", cell_text))
    for key, pattern_list in patterns.items():
        for pattern in pattern_list:
            for candidate in candidates:
                if re.search(pattern, candidate, re.IGNORECASE):
                    return key
    return None


def _is_symbol_cell(text: str) -> bool:
    stripped = text.replace("\n", "").strip()
    if not stripped or len(stripped) > 20:
        return False
    if " " in stripped:
        return False
    return bool(_SYMBOL_CELL_RE.match(stripped))


def pick_value_from_row(row: Sequence[str | None], param_key: str) -> float:
    """Extract the first parseable numeric value from a table row.

    Skips the parameter-name column (always row[0]) and any
    immediately-following symbol-only column (e.g. "VDS",
    "RDS(ON)").  Each remaining cell is split on newlines; each line
    is fed to :func:`parse_si_value`.  The first parseable line wins.

    The Proteus reader applied per-parameter sanity clamps (drop
    Rds(on) ≥ 1000 Ω etc.); we do not — strict-mode trusts the
    parsed value and surfaces obviously-wrong values to the caller
    (which is typically the auditor, whose job is exactly to flag
    such values).

    Raises
    ------
    ValueError
        No cell in ``row`` after the name column contained a
        parseable numeric value.
    """
    cells = list(row)
    if len(cells) < 2:
        raise ValueError("row has fewer than 2 cells; no value column")
    skip_next_symbol = True
    for cell in cells[1:]:
        if cell is None:
            continue
        text = str(cell).strip()
        if not text:
            continue
        if skip_next_symbol and _is_symbol_cell(text):
            skip_next_symbol = False
            continue
        skip_next_symbol = False
        for line in text.split("\n"):
            line = _strip_annotations(line).strip()
            if not line:
                continue
            # Need a numeric leading token; otherwise this cell is
            # almost certainly a unit-only header ("V", "mΩ") rather
            # than a value.
            if not _NUMERIC_PREFIX_RE.match(line):
                continue
            try:
                return parse_si_value(line)
            except ValueError:
                # Try the next line / cell — some datasheets carry
                # ranges like "20 to 30" where the parser stops at
                # "20"; that's fine, parse_si_value handles it.
                # Genuinely unparseable strings ("TBD") fall through.
                continue
    raise ValueError(
        f"no parseable numeric value in row for {param_key!r}: "
        f"cells={[str(c).strip() for c in cells]}"
    )


def _strip_annotations(text: str) -> str:
    """Remove footnote markers and bracketed notes.

    Datasheets pepper value cells with ``(1)``, ``(2)``, ``[Note 3]``
    and ``See Note 4``.  We strip them so the numeric tokeniser
    sees a clean value.
    """
    text = re.sub(r"\(\d+\)", "", text)
    text = re.sub(r"\[[^\]]*\]", "", text)
    text = re.sub(r"\bNote[s]?\s*\d*\b.*", "", text, flags=re.IGNORECASE)
    return text


# ---------------------------------------------------------------------------
# Top-level: tables → params dict
# ---------------------------------------------------------------------------


def extract_params(
    tables: Sequence[Table],
    *,
    category: str,
    require_section: bool = True,
) -> dict[str, float]:
    """Walk ``tables`` and return ``{param_key: value}`` for
    everything recognised.

    Parameters
    ----------
    tables : sequence of Table
        Raw tables from :func:`extract_tables`.
    category : str
        One of ``"mosfets"``, ``"diodes"``, ``"igbts"``,
        ``"capacitors"``, ``"resistors"``.
    require_section : bool, default True
        When ``True`` (default), only rows in an Electrical
        Characteristics section are scanned.  Set to ``False`` only
        for datasheets that genuinely lack section headers (rare,
        and risks false matches); the default refuses to fall back
        the way Proteus did.

    Returns
    -------
    dict[str, float]
        Parameters found, keyed by the standardised names from
        :mod:`heaviside.librarian.datasheet.patterns`.  May be
        sparse — does *not* raise when required fields are absent;
        that's :func:`extract_required_params`'s job.
    """
    if category not in CATEGORY_PATTERNS:
        raise ValueError(
            f"unknown category {category!r}; expected one of {sorted(CATEGORY_PATTERNS)}"
        )
    selected = filter_electrical_tables(tables) if require_section else list(tables)
    if require_section and not selected:
        raise DatasheetParseError(
            "no Electrical / Static / Dynamic / Switching Characteristics "
            "section detected; pass require_section=False to scan every "
            "table (not recommended)"
        )

    found: dict[str, float] = {}
    for table in selected:
        for row in table:
            if not row:
                continue
            name_cell = str(row[0] or "").strip()
            if not name_cell or len(name_cell) > 500:
                continue
            # Skip rows whose first cell carries multiple section
            # headers (occurs when pdfplumber merges adjacent
            # section banners).
            if _looks_like_merged_section_banner(name_cell):
                continue
            key = match_param_name(name_cell, category)
            if key is None:
                continue
            # First-occurrence-wins: a static-characteristics row
            # outranks a dynamic-section row with the same name.
            if key in found:
                continue
            try:
                value = pick_value_from_row(row, key)
            except ValueError:
                continue
            found[key] = value
    return found


def extract_required_params(
    tables: Sequence[Table],
    *,
    category: str,
    mpn: str,
    require_section: bool = True,
) -> dict[str, float]:
    """Same as :func:`extract_params` but raises
    :class:`IncompleteDatasheetError` if any field in
    :data:`REQUIRED_BY_CATEGORY[category]` is missing.

    Use this when the caller's only acceptable outcome is a fully
    enriched component; otherwise call :func:`extract_params` and
    inspect the returned dict.
    """
    params = extract_params(
        tables,
        category=category,
        require_section=require_section,
    )
    required = REQUIRED_BY_CATEGORY[category]
    missing = required - set(params)
    if missing:
        # Report the first missing field with the canonical dotted
        # path so the error matches the converter's error shape.
        first = sorted(missing)[0]
        raise IncompleteDatasheetError(
            mpn,
            f"electrical.{first}",
            detail=(
                f"datasheet tables scanned but field {first!r} was not "
                f"recognised; full required set: {sorted(required)}; "
                f"found: {sorted(params)}"
            ),
        )
    return params


def _looks_like_merged_section_banner(cell: str) -> bool:
    """Heuristic: cell contains ≥2 section banner phrases."""
    markers = (
        "Maximum Ratings",
        "Static Characteristics",
        "Dynamic Characteristics",
        "Thermal Characteristics",
        "Switching Characteristics",
        "Mechanical",
    )
    return sum(1 for m in markers if m in cell) >= 2


# Used by :class:`heaviside.librarian.datasheet.reader.DatasheetReader`
# to annotate which fields the extractor consumed as "values" — not
# part of the public extraction API.
def _row_has_numeric(row: Sequence[str | None]) -> bool:
    return any(cell and _NUMERIC_PREFIX_RE.match(str(cell).strip()) for cell in row[1:])


_ = _row_has_numeric  # retained for future :mod:`reader` callers


# Backwards-compat alias for the type hint
__annotations__["Table"] = Table
