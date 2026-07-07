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


def test_true_ceiling_appended_from_catalogue(tmp_path, monkeypatch):
    # After the REDEXPERT status+reference backfill the catalogue holds the real
    # Würth 1206 high-CV parts; the no_substitute note must state the TRUE ceiling
    # (100µF, 55% short) rather than the LLM's fabricated 47µF.
    import json

    from heaviside.pipeline.crossref_pipeline import _annotate_true_cap_ceiling

    def _cap(mpn, cap_f, v, case):
        return {"capacitor": {"manufacturerInfo": {
            "name": "Würth Elektronik", "reference": mpn, "status": "production",
            "datasheetInfo": {"electrical": {"capacitance": {"nominal": cap_f}, "ratedVoltage": v},
                              "part": {"case": case}}}}}

    (tmp_path / "capacitors.ndjson").write_text("\n".join(json.dumps(e) for e in [
        _cap("A", 47e-6, 6.3, "1206"),
        _cap("B", 100e-6, 6.3, "1206"),   # the real ceiling
        _cap("C", 220e-6, 6.3, "1210"),   # wrong package — must not count
    ]) + "\n")

    row = {"original_pn": "GRM31CR60J227ME39", "original_value": "220uF", "status": "no_substitute",
           "notes": "Retain original."}
    op = {"capacitance": 220e-6, "voltage": 6.3, "package": "1206"}
    _annotate_true_cap_ceiling(row, op, "Würth Elektronik", tmp_path)
    assert "100µF" in row["notes"]
    assert "55% short" in row["notes"]
    assert "B" in row["notes"]  # cites the real MPN
    assert "47µF" not in row["notes"].split("Largest")[-1]  # ceiling isn't the 47µF part


def test_true_ceiling_silent_when_larger_exists(tmp_path):
    import json

    from heaviside.pipeline.crossref_pipeline import _annotate_true_cap_ceiling

    (tmp_path / "capacitors.ndjson").write_text(json.dumps({"capacitor": {"manufacturerInfo": {
        "name": "Würth Elektronik", "reference": "B", "status": "production",
        "datasheetInfo": {"electrical": {"capacitance": {"nominal": 100e-6}, "ratedVoltage": 6.3},
                          "part": {"case": "1206"}}}}}) + "\n")
    row = {"original_pn": "X", "original_value": "47uF", "status": "no_substitute", "notes": "n"}
    op = {"capacitance": 47e-6, "voltage": 6.3, "package": "1206"}  # 100µF >= 47µF
    _annotate_true_cap_ceiling(row, op, "Würth Elektronik", tmp_path)
    assert "short" not in row["notes"]  # a larger part exists → no ceiling caveat
