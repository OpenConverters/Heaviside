"""Regression: chip-bead (ferrite bead) cross-referencing.

Live prod bug (BLM31PG500SN1, a Murata 50Ω@100MHz ferrite bead):
1. It was classified as a *magnetic/inductor* — its description says "Ferrite
   Chip" (not "…Bead"), and the bare "FERRITE" keyword fell through to the
   magnetic branch — so it searched the inductor catalogue, never the beads.
2. Its impedance (50Ω) lived only in the secondary "Description (IPN)" column
   ("50H 100MHZ"), which the pipeline ignored, so even once classified the
   candidates came back unranked.

These guard the classification, the multi-column value recovery, and the
impedance ranking that surfaces the closest Würth bead.
"""

from __future__ import annotations

from heaviside.pipeline.bom_import import parse_bom_file
from heaviside.pipeline.crossref import CrossRefState
from heaviside.pipeline.crossref_pipeline import (
    _chip_bead_impedance_at_100mhz,
    _infer_component_type,
    _normalize_bom,
    _stage1_prefetch,
    _value_from_description,
)

# --- classification --------------------------------------------------------


def test_ferrite_chip_without_inductance_is_chipbead():
    """ "Ferrite Chip … 3A, 2 Pin" (Murata BLM wording) is a bead, not an inductor."""
    assert (
        _infer_component_type({"description": "Ferrite Chip, 1 Function(s), 3A, 2 Pin(s)"})
        == "chipBead"
    )


def test_ferrite_bead_is_chipbead():
    assert (
        _infer_component_type({"description": "Ferrite Beads Multi-Layer 600Ohm 100MHz 1A 0805"})
        == "chipBead"
    )


def test_ferrite_chip_inductor_stays_magnetic():
    """A ferrite part that DOES declare inductance is an inductor, not a bead."""
    assert _infer_component_type({"description": "Ferrite Chip Inductor 10uH"}) == "magnetic"
    assert (
        _infer_component_type({"description": "Inductor Power Shielded Ferrite 15uH"}) == "magnetic"
    )


def test_classification_reads_secondary_description_columns():
    """The type signal in a secondary column (IPN) must not be ignored: when the
    main part-desc is uninformative but the IPN names a bead, classify on it."""
    row = {"description": "Component", "description_(ipn)": "FERRITE BEAD 600 OHM 100MHZ"}
    assert _infer_component_type(row) == "chipBead"


# --- impedance value extraction --------------------------------------------


def test_chipbead_impedance_from_ohm_description():
    assert _value_from_description("Ferrite Bead 600 Ohm 100MHz", "chipBead") == "600"
    assert _value_from_description("Ferrite 1KOhm 100MHz", "chipBead") == "1K"


def test_chipbead_impedance_from_ipn_shorthand():
    """LumiQuote IPN shorthand "50H" / "50R" means 50Ω for a bead."""
    assert _value_from_description("S FERRITE 50H 100MHZ 3A", "chipBead") == "50"
    assert _value_from_description("FER 330 B0603", "chipBead") is None  # bare number, no unit
    assert _value_from_description("BEAD 120R 100MHZ", "chipBead") == "120"


def test_chipbead_impedance_does_not_match_mhz():
    """The "H" of "MHz" must NOT be read as an ohm value."""
    assert _value_from_description("Ferrite 100MHz only", "chipBead") is None


# --- end-to-end on the real BLM31PG500SN1 row ------------------------------


def test_blm_ferrite_bead_classified_valued_and_matched():
    from pathlib import Path

    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "lumiquote_bom_v1.xlsx"
    bom = parse_bom_file(fixture.read_bytes(), fixture.name)
    blm = [r for r in bom if r.get("original_mpn") == "BLM31PG500SN1"]
    assert blm, "BLM31PG500SN1 must be in the v1 fixture"
    nb = _normalize_bom(blm)
    row = nb[0]
    # (1) classified as a chip bead, not an inductor
    assert row["component_type"] == "chipBead"
    # (2) impedance recovered from the IPN column → 50 (Ω)
    assert row["value"] == "50"
    # (3) prefetch returns Würth beads ranked by impedance near 50 Ω
    state = _stage1_prefetch(CrossRefState(source_bom=nb, target_manufacturer="Würth Elektronik"))
    cands = state.candidates_by_ref[row["ref_des"]]
    assert cands, "expected Würth chip-bead candidates"
    top_z = _chip_bead_impedance_at_100mhz(cands[0])
    assert top_z is not None and abs(top_z - 50.0) <= 15.0  # closest bead is ~50 Ω


# --- classification from the catalogue, by subtype (ABT #874) ---------------


def test_a_catalogued_bead_classifies_as_chipbead_not_magnetic():
    """A bare bead MPN with no description resolves through the catalogue. The
    magnetics file holds inductors AND beads, so answering with the FILE's
    category filed a 600 Ω bead as a generic magnetic — and the prefetch then
    offered it power inductors (74438356010, a 1 µH WE-PD). The record's
    electrical subtype is what tells them apart.
    """
    from heaviside.pipeline.guardrails import lookup_mpn_category

    assert lookup_mpn_category("BLM21AG601SN1") == "chipBead"


def test_a_bom_bead_with_its_taping_code_resolves_and_is_valued():
    """The customer BOM carries the orderable BLM21AG601SN1D; the catalogue the
    base BLM21AG601SN1 (ABT #873). It must classify AND recover its impedance,
    or the candidates come back unranked."""
    row = _normalize_bom([{"ref_des": "FB1", "part": "BLM21AG601SN1D"}])[0]
    assert row["component_type"] == "chipBead"
    assert row["value"] and row["value"].endswith("Ω")


def test_a_bead_row_is_offered_beads_not_inductors():
    """End-to-end on the real catalogue: every candidate must be a chip bead
    with an impedance, and none an inductor with an inductance."""
    nb = _normalize_bom([{"ref_des": "FB1", "part": "BLM21AG601SN1D"}])
    state = _stage1_prefetch(
        CrossRefState(source_bom=nb, target_manufacturer="Würth Elektronik")
    )
    cands = state.candidates_by_ref["FB1"]
    assert cands, "expected Würth chip-bead candidates"
    for cand in cands[:10]:
        electrical = cand["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"][0]
        assert electrical["subtype"] == "chipBead"
        assert "inductance" not in electrical
    top_z = _chip_bead_impedance_at_100mhz(cands[0])
    assert top_z is not None and abs(top_z - 600.0) <= 60.0


def test_a_ferrite_bead_type_column_is_not_routed_to_magnetic():
    """A BOM whose type column literally says "ferrite_bead" was aliased onto
    "magnetic" — the same wrong pool by a different route."""
    for spelling in ("ferrite_bead", "Ferrite Bead", "chip bead", "BEAD"):
        row = _normalize_bom([{"ref_des": "FB1", "type": spelling, "part": "X"}])[0]
        assert row["component_type"] == "chipBead", spelling
