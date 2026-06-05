"""Unit tests for heaviside.pipeline.bom_import — CSV/TSV/XLSX BOM ingestion."""

from __future__ import annotations

import io

import pytest

from heaviside.pipeline.bom_import import BomImportError, parse_bom_file


def test_csv_messy_headers_canonicalised():
    raw = (
        b"Ref Des,Manufacturer Part Number,Mfr,Category,Value,Voltage\n"
        b"L1,744066047,Wurth,inductor,4.7uH,\n"
        b"C1,GRM188R61A106KE69D,Murata,capacitor,10uF,25V\n"
    )
    bom = parse_bom_file(raw, "bom.csv")
    assert len(bom) == 2
    assert bom[0]["ref_des"] == "L1"
    assert bom[0]["original_mpn"] == "744066047"
    assert bom[0]["manufacturer"] == "Wurth"
    assert bom[0]["component_type"] == "inductor"
    # Empty cell (no voltage on L1) must NOT create a key.
    assert "rated_voltage" not in bom[0]
    assert bom[1]["rated_voltage"] == "25V"


def test_csv_semicolon_delimiter_sniffed():
    bom = parse_bom_file(b"MPN;Manufacturer;Type\nABC123;TDK;capacitor\n", "x.csv")
    assert bom == [
        {"original_mpn": "ABC123", "manufacturer": "TDK", "component_type": "capacitor"}
    ]


def test_csv_tab_delimiter():
    bom = parse_bom_file(b"Part\tMfr\nXYZ\tVishay\n", "x.tsv")
    assert bom[0]["original_mpn"] == "XYZ"
    assert bom[0]["manufacturer"] == "Vishay"


def test_unknown_columns_carried_through_lowercased():
    bom = parse_bom_file(b"MPN,Tolerance,DK Part\nR1,1%,foo\n", "x.csv")
    assert bom[0]["original_mpn"] == "R1"
    assert bom[0]["tolerance"] == "1%"
    assert bom[0]["dk_part"] == "foo"


def test_xlsx_roundtrip():
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["MPN", "Manufacturer", "Category"])
    ws.append(["744066047", "Wurth", "inductor"])
    ws.append(["GRM188", "Murata", "capacitor"])
    buf = io.BytesIO()
    wb.save(buf)
    bom = parse_bom_file(buf.getvalue(), "bom.xlsx")
    assert len(bom) == 2
    assert bom[0]["original_mpn"] == "744066047"
    assert bom[1]["component_type"] == "capacitor"


def test_empty_file_raises():
    with pytest.raises(BomImportError):
        parse_bom_file(b"", "e.csv")


def test_no_part_number_column_raises():
    with pytest.raises(BomImportError, match="part-number"):
        parse_bom_file(b"foo,bar\n1,2\n", "nohdr.csv")


def test_legacy_xls_rejected():
    with pytest.raises(BomImportError, match="xls"):
        parse_bom_file(b"anything", "old.xls")


def test_blank_rows_skipped():
    raw = b"MPN,Mfr\nA1,X\n\n  ,\nA2,Y\n"
    bom = parse_bom_file(raw, "x.csv")
    assert [c["original_mpn"] for c in bom] == ["A1", "A2"]
