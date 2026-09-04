"""Reading numbers off a datasheet without inventing any.

Every defect pinned here was observed on one real Infineon PDF while building
this. Across five runs the same gate charge came back as 88, 117, 8.8, 11.7 and
1.17 — each a real number from the page with the exponent guessed differently —
and a prompt demanding SI made it worse, turning a 100 V part into 1.0 V and
175 degC into 448.15 K. So the model is asked for the printed string and the
arithmetic is done here, and nothing is accepted that the page does not say.

No network: the datasheet text is a fixture.
"""

from __future__ import annotations

import pytest

from heaviside.librarian.fetcher.from_datasheet import (
    _BUILDERS,
    _corroborated,
    _is_printed,
    parse_verbatim,
)

# the shapes the Infineon sheet actually renders as, including the PDF text
# layer turning the ohm sign into a W
SHEET = (
    "IPA045N10N3 G OptiMOS 3 Power-Transistor "
    "V(BR)DSS 100 V  ID 64 A  RDS(on) typ 3.9 mW max 4.5 mW "
    "QG typ 88 nC max 117 nC  Coss typ 1460 pF "
    "VGS(th) min 2.5 typ 3.0 max 3.5 V  Tj -55 to 175 degC"
)


# ---------------------------------------------------------------------------
# the arithmetic the model is no longer asked to do
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "printed,expected",
    [
        ("88 nC", 8.8e-8),
        ("117 nC", 1.17e-7),
        ("11.7nC", 1.17e-8),
        ("100 V", 100.0),
        ("64 A", 64.0),
        ("4.5 mOhm", 0.0045),
        ("max 4.5 mΩ", 0.0045),      # a leading word, and the ohm sign
        ("4.5 mW", 0.0045),               # what the PDF's text layer gives
        ("2.2 kOhm", 2200.0),
        ("1460 pF", 1.46e-9),
        ("1.5 MHz", 1.5e6),               # M is mega, m is milli
        ("1.5 mHz", 1.5e-3),
        ("-55 to 175 °C", 175.0),    # a range takes its upper end
        ("typ. 88 nC", 8.8e-8),
        ("< 3.5 V", 3.5),
        ("0.5 V at 100 A", 0.5),          # NOT the current
        (0.0045, 0.0045),                 # already a number
        (None, None),
        ("", None),
        ("n/a", None),
    ],
)
def test_a_printed_value_parses_to_si(printed, expected):
    got = parse_verbatim(printed)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected, rel=1e-9)


def test_the_ohm_sign_survives_folding():
    """`"Ω".lower()` is `"ω"`, a DIFFERENT character. Folding the unit
    with .lower() silently stopped recognising ohms, which is most of what a
    MOSFET datasheet says."""
    assert parse_verbatim("4.5 mΩ") == pytest.approx(0.0045)
    assert parse_verbatim("4.5 mΩ") == pytest.approx(0.0045)   # U+2126 ohm sign


# ---------------------------------------------------------------------------
# nothing enters that the page does not say
# ---------------------------------------------------------------------------


def test_a_value_the_datasheet_prints_is_accepted():
    assert _corroborated("drainSourceVoltage", 100.0, SHEET) == (100.0, "")


def test_the_worst_case_a_hundred_volt_part_read_as_one_volt_is_refused():
    """The single reason this check exists. A bare substring search let the "1"
    corroborate itself out of the "100" in "100 V"."""
    value, note = _corroborated("drainSourceVoltage", 1.0, SHEET)
    assert value is None
    assert "not printed" in note


def test_a_dropped_si_prefix_is_repaired_only_at_a_scale_the_page_prints():
    value, note = _corroborated("totalGateCharge", 117.0, SHEET)
    assert value == pytest.approx(1.17e-7)
    assert "converted to SI" in note


def test_a_value_already_in_si_is_found_in_the_units_the_page_uses():
    """0.0045 ohm is printed "4.5 m", never "0.0045"."""
    assert _corroborated("onResistance", 0.0045, SHEET)[0] == pytest.approx(0.0045)


