"""Unit tests for the realism_gate stage.

The engine (``evaluate``) and the deterministic ``explain`` fallback are
tested without an LLM. The verdict is physics and must never be altered by
the explanation layer.
"""
from __future__ import annotations

from heaviside.pipeline.realism import (
    CheckResult,
    CheckStatus,
    RealismReport,
    RealismVerdict,
)
from heaviside.stages.realism_gate import evaluate, explain


def test_evaluate_returns_report_for_minimal_tas():
    # nothing to check -> honest INCOMPLETE (CLAUDE.md: no fabricated PASS)
    report = evaluate({}, topology="buck")
    assert isinstance(report, RealismReport)
    assert report.verdict is RealismVerdict.INCOMPLETE


def test_explain_fallback_summarizes_failures(monkeypatch):
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    report = RealismReport(
        verdict=RealismVerdict.FAIL,
        checks=(
            CheckResult(
                name="fet_voltage_derating", status=CheckStatus.FAIL,
                value=55.0, limit=60.0, margin=-5.0,
                detail="Vds stress 55V exceeds 1.5x-derated 40V rating",
            ),
            CheckResult(name="power_balance", status=CheckStatus.PASS, value=0.0),
        ),
    )
    text = explain(report)
    assert "FAIL" in text
    assert "fet_voltage_derating" in text
    assert "power_balance" not in text  # only failures are explained


def test_explain_does_not_change_verdict(monkeypatch):
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    report = RealismReport(verdict=RealismVerdict.FAIL, checks=())
    _ = explain(report)
    assert report.verdict is RealismVerdict.FAIL  # frozen verdict untouched


def test_explain_handles_tuple_limit(monkeypatch):
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    report = RealismReport(
        verdict=RealismVerdict.FAIL,
        checks=(CheckResult(
            name="duty_cycle_bounds", status=CheckStatus.FAIL,
            value=0.97, limit=(0.05, 0.95), detail="duty out of bounds",
        ),),
    )
    text = explain(report)  # must not crash on a tuple limit
    assert "duty_cycle_bounds" in text
