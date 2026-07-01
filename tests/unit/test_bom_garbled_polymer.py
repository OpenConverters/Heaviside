"""(a) garbled-row detection + LLM refinement, and (b) polymer↔polymer
cross-chemistry allowance — the two fixes that flipped um3491's C21 (a 330µF
Panasonic POSCAP whose atypical row mis-parsed to mpn='330 uF')."""
from heaviside.pipeline.crossref_pipeline import _is_polymer_cap, _capacitor_technology_family
from heaviside.stages import bom_extract
from heaviside.stages.bom_extract import _row_looks_garbled, BomComponent, extract_bom_from_rows


# --- (a) garbled-row detector -------------------------------------------

def _c(mpn):
    return BomComponent(ref_des="C21", category="capacitor", mpn=mpn)


def test_mpn_that_is_a_value_is_garbled():
    assert _row_looks_garbled(_c("330 uF"))
    assert _row_looks_garbled(_c("330uF"))
    assert _row_looks_garbled(_c("10 kΩ"))
    assert _row_looks_garbled(_c("4.7µF"))
    assert _row_looks_garbled(_c("470 nH"))


def test_real_mpn_is_not_garbled():
    assert not _row_looks_garbled(_c("6TAE330ML"))        # the real Panasonic part
    assert not _row_looks_garbled(_c("GRM188R61H225KE11J"))
    assert not _row_looks_garbled(_c("885012206126"))
    assert not _row_looks_garbled(_c(None))
    assert not _row_looks_garbled(_c(""))


def test_garbled_rows_are_refined_from_llm_others_kept(monkeypatch):
    """extract_bom_from_pdf keeps well-parsed deterministic rows but re-reads a
    garbled row (mpn≈value) from the LLM census."""
    det_rows = [
        {"ref_des": "C1", "category": "capacitor", "mpn": "GRM188", "value": "22uF"},
        {"ref_des": "C21", "category": "capacitor", "mpn": "330 uF", "value": "330uF"},  # garbled
    ] + [{"ref_des": f"R{i}", "category": "resistor", "mpn": f"RC{i}"} for i in range(1, 12)]
    llm_rows = [
        {"ref_des": "C21", "category": "capacitor", "mpn": "6TAE330ML",
         "manufacturer": "Panasonic", "value": "330uF", "rated_voltage": "6.3"},
    ]
    text = "C1 C21 " + " ".join(f"R{i}" for i in range(1, 12))
    monkeypatch.setattr("heaviside.stages.bom_table.parse_bom_table", lambda p: det_rows)
    monkeypatch.setattr(bom_extract, "_extract_full_bom_rows", lambda t, r, **k: llm_rows)

    bom = bom_extract.extract_bom_from_pdf("um3491.pdf", reference="um3491", pdf_text=text)
    by = {c.ref_des: c for c in bom}
    assert by["C21"].mpn == "6TAE330ML"          # garbled row refined from LLM
    assert by["C21"].manufacturer == "Panasonic"
    assert by["C1"].mpn == "GRM188"              # good deterministic row kept


# --- (b) polymer cross-chemistry ----------------------------------------

def test_polymer_detection():
    assert _is_polymer_cap("tantalum polymer")
    assert _is_polymer_cap("aluminum polymer")
    assert _is_polymer_cap("POSCAP")
    assert _is_polymer_cap("OS-CON")
    assert not _is_polymer_cap("aluminum electrolytic")   # wet -> not a polymer
    assert not _is_polymer_cap("X7R")
    assert not _is_polymer_cap(None)


def test_polymer_families_still_differ_by_metal():
    # the family gate still separates the anode metals — the polymer allowance is
    # applied on top in the ranking, not by collapsing the families.
    assert _capacitor_technology_family("tantalum polymer") == "tantalum"
    assert _capacitor_technology_family("aluminum polymer") == "aluminum"
