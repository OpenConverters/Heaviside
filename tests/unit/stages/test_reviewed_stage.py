"""Unit tests for heaviside.stages.reviewed_stage — the Ray+Nicola
review-and-retry wrapper. Reviewers are mocked, so these are hermetic."""

from __future__ import annotations

import pytest

from heaviside.stages import reviewed_stage, reviewer_panel
from heaviside.stages.reviewed_stage import (
    ReviewedStageError,
    review_and_retry,
)


def _panel(approved: bool, objections=None):
    verdict = "APPROVED" if approved else "REJECTED"
    return reviewer_panel.PanelResult(
        decision=verdict,
        approved=approved,
        verdicts=[
            reviewer_panel.ReviewVerdict(reviewer="ray", verdict=verdict,
                                         objections=list(objections or [])),
            reviewer_panel.ReviewVerdict(reviewer="nicola", verdict="APPROVED",
                                         objections=[]),
        ],
    )


def test_approved_first_round_no_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(reviewed_stage.reviewer_panel, "review",
                        lambda payload, **kw: _panel(True))
    out = review_and_retry(
        lambda fb: calls.append(fb) or {"v": 1},
        lambda o: o, scope="s", title="T",
    )
    assert out.approved and out.rounds == 1
    assert calls == [None]  # produced once, no feedback


def test_reject_then_approve_feeds_objections_back(monkeypatch):
    seen_feedback = []
    panels = iter([_panel(False, ["Vds too low for the off-state spike"]), _panel(True)])
    monkeypatch.setattr(reviewed_stage.reviewer_panel, "review",
                        lambda payload, **kw: next(panels))

    def produce(fb):
        seen_feedback.append(fb)
        return {"attempt": len(seen_feedback)}

    out = review_and_retry(produce, lambda o: o, scope="s", title="T", max_rounds=2)
    assert out.approved and out.rounds == 2
    # round 1 feedback is None; round 2 carries the reviewer objection text
    assert seen_feedback[0] is None
    assert "Vds too low" in seen_feedback[1]
    assert "REJECTED" in seen_feedback[1]


def test_unresolved_returns_best_effort_with_objections(monkeypatch):
    monkeypatch.setattr(reviewed_stage.reviewer_panel, "review",
                        lambda payload, **kw: _panel(False, ["still wrong"]))
    out = review_and_retry(lambda fb: {"x": 1}, lambda o: o,
                           scope="s", title="T", max_rounds=2)
    assert out.approved is False
    assert out.rounds == 2
    assert any("still wrong" in o for o in out.objections)
    assert out.output == {"x": 1}  # best effort still returned


def test_unresolved_raise_mode(monkeypatch):
    monkeypatch.setattr(reviewed_stage.reviewer_panel, "review",
                        lambda payload, **kw: _panel(False, ["nope"]))
    with pytest.raises(ReviewedStageError, match="did not approve"):
        review_and_retry(lambda fb: {"x": 1}, lambda o: o,
                         scope="s", title="T", max_rounds=2, on_unresolved="raise")


def test_reviewer_failure_propagates(monkeypatch):
    from heaviside.agents.llm_call import LLMCallError

    def boom(payload, **kw):
        raise LLMCallError("reviewer unreachable")

    monkeypatch.setattr(reviewed_stage.reviewer_panel, "review", boom)
    with pytest.raises(LLMCallError):
        review_and_retry(lambda fb: {"x": 1}, lambda o: o, scope="s", title="T")


def test_max_rounds_validated():
    with pytest.raises(ValueError, match="max_rounds"):
        review_and_retry(lambda fb: 1, lambda o: o, scope="s", title="T", max_rounds=0)


def test_present_shapes_payload(monkeypatch):
    seen = {}
    monkeypatch.setattr(reviewed_stage.reviewer_panel, "review",
                        lambda payload, **kw: seen.update(payload) or _panel(True))
    review_and_retry(lambda fb: {"raw": "big"}, lambda o: {"summary": o["raw"]},
                     scope="s", title="T")
    assert seen == {"summary": "big"}  # reviewers saw the presented payload, not raw
