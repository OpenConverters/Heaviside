"""FAE round-6 (trap C2): a capacitor no_substitute note must not assert a
fabricated 'maximum available capacitance is NµF' catalogue ceiling. The model
invented '47µF' as the Würth 1206 ceiling when a 100µF/6.3V 1206 exists; and the
internal catalogue is itself incomplete here (zero Würth 1206 ≥6.3V rows), so we
strip the unfounded claim rather than substitute a different unfounded number.
"""

from heaviside.pipeline.crossref_pipeline import _strip_fabricated_cap_ceiling as strip


def test_strips_fabricated_maximum_available_ceiling():
    note = (
        "CRITICAL: Maximum available capacitance in Würth 1206 X5R/X7R at ≥6.3V is 47µF "
        "(885012108004). No 1210/1812/2220 Würth parts at 220µF/6.3V. "
        "Options: (1) RETAIN ORIGINAL Murata GRM31CR60J227ME39."
    )
    out = strip(note)
    assert "Maximum available capacitance" not in out
    assert "is 47µF" not in out
    # the honest verdict + retain-original guidance survive
    assert "No 1210/1812/2220" in out
    assert "RETAIN ORIGINAL" in out


def test_decimal_voltage_does_not_shield_the_claim():
    # the '.' in "6.3V" once acted as a false boundary and left the claim in.
    note = "Max available cap in 0805 at ≥6.3V is 10µF."
    assert strip(note) == ""


def test_legit_no_substitute_note_untouched():
    note = "No polymer tantalum in the catalogue. Retain original Panasonic 6TAE330ML."
    assert strip(note) == note


def test_empty_and_none_safe():
    assert strip("") == ""
    assert strip(None) is None
