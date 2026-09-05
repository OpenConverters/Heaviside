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
    """"241" reads as "161 241 I = 81A" and the "nC" that qualifies it is 62
    characters away, in the column header."""
    value, note = _corroborated("totalGateCharge", 241.0, IRFP_SHEET)
    assert value == pytest.approx(2.41e-7)
    assert "converted to SI" in note


def test_the_wide_search_needs_the_unit_not_just_the_prefix_letter():
    """Matching the bare prefix let a gate charge of 117 borrow the "p" out of
    an unrelated "1460 pF" and come back as 117 pC — three orders out."""
    sheet = "QG 117 ... nC ... Coss 1460 pF"
    assert _corroborated("totalGateCharge", 117.0, sheet)[0] == pytest.approx(1.17e-7)


def test_a_value_nowhere_on_the_page_is_still_refused():
    """What this check still guarantees, after the unit window had to widen.

    A table puts its unit in a column HEADER — Vishay writes "RDS(on) max.
    (Ohm) at VGS = 10 V 0.00210", 24 characters ahead of the number — so
    looking only AFTER the number refused correct readings of well-formed
    sheets, repeatedly. Widening it costs the strongest form of this check: a
    stray "1.0" elsewhere on a 200 V sheet can now find a "V" near enough to
    qualify it.

    What is left, and what this pins, is that a number the page does not print
    at all is refused. The rest of the defence is the physical bound, the two
    readings having to agree, and the record being staged for a human against
    the document it links — never this check on its own.
    """
    value, note = _corroborated("drainSourceVoltage", 37.0, IRFP_SHEET)
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


def test_a_unit_the_document_never_spells_cannot_be_required():
    """A PDF's text layer renders the ohm sign as "W", as a private-use glyph,
    or not at all. Requiring it unconditionally scored 1 of 6 on real MOSFETs.
    When none of a field's spellings appear anywhere in the document, the check
    cannot be applied to that field and the mantissa alone stands."""
    sheet = "RDS(on) max 4.1 m  VDSS 120 V  ID 90 A"   # no ohm sign at all
    assert _corroborated("onResistance", 0.0041, sheet)[0] == pytest.approx(0.0041)


def test_lifting_the_unit_rule_is_per_field_and_does_not_leak():
    """Volts ARE on that page, so the drain-source check still applies and a
    1 V reading of a 120 V part is still refused."""
    sheet = "RDS(on) max 4.1 m  VDSS 120 V  ID 90 A"
    value, note = _corroborated("drainSourceVoltage", 1.0, sheet)
    assert value is None and "not printed" in note


def test_a_small_value_printed_in_base_units_is_found():
    """Vishay prints an on-resistance as "0.00210 Ω" rather than scaling it to
    milliohms. The form generator skipped every mantissa below 0.05 and never
    produced trailing zeros, so neither "0.0021" nor "0.00210" was looked for
    and a correctly-read value was refused."""
    sheet = "R DS(on) V GS = 10 V, I D = 30 A 0.00210 Ω  V DS 60 V"
    assert _corroborated("onResistance", 0.0021, sheet)[0] == pytest.approx(0.0021)


def test_a_form_that_rounds_the_value_away_is_not_that_value():
    """"4" is not 4.5, and "0.00" is not 0.0021. Generating them would let any
    page with a zero on it corroborate anything."""
    from heaviside.librarian.fetcher.from_datasheet import _mantissas

    assert "4" not in _mantissas(4.5)
    assert "0.0" not in _mantissas(0.0021)
    assert "0.00" not in _mantissas(0.0021)
    assert "0.00210" in _mantissas(0.0021)


def test_a_document_that_does_not_name_the_part_is_not_its_datasheet():
    """"Yields text" was too weak: a landing page, a selection guide or another
    part's sheet all yield text, and reading one produced a plausible record
    for the wrong component."""
    from heaviside.librarian.fetcher.from_datasheet import envelope_from_datasheet

    class _Doc:
        content_type = "text/plain"
        final_url = "https://example.com/other.pdf"
        content = (b"Some other part entirely. " * 40)

    seen = []

    def fetch(url, timeout=60):
        seen.append(url)
        return _Doc()

    env, why, detail = envelope_from_datasheet(
        "IPA045N10N3G", "mosfet", datasheet_url="https://example.com/other.pdf",
        fetch=fetch, seek=lambda *a, **k: {})
    assert env is None
    assert "does not name" in " ".join(detail.get("rejected", []))


# ---------------------------------------------------------------------------
# the families beyond MOSFET and diode
# ---------------------------------------------------------------------------


