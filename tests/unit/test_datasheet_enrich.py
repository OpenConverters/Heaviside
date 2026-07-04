"""Tests for category-aware datasheet enrichment (:mod:`enrich`).

Exercises :func:`enrich_from_text` (the pure, PDF-free core of
:func:`enrich_from_datasheet`) with pdfplumber-shaped tables + real
datasheet text, so the whole mapping/merging logic is covered without
network or a PDF. The motivating invariants:

* the out-of-DB original's REAL specs come back keyed like the in-DB
  ``_summarize_candidate`` summary (``rds_on``, ``voltage``, ``temp_max_C``,
  ``dielectric_code``, ``saturation_current`` …);
* max operating temperature and dielectric class — the two fields behind
  the "worse-temp X5R shipped as recommended" bug — are populated;
* absent fields are omitted, never guessed.
"""

from __future__ import annotations

import pytest

from heaviside.librarian.datasheet.enrich import (
    enrich_from_text,
    normalize_category,
)

# --- Category normalisation -------------------------------------------------


def test_normalize_category_accepts_singular_and_plural():
    assert normalize_category("capacitor") == "capacitor"
    assert normalize_category("capacitors") == "capacitor"
    assert normalize_category("MOSFETs") == "mosfet"
    assert normalize_category("inductor") == "magnetic"
    assert normalize_category("magnetics") == "magnetic"


def test_normalize_category_rejects_unknown():
    with pytest.raises(ValueError):
        normalize_category("thyristor")


# --- Capacitor: table params + dielectric + max temp (the headline case) ----

# TDK C3216X7R1H105K160AB — 1 µF 50 V X7R. The dielectric + operating-temp
# ceiling live in prose; ESR/ripple/voltage live in the electrical table.
_TDK_X7R_TEXT = """
Series/Type C3216X7R1H105K160AB
Temperature characteristic [EIA] X7R
Operating temperature range -55 to 125 °C
AEC-Q200 qualified
"""

_TDK_X7R_TABLE = [
    [
        ["Electrical Characteristics"],
        ["Parameter", "Symbol", "Value"],
        ["Nominal Capacitance", "CAP", "1 µF"],
        ["Rated Voltage", "UR", "50 V"],
        ["Equivalent Series Resistance", "ESR", "0.01 Ω"],
    ]
]


def test_capacitor_merges_table_and_text_fields():
    out = enrich_from_text(
        "C3216X7R1H105K160AB", "capacitor", _TDK_X7R_TEXT, tables=_TDK_X7R_TABLE
    )
    assert out["mpn"] == "C3216X7R1H105K160AB"
    # text-derived (the bug fix):
    assert out["dielectric_code"] == "X7R"
    assert out["temp_max_C"] == 125.0
    assert out["aec_qualification"] == "AEC-Q200"
    # table-derived, mapped to summary keys + SI:
    assert out["capacitance"] == pytest.approx(1e-6)
    assert out["voltage"] == pytest.approx(50.0)
    assert out["esr"] == pytest.approx(0.01)


def test_capacitor_x5r_lower_temp_ceiling_is_captured():
    # This is the exact substitution hazard: an X5R original tops out at
    # +85 °C. Enrichment must report 85, not blanket-"unverified".
    text = (
        "Temperature Characteristics R6 (X5R)\n"
        "Operating Temperature Range -55 to 85 °C\n"
    )
    out = enrich_from_text("GRM31CR61E106KA12", "capacitor", text)
    assert out["dielectric_code"] == "X5R"
    assert out["temp_max_C"] == 85.0


def test_absent_fields_are_omitted_never_guessed():
    out = enrich_from_text("UNKNOWNCAP", "capacitor", "Aluminium electrolytic 470 µF\n")
    assert "dielectric_code" not in out
    assert "temp_max_C" not in out
    assert "esr" not in out
    assert out == {"mpn": "UNKNOWNCAP"}


# --- MOSFET: table schema keys → summary keys -------------------------------

_MOSFET_TABLE = [
    [
        ["Static Characteristics"],
        ["Parameter", "Symbol", "Min", "Typ", "Max", "Unit"],
        ["Drain-Source Voltage", "VDS", "", "", "55", "V"],
        ["Static Drain-Source On-Resistance", "RDS(ON)", "", "", "0.022", "Ω"],
        # Qg carries its unit inline in the value cell (the sub-prefixed
        # "nC" would be lost if it sat in a separate Unit column — a known
        # limit of the table extractor, not of the mapping under test).
        ["Total Gate Charge", "Qg", "", "63 nC", "", ""],
        ["Gate Threshold Voltage", "VGS(th)", "2.0", "", "4.0", "V"],
    ]
]
_MOSFET_TEXT = "Operating Junction and Storage Temperature Range TJ -55 to +175 °C\n"


def test_mosfet_maps_schema_keys_to_summary_keys():
    out = enrich_from_text("IRFZ44N", "mosfet", _MOSFET_TEXT, tables=_MOSFET_TABLE)
    assert out["vds"] == pytest.approx(55.0)
    assert out["rds_on"] == pytest.approx(0.022)
    assert out["qg"] == pytest.approx(63e-9)
    assert out["vgs_threshold_max"] == pytest.approx(2.0)  # first numeric in row
    assert out["temp_max_C"] == 175.0


# --- Magnetic: WE text parser → summary keys --------------------------------

_WE_MAGNETIC_TEXT = """
Inductance L 100 kHz/ 10 mA 1.5 µH ±20%
Rated Current I RP,40K ΔT = 40K 8.6 A max.
Saturation Current @ 10% I SAT, 10% |ΔL/L| < 10 % 4.8 A typ.
Saturation Current @ 30% I SAT,30% |ΔL/L| < 30 % 10.2 A typ.
DC Resistance R DC @ 20 °C 16 mΩ typ.
DC Resistance R DC @ 20 °C 19 mΩ max.
"""


def test_magnetic_uses_conservative_isat_and_summary_keys():
    out = enrich_from_text("74438356015", "magnetic", _WE_MAGNETIC_TEXT)
    assert out["inductance"] == pytest.approx(1.5e-6)
    # Conservative: 10 %-drop Isat (4.8 A), NOT the 30 %-drop 10.2 A.
    assert out["saturation_current"] == pytest.approx(4.8)
    assert out["saturation_current_drop_pct"] == 10
    assert out["rated_current"] == pytest.approx(8.6)
    assert out["dcr"] == pytest.approx(0.016)  # typical preferred


def test_magnetic_absent_isat_omitted():
    out = enrich_from_text("Lonly", "magnetic", "Inductance L 4.7 µH ±20%\n")
    assert out["inductance"] == pytest.approx(4.7e-6)
    assert "saturation_current" not in out
    assert "dcr" not in out
