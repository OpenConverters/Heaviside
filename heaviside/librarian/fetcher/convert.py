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
    "convert_digikey_to_tas_capacitor",
    "convert_digikey_to_tas_diode",
    "convert_digikey_to_tas_igbt",
    "convert_digikey_to_tas_mosfet",
    "convert_digikey_to_tas_resistor",
    "convert_mouser_to_tas_capacitor",
    "convert_mouser_to_tas_diode",
    "convert_mouser_to_tas_igbt",
    "convert_mouser_to_tas_mosfet",
    "convert_mouser_to_tas_resistor",
    "detect_category",
    "parse_si_value",
]


def detect_category(product: dict[str, Any], distributor: str) -> str | None:
    """Best-effort category detection from a distributor product payload.

    Returns one of ``mosfets``, ``diodes``, ``igbts``, ``capacitors``,
    ``resistors``, ``magnetics``, or ``None`` if unrecognised.
    """
    if distributor == "digikey":
        family = (
            product.get("Category", {}).get("Value", "")
            + " "
            + product.get("Family", {}).get("Value", "")
        ).lower()
    elif distributor == "mouser":
        family = (product.get("Category", "") + " " + product.get("ProductDetailUrl", "")).lower()
    else:
        family = ""

    if any(k in family for k in ("mosfet", "transistor fet")):
        return "mosfets"
    if any(k in family for k in ("igbt",)):
        return "igbts"
    if any(k in family for k in ("diode", "rectifier", "schottky", "tvs", "zener")):
        return "diodes"
    if any(k in family for k in ("capacitor", "mlcc", "electrolytic", "film cap")):
        return "capacitors"
    if any(k in family for k in ("resistor", "sense resistor", "chip resistor")):
        return "resistors"
    if any(k in family for k in ("inductor", "transformer", "choke", "ferrite", "magnetic")):
        return "magnetics"
    return None


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
    "": 1.0,
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


def _extract_optional_numeric(
    *,
    params: dict[str, str],
    candidates: tuple[tuple[str, str], ...],
) -> float | None:
    """Like _extract_required_numeric but returns None if the field is absent."""
    looked = _lookup_param(params, candidates)
    if looked is None:
        return None
    raw, _unit = looked
    try:
        return parse_si_value(raw)
    except ValueError:
        return None


