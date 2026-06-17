"""reviewed_stage — wrap any LLM-producing stage in a Ray + Nicola review loop.

The pattern, applied to the high-risk decision stages (the ones whose errors
poison everything downstream — competitor extraction, reverse-engineering,
topology constraints, magnetic pick):

1. produce an LLM output;
2. have the full adversarial panel (Ray + Nicola) judge it for THIS stage's job;
3. if they reject, RE-RUN the stage with their objections fed back into the
   prompt — the LLM gets a second (bounded) shot, informed by the critique;
4. on exhaustion, the caller chooses: surface (raise) or proceed with the best
   effort and the unresolved objections recorded — never a silent pass.

This generalises the CR pipeline's existing review→correct→re-review loop into a
single reusable helper, so every gated stage gets the same "verify the LLM, and
if the reviewers don't buy it, try again with their comments" behaviour.

A reviewer that cannot produce a verdict (LLM unreachable / unparseable even
after retries) raises ``LLMCallError`` straight through — per CLAUDE.md a stage
whose review could not run is a hard failure, not a quiet approval.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from heaviside.stages import reviewer_panel

__all__ = ["ReviewedOutcome", "ReviewedStageError", "review_and_retry"]


class ReviewedStageError(RuntimeError):
    """Raised (only with ``on_unresolved='raise'``) when the panel never
    approves the stage output within the retry budget."""


@dataclass
class ReviewedOutcome[T]:
    """Result of a reviewed stage: the (possibly best-effort) output plus the
    panel's final decision and any unresolved objections."""

    output: T
    approved: bool
    rounds: int
    panel: reviewer_panel.PanelResult
    objections: list[str] = field(default_factory=list)


def _objection_lines(panel: reviewer_panel.PanelResult) -> list[str]:
    """Flatten the non-approving reviewers' objections to plain strings."""
    out: list[str] = []
    for v in panel.verdicts:
        if v.verdict == "APPROVED":
            continue
        for o in v.objections:
            out.append(f"{v.reviewer}: {o}" if not str(o).startswith(v.reviewer) else str(o))
    return out


def _feedback_block(panel: reviewer_panel.PanelResult) -> str:
    """Render the panel's objections as a prompt addendum for the re-run, so the
    LLM addresses each specific complaint rather than guessing what was wrong."""
    lines = ["The reviewers REJECTED your previous answer. Address every point:"]
    for v in panel.verdicts:
        if v.verdict == "APPROVED" or not v.objections:
            continue
        lines.append(f"\n{v.reviewer.upper()} ({v.verdict}):")
        lines.extend(f"  - {o}" for o in v.objections)
    lines.append("\nReturn a corrected answer in the same format.")
    return "\n".join(lines)


def review_and_retry[T](
    produce: Callable[[str | None], T],
    present: Callable[[T], dict[str, Any]],
    *,
    scope: str,
    title: str,
    max_rounds: int = 2,
    reviewers: tuple[str, ...] = reviewer_panel.REVIEWERS,
    progress: Any = None,
    on_unresolved: str = "return",
) -> ReviewedOutcome[T]:
    """Run ``produce`` → Ray+Nicola review → retry-with-objections, bounded.

    Parameters
    ----------
    produce:
        ``feedback -> output``. Called once per round; ``feedback`` is ``None``
        on the first round and the reviewers' objection block on later rounds
        (the stage should append it to its LLM prompt).
    present:
        ``output -> JSON-able payload`` shown to the reviewers. Keep it to the
        decision-relevant facts (the stage's actual output), not the whole world.
    scope / title:
        The reviewers behave differently per scope; ``scope`` tells them exactly
        what they are judging at this stage (and what is out of scope), ``title``
        labels the review.
    max_rounds:
        Total attempts (1 = produce once, review, no retry). Default 2.
    on_unresolved:
        ``"return"`` (default) hands back the best-effort output with
        ``approved=False`` and the unresolved objections; ``"raise"`` raises
        :class:`ReviewedStageError`.

    Raises ``LLMCallError`` if a reviewer cannot produce a verdict at all.
    """
    if max_rounds < 1:
        raise ValueError("review_and_retry: max_rounds must be >= 1")

    def _say(msg: str) -> None:
        if progress is not None:
            with contextlib.suppress(Exception):
                progress(msg, -1)

    feedback: str | None = None
    output: T | None = None
    panel: reviewer_panel.PanelResult | None = None
    for rnd in range(1, max_rounds + 1):
        if rnd > 1:
            _say(f"{title}: reviewers rejected — retrying with their comments (round {rnd})")
        output = produce(feedback)
        # Note: reviewer_panel's own ``progress`` is a per-reviewer (name, idx,
        # total) hook — a different shape from this stage-level (msg, pct) one —
        # so it is intentionally NOT forwarded here.
        panel = reviewer_panel.review(
            present(output), scope=scope, title=title, reviewers=reviewers
        )
        if panel.approved:
            return ReviewedOutcome(output=output, approved=True, rounds=rnd, panel=panel)
        feedback = _feedback_block(panel)

    assert panel is not None  # max_rounds >= 1 guarantees the loop ran
    objections = _objection_lines(panel)
    if on_unresolved == "raise":
        raise ReviewedStageError(
            f"{title}: Ray+Nicola did not approve after {max_rounds} rounds; "
            f"unresolved objections: {objections}"
        )
    return ReviewedOutcome(
        output=output, approved=False, rounds=max_rounds, panel=panel, objections=objections
    )
