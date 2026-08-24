"""A row categorised AFTER the candidate prefetch must still get candidates.

The user-reported failure (job 1b73eec81dbd, TestBOM_3.xlsx → Würth Elektronik)
returned "PASS" at 0/7 coverage: every row came back ``no_substitute`` with the
cross-referencer's own words, "No the candidate list provided for chip bead
substitution".

Stage 1 keys the candidate prefetch off ``component_type``, and every row on that
BOM arrived uncategorised. Stage 1.6 then DID resolve the category from Digi-Key's
taxonomy — but nothing re-ran the prefetch, so those rows reached the LLM with an
empty candidate list while the catalogue held 278 Würth chip beads and 3 645 Würth
capacitors (ABT #872).

These tests pin the two halves of the fix: the re-prefetch itself, and the refusal
to call a run that evaluated nothing a PASS (ABT #879).
"""

from __future__ import annotations

import pytest

from heaviside.pipeline.crossref import CrossRefState
from heaviside.pipeline.crossref_pipeline import (
    _gate_on_evaluability,
    _stage1_7_reprefetch_late,
)


def _bead_envelope(mpn: str, ohms: float) -> dict:
    return {
        "magnetic": {
            "manufacturerInfo": {
                "name": "Würth Elektronik",
                "reference": mpn,
                "datasheetInfo": {
                    "part": {"partNumber": mpn},
                    "electrical": [
                        {
                            "subtype": "chipBead",
                            "dcResistance": {"maximum": 0.15},
                            "impedancePoints": [
                                {"frequency": 100e6, "impedance": {"magnitude": ohms}}
                            ],
                        }
                    ],
                },
            }
        }
    }


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    """A one-file catalogue of Würth chip beads, pointed at by the env var the
    prefetch reads."""
    import json

    (tmp_path / "magnetics.ndjson").write_text(
        "\n".join(
            json.dumps(_bead_envelope(mpn, ohms))
            for mpn, ohms in (("742792040", 600.0), ("74279220", 120.0))
        )
    )
    monkeypatch.setenv("HEAVISIDE_TAS_DATA_DIR", str(tmp_path))
    return tmp_path


# ── the re-prefetch (ABT #872) ───────────────────────────────────────────────


def test_a_late_classified_row_gets_its_candidates(catalogue):
    """The exact shape of the bug: stage 1 left the row empty because it had no
    category, the librarian supplied one, and the prefetch must run again."""
    row = {"ref_des": "FB1", "original_mpn": "BLM21AG601SN1D", "value": "600Ω"}
    state = CrossRefState(source_bom=[row], target_manufacturer="Würth Elektronik")
    state.candidates_by_ref["FB1"] = []          # what stage 1 leaves behind
    row["component_type"] = "chipBead"           # what stage 1.6 then resolves
    state.late_classified_refs.add("FB1")

    state = _stage1_7_reprefetch_late(state)

    assert state.candidates_by_ref["FB1"], "the row must now carry candidates"
    assert state.candidates_by_ref["FB1"][0]["magnetic"]["manufacturerInfo"][
        "reference"
    ] == "742792040", "and they must be ranked to the 600 Ω original"


def test_rows_that_were_not_late_classified_are_left_alone(catalogue):
    """The re-prefetch is targeted: it must not disturb rows stage 1 handled."""
    late = {"ref_des": "FB1", "original_mpn": "X", "component_type": "chipBead"}
    early = {"ref_des": "FB2", "original_mpn": "Y", "component_type": "chipBead"}
    state = CrossRefState(source_bom=[late, early], target_manufacturer="Würth Elektronik")
    sentinel: list[dict] = [{"already": "prefetched"}]
    state.candidates_by_ref["FB2"] = sentinel
    state.late_classified_refs.add("FB1")

    state = _stage1_7_reprefetch_late(state)

    assert state.candidates_by_ref["FB2"] is sentinel
    assert state.candidates_by_ref["FB1"]


def test_nothing_late_classified_is_a_no_op(catalogue):
    state = CrossRefState(source_bom=[], target_manufacturer="Würth Elektronik")
    assert _stage1_7_reprefetch_late(state).candidates_by_ref == {}


