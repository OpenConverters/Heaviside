"""Deterministic BOM-table extraction — the "reading" layer.

For datasheets / eval-board PDFs that carry a *structured ruled BOM table* (the
common ``ITEM | QTY | DESIGNATOR | DESCRIPTION | MANUFACTURER P/N`` format used
by ADI/LT, TI, etc.), pdfplumber extracts the rows deterministically — same
result every run, no LLM, no stochastic row-dropping. The compound DESCRIPTION
cell ("CAP., 1µF, X7R, 50V, 10%, 0402") is parsed into structured fields.

Returns rows keyed with the canonical BomComponent field names so the caller
can run them through the shared ``_component_from_fields`` / grouped-ref
expansion. Returns ``None`` when no BOM table is found, so the caller falls
back to the LLM census. This module is dependency-light (pdfplumber only) and
does NOT import bom_extract, to avoid a circular import.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# A header row is the BOM table's header if it names ≥2 of these.
_HEADER_TOKENS = ("DESIGNATOR", "DESCRIPTION", "MANUFACTURER")

# DESCRIPTION-prefix → PEAS category (most specific; falls back to ref-des prefix).
_DESC_CATEGORY: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\s*cap", re.I), "capacitor"),
    (re.compile(r"^\s*res", re.I), "resistor"),
    (re.compile(r"^\s*(ind|choke|transformer|coil|bead|ferrite)", re.I), "magnetic"),
    (re.compile(r"^\s*(diode|rectifier|schottky|tvs|zener|led|mosfet|fet|transistor|igbt|bjt)", re.I),
     "semiconductor"),
    (re.compile(r"^\s*(ic|regulator|converter|controller|driver|u\b)", re.I), "controller"),
]
_REF_CATEGORY = {"C": "capacitor", "R": "resistor", "L": "magnetic", "T": "magnetic",
                 "FB": "magnetic", "D": "semiconductor", "Q": "semiconductor", "U": "controller"}

_DIELECTRIC_RE = re.compile(r"\b(X7R|X5R|X7S|X7T|X8L|X8R|X6S|C0G|NP0|Y5V|Z5U)\b", re.I)
_VOLTAGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*V\b")
_PACKAGE_RE = re.compile(r"\b(0201|0402|0603|0805|1206|1210|1808|1812|2010|2220|2512|1008)\b")
_TOLERANCE_RE = re.compile(r"\b\d+(?:\.\d+)?\s*%")
# a value token: number + an R/L/C unit (µ and u both accepted)
_VALUE_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:pF|nF|[µu]F|mF|F|pH|nH|[µu]H|mH|H|m[ΩO]|k[ΩO]|M[ΩO]|[ΩO]|kΩ|R)\b"
)
_REF_TOKEN_RE = re.compile(r"\b([A-Z]{1,3})(\d{1,3})\b")


def _infer_category(description: str, designators: str) -> str:
    for pat, cat in _DESC_CATEGORY:
        if pat.search(description or ""):
            return cat
    m = re.match(r"([A-Z]{1,3})", (designators or "").strip())
    if m:
        return _REF_CATEGORY.get(m.group(1), "")
    return ""


def _parse_description(desc: str) -> dict[str, Any]:
    """Pull value / dielectric / voltage / package / tolerance out of a
    free-form BOM description like 'CAP., 1µF, X7R, 50V, 10%, 0402, AEC-Q200'."""
    out: dict[str, Any] = {}
    if not desc:
        return out
    flat = desc.replace("\n", " ")
    mv = _VALUE_RE.search(flat)
    if mv:
        out["value"] = mv.group(0).replace(" ", "")
    md = _DIELECTRIC_RE.search(flat)
    if md:
        out["technology"] = md.group(1).upper()
    mvolt = _VOLTAGE_RE.search(flat)
    if mvolt:
        out["rated_voltage"] = mvolt.group(1)
    mp = _PACKAGE_RE.search(flat)
    if mp:
        out["package"] = mp.group(1)
    mt = _TOLERANCE_RE.search(flat)
    if mt:
        out["notes"] = f"tol {mt.group(0).strip()}"
    return out


def _split_manufacturer_pn(cell: str) -> tuple[str | None, str | None]:
    """'MURATA, GCM31CL81H105KA55L' -> ('MURATA', 'GCM31CL81H105KA55L').
    A bare MPN (no comma) -> (None, mpn). Empty -> (None, None)."""
    cell = (cell or "").replace("\n", " ").strip()
    if not cell:
        return None, None
    parts = [p.strip() for p in cell.split(",") if p.strip()]
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:]).strip()
    return None, parts[0]


def _is_bom_header(row: list[Any]) -> bool:
    cells = " ".join((c or "").upper() for c in row)
    return sum(tok in cells for tok in _HEADER_TOKENS) >= 2


def _col_index(header: list[str], *needles: str) -> int | None:
    for i, c in enumerate(header):
        up = (c or "").upper()
        if any(n in up for n in needles):
            return i
    return None


def parse_bom_table(pdf_path: str | Path) -> list[dict[str, Any]] | None:
    """Deterministically extract BOM rows from a PDF's ruled BOM table(s).

    Returns canonical-field row dicts (ref_des/category/value/technology/
    rated_voltage/package/mpn/manufacturer/quantity/notes) ready for
    ``_component_from_fields`` + grouped-ref expansion, or ``None`` if no BOM
    table is present (caller falls back to the LLM census)."""
    try:
        import pdfplumber
    except Exception as exc:  # pragma: no cover - dependency missing
        logger.info("bom_table: pdfplumber unavailable (%s) — skipping deterministic parse", exc)
        return None

    rows_out: list[dict[str, Any]] = []
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                for tbl in (page.extract_tables() or []):
                    if not tbl or not _is_bom_header(tbl[0]):
                        continue
                    header = [(c or "").upper() for c in tbl[0]]
                    di = _col_index(header, "DESIGNATOR", "REF")
                    desc_i = _col_index(header, "DESCRIPTION", "VALUE")
                    mfr_i = _col_index(header, "MANUFACTURER", "P/N", "PART")
                    qty_i = _col_index(header, "QTY", "QUANTITY")
                    if di is None:
                        continue
                    for r in tbl[1:]:
                        if di >= len(r):
                            continue
                        desig = (r[di] or "").strip()
                        if not _REF_TOKEN_RE.search(desig):
                            continue  # not a real BOM data row (sub-header / blank)
                        desc = (r[desc_i] if desc_i is not None and desc_i < len(r) else "") or ""
                        mfr_cell = (r[mfr_i] if mfr_i is not None and mfr_i < len(r) else "") or ""
                        qty = (r[qty_i] if qty_i is not None and qty_i < len(r) else "") or ""
                        manufacturer, mpn = _split_manufacturer_pn(mfr_cell)
                        fields = {
                            "ref_des": desig.replace("\n", " ").strip(),
                            "category": _infer_category(desc, desig),
                            "mpn": mpn,
                            "manufacturer": manufacturer,
                            "quantity": str(qty).strip(),
                        }
                        fields.update(_parse_description(desc))
                        rows_out.append(fields)
    except Exception as exc:
        logger.info("bom_table[%s]: deterministic parse failed (%s) — falling back", pdf_path, exc)
        return None

    return rows_out or None
