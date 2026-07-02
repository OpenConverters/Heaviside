"""refinement — the bounded design-refinement loop (master-plan step B8).

The designer's outer loop is block-coordinate descent over a discrete catalog:
pick a magnetic + fsw* (sweep), select a real FET for that Vds/Id class, then
the real FET's Vds_rated / Qg_total re-seed ``maximumDrainSourceVoltage`` and
re-cost the switching loss — which can move fsw* and the magnetic. Repeat until
it settles.

Two correctness hazards this wrapper exists to bound:

* **Non-convergence / oscillation.** Negative feedback over a discrete catalog
  (and an LLM that is not an argmin) can oscillate between two near-equal FETs
  forever. The wrapper makes the inner magnetic pick DETERMINISTIC during
  refinement (the LLM suitability pick runs ONCE, after convergence), restoring
  monotonicity, detects an A/B oscillation, and raises
  :class:`RefinementStalled` rather than looping or silently picking one.
* **Cost blowup.** Each iteration is many MKF calls; the loop is hard-capped at
  ``max_iters`` (N≤3) and surfaces a non-converged result loudly.

Convergence = the chosen FET MPN is stable across two consecutive iterations
AND ``|Δfsw*| / fsw* < fsw_rel_tol`` AND the design is feasible at all OPs.

The wrapper is pure: it drives an injected ``step`` callable (one
sweep→pick→reconcile→re-seed pass) so it is fully unit-testable without MKF.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class RefinementStalled(RuntimeError):
    """The refinement loop did not converge within the cost cap, or it
    oscillated between two near-equal designs. Surfaced to the reviewer with the
    full iteration history — never silently resolved by picking one."""

    def __init__(self, history: list[RefinementState], reason: str) -> None:
        self.history = history
        super().__init__(
            f"{reason} after {len(history)} iteration(s): "
            f"FET path {[s.fet_mpn for s in history]}, "
            f"fsw path {[round(s.fsw_hz) for s in history]}"
        )


@dataclass(frozen=True, slots=True)
class RefinementState:
    """The outcome of one refinement pass."""

    iteration: int
    fet_mpn: str
    fsw_hz: float
    feasible: bool
    constraints: dict[str, Any] = field(default_factory=dict)
    feedback: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RefinementResult:
    converged: bool
    final: RefinementState
    history: list[RefinementState]
    reason: str


def _fsw_settled(a: RefinementState, b: RefinementState, tol: float) -> bool:
    if b.fsw_hz <= 0:
        return False
    return abs(a.fsw_hz - b.fsw_hz) / b.fsw_hz < tol


def _is_ab_oscillation(history: list[RefinementState]) -> bool:
    """A,B,A pattern in the FET MPN (with B≠A) over the last three states — the
    classic two-near-equal-FET oscillation."""
    if len(history) < 3:
        return False
    a, b, c = history[-3].fet_mpn, history[-2].fet_mpn, history[-1].fet_mpn
    return a == c and a != b


def refine(
    step: Callable[[RefinementState | None], RefinementState],
    *,
    max_iters: int = 3,
    fsw_rel_tol: float = 0.05,
) -> RefinementResult:
    """Drive the bounded refinement loop.

    ``step(prev)`` performs ONE pass (sweep → deterministic magnetic pick →
    real-FET select → re-seed constraints) and returns a :class:`RefinementState`.
    ``prev`` is ``None`` on the first call, else the previous state (so the step
    can re-seed from ``prev.constraints`` / ``prev.feedback``).

    Returns a converged :class:`RefinementResult`, or raises
    :class:`RefinementStalled` on oscillation or hitting ``max_iters`` without
    convergence (cost cap honored, surfaced — never an infinite loop)."""
    if max_iters < 1:
        raise ValueError(f"max_iters must be >= 1, got {max_iters}")

    history: list[RefinementState] = []
    prev: RefinementState | None = None
    for i in range(max_iters):
        state = step(prev)
        # normalise the iteration index so callers can't desync it
        state = RefinementState(
            iteration=i,
            fet_mpn=state.fet_mpn,
            fsw_hz=state.fsw_hz,
            feasible=state.feasible,
            constraints=state.constraints,
            feedback=state.feedback,
        )
        history.append(state)

        if _is_ab_oscillation(history):
            raise RefinementStalled(history, "oscillation between two near-equal designs")

        if (
            prev is not None
            and state.feasible
            and state.fet_mpn == prev.fet_mpn
            and _fsw_settled(state, prev, fsw_rel_tol)
        ):
            return RefinementResult(
                converged=True,
                final=state,
                history=history,
                reason=(
                    f"FET {state.fet_mpn!r} stable and "
                    f"|Δfsw|/fsw < {fsw_rel_tol:g} at iteration {i}"
                ),
            )
        prev = state

    raise RefinementStalled(
        history, f"did not converge within the cost cap (max_iters={max_iters})"
    )
