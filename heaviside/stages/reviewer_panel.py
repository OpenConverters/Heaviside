"""reviewer_panel — the Ray + Nicola adversarial review, as one stage.

The named reviewers (Ray = engineering veteran, Nicola = quality inspector)
gate both the cross-reference pipeline and the converter designer. This
stage is the single place that runs the panel and turns its verdicts into
one decision, so every pipeline reviews the same way.

Deterministic engine (``aggregate``): combine individual reviewer verdicts
into one panel decision — REJECTED if anyone rejects, INCOMPLETE if anyone
flags incomplete, APPROVED only when everyone approves. No LLM, fully
unit-tested.

LLM layer (``review``): run each reviewer for real (no mocks) via the
existing ``call_agent_json`` + ``normalize_reviewer_verdict`` plumbing,
scoped exactly like the designer's stage-4 review. A reviewer that can't
produce a valid verdict raises ``LLMCallError`` — a design without its
real Ray+Nicola review is not a result (CLAUDE.md: no silent fallback,
never fabricate a "review").
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any

REVIEWERS: tuple[str, ...] = ("ray", "nicola")
_VALID = ("APPROVED", "REJECTED", "INCOMPLETE")


@dataclass
class ReviewVerdict:
    reviewer: str
    verdict: str  # one of _VALID
    objections: list[Any] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PanelResult:
    decision: str  # one of _VALID
    approved: bool
    verdicts: list[ReviewVerdict]


def aggregate(verdicts: list[ReviewVerdict]) -> PanelResult:
    """Deterministic engine: fold reviewer verdicts into one decision.
    Any REJECTED -> REJECTED; else any INCOMPLETE -> INCOMPLETE; else
    (all APPROVED) -> APPROVED. Empty input is an error, not an approval."""
    if not verdicts:
        raise ValueError("reviewer_panel.aggregate: no verdicts to aggregate")
    states = [v.verdict for v in verdicts]
    for s in states:
        if s not in _VALID:
            raise ValueError(f"reviewer_panel.aggregate: invalid verdict {s!r}")
    if "REJECTED" in states:
        decision = "REJECTED"
    elif "INCOMPLETE" in states:
        decision = "INCOMPLETE"
    else:
        decision = "APPROVED"
    return PanelResult(decision=decision, approved=decision == "APPROVED", verdicts=verdicts)


def _build_message(payload: dict[str, Any], *, scope: str, title: str) -> str:
    import json

    return f"[SCOPE: {scope}]\n\n{title}\n\n{json.dumps(payload, indent=2)}"


def review(
    payload: dict[str, Any],
    *,
    scope: str,
    title: str = "DESIGN REVIEW",
    reviewers: tuple[str, ...] = REVIEWERS,
    max_tokens: int = 8192,
    max_retries: int = 2,
    progress: Any = None,
) -> PanelResult:
    """LLM layer: run the real reviewer panel over ``payload`` and aggregate.

    ``scope`` is the in/out-of-scope preamble (the reviewers behave
    differently for a CR check vs a full power-stage design). Each reviewer
    is called for real; an invalid verdict propagates as ``LLMCallError``
    (no fabricated review). Returns the aggregated :class:`PanelResult`.

    ``progress`` (optional) is called as ``progress(reviewer_name, index, total)``
    just before each reviewer runs, so a caller can surface per-reviewer stage
    progress (Ray, then Nicola)."""
    from heaviside.agents.llm_call import call_agent_json, normalize_reviewer_verdict

    msg = _build_message(payload, scope=scope, title=title)
    verdicts: list[ReviewVerdict] = []
    for i, name in enumerate(reviewers):
        if progress is not None:
            with contextlib.suppress(Exception):
                progress(name, i, len(reviewers))
        data = call_agent_json(
            name, msg, max_tokens=max_tokens, max_retries=max_retries, json_mode=True
        )
        data = normalize_reviewer_verdict(data, name)
        data["reviewer"] = name
        verdicts.append(ReviewVerdict(
            reviewer=name,
            verdict=data["verdict"],
            objections=list(data.get("objections") or []),
            raw=data,
        ))
    return aggregate(verdicts)
