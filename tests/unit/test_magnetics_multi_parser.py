"""Tests for the multi-vendor (non-Würth) power-inductor datasheet parser.

The text snippets below are the REAL pdfplumber output of the current datasheets
(Coilcraft XGL6060 Doc 1621, Vishay IHLP-2020BZ-01 Doc 34253, MPS MPL-AL6050-1R5
Rev 1.1), so no PDF or network is needed at test time.

The two FAE-critical assertions per vendor:
  * the CONSERVATIVE (lowest-drop) saturation current is the one the gate sees;
  * the STANDARD rated current is extracted, never a best-case/performance figure.
"""

from __future__ import annotations

import pytest

from heaviside.librarian.datasheet.enrich import enrich_from_text
from heaviside.librarian.datasheet.magnetics_multi import (
    detect_magnetic_vendor,
    parse_magnetic_text,
)

# ---------------------------------------------------------------------------
# Coilcraft XGL6060 — per-series selection table (real Doc 1621-2 rows)
# ---------------------------------------------------------------------------

_COILCRAFT_XGL6060 = """
Document 1621-2
Shielded Power Inductors – XGL6060
Inductance2 DCR (mOhms)3 SRF typ4 Isat (A)5 Irms (A)6
Part number1 ±20% (µH) typ max (MHz) 10% drop 20% drop 30% drop 20°C rise 40°C rise
XGL6060-471ME_ 0.47 1.5 1.8 75 13.8 22.0 29.5 26.0 35.5
XGL6060-682ME_ 6.8 12.7 14.0 16 4.5 6.8 8.9 8.5 11.5
Halogen XGL6060-822ME_ 8.2 15.2 16.8 14 4.1 6.2 8.1 8.0 11.0
XGL6060-103ME_ 10 18.5 20.4 14 3.6 5.5 7.3 7.3 10.0
US +1-847-639-6400 sales@coilcraft.com Document 1621-2 Revised 02/19/26
"""


def test_coilcraft_vendor_detected():
    assert detect_magnetic_vendor(_COILCRAFT_XGL6060, mpn="XGL6060-822") == "coilcraft"
    # MPN-prefix fallback when the text is silent on the vendor.
    assert detect_magnetic_vendor("no vendor here", mpn="XGL6060-822") == "coilcraft"


def test_coilcraft_row_selected_by_mpn_conservative_isat_and_standard_irms():
    r = parse_magnetic_text(_COILCRAFT_XGL6060, mpn="XGL6060-822")
    assert r["inductance"] == pytest.approx(8.2e-6)
    assert r["rdc_typ"] == pytest.approx(0.0152)
    assert r["rdc_max"] == pytest.approx(0.0168)
    # Conservative Isat = the 10 %-drop figure (4.1 A), NOT 20 % (6.2) or 30 % (8.1).
    assert r["isat_10pct"] == pytest.approx(4.1)
    assert r["saturation_current"] == pytest.approx(4.1)
    assert r["saturation_current_drop_pct"] == 10
    # Rated current = the 40 °C-rise Irms (the WE I R,40K equivalent), 11.0 A —
    # both rise figures are captured so the choice is auditable.
    assert r["irms_20c_rise"] == pytest.approx(8.0)
    assert r["irms_40c_rise"] == pytest.approx(11.0)
    assert r["rated_current"] == pytest.approx(11.0)


def test_coilcraft_selects_the_correct_row_not_a_neighbour():
    # The 0.47 µH row must not bleed into the 8.2 µH part's result.
    r = parse_magnetic_text(_COILCRAFT_XGL6060, mpn="XGL6060-471")
    assert r["inductance"] == pytest.approx(0.47e-6)
    assert r["saturation_current"] == pytest.approx(13.8)  # 10 % drop
    assert r["rated_current"] == pytest.approx(35.5)  # 40 °C rise


def test_coilcraft_unresolvable_mpn_returns_no_fabricated_row():
    # A multi-row table with no MPN and no inductance hint cannot be pinned to a
    # single part → only the vendor, never an arbitrary row.
    r = parse_magnetic_text(_COILCRAFT_XGL6060)
    assert r == {"vendor": "coilcraft"}


# ---------------------------------------------------------------------------
# Vishay IHLP-2020BZ-01 — High-Saturation series table (real Doc 34253 rows)
# ---------------------------------------------------------------------------

_VISHAY_IHLP2020BZ = """
IHLP2020BZ-01
www.vishay.com Vishay Dale
IHLP® Commercial Inductors, High Saturation Series
HEAT RATING SATURATION
L INDUCTANCE ± 20 % DCR TYP. DCR MAX. CURRENT CURRENT
AT 100 kHz, 0.25 V, 0 A 25 °C 25 °C DC TYP. DC TYP. SRF TYP.
(μH) (mΩ) (mΩ) (A) (1) (A) (2) (MHz)
IHLP2020BZE_R10M01 0.10 3.6 3.9 17.0 45.0 239
IHLP2020BZE_2R2M01 2.2 45.6 50.1 4.2 9.5 39
IHLP2020BZE_100M01 10 184.0 199.0 2.3 4.0 20
(1) DC current (A) that will cause an approximate ΔT of 40 °C
(2) DC current (A) that will cause L to drop approximately 20 %
"""


def test_vishay_vendor_detected():
    assert detect_magnetic_vendor(_VISHAY_IHLP2020BZ) == "vishay"