def test_a_kelvin_conversion_is_refused_outright():
    value, note = _corroborated("junctionTemperatureMax", 448.15, SHEET)
    assert value is None and "not a possible value" in note


def test_a_number_nowhere_on_the_page_is_refused_even_when_plausible():
    value, note = _corroborated("continuousDrainCurrent", 37.0, SHEET)
    assert value is None and "not printed" in note


def test_a_page_number_does_not_corroborate_a_gate_charge():
    """117 alone must not pass; only "117 nC" does."""
    assert _is_printed(1.17e-7, SHEET) is True
    assert _is_printed(1.17e-7, "page 117 of 240") is False


# ---------------------------------------------------------------------------
# the record that comes out
# ---------------------------------------------------------------------------


def test_the_verbatim_reading_beats_the_models_own_arithmetic():
    """Both passes run; where they disagree the one carrying the page's units
    wins. Here the SI pass says 11.7 C of gate charge (impossible) and the
    verbatim pass says "117 nC"."""
    verbatim = {
        "drainSourceVoltage": (100.0, "100 V"),
        "continuousDrainCurrent": (64.0, "64 A"),
        "onResistance": (0.0045, "4.5 mW"),
        "totalGateCharge": (1.17e-7, "117 nC"),
    }
    specs = {"vds_V": 1.0, "id_A": 64, "rds_on_ohm": 0.0045, "qg_C": 11.7,
             "manufacturer": "Infineon"}
    env, notes = _BUILDERS["mosfet"](specs, "IPA045N10N3G", "Infineon",
                                     "https://www.infineon.com/x.pdf",
                                     "www.infineon.com", SHEET, verbatim)
    assert env is not None
    e = env["semiconductor"]["mosfet"]["manufacturerInfo"]["datasheetInfo"]["electrical"]
    assert e["drainSourceVoltage"] == 100.0        # not the SI pass's 1.0
    assert e["totalGateCharge"] == pytest.approx(1.17e-7)
    assert "117 nC" in notes


def test_a_record_built_from_a_reading_says_it_was_read_from_a_datasheet():
    verbatim = {"drainSourceVoltage": (100.0, "100 V"),
                "continuousDrainCurrent": (64.0, "64 A"),
                "onResistance": (0.0045, "4.5 mW")}
    env, _ = _BUILDERS["mosfet"]({}, "IPA045N10N3G", "Infineon",
                                 "https://www.infineon.com/x.pdf",
                                 "www.infineon.com", SHEET, verbatim)
    prov = env["semiconductor"]["mosfet"]["manufacturerInfo"]["datasheetInfo"]["provenance"]
    assert prov[0]["source"] == "manufacturerDatasheet"
    assert prov[0]["sourceUrl"].endswith(".pdf")


def test_a_reading_that_cannot_fill_the_required_fields_builds_nothing():
    env, why = _BUILDERS["mosfet"]({"vds_V": 100}, "X", "Infineon", "u", "h", SHEET, {})
    assert env is None
    assert "missing" in why and "onResistance" in why


# ---------------------------------------------------------------------------
# who made it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host,expected",
    [
        ("www.infineon.com", "Infineon Technologies"),
        ("ti.com", "Texas Instruments"),
        ("www.st.com", "STMicroelectronics"),
        ("somevendor.com", "Somevendor"),
        # a reseller or an aggregator republishes everyone's documents, so its
        # hostname says nothing about who made the part
        ("www.alldatasheet.com", ""),
        ("www.mouser.com", ""),
        ("datasheet.iiic.cc", ""),
        ("", ""),
    ],
)
def test_the_serving_site_names_the_manufacturer_only_when_it_can(host, expected):
    from heaviside.librarian.fetcher.from_datasheet import _manufacturer_from_host

    assert _manufacturer_from_host(host) == expected


