"""Manufacturer-agnostic saturation-current normalization (the FAE #134 fix).

Proves the core claim: normalizing both parts to a common inductance-drop
criterion removes the *false* shortfall the FAE caught, while still catching real
shortfalls — and does so without ever branching on the manufacturer.
"""

import pytest

from heaviside.pipeline.isat_normalize import (
    ADEQUATE,
    SHORTFALL,
    UNCERTAIN,
    compare_isat,
    isat_at_percent_drop,
)

# Real datasheet points (the FAE trap case):
#   Würth WE-MAPI 74438324015: I_sat 2.5 A @10 %, 4.7 A @30 %
#   Vishay IHLP-1616AB-1R5:    I_sat 3.25 A @20 %
WE_MAPI = [{"percent_drop": 10, "current": 2.5}, {"percent_drop": 30, "current": 4.7}]
VISHAY_1R5 = [{"percent_drop": 20, "current": 3.25}]


class TestInterpolation:
    def test_interpolates_to_20pct(self):
        # WE-MAPI at a matched 20 %: linear between (10,2.5) and (30,4.7) = 3.6 A,
        # matching the FAE judge's ~3.6–3.7 A read of the L-vs-I curve.
        assert isat_at_percent_drop(WE_MAPI, 20) == pytest.approx(3.6, abs=0.05)

    def test_exact_point(self):
        assert isat_at_percent_drop(VISHAY_1R5, 20) == 3.25

    def test_clamps_beyond_range_not_extrapolates(self):
        # Asking for 40 % when only 10/30 % are known clamps to the 30 % value
        # (never invents headroom past the measured range).
        assert isat_at_percent_drop(WE_MAPI, 40) == 4.7
        assert isat_at_percent_drop(WE_MAPI, 5) == 2.5

    def test_basis_unknown_returns_none(self):
        assert isat_at_percent_drop([{"percent_drop": None, "current": 2.5}], 20) is None
        assert isat_at_percent_drop([], 20) is None


class TestCompareMatchedBasis:
    def test_false_shortfall_is_removed(self):
        # THE headline fix: WE-MAPI (2.5 A @10 %) vs Vishay (3.25 A @20 %). At a
        # matched 20 % the WE part delivers 3.6 A >= 3.25 A -> ADEQUATE, not the
        # bogus "38 % shortfall" the raw-headline comparison produced.
        r = compare_isat(VISHAY_1R5, WE_MAPI)
        assert r.verdict == ADEQUATE
        assert r.percent_drop == 20.0
        assert r.sub_at == pytest.approx(3.6, abs=0.05)

    def test_real_shortfall_still_caught(self):
        # A genuinely under-rated substitute (5 A @20 % for an 18 A @20 % original)
        # is still a SHORTFALL at the matched criterion.
        orig = [{"percent_drop": 20, "current": 18.0}]
        sub = [{"percent_drop": 20, "current": 5.0}]
        assert compare_isat(orig, sub).verdict == SHORTFALL

    def test_manufacturer_agnostic_symmetry(self):
        # The rule depends only on the stated criteria, never on which side is
        # "Würth": the verdict is invariant to relabelling the vendors.
        a = compare_isat(VISHAY_1R5, WE_MAPI).verdict
        # Same numbers, no vendor identity anywhere in the call — still ADEQUATE.
        b = compare_isat(
            [{"percent_drop": 20, "current": 3.25}],
            [{"percent_drop": 10, "current": 2.5}, {"percent_drop": 30, "current": 4.7}],
        ).verdict
        assert a == b == ADEQUATE


class TestCompareUnknownBasis:
    def test_close_ratio_is_uncertain_not_false_fail(self):
        # Legacy scalars with no stated basis, ratio 2.5/3.25 = 0.77: a difference
        # this small could be pure roll-off-criterion mismatch, so we must NOT hard
        # fail — emit an honest "verify at matched %-drop" caveat.
        r = compare_isat(
            [{"percent_drop": None, "current": 3.25}],
            [{"percent_drop": None, "current": 2.5}],
        )
        assert r.verdict == UNCERTAIN
        assert "matched" in r.note.lower()

    def test_egregious_shortfall_still_fails_without_basis(self):
        # 2.5 A vs 18 A (ratio 0.14) is too large for any basis difference to
        # explain -> SHORTFALL even with unknown bases.
        r = compare_isat(
            [{"percent_drop": None, "current": 18.0}],
            [{"percent_drop": None, "current": 2.5}],
        )
        assert r.verdict == SHORTFALL

    def test_missing_current_is_uncertain(self):
        assert compare_isat(None, WE_MAPI).verdict == UNCERTAIN
        assert compare_isat(WE_MAPI, None).verdict == UNCERTAIN


class TestDeriveFromCurve:
    # A synthetic saturating L-vs-I curve: 10 µH small-signal, dropping with bias.
    CURVE = [
        {"inductance": 10.0e-6, "current": 0.0, "temperature": 25},
        {"inductance": 9.0e-6, "current": 4.0, "temperature": 25},   # 10% drop @4A
        {"inductance": 8.0e-6, "current": 6.0, "temperature": 25},   # 20% drop @6A
        {"inductance": 7.0e-6, "current": 7.5, "temperature": 25},   # 30% drop @7.5A
    ]

    def test_derives_percent_drop_points(self):
        from heaviside.pipeline.isat_normalize import points_from_inductance_curve
        pts = points_from_inductance_curve(self.CURVE)
        by = {round(p["percent_drop"]): p["current"] for p in pts}
        assert by[10] == 4.0 and by[20] == 6.0 and by[30] == 7.5

    def test_resolver_prefers_explicit_then_curve_then_scalar(self):
        from heaviside.pipeline.isat_normalize import resolve_saturation_points
        # explicit table wins
        e1 = {"saturationCurrents": [{"percentInductanceDrop": 20, "current": 5.0}],
              "inductancePoints": self.CURVE, "saturationCurrentPeak": 2.0}
        assert resolve_saturation_points(e1) == [{"percent_drop": 20, "current": 5.0, "temperature": None}]
        # else derive from the curve
        e2 = {"inductancePoints": self.CURVE, "saturationCurrentPeak": 2.0}
        assert any(round(p["percent_drop"]) == 20 for p in resolve_saturation_points(e2))
        # else the legacy scalar (basis unknown)
        e3 = {"saturationCurrentPeak": 2.0}
        assert resolve_saturation_points(e3) == [{"percent_drop": None, "current": 2.0}]

    def test_curve_derived_comparison_matches_at_20pct(self):
        # An original curve giving 6A@20% vs a substitute scalar 2A (unknown basis)
        # -> ratio 0.33, too low for any basis diff -> still a real SHORTFALL.
        from heaviside.pipeline.isat_normalize import resolve_saturation_points
        orig = resolve_saturation_points({"inductancePoints": self.CURVE})
        sub = resolve_saturation_points({"saturationCurrentPeak": 2.0})
        assert compare_isat(orig, sub).verdict == SHORTFALL