def test_a_row_still_without_candidates_is_reported(catalogue):
    """Honest reporting: the row was categorised but the target makes nothing of
    that category, and the run must say so rather than go quiet."""
    row = {"ref_des": "U1", "original_mpn": "LM317", "component_type": "mosfet"}
    state = CrossRefState(source_bom=[row], target_manufacturer="Würth Elektronik")
    state.late_classified_refs.add("U1")

    state = _stage1_7_reprefetch_late(state)

    assert not state.candidates_by_ref.get("U1")
    assert any("still have no" in d for d in state.diagnostics)


# ── the evaluability gate (ABT #879) ─────────────────────────────────────────


def test_a_run_that_evaluated_nothing_cannot_pass():
    """0/7 coverage with no candidates for any row is a failure, not a pass."""
    state = CrossRefState(source_bom=[], target_manufacturer="Würth Elektronik")
    state.passed = True
    state.crossref_result = [
        {"ref_des": f"CMP#{i}", "status": "no_substitute", "substitute_pn": None}
        for i in range(7)
    ]

    _gate_on_evaluability(state)

    assert state.passed is False
    assert any("no substitution was actually evaluated" in d for d in state.diagnostics)


def test_an_honest_no_substitute_from_a_real_candidate_list_still_passes():
    """A row judged against real candidates is a legitimate answer — the gate
    must not turn every no_substitute into a failure."""
    state = CrossRefState(source_bom=[], target_manufacturer="Würth Elektronik")
    state.passed = True
    state.crossref_result = [{"ref_des": "C1", "status": "no_substitute", "substitute_pn": None}]
    state.candidates_by_ref["C1"] = [{"a": "candidate"}]

    _gate_on_evaluability(state)

    assert state.passed is True


def test_one_evaluated_row_is_enough_to_keep_the_verdict():
    state = CrossRefState(source_bom=[], target_manufacturer="Würth Elektronik")
    state.passed = True
    state.crossref_result = [
        {"ref_des": "C1", "status": "exact", "substitute_pn": "885012206095"},
        {"ref_des": "C2", "status": "no_substitute", "substitute_pn": None},
    ]

    _gate_on_evaluability(state)

    assert state.passed is True


def test_an_empty_result_is_not_gated():
    state = CrossRefState(source_bom=[], target_manufacturer="Würth Elektronik")
    state.passed = True
    _gate_on_evaluability(state)
    assert state.passed is True


# ── a part whose MPN lives only in part.partNumber (ABT #877) ────────────────


def test_an_envelope_with_no_reference_still_reports_its_mpn():
    """39 657 shipped records (35 966 capacitors, 3 243 resistors, 448 magnetics
    — TDK, Taiyo Yuden, Samsung, KEMET, YAGEO, Murata) carry their MPN ONLY in
    datasheetInfo.part.partNumber and leave manufacturerInfo.reference empty.
    The guardrail index has always keyed on both; the crossref pipeline read
    only `reference`, so those parts were anonymous to it and the two paths
    disagreed about what a part's MPN is."""
    from heaviside.pipeline.crossref_pipeline import _envelope_reference

    env = {
        "capacitor": {
            "manufacturerInfo": {
                "name": "Taiyo Yuden",
                "datasheetInfo": {"part": {"partNumber": "MSASP063EB5475MFNA01"}},
            }
        }
    }
    assert _envelope_reference(env, "capacitor") == "MSASP063EB5475MFNA01"


def test_an_explicit_reference_still_wins():
    from heaviside.pipeline.crossref_pipeline import _envelope_reference

    env = {
        "capacitor": {
            "manufacturerInfo": {
                "name": "Murata",
                "reference": "GRM21BR71H104KA01L",
                "datasheetInfo": {"part": {"partNumber": "SOMETHING-ELSE"}},
            }
        }
    }
    assert _envelope_reference(env, "capacitor") == "GRM21BR71H104KA01L"


def test_a_blank_reference_falls_through_rather_than_returning_empty():
    from heaviside.pipeline.crossref_pipeline import _envelope_reference

    env = {
        "capacitor": {
            "manufacturerInfo": {
                "reference": "   ",
                "datasheetInfo": {"part": {"partNumber": "C0805C104K5RACTU"}},
            }
        }
    }
    assert _envelope_reference(env, "capacitor") == "C0805C104K5RACTU"


def test_an_envelope_with_no_mpn_at_all_is_none():
    from heaviside.pipeline.crossref_pipeline import _envelope_reference

    assert _envelope_reference({"capacitor": {"manufacturerInfo": {}}}, "capacitor") is None
