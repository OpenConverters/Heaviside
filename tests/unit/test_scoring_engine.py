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
    score_primary_value,
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
# TestRange / TestHigherBetter / TestLowerBetter removed: score_range /
# score_directional now live in Kelvin (Catch2 test_crossref_score + the golden).
# The delegating Python API is exercised by TestOverDimensioningPenalty +
# TestPrimaryValueDispatch below.


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
