"""realism_gate — deterministic physics PASS/FAIL over a design.

Engine (``evaluate``): runs every realism check whose inputs are present
(power balance, voltage derating on FET/diode/cap, inductor Isat margin,
output regulation, efficiency sanity, duty bounds, no-negative-losses,
thermal) and folds them into one verdict — PASS only if every applicable
check passes, FAIL on any failure, INCOMPLETE when nothing could run. This
reuses ``pipeline.realism.evaluate_tas`` so the physics lives in one place
and the verdict stays purely deterministic.

LLM layer (``explain``): turns a report's failures into a readable
explanation. It is strictly advisory — it reads the verdict, it never
changes it (CLAUDE.md: the gate's PASS/FAIL is physics, not opinion).
Without an LLM key it returns a deterministic summary of the failed checks.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from heaviside.pipeline.realism import RealismReport


def evaluate(
    tas: Mapping[str, Any],
    *,
    topology: str,
    spec: Mapping[str, Any] | None = None,
) -> RealismReport:
    """Deterministic engine: physics PASS/FAIL/INCOMPLETE for a TAS design."""
    from heaviside.pipeline.realism import evaluate_tas

    return evaluate_tas(tas, topology=topology, spec=spec)


def _failed_checks(report: RealismReport) -> list[Any]:
    from heaviside.pipeline.realism import CheckStatus

    return [c for c in report.checks if c.status == CheckStatus.FAIL]


def _deterministic_summary(report: RealismReport) -> str:
    failures = _failed_checks(report)
    head = f"verdict={report.verdict.value.upper()}"
    if not failures:
        return f"{head}: no failed checks"
    lines = [head + ":"]
    for c in failures:
        bits = [c.name]
        if c.value is not None:
            limit = f", limit={c.limit:.4g}" if isinstance(c.limit, (int, float)) else (
                f", limit={c.limit}" if c.limit is not None else "")
            bits.append(f"(value={c.value:.4g}{limit})")
        if c.detail:
            bits.append(f"- {c.detail}")
        lines.append("  " + " ".join(bits))
    return "\n".join(lines)


def explain(report: RealismReport, *, context: str = "") -> str:
    """Advisory LLM layer: explain why the gate failed (or is incomplete) and
    what to change. Never alters ``report.verdict``. Falls back to a
    deterministic summary of the failed checks without an LLM key."""
    import os

    if not os.environ.get("MOONSHOT_API_KEY"):
        return _deterministic_summary(report)

    import json

    from heaviside.agents.llm_call import call_agent

    failures = _failed_checks(report)
    payload = {
        "verdict": report.verdict.value,
        "context": context,
        "failed_checks": [
            {"name": c.name, "value": c.value, "limit": c.limit,
             "margin": c.margin, "detail": c.detail}
            for c in failures
        ],
        "instructions": (
            "Explain concisely why this design failed the realism gate and what "
            "concrete component/spec change would fix each failure. Do NOT pass "
            "or re-judge the design — the verdict is fixed; only explain it."
        ),
    }
    try:
        return call_agent("reviewer", json.dumps(payload), max_tokens=1024)
    except Exception:
        return _deterministic_summary(report)