# ---------------------------------------------------------------------------
# which row did that come from?
# ---------------------------------------------------------------------------


def _reader(*responses):
    """A call_llm stand-in that returns each response in turn."""
    seq = list(responses)

    def call(system, user, **kw):
        return seq.pop(0) if seq else seq[-1]

    return call


def test_a_field_two_readings_disagree_about_is_dropped_and_named():
    """Corroboration proves a number is ON the page, never that it came from
    the RIGHT row. On IPA045N10N3G, RDS(on) read 4.5 mOhm one run and 4.7 mOhm
    the next — both printed, at different conditions. A value that changes when
    you ask again is not one the catalogue should carry."""
    from heaviside.librarian.fetcher.from_datasheet import _read_verbatim

    kept, disagreed = _read_verbatim(
        "X", "mosfet", "text",
        call=_reader('{"drainSourceVoltage":"100 V","onResistance":"4.5 mOhm"}',
                     '{"drainSourceVoltage":"100 V","onResistance":"4.7 mOhm"}'))
    assert "drainSourceVoltage" in kept
    assert "onResistance" not in kept
    assert any("onResistance" in d and "0.0045" in d and "0.0047" in d for d in disagreed)


def test_a_field_only_one_reading_found_is_not_agreement():
    from heaviside.librarian.fetcher.from_datasheet import _read_verbatim

    kept, disagreed = _read_verbatim(
        "X", "mosfet", "text",
        call=_reader('{"totalGateCharge":"117 nC"}', '{"totalGateCharge":null}'))
    assert "totalGateCharge" not in kept
    assert any("only 1 of 2" in d for d in disagreed)


def test_two_readings_that_print_it_differently_still_agree():
    """"4.5 mOhm" and "max 4.50 mΩ" are the same measurement; agreement is on
    the parsed value, not on the string."""
    from heaviside.librarian.fetcher.from_datasheet import _read_verbatim

    kept, disagreed = _read_verbatim(
        "X", "mosfet", "text",
        call=_reader('{"onResistance":"4.5 mOhm"}', '{"onResistance":"max 4.50 mΩ"}'))
    assert kept["onResistance"][0] == pytest.approx(0.0045)
    assert disagreed == []


def test_the_condition_a_value_was_measured_at_is_carried():
    """An Rds(on) without its Vgs is not comparable to another part's: a
    logic-level part at 4.5 V and a standard one at 10 V are different
    numbers. The schema has fields for it, so the reading fills them."""
    verbatim = {
        "drainSourceVoltage": (100.0, "100 V"),
        "continuousDrainCurrent": (64.0, "64 A"),
        "onResistance": (0.0045, "4.5 mOhm"),
        "onResistanceVgs": (10.0, "10 V"),
        "onResistanceId": (64.0, "64 A"),
    }
    sheet = SHEET + " RDS(on) at VGS = 10 V, ID = 64 A"
    env, _ = _BUILDERS["mosfet"]({}, "X", "Infineon", "https://www.infineon.com/x.pdf",
                                 "www.infineon.com", sheet, verbatim)
    assert env is not None
    e = env["semiconductor"]["mosfet"]["manufacturerInfo"]["datasheetInfo"]["electrical"]
    assert e["onResistanceVgs"] == 10.0
    assert e["onResistanceId"] == 64.0


def test_a_condition_the_page_does_not_print_is_not_carried_either():
    verbatim = {
        "drainSourceVoltage": (100.0, "100 V"),
        "continuousDrainCurrent": (64.0, "64 A"),
        "onResistance": (0.0045, "4.5 mOhm"),
        "onResistanceVgs": (7.0, "7 V"),        # nowhere in SHEET
    }
    env, _ = _BUILDERS["mosfet"]({}, "X", "Infineon", "u", "www.infineon.com",
                                 SHEET, verbatim)
    e = env["semiconductor"]["mosfet"]["manufacturerInfo"]["datasheetInfo"]["electrical"]
    assert "onResistanceVgs" not in e


