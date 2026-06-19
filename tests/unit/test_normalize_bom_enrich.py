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
