"""Unit tests for the ``cross_check`` stage (master-plan step B7).

The stage triangulates *genuinely independent* estimators of the same
quantity (analyst closed-form vs ngspice sim) and records disagreements the
realism gate's ``estimators_agree`` check then reads. These tests pin the
triangulation math, the independence guard, and the serialised record shape.
"""
from __future__ import annotations

import pytest

from heaviside.stages.cross_check import (
    DEFAULT_TOLERANCES,
    CrossCheckError,
    Disagreement,
    Estimate,
    all_agree,
    to_record,
    triangulate,
)


class TestTriangulate:
    def test_agreeing_pair_is_marked_agree(self):
        # 0.955 vs 0.956 → 0.1% relative, well within the 3% efficiency band.
        ds = triangulate([
            Estimate("efficiency", 0.955, "ngspice_sim"),
            Estimate("efficiency", 0.956, "analyst"),
        ])
        assert len(ds) == 1
        assert ds[0].agree is True
        assert ds[0].relative_diff < DEFAULT_TOLERANCES["efficiency"]

    def test_catastrophic_disagreement_is_flagged(self):
        # The ABT #71 signature: sim efficiency 0.247 vs a sane analytic 0.90.
        ds = triangulate([
            Estimate("efficiency", 0.247, "ngspice_sim"),
            Estimate("efficiency", 0.90, "analyst"),
        ])
        assert len(ds) == 1
        assert ds[0].agree is False
        assert ds[0].relative_diff == pytest.approx(abs(0.247 - 0.90) / 0.90, abs=1e-6)
        assert not all_agree(ds)

    def test_same_source_pair_is_not_compared(self):
        # Two estimates from the SAME method are not independent → no comparison.
        ds = triangulate([
            Estimate("efficiency", 0.90, "analyst"),
            Estimate("efficiency", 0.50, "analyst"),
        ])
        assert ds == []

    def test_single_estimate_yields_no_comparison(self):
        assert triangulate([Estimate("efficiency", 0.9, "analyst")]) == []

    def test_multiple_quantities_are_grouped(self):
        ds = triangulate([
            Estimate("efficiency", 0.90, "ngspice_sim"),
            Estimate("efficiency", 0.905, "analyst"),
            Estimate("total_loss", 10.0, "ngspice_sim"),
            Estimate("total_loss", 10.5, "analyst"),
        ])
        quantities = sorted(d.quantity for d in ds)
        assert quantities == ["efficiency", "total_loss"]
        assert all(d.agree for d in ds)

    def test_total_loss_uses_its_own_coarser_tolerance(self):
        # 10 vs 12 → 16.7% relative: fails the 3% efficiency band but PASSES the
        # 25% total_loss band — proving the per-quantity tolerance is applied.
        ds = triangulate([
            Estimate("total_loss", 10.0, "ngspice_sim"),
            Estimate("total_loss", 12.0, "analyst"),
        ])
        assert ds[0].tolerance == DEFAULT_TOLERANCES["total_loss"]
        assert ds[0].agree is True

    def test_zvs_widens_only_efficiency_tolerance(self):
        estimates = [
            Estimate("efficiency", 0.90, "ngspice_sim"),
            Estimate("efficiency", 0.945, "analyst"),  # 4.8% — over 3%, under 6%
        ]
        assert triangulate(estimates)[0].agree is False           # normal band
        assert triangulate(estimates, zvs=True)[0].agree is True  # ZVS band

    def test_tolerance_override_is_respected(self):
        ds = triangulate(
            [Estimate("efficiency", 0.80, "ngspice_sim"),
             Estimate("efficiency", 0.90, "analyst")],
            tolerances={"efficiency": 0.20},
        )
        assert ds[0].tolerance == 0.20
        assert ds[0].agree is True  # 11% < 20%

    def test_non_independent_estimators_raise(self):
        # analyst magnetic loss IS MKF's magnetic loss — comparing them is vacuous.
        with pytest.raises(CrossCheckError, match="not independent"):
            triangulate([
                Estimate("total_loss", 5.0, "analyst_magnetic_loss"),
                Estimate("total_loss", 5.0, "mkf_magnetic_loss"),
            ])


class TestRelativeDiff:
    def test_zero_scale_pair_agrees(self):
        # Both zero → scale 0 → relative_diff 0 (no divide-by-zero, and they agree).
        ds = triangulate([
            Estimate("total_loss", 0.0, "ngspice_sim"),
            Estimate("total_loss", 0.0, "analyst"),
        ])
        assert ds[0].relative_diff == 0.0
        assert ds[0].agree is True

    def test_relative_diff_scales_by_larger_magnitude(self):
        d = Disagreement(
            quantity="efficiency", sources=("a", "b"), values=(0.5, 1.0),
            relative_diff=0.5, tolerance=0.03,
        )
        assert d.agree is False


