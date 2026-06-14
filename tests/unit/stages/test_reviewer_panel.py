"""Unit tests for the reviewer_panel stage.

The ``aggregate`` engine (verdict folding) is deterministic and tested
in full here. The real-LLM ``review`` path runs Ray + Nicola for real
(no mocks) and skips cleanly without an API key.
"""
from __future__ import annotations

import os

import pytest

from heaviside.stages.reviewer_panel import (
    PanelResult,
    ReviewVerdict,
    aggregate,
    review,
)


def _v(reviewer, verdict):
    return ReviewVerdict(reviewer=reviewer, verdict=verdict)


def test_all_approved_is_approved():
    r = aggregate([_v("ray", "APPROVED"), _v("nicola", "APPROVED")])
    assert isinstance(r, PanelResult)
    assert r.decision == "APPROVED"
    assert r.approved is True


def test_any_rejection_rejects():
    r = aggregate([_v("ray", "APPROVED"), _v("nicola", "REJECTED")])
    assert r.decision == "REJECTED"
    assert r.approved is False


def test_rejection_dominates_incomplete():
    r = aggregate([_v("ray", "INCOMPLETE"), _v("nicola", "REJECTED")])
    assert r.decision == "REJECTED"


def test_incomplete_when_no_rejection():
    r = aggregate([_v("ray", "APPROVED"), _v("nicola", "INCOMPLETE")])
    assert r.decision == "INCOMPLETE"
    assert r.approved is False


def test_empty_panel_raises():
    with pytest.raises(ValueError, match="no verdicts"):
        aggregate([])


def test_invalid_verdict_raises():
    with pytest.raises(ValueError, match="invalid verdict"):
        aggregate([_v("ray", "MAYBE")])


@pytest.mark.skipif(not os.environ.get("MOONSHOT_API_KEY"), reason="MOONSHOT_API_KEY not set")
def test_review_runs_real_panel():
    payload = {
        "topology": "buck",
        "verdict": {"vout": 3.3, "iout": 2.0, "efficiency": 0.92},
        "bom": [
            {"ref_des": "Q1", "category": "semiconductor", "mpn": "BSC0902NS"},
            {"ref_des": "L1", "category": "magnetic", "mpn": "744325240"},
            {"ref_des": "Cout", "category": "capacitor", "value": "47uF"},
        ],
        "diagnostics": ["steady-state sim converged"],
    }
    r = review(
        payload,
        scope="POWER-STAGE AUTO-DESIGN — topology, magnetics, BOM, steady-state sim, "
        "realism. Control loop, gate drive, protection, EMI, PCB OUT OF SCOPE.",
        title="CONVERTER DESIGN REVIEW",
    )
    assert isinstance(r, PanelResult)
    assert {v.reviewer for v in r.verdicts} == {"ray", "nicola"}
    assert r.decision in ("APPROVED", "REJECTED", "INCOMPLETE")
