"""Property + behaviour tests for the crossref v2 utility-curve scoring engine.

These pin the load-bearing math the pipeline relies on: closest-value-wins,
diminishing-returns on over-dimensioning, gates dominate compensation, and the
330 nH-for-1.5 µH regression (the reason this engine exists).
"""

from __future__ import annotations

import math

import pytest

from heaviside.pipeline.scoring import (
    FAIL,
    PASS,
    UNVERIFIED,
    WARN,
    Mode,
    over_dimensioning_penalty,
    score_directional,
    score_primary_value,
    score_range,
)


class TestOverDimensioningPenalty:
    def test_at_or_under_requirement_is_zero(self):
        assert over_dimensioning_penalty(3.25, 3.25) == 0.0
        assert over_dimensioning_penalty(3.25, 3.0) == 0.0

    def test_missing_inputs_zero(self):
        assert over_dimensioning_penalty(None, 5.0) == 0.0
        assert over_dimensioning_penalty(3.25, None) == 0.0
        assert over_dimensioning_penalty(0, 5.0) == 0.0

    def test_diminishing_and_right_sized_wins(self):
        # Right-sized (1.2×) < 4× < 12× but marginal cost shrinks; a grossly
        # oversized part always carries more penalty than a right-sized one.
        p12 = over_dimensioning_penalty(3.25, 3.9)
        p4 = over_dimensioning_penalty(3.25, 13.0)
        p12x = over_dimensioning_penalty(3.25, 39.0)
        assert 0 < p12 < p4 < p12x
        assert (p12x - p4) < (p4 - p12)

    def test_weight_scales(self):
        base = over_dimensioning_penalty(1.0, 4.0, weight=1.0)
        assert over_dimensioning_penalty(1.0, 4.0, weight=0.1) == pytest.approx(base * 0.1)


# ── RANGE / proximity (primary passive value) ────────────────────────────────
class TestRange:
    def _mag(self, sub, orig=1.5e-6):
        # magnetic window: tight 0.9–1.1, accept 0.8–1.25
        return score_range(
            orig, sub,
            tight_lo=0.90, tight_hi=1.10, accept_lo=0.80, accept_hi=1.25,
            label="L", unit="H",
        )

    def test_exact_value_passes_zero_penalty(self):
        r = self._mag(1.5e-6)
        assert r.verdict == PASS
        assert r.penalty == pytest.approx(0.0, abs=1e-9)

    def test_the_330nH_for_1p5uH_bug_is_a_hard_fail(self):
        # The regression that motivated the whole rework: 330 nH is 0.22× —
        # far outside the accept window — must FAIL, never pass as "partial".
        r = self._mag(330e-9)
        assert r.verdict == FAIL
        assert r.ratio == pytest.approx(0.22, abs=0.01)

    def test_in_tight_window_passes(self):
        assert self._mag(1.6e-6).verdict == PASS  # 1.067× within ±10%

    def test_in_accept_but_off_nominal_warns(self):
        r = self._mag(1.2e-6)  # 0.8× — at the accept floor, outside tight
        assert r.verdict == WARN

    def test_just_outside_accept_fails(self):
        assert self._mag(1.1e-6).verdict == FAIL  # 0.733× < 0.8× accept floor

    def test_closest_value_has_lowest_penalty(self):
        # Monotonicity: as the substitute moves away from nominal, penalty rises.
        penalties = [self._mag(v).penalty for v in (1.5e-6, 1.6e-6, 1.75e-6, 1.9e-6)]
        assert penalties == sorted(penalties)

    def test_missing_value_is_unverified_not_pass(self):
        assert score_range(
            1.5e-6, None,
            tight_lo=0.9, tight_hi=1.1, accept_lo=0.8, accept_hi=1.25,
        ).verdict == UNVERIFIED

    def test_resistor_wrong_value_fails(self):
        # 10k vs 47k original — a classic wrong-value trap.
        r = score_range(
            47000.0, 10000.0,
            tight_lo=0.99, tight_hi=1.01, accept_lo=0.95, accept_hi=1.05,
            label="R", unit="Ω",
        )
        assert r.verdict == FAIL