class TestToRecord:
    def test_record_shape_and_all_agree_flag(self):
        ds = triangulate([
            Estimate("efficiency", 0.90, "ngspice_sim"),
            Estimate("efficiency", 0.905, "analyst"),
            Estimate("total_loss", 10.0, "ngspice_sim"),
            Estimate("total_loss", 40.0, "analyst"),  # 75% → disagree
        ])
        rec = to_record(ds)
        assert rec["all_agree"] is False
        assert isinstance(rec["comparisons"], list) and len(rec["comparisons"]) == 2
        for c in rec["comparisons"]:
            assert set(c) == {"quantity", "sources", "values", "relative_diff", "tolerance", "agree"}
            assert isinstance(c["sources"], list) and len(c["sources"]) == 2
        eff = next(c for c in rec["comparisons"] if c["quantity"] == "efficiency")
        loss = next(c for c in rec["comparisons"] if c["quantity"] == "total_loss")
        assert eff["agree"] is True
        assert loss["agree"] is False

    def test_empty_disagreements_all_agree_true(self):
        rec = to_record([])
        assert rec["all_agree"] is True
        assert rec["comparisons"] == []


# ---------------------------------------------------------------------------
# Wiring: _stamp_efficiency_cross_check (full_design) → realism estimators_agree
# ---------------------------------------------------------------------------


def _tas_with(eta_sim, eta_analyst) -> dict:
    op: dict = {}
    if eta_sim is not None:
        op["efficiency"] = eta_sim
    if eta_analyst is not None:
        op["efficiency_analyst"] = eta_analyst
    return {"simulation_results": {"op0": op}}


class TestEfficiencyCrossCheckWiring:
    def test_agreeing_efficiencies_pass(self):
        from heaviside.pipeline.full_design import _stamp_efficiency_cross_check
        from heaviside.pipeline.realism import CheckStatus, _check_estimators_agree

        # The real buck numbers: sim 0.9551 vs analyst 0.9557 (agree to ~0.06%).
        tas = _tas_with(0.9551, 0.9557)
        _stamp_efficiency_cross_check(tas)
        assert "cross_check" in tas
        assert tas["cross_check"]["all_agree"] is True
        assert _check_estimators_agree(tas).status is CheckStatus.PASS

    def test_catastrophic_efficiency_gap_fails(self):
        from heaviside.pipeline.full_design import _stamp_efficiency_cross_check
        from heaviside.pipeline.realism import CheckStatus, _check_estimators_agree

        # ABT #71 signature: sim collapses to 24.7 % while the analytic estimate
        # says ~90 %. estimators_agree must SURFACE this, not silently PASS.
        tas = _tas_with(0.247, 0.90)
        _stamp_efficiency_cross_check(tas)
        result = _check_estimators_agree(tas)
        assert result.status is CheckStatus.FAIL
        assert "disagree" in result.detail

    def test_normal_analyst_optimism_still_passes(self):
        from heaviside.pipeline.full_design import _stamp_efficiency_cross_check
        from heaviside.pipeline.realism import CheckStatus, _check_estimators_agree

        # Analyst optimistic by ~11 pp (it omitted some loss buckets): a real
        # limitation of the estimator, not a design defect → must NOT fail.
        tas = _tas_with(0.80, 0.90)  # 11.1 % relative, under the 0.20 tripwire
        _stamp_efficiency_cross_check(tas)
        assert _check_estimators_agree(tas).status is CheckStatus.PASS

    def test_missing_analyst_leaves_unavailable(self):
        from heaviside.pipeline.full_design import _stamp_efficiency_cross_check
        from heaviside.pipeline.realism import CheckStatus, _check_estimators_agree

        tas = _tas_with(0.95, None)
        _stamp_efficiency_cross_check(tas)
        assert "cross_check" not in tas
        assert _check_estimators_agree(tas).status is CheckStatus.UNAVAILABLE

    def test_invalid_efficiency_ratio_is_skipped(self):
        from heaviside.pipeline.full_design import _stamp_efficiency_cross_check

        # A non-ratio efficiency (>1, 0, or negative) is not a usable estimate.
        for bad in (0.0, 1.0, 1.5, -0.1):
            tas = _tas_with(bad, 0.9)
            _stamp_efficiency_cross_check(tas)
            assert "cross_check" not in tas

    def test_no_simulation_results_is_noop(self):
        from heaviside.pipeline.full_design import _stamp_efficiency_cross_check

        tas: dict = {}
        _stamp_efficiency_cross_check(tas)  # must not raise
        assert "cross_check" not in tas