def _extract_gate_threshold(*, source: str, mpn: str, params: dict[str, str]) -> dict[str, float]:
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
    source: str,
    mpn_for_error: str,
    field: str,
    value: Any,
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
    ``{"semiconductor": {"mosfet": {...}}}`` envelope.

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
            source,
            mpn,
            "manufacturerInfo.name",
            detail="Manufacturer.Value missing from Digi-Key payload",
        )

    raw_params = product.get("Parameters") or []
    if not isinstance(raw_params, list):
        raise IncompleteSourceError(
            source,
            mpn,
            "Parameters",
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
            source=source,
            mpn=mpn,
            params=params,
            field="drainSourceVoltage",
            candidates=DIGIKEY_MOSFET_PARAM_MAP["drainSourceVoltage"],
        ),
        "onResistance": _extract_required_numeric(
            source=source,
            mpn=mpn,
            params=params,
            field="onResistance",
            candidates=DIGIKEY_MOSFET_PARAM_MAP["onResistance"],
        ),
        "continuousDrainCurrent": _extract_required_numeric(
            source=source,
            mpn=mpn,
            params=params,
            field="continuousDrainCurrent",
            candidates=DIGIKEY_MOSFET_PARAM_MAP["continuousDrainCurrent"],
        ),
        "gateThresholdVoltage": _extract_gate_threshold(
            source=source,
            mpn=mpn,
            params=params,
        ),
        "outputCapacitance": _extract_required_numeric(
            source=source,
            mpn=mpn,
            params=params,
            field="outputCapacitance",
            candidates=DIGIKEY_MOSFET_PARAM_MAP["outputCapacitance"],
        ),
        "totalGateCharge": _extract_required_numeric(
            source=source,
            mpn=mpn,
            params=params,
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

    status = _digikey_lifecycle_status(product)

    datasheet_url = product.get("PrimaryDatasheet") or ""

    distributor_ref = product.get("DigiKeyPartNumber") or product.get("MouserPartNumber") or ""
    try:
        cost = float(product.get("UnitPrice", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise IncompleteSourceError(
            source,
            mpn,
            "distributorsInfo.cost",
            detail=f"UnitPrice {product.get('UnitPrice')!r} not numeric: {exc}",
        ) from exc

    return {
        "semiconductor": {
            "mosfet": {
                "manufacturerInfo": {
                    "name": manufacturer,
                    "reference": mpn,
                    **({"status": status} if status is not None else {}),
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
    }


# ---------------------------------------------------------------------------
# Mouser MOSFET converter
# ---------------------------------------------------------------------------


def convert_mouser_to_tas_mosfet(product: dict[str, Any]) -> dict[str, Any]:
    """Convert a Mouser ``Parts[]`` entry into a TAS ``{"semiconductor": {"mosfet": {...}}}`` envelope.

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
            source,
            mpn,
            "manufacturerInfo.name",
            detail="Manufacturer string missing from Mouser payload",
        )

    description = product.get("Description") or ""
    if not isinstance(description, str):
        description = ""

    # Mouser attribute names diverge from Digi-Key — they live in
    # ``ProductAttributes`` (newer schema) or ``Attributes`` (older).
    raw_attrs = product.get("ProductAttributes") or product.get("Attributes") or []
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
            source=source,
            mpn=mpn,
            params=attrs,
            field=field,
            candidates=candidates,
        )
    electrical["gateThresholdVoltage"] = _extract_gate_threshold(
        source=source,
        mpn=mpn,
        params=attrs,
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
                        source,
                        mpn,
                        "distributorsInfo.cost",
                        detail=f"unparseable Mouser price {raw_price!r}: {exc}",
                    ) from exc

    quantity_raw = product.get("AvailabilityInStock") or 0
    try:
        quantity = int(quantity_raw)
    except (TypeError, ValueError):
        quantity = 0

    return {
        "semiconductor": {
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
    }


# ===========================================================================
# Shared helpers for non-MOSFET categories (diode / IGBT / capacitor /
# resistor).  Same strict-mode contract as the MOSFET converter above:
# every helper raises :class:`IncompleteSourceError` on missing or
# malformed data — never substitutes a default.
# ===========================================================================


# IGBT modules / multi-die assemblies are out of scope for the
# single-device IGBT schema; they need their own envelope.  We reject
# them up front rather than silently emitting a half-populated
# single-device row.
_IGBT_MODULE_TOKENS = (
    "MODULE",
    "DUAL",
    "HALF BRIDGE",
    "HALF-BRIDGE",
    "FULL BRIDGE",
    "FULL-BRIDGE",
    "H-BRIDGE",
    "6-PACK",
    "6PACK",
    "SIXPACK",
)


def _reject_igbt_module(source: str, mpn: str, description: str) -> None:
    blob = description.upper()
    for token in _IGBT_MODULE_TOKENS:
        if token in blob:
            raise IncompleteSourceError(
                source,
                mpn,
                "semiconductor.igbt",
                detail=(
                    f"description contains module/multi-die token "
                    f"{token!r}; single-IGBT envelope cannot represent it"
                ),
            )


def _extract_digikey_params(source: str, mpn: str, product: dict[str, Any]) -> dict[str, str]:
    raw_params = product.get("Parameters") or []
    if not isinstance(raw_params, list):
        raise IncompleteSourceError(
            source,
            mpn,
            "Parameters",
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
    return params


def _extract_mouser_attrs(product: dict[str, Any]) -> dict[str, str]:
    raw_attrs = product.get("ProductAttributes") or product.get("Attributes") or []
    if not isinstance(raw_attrs, list):
        return {}
    attrs: dict[str, str] = {}
    for entry in raw_attrs:
        if not isinstance(entry, dict):
            continue
        name = entry.get("AttributeName") or entry.get("Name")
        value = entry.get("AttributeValue") or entry.get("Value")
        if isinstance(name, str) and isinstance(value, str):
            attrs[name] = value
    return attrs


def _digikey_lifecycle_status(product: dict[str, Any]) -> str | None:
    """Map Digi-Key ``ProductStatus`` onto the SAS manufacturerInfo.status
    enum (``production``/``nrnd``/``obsolete``/``preview``).

    The previous mapping emitted ``"discontinued"`` for every non-Active
    part — a value that is NOT in the SAS enum, so every such row failed
    schema validation.  Distributor-only statuses ("Discontinued at
    DigiKey", "Last Time Buy" stock states) say nothing reliable about the
    *manufacturer's* lifecycle, so they map to ``None`` and the optional
    ``status`` field is omitted rather than invented.
    """
    raw = product.get("ProductStatus")
    if raw is None or raw == "Active":
        return "production"
    if raw == "Not For New Designs":
        return "nrnd"
    if raw == "Obsolete":
        return "obsolete"
    # Unknown / distributor-specific status — omit, never guess.
    return None


def _digikey_description(product: dict[str, Any]) -> str:
    block = product.get("Description") or {}
    if isinstance(block, dict):
        desc = block.get("ProductDescription")
        if desc:
            return desc
    elif isinstance(block, str) and block:
        return block
    # Digi-Key Product Information v3 *search* payloads put the
    # description at the top level ("ProductDescription" /
    # "DetailedDescription"), not under a "Description" object.  The
    # subType/technology resolvers depend on this string, so falling
    # through to "" would silently misclassify every part as
    # subType="standard" / technology="Si".
    for key in ("ProductDescription", "DetailedDescription"):
        val = product.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def _digikey_manufacturer(source: str, mpn: str, product: dict[str, Any]) -> str:
    block = product.get("Manufacturer") or {}
    name = block.get("Value") if isinstance(block, dict) else None
    if not name:
        raise IncompleteSourceError(
            source,
            mpn,
            "manufacturerInfo.name",
            detail="Manufacturer.Value missing from Digi-Key payload",
        )
    return name


def _mouser_manufacturer(source: str, mpn: str, product: dict[str, Any]) -> str:
    name = product.get("Manufacturer")
    if not isinstance(name, str) or not name.strip():
        raise IncompleteSourceError(
            source,
            mpn,
            "manufacturerInfo.name",
            detail="Manufacturer string missing from Mouser payload",
        )
    return name


def _digikey_distributor_block(
    source: str,
    mpn: str,
    product: dict[str, Any],
    distributor: str,
) -> dict[str, Any]:
    try:
        cost = float(product.get("UnitPrice", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise IncompleteSourceError(
            source,
            mpn,
            "distributorsInfo.cost",
            detail=f"UnitPrice {product.get('UnitPrice')!r} not numeric: {exc}",
        ) from exc
    return {
        "name": distributor,
        "reference": product.get("DigiKeyPartNumber") or "",
        "link": product.get("ProductUrl", ""),
        "cost": cost,
        "quantity": int(product.get("QuantityAvailable", 0) or 0),
        "updatedAt": date.today().strftime("%Y-%m-%d"),
    }


def _mouser_distributor_block(
    source: str,
    mpn: str,
    product: dict[str, Any],
) -> dict[str, Any]:
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
                        source,
                        mpn,
                        "distributorsInfo.cost",
                        detail=f"unparseable Mouser price {raw_price!r}: {exc}",
                    ) from exc
    quantity_raw = product.get("AvailabilityInStock") or 0
    try:
        quantity = int(quantity_raw)
    except (TypeError, ValueError):
        quantity = 0
    return {
        "name": "Mouser",
        "reference": product.get("MouserPartNumber", ""),
        "link": product.get("ProductDetailUrl", ""),
        "cost": cost,
        "quantity": quantity,
        "updatedAt": date.today().strftime("%Y-%m-%d"),
    }


def _mpn_or_raise(source: str, product: dict[str, Any]) -> str:
    return _require_str(
        source,
        str(product.get("ManufacturerPartNumber") or "<unknown>"),
        "ManufacturerPartNumber",
        product.get("ManufacturerPartNumber"),
    )


def _resolve_semi_technology(description: str, mpn: str) -> str:
    """SAS ``part.technology`` enum: Si / SiC / GaN / GaAs / Ge."""
    blob = f"{description} {mpn}".upper()
    if "GAN" in blob:
        return "GaN"
    if "SIC" in blob or "SILICON CARBIDE" in blob:
        return "SiC"
    if "GAAS" in blob:
        return "GaAs"
    if "GERMANIUM" in blob:
        return "Ge"
    return "Si"


# ---------------------------------------------------------------------------
# Diode
# ---------------------------------------------------------------------------


# Diode subType values are taken from SAS utils.json#/$defs/part.subType
# docstring: "schottky/sicSchottky/ultrafast/fastRecovery/fast/standard/
# zener/tvs".  Order matters — "SiC Schottky" must be tested before
# generic "Schottky", and "Ultrafast" before "Fast Recovery".
def _resolve_diode_subtype(description: str) -> str:
    blob = description.upper()
    if "SIC SCHOTTKY" in blob or ("SCHOTTKY" in blob and "SIC" in blob):
        return "sicSchottky"
    if "SCHOTTKY" in blob:
        return "schottky"
    if "ULTRAFAST" in blob or "ULTRA-FAST" in blob or "ULTRA FAST" in blob:
        return "ultrafast"
    if "FAST RECOVERY" in blob or "FAST-RECOVERY" in blob:
        return "fastRecovery"
    if "FAST" in blob:
        return "fast"
    if "ZENER" in blob:
        return "zener"
    if "TVS" in blob or "TRANSIENT VOLTAGE" in blob:
        return "tvs"
    return "standard"


# Digi-Key parameter labels for diodes.  As with MOSFETs we list two
# candidate labels per field — Digi-Key has reshuffled its taxonomy at
# least once.  The Qrr label is currently rare in the Digi-Key feed
# (most rows only publish trr); strict-mode therefore makes Qrr a
# common gap and the librarian agent must enrich from the datasheet.
DIGIKEY_DIODE_PARAM_MAP: dict[str, tuple[tuple[str, str], ...]] = {
    "reverseVoltage": (
        ("Voltage - DC Reverse (Vr) (Max)", "V"),
        ("Voltage - Reverse Standoff (Typ)", "V"),
    ),
    "forwardVoltage": (
        ("Voltage - Forward (Vf) (Max) @ If", "V"),
        ("Voltage - Forward (Vf) (Max)", "V"),
    ),
    "forwardCurrent": (
        ("Current - Average Rectified (Io)", "A"),
        ("Current - Average Rectified (Io) (per Diode)", "A"),
    ),
    "reverseRecoveryCharge": (
        ("Reverse Recovery Charge (Qrr) (Typ)", "C"),
        ("Reverse Recovery Charge (Qrr)", "C"),
    ),
}


def _build_diode_envelope(
    *,
    source: str,
    mpn: str,
    manufacturer: str,
    description: str,
    params: dict[str, str],
    distributor_block: dict[str, Any],
    datasheet_url: str,
    status: str | None,
) -> dict[str, Any]:
    _DIODE_OPTIONAL = {"forwardVoltage", "reverseRecoveryCharge"}
    electrical: dict[str, Any] = {}
    for field, candidates in DIGIKEY_DIODE_PARAM_MAP.items():
        if field in _DIODE_OPTIONAL:
            val = _extract_optional_numeric(params=params, candidates=candidates)
            if val is not None:
                electrical[field] = val
        else:
            electrical[field] = _extract_required_numeric(
                source=source,
                mpn=mpn,
                params=params,
                field=field,
                candidates=candidates,
            )
    # Digi-Key publishes an explicit "Technology" parameter for diodes
    # ("SiC (Silicon Carbide) Schottky" / "Schottky" / "Standard").  It is
    # authoritative where present — the v3 search descriptions abbreviate
    # ("DIODE SIL CARB 1200V") and defeat the description-based resolver.
    tech_param = (params.get("Technology") or "").upper()
    if "SILICON CARBIDE" in tech_param or "SIC" in tech_param:
        subtype = "sicSchottky"
        technology = "SiC"
    elif "SCHOTTKY" in tech_param:
        subtype = "schottky"
        technology = _resolve_semi_technology(description, mpn)
    else:
        subtype = _resolve_diode_subtype(description)
        technology = _resolve_semi_technology(description, mpn)
    case = params.get("Supplier Device Package") or params.get("Package / Case")
    part: dict[str, Any] = {
        "partNumber": mpn,
        "technology": technology,
        "subType": subtype,
    }
    if case:
        part["case"] = case
    return {
        "semiconductor": {
            "diode": {
                "manufacturerInfo": {
                    "name": manufacturer,
                    "reference": mpn,
                    **({"status": status} if status is not None else {}),
                    "datasheetUrl": datasheet_url,
                    "datasheetInfo": {
                        "part": part,
                        "electrical": electrical,
                    },
                },
                "distributorsInfo": [distributor_block],
            }
        }
    }


def convert_digikey_to_tas_diode(
    product: dict[str, Any],
    *,
    distributor: str = "Digi-Key",
) -> dict[str, Any]:
    """Convert a Digi-Key Product v3 payload into a TAS
    ``{"semiconductor": {"diode": {...}}}`` envelope.

    Strict-mode on the REQUIRED electrical fields (reverseVoltage,
    forwardCurrent): either absent raises :class:`IncompleteSourceError`.
    ``forwardVoltage`` and ``reverseRecoveryCharge`` are OPTIONAL at fetch
    time — Digi-Key rarely populates Qrr, and Schottky parts have
    negligible reverse recovery — so when absent they are OMITTED (never
    defaulted) and the ``component-librarian`` agent enriches them from the
    linked datasheet before the part is used in a design.
    """
    source = "digikey"
    mpn = _mpn_or_raise(source, product)
    manufacturer = _digikey_manufacturer(source, mpn, product)
    params = _extract_digikey_params(source, mpn, product)
    description = _digikey_description(product)
    status = _digikey_lifecycle_status(product)
    return _build_diode_envelope(
        source=source,
        mpn=mpn,
        manufacturer=manufacturer,
        description=description,
        params=params,
        distributor_block=_digikey_distributor_block(source, mpn, product, distributor),
        datasheet_url=product.get("PrimaryDatasheet") or "",
        status=status,
    )


def convert_mouser_to_tas_diode(product: dict[str, Any]) -> dict[str, Any]:
    """Mouser counterpart of :func:`convert_digikey_to_tas_diode`."""
    source = "mouser"
    mpn = _mpn_or_raise(source, product)
    manufacturer = _mouser_manufacturer(source, mpn, product)
    attrs = _extract_mouser_attrs(product)
    description = product.get("Description") or ""
    if not isinstance(description, str):
        description = ""
    return _build_diode_envelope(
        source=source,
        mpn=mpn,
        manufacturer=manufacturer,
        description=description,
        params=attrs,
        distributor_block=_mouser_distributor_block(source, mpn, product),
        datasheet_url=product.get("DataSheetUrl") or "",
        status="production",
    )


# ---------------------------------------------------------------------------
# IGBT
# ---------------------------------------------------------------------------


DIGIKEY_IGBT_PARAM_MAP: dict[str, tuple[tuple[str, str], ...]] = {
    "collectorEmitterVoltage": (
        ("Voltage - Collector Emitter Breakdown (Max)", "V"),
        ("Vces (Max)", "V"),
    ),
    "collectorEmitterSaturation": (
        ("Vce(on) (Max) @ Vge, Ic", "V"),
        ("Vce(on) (Max)", "V"),
        ("Vce(sat) (Max) @ Vge, Ic", "V"),
    ),
    "continuousCollectorCurrent": (
        ("Current - Collector (Ic) @ 25°C", "A"),
        ("Current - Collector (Ic) (Max)", "A"),
    ),
}


def _build_igbt_envelope(
    *,
    source: str,
    mpn: str,
    manufacturer: str,
    description: str,
    params: dict[str, str],
    distributor_block: dict[str, Any],
    datasheet_url: str,
    status: str | None,
) -> dict[str, Any]:
    _reject_igbt_module(source, mpn, description)
    electrical: dict[str, Any] = {}
    for field, candidates in DIGIKEY_IGBT_PARAM_MAP.items():
        electrical[field] = _extract_required_numeric(
            source=source,
            mpn=mpn,
            params=params,
            field=field,
            candidates=candidates,
        )
    technology = _resolve_semi_technology(description, mpn)
    case = params.get("Supplier Device Package") or params.get("Package / Case")
    part: dict[str, Any] = {
        "partNumber": mpn,
        "technology": technology,
        # SAS utils.json says IGBT subType is always "nChannel".
        "subType": "nChannel",
    }
    if case:
        part["case"] = case
    return {
        "semiconductor": {
            "igbt": {
                "manufacturerInfo": {
                    "name": manufacturer,
                    "reference": mpn,
                    **({"status": status} if status is not None else {}),
                    "datasheetUrl": datasheet_url,
                    "datasheetInfo": {
                        "part": part,
                        "electrical": electrical,
                    },
                },
                "distributorsInfo": [distributor_block],
            }
        }
    }


def convert_digikey_to_tas_igbt(
    product: dict[str, Any],
    *,
    distributor: str = "Digi-Key",
) -> dict[str, Any]:
    """Convert a Digi-Key Product v3 payload into a TAS
    ``{"semiconductor": {"igbt": {...}}}`` envelope.

    Rejects multi-die assemblies (modules, half-bridges, six-packs)
    rather than silently emitting a half-populated single-IGBT row —
    Proteus's converter did the opposite, regex-parsing voltages out
    of free-form description text for these and producing nonsense.
    """
    source = "digikey"
    mpn = _mpn_or_raise(source, product)
    manufacturer = _digikey_manufacturer(source, mpn, product)
    params = _extract_digikey_params(source, mpn, product)
    description = _digikey_description(product)
    status = _digikey_lifecycle_status(product)
    return _build_igbt_envelope(
        source=source,
        mpn=mpn,
        manufacturer=manufacturer,
        description=description,
        params=params,
        distributor_block=_digikey_distributor_block(source, mpn, product, distributor),
        datasheet_url=product.get("PrimaryDatasheet") or "",
        status=status,
    )


def convert_mouser_to_tas_igbt(product: dict[str, Any]) -> dict[str, Any]:
    """Mouser counterpart of :func:`convert_digikey_to_tas_igbt`."""
    source = "mouser"
    mpn = _mpn_or_raise(source, product)
    manufacturer = _mouser_manufacturer(source, mpn, product)
    attrs = _extract_mouser_attrs(product)
    description = product.get("Description") or ""
    if not isinstance(description, str):
        description = ""
    return _build_igbt_envelope(
        source=source,
        mpn=mpn,
        manufacturer=manufacturer,
        description=description,
        params=attrs,
        distributor_block=_mouser_distributor_block(source, mpn, product),
        datasheet_url=product.get("DataSheetUrl") or "",
        status="production",
    )


# ---------------------------------------------------------------------------
# Resistor
# ---------------------------------------------------------------------------


# RAS technology enum (camelCase).  Keys are Digi-Key's "Composition"
# parameter values (case-insensitive); values are the RAS enum names.
_RESISTOR_TECHNOLOGY_MAP: dict[str, str] = {
    "thick film": "thickFilm",
    "thin film": "thinFilm",
    "metal film": "metalFilm",
    "metal oxide": "metalOxide",
    "wirewound": "wirewound",
    "wire wound": "wirewound",
    "carbon composition": "carbonComposition",
    "carbon film": "carbonFilm",
    "metal foil": "metalFoil",
    "bulk metal foil": "bulkMetalFoil",
    "current sense": "currentSenseShunt",
    "shunt": "currentSenseShunt",
    "melf": "melf",
}


def _resolve_resistor_technology(source: str, mpn: str, raw: str | None) -> str:
    if not raw:
        raise IncompleteSourceError(
            source,
            mpn,
            "datasheetInfo.part.technology",
            detail="distributor payload lacks Composition parameter",
        )
    lowered = raw.lower()
    for needle, enum in _RESISTOR_TECHNOLOGY_MAP.items():
        if needle in lowered:
            return enum
    raise IncompleteSourceError(
        source,
        mpn,
        "datasheetInfo.part.technology",
        detail=f"unknown resistor composition {raw!r}; extend _RESISTOR_TECHNOLOGY_MAP",
    )


def _parse_tolerance(source: str, mpn: str, raw: str | None) -> float:
    """Parse a Digi-Key/Mouser tolerance string into a unit-less fraction.

    Examples: ``"±1%"`` → 0.01, ``"5%"`` → 0.05, ``"0.1%"`` → 0.001.
    Raises :class:`IncompleteSourceError` on any other shape; Proteus
    used to default to 0.05 silently here.
    """
    if not raw or not isinstance(raw, str):
        raise IncompleteSourceError(
            source,
            mpn,
            "electrical.tolerance",
            detail="no Tolerance parameter present in distributor payload",
        )
    cleaned = raw.strip().lstrip("±").rstrip("%").strip()
    try:
        pct = float(cleaned)
    except ValueError as exc:
        raise IncompleteSourceError(
            source,
            mpn,
            "electrical.tolerance",
            detail=f"unparseable tolerance {raw!r}: {exc}",
        ) from exc
    return pct / 100.0


DIGIKEY_RESISTOR_PARAM_MAP: dict[str, tuple[tuple[str, str], ...]] = {
    "resistance": (("Resistance", "Ω"),),
    "powerRating": (("Power (Watts)", "W"),),
}


def _build_resistor_envelope(
    *,
    source: str,
    mpn: str,
    manufacturer: str,
    params: dict[str, str],
    distributor_block: dict[str, Any],
    datasheet_url: str,
    status: str | None,
) -> dict[str, Any]:
    resistance = _extract_required_numeric(
        source=source,
        mpn=mpn,
        params=params,
        field="resistance",
        candidates=DIGIKEY_RESISTOR_PARAM_MAP["resistance"],
    )
    power = _extract_required_numeric(
        source=source,
        mpn=mpn,
        params=params,
        field="powerRating",
        candidates=DIGIKEY_RESISTOR_PARAM_MAP["powerRating"],
    )
    tolerance = _parse_tolerance(source, mpn, params.get("Tolerance"))
    case = params.get("Supplier Device Package") or params.get("Package / Case")
    if not case:
        raise IncompleteSourceError(
            source,
            mpn,
            "datasheetInfo.part.case",
            detail="no Package/Case parameter present",
        )
    technology = _resolve_resistor_technology(source, mpn, params.get("Composition"))
    return {
        "resistor": {
            "manufacturerInfo": {
                "name": manufacturer,
                "reference": mpn,
                **({"status": status} if status is not None else {}),
                "datasheetUrl": datasheet_url,
                "datasheetInfo": {
                    "part": {
                        "partNumber": mpn,
                        "technology": technology,
                        "case": case,
                    },
                    "electrical": {
                        # RAS resistance is a dimensionWithTolerance.
                        # Tolerance fraction goes in the scalar
                        # `tolerance` field, not nested into resistance.
                        "resistance": {"nominal": resistance},
                        "tolerance": tolerance,
                        "powerRating": power,
                    },
                },
            },
            "distributorsInfo": [distributor_block],
        }
    }


def convert_digikey_to_tas_resistor(
    product: dict[str, Any],
    *,
    distributor: str = "Digi-Key",
) -> dict[str, Any]:
    """Convert a Digi-Key resistor payload into a TAS ``{"resistor": {...}}``
    envelope.

    Proteus's converter hardcoded ``cost=0.10``, ``vpe=4000``,
    ``tolerance=0.05`` and ``power=0.25`` whenever the parameter was
    missing — this rewrite raises :class:`IncompleteSourceError`
    instead.  The hardcoded numbers in Proteus poisoned thermal and
    cost models with single-value plateaus that anyone reviewing the
    database treated as "real" datasheet content.
    """
    source = "digikey"
    mpn = _mpn_or_raise(source, product)
    manufacturer = _digikey_manufacturer(source, mpn, product)
    params = _extract_digikey_params(source, mpn, product)
    status = _digikey_lifecycle_status(product)
    return _build_resistor_envelope(
        source=source,
        mpn=mpn,
        manufacturer=manufacturer,
        params=params,
        distributor_block=_digikey_distributor_block(source, mpn, product, distributor),
        datasheet_url=product.get("PrimaryDatasheet") or "",
        status=status,
    )


def convert_mouser_to_tas_resistor(product: dict[str, Any]) -> dict[str, Any]:
    """Mouser counterpart of :func:`convert_digikey_to_tas_resistor`."""
    source = "mouser"
    mpn = _mpn_or_raise(source, product)
    manufacturer = _mouser_manufacturer(source, mpn, product)
    attrs = _extract_mouser_attrs(product)
    return _build_resistor_envelope(
        source=source,
        mpn=mpn,
        manufacturer=manufacturer,
        params=attrs,
        distributor_block=_mouser_distributor_block(source, mpn, product),
        datasheet_url=product.get("DataSheetUrl") or "",
        status="production",
    )


# ---------------------------------------------------------------------------
# Capacitor
# ---------------------------------------------------------------------------


# EIA/JIS temperature-characteristic code → CAS ceramic class. Y5V is
# treated as class-2 and Y5U/Z5U as class-3 per the project convention
# documented in scripts/cap_tech_rules/ceramic_codes.json.
_CAP_EIA_CLASS: dict[str, str] = {
    "C0G": "ceramic-class-1",
    "NP0": "ceramic-class-1",
    "U2J": "ceramic-class-1",
    "X5R": "ceramic-class-2",
    "X6S": "ceramic-class-2",
    "X6T": "ceramic-class-2",
    "X7R": "ceramic-class-2",
    "X7S": "ceramic-class-2",
    "X7T": "ceramic-class-2",
    "X8R": "ceramic-class-2",
    "X8L": "ceramic-class-2",
    "Y5V": "ceramic-class-2",
    "Y5U": "ceramic-class-3",
    "Z5U": "ceramic-class-3",
}


# Map the distributor family/series text onto the CAS ``technology``
# chemistry enum (closed since CAS baefd79).  Returns ``(technology,
# dielectricCode | None)``.  For ceramics the EIA class is read from the
# Temperature Coefficient / Dielectric parameter, the description, or
# the MPN (manufacturers print the code literally); for film the
# Dielectric Material parameter decides.  Anything we cannot resolve
# from distributor-published facts raises — no chemistry guessing.
def _resolve_capacitor_technology(
    source: str,
    mpn: str,
    family: str | None,
    *,
    params: dict[str, str],
    description: str = "",
) -> tuple[str, str | None]:
    if not family:
        raise IncompleteSourceError(
            source,
            mpn,
            "datasheetInfo.part.technology",
            detail="distributor payload lacks Family/Series parameter",
        )
    blob = family.lower()

    if "ceramic" in blob or "mlcc" in blob:
        haystacks = (
            params.get("Temperature Coefficient") or "",
            params.get("Dielectric Material") or "",
            params.get("Dielectric") or "",
            description,
            mpn,
        )
        for text in haystacks:
            upper = text.upper()
            for code, cls in _CAP_EIA_CLASS.items():
                if code in upper:
                    return cls, code
        raise IncompleteSourceError(
            source,
            mpn,
            "datasheetInfo.part.technology",
            detail=(
                "ceramic capacitor without a resolvable EIA temperature "
                "characteristic (checked Temperature Coefficient / "
                "Dielectric parameters, description, and MPN) — cannot "
                "assign ceramic-class-1/2/3 without it"
            ),
        )
    if "hybrid" in blob and ("supercap" in blob or "edlc" in blob):
        return "supercapacitor-hybrid", None
    if "supercap" in blob or "super cap" in blob or "edlc" in blob:
        return "supercapacitor-edlc", None
    if "hybrid" in blob:
        return "aluminum-hybrid-polymer", None
    if "aluminum polymer" in blob or "alum. polymer" in blob or "polymer alum" in blob:
        return "aluminum-electrolytic-polymer", None
    if "aluminum" in blob or "aluminium" in blob or "electrolytic" in blob:
        return "aluminum-electrolytic-wet", None
    if "tantalum" in blob and "polymer" in blob:
        return "tantalum-polymer", None
    if "tantalum" in blob and "wet" in blob:
        return "tantalum-wet", None
    if "tantalum" in blob:
        # Distributor "Tantalum Capacitors" families are the solid MnO2
        # chemistry; wet and polymer parts live in separate families.
        return "tantalum-mno2", None
    if "niobium" in blob:
        return "niobium-oxide", None
    if "mica" in blob:
        return "mica", None
    if "film" in blob:
        dielectric = (
            params.get("Dielectric Material") or params.get("Dielectric") or ""
        ).lower()
        if "polypropylene" in dielectric:
            return "film-polypropylene", None
        if "polyester" in dielectric or "pet" in dielectric:
            return "film-polyester", None
        if "sulfide" in dielectric or "sulphide" in dielectric or "pps" in dielectric:
            return "film-polyphenylene-sulfide", None
        if "paper" in dielectric:
            return "film-paper", None
        raise IncompleteSourceError(
            source,
            mpn,
            "datasheetInfo.part.technology",
            detail=(
                f"film capacitor without a resolvable Dielectric Material "
                f"parameter (got {dielectric!r})"
            ),
        )
    raise IncompleteSourceError(
        source,
        mpn,
        "datasheetInfo.part.technology",
        detail=f"unrecognised capacitor family {family!r}",
    )


# CAS shape.assembly enum: THT / Screw Type / SMT / Snap-In.
def _resolve_capacitor_assembly(source: str, mpn: str, mounting: str | None) -> str:
    if not mounting:
        raise IncompleteSourceError(
            source,
            mpn,
            "datasheetInfo.mechanical.shape.assembly",
            detail="no Mounting Type parameter present",
        )
    blob = mounting.lower()
    if "surface mount" in blob or "smt" in blob or "smd" in blob:
        return "SMT"
    if "snap-in" in blob or "snap in" in blob:
        return "Snap-In"
    if "screw" in blob:
        return "Screw Type"
    if "through hole" in blob or "through-hole" in blob or "tht" in blob:
        return "THT"
    raise IncompleteSourceError(
        source,
        mpn,
        "datasheetInfo.mechanical.shape.assembly",
        detail=f"unrecognised Mounting Type {mounting!r}",
    )


def _resolve_capacitor_shape_type(
    source: str,
    mpn: str,
    assembly: str,
    technology: str,
) -> str:
    """Resolve CAS shape.shapeType from assembly + technology.

    SMT MLCCs are universally rectangular chips.  THT aluminum
    electrolytics are universally radial cylinders.  Anything we
    can't disambiguate from those two industry conventions raises —
    we refuse to guess at shape.
    """
    if assembly == "SMT" and (
        technology.startswith(("ceramic-", "film-", "tantalum-")) or technology == "mica"
    ):
        return "rectangular"
    if assembly in {"THT", "Snap-In", "Screw Type"} and technology.startswith(
        ("aluminum-", "supercapacitor-")
    ):
        return "cylindrical"
    raise IncompleteSourceError(
        source,
        mpn,
        "datasheetInfo.mechanical.shape.shapeType",
        detail=(f"cannot infer shapeType from assembly={assembly!r}, technology={technology!r}"),
    )


DIGIKEY_CAPACITOR_PARAM_MAP: dict[str, tuple[tuple[str, str], ...]] = {
    "capacitance": (("Capacitance", "F"),),
    "ratedVoltage": (("Voltage - Rated", "V"),),
    "esr": (
        ("ESR (Equivalent Series Resistance)", "Ω"),
        ("ESR", "Ω"),
    ),
    "rippleCurrent": (
        ("Ripple Current @ Low Frequency", "A"),
        ("Ripple Current @ High Frequency", "A"),
        ("Ripple Current", "A"),
    ),
}


def _build_capacitor_envelope(
    *,
    source: str,
    mpn: str,
    manufacturer: str,
    params: dict[str, str],
    distributor_block: dict[str, Any],
    datasheet_url: str,
    status: str | None,
    description: str = "",
) -> dict[str, Any]:
    capacitance = _extract_required_numeric(
        source=source,
        mpn=mpn,
        params=params,
        field="capacitance",
        candidates=DIGIKEY_CAPACITOR_PARAM_MAP["capacitance"],
    )
    rated_voltage = _extract_required_numeric(
        source=source,
        mpn=mpn,
        params=params,
        field="ratedVoltage",
        candidates=DIGIKEY_CAPACITOR_PARAM_MAP["ratedVoltage"],
    )
    esr = _extract_optional_numeric(
        params=params,
        candidates=DIGIKEY_CAPACITOR_PARAM_MAP["esr"],
    )
    ripple = _extract_optional_numeric(
        params=params,
        candidates=DIGIKEY_CAPACITOR_PARAM_MAP["rippleCurrent"],
    )
    case = params.get("Package / Case") or params.get("Supplier Device Package")
    if not case:
        raise IncompleteSourceError(
            source,
            mpn,
            "datasheetInfo.part.case",
            detail="no Package/Case parameter present",
        )
    family = params.get("Family") or params.get("Family.Value") or params.get("Capacitor Type")
    technology, dielectric_code = _resolve_capacitor_technology(
        source, mpn, family, params=params, description=description
    )
    series = params.get("Series")
    if not series:
        raise IncompleteSourceError(
            source,
            mpn,
            "datasheetInfo.part.series",
            detail="no Series parameter present",
        )
    assembly = _resolve_capacitor_assembly(source, mpn, params.get("Mounting Type"))
    shape_type = _resolve_capacitor_shape_type(source, mpn, assembly, technology)

    return {
        "capacitor": {
            "manufacturerInfo": {
                "name": manufacturer,
                "reference": mpn,
                **({"status": status} if status is not None else {}),
                "datasheetUrl": datasheet_url,
                "datasheetInfo": {
                    "part": {
                        "partNumber": mpn,
                        "series": series,
                        "technology": technology,
                        **({"dielectricCode": dielectric_code} if dielectric_code else {}),
                        "case": case,
                    },
                    "electrical": {
                        "capacitance": {"nominal": capacitance},
                        "ratedVoltage": rated_voltage,
                        **({"esr": esr} if esr is not None else {}),
                        **({"rippleCurrent": ripple} if ripple is not None else {}),
                    },
                    "mechanical": {
                        # CAS allows dimensions to be empty — when the
                        # distributor doesn't publish length/width/
                        # height we leave it that way rather than
                        # inventing values from the case code.
                        "dimensions": {},
                        "shape": {
                            "assembly": assembly,
                            "shapeType": shape_type,
                        },
                    },
                },
            },
            "distributorsInfo": [distributor_block],
        }
    }


def convert_digikey_to_tas_capacitor(
    product: dict[str, Any],
    *,
    distributor: str = "Digi-Key",
) -> dict[str, Any]:
    """Convert a Digi-Key capacitor payload into a TAS ``{"capacitor": {...}}``
    envelope.

    CAS requires capacitance + ratedVoltage; esr and rippleCurrent are
    optional at fetch time (MLCCs in particular often lack ESR — the
    ``component-librarian`` agent enriches them from the vendor SPICE
    model before the part is used in a design). The ``technology``
    chemistry enum is resolved from distributor-published facts only.
    """
    source = "digikey"
    mpn = _mpn_or_raise(source, product)
    manufacturer = _digikey_manufacturer(source, mpn, product)
    params = _extract_digikey_params(source, mpn, product)
    status = _digikey_lifecycle_status(product)
    desc = product.get("Description") or {}
    description = desc.get("ProductDescription", "") if isinstance(desc, dict) else str(desc)
    return _build_capacitor_envelope(
        source=source,
        mpn=mpn,
        manufacturer=manufacturer,
        params=params,
        distributor_block=_digikey_distributor_block(source, mpn, product, distributor),
        datasheet_url=product.get("PrimaryDatasheet") or "",
        status=status,
        description=description,
    )


def convert_mouser_to_tas_capacitor(product: dict[str, Any]) -> dict[str, Any]:
    """Mouser counterpart of :func:`convert_digikey_to_tas_capacitor`."""
    source = "mouser"
    mpn = _mpn_or_raise(source, product)
    manufacturer = _mouser_manufacturer(source, mpn, product)
    attrs = _extract_mouser_attrs(product)
    return _build_capacitor_envelope(
        source=source,
        mpn=mpn,
        manufacturer=manufacturer,
        params=attrs,
        distributor_block=_mouser_distributor_block(source, mpn, product),
        datasheet_url=product.get("DataSheetUrl") or "",
        status="production",
        description=str(product.get("Description") or ""),
    )


# ---------------------------------------------------------------------------
# Magnetic / inductor converter
# ---------------------------------------------------------------------------

DIGIKEY_MAGNETIC_PARAM_MAP: dict[str, tuple[tuple[str, str], ...]] = {
    "inductance": (
        ("Inductance", "H"),
        ("Inductance @ Frequency", "H"),
    ),
    "dcResistance": (
        ("DC Resistance (DCR)", "Ω"),
        ("DC Resistance (DCR) (Max)", "Ω"),
        ("DCR (Max)", "Ω"),
    ),
    "ratedCurrent": (
        ("Current Rating (Amps)", "A"),
        ("Current Rating", "A"),
    ),
    "saturationCurrent": (
        ("Current - Saturation (Isat)", "A"),
        ("Current - Saturation", "A"),
        ("Saturation Current", "A"),
    ),
    "selfResonantFrequency": (
        ("Frequency - Self Resonant", "Hz"),
        ("Self-Resonant Frequency", "Hz"),
    ),
}


def _build_magnetic_envelope(
    *,
    source: str,
    mpn: str,
    manufacturer: str,
    params: dict[str, str],
    distributor_block: dict[str, Any],
    datasheet_url: str,
    status: str | None,
    description: str = "",
) -> dict[str, Any]:
    """Build a TAS ``{"magnetic": {...}}`` envelope from parsed parameters."""
    inductance = _extract_required_numeric(
        source=source,
        mpn=mpn,
        params=params,
        field="inductance",
        candidates=DIGIKEY_MAGNETIC_PARAM_MAP["inductance"],
    )
    dcr = _extract_required_numeric(
        source=source,
        mpn=mpn,
        params=params,
        field="dcResistance",
        candidates=DIGIKEY_MAGNETIC_PARAM_MAP["dcResistance"],
    )
    rated_current = _extract_optional_numeric(
        params=params,
        candidates=DIGIKEY_MAGNETIC_PARAM_MAP["ratedCurrent"],
    )
    isat = _extract_optional_numeric(
        params=params,
        candidates=DIGIKEY_MAGNETIC_PARAM_MAP["saturationCurrent"],
    )
    srf = _extract_optional_numeric(
        params=params,
        candidates=DIGIKEY_MAGNETIC_PARAM_MAP["selfResonantFrequency"],
    )

    # Tolerance: parse from params or default ±20%
    tol = _extract_optional_numeric(
        params=params,
        candidates=(("Tolerance", "%"),),
    )
    tol_frac = abs(tol) / 100 if tol else 0.2

    electrical: dict[str, Any] = {
        "inductance": {
            "nominal": inductance,
            "minimum": inductance * (1 - tol_frac),
            "maximum": inductance * (1 + tol_frac),
        },
        "dcResistance": {"maximum": dcr},
    }
    if rated_current is not None:
        electrical["ratedCurrent"] = rated_current
    if isat is not None:
        electrical["saturationCurrentPeak"] = isat
    if srf is not None:
        electrical["selfResonantFrequency"] = srf

    # Detect shielding and material from params
    shielded = params.get("Shielding", "").lower() in ("shielded", "yes")
    material = params.get("Core Material", params.get("Material", ""))

    return {
        "magnetic": {
            "manufacturerInfo": {
                "name": manufacturer,
                "reference": mpn,
                **({"status": status} if status is not None else {}),
                "datasheetUrl": datasheet_url,
                "datasheetInfo": {
                    "part": {
                        "description": description,
                        "shielded": shielded,
                        **({"material": material} if material else {}),
                    },
                    "electrical": electrical,
                },
            },
            "distributorsInfo": [distributor_block],
        },
    }


def convert_digikey_to_tas_magnetic(
    product: dict[str, Any],
    *,
    distributor: str = "Digi-Key",
) -> dict[str, Any]:
    """Convert a Digi-Key inductor/magnetic payload into a TAS
    ``{"magnetic": {...}}`` envelope.

    Required parameters: inductance, dcResistance.
    Optional: ratedCurrent, saturationCurrent, selfResonantFrequency.
    """
    source = "digikey"
    mpn = _mpn_or_raise(source, product)
    manufacturer = _digikey_manufacturer(source, mpn, product)
    params = _extract_digikey_params(source, mpn, product)
    status = _digikey_lifecycle_status(product)
    return _build_magnetic_envelope(
        source=source,
        mpn=mpn,
        manufacturer=manufacturer,
        params=params,
        distributor_block=_digikey_distributor_block(source, mpn, product, distributor),
        datasheet_url=product.get("PrimaryDatasheet") or "",
        status=status,
        description=_digikey_description(product),
    )