def test_vishay_heat_rating_is_rated_saturation_is_isat_20pct():
    # Select the 2.2 µH row by MPN (the '_' is a termination-code wildcard).
    r = parse_magnetic_text(_VISHAY_IHLP2020BZ, mpn="IHLP2020BZER2R2M01")
    assert r["inductance"] == pytest.approx(2.2e-6)
    assert r["rdc_typ"] == pytest.approx(0.0456)
    assert r["rdc_max"] == pytest.approx(0.0501)
    # HEAT RATING CURRENT (note 1, ΔT≈40 °C) is the STANDARD rated current, and
    # sits in the column BEFORE saturation — must not be swapped.
    assert r["rated_current"] == pytest.approx(4.2)
    # SATURATION CURRENT (note 2, L drops ≈20 %) is the Isat.
    assert r["isat_20pct"] == pytest.approx(9.5)
    assert r["saturation_current"] == pytest.approx(9.5)
    assert r["saturation_current_drop_pct"] == 20


def test_vishay_row_selectable_by_inductance_when_mpn_absent():
    r = parse_magnetic_text(_VISHAY_IHLP2020BZ, vendor="vishay", inductance=10e-6)
    assert r["inductance"] == pytest.approx(10e-6)
    assert r["rated_current"] == pytest.approx(2.3)
    assert r["saturation_current"] == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# MPS MPL-AL6050-1R5 — single-part labelled sheet (real Rev 1.1 lines)
# ---------------------------------------------------------------------------

# Verbatim spec lines + the roll-off definition footnote (the 30 % lives in the
# footnote, NOT on the ISAT spec line — the parser must reach for it there).
_MPS_MPL_AL6050 = """
MPL-AL6050-1R5
Low-Resistance Molded Inductor 1.5µH
Inductance (1) L ±20% 1.5 µH
Resistance R typ 6.0 mΩ
Resistance R max 6.5 mΩ
Molded Construction Rated Current (2) I R typ 13.3 A
Soft Saturation Saturation Current (3) I typ 18 A
Stable Over High Temperatures Saturation Current (4) I typ 18 A
(1) Inductance Measured at 100kHz, 100mA
(2) Rated Current Rated current will cause the coil temperature rise ΔT of 40K
(3) Saturation Current Saturation current will cause L to drop from 30% at 25°C ambient temperature
MPL-AL6050-1R5 Rev. 1.1 www.MonolithicPower.com
"""


def test_mps_vendor_detected():
    assert detect_magnetic_vendor(_MPS_MPL_AL6050, mpn="MPL-AL6050-1R5") == "mps"


def test_mps_labelled_specs_with_footnote_drop_definition():
    r = parse_magnetic_text(_MPS_MPL_AL6050, mpn="MPL-AL6050-1R5")
    assert r["inductance"] == pytest.approx(1.5e-6)
    assert r["tolerance"] == pytest.approx(0.20)
    assert r["rdc_typ"] == pytest.approx(0.006)
    assert r["rdc_max"] == pytest.approx(0.0065)
    # Rated current = I R (ΔT = 40 K) — the standard thermal rating.
    assert r["rated_current"] == pytest.approx(13.3)
    # Saturation current 18 A, its 30 %-drop definition pulled from the footnote.
    assert r["saturation_current"] == pytest.approx(18.0)
    assert r["saturation_current_drop_pct"] == 30


# ---------------------------------------------------------------------------
# Rule (a): standard rated current is preferred over the best-case performance
# figure when a datasheet lists BOTH (WE-XHMI-style labelled sheet).
# ---------------------------------------------------------------------------

def test_standard_ir_preferred_over_performance_irp():
    txt = (
        "Inductance L 100 kHz 4.7 µH ±20%\n"
        "Rated Current I R,40K ΔT = 40K 13.2 A\n"
        "Performance Rated Current I RP,40K ΔT = 40K 19.35 A\n"
        "Saturation Current @ 20% |ΔL/L| < 20 % 15.0 A\n"
        "DCR R typ 4.5 mΩ\n"
    )
    r = parse_magnetic_text(txt, vendor="tdk")  # force the labelled path
    assert r["rated_current"] == pytest.approx(13.2)  # IR, not IRP
    assert r["irp_40k"] == pytest.approx(19.35)  # kept, flagged as best-case
    assert r["saturation_current"] == pytest.approx(15.0)
    assert r["saturation_current_drop_pct"] == 20


# ---------------------------------------------------------------------------
# enrich.py dispatch: a non-Würth original routes to the multi-vendor parser
# and produces the same summary keys the pipeline compares.
# ---------------------------------------------------------------------------

def test_enrich_dispatches_coilcraft_to_multi_parser():
    out = enrich_from_text("XGL6060-822", "inductor", _COILCRAFT_XGL6060)
    assert out["inductance"] == pytest.approx(8.2e-6)
    assert out["saturation_current"] == pytest.approx(4.1)
    assert out["saturation_current_drop_pct"] == 10
    assert out["rated_current"] == pytest.approx(11.0)
    assert out["dcr"] == pytest.approx(0.0152)


def test_enrich_still_routes_wurth_to_we_parser():
    we_text = (
        "Würth Elektronik eiSos\n"
        "Inductance L 100 kHz/ 10 mA 1.5 µH ±20%\n"
        "Saturation Current @ 10% I SAT, 10% |ΔL/L| < 10 % 4.8 A typ.\n"
        "Saturation Current @ 30% I SAT,30% |ΔL/L| < 30 % 10.2 A typ.\n"
        "DC Resistance R DC @ 20 °C 16 mΩ typ.\n"
    )
    out = enrich_from_text("74438356015", "magnetic", we_text)
    assert out["saturation_current"] == pytest.approx(4.8)  # conservative 10 %
    assert out["saturation_current_drop_pct"] == 10
    assert out["dcr"] == pytest.approx(0.016)
