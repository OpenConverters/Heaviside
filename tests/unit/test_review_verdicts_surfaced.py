"""The adversarial (Ray + Nicola) review verdict must be surfaced, never
discarded (the G01 defect class).

Two independent sites dropped it:
  * converter_designer set ``review = None`` and never captured the panel;
    ``_stage4_adversarial_review`` returned only the outcome, discarding the
    verdicts.
  * the crossref correction loop read ``review_verdicts[-1]`` (always Nicola)
    and gated pass/fail with ``any`` over the whole history (an early approval
    masked a later rejection).
"""

from __future__ import annotations

from types import SimpleNamespace

from heaviside.pipeline.crossref_pipeline import _latest_ray_verdict

# ---------------------------------------------------------------------------
# crossref: the gating reviewer is Ray, and it is the LATEST Ray verdict
# ---------------------------------------------------------------------------


def test_latest_ray_verdict_ignores_nicola() -> None:
    verdicts = [
        {"reviewer": "ray", "verdict": "APPROVED", "objections": []},
        {"reviewer": "nicola", "verdict": "REJECTED", "objections": [{"i": "n"}]},
    ]
    # [-1] would be Nicola; the loop must key off Ray.
    assert _latest_ray_verdict(verdicts)["reviewer"] == "ray"
    assert _latest_ray_verdict(verdicts)["verdict"] == "APPROVED"


def test_latest_ray_verdict_takes_most_recent_round() -> None:
    """After a correction loop re-review, the LATEST Ray verdict decides —
    an early approval must not mask a later rejection."""
    verdicts = [
        {"reviewer": "ray", "verdict": "APPROVED", "objections": []},
        {"reviewer": "nicola", "verdict": "APPROVED", "objections": []},
        {"reviewer": "ray", "verdict": "REJECTED", "objections": [{"i": "r2"}]},
        {"reviewer": "nicola", "verdict": "APPROVED", "objections": []},
    ]
    latest = _latest_ray_verdict(verdicts)
    assert latest["verdict"] == "REJECTED"
    assert latest["objections"] == [{"i": "r2"}]


def test_latest_ray_verdict_empty_when_absent() -> None:
    assert _latest_ray_verdict([]) == {}
    assert _latest_ray_verdict([{"reviewer": "nicola", "verdict": "APPROVED"}]) == {}


# ---------------------------------------------------------------------------
# designer: _stage4_adversarial_review returns (outcome, panel) and surfaces
# the panel decision in diagnostics
# ---------------------------------------------------------------------------


def test_stage4_returns_and_surfaces_rejected_panel(monkeypatch) -> None:
    from heaviside.pipeline import full_design
    from heaviside.stages import reviewer_panel

    panel = reviewer_panel.PanelResult(
        decision="REJECTED",
        approved=False,
        verdicts=[
            reviewer_panel.ReviewVerdict("ray", "REJECTED", [{"issue": "undersized FET"}]),
            reviewer_panel.ReviewVerdict("nicola", "APPROVED", []),
        ],
    )
    monkeypatch.setattr(reviewer_panel, "review", lambda *a, **k: panel)

    outcome = full_design.DesignOutcome(
        pick=SimpleNamespace(topology=SimpleNamespace(name="buck")),
        tas={"topology": {}},
        verdict_dict={"verdict": "PASS"},
        gatekeeper=None,
        report=None,
        fsw_optimal=200_000.0,
        diagnostics=("realized",),
    )

    reviewed, returned_panel = full_design._stage4_adversarial_review(outcome)

    # The panel is returned (not discarded) and its decision is surfaced.
    assert returned_panel is panel
    assert returned_panel.decision == "REJECTED"
    assert any("REJECTED" in d for d in reviewed.diagnostics), reviewed.diagnostics
