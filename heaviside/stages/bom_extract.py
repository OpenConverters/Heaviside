"""bom_extract — extract a BOM from a source into PEAS-aligned components.

Engine (deterministic, this module): structured sources — CSV / table
rows / lists of dicts — parsed into a canonical :class:`BomComponent`.
The canonical shape mirrors PEAS: ``category`` is a PEAS oneOf key
(capacitor / magnetic / semiconductor / resistor / controller), specs use
PEAS/CAS field names (capacitance/resistance/inductance, ratedVoltage,
technology, case), values are parsed to SI via ``value_parse``. A
component therefore lines up directly with the PEAS catalogue types in
``heaviside.types`` and with the TAS rows the matcher queries — no field
translation, no ``type`` vs ``component_type`` / ``part`` vs ``mpn`` drift.

The unstructured PDF/image path (LLM) lands in a sibling function on top
of this engine; it returns the SAME :class:`BomComponent` list, so the
deterministic normalize/dedup/validate below is shared and tested once.
"""
from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from heaviside.pipeline.value_parse import parse_si_value

# PEAS oneOf component keys (heaviside.types.Peas) — the canonical category set.
PEAS_CATEGORIES = ("capacitor", "magnetic", "semiconductor", "resistor", "controller")

# loose source words -> PEAS category
_CATEGORY_ALIASES: dict[str, str] = {
    "capacitor": "capacitor", "cap": "capacitor", "mlcc": "capacitor",
    "inductor": "magnetic", "magnetic": "magnetic", "choke": "magnetic",
    "ferrite": "magnetic", "ferrite_bead": "magnetic", "ferrite bead": "magnetic",
    "transformer": "magnetic", "bead": "magnetic",
    "resistor": "resistor", "res": "resistor",
    "mosfet": "semiconductor", "diode": "semiconductor", "transistor": "semiconductor",
    "igbt": "semiconductor", "bjt": "semiconductor", "semiconductor": "semiconductor",
    "fet": "semiconductor", "rectifier": "semiconductor", "tvs": "semiconductor",
    "ic": "controller", "controller": "controller", "regulator": "controller",
}

# source column header (lowercased) -> canonical field
_COLUMN_ALIASES: dict[str, str] = {
    "ref_des": "ref_des", "refdes": "ref_des", "ref": "ref_des",
    "designator": "ref_des", "reference": "ref_des", "designators": "ref_des",
    "type": "category", "category": "category", "component_type": "category",
    "mpn": "mpn", "part": "mpn", "part_number": "mpn", "partnumber": "mpn",
    "manufacturer part number": "mpn", "manufacturer_part_number": "mpn",
    "manufacturer": "manufacturer", "mfr": "manufacturer", "mfg": "manufacturer",
    "value": "value", "val": "value",
    "voltage": "rated_voltage", "rated_voltage": "rated_voltage",
    "rated voltage": "rated_voltage", "vdc": "rated_voltage",
    "package": "package", "footprint": "package", "case": "package", "size": "package",
    "technology": "technology", "dielectric": "technology", "tempco": "technology",
    "temperature coefficient": "technology",
    "qty": "quantity", "quantity": "quantity", "qnty": "quantity",
    "notes": "notes", "note": "notes", "description": "notes",
}

_VALUE_PARSE_UNIT = {"capacitor": "F", "magnetic": "H", "resistor": "Ω"}


@dataclass
class BomComponent:
    """One BOM line, PEAS-aligned. Sparse by nature (a design reference,
    not a full datasheet); fields use PEAS/CAS names so it maps 1:1 onto
    the PEAS catalogue types and TAS rows."""

    ref_des: str
    category: str  # a PEAS_CATEGORIES value, or "" when unclassifiable
    mpn: str | None = None
    manufacturer: str | None = None
    value: str | None = None  # raw/human, e.g. "4.7µF"
    value_si: float | None = None  # parsed SI base unit
    rated_voltage: float | None = None
    package: str | None = None
    technology: str | None = None
    quantity: int = 1
    notes: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_peas_category(self) -> str | None:
        """PEAS oneOf key for this component, or None if unclassified."""
        return self.category if self.category in PEAS_CATEGORIES else None


