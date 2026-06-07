"""Datasheet reader — strict-mode PDF parameter extraction.

Replaces ``Proteus/scripts/librarian_datasheet_reader.py``.  See
individual submodules for the contract details:

* :mod:`heaviside.librarian.datasheet.base` — exceptions
* :mod:`heaviside.librarian.datasheet.cache` — content-addressed PDF cache
* :mod:`heaviside.librarian.datasheet.patterns` — per-category regex patterns
  and schema-required field sets
* :mod:`heaviside.librarian.datasheet.extract` — table → params extraction
* :mod:`heaviside.librarian.datasheet.reader` — :class:`DatasheetReader`
  orchestrator (URL → params)
"""

from __future__ import annotations

from heaviside.librarian.datasheet.base import (
    DatasheetDownloadError,
    DatasheetError,
    DatasheetParseError,
    IncompleteDatasheetError,
    MissingDependencyError,
)
from heaviside.librarian.datasheet.cache import DEFAULT_CACHE_DIR, PdfCache
from heaviside.librarian.datasheet.extract import (
    ELECTRICAL_SECTION_HEADERS,
    SECTION_TERMINATORS,
    extract_params,
    extract_required_params,
    extract_tables,
    filter_electrical_tables,
    match_param_name,
    pick_value_from_row,
)
from heaviside.librarian.datasheet.patterns import (
    CATEGORY_PATTERNS,
    PARAM_UNITS,
    REQUIRED_BY_CATEGORY,
)
from heaviside.librarian.datasheet.reader import DatasheetReader

__all__ = [
    "CATEGORY_PATTERNS",
    "DEFAULT_CACHE_DIR",
    "ELECTRICAL_SECTION_HEADERS",
    "PARAM_UNITS",
    "REQUIRED_BY_CATEGORY",
    "SECTION_TERMINATORS",
    "DatasheetDownloadError",
    "DatasheetError",
    "DatasheetParseError",
    "DatasheetReader",
    "IncompleteDatasheetError",
    "MissingDependencyError",
    "PdfCache",
    "extract_params",
    "extract_required_params",
    "extract_tables",
    "filter_electrical_tables",
    "match_param_name",
    "pick_value_from_row",
]
