"""Zero-token unit tests for the value-integrity invariant checker.

The checker is the machine-graded half of the FAE eval: it flags a substitution
that violates a physically-required invariant (wrong value, dielectric
downgrade) without any datasheet lookup. These tests pin it against synthetic
result rows so the FAE orchestrator can trust it to auto-grade live output.
"""

from __future__ import annotations

from heaviside.pipeline.crossref_invariants import check_result, check_row


def test_330nH_for_1p5uH_is_flagged():
    row = {
        "ref_des": "L1",
        "component_type": "magnetic",
        "original_value": "1.5uH",
        "substitute_pn": "744383560R33",
        "substitute_value": "330nH",
        "status": "partial",
    }
    inv = {
        "category": "magnetic",
        "original_value_si": 1.5e-6,
        "primary_value_accept_lo": 0.8,
        "primary_value_accept_hi": 1.25,
    }
    v = check_row(row, inv)
    assert len(v) == 1 and v[0].parameter == "primary_value"
    assert "below" in v[0].detail


def test_matching_value_is_clean():
    row = {
        "ref_des": "L1",
        "component_type": "magnetic",
        "original_value": "1.5uH",
        "substitute_pn": "74438356015",
        "substitute_value": "1.5uH",
        "status": "recommended",
    }
    inv = {"category": "magnetic", "original_value_si": 1.5e-6,
           "primary_value_accept_lo": 0.8, "primary_value_accept_hi": 1.25}
    assert check_row(row, inv) == []


def test_no_substitute_is_not_a_violation():
    # The tool declining to substitute is not an invariant violation.
    row = {"ref_des": "L1", "component_type": "magnetic", "original_value": "1.5uH",
           "substitute_pn": None, "status": "no_substitute"}
    inv = {"category": "magnetic", "original_value_si": 1.5e-6,
           "primary_value_accept_lo": 0.8, "primary_value_accept_hi": 1.25}
    assert check_row(row, inv) == []


def test_resistor_wrong_value_flagged():
    row = {"ref_des": "R1", "component_type": "resistor", "original_value": "47k",
           "substitute_pn": "X", "substitute_value": "10k", "status": "partial"}
    inv = {"category": "resistor", "original_value_si": 47000.0,
           "primary_value_accept_lo": 0.95, "primary_value_accept_hi": 1.05}
    v = check_row(row, inv)
    assert len(v) == 1 and v[0].parameter == "primary_value"


def test_over_ceiling_capacitor_flagged():
    row = {"ref_des": "C1", "component_type": "capacitor", "original_value": "1uF",
           "substitute_pn": "X", "substitute_value": "10uF", "status": "recommended"}
    inv = {"category": "capacitor", "original_value_si": 1e-6,
           "primary_value_accept_lo": 0.8, "primary_value_accept_hi": 4.0}
    v = check_row(row, inv)
    assert len(v) == 1 and "above" in v[0].detail


def test_dielectric_downgrade_flagged():
    row = {"ref_des": "C1", "component_type": "capacitor", "original_value": "0.1uF",
           "substitute_pn": "X", "substitute_value": "0.1uF",
           "substitute_dielectric": "Y5V", "status": "recommended"}
    inv = {"category": "capacitor", "original_value_si": 1e-7, "dielectric_class_min": "X7R"}
    v = check_row(row, inv)
    assert any(x.parameter == "dielectric_class" for x in v)


def test_check_result_matches_by_ref():
    rows = [
        {"ref_des": "L1", "component_type": "magnetic", "original_value": "1.5uH",
         "substitute_pn": "bad", "substitute_value": "330nH", "status": "partial"},
        {"ref_des": "R1", "component_type": "resistor", "original_value": "47k",
         "substitute_pn": "ok", "substitute_value": "47k", "status": "exact"},
    ]
    invs = {
        "L1": {"category": "magnetic", "original_value_si": 1.5e-6,
               "primary_value_accept_lo": 0.8, "primary_value_accept_hi": 1.25},
        "R1": {"category": "resistor", "original_value_si": 47000.0,
               "primary_value_accept_lo": 0.95, "primary_value_accept_hi": 1.05},
    }
    v = check_result(rows, invs)
    assert len(v) == 1 and v[0].ref_des == "L1"


# ── the status vocabulary is closed, and enforced BEFORE assembly ────────────


def test_an_invented_status_does_not_discard_the_whole_run():
    """A row the cross-referencer marked "informational" reached
    CrossRefOutcome.from_state, whose coerce() rightly refuses an unknown value —
    and killed a completed run at the final step, with the UI still showing the
    last stage it had announced ("Learning from this run").

    The vocabulary stays closed; it is now enforced where the LLM's answer lands,
    so an invented verdict costs one row a demotion instead of the whole job."""
    from heaviside.pipeline.crossref import CrossRefOutcome, CrossRefState
    from heaviside.pipeline.crossref_pipeline import _restore_valid_statuses

    state = CrossRefState(source_bom=[], target_manufacturer="Würth Elektronik")
    state.crossref_result = [
        {"ref_des": "CMP#0", "status": "informational", "substitute_pn": None},
        {"ref_des": "CMP#1", "status": "recommended", "substitute_pn": "742792040"},
    ]

    _restore_valid_statuses(state)

    assert state.crossref_result[0]["status"] == "no_substitute"
    assert state.crossref_result[1]["status"] == "recommended"  # untouched
    # And assembly, which used to raise, now completes.
    assert len(CrossRefOutcome.from_state(state).components) == 2


def test_the_invented_status_is_reported_not_swallowed():
    """Demoting quietly would hide that the model is ignoring the contract."""
    from heaviside.pipeline.crossref import CrossRefState
    from heaviside.pipeline.crossref_pipeline import _restore_valid_statuses

    state = CrossRefState(source_bom=[], target_manufacturer="Würth Elektronik")
    state.crossref_result = [{"ref_des": "C7", "status": "review", "substitute_pn": None}]

    _restore_valid_statuses(state)

    assert any("status the engine does not define" in d for d in state.diagnostics)
    assert "review" in state.diagnostics[0]
    assert "review" in state.crossref_result[0]["notes"]


def test_a_legacy_alias_is_still_honoured():
    """keep_original/already_target fold to exact — they are not inventions."""
    from heaviside.pipeline.crossref import CrossRefState
    from heaviside.pipeline.crossref_pipeline import _restore_valid_statuses

    state = CrossRefState(source_bom=[], target_manufacturer="Würth Elektronik")
    state.crossref_result = [
        {"ref_des": "C1", "status": "keep_original", "substitute_pn": "X"},
        {"ref_des": "C2", "status": "already_target", "substitute_pn": "Y"},
    ]

    _restore_valid_statuses(state)

    assert [r["status"] for r in state.crossref_result] == ["exact", "exact"]
    assert not state.diagnostics


def test_a_demoted_row_can_still_be_rescued_on_evidence():
    """Why no_substitute and not a guessed grade: the deterministic rescue and
    the param check reconsider exactly the rows in that state, so a substitute
    that genuinely checks out is promoted on evidence rather than on the LLM's
    invented word."""
    from heaviside.pipeline.crossref import CrossRefState
    from heaviside.pipeline.crossref_pipeline import _restore_valid_statuses

    state = CrossRefState(source_bom=[], target_manufacturer="Würth Elektronik")
    state.crossref_result = [
        {"ref_des": "C1", "status": "informational", "substitute_pn": "742792040"}
    ]
    _restore_valid_statuses(state)
    row = state.crossref_result[0]
    assert row["status"] == "no_substitute"
    assert row["substitute_pn"] == "742792040", "the pick is kept for re-grading"
