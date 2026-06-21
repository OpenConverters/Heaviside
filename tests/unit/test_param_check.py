"""Electrical-parameter cross-reference checks (ESR, ripple, dielectric, Rds(on),
Qrr, Isat, …) — the declarative spec framework and the pipeline stage that
attaches verdicts and demotes substitutes that fall outside the allowed margin.
"""
from __future__ import annotations

from heaviside.pipeline.param_check import (
    FAIL,
    PASS,
    UNVERIFIED,
    WARN,
    evaluate_params,
    worst_verdict,
)


# ── capacitor ESR / ripple / dielectric ──────────────────────────────────────
def test_cap_esr_pass_warn_fail():
    # lower_better, tol 1.5×
    assert evaluate_params("capacitor", {"esr": 0.1}, {"esr": 0.1})[0]["verdict"] == PASS
    assert evaluate_params("capacitor", {"esr": 0.1}, {"esr": 0.14})[0]["verdict"] == WARN
    assert evaluate_params("capacitor", {"esr": 0.1}, {"esr": 0.30})[0]["verdict"] == FAIL


def test_cap_ripple_higher_better():
    # ripple must be ≥ original; allow 10% shortfall
    assert evaluate_params("capacitor", {"ripple_current": 1.0}, {"ripple_current": 1.2})[0]["verdict"] == PASS
    assert evaluate_params("capacitor", {"ripple_current": 1.0}, {"ripple_current": 0.95})[0]["verdict"] == WARN
    assert evaluate_params("capacitor", {"ripple_current": 1.0}, {"ripple_current": 0.5})[0]["verdict"] == FAIL


def test_cap_dielectric_downgrade_fails():
    # X7R → X5R is a downgrade; X5R → X7R is safe; C0G → X7R is a downgrade
    r = evaluate_params("capacitor", {"technology": "X7R"}, {"technology": "X5R"})
    assert r[0]["verdict"] == FAIL
    r = evaluate_params("capacitor", {"technology": "X5R"}, {"technology": "X7R"})
    assert r[0]["verdict"] == PASS
    r = evaluate_params("capacitor", {"technology": "C0G"}, {"technology": "X7R"})
    assert r[0]["verdict"] == FAIL


def test_missing_substitute_esr_excluded():
    # "if a DB object is missing ESR, don't use it" → FAIL (cannot verify)
    r = evaluate_params("capacitor", {"esr": 0.1}, {"esr": None})
    assert r[0]["verdict"] == FAIL
    assert "no ESR data" in r[0]["note"]


def test_missing_original_esr_unverified_minimize():
    # original ESR unknown → can't compare; flagged unverified, minimize hint
    r = evaluate_params("capacitor", {"esr": None}, {"esr": 0.05})
    assert r[0]["verdict"] == UNVERIFIED
    assert "lowest available preferred" in r[0]["note"]


def test_both_absent_param_skipped():
    # neither side has ESR → no row emitted (avoid noise), only the ones present
    r = evaluate_params("capacitor", {"ripple_current": 1.0}, {"ripple_current": 1.0})
    assert [x["name"] for x in r] == ["ripple_current"]


# ── other categories use the same engine ─────────────────────────────────────
def test_mosfet_rdson_qg():
    r = {x["name"]: x["verdict"] for x in evaluate_params(
        "mosfet", {"rds_on": 0.010, "qg": 20e-9}, {"rds_on": 0.012, "qg": 60e-9})}
    assert r["rds_on"] == WARN   # 1.2× ≤ 1.5×
    assert r["qg"] == FAIL       # 3× > 2×


def test_diode_vf_qrr():
    r = {x["name"]: x["verdict"] for x in evaluate_params(
        "diode", {"vf": 0.5, "qrr": 10e-9}, {"vf": 0.55, "qrr": 8e-9})}
    assert r["vf"] == WARN
    assert r["qrr"] == PASS


def test_magnetic_isat_dcr():
    r = {x["name"]: x["verdict"] for x in evaluate_params(
        "magnetic", {"saturation_current": 5.0, "dcr": 0.05}, {"saturation_current": 6.0, "dcr": 0.04})}
    assert r["saturation_current"] == PASS
    assert r["dcr"] == PASS
    # Isat shortfall below margin fails
    r2 = evaluate_params("magnetic", {"saturation_current": 5.0}, {"saturation_current": 3.0})
    assert r2[0]["verdict"] == FAIL


def test_worst_verdict_ordering():
    assert worst_verdict([{"verdict": PASS}, {"verdict": WARN}, {"verdict": FAIL}]) == FAIL
    assert worst_verdict([{"verdict": PASS}, {"verdict": UNVERIFIED}]) == UNVERIFIED
    assert worst_verdict([{"verdict": PASS}]) == PASS
