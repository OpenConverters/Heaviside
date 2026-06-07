"""Ingest a bare BOM (bill of materials) from a CSV/TSV or Excel (.xlsx) file
into the list-of-dicts shape the cross-reference pipeline consumes.

This lets a user cross-reference a plain component list (e.g. exported from a
PLM / distributor cart / a reference design's BOM tab) without supplying a
whole reference-design PDF. The parser only canonicalises column names and
rows — it never fabricates missing values. ``crossref_pipeline._normalize_bom``
applies further canonicalisation downstream, so this layer is deliberately
forgiving about header spelling but strict about "is there actually data".
"""

from __future__ import annotations

import csv
import io
from typing import Any

__all__ = ["BomImportError", "parse_bom_bytes", "parse_bom_file"]


class BomImportError(ValueError):
    """Raised when an uploaded BOM file cannot be parsed into components."""


# Map common real-world spreadsheet header spellings → the canonical field
# names the CR pipeline understands (see crossref_pipeline._normalize_bom,
# which additionally maps mpn/part→original_mpn and type/category→component_type).
# Keys are compared lower-cased with surrounding whitespace/punctuation trimmed.
_HEADER_ALIASES: dict[str, str] = {
    "mpn": "original_mpn",
    "part": "original_mpn",
    "part number": "original_mpn",
    "part no": "original_mpn",
    "part#": "original_mpn",
    "manufacturer part number": "original_mpn",
    "manufacturer part no": "original_mpn",
    "mfr part number": "original_mpn",
    "mfr part #": "original_mpn",
    "mfg part number": "original_mpn",
    "original mpn": "original_mpn",
    "original_mpn": "original_mpn",
    "manufacturer": "manufacturer",
    "mfr": "manufacturer",
    "mfg": "manufacturer",
    "maker": "manufacturer",
    "vendor": "manufacturer",
    "brand": "manufacturer",
    "manufacturer name": "manufacturer",
    "category": "component_type",
    "type": "component_type",
    "component type": "component_type",
    "component_type": "component_type",
    "ref": "ref_des",
    "refs": "ref_des",
    "reference": "ref_des",
    "references": "ref_des",
    "ref des": "ref_des",
    "refdes": "ref_des",
    "ref_des": "ref_des",
    "designator": "ref_des",
    "reference designator": "ref_des",
    "value": "value",
    "val": "value",
    "voltage": "rated_voltage",
    "rated voltage": "rated_voltage",
    "voltage rating": "rated_voltage",
    "rated_voltage": "rated_voltage",
    "qty": "quantity",
    "quantity": "quantity",
    "description": "description",
    "desc": "description",
    "notes": "notes",
    "note": "notes",
    "comment": "notes",
    "comments": "notes",
}


def _canon_header(raw: str) -> str:
    """Canonicalise a single header cell to a pipeline field name (or a
    lower-cased fallback so unknown columns are still carried through)."""
    key = " ".join(str(raw).strip().lower().replace("_", " ").split())
    if not key:
        return ""
    if key in _HEADER_ALIASES:
        return _HEADER_ALIASES[key]
    # Also try the un-spaced form (e.g. "part#") and underscored fallback.
    compact = key.replace(" ", "")
    if compact in _HEADER_ALIASES:
        return _HEADER_ALIASES[compact]
    return key.replace(" ", "_")


def _rows_to_components(headers: list[str], rows: list[list[Any]]) -> list[dict[str, Any]]:
    canon = [_canon_header(h) for h in headers]
    if not any(canon):
        raise BomImportError("BOM file has no usable header row.")
    components: list[dict[str, Any]] = []
    for raw_row in rows:
        row: dict[str, Any] = {}
        for i, field in enumerate(canon):
            if not field or i >= len(raw_row):
                continue
            cell = raw_row[i]
            if cell is None:
                continue
            text = str(cell).strip()
            if text == "":
                continue
            # Don't clobber a real value with a later duplicate-mapped blank.
            row.setdefault(field, text)
        if row:
            components.append(row)
    if not components:
        raise BomImportError("BOM file parsed but contained no component rows (all rows empty).")
    if not any("original_mpn" in c for c in components):
        raise BomImportError(
            "BOM file has no recognisable part-number column. Include a column "
            "named one of: MPN, Part, Part Number, or Manufacturer Part Number."
        )
    return components


def _parse_csv(raw: bytes) -> list[dict[str, Any]]:
    text = raw.decode("utf-8-sig", errors="replace")
    if not text.strip():
        raise BomImportError("CSV file is empty.")
    # Sniff the delimiter from the first non-trivial chunk; fall back to comma.
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    table = [row for row in reader]
    # Drop leading fully-blank lines, then take the first row as the header.
    table = [r for r in table if any(str(c).strip() for c in r)]
    if not table:
        raise BomImportError("CSV file has no rows.")
    headers, *rows = table
    return _rows_to_components(headers, rows)


def _parse_xlsx(raw: bytes) -> list[dict[str, Any]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - dependency present in env
        raise BomImportError(
            "openpyxl is required to read .xlsx files; convert to CSV instead."
        ) from exc
    try:
        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as exc:
        raise BomImportError(f"could not open Excel workbook: {exc}") from exc
    try:
        ws = wb.active
        table = [
            list(r)
            for r in ws.iter_rows(values_only=True)
            if any(c is not None and str(c).strip() for c in r)
        ]
    finally:
        wb.close()
    if not table:
        raise BomImportError("Excel sheet has no rows.")
    headers, *rows = table
    return _rows_to_components([h if h is not None else "" for h in headers], rows)


def parse_bom_bytes(raw: bytes, filename: str) -> list[dict[str, Any]]:
    """Parse raw file bytes into a BOM component list, choosing CSV vs XLSX
    by the filename extension. Raises :class:`BomImportError` on any failure
    (unsupported type, empty file, no header, no rows, no part-number column)."""
    if not raw:
        raise BomImportError("uploaded file is empty.")
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        return _parse_xlsx(raw)
    if name.endswith(".xls"):
        raise BomImportError("legacy .xls is not supported — re-save as .xlsx or export to CSV.")
    if name.endswith((".csv", ".tsv", ".txt", "")):
        return _parse_csv(raw)
    # Unknown extension: try CSV (most BOM exports are text) before giving up.
    try:
        return _parse_csv(raw)
    except BomImportError as exc:
        raise BomImportError(
            f"unsupported BOM file type {filename!r}; use .csv or .xlsx ({exc})"
        ) from exc


# Convenience alias used by the API layer.
parse_bom_file = parse_bom_bytes
