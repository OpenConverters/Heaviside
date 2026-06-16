"""Bounded refinement loop (master-plan step B8).

The pure wrapper is driven by an injected step callable so convergence,
oscillation, and the cost cap are tested deterministically without MKF.
"""
from __future__ import annotations

import pytest

from heaviside.stages import refinement as rf


def _state(it, mpn, fsw, feasible=True, **fb):
    return rf.RefinementState(iteration=it, fet_mpn=mpn, fsw_hz=fsw,
                              feasible=feasible, feedback=dict(fb))


def _stepper(sequence):
    """Return a step() that yields the given states in order, ignoring prev."""
    it = iter(sequence)

    def step(prev):
        return next(it)
    return step


def test_converges_when_fet_stable_and_fsw_settled():
    seq = [
        _state(0, "FET_A", 300_000),
        _state(1, "FET_A", 305_000),  # same FET, <5% fsw change, feasible
    ]
    res = rf.refine(_stepper(seq), max_iters=3, fsw_rel_tol=0.05)
    assert res.converged
    assert res.final.fet_mpn == "FET_A"
    assert len(res.history) == 2


def test_reseed_then_converge_on_pass_two():
    """A design that re-seeds Vds after pass 1 (different FET) then settles."""
    seq = [
        _state(0, "FET_LOWV", 280_000, feasible=False),  # failed derating
        _state(1, "FET_HIV", 300_000),                    # re-seeded, now feasible
        _state(2, "FET_HIV", 301_000),                    # stable ⇒ converged
    ]
    res = rf.refine(_stepper(seq), max_iters=3)
    assert res.converged
    assert res.final.fet_mpn == "FET_HIV"


def test_oscillation_raises_stalled():
    seq = [
        _state(0, "FET_A", 300_000),
        _state(1, "FET_B", 320_000),
        _state(2, "FET_A", 300_000),  # A,B,A ⇒ oscillation
    ]
    with pytest.raises(rf.RefinementStalled, match="oscillation") as ei:
        rf.refine(_stepper(seq), max_iters=5)
    assert [s.fet_mpn for s in ei.value.history] == ["FET_A", "FET_B", "FET_A"]


def test_cost_cap_raises_when_not_converged():
    # always a different FET ⇒ never converges; capped at 3
    seq = [_state(i, f"FET_{i}", 300_000 + 1000 * i) for i in range(3)]
    with pytest.raises(rf.RefinementStalled, match="cost cap"):
        rf.refine(_stepper(seq), max_iters=3)


def test_not_converged_if_infeasible_even_when_stable():
    # same FET + settled fsw but infeasible must NOT count as converged
    seq = [
        _state(0, "FET_A", 300_000, feasible=False),
        _state(1, "FET_A", 301_000, feasible=False),
        _state(2, "FET_A", 301_500, feasible=False),
    ]
    with pytest.raises(rf.RefinementStalled):
        rf.refine(_stepper(seq), max_iters=3)


def test_large_fsw_jump_prevents_convergence():
    seq = [
        _state(0, "FET_A", 200_000),
        _state(1, "FET_A", 400_000),  # same FET but 100% fsw jump ⇒ not settled
        _state(2, "FET_A", 250_000),  # still moving
    ]
    with pytest.raises(rf.RefinementStalled, match="cost cap"):
        rf.refine(_stepper(seq), max_iters=3, fsw_rel_tol=0.05)


def test_step_receives_prev_for_reseeding():
    seen = []

    def step(prev):
        seen.append(prev)
        n = len(seen)
        return _state(n - 1, "FET_A", 300_000)

    rf.refine(step, max_iters=2)
    assert seen[0] is None                    # first call: no prev
    assert isinstance(seen[1], rf.RefinementState)  # second: previous state
    assert seen[1].fet_mpn == "FET_A"


def test_max_iters_validated():
    with pytest.raises(ValueError):
        rf.refine(_stepper([]), max_iters=0)
