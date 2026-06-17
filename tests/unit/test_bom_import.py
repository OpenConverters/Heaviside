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
    assert bom == [{"original_mpn": "ABC123", "manufacturer": "TDK", "component_type": "capacitor"}]


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
    # allow_llm=False isolates the deterministic path (no LLM consulted).
    with pytest.raises(BomImportError, match="part-number"):
        parse_bom_file(b"foo,bar\n1,2\n", "nohdr.csv", allow_llm=False)


def test_mfg_pn_alias_picks_manufacturer_pn_over_internal():
    # Real-world PLM export: leading-space ` MFG_PN` is the manufacturer part
    # number; `WW_PN` is an internal house number. The deterministic alias must
    # resolve MFG_PN → original_mpn (not the internal one), no LLM needed.
    raw = (
        b'ITEM#,Qty,WW_PN,DESCRIPTION," VALUE",MFG," MFG_PN"\n'
        b"1,2,WW123,Cap,100nF,Murata,GRM155R71C104KA88D\n"
    )
    bom = parse_bom_file(raw, "bom.csv", allow_llm=False)
    assert bom[0]["original_mpn"] == "GRM155R71C104KA88D"
    assert bom[0]["manufacturer"] == "Murata"
    # the internal number is still carried through, just not as the MPN
    assert bom[0]["ww_pn"] == "WW123"


@pytest.mark.parametrize("header", ["MPN", "Mfr PN", "MFG PN", "Manufacturer PN"])
def test_mpn_header_spellings(header):
    raw = f"{header},Mfr\nABC123,TDK\n".encode()
    assert parse_bom_file(raw, "x.csv", allow_llm=False)[0]["original_mpn"] == "ABC123"


def test_llm_fallback_maps_novel_headers(monkeypatch):
    # Headers the alias table doesn't know: deterministic parse fails, the LLM
    # column-mapper names which column is the MPN. Mock the agent so the test is
    # hermetic — the values still come from the real cells, never the LLM.
    import heaviside.agents.llm_call as llm

    def fake_call_agent_json(name, message, **kw):
        assert name == "bom-header-mapper"
        return {
            "original_mpn": "Cmp_Number",
            "manufacturer": "Maker",
            "description": "Detail",
            "rationale": "Cmp_Number is the orderable MPN; IntRef is internal",
        }

    monkeypatch.setattr(llm, "call_agent_json", fake_call_agent_json)
    raw = b"IntRef,Pieces,Cmp_Number,Maker,Detail\nA001,2,GRM155R71C104KA88D,Murata,cap\n"
    bom = parse_bom_file(raw, "bom.csv")  # allow_llm defaults True
    assert bom[0]["original_mpn"] == "GRM155R71C104KA88D"  # verbatim from the cell
    assert bom[0]["manufacturer"] == "Murata"
    assert bom[0]["description"] == "cap"


def test_llm_fallback_unavailable_resurfaces_part_number_error(monkeypatch):
    import heaviside.agents.llm_call as llm

    def boom(*a, **k):
        raise llm.LLMCallError("no MOONSHOT_API_KEY or OPENAI_API_KEY in environment")

    monkeypatch.setattr(llm, "call_agent_json", boom)
    with pytest.raises(BomImportError, match="part-number"):
        parse_bom_file(b"IntRef,Pieces,Detail\nA001,2,cap\n", "bom.csv")


def test_llm_fallback_without_mpn_still_raises(monkeypatch):
    # The LLM maps other columns but finds no MPN column → re-raises (correct:
    # better than mislabelling an internal number as the manufacturer MPN).
    import heaviside.agents.llm_call as llm

    monkeypatch.setattr(
        llm, "call_agent_json",
        lambda *a, **k: {"original_mpn": None, "manufacturer": "Maker"},
    )
    with pytest.raises(BomImportError, match="part-number"):
        parse_bom_file(b"IntRef,Pieces,Maker\nA001,2,Acme\n", "bom.csv")


def test_legacy_xls_rejected():
    with pytest.raises(BomImportError, match="xls"):
        parse_bom_file(b"anything", "old.xls")


def test_blank_rows_skipped():
    raw = b"MPN,Mfr\nA1,X\n\n  ,\nA2,Y\n"
    bom = parse_bom_file(raw, "x.csv")
    assert [c["original_mpn"] for c in bom] == ["A1", "A2"]
