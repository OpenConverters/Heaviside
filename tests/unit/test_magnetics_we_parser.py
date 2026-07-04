"""Tests for the Würth magnetics datasheet text parser.

Uses the exact pdfplumber-extracted lines from the WE-MAPI 74438356015 datasheet
(rev 003.001) so no PDF is needed at test time. The critical assertion is that
the saturation-current drop-% is read from the DEFINITION, not from the value —
the 30%-drop value 10.2 also contains "10".
"""

from __future__ import annotations

from heaviside.librarian.datasheet.magnetics_we import parse_we_magnetic_text

_WE_MAPI_74438356015 = """
Marking 1R5 (Inductance Code)
Solder Resist Inductance L 100 kHz/ 10 mA 1.5 µH ±20%
Performance Rated Current 1) I RP,40K ΔT = 40K 8.6 A max.
Saturation Current @ 10% I SAT, 10% |ΔL/L| < 10 % 4.8 A typ.
Saturation Current @ 30% I SAT,30% |ΔL/L| < 30 % 10.2 A typ.
DC Resistance R DC @ 20 °C 16 mΩ typ.
DC Resistance R DC @ 20 °C 19 mΩ max.
"""


def test_parses_all_fields_in_si():
    r = parse_we_magnetic_text(_WE_MAPI_74438356015)
    assert r["inductance"] == 1.5e-6
    assert r["tolerance"] == 0.20
    assert r["irp_40k"] == 8.6
    assert r["rdc_typ"] == 0.016
    assert r["rdc_max"] == 0.019


def test_isat_drop_from_definition_not_value():
    # The 30% line's value 10.2 contains "10" — must NOT land in isat_10pct.
    r = parse_we_magnetic_text(_WE_MAPI_74438356015)
    assert r["isat_10pct"] == 4.8
    assert r["isat_30pct"] == 10.2
    assert "isat_20pct" not in r


def test_unit_conversion_nh_and_ohm():
    txt = (
        "Inductance L 100 kHz 330 nH ±20%\n"
        "Saturation Current @ 20% I SAT |ΔL/L| < 20 % 3.5 A typ.\n"
        "DC Resistance R DC @ 20 °C 8.5 mΩ max.\n"
    )
    r = parse_we_magnetic_text(txt)
    assert r["inductance"] == 330e-9
    assert r["isat_20pct"] == 3.5
    assert r["rdc_max"] == 0.0085


def test_missing_fields_absent_not_guessed():
    r = parse_we_magnetic_text("Inductance L 4.7 µH ±20%\n")
    assert r["inductance"] == 4.7e-6
    assert "isat_10pct" not in r and "irp_40k" not in r and "rdc_max" not in r