# ---------------------------------------------------------------------------
# a real datasheet's table, as the text layer actually renders it
# ---------------------------------------------------------------------------

# IRFP4668: the unit COLUMN is detached from the value. "241" reads as
# "161 241 I = 81A" and the "nC" that qualifies it is 62 characters away.
# Requiring the unit beside the number threw this reading away and the record
# failed schema validation for a gate charge the datasheet states plainly.
IRFP_SHEET = (
    "IRFP4668PbF V DSS 200V R typ. 8.0m DS(on) max. 9.7m I 130A D TO-247AC\n"
    "Gate-to-Source Voltage +/- 30 V\n"
    "R G Internal Gate Resistance --- 1.0 --- \n"
    "Dynamic @ TJ = 25C (unless otherwise specified)  nC\n"
    "Q Total Gate Charge --- 161 241 I = 81A\ng D\n"
    "Q Gate-to-Source Charge --- 54 ---\n"
    "t Turn-On Delay Time --- 105 --- I = 81A r D ns\n"
    "C Input Capacitance --- 10870 --- V = 0V iss GS pF\n"
    "C Output Capacitance --- 810 --- V = 50V oss DS\n"
    "V GS(th) Gate Threshold Voltage 3.0 --- 5.0 V\n"
    "TJ Operating Junction Temperature -55 to 175 C\n"
)


def test_a_value_whose_unit_is_in_the_table_column_is_still_corroborated():
    value, note = _corroborated("totalGateCharge", 241.0, IRFP_SHEET)
    assert value == pytest.approx(2.41e-7)
    assert "table column" in note


def test_the_wide_search_needs_the_unit_not_just_the_prefix_letter():
    """Matching the bare prefix let a gate charge of 117 borrow the "p" out of
    an unrelated "1460 pF" and come back as 117 pC — three orders out."""
    sheet = "QG 117 ... nC ... Coss 1460 pF"
    assert _corroborated("totalGateCharge", 117.0, sheet)[0] == pytest.approx(1.17e-7)


def test_a_bare_number_still_needs_its_unit_beside_it():
    """The wide window is only for a value that NEEDS a prefix. A number that
    is already plausible as written must have its unit adjacent, or a "1" in a
    figure caption would corroborate a 1 V part on a 200 V sheet."""
    value, note = _corroborated("drainSourceVoltage", 1.0, IRFP_SHEET)
    assert value is None
    assert "not printed" in note


def test_the_whole_irfp4668_reading_builds_a_valid_record():
    verbatim = {
        "drainSourceVoltage": (200.0, "200 V"),
        "continuousDrainCurrent": (130.0, "130 A"),
        "onResistance": (0.0097, "9.7 mOhm"),
        "onResistanceVgs": (10.0, "10 V"),
        "onResistanceId": (81.0, "81 A"),
        "capacitanceMeasurementVds": (50.0, "50 V"),
        "totalGateCharge": (2.41e-7, "241 nC"),
        "outputCapacitance": (8.1e-10, "810 pF"),
        "gateThresholdVoltage": (5.0, "5.0 V"),
        "junctionTemperatureMax": (175.0, "175 C"),
    }
    env, _ = _BUILDERS["mosfet"]({}, "IRFP4668PBF", "Infineon",
                                 "https://www.infineon.com/x.pdf",
                                 "www.infineon.com", IRFP_SHEET, verbatim)
    assert env is not None
    e = env["semiconductor"]["mosfet"]["manufacturerInfo"]["datasheetInfo"]["electrical"]
    # every field the schema requires of a MOSFET
    for required in ("drainSourceVoltage", "continuousDrainCurrent", "onResistance",
                     "gateThresholdVoltage", "totalGateCharge"):
        assert required in e, f"{required} was dropped"
    assert e["totalGateCharge"] == pytest.approx(2.41e-7)
    assert e["outputCapacitance"] == pytest.approx(8.1e-10)
