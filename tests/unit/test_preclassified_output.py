"""Regression: pre-classified (keep_original) components must appear in the
output, even when EVERY component is pre-classified.

Bug: _stage3_crossref early-returns on an empty bom_for_llm ("all components
pre-classified, nothing to crossref") BEFORE the merge-back of keep_original
rows, so the outcome had 0 components and looked like a failure (seen on the
ADAQ7767-1 eval-board BOM — all rows already-target-mfr / not-fitted)."""

from __future__ import annotations

from heaviside.pipeline import crossref_pipeline as cp
from heaviside.pipeline.crossref import CrossRefOutcome, CrossRefState


def test_all_preclassified_still_emits_components():
    bom = [
        {"ref_des": "U1", "component_type": "controller", "original_mpn": "ADAQ7767-1",
         "manufacturer": "Würth Elektronik", "value": ""},
        {"ref_des": "C1", "component_type": "capacitor", "original_mpn": "X",
         "manufacturer": "Würth Elektronik", "value": "100nF"},
    ]
    st = CrossRefState(source_bom=bom, target_manufacturer="Würth Elektronik")
    st = cp._stage2_preclassify(st)
    st = cp._stage3_crossref(st)  # empty bom_for_llm → early-return path
    out = CrossRefOutcome.from_state(st)
    assert len(out.components) == 2, "kept_original components must not be dropped"
    # keep_original is folded into 'exact' at the output boundary — a kept part
    # (already the target manufacturer) is an exact match to itself. The user
    # doesn't care about the keep_original/exact distinction.
    assert all(c.status.value == "exact" for c in out.components)


def test_merge_is_idempotent():
    # The correction loop re-runs stage 3; merging twice must not duplicate rows.
    bom = [{"ref_des": "C1", "component_type": "capacitor", "original_mpn": "X",
            "manufacturer": "Würth Elektronik", "value": "1uF"}]
    st = CrossRefState(source_bom=bom, target_manufacturer="Würth Elektronik")
    st = cp._stage2_preclassify(st)
    cp._merge_preclassified(st)
    cp._merge_preclassified(st)
    assert len(st.crossref_result) == 1


def test_mixed_bom_keeps_preclassified_and_crossrefs_rest(monkeypatch):
    # One already-Würth (preclassified) + one substitutable; the LLM handles the
    # latter, and the kept-original still appears.
    monkeypatch.setattr(
        cp, "call_agent_json",
        lambda *a, **k: {"crossref": [{"ref_des": "C2", "component_type": "capacitor",
                                       "status": "no_substitute"}]},
    )
    bom = [
        {"ref_des": "C1", "component_type": "capacitor", "original_mpn": "A",
         "manufacturer": "Würth Elektronik", "value": "1uF"},
        {"ref_des": "C2", "component_type": "capacitor", "original_mpn": "B",
         "manufacturer": "Murata", "value": "1uF"},
    ]
    st = CrossRefState(source_bom=bom, target_manufacturer="Würth Elektronik")
    st = cp._stage2_preclassify(st)
    st = cp._stage3_crossref(st)
    refs = {r["ref_des"] for r in st.crossref_result}
    assert refs == {"C1", "C2"}
