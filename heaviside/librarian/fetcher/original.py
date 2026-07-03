"""Fetch a BOM component's ORIGINAL part from Digi-Key when it is not in the
internal DB, so the cross-referencer can verify it instead of giving up.

Policy (user, 2026-07-03): "the librarian should fetch everything." An original
that isn't in the internal catalogue is looked up on Digi-Key by its exact MPN,
converted to the category's PEAS envelope, VALIDATED against the schema, and
persisted — so the identity-matched no_substitute rule only fires after Digi-Key
also comes up empty, not just because our DB happens to lack the part.

Safety: the converted envelope is validated against its schema before it is
persisted or returned. A conversion that can't produce a schema-valid part is
DROPPED (returns None) — we never inject fabricated/half-parsed original specs
into the pipeline (that would be the very "garbage cross-reference" this exists
to prevent). Categories without a converter simply return None.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parents[3]

# Crossref category -> (plural DB/category name used by the librarian validator
# + NDJSON file, e.g. "connectors").
_CATEGORY_TO_DB: dict[str, str] = {
    "capacitor": "capacitors",
    "resistor": "resistors",
    "diode": "diodes",
    "mosfet": "mosfets",
    "igbt": "igbts",
    "magnetic": "magnetics",
    "connector": "connectors",
    "timeBase": "timebases",
}


def _load_script_module(name: str):
    """Import a scripts/*.py module by path (they're not a package). Safe: the
    scripts only define functions/constants at import — main() is behind a
    __name__ guard."""
    path = _REPO / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_hs_script_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load script module {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _canonical_mfr(product: dict[str, Any]) -> str:
    return (product.get("Manufacturer") or {}).get("Value") or ""


# Plural DB/category name -> singular crossref category (inverse of _CATEGORY_TO_DB).
CR_CATEGORY_BY_DB: dict[str, str] = {v: k for k, v in {
    "capacitor": "capacitors",
    "resistor": "resistors",
    "diode": "diodes",
    "mosfet": "mosfets",
    "igbt": "igbts",
    "magnetic": "magnetics",
    "connector": "connectors",
    "timeBase": "timebases",
}.items()}


# Digi-Key Category/Family text -> crossref category. Ordered most-specific first;
# matched as substrings against the lowercased Digi-Key "Category" + "Family".
# Manufacturer-agnostic: classification is by the part's *type*, from the
# distributor's own taxonomy, never by who makes it. Used to categorise a bare
# pasted MPN (e.g. "1707654") that isn't in the internal catalogue and carries no
# description keyword, so the librarian can still source + verify it.
_DK_CATEGORY_RULES: list[tuple[str, str]] = [
    ("terminal block", "connector"),
    ("connector", "connector"),
    ("interconnect", "connector"),
    ("crystal", "timeBase"),
    ("oscillator", "timeBase"),
    ("resonator", "timeBase"),
    ("capacitor", "capacitor"),
    ("resistor", "resistor"),
    ("ferrite bead", "magnetic"),
    ("inductor", "magnetic"),
    ("choke", "magnetic"),
    ("transformer", "magnetic"),
    ("igbt", "igbt"),
    ("diode", "diode"),
    ("rectifier", "diode"),
    ("schottky", "diode"),
    ("tvs", "diode"),
    ("zener", "diode"),
    ("mosfet", "mosfet"),
    ("transistor", "mosfet"),
    ("fet", "mosfet"),
]


def classify_dk_product(product: dict[str, Any]) -> str | None:
    """Infer the crossref category from a Digi-Key product's Category/Family
    taxonomy. Returns None when it isn't a category the librarian can source."""
    text = (
        ((product.get("Category") or {}).get("Value") or "")
        + " "
        + ((product.get("Family") or {}).get("Value") or "")
    ).lower()
    for keyword, category in _DK_CATEGORY_RULES:
        if keyword in text:
            return category
    return None


# Digi-Key connector "Family" / description keyword -> CONAS family. Ordered
# most-specific first; matched as substrings against the lowercased DK family +
# description. Manufacturer-agnostic: we branch on the connector's *physics /
# type*, never on who makes it (a Phoenix Contact terminal block and a Würth one
# both map to "terminalBlock"). Fallback is "wireToBoard" (the commonest
# discrete-wire type) — never a drop.
_DK_FAMILY_RULES: list[tuple[str, str]] = [
    ("terminal block", "terminalBlock"),
    ("card edge", "cardEdge"),
    ("edge connector", "cardEdge"),
    ("ffc", "fpcFfc"),
    ("fpc", "fpcFfc"),
    ("flat flex", "fpcFfc"),
    ("mezzanine", "boardToBoard"),
    ("board to board", "boardToBoard"),
    ("board-to-board", "boardToBoard"),
    ("backplane", "boardToBoard"),
    ("coaxial", "rf"),
    ("(rf)", "rf"),
    ("u.fl", "rf"),
    ("circular", "circular"),
    ("usb", "dataInterface"),
    ("hdmi", "dataInterface"),
    ("dvi", "dataInterface"),
    ("displayport", "dataInterface"),
    ("modular connector", "dataInterface"),
    ("d-sub", "dataInterface"),
    ("d-subminiature", "dataInterface"),
    ("ethernet", "dataInterface"),
    ("rj45", "dataInterface"),
    ("barrel", "power"),
    ("banana", "power"),
    ("bus bar", "power"),
    ("busbar", "power"),
    ("solar", "power"),
    ("header", "pinHeaderSocket"),
    ("socket", "pinHeaderSocket"),
    ("receptacle", "pinHeaderSocket"),
    ("male pin", "pinHeaderSocket"),
    ("housing", "wireToBoard"),
    ("crimp", "wireToBoard"),
    ("wire to board", "wireToBoard"),
    ("wire-to-board", "wireToBoard"),
]


def _dk_family_to_conas(*texts: str) -> str:
    blob = " ".join(t for t in texts if t).lower()
    for keyword, family in _DK_FAMILY_RULES:
        if keyword in blob:
            return family
    return "wireToBoard"


def _convert_connector(product: dict[str, Any]) -> dict[str, Any] | None:
    """Generic (manufacturer-agnostic) Digi-Key -> CONAS connector converter.

    Unlike the Würth catalogue fetcher (which drives family/polarity off known WE
    series codes), this works for ANY manufacturer: family comes from the Digi-Key
    Family string + description, and every electrical/mechanical field is emitted
    ONLY when Digi-Key actually provides it — nothing is invented, and the part is
    never dropped merely because a rating is absent. The result is schema-valid
    (validated by the caller) with the four required objects always present.
    """
    mod = _load_script_module("fetch_we_connectors")
    params = mod._params_dict(product.get("Parameters") or [])
    mpn = product.get("ManufacturerPartNumber", "") or ""
    mfr = _canonical_mfr(product)
    desc = product.get("Description") or {}
    desc_text = desc.get("DetailedDescription") or desc.get("ProductDescription") or ""
    dk_fam = (product.get("Family") or {}).get("Value") or ""
    url = product.get("DatasheetUrl") or product.get("PrimaryDatasheet") or ""
    if not mpn or not mfr:
        return None

    family = _dk_family_to_conas(dk_fam, desc_text)

    # matingPolarity (enum: male/female/hermaphroditic/genderless). From the DK
    # Gender param, or inferred from the connector Type/Description text (e.g.
    # "Header, Male Pins"); genderless when DK states nothing.
    polarity = (
        mod._gender_to_polarity(params.get("Gender"))
        or mod._gender_to_polarity(params.get("Gender / Termination Style"))
        or mod._gender_to_polarity(params.get("Type"))
        or mod._gender_to_polarity(desc_text)
        or "genderless"
    )

    # Electrical — include each rating only if DK lists it (no fabrication). DK
    # names current/voltage differently per family and per rating standard
    # (IEC/UL/DC); cover the common aliases.
    electrical: dict[str, Any] = {}
    i_rated = (
        mod._float_value(params.get("Current Rating (Amps)", ""))
        or mod._float_value(params.get("Current - DC (Max)", ""))
        or mod._float_value(params.get("Current - UL", ""))
        or mod._float_value(params.get("Current - IEC", ""))
        or mod._float_value(params.get("Current", ""))
        or mod._float_value(params.get("Current Rating", ""))
        or mod._float_value(params.get("Current - Contact", ""))
        or mod._float_value(params.get("Current - Max", ""))
    )
    if i_rated is not None:
        electrical["ratedCurrentPerContact"] = i_rated
    v_rated = (
        mod._float_value(params.get("Voltage Rating", ""))
        or mod._float_value(params.get("Voltage - Rated", ""))
        or mod._float_value(params.get("Voltage - UL", ""))
        or mod._float_value(params.get("Voltage - IEC", ""))
        or mod._float_value(params.get("Voltage", ""))
        or mod._float_value(params.get("Voltage - DC", ""))
    )
    if v_rated is not None:
        electrical["ratedVoltage"] = v_rated

    # Mechanical — positions + pitch only when present.
    mechanical: dict[str, Any] = {}
    pitch_mm = (
        mod._float_value(params.get("Pitch - Mating"), unit_hint="m")
        or mod._float_value(params.get("Pitch"), unit_hint="m")
        or mod._float_value(params.get("Pitch - Termination to Termination"), unit_hint="m")
    )
    if pitch_mm is not None:
        mechanical["pitch"] = pitch_mm
    positions = mod._int_value(
        params.get("Number of Positions")
        or params.get("Number of Contacts")
        or params.get("Number of Rows x Number of Contacts")
        or ""
    )
    if positions is not None:
        mechanical["positions"] = positions

    # Environmental temperature, only if DK states a range.
    env: dict[str, Any] = {}
    temp_str = params.get("Operating Temperature") or ""
    if temp_str:
        import re

        m_temp = re.search(r"([-\d.]+)\s*°?C\s*~\s*([-\d.]+)\s*°?C", temp_str)
        if m_temp:
            env["operatingTemperature"] = {
                "minimum": float(m_temp.group(1)),
                "maximum": float(m_temp.group(2)),
            }

    part: dict[str, Any] = {"partNumber": mpn, "matingPolarity": polarity}
    if desc_text:
        part["description"] = desc_text[:200]

    datasheet_info: dict[str, Any] = {
        "part": part,
        "electrical": electrical,
        "mechanical": mechanical,
        "familyDetails": {"family": family},
    }
    if env:
        datasheet_info["environmental"] = env

    manufacturer_info: dict[str, Any] = {
        "name": mfr,
        "reference": mpn,
        "datasheetInfo": datasheet_info,
    }
    if url:
        manufacturer_info["datasheetUrl"] = url

    return {"connector": {"manufacturerInfo": manufacturer_info}}


def _convert_timebase(product: dict[str, Any]) -> dict[str, Any] | None:
    mod = _load_script_module("fetch_timebases")
    record, _reason = mod._convert(product, mod._canonicalize_mfr(_canonical_mfr(product), ""))
    return record


def _converter_for(category: str) -> Callable[[dict[str, Any]], dict[str, Any] | None] | None:
    """Return a (product -> envelope|None) converter for a crossref category."""
    from heaviside.librarian.fetcher import convert as C

    simple: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
        "capacitor": C.convert_digikey_to_tas_capacitor,
        "resistor": C.convert_digikey_to_tas_resistor,
        "diode": C.convert_digikey_to_tas_diode,
        "mosfet": C.convert_digikey_to_tas_mosfet,
        "igbt": C.convert_digikey_to_tas_igbt,
        "magnetic": C.convert_digikey_to_tas_magnetic,
    }
    if category in simple:
        return simple[category]
    if category == "connector":
        return _convert_connector
    if category == "timeBase":
        return _convert_timebase
    # analog and anything else: no converter yet.
    return None


def _mpn_matches(product: dict[str, Any], mpn: str) -> bool:
    got = str(product.get("ManufacturerPartNumber") or "").strip().lower()
    return got == mpn.strip().lower()


def fetch_dk_product(dk_client: Any, mpn: str) -> dict[str, Any] | None:
    """Exact-MPN Digi-Key lookup: prefer get_product, fall back to a keyword
    search matching ManufacturerPartNumber exactly. Returns the product dict (the
    shape the converters + classifier expect) or None."""
    try:
        detail = dk_client.get_product(mpn)
        if isinstance(detail, dict) and _mpn_matches(detail, mpn):
            return detail
    except Exception as exc:
        logger.debug("get_product(%s) failed, will keyword-search: %s", mpn, exc)
    try:
        res = dk_client.search(mpn, limit=10)
    except Exception as exc:
        logger.debug("Digi-Key search(%s) failed: %s", mpn, exc)
        return None
    for p in res.get("Products", []) if isinstance(res, dict) else []:
        if _mpn_matches(p, mpn):
            return p
    return None


def fetch_original_envelope(
    dk_client: Any, mpn: str, category: str, *, product: dict[str, Any] | None = None
) -> tuple[dict[str, Any] | None, str]:
    """Fetch + convert + validate an original part from Digi-Key by exact MPN.

    ``category`` may be empty/None — the category is then inferred from the
    Digi-Key product's taxonomy (so a bare pasted MPN that isn't in the internal
    catalogue and carries no description keyword can still be sourced). Pass an
    already-fetched ``product`` to avoid a second Digi-Key round-trip.

    Returns ``(envelope, db_category)`` on success, or ``(None, reason)``. The
    envelope is guaranteed schema-valid (validated here); the caller persists it.
    """
    # When the category is known up front and we can't convert it, short-circuit
    # before spending a Digi-Key call.
    if category and _converter_for(category) is None:
        return None, f"no Digi-Key converter for category {category!r}"

    if product is None:
        product = fetch_dk_product(dk_client, mpn)
    if product is None:
        return None, "not found on Digi-Key"

    if not category:
        category = classify_dk_product(product) or ""
        if not category:
            fam = (product.get("Family") or {}).get("Value") or "?"
            return None, f"could not classify Digi-Key product (family {fam!r})"

    converter = _converter_for(category)
    if converter is None:
        return None, f"no Digi-Key converter for category {category!r}"

    try:
        result = converter(product)
    except Exception as exc:
        return None, f"conversion failed: {exc}"
    # Some converters return (envelope, reason); normalise.
    if isinstance(result, tuple):
        result = result[0]
    if not isinstance(result, dict):
        return None, "conversion produced no envelope"

    db_cat = _CATEGORY_TO_DB.get(category, category)
    # Schema-validate before we trust it — a half-parsed original is worse than
    # no original (it would enable a bad substitution on fabricated specs).
    try:
        from heaviside.librarian.tas import ValidationError, validate_component

        validate_component(db_cat, result)
    except ValidationError as exc:
        return None, f"fetched original failed {db_cat} schema validation: {str(exc)[:160]}"
    except Exception as exc:  # validator unavailable etc. — don't trust unvalidated data
        return None, f"could not validate fetched original: {exc}"

    return result, db_cat


__all__ = ["fetch_original_envelope"]