def test_a_capacitors_technology_is_read_off_the_page_never_chosen():
    """This is why capacitors were left out at first. The schema wants one of
    twenty technologies, and a model asked to pick from an enum always returns
    something. A datasheet SAYS "X7R", so the mapping is a lookup here."""
    from heaviside.librarian.fetcher.from_datasheet import technology_from_text

    assert technology_from_text("capacitor", "MLCC, X7R dielectric") == "ceramic-class-2"
    assert technology_from_text("capacitor", "C0G/NP0 class 1") == "ceramic-class-1"
    assert technology_from_text("capacitor", "Aluminum electrolytic") == "aluminum-electrolytic-wet"
    assert technology_from_text("capacitor", "conductive polymer aluminum") == "aluminum-electrolytic-polymer"
    assert technology_from_text("capacitor", "metallized polypropylene") == "film-polypropylene"
    assert technology_from_text("resistor", "Thick Film Chip Resistor") == "thickFilm"
    assert technology_from_text("resistor", "Bulk Metal Foil") == "bulkMetalFoil"
    # most specific wins: bulk metal foil is not metal foil
    assert technology_from_text("resistor", "metal foil") == "metalFoil"
    # and a page that says none of them yields nothing to guess from
    assert technology_from_text("capacitor", "a capacitor of some kind") == ""


def test_a_capacitor_with_no_stated_technology_is_refused():
    env, why = _BUILDERS["capacitor"](
        {}, "X", "Murata", "u", "h", "Capacitance 100 nF Rated 50 V chip",
        {"capacitance": (1e-7, "100 nF"), "ratedVoltage": (50.0, "50 V")})
    assert env is None
    assert "not a value to guess" in why


def _valid(cat, env):
    from heaviside.librarian.fetcher.from_datasheet import _CATEGORY_TO_DB
    from heaviside.librarian.tas import validate_component

    validate_component(_CATEGORY_TO_DB[cat], env)   # raises if not