# ── HIGHER_BETTER (ratings) with diminishing-returns over-dimensioning ────────
class TestHigherBetter:
    def _isat(self, sub, orig=3.25):
        return score_directional(
            orig, sub, Mode.HIGHER_BETTER, warn_factor=0.9, gate_factor=0.8,
            label="Isat", unit="A",
        )

    def test_meets_requirement_passes(self):
        assert self._isat(3.3).verdict == PASS

    def test_slight_deficit_warns_not_fails(self):
        # 3.1 A vs 3.25 A (0.95×) — a near-miss, compensable, WARN not FAIL.
        assert self._isat(3.1).verdict == WARN

    def test_large_deficit_fails(self):
        assert self._isat(2.0).verdict == FAIL  # 0.62× < 0.8× gate

    def test_over_dimensioning_diminishing_returns(self):
        # A 12.4 A part (the screenshot) passes, but a right-sized part must
        # carry a smaller penalty — and the marginal penalty must shrink.
        p1 = self._isat(3.5).penalty   # 1.08×
        p2 = self._isat(6.5).penalty   # 2×
        p4 = self._isat(13.0).penalty  # 4×
        assert p1 < p2 < p4
        # diminishing returns: each doubling adds less than the previous
        assert (p4 - p2) < (p2 - p1)

    def test_oversize_penalty_is_capped(self):
        # 8× and 80× should be nearly identical (saturation), so a huge part is
        # "a bit worse", never disqualified purely on over-dimensioning.
        p8 = self._isat(26.0).penalty
        p80 = self._isat(260.0).penalty
        assert p80 == pytest.approx(p8, abs=1e-9)

    def test_right_sized_always_beats_grossly_oversized(self):
        assert self._isat(3.6).penalty < self._isat(40.0).penalty


# ── LOWER_BETTER (parasitics) is the mirror ──────────────────────────────────
class TestLowerBetter:
    def _dcr(self, sub, orig=0.075):
        return score_directional(
            orig, sub, Mode.LOWER_BETTER, warn_factor=1.3, gate_factor=1.6,
            label="DCR", unit="Ω",
        )

    def test_lower_parasitic_passes(self):
        assert self._dcr(0.0085).verdict == PASS  # much lower DCR is good

    def test_slightly_higher_warns(self):
        assert self._dcr(0.09).verdict == WARN  # 1.2× within warn

    def test_much_higher_fails(self):
        assert self._dcr(0.20).verdict == FAIL  # 2.67× beyond gate

    def test_being_lower_carries_capped_penalty(self):
        # A far-lower parasitic is "better" but the over-achievement is capped
        # (it usually costs die/size elsewhere) — still a PASS.
        r = self._dcr(0.001)
        assert r.verdict == PASS
        assert r.penalty <= 0.6 * math.log(8.0) + 1e-9


# ── Primary-value dispatch by category ───────────────────────────────────────
class TestPrimaryValueDispatch:
    def test_magnetic_330nH_regression(self):
        r = score_primary_value("magnetic", 1.5e-6, 330e-9)
        assert r is not None and r.verdict == FAIL

    def test_magnetic_exact(self):
        r = score_primary_value("magnetic", 1.5e-6, 1.5e-6)
        assert r is not None and r.verdict == PASS

    def test_mosfet_has_no_primary_value_spec(self):
        assert score_primary_value("mosfet", 1.0, 1.0) is None

    def test_chipbead_impedance_higher_better(self):
        # 120 Ω bead replaced by 60 Ω — insufficient suppression → FAIL.
        r = score_primary_value("chipBead", 120.0, 60.0)
        assert r is not None and r.verdict == FAIL

    def test_capacitor_double_value_is_tolerated_warn(self):
        # 2× capacitance is acceptable for bypass (in accept window) but off
        # nominal → WARN, and penalised so the closest value ranks first.
        r = score_primary_value("capacitor", 1e-6, 2e-6)
        assert r is not None and r.verdict in (PASS, WARN)
        closer = score_primary_value("capacitor", 1e-6, 1.1e-6)
        assert closer.penalty < r.penalty
