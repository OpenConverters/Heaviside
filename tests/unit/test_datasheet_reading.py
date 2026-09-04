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
