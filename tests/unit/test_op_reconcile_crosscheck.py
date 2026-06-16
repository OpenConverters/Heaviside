"""op_reconcile + cross_check (master-plan step B7, verification spine).

The pure engines are tested hermetically (no MKF): per-OP reconciliation with a
binding OP, InfeasibleAtOP on corner saturation, QR/DCM monotone law; and
independent-estimator triangulation that refuses the vacuous analyst-vs-MKF
magnetic-loss pairing and FAILs the realism gate on disagreement.
"""
from __future__ import annotations

import pytest

from heaviside.stages import cross_check as cc
from heaviside.stages import op_reconcile as orc


# ---------------------------------------------------------------------------
# op_reconcile
# ---------------------------------------------------------------------------


def _est(i, ipeak, isat, tj=None, tj_max=None):
    return orc.OpEstimate(op_index=i, ipeak_a=ipeak, isat_a=isat, tj_c=tj,
                          tj_max_c=tj_max, label=f"op{i}")


def test_binding_op_is_least_isat_margin():
    # OP1 has the tightest saturation margin (isat/ipeak = 1.3) and binds.
    rep = orc.reconcile_margins([
        _est(0, ipeak=3.0, isat=6.0),   # ratio 2.0
        _est(1, ipeak=4.0, isat=5.2),   # ratio 1.3  <- binding
        _est(2, ipeak=3.5, isat=7.0),   # ratio 2.0
    ], min_isat_ratio=1.2)
    assert rep.binding_op_index == 1
    assert rep.feasible_all_ops
    assert rep.constraint_feedback["binding_isat_ratio"] == pytest.approx(1.3)


def test_corner_op_saturation_raises_infeasible_at_op():
    with pytest.raises(orc.InfeasibleAtOP) as ei:
        orc.reconcile_margins([
            _est(0, ipeak=3.0, isat=6.0),    # fine
            _est(1, ipeak=5.0, isat=5.2),    # ratio 1.04 < 1.2 -> saturates
        ], min_isat_ratio=1.2)
    rep = ei.value.report
    assert ei.value.binding_op_index == 1
    assert not rep.feasible_all_ops
    assert rep.constraint_feedback["saturation_infeasible_ops"] == [1]
    # shortfall factor tells the refinement loop how much more isat is needed
    assert rep.constraint_feedback["isat_shortfall_factor"] == pytest.approx(1.2 / 1.04, rel=1e-3)


def test_thermal_infeasibility_fails():
    with pytest.raises(orc.InfeasibleAtOP) as ei:
        orc.reconcile_margins([
            _est(0, ipeak=3.0, isat=6.0, tj=160.0, tj_max=150.0),  # over Tj_max
        ], min_isat_ratio=1.2)
    assert ei.value.report.constraint_feedback["thermal_infeasible_ops"] == [0]


def test_inspect_without_raising():
    rep = orc.reconcile_margins([
        _est(0, ipeak=5.0, isat=5.2),
    ], min_isat_ratio=1.2, raise_on_infeasible=False)
    assert not rep.feasible_all_ops
    assert rep.per_op[0].sat_feasible is False


def test_thermal_none_when_unavailable():
    rep = orc.reconcile_margins([_est(0, ipeak=3.0, isat=6.0)], min_isat_ratio=1.2)
    assert rep.per_op[0].thermal_feasible is None
    assert rep.per_op[0].feasible  # sat ok, thermal not evaluated ⇒ not a failure


def test_reconcile_margins_validates_inputs():
    with pytest.raises(ValueError):
        orc.reconcile_margins([_est(0, ipeak=0.0, isat=5.0)])
    with pytest.raises(ValueError):
        orc.reconcile_margins([])


# ---- QR/DCM fsw-load law ----------------------------------------------------


