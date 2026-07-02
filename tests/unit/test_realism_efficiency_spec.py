"""The realism gate must FAIL a design that misses its own efficiency target —
not just check that efficiency is physically plausible. Uses the MEASURED (sim)
efficiency, never the over-optimistic analyst budget."""

import pytest

from heaviside.pipeline.realism import (
    CheckStatus,
    RealismVerdict,
    check_efficiency_vs_spec,
    evaluate_tas,
)


def test_meets_target_passes():
    assert check_efficiency_vs_spec(0.9215, 0.92).status is CheckStatus.PASS


def test_below_target_fails():
    r = check_efficiency_vs_spec(0.875, 0.92)
    assert r.status is CheckStatus.FAIL
    assert "MISSES" in r.detail


def test_within_tolerance_passes():
    # 0.5 pp below is within the 2 pp scaffolding-bias tolerance
    assert check_efficiency_vs_spec(0.915, 0.92).status is CheckStatus.PASS


def test_gate_fails_a_sub_spec_design():
    """A design that simulates below its spec efficiency must not be 'validated'."""
    tas = {
        "simulation_results": {
            "op": {
                "efficiency": 0.816,
                "pin": 100.0,
                "pout": 81.6,
                "vout": 5.0,
                "total_losses": 18.4,
            }
        }
    }
    spec = {"efficiency": 0.92, "operatingPoints": [{"outputVoltages": [5]}]}
    rep = evaluate_tas(tas, topology="push_pull", spec=spec)
    es = [c for c in rep.checks if c.name == "efficiency_vs_spec"]
    assert es and es[0].status is CheckStatus.FAIL
    assert rep.verdict is RealismVerdict.FAIL


def test_gate_uses_sim_not_optimistic_analyst():
    """When the analyst budget is optimistic (0.99) but the sim shows a sub-spec
    0.82, the spec check must gate on the sim, not the analyst."""
    tas = {
        "simulation_results": {
            "op": {
                "efficiency": 0.82,
                "efficiency_analyst": 0.99,
                "pin": 100.0,
                "pout": 82.0,
                "vout": 12.0,
                "total_losses": 18.0,
            }
        }
    }
    spec = {"efficiency": 0.92, "operatingPoints": [{"outputVoltages": [12]}]}
    rep = evaluate_tas(tas, topology="llc", spec=spec)
    es = [c for c in rep.checks if c.name == "efficiency_vs_spec"]
    assert es and es[0].status is CheckStatus.FAIL  # gated on the 0.82 sim, not the 0.99 analyst
    assert es[0].value == pytest.approx(0.82)
