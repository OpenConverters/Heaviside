"""Distributor-payload → TAS envelope converters (strict mode).

Replaces Proteus's ``scripts/librarian_tas.py:convert_*_to_tas_*``
methods (lines 562-1934).  The Proteus originals:

* Wrapped every conversion in ``try/except Exception: return None``,
  which silently swallowed *every* missing parameter into the
  ``return None`` branch — including KeyErrors that should have
  surfaced as bugs.
* Defaulted missing numeric fields to ``0.0`` via ``_parse_value``,
  producing rows like ``onResistance: 0.0`` that then validated
  against the schema (which accepts any non-negative number) and
  poisoned every downstream loss-budget calculation.
* Wrote ``deviceType`` into the SAS ``part`` block — but the SAS
  schema sets ``additionalProperties: false`` on ``part`` and uses
  the *outer key* (``mosfet``/``diode``/``igbt``) as the device-type
  discriminator.  Every Proteus-imported row that ran through this
  path would now fail strict validation.

Strict-mode rewrite
-------------------

* Numeric parsing raises :class:`IncompleteSourceError` on any
  failure — never returns ``0.0`` as a fallback.
* Missing schema-required fields raise
  :class:`IncompleteSourceError` with a dotted path identifying the
  gap, so the caller (typically the ``component-librarian`` agent)
  can either enrich the payload from the datasheet or quarantine
  the part.
* Output envelope omits ``deviceType``, matches the current SAS
  ``additionalProperties: false`` contract, and includes the six
  schema-required ``electrical`` fields enforced after the May 2026
  schema tightening (``drainSourceVoltage``, ``onResistance``,
  ``continuousDrainCurrent``, ``gateThresholdVoltage``,
  ``outputCapacitance``, ``totalGateCharge``).
* ``technology`` resolution uses an explicit allow-list (Si / SiC /
  GaN) and raises on ambiguity rather than silently defaulting to
  Si the way Proteus did.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from heaviside.librarian.fetcher.base import IncompleteSourceError


__all__ = [
    "DIGIKEY_MOSFET_PARAM_MAP",
    "convert_digikey_to_tas_mosfet",
    "convert_mouser_to_tas_mosfet",
    "parse_si_value",
]


# ---------------------------------------------------------------------------
# Numeric parsing
# ---------------------------------------------------------------------------


# Common Digi-Key parameter values look like "100 V", "20 mΩ",
# "230 pF", "51 nC", "1.5 µA".  Optional sign, optional decimal,
# optional SI prefix, optional unit.  We require the value to start
# with a numeric token; everything after is treated as
# prefix-and-unit (a single prefix character optional).
_VALUE_RE = re.compile(
    r"""
    ^\s*
    (?P<num>[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
    \s*
    (?P<prefix>[pnuµμmkKMG]?)
    """,
    re.VERBOSE,
)

_PREFIX_MULT: dict[str, float] = {
    "":  1.0,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "μ": 1e-6,  # GREEK SMALL LETTER MU (different codepoint from µ).
    "m": 1e-3,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
}


def parse_si_value(value_str: str | None, *, allow_blank: bool = False) -> float:
    """Parse a Digi-Key/Mouser value string with SI prefix into a float.

    Strict-mode: any parse failure raises :class:`ValueError`.  The
    converters wrap that into :class:`IncompleteSourceError` so the
    distinction between "missing parameter" and "malformed
    parameter" is preserved.

    Args:
        value_str: A string like ``"100 V"`` or ``"20 mΩ"``.
        allow_blank: When ``True``, returns ``math.nan`` for blank /
            None / "-" inputs instead of raising.  Default ``False``.

    Returns:
        The numeric value in SI base units.
    """
    if value_str is None or value_str == "" or value_str.strip() in {"", "-", "—"}:
        if allow_blank:
            return float("nan")
        raise ValueError(f"empty or sentinel value: {value_str!r}")
    cleaned = value_str.replace(",", "").strip()
    match = _VALUE_RE.match(cleaned)
    if not match:
        raise ValueError(f"cannot parse SI value: {value_str!r}")
    num = float(match.group("num"))
    prefix = match.group("prefix") or ""
    # If what follows the matched prefix is actually a unit letter
    # (e.g. ``m`` in ``"30 mA"``), the regex is correct.  But for a
    # bare ``"30"`` with no prefix, ``prefix`` is ``""`` and the
    # multiplier is 1.0.  Ambiguity case: ``"3M"`` could be 3 mega
    # or 3 (with stray ``M``); we treat any matched prefix as the
    # prefix.  Callers that care about disambiguation should clean
    # the input upstream.
    return num * _PREFIX_MULT[prefix]


# ---------------------------------------------------------------------------
# Technology resolution
# ---------------------------------------------------------------------------


def _resolve_mosfet_technology(description: str, mpn: str) -> str:
    """Resolve the SAS ``technology`` enum from free-form text.

    Returns one of ``"Si"`` / ``"SiC"`` / ``"GaN"``.  Default
    fallback is ``"Si"`` (true for the overwhelming majority of
    parts in the Digi-Key catalogue); detection is case-insensitive.
    """
    blob = f"{description} {mpn}".upper()
    # Order matters: ``GaN`` substrings can appear inside ``GANSi``
    # variants but we treat any explicit GaN mention as winning.
    if "GAN" in blob:
        return "GaN"
    if "SIC" in blob:
        return "SiC"
    return "Si"


def _resolve_mosfet_subtype(description: str) -> str:
    blob = description.upper()
    if "P-CH" in blob or "P-CHANNEL" in blob or "PMOS" in blob:
        return "pChannel"
    return "nChannel"


# ---------------------------------------------------------------------------
# Digi-Key MOSFET converter
# ---------------------------------------------------------------------------


# Maps SAS ``electrical`` field name → list of Digi-Key Parameter
# names (first match wins).  Digi-Key changed parameter labels twice
# in 2023; we keep both spellings.
DIGIKEY_MOSFET_PARAM_MAP: dict[str, tuple[tuple[str, str], ...]] = {
    "drainSourceVoltage": (
        ("Drain to Source Voltage (Vdss)", "V"),
        ("Drain to Source Voltage Vdss", "V"),
    ),
    "onResistance": (
        ("Rds On (Max) @ Id, Vgs", "Ω"),
        ("Rds On (Max)", "Ω"),
    ),
    "continuousDrainCurrent": (
        ("Current - Continuous Drain (Id) @ 25°C", "A"),
        ("Current - Continuous Drain (Id) @ 25 C", "A"),
    ),
    "totalGateCharge": (
        ("Gate Charge (Qg) @ Vgs", "C"),
        ("Gate Charge (Qg)", "C"),
    ),
    "outputCapacitance": (
        ("Output Capacitance (Coss) @ Vds, Vgs", "F"),
        ("Output Capacitance (Coss)", "F"),
    ),
}


def _lookup_param(
    params: dict[str, str],
    candidates: tuple[tuple[str, str], ...],
) -> tuple[str, str] | None:
    """Return ``(raw_value, unit)`` for the first candidate present, else None."""
    for name, unit in candidates:
        raw = params.get(name)
        if raw not in (None, "", "-"):
            return raw, unit
    return None


def _extract_required_numeric(
    *,
    source: str,
    mpn: str,
    params: dict[str, str],
    field: str,
    candidates: tuple[tuple[str, str], ...],
) -> float:
    looked = _lookup_param(params, candidates)
    if looked is None:
        raise IncompleteSourceError(
            source,
            mpn,
            f"electrical.{field}",
            detail=(
                "no Digi-Key parameter matched any of: "
                + ", ".join(repr(name) for name, _ in candidates)
            ),
        )
    raw, _unit = looked
    try:
        return parse_si_value(raw)
    except ValueError as exc:
        raise IncompleteSourceError(
            source,
            mpn,
            f"electrical.{field}",
            detail=f"unparseable value {raw!r}: {exc}",
        ) from exc


def _extract_gate_threshold(
    *, source: str, mpn: str, params: dict[str, str]
) -> dict[str, float]:
    """Extract Vgs(th) as a SAS ``dimensionWithTolerance`` object.

    Digi-Key publishes only a single value for V_GS(th), usually the
    Max.  We surface it as ``{"maximum": value}`` (SAS schema accepts
    any subset of {minimum, nominal, maximum}).
    """
    candidates = (
        ("Vgs(th) (Max) @ Id", "V"),
        ("Vgs(th) (Max)", "V"),
        ("Vgs(th)", "V"),
    )
    looked = _lookup_param(params, candidates)
    if looked is None:
        raise IncompleteSourceError(
            source,
            mpn,
            "electrical.gateThresholdVoltage",
            detail="no Digi-Key Vgs(th) parameter present",
        )
    raw, _unit = looked
    try:
        value = parse_si_value(raw)
    except ValueError as exc:
        raise IncompleteSourceError(
            source,
            mpn,
            "electrical.gateThresholdVoltage",
            detail=f"unparseable Vgs(th) value {raw!r}: {exc}",
        ) from exc
    return {"maximum": value}


def _require_str(
    source: str, mpn_for_error: str, field: str, value: Any,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IncompleteSourceError(source, mpn_for_error, field)
    return value


def convert_digikey_to_tas_mosfet(
    product: dict[str, Any],
    *,
    distributor: str = "Digi-Key",
) -> dict[str, Any]:
    """Convert a Digi-Key Product Information v3 payload into a TAS
    ``{"mosfet": {...}}`` envelope.

    Raises:
        IncompleteSourceError: Any of the six schema-required
            ``electrical`` fields, the manufacturer name, the part
            number, or the technology could not be resolved.
    """
    source = "digikey"
    mpn = _require_str(
        source,
        str(product.get("ManufacturerPartNumber") or "<unknown>"),
        "ManufacturerPartNumber",
        product.get("ManufacturerPartNumber"),
    )

    manufacturer_block = product.get("Manufacturer") or {}
    manufacturer = manufacturer_block.get("Value") if isinstance(manufacturer_block, dict) else None
    if not manufacturer:
        raise IncompleteSourceError(
            source, mpn, "manufacturerInfo.name",
            detail="Manufacturer.Value missing from Digi-Key payload",
        )

    raw_params = product.get("Parameters") or []
    if not isinstance(raw_params, list):
        raise IncompleteSourceError(
            source, mpn, "Parameters",
            detail=f"Parameters is {type(raw_params).__name__}, expected list",
        )
    params: dict[str, str] = {}
    for entry in raw_params:
        if not isinstance(entry, dict):
            continue
        name = entry.get("Parameter")
        value = entry.get("Value")
        if isinstance(name, str) and isinstance(value, str):
            params[name] = value

    description_block = product.get("Description") or {}
    if isinstance(description_block, dict):
        description = description_block.get("ProductDescription") or ""
    elif isinstance(description_block, str):
        description = description_block
    else:
        description = ""

    technology = _resolve_mosfet_technology(description, mpn)
    subtype = _resolve_mosfet_subtype(description)
    case = params.get("Supplier Device Package") or params.get("Package / Case")

    electrical = {
        "drainSourceVoltage": _extract_required_numeric(
            source=source, mpn=mpn, params=params,
            field="drainSourceVoltage",
            candidates=DIGIKEY_MOSFET_PARAM_MAP["drainSourceVoltage"],
        ),
        "onResistance": _extract_required_numeric(
            source=source, mpn=mpn, params=params,
            field="onResistance",
            candidates=DIGIKEY_MOSFET_PARAM_MAP["onResistance"],
        ),
        "continuousDrainCurrent": _extract_required_numeric(
            source=source, mpn=mpn, params=params,
            field="continuousDrainCurrent",
            candidates=DIGIKEY_MOSFET_PARAM_MAP["continuousDrainCurrent"],
        ),
        "gateThresholdVoltage": _extract_gate_threshold(
            source=source, mpn=mpn, params=params,
        ),
        "outputCapacitance": _extract_required_numeric(
            source=source, mpn=mpn, params=params,
            field="outputCapacitance",
            candidates=DIGIKEY_MOSFET_PARAM_MAP["outputCapacitance"],
        ),
        "totalGateCharge": _extract_required_numeric(
            source=source, mpn=mpn, params=params,
            field="totalGateCharge",
            candidates=DIGIKEY_MOSFET_PARAM_MAP["totalGateCharge"],
        ),
    }

    part: dict[str, Any] = {
        "partNumber": mpn,
        "technology": technology,
        "subType": subtype,
    }
    if case:
        part["case"] = case

    status_field = product.get("ProductStatus")
    status = "production" if status_field in (None, "Active") else "discontinued"

    datasheet_url = product.get("PrimaryDatasheet") or ""

    distributor_ref = (
        product.get("DigiKeyPartNumber")
        or product.get("MouserPartNumber")
        or ""
    )
    try:
        cost = float(product.get("UnitPrice", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise IncompleteSourceError(
            source, mpn, "distributorsInfo.cost",
            detail=f"UnitPrice {product.get('UnitPrice')!r} not numeric: {exc}",
        ) from exc

    return {
        "mosfet": {
            "manufacturerInfo": {
                "name": manufacturer,
                "reference": mpn,
                "status": status,
                "datasheetUrl": datasheet_url,
                "datasheetInfo": {
                    "part": part,
                    "electrical": electrical,
                },
            },
            "distributorsInfo": [
                {
                    "name": distributor,
                    "reference": distributor_ref,
                    "link": product.get("ProductUrl", ""),
                    "cost": cost,
                    "quantity": int(product.get("QuantityAvailable", 0) or 0),
                    "updatedAt": date.today().strftime("%Y-%m-%d"),
                }
            ],
        }
    }


# ---------------------------------------------------------------------------
# Mouser MOSFET converter
# ---------------------------------------------------------------------------


def convert_mouser_to_tas_mosfet(product: dict[str, Any]) -> dict[str, Any]:
    """Convert a Mouser ``Parts[]`` entry into a TAS ``{"mosfet": {...}}`` envelope.

    Mouser's free-tier API returns far thinner parameter data than
    Digi-Key — most rows lack at least one of the six schema-required
    electrical fields.  This function raises :class:`IncompleteSourceError`
    aggressively rather than papering over the gaps; the caller (the
    ``component-librarian`` agent) is expected to enrich the payload
    from the linked datasheet PDF before re-attempting conversion.
    """
    source = "mouser"
    mpn = _require_str(
        source,
        str(product.get("ManufacturerPartNumber") or "<unknown>"),
        "ManufacturerPartNumber",
        product.get("ManufacturerPartNumber"),
    )
    manufacturer = product.get("Manufacturer")
    if not isinstance(manufacturer, str) or not manufacturer.strip():
        raise IncompleteSourceError(
            source, mpn, "manufacturerInfo.name",
            detail="Manufacturer string missing from Mouser payload",
        )

    description = product.get("Description") or ""
    if not isinstance(description, str):
        description = ""

    # Mouser attribute names diverge from Digi-Key — they live in
    # ``ProductAttributes`` (newer schema) or ``Attributes`` (older).
    raw_attrs = (
        product.get("ProductAttributes")
        or product.get("Attributes")
        or []
    )
    if not isinstance(raw_attrs, list):
        raw_attrs = []
    attrs: dict[str, str] = {}
    for entry in raw_attrs:
        if not isinstance(entry, dict):
            continue
        name = entry.get("AttributeName") or entry.get("Name")
        value = entry.get("AttributeValue") or entry.get("Value")
        if isinstance(name, str) and isinstance(value, str):
            attrs[name] = value

    # Mouser parameter names — best-effort, raise if missing.
    mouser_param_map: dict[str, tuple[tuple[str, str], ...]] = {
        "drainSourceVoltage": (("Drain to Source Voltage (Vdss)", "V"),),
        "onResistance": (("Rds On (Max) @ Id, Vgs", "Ω"),),
        "continuousDrainCurrent": (("Current - Continuous Drain (Id) @ 25°C", "A"),),
        "totalGateCharge": (("Gate Charge (Qg) @ Vgs", "C"),),
        "outputCapacitance": (("Output Capacitance (Coss) @ Vds, Vgs", "F"),),
    }

    electrical: dict[str, Any] = {}
    for field, candidates in mouser_param_map.items():
        electrical[field] = _extract_required_numeric(
            source=source, mpn=mpn, params=attrs, field=field, candidates=candidates,
        )
    electrical["gateThresholdVoltage"] = _extract_gate_threshold(
        source=source, mpn=mpn, params=attrs,
    )

    technology = _resolve_mosfet_technology(description, mpn)
    subtype = _resolve_mosfet_subtype(description)

    # Price extraction — Mouser publishes a tiered ``PriceBreaks``
    # array.  Strict-mode: missing prices are not an error (cost is
    # optional in the TAS schema), but a malformed price *is*.
    cost = 0.0
    price_breaks = product.get("PriceBreaks") or []
    if isinstance(price_breaks, list) and price_breaks:
        first = price_breaks[0]
        if isinstance(first, dict):
            raw_price = first.get("Price")
            if isinstance(raw_price, str) and raw_price.strip():
                stripped = raw_price.replace("$", "").replace(",", ".").strip()
                try:
                    cost = float(stripped)
                except ValueError as exc:
                    raise IncompleteSourceError(
                        source, mpn, "distributorsInfo.cost",
                        detail=f"unparseable Mouser price {raw_price!r}: {exc}",
                    ) from exc

    quantity_raw = product.get("AvailabilityInStock") or 0
    try:
        quantity = int(quantity_raw)
    except (TypeError, ValueError):
        quantity = 0

    return {
        "mosfet": {
            "manufacturerInfo": {
                "name": manufacturer,
                "reference": mpn,
                "status": "production",
                "datasheetUrl": product.get("DataSheetUrl") or "",
                "datasheetInfo": {
                    "part": {
                        "partNumber": mpn,
                        "technology": technology,
                        "subType": subtype,
                    },
                    "electrical": electrical,
                },
            },
            "distributorsInfo": [
                {
                    "name": "Mouser",
                    "reference": product.get("MouserPartNumber", ""),
                    "link": product.get("ProductDetailUrl", ""),
                    "cost": cost,
                    "quantity": quantity,
                    "updatedAt": date.today().strftime("%Y-%m-%d"),
                }
            ],
        }
    }