def normalize_category(raw: str | None) -> str:
    if not raw:
        return ""
    key = str(raw).strip().lower()
    if key in PEAS_CATEGORIES:
        return key
    return _CATEGORY_ALIASES.get(key, "")


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return parse_si_value(v)


def _component_from_fields(fields: Mapping[str, Any]) -> BomComponent | None:
    ref = str(fields.get("ref_des") or "").strip()
    if not ref:
        return None
    category = normalize_category(fields.get("category"))
    value = fields.get("value")
    value = str(value).strip() if value not in (None, "") else None
    value_si = None
    if value is not None and category in _VALUE_PARSE_UNIT:
        value_si = parse_si_value(value)
    qty_raw = fields.get("quantity")
    try:
        quantity = int(float(qty_raw)) if qty_raw not in (None, "") else 1
    except (TypeError, ValueError):
        quantity = 1
    mpn = fields.get("mpn")
    return BomComponent(
        ref_des=ref,
        category=category,
        mpn=str(mpn).strip() if mpn not in (None, "") else None,
        manufacturer=(str(fields["manufacturer"]).strip()
                      if fields.get("manufacturer") not in (None, "") else None),
        value=value,
        value_si=value_si,
        rated_voltage=_to_float(fields.get("rated_voltage")),
        package=(str(fields["package"]).strip()
                 if fields.get("package") not in (None, "") else None),
        technology=(str(fields["technology"]).strip()
                    if fields.get("technology") not in (None, "") else None),
        quantity=quantity,
        notes=(str(fields["notes"]).strip()
               if fields.get("notes") not in (None, "") else None),
        raw=dict(fields),
    )


def _map_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Map a source row's headers onto canonical field names."""
    mapped: dict[str, Any] = {}
    for k, v in row.items():
        if k is None:
            continue
        key = str(k).strip().lower()
        canon = (_COLUMN_ALIASES.get(key)
                 or _COLUMN_ALIASES.get(key.replace(" ", "_"))
                 or _COLUMN_ALIASES.get(key.replace("_", " ")))
        if canon and (canon not in mapped or mapped[canon] in (None, "")):
            mapped[canon] = v
    return mapped


def _expand_grouped_refs(comp: BomComponent) -> list[BomComponent]:
    """A row like 'C1, C2, C3' or 'R1-R4' is several components."""
    import re

    ref = comp.ref_des
    parts = [p.strip() for p in ref.replace(";", ",").split(",") if p.strip()]
    expanded: list[str] = []
    # range forms: "R1-R4" or "R1-4" (prefix + lo - [prefix] + hi)
    range_re = re.compile(r"^([A-Za-z]+)(\d+)\s*-\s*([A-Za-z]*)(\d+)$")
    for p in parts:
        m = range_re.match(p.replace(" ", ""))
        if m:
            prefix, lo, prefix2, hi = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
            if (not prefix2 or prefix2 == prefix) and 0 <= hi - lo <= 200:
                expanded.extend(f"{prefix}{i}" for i in range(lo, hi + 1))
                continue
        expanded.append(p)
    if len(expanded) <= 1:
        return [comp]
    out = []
    for r in expanded:
        from dataclasses import replace
        out.append(replace(comp, ref_des=r, quantity=1))
    return out


def extract_bom_from_rows(rows: Iterable[Mapping[str, Any]]) -> list[BomComponent]:
    """Deterministic engine: list-of-dict / table rows -> BomComponents.
    Headers are matched case-insensitively against known aliases; grouped
    ref-des cells are expanded into individual components."""
    out: list[BomComponent] = []
    for row in rows:
        comp = _component_from_fields(_map_row(row))
        if comp is not None:
            out.extend(_expand_grouped_refs(comp))
    return out


def extract_bom_from_csv(source: str | Path) -> list[BomComponent]:
    """Deterministic engine: a CSV file (or CSV text) -> BomComponents."""
    text = (
        Path(source).read_text(encoding="utf-8")
        if isinstance(source, Path) or (isinstance(source, str) and "\n" not in source
                                        and Path(source).exists())
        else str(source)
    )
    reader = csv.DictReader(io.StringIO(text))
    return extract_bom_from_rows(reader)