def test_fsw_load_law_accepts_monotone():
    law = orc.fsw_load_law([
        orc.LoadPoint(1.0, 100_000),
        orc.LoadPoint(0.5, 160_000),
        orc.LoadPoint(0.1, 300_000),  # light load → high fsw
    ])
    assert [p.load_fraction for p in law] == [0.1, 0.5, 1.0]  # sorted by load


def test_fsw_load_law_rejects_nonmonotone():
    with pytest.raises(ValueError, match="non-monotone"):
        orc.fsw_load_law([
            orc.LoadPoint(0.1, 100_000),  # light load but LOW fsw — wrong
            orc.LoadPoint(1.0, 300_000),
        ])


# ---------------------------------------------------------------------------
# cross_check
# ---------------------------------------------------------------------------


def test_independent_estimators_agree_within_tolerance():
    ds = cc.triangulate([
        cc.Estimate("efficiency", 0.91, "analyst"),
        cc.Estimate("efficiency", 0.92, "ngspice_sim"),
    ])
    assert len(ds) == 1
    assert ds[0].agree
    assert cc.all_agree(ds)


def test_independent_estimators_disagree():
    ds = cc.triangulate([
        cc.Estimate("efficiency", 0.95, "analyst"),
        cc.Estimate("efficiency", 0.80, "ngspice_sim"),
    ])
    assert not ds[0].agree
    assert not cc.all_agree(ds)


def test_zvs_widens_efficiency_tolerance():
    # 5% relative gap: fails the normal 3% band, passes the ZVS 6% band
    est = [cc.Estimate("efficiency", 0.98, "analyst"),
           cc.Estimate("efficiency", 0.932, "ngspice_sim")]
    assert not cc.triangulate(est)[0].agree           # normal
    assert cc.triangulate(est, zvs=True)[0].agree       # ZVS band


def test_vacuous_pairing_raises():
    """analyst-magnetic-loss vs MKF-magnetic-loss is the SAME number — must not
    be presented as independent corroboration."""
    with pytest.raises(cc.CrossCheckError, match="not independent"):
        cc.triangulate([
            cc.Estimate("total_loss", 1.0, "analyst_magnetic_loss"),
            cc.Estimate("total_loss", 1.0, "mkf_magnetic_loss"),
        ])


def test_same_source_not_compared():
    ds = cc.triangulate([
        cc.Estimate("tj", 100.0, "analyst"),
        cc.Estimate("tj", 105.0, "analyst"),  # same source — skipped
    ])
    assert ds == []


# ---------------------------------------------------------------------------
# realism gate: estimators_agree
# ---------------------------------------------------------------------------


def _check(report, name):
    return next(c for c in report.checks if c.name == name)


def test_realism_estimators_agree_pass():
    from heaviside.pipeline.realism import CheckStatus, evaluate_tas

    ds = cc.triangulate([
        cc.Estimate("efficiency", 0.91, "analyst"),
        cc.Estimate("efficiency", 0.92, "ngspice_sim"),
    ])
    tas = {"cross_check": cc.to_record(ds), "topology": {"stages": []}}
    assert _check(evaluate_tas(tas, topology="buck"), "estimators_agree").status is CheckStatus.PASS


def test_realism_estimators_agree_fail():
    from heaviside.pipeline.realism import CheckStatus, RealismVerdict, evaluate_tas

    ds = cc.triangulate([
        cc.Estimate("efficiency", 0.95, "analyst"),
        cc.Estimate("efficiency", 0.80, "ngspice_sim"),
    ])
    tas = {"cross_check": cc.to_record(ds), "topology": {"stages": []}}
    report = evaluate_tas(tas, topology="buck")
    assert _check(report, "estimators_agree").status is CheckStatus.FAIL
    assert report.verdict is RealismVerdict.FAIL  # disagreement fails the design


def test_realism_estimators_agree_unavailable_without_crosscheck():
    from heaviside.pipeline.realism import CheckStatus, evaluate_tas

    tas = {"topology": {"stages": []}}
    assert _check(evaluate_tas(tas, topology="buck"), "estimators_agree").status is CheckStatus.UNAVAILABLE
