"""Tests for the text-based datasheet parsers (max operating temperature,
dielectric code, AEC-Q qualification).

Snippets reproduce the pdfplumber-extracted phrasing of REAL vendor
datasheets (MPN + source noted per case) so no PDF is needed at test time.
The load-bearing invariants: the temperature parser returns the *ceiling*
of an operating range (not the −55 floor) and never a storage-only value;
absent fields come back ``None`` (never a default).
"""

from __future__ import annotations

from heaviside.librarian.datasheet.text_specs import (
    parse_aec_qualification,
    parse_dielectric_code,
    parse_operating_temp_max_C,
)

# --- Real datasheet text fragments -----------------------------------------

# Murata GRM31CR61E106KA12 — 10 µF 25 V X5R 1206
# https://www.murata.com/en-us/products/productdetail?partno=GRM31CR61E106KA12%23
_MURATA_X5R_106 = """
Part Number GRM31CR61E106KA12
Capacitance 10 µF
Rated Voltage 25 Vdc
Temperature Characteristics R6 (X5R)
Operating Temperature Range -55 to 85 °C
Capacitance Tolerance ±10%
"""

# TDK C3216X7R1H105K160AB — 1 µF 50 V X7R 1206
# https://product.tdk.com/en/search/capacitor/ceramic/mlcc/info?part_no=C3216X7R1H105K160AB
_TDK_X7R_105 = """
Series/Type C3216X7R1H105K160AB
Rated voltage 50 V
Capacitance 1 µF
Temperature characteristic [EIA] X7R
Operating temperature range -55 to 125 °C
"""

# Murata GRM1885C1H101JA01 — 100 pF 50 V C0G 0603
_MURATA_C0G_101 = """
Part Number GRM1885C1H101JA01
Temperature Characteristics C0G
Operating Temperature Range -55 to 125°C
Capacitance 100 pF
"""

# Infineon IRFZ44N HEXFET Power MOSFET — 55 V N-channel
# Absolute Maximum Ratings (the operating/storage line shares a row).
_IRFZ44N = """
Absolute Maximum Ratings
VDS Drain-to-Source Voltage 55 V
ID Continuous Drain Current 49 A
TJ, TSTG Operating Junction and Storage Temperature Range -55 to + 175 °C
"""

# Vishay CRCW080510K0FKEA thick film chip resistor, AEC-Q200
# https://www.vishay.com/en/product/28773/
_VISHAY_CRCW = """
CRCW0805 Thick Film, Rectangular Chip Resistors
AEC-Q200 qualified
Operating Temperature Range -55 °C to +155 °C
Temperature Coefficient ±100 ppm/°C
"""

# ON Semiconductor MURS340 surface-mount ultrafast rectifier
_MURS340 = """
MURS340 SWITCHMODE Power Rectifier
Operating Junction Temperature Range TJ -65 to +175 °C
Storage Temperature Range Tstg -65 to +200 °C
"""


# --- Max operating temperature ----------------------------------------------


def test_temp_max_takes_range_ceiling_not_floor():
    assert parse_operating_temp_max_C(_MURATA_X5R_106) == 85.0
    assert parse_operating_temp_max_C(_TDK_X7R_105) == 125.0
    assert parse_operating_temp_max_C(_MURATA_C0G_101) == 125.0


def test_temp_max_junction_from_combined_operating_storage_line():
    # +175 carries the °C; −55 (en/ascii dash) does not — ceiling is 175.
    assert parse_operating_temp_max_C(_IRFZ44N) == 175.0


def test_temp_max_prefers_operating_over_storage():
    # Operating Tj = 175; storage = 200. Must report the OPERATING ceiling.
    assert parse_operating_temp_max_C(_MURS340) == 175.0


def test_temp_max_ignores_tcr_ppm_line():
    assert parse_operating_temp_max_C(_VISHAY_CRCW) == 155.0


def test_temp_max_absent_returns_none():
    assert parse_operating_temp_max_C("Capacitance 10 µF\nRated Voltage 25 V\n") is None


# --- Dielectric code --------------------------------------------------------


def test_dielectric_code_from_context_line():
    assert parse_dielectric_code(_MURATA_X5R_106) == "X5R"
    assert parse_dielectric_code(_TDK_X7R_105) == "X7R"
    assert parse_dielectric_code(_MURATA_C0G_101) == "C0G"


def test_dielectric_code_normalises_letter_o_spelling():
    assert parse_dielectric_code("Temperature Characteristic: COG\n") == "C0G"
    assert parse_dielectric_code("Dielectric: NPO\n") == "NP0"


def test_dielectric_code_absent_returns_none():
    assert parse_dielectric_code("Aluminium electrolytic 470 µF 35 V\n") is None


def test_dielectric_code_not_matched_inside_a_word():
    # "MAXX7READY" must not yield X7R.
    assert parse_dielectric_code("Marketing code MAXX7READY only\n") is None


# --- AEC-Q qualification ----------------------------------------------------


def test_aec_q200_from_resistor():
    assert parse_aec_qualification(_VISHAY_CRCW) == "AEC-Q200"


def test_aec_q_spelling_variants():
    assert parse_aec_qualification("This device is AEC-Q101 qualified.") == "AEC-Q101"
    assert parse_aec_qualification("AEC Q 200 compliant") == "AEC-Q200"
    assert parse_aec_qualification("aec-q100 grade 1") == "AEC-Q100"


def test_aec_q_absent_returns_none():
    assert parse_aec_qualification("Standard commercial grade part.\n") is None
