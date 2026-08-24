"""One part, however the BOM spells it — the "Resolve part numbers" step.

The reported BOM named the SAME Murata bead four ways:

    BLM21AG601SN1D      the orderable number
    BLM-21A-G601SN1D    retyped with separators
    BLM21/AG6/01SN1D    wrapped differently
    BLM21-AG601/SN1D    and again

and got four different answers. CMP#0 resolved its original fully; CMP#1-3 came
back ``ORIGINAL_UNVERIFIED`` with Z@100MHz, DCR and Irms all blank — not because
the part differs, it does not, but because each stage matched MPNs its own way.
``_normalize_bom`` and ``lookup_part_fields`` understood separators; the param
check knew only exact + packaging-base. Three implementations of one question,
disagreeing.

The fix is to answer it ONCE: the row's MPN is rewritten to the spelling the
catalogue uses, before any stage looks anything up, so none of them can
disagree afterwards. These tests are the contract for that — every spelling in
the reported BOM must come out as the same part, with the same specs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from heaviside.pipeline.crossref import CrossRefState
from heaviside.pipeline.crossref_pipeline import (
    _needs_part_resolution,
    _normalize_bom,
    _stage_param_check,
)

# Exactly the chip-bead rows of the reported BOM (job 1b73eec81dbd).
BEAD_SPELLINGS = [
    "BLM21AG601SN1D",
    "BLM-21A-G601SN1D",
    "BLM21/AG6/01SN1D",
    "BLM21-AG601/SN1D",
]
CANONICAL = "BLM21AG601SN1"
CAP_SPELLINGS = ["EMK105BJ105KV-F", "EMK105BJ105KVF"]


@pytest.fixture(scope="module")
def normalised():
    rows = [
        {"ref_des": f"CMP#{i}", "original_mpn": mpn}
        for i, mpn in enumerate(BEAD_SPELLINGS + CAP_SPELLINGS)
    ]
    return _normalize_bom(rows)


def test_every_spelling_of_the_bead_resolves_to_one_part(normalised):
    """The headline contract: four spellings, one part number."""
    beads = normalised[: len(BEAD_SPELLINGS)]
    assert {r["original_mpn"] for r in beads} == {CANONICAL}


def test_every_spelling_is_classified_the_same(normalised):
    beads = normalised[: len(BEAD_SPELLINGS)]
    assert {r.get("component_type") for r in beads} == {"chipBead"}


def test_every_spelling_gets_the_same_value(normalised):
    """If one row can state the original's impedance, all four must — they are
    the same physical part."""
    beads = normalised[: len(BEAD_SPELLINGS)]
    values = {r.get("value") for r in beads}
    assert len(values) == 1 and all(values), f"expected one shared value, got {values}"


def test_the_bom_s_own_spelling_is_not_lost(normalised):
    """Rewriting the MPN must not erase what the engineer typed — the report
    still has to be able to show the row as it arrived."""
    for row, written in zip(normalised, BEAD_SPELLINGS + CAP_SPELLINGS, strict=True):
        if row["original_mpn"].lower() != written.lower():
            assert row["original_mpn_as_written"] == written


def test_the_capacitor_spellings_also_collapse(normalised):
    """Same rule for the separator the BOM dropped from a Taiyo Yuden number."""
    caps = normalised[len(BEAD_SPELLINGS) :]
    assert len({r["original_mpn"] for r in caps}) == 1


def test_a_canonicalised_row_is_not_sent_to_the_llm_resolver():
    """Deterministic first: a spelling the catalogue can identify costs nothing
    and cannot be hallucinated, so it must never reach the LLM resolver."""
    for row in _normalize_bom([{"ref_des": "FB1", "original_mpn": s} for s in BEAD_SPELLINGS]):
        assert not _needs_part_resolution(row), row["original_mpn"]


def test_a_mangled_mpn_the_catalogue_cannot_identify_does_reach_the_resolver():
    """...and what is left over — genuinely mangled AND genuinely unknown — is
    exactly what an LLM with a web search can settle and a lookup cannot."""
    row = _normalize_bom([{"ref_des": "U1", "original_mpn": "LTC-3897/IUHF#PBF"}])[0]
    assert not row.get("component_type"), "precondition: the catalogue cannot identify it"
    assert _needs_part_resolution(row)


def test_a_legitimate_hyphenated_mpn_is_left_alone():
    """Most hyphens in a part number are real. Flagging every one would send
    half of every BOM to the LLM for no reason."""
    row = _normalize_bom([{"ref_des": "C1", "original_mpn": "EMK105BJ105KV-F"}])[0]
    assert not _needs_part_resolution(row)


def test_all_four_spellings_get_an_identical_verdict():
    """End of the chain, and the thing the user actually saw go wrong: the same
    part, substituted by the same part, must be graded the same way — same
    status, same guardrail fires, no ORIGINAL_UNVERIFIED on three of four."""
    rows = _normalize_bom(
        [{"ref_des": f"CMP#{i}", "original_mpn": m} for i, m in enumerate(BEAD_SPELLINGS)]
    )
    state = CrossRefState(source_bom=rows, target_manufacturer="Würth Elektronik")
    state.crossref_result = [
        {
            "ref_des": r["ref_des"],
            "component_type": r["component_type"],
            "original_pn": r["original_mpn"],
            "substitute_pn": "74279220601",
            "status": "recommended",
            "notes": "",
        }
        for r in rows
    ]
    _stage_param_check(state)

    statuses = {r["status"] for r in state.crossref_result}
    fires = {tuple(sorted(r.get("guardrail_fires") or [])) for r in state.crossref_result}
    assert len(statuses) == 1, f"one part graded {len(statuses)} ways: {statuses}"
    assert len(fires) == 1, f"one part fired {len(fires)} different guardrail sets: {fires}"
    assert not any("ORIGINAL_UNVERIFIED" in f for f in fires), (
        "the original IS in the catalogue under every one of these spellings"
    )


def test_the_reported_bom_file_resolves_consistently():
    """The same contract, driven from the file the user actually uploaded."""
    from heaviside.pipeline.bom_import import parse_bom_file

    path = Path(__file__).resolve().parents[1] / "fixtures" / "TestBOM_3_original.xlsx"
    if not path.is_file():  # pragma: no cover - fixture ships with the repo
        pytest.skip("recovered BOM fixture not present")
    rows = _normalize_bom(parse_bom_file(path.read_bytes(), path.name))
    beads = [r for r in rows if r.get("component_type") == "chipBead"]
    assert len(beads) == 4, "the four bead rows must all classify"
    assert {r["original_mpn"] for r in beads} == {CANONICAL}
