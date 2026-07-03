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


def _convert_connector(product: dict[str, Any]) -> dict[str, Any] | None:
    mod = _load_script_module("fetch_we_connectors")
    mpn = product.get("ManufacturerPartNumber", "")
    desc = product.get("Description") or {}
    desc_text = desc.get("DetailedDescription") or desc.get("ProductDescription") or ""
    dk_fam = (product.get("Family") or {}).get("Value") or ""
    cfg = mod._series_config(mpn, desc_text, dk_fam) or {
        "series": "",
        "family": "wireToBoard",
        "polarity": None,
    }
    return mod._convert(product, cfg)


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


def fetch_original_envelope(
    dk_client: Any, mpn: str, category: str
) -> tuple[dict[str, Any] | None, str]:
    """Fetch + convert + validate an original part from Digi-Key by exact MPN.

    Returns ``(envelope, db_category)`` on success, or ``(None, reason)``. The
    envelope is guaranteed schema-valid (validated here); the caller persists it.
    """
    converter = _converter_for(category)
    if converter is None:
        return None, f"no Digi-Key converter for category {category!r}"

    # Exact-MPN lookup: prefer get_product; fall back to a keyword search and
    # match the ManufacturerPartNumber exactly (search results are the shape the
    # converters expect).
    product: dict[str, Any] | None = None
    try:
        detail = dk_client.get_product(mpn)
        if isinstance(detail, dict) and _mpn_matches(detail, mpn):
            product = detail
    except Exception as exc:
        logger.debug("get_product(%s) failed, will keyword-search: %s", mpn, exc)
    if product is None:
        try:
            res = dk_client.search(mpn, limit=10)
        except Exception as exc:
            return None, f"Digi-Key search failed: {exc}"
        for p in res.get("Products", []) if isinstance(res, dict) else []:
            if _mpn_matches(p, mpn):
                product = p
                break
    if product is None:
        return None, "not found on Digi-Key"

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