def test_a_capacitor_reading_builds_a_valid_record_with_its_tolerance_band():
    text = ("Chip Monolithic Ceramic Capacitor X7R dielectric SMD "
            "Capacitance 100 nF Rated Voltage 50 V Tolerance 10 % ESR 20 mOhm")
    env, _ = _BUILDERS["capacitor"](
        {}, "GRM188R71H104KA93D", "Murata", "https://x/y.pdf", "x", text,
        {"capacitance": (1e-7, "100 nF"), "ratedVoltage": (50.0, "50 V"),
         "tolerance": (10.0, "10 %"), "esr": (0.02, "20 mOhm")})
    assert env is not None
    _valid("capacitor", env)
    e = env["capacitor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]
    # a capacitance is a toleranced value, not a number: 100 nF +/-10 % and
    # 100 nF +/-1 % are different parts
    assert e["capacitance"]["nominal"] == pytest.approx(1e-7)
    assert e["capacitance"]["minimum"] == pytest.approx(0.9e-7)
    assert e["capacitance"]["maximum"] == pytest.approx(1.1e-7)
    # CAS carries the tolerance in that band, not as a field of its own —
    # unlike RAS, which requires the field. The record matches the schema it
    # claims to be.
    assert "tolerance" not in e


def test_a_resistor_reading_builds_a_valid_record():
    text = "Thick Film Chip Resistor 10 kOhm Tolerance 1 % Power Rating 0.1 W"
    env, _ = _BUILDERS["resistor"](
        {}, "CRCW060310K0FKED", "Vishay", "https://x/y.pdf", "x", text,
        {"resistance": (10000.0, "10 kOhm"), "tolerance": (1.0, "1 %"),
         "powerRating": (0.1, "0.1 W")})
    assert env is not None
    _valid("resistor", env)


def test_an_igbt_reading_builds_a_valid_record():
    text = "IGBT VCES 1200 V IC 40 A VCE(sat) 1.75 V Tj 175 C"
    env, _ = _BUILDERS["igbt"](
        {}, "IKW40N120H3", "Infineon", "https://x/y.pdf", "x", text,
        {"collectorEmitterVoltage": (1200.0, "1200 V"),
         "continuousCollectorCurrent": (40.0, "40 A"),
         "collectorEmitterSaturation": (1.75, "1.75 V"),
         "junctionTemperatureMax": (175.0, "175 C")})
    assert env is not None
    _valid("igbt", env)


def test_bjt_is_absent_because_the_librarian_cannot_write_one():
    """Not an oversight: heaviside.librarian.safe_access has no "bjts"
    category, so a perfectly-read BJT could not be staged anywhere. Adding a
    category is a data-governance change, not this module's to make."""
    from heaviside.librarian import safe_access
    from heaviside.librarian.fetcher.from_datasheet import SUPPORTED

    assert "bjts" not in safe_access.CATEGORIES
    assert "bjt" not in SUPPORTED
    for supported in SUPPORTED:
        from heaviside.librarian.fetcher.from_datasheet import _CATEGORY_TO_DB
        assert _CATEGORY_TO_DB[supported] in safe_access.CATEGORIES


# ---------------------------------------------------------------------------
# the document has to be the part's own datasheet
# ---------------------------------------------------------------------------


def test_a_family_datasheet_with_a_wildcarded_suffix_names_the_part():
    """Manufacturers publish ONE sheet for a family whose trailing characters
    are packaging options, written as a wildcard: Murata's sheet for
    GRM188R71H104KA93D calls it "GRM188R71H104KA93#". An exact match threw a
    correct datasheet away over a hash."""
    from heaviside.librarian.fetcher.from_datasheet import _names_the_part

    mpn = "grm188r71h104ka93d"
    assert _names_the_part(mpn, "... GRM188R71H104KA93D ...")[0] is True
    named, how = _names_the_part(mpn, "... GRM188R71H104KA93# ...")
    assert named and "packaging or tolerance" in how
    # but a stem is never allowed to get short enough to match anything
    assert _names_the_part("ss34", "... SS3 ...")[0] is False
    assert _names_the_part(mpn, "... C0603C104K5RACTU ...")[0] is False


def test_the_technology_is_read_from_the_title_block_not_the_whole_file():
    """A TDK product page's related-products sidebar listed an MLCC whose part
    number contains "C0G", and a polypropylene film capacitor came back
    classified ceramic-class-1. The part's own description is at the top."""
    from heaviside.librarian.fetcher.from_datasheet import technology_from_text

    sheet = ("Metallized Polypropylene Film Capacitor B32922 series, EMI suppression"
             + " filler " * 400 + " related products: FG26C0G2J102JNT06 MLCC")
    assert technology_from_text("capacitor", sheet) == "film-polypropylene"


def test_the_manufacturer_can_come_from_the_datasheets_own_title_block():
    """The only PDF copy is often hosted by a DISTRIBUTOR — Farnell serves
    Murata's sheet — and a distributor's hostname says nothing about who made
    the part. The document's first page does."""
    from heaviside.librarian.fetcher.from_datasheet import manufacturer_from_text

    assert manufacturer_from_text("Murata Manufacturing Co., Ltd. Chip MLCC") == "Murata"
    assert manufacturer_from_text("no vendor named here at all") == ""


def test_a_web_page_is_not_a_datasheet(tmp_path):
    """Accepting HTML to widen coverage let a product page through, and its
    sidebar renamed the part's technology. A wrong record is worse than none."""
    from heaviside.librarian.fetcher.from_datasheet import envelope_from_datasheet

    class _Page:
        content_type = "text/html; charset=utf-8"
        final_url = "https://product.tdk.com/en/search/capacitor/film/info"
        content = (b"<html>B32922C3224M electrical characteristics rating "
                   b"absolute maximum </html>" + b"x" * 900)

    env, why, detail = envelope_from_datasheet(
        "B32922C3224M", "mosfet", datasheet_url="https://product.tdk.com/x",
        fetch=lambda url, timeout=60: _Page(), seek=lambda *a, **k: {})
    assert env is None
    assert "not a datasheet PDF" in " ".join(detail.get("rejected", []))


# ---------------------------------------------------------------------------
# connectors and varistors
# ---------------------------------------------------------------------------


def test_the_schema_says_which_fields_are_toleranced_objects():
    """A varistor's varistorVoltage is a dimensionWithTolerance while the
    clampingVoltage beside it is a plain number. A table of that here would be
    a second copy of the contract, drifting; the schema is asked instead."""
    from heaviside.librarian.fetcher.from_datasheet import toleranced_fields

    assert "varistorVoltage" in toleranced_fields("varistors")
    assert "clampingVoltage" not in toleranced_fields("varistors")
    assert "contactResistance" in toleranced_fields("connectors")
    assert "capacitance" in toleranced_fields("capacitors")
    assert "resistance" in toleranced_fields("resistors")


def test_a_connector_family_is_read_not_invented():
    """CONAS's familyDetails is a discriminated union — `family` is a const that
    selects the variant and catalogue filtering reads it — so it is read off the
    title block, and a sheet naming none of the fourteen is refused."""
    from heaviside.librarian.fetcher.from_datasheet import _connector_family

    assert _connector_family("PCB Terminal Block, pluggable") == "terminalBlock"
    assert _connector_family("2.54 mm Pin Header, single row") == "pinHeaderSocket"
    assert _connector_family("USB Type-C Receptacle") == "dataInterface"
    assert _connector_family("M12 circular connector, 5-pole") == "circular"
    assert _connector_family("a connector of some kind") == ""


def test_a_connector_reading_builds_a_valid_record():
    text = ("MKDS 1,5/2-5,08 PCB terminal block, 2 positions, pitch 5.08 mm, "
            "rated current per contact 17.5 A, rated voltage 630 V")
    env, _ = _BUILDERS["connector"](
        {}, "1725656", "Phoenix Contact", "https://x/y.pdf", "x", text,
        {"ratedCurrentPerContact": (17.5, "17.5 A"), "ratedVoltage": (630.0, "630 V"),
         "positions": (2.0, "2"), "pitch": (0.00508, "5.08 mm")})
    assert env is not None
    _valid("connector", env)
    ds = env["connector"]["manufacturerInfo"]["datasheetInfo"]
    assert ds["familyDetails"]["family"] == "terminalBlock"
    assert ds["electrical"]["ratedCurrentPerContact"] == 17.5


def test_a_connector_family_needing_its_own_field_is_refused_not_faked():
    """rf, dataInterface and acInlet each require a field of their own. Filing
    one without it would be a record the schema accepts and the catalogue
    cannot use."""
    env, why = _BUILDERS["connector"](
        {}, "X", "Amphenol", "u", "h", "USB Type-C Receptacle, 24 position",
        {"ratedCurrentPerContact": (5.0, "5 A")})
    assert env is None and "interfaceStandard" in why


def test_a_varistor_reading_builds_a_valid_record_with_its_toleranced_voltage():
    text = ("Metal Oxide Varistor MOV. Varistor voltage V1mA 430 V, "
            "clamping voltage 710 V, peak surge current 6500 A")
    env, _ = _BUILDERS["varistor"](
        {}, "V275LA40AP", "Littelfuse", "https://x/y.pdf", "x", text,
        {"varistorVoltage": (430.0, "430 V"), "clampingVoltage": (710.0, "710 V"),
         "peakSurgeCurrent": (6500.0, "6500 A")})
    assert env is not None
    _valid("varistor", env)
    e = env["varistor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]
    # the schema wants this one as an object and the one beside it as a number
    assert e["varistorVoltage"] == {"nominal": 430.0}
    assert e["clampingVoltage"] == 710.0


def test_a_varistor_with_no_stated_technology_is_refused():
    """Every value reads fine; the sheet just never says what kind of varistor
    it is. The technology is a fixed list, so that is a refusal, not a guess."""
    text = ("Surge protection device. Varistor voltage V1mA 430 V, "
            "clamping voltage 710 V, peak surge current 6500 A")
    env, why = _BUILDERS["varistor"](
        {}, "X", "Y", "u", "h", text,
        {"varistorVoltage": (430.0, "430 V"), "clampingVoltage": (710.0, "710 V"),
         "peakSurgeCurrent": (6500.0, "6500 A")})
    assert env is None and "technology" in why


def test_a_disagreed_field_never_falls_through_to_the_weaker_reading():
    """The hole this closes. When the two verbatim passes disagreed the field
    was correctly withheld — and then _resolve fell back to the SI pass and
    built the record anyway from the reading with no second opinion behind it.
    Observed live on IPA045N10N3G: 4.5 vs 4.7 mOhm, disagreement recorded,
    record built regardless."""
    specs = {"vds_V": 100, "id_A": 64, "rds_on_ohm": 0.0047, "manufacturer": "Infineon"}
    verbatim = {"drainSourceVoltage": (100.0, "100 V"),
                "continuousDrainCurrent": (64.0, "64 A")}   # onResistance withheld
    sheet = "VDSS 100 V ID 64 A RDS(on) max 4.5 mOhm typ 4.7 mOhm"

    # without the disagreement it falls through and builds
    env, _ = _BUILDERS["mosfet"](specs, "X", "Infineon", "u", "h", sheet, verbatim)
    assert env is not None, "sanity: the SI pass can supply it when nothing objected"

    # with it, the field is dropped and the record refused
    env, why = _BUILDERS["mosfet"](specs, "X", "Infineon", "u", "h", sheet, verbatim,
                                   frozenset({"onResistance"}))
    assert env is None
    assert "onResistance" in why
