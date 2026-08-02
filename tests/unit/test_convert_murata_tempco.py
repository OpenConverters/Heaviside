"""Unit tests for the temperature characteristic ``scripts/convert_murata.py`` emits.

The importer used to build thermal like ``if temp_characteristic: tcc = {"nominal": 0}``
— Murata's product list NAMES the characteristic in that column ('C0G', 'U2J', 'X7R',
'ZLM') and the importer read it as a yes/no flag. For C0G the placeholder is accidentally
right, which is why it survived; for the 401 U2J rows it asserted the one property a U2J
does not have and made them indistinguishable from a C0G in the crossref ranker
(ABT #517).

So these pin the three outcomes: an EIA class-1 code decodes, a class-2 code and Murata's
own designations emit NO tcc at all, and a zero is only ever written when the code really
does specify zero.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# Load scripts/convert_murata.py without it being a package member.
_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "convert_murata.py"
_spec = importlib.util.spec_from_file_location("convert_murata", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
convert_murata = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(convert_murata)


class TestEiaTempco:
    @pytest.mark.parametrize(
        "code,nominal,minimum,maximum",
        [
            # U2J: U = 7.5, 2 = x(-100), J = +/-120 ppm/K. The ABT #517 part.
            ("U2J", -750.0, -870.0, -630.0),
            # C0G: C = 0.0, 0 = x(-1), G = +/-30 ppm/K — zero, and legitimately so.
            ("C0G", 0.0, -30.0, 30.0),
            ("P2H", -150.0, -210.0, -90.0),
            ("S2H", -330.0, -390.0, -270.0),
        ],
    )
    def test_eia_class1_codes_decode(self, code, nominal, minimum, maximum):
        assert convert_murata.eia_tempco(code) == {
            "nominal": nominal, "minimum": minimum, "maximum": maximum, "unit": "ppm/K"}

    def test_c0g_nominal_is_not_negative_zero(self):
        # 0.0 x -1 is -0.0, which serialises as "-0.0" and reads as a coefficient.
        assert repr(convert_murata.eia_tempco("C0G")["nominal"]) == "0.0"

    @pytest.mark.parametrize("code", ["X7R", "X5R", "X8R", "Y5V", "Z5U"])
    def test_class2_codes_have_no_coefficient(self, code):
        # A class-2 dielectric is specified as a percent capacitance-change band over a
        # range, not as a coefficient. There is no number to put in tcc.
        assert convert_murata.eia_tempco(code) is None

    @pytest.mark.parametrize("code", ["ZLM", "X8G", "X8L", "X8M"])
    def test_murata_own_designations_have_no_coefficient(self, code):
        # publicstandard MURATA, not EIA — nothing decodable, so nothing may be written.
        assert convert_murata.eia_tempco(code) is None

    @pytest.mark.parametrize("code", [None, "", "C0", "C0GX", "Q0G", "C5G", "C0Z"])
    def test_undecodable_input_yields_none(self, code):
        assert convert_murata.eia_tempco(code) is None


def _sheet(characteristic):
    doc = convert_murata.make_capacitor_document(
        part_number="TEST-PART",
        series="TEST",
        technology="MLCC Class II" if (characteristic or "").startswith("X") else "MLCC Class I",
        case="2012M/0805",
        capacitance_f=1.5e-8,
        rated_voltage=50.0,
        tolerance_str="±5%",
        temp_max=125.0,
        temp_characteristic=characteristic,
    )
    return doc["capacitor"]["manufacturerInfo"]["datasheetInfo"]


class TestEmittedDocument:
    def test_u2j_row_carries_its_real_coefficient_and_code(self):
        sheet = _sheet("U2J")
        assert sheet["thermal"]["tcc"] == {
            "nominal": -750.0, "minimum": -870.0, "maximum": -630.0, "unit": "ppm/K"}
        assert sheet["part"]["dielectricCode"] == "U2J"

    def test_c0g_row_keeps_its_true_zero(self):
        sheet = _sheet("C0G")
        assert sheet["thermal"]["tcc"]["nominal"] == 0.0
        assert sheet["part"]["dielectricCode"] == "C0G"

    @pytest.mark.parametrize("characteristic", ["X7R", "ZLM", None])
    def test_undecodable_characteristic_emits_no_tcc_and_no_code(self, characteristic):
        # An absent field is missing data; a zero is a claim that the part is
        # temperature-stable. The importer may only make the claim it can source.
        sheet = _sheet(characteristic)
        assert "tcc" not in sheet["thermal"]
        assert "dielectricCode" not in sheet["part"]
