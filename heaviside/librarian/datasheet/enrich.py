"""Category-aware datasheet enrichment.

The pipeline calls :func:`enrich_from_datasheet` when a cross-reference
*original* part is NOT in the internal DB: instead of marking every
critical spec "unverified" and matching on nominal value alone (the bug
that shipped an X5R/+85 °C part as a "recommended" replacement for an
X7R/+125 °C part, and an under-rated inductor as a substitute), it fetches
the original's real datasheet and parses the specs that actually govern
substitution.

Output shape
------------

The returned dict is keyed to match the keys
:func:`heaviside.pipeline.crossref_pipeline._summarize_candidate` emits for
the same category, so the pipeline can splice a datasheet-derived original
straight into the same :mod:`heaviside.pipeline.param_check` comparison it
runs for in-DB parts. Every value is in **SI base units** (temperatures in
°C, per the ``temp_max_C`` convention). A field that is not literally in the
datasheet is **absent** from the dict — never guessed, never defaulted.

Two entry points
----------------

* :func:`enrich_from_datasheet` — URL → dict (fetches + parses a PDF).
* :func:`enrich_from_text` — text (+ optional pre-extracted tables) → dict.
  Pure and side-effect-free, so parsers are unit-tested against real
  datasheet text snippets with no PDF or network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from heaviside.librarian.datasheet.base import DatasheetParseError
from heaviside.librarian.datasheet.cache import PdfCache
from heaviside.librarian.datasheet.extract import Table, extract_params, extract_tables
from heaviside.librarian.datasheet.magnetics_we import parse_we_magnetic_text
from heaviside.librarian.datasheet.text_specs import (
    parse_aec_qualification,
    parse_dielectric_code,
    parse_operating_temp_max_C,
)

__all__ = [
    "enrich_from_datasheet",
    "enrich_from_text",
    "normalize_category",
]


# Accept both the plural table-extractor categories and the singular
# categories the pipeline / param_check use, mapping each to a canonical
# singular tag that drives the category-specific logic below.
_CATEGORY_ALIASES: dict[str, str] = {
    "mosfet": "mosfet",
    "mosfets": "mosfet",
    "diode": "diode",
    "diodes": "diode",
    "capacitor": "capacitor",
    "capacitors": "capacitor",
    "resistor": "resistor",
    "resistors": "resistor",
    "igbt": "igbt",
    "igbts": "igbt",
    "magnetic": "magnetic",
    "magnetics": "magnetic",
    "inductor": "magnetic",
    "inductors": "magnetic",
}

# Canonical singular → the plural key the table extractor's
# CATEGORY_PATTERNS is registered under.
_TABLE_CATEGORY: dict[str, str] = {
    "mosfet": "mosfets",
    "diode": "diodes",
    "capacitor": "capacitors",
    "resistor": "resistors",
    "igbt": "igbts",
}


# Electrical-table schema key → _summarize_candidate summary key, per
# category. Only keys the param-check actually consumes (plus the headline
# ratings a reviewer reads) are mapped; unmapped schema keys are dropped so
# the enrichment result stays aligned with the in-DB summary shape.
_TABLE_KEY_MAP: dict[str, dict[str, str]] = {
    "mosfet": {
        "drainSourceVoltage": "vds",
        "onResistance": "rds_on",
        "continuousDrainCurrent": "id",
        "totalGateCharge": "qg",
        "outputCapacitance": "coss",
        "gateThresholdVoltage": "vgs_threshold_max",
        "reverseRecoveryCharge": "qrr",
        "reverseRecoveryTime": "trr",
    },
    "diode": {
        "reverseVoltage": "vrrm",
        "forwardVoltage": "vf",
        "forwardCurrent": "if_avg",
        "reverseRecoveryCharge": "qrr",
        "reverseRecoveryTime": "trr",
    },
    "capacitor": {
        "capacitance": "capacitance",
        "ratedVoltage": "voltage",
        "esr": "esr",
        "rippleCurrent": "ripple_current",
    },
    "resistor": {
        "resistance": "resistance",
        "tolerance": "tolerance",
        "powerRating": "power_rating",
        "temperatureCoefficient": "tcr",
    },
    "igbt": {
        "collectorEmitterVoltage": "vces",
        "collectorEmitterSaturation": "vce_sat",
        "continuousCollectorCurrent": "ic",
        "gateEmitterThreshold": "vge_threshold",
    },
}


def normalize_category(category: str) -> str:
    """Return the canonical singular category tag for ``category``.

    Raises
    ------
    ValueError
        ``category`` is not a recognised component category.
    """
    key = (category or "").strip().lower()
    norm = _CATEGORY_ALIASES.get(key)
    if norm is None:
        raise ValueError(
            f"unknown category {category!r}; expected one of {sorted(set(_CATEGORY_ALIASES))}"
        )
    return norm


def _map_magnetic(we: dict[str, float]) -> dict[str, Any]:
    """Map WE-magnetics text-parser output onto summary keys.

    Saturation current uses the most conservative available drop-% (10 %
    before 20 % before 30 %), matching the saturation-gate policy: the
    smaller |ΔL/L| threshold yields the lower, safer Isat.
    """
    out: dict[str, Any] = {}
    if "inductance" in we:
        out["inductance"] = we["inductance"]
    for pct in ("isat_10pct", "isat_20pct", "isat_30pct"):
        if pct in we:
            out["saturation_current"] = we[pct]
            out["saturation_current_drop_pct"] = int(pct.split("_")[1].rstrip("pct"))
            break
    if "irp_40k" in we:
        out["rated_current"] = we["irp_40k"]
    # DCR: prefer the typical (what the in-DB summary reports) then max.
    if "rdc_typ" in we:
        out["dcr"] = we["rdc_typ"]
    elif "rdc_max" in we:
        out["dcr"] = we["rdc_max"]
    return out


def enrich_from_text(
    mpn: str,
    category: str,
    text: str,
    *,
    tables: list[Table] | None = None,
) -> dict[str, Any]:
    """Parse datasheet ``text`` (and optional pre-extracted ``tables``) into
    a summary-keyed spec dict for ``mpn`` in ``category``.

    Pure function — no IO. ``tables`` is the raw output of
    :func:`heaviside.librarian.datasheet.extract.extract_tables` (list of
    rows-of-cells); when omitted, only text-based fields are produced.

    Every field is optional: absent in the datasheet → absent in the result.
    """
    norm = normalize_category(category)
    out: dict[str, Any] = {"mpn": mpn}

    if norm == "magnetic":
        # WE magnetics render their electrical block as vector text, not a
        # table pdfplumber can grid — parse the text form.
        out.update(_map_magnetic(parse_we_magnetic_text(text)))
    else:
        table_cat = _TABLE_CATEGORY[norm]
        key_map = _TABLE_KEY_MAP[norm]
        if tables:
            try:
                params = extract_params(tables, category=table_cat, require_section=True)
            except DatasheetParseError:
                # No Electrical-Characteristics section detected in the
                # supplied tables — text-based fields below may still apply.
                params = {}
            for schema_key, value in params.items():
                summary_key = key_map.get(schema_key)
                if summary_key is not None:
                    out[summary_key] = value

    # Text-based fields — apply to every category (temperature ceiling is
    # universal; dielectric only ever matches on a capacitor datasheet).
    temp_max = parse_operating_temp_max_C(text)
    if temp_max is not None:
        out["temp_max_C"] = temp_max

    aec = parse_aec_qualification(text)
    if aec is not None:
        out["aec_qualification"] = aec

    if norm == "capacitor":
        code = parse_dielectric_code(text)
        if code is not None:
            out["dielectric_code"] = code

    return out


def _pdf_full_text(pdf_path: Path | str) -> str:
    """Return the concatenated text of every page in ``pdf_path``.

    Raises
    ------
    MissingDependencyError
        ``pdfplumber`` is not installed.
    """
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - dependency is declared
        from heaviside.librarian.datasheet.base import MissingDependencyError

        raise MissingDependencyError("pdfplumber") from exc

    with pdfplumber.open(str(pdf_path)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def enrich_from_datasheet(
    mpn: str,
    category: str,
    datasheet_url: str,
    *,
    cache: PdfCache | None = None,
    force_download: bool = False,
) -> dict[str, Any]:
    """Fetch ``datasheet_url`` and return a summary-keyed spec dict for the
    out-of-DB original part ``mpn`` in ``category``.

    This is the entry point the cross-reference pipeline calls to get an
    original's REAL specs when the MPN does not resolve in the internal DB —
    so critical parameters (max operating temperature, dielectric class,
    saturation current, Rds(on), …) are compared against the substitute
    instead of being blanket-marked "unverified".

    Parameters
    ----------
    mpn : str
        The original's manufacturer part number (carried through to the
        result; not used to fabricate anything).
    category : str
        Component category — singular or plural (e.g. ``"capacitor"`` or
        ``"capacitors"``, ``"magnetic"``/``"inductor"``).
    datasheet_url : str
        URL of the original's datasheet PDF.
    cache : PdfCache, optional
        PDF cache to use. Defaults to a fresh :class:`PdfCache` (the shared
        content-addressed cache directory).
    force_download : bool
        Re-download even if a cache entry exists.

    Returns
    -------
    dict
        Summary-keyed spec dict in SI units. Absent fields are omitted.

    Raises
    ------
    DatasheetDownloadError
        The PDF could not be fetched (transport/HTTP/empty body).
    MissingDependencyError
        ``pdfplumber`` is not installed.
    ValueError
        ``category`` is not recognised.
    """
    norm = normalize_category(category)
    pdf_cache = cache if cache is not None else PdfCache()
    pdf_path = pdf_cache.fetch(datasheet_url, force=force_download)

    text = _pdf_full_text(pdf_path)

    tables: list[Table] | None = None
    if norm != "magnetic":
        # Zero-table / image-only PDFs raise DatasheetParseError; that is not
        # fatal to enrichment (text-based fields may still parse), so fall
        # through with no tables rather than aborting the whole enrichment.
        try:
            tables = extract_tables(pdf_path)
        except DatasheetParseError:
            tables = None

    return enrich_from_text(mpn, category, text, tables=tables)
