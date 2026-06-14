"""Unit tests for the bom_extract deterministic engine (no LLM, free)."""
from __future__ import annotations

from heaviside.stages.bom_extract import (
    PEAS_CATEGORIES,
    BomComponent,
    extract_bom_from_csv,
    extract_bom_from_rows,
    normalize_category,
)


def test_category_aliases_map_to_peas_keys():
    assert normalize_category("MLCC") == "capacitor"
    assert normalize_category("inductor") == "magnetic"
    assert normalize_category("ferrite bead") == "magnetic"
    assert normalize_category("MOSFET") == "semiconductor"
    assert normalize_category("diode") == "semiconductor"
    assert normalize_category("resistor") == "resistor"
    assert normalize_category("regulator") == "controller"
    assert normalize_category("widget") == ""
    # every mapped value is a real PEAS oneOf key
    for cat in ("capacitor", "magnetic", "semiconductor", "resistor", "controller"):
        assert cat in PEAS_CATEGORIES


def test_rows_parse_to_peas_aligned_components():
    rows = [
        {"RefDes": "C1", "Type": "MLCC", "Value": "4.7uF", "Voltage": "100",
         "Package": "1210", "MPN": "GRM32ER72A475K", "Manufacturer": "Murata", "Dielectric": "X7R"},
        {"Designator": "L1", "Category": "inductor", "Value": "1.5uH",
         "Part Number": "XGL4040-152ME", "Mfr": "Coilcraft"},
        {"ref": "R1", "type": "resistor", "val": "10k", "footprint": "0402"},
    ]
    bom = extract_bom_from_rows(rows)
    by_ref = {c.ref_des: c for c in bom}

    c1 = by_ref["C1"]
    assert c1.category == "capacitor"
    assert c1.mpn == "GRM32ER72A475K"  # 'part'/'mpn' drift handled
    assert c1.manufacturer == "Murata"
    assert c1.rated_voltage == 100.0
    assert c1.technology == "X7R"
    assert abs(c1.value_si - 4.7e-6) < 1e-9  # parsed to SI farads

    assert by_ref["L1"].category == "magnetic"
    assert by_ref["L1"].mpn == "XGL4040-152ME"
    assert abs(by_ref["L1"].value_si - 1.5e-6) < 1e-9

    assert by_ref["R1"].category == "resistor"
    assert abs(by_ref["R1"].value_si - 10_000.0) < 1.0


def test_grouped_refdes_expand():
    rows = [{"RefDes": "C1, C2, C3", "Type": "cap", "Value": "100nF", "Voltage": "50"}]
    bom = extract_bom_from_rows(rows)
    assert sorted(c.ref_des for c in bom) == ["C1", "C2", "C3"]
    assert all(c.value_si and abs(c.value_si - 1e-7) < 1e-12 for c in bom)
    assert all(c.quantity == 1 for c in bom)

    rng = extract_bom_from_rows([{"Designator": "R1-R4", "type": "resistor", "value": "1k"}])
    assert sorted(c.ref_des for c in rng) == ["R1", "R2", "R3", "R4"]


def test_csv_text_extraction():
    csv_text = (
        "Designator,Type,Value,Voltage,Package,MPN,Manufacturer\n"
        "C10,Capacitor,22uF,25,0805,GRM21BR61E226,Murata\n"
        "U1,IC,,,SOT-23,LT80602,Analog Devices\n"
    )
    bom = extract_bom_from_csv(csv_text)
    by_ref = {c.ref_des: c for c in bom}
    assert by_ref["C10"].category == "capacitor"
    assert abs(by_ref["C10"].value_si - 22e-6) < 1e-9
    assert by_ref["U1"].category == "controller"
    assert by_ref["U1"].value_si is None  # ICs have no SI value


def test_blank_refdes_dropped():
    assert extract_bom_from_rows([{"Type": "cap", "Value": "1uF"}]) == []


def test_as_peas_category():
    assert BomComponent("C1", "capacitor").as_peas_category() == "capacitor"
    assert BomComponent("X1", "").as_peas_category() is None
