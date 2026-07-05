"""Regression: a PLM/eval-board BOM (no ref_des or category columns — the
ref-designator lives in a LOCATION column, the type only in the description)
must still cross-reference. Previously every row defaulted to ref_des '?',
collapsing onto one identity → 'all components pre-classified, nothing to
crossref' → 0 components (the ADAQ7767-1 bug)."""

from __future__ import annotations

from heaviside.pipeline.crossref_pipeline import _infer_component_type, _normalize_bom


def test_missing_refdes_does_not_collapse():
    bom = [
        {"location": "C11", "original_mpn": "CL05A106MP8", "description": "CAP CER 10UF 10V"},
        {"location": "C12", "original_mpn": "CL10B105KP8", "description": "CAP CER 1UF 10V"},
        {"location": "A1", "original_mpn": "ADA4807", "description": "IC-ADI AMP"},
    ]
    out = _normalize_bom(bom)
    refs = [c["ref_des"] for c in out]
    assert refs == ["C11", "C12", "A1"]          # taken from LOCATION
    assert len(set(refs)) == 3                    # unique, no '?' collapse


def test_blank_refdes_gets_unique_synthetic_id():
    bom = [{"original_mpn": "X", "description": "CAP CER 1UF"},
           {"original_mpn": "Y", "description": "CAP CER 1UF"}]
    out = _normalize_bom(bom)
    refs = [c["ref_des"] for c in out]
    assert len(set(refs)) == 2  # not both '?'


def test_category_inferred_from_description():
    assert _infer_component_type({"description": "CAP CER 10UF 10V X5R"}) == "capacitor"
    assert _infer_component_type({"description": "RES 10K 1% 0402"}) == "resistor"
    assert _infer_component_type({"description": "IND SHIELDED POWER", "value": "3.6UH"}) == "magnetic"
    assert _infer_component_type({"description": "IND FERRITE BEAD 600OHM"}) == "chipBead"
    # non-substitutable: left blank so they fall to keep_original, not mis-typed
    assert _infer_component_type({"description": "IC-ADI 180MEGHZ AMP"}) == ""
    assert _infer_component_type({"description": "CONN-PCB COAX SMB"}) == ""
    assert _infer_component_type({"description": "DIODE LOW LEAKAGE"}) == ""


def test_existing_category_is_not_overwritten():
    out = _normalize_bom([{"ref_des": "C1", "component_type": "capacitor",
                           "description": "IND something"}])
    assert out[0]["component_type"] == "capacitor"  # explicit wins over inference


def test_value_and_package_recovered_from_description():
    """LumiQuote/distributor BOMs have no value or package column — the value
    ("15uH"/"10uF") and chip size ("0402") live in the description. _normalize_bom
    must recover both so ranking can value-filter and footprint-fit has a source
    size (the 'Test BOM -V2.xlsx' failure: warnings on every row, nothing
    referenced)."""
    bom = [
        # An MPN NOT in the internal catalogue, so this row exercises the
        # description-recovery path specifically (a real SRR1260-150M is now in
        # the DB and would backfill its canonical "15µH" instead — a different,
        # equally-valid path covered elsewhere).
        {"ref_des": "L1", "original_mpn": "SRR1260-150M-DISTRIBUTORONLY",
         "description": "Inductor Power Shielded Wirewound 15uH 20% 5A 0.027Ohm DCR"},
        {"ref_des": "L2", "original_mpn": "0402CS-10NXGLU",
         "description": "Inductor RF Chip 0.01uH 2% 250MHz 0.48A 0.2Ohm DCR 0402"},
        {"ref_des": "C1", "original_mpn": "GRM155",
         "description": "Capacitor Ceramic 10uF 25V X5R 0805"},
        {"ref_des": "R1", "original_mpn": "CRCW",
         "description": "Resistor Chip 10k 1% 0603"},
    ]
    nb = _normalize_bom(bom)
    by = {c["ref_des"]: c for c in nb}
    assert by["L1"]["value"] == "15uH"            # inductance, not the 0.027Ohm DCR
    assert by["L2"]["value"] == "0.01uH"
    assert by["L2"]["package"] == "0402"
    assert by["C1"]["value"] == "10uF"
    assert by["C1"]["package"] == "0805"
    assert by["R1"]["value"] == "10k"
    assert by["R1"]["package"] == "0603"


def test_inductor_value_not_confused_by_khz_or_ohm():
    """The magnetic value regex must skip '1KHz' and 'DCR Ohm' tokens."""
    nb = _normalize_bom([{
        "ref_des": "L1", "component_type": "magnetic", "original_mpn": "X",
        "description": "Inductor 22uH 20% 1KHz 25Q-Factor 5A 0.027Ohm DCR",
    }])
    assert nb[0]["value"] == "22uH"
