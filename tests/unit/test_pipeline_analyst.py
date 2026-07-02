"""Tests for ``heaviside.pipeline.analyst``: loss budget + Tj stages."""

from __future__ import annotations

from typing import Any

import pytest

from heaviside.pipeline.analyst import (
    AnalystError,
    compute_buck_loss_budget,
    run_analyst,
    run_buck_analyst,
    run_cllc_analyst,
    stamp_junction_temperatures,
)

_BUCK_SPEC = {
    "inputVoltage": {"nominal": 48.0, "minimum": 36.0, "maximum": 60.0},
    "currentRippleRatio": 0.4,
    "operatingPoints": [
        {
            "outputVoltages": [12.0],
            "outputCurrents": [5.0],
            "switchingFrequency": 200_000.0,
            "ambientTemperature": 25.0,
        }
    ],
}


def _buck_tas_with_picked_components() -> dict[str, Any]:
    """A buck TAS where the catalogue selector has already stamped
    Q1/D1/L1/C_out with the gate-readable flat fields."""
    return {
        "topology": {
            "stages": [
                {
                    "name": "power_stage",
                    "circuit": {
                        "components": [
                            {
                                "name": "Q1",
                                "rds_on": 0.005,
                                "qg_total": 30e-9,
                                "rth_ja": 40.0,
                                "tj_max": 150.0,
                            },
                            {
                                "name": "D1",
                                "vf_typ": 0.45,
                                "qrr": 0.0,
                                "rth_ja": 50.0,
                                "tj_max": 175.0,
                            },
                            {
                                "name": "L1",
                                "data": {
                                    "outputs": [
                                        {
                                            "coreLosses": {"coreLosses": 0.300},
                                            "windingLosses": {
                                                "windingLosses": [
                                                    {"totalLosses": 0.150},
                                                ],
                                            },
                                        }
                                    ],
                                },
                            },
                            {
                                "name": "C_out",
                                "esr": 0.020,
                                "ripple_current_stress": 0.577,
                            },
                        ],
                    },
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# compute_buck_loss_budget — hand-checked closed-form values
# ---------------------------------------------------------------------------


def test_q1_conduction_loss_matches_hand_calc() -> None:
    """P_Q1_cond = D * Iout^2 * Rds_on  with D = Vout/Vin_nom = 0.25:
    = 0.25 * 25 * 0.005 = 0.03125 W"""
    tas = _buck_tas_with_picked_components()
    budget = compute_buck_loss_budget(tas, _BUCK_SPEC)
    assert budget["Q1_conduction"] == pytest.approx(0.03125, rel=1e-6)


def test_q1_switching_loss_matches_hand_calc() -> None:
    """P_Q1_sw = 0.5 * Vin * Iout * (Qg / Ig) * fsw
    = 0.5 * 48 * 5 * 30e-9 / 1.0 * 200000 = 0.72 W.
    The 0.5 is the triangular V-I overlap factor (E_sw = ½·V·I·t_sw per
    switching transition, t_sw = Qg/Ig)."""
    tas = _buck_tas_with_picked_components()
    budget = compute_buck_loss_budget(tas, _BUCK_SPEC)
    assert budget["Q1_switching"] == pytest.approx(0.72, rel=1e-6)


def test_d1_conduction_loss_matches_hand_calc() -> None:
    """P_D1_cond = (1 - D) * Iout * Vf = 0.75 * 5 * 0.45 = 1.6875 W"""
    tas = _buck_tas_with_picked_components()
    budget = compute_buck_loss_budget(tas, _BUCK_SPEC)
    assert budget["D1_conduction"] == pytest.approx(1.6875, rel=1e-6)


def test_d1_switching_loss_zero_for_schottky() -> None:
    """Qrr=0 (Schottky) -> P_D1_sw = 0."""
    tas = _buck_tas_with_picked_components()
    budget = compute_buck_loss_budget(tas, _BUCK_SPEC)
    assert budget["D1_switching"] == 0.0


def test_inductor_losses_extracted_from_mas() -> None:
    """L1 losses come straight from PyMKF's MAS outputs[op].coreLosses
    and outputs[op].windingLosses. No closed-form recomputation."""
    tas = _buck_tas_with_picked_components()
    budget = compute_buck_loss_budget(tas, _BUCK_SPEC)
    assert budget["L1_core"] == pytest.approx(0.300)
    assert budget["L1_dcr"] == pytest.approx(0.150)


def test_capacitor_esr_loss_uses_rms_ripple() -> None:
    """P_C_esr = I_ripple_rms^2 * ESR = 0.577^2 * 0.020 ~= 6.66 mW"""
    tas = _buck_tas_with_picked_components()
    budget = compute_buck_loss_budget(tas, _BUCK_SPEC)
    assert budget["C_out_esr"] == pytest.approx(0.577**2 * 0.020, rel=1e-3)


def test_loss_budget_reports_none_for_missing_inputs() -> None:
    """Removing a stamped field -> the analyst returns None for that
    bucket (not 0, not a fallback). Realism gate ignores None."""
    tas = _buck_tas_with_picked_components()
    # Strip Q1's Rds_on (selector didn't run? data quality?)
    tas["topology"]["stages"][0]["circuit"]["components"][0].pop("rds_on")
    budget = compute_buck_loss_budget(tas, _BUCK_SPEC)
    assert budget["Q1_conduction"] is None
    # Q1_switching still computable from Qg
    assert budget["Q1_switching"] is not None


def test_loss_budget_throws_on_missing_spec_fields() -> None:
    bad = {"inputVoltage": {"nominal": 48.0}}  # no operatingPoints
    with pytest.raises(AnalystError):
        compute_buck_loss_budget(_buck_tas_with_picked_components(), bad)


# ---------------------------------------------------------------------------
# stamp_junction_temperatures
# ---------------------------------------------------------------------------


def test_tj_is_ambient_plus_loss_times_rth() -> None:
    """Q1 has loss = 0.03125 + 0.72 = 0.75125 W, Rth_ja = 40.
    Tj = 25 + 0.75125 * 40 = 55.05 °C. (Q1_switching carries the 0.5
    triangular-overlap factor — see test_q1_switching_loss_matches_hand_calc.)"""
    tas = _buck_tas_with_picked_components()
    run_buck_analyst(tas, _BUCK_SPEC)
    q1 = tas["topology"]["stages"][0]["circuit"]["components"][0]
    expected_loss = 0.03125 + 0.72
    expected_tj = 25.0 + expected_loss * 40.0
    assert q1["tj"] == pytest.approx(expected_tj, rel=1e-3)
    assert q1["tj_provenance"]["t_ambient_c"] == 25.0
    assert q1["tj_provenance"]["rth_ja_c_per_w"] == 40.0


def test_tj_skipped_when_rth_ja_missing() -> None:
    tas = _buck_tas_with_picked_components()
    # Drop Q1's Rth_ja
    tas["topology"]["stages"][0]["circuit"]["components"][0].pop("rth_ja")
    run_buck_analyst(tas, _BUCK_SPEC)
    q1 = tas["topology"]["stages"][0]["circuit"]["components"][0]
    assert "tj" not in q1, "Tj should not be stamped when Rth_ja is missing"


def test_run_buck_analyst_stamps_loss_budget_at_root() -> None:
    tas = _buck_tas_with_picked_components()
    run_buck_analyst(tas, _BUCK_SPEC)
    assert "loss_budget" in tas
    assert isinstance(tas["loss_budget"], dict)


def test_run_analyst_dispatches_per_topology() -> None:
    """Buck dispatches; an unported topology is a clean no-op."""
    tas = _buck_tas_with_picked_components()
    run_analyst("buck", tas, _BUCK_SPEC)
    assert "loss_budget" in tas

    tas2 = _buck_tas_with_picked_components()
    run_analyst("totally_made_up", tas2, _BUCK_SPEC)
    assert "loss_budget" not in tas2  # no-op for unknown


def test_multi_op_stamps_per_op_budgets_and_worst_case_root() -> None:
    """With 2 operating points, each gets a per-op budget at
    simulation_results.op<i>.loss_budget, and tas.loss_budget is the
    element-wise max across ops."""
    tas = _buck_tas_with_picked_components()
    spec = dict(_BUCK_SPEC)
    spec["operatingPoints"] = [
        {**_BUCK_SPEC["operatingPoints"][0], "outputCurrents": [5.0]},
        {**_BUCK_SPEC["operatingPoints"][0], "outputCurrents": [8.0]},
    ]
    run_buck_analyst(tas, spec)

    # Per-op budgets present at sim_results
    sim = tas["simulation_results"]
    assert "op0" in sim and "op1" in sim
    assert "loss_budget" in sim["op0"]
    assert "loss_budget" in sim["op1"]

    # op1 has higher iout -> higher Q1_conduction (D*Iout^2*Rds_on scales with Iout^2)
    op0_q1 = sim["op0"]["loss_budget"]["Q1_conduction"]
    op1_q1 = sim["op1"]["loss_budget"]["Q1_conduction"]
    assert op1_q1 > op0_q1, "op1 (8A) Q1 cond should exceed op0 (5A)"

    # Root loss_budget = worst-case (max) per bucket = op1's number
    assert tas["loss_budget"]["Q1_conduction"] == pytest.approx(op1_q1)


# ---------------------------------------------------------------------------
# CLLC dispatch — dual full bridge, not the half-bridge + diode LLC model
# ---------------------------------------------------------------------------

_CLLC_SPEC = {
    "inputVoltage": {"nominal": 400.0, "minimum": 380.0, "maximum": 420.0},
    "desiredTurnsRatios": [8.0],
    "operatingPoints": [
        {
            "outputVoltages": [48.0],
            "outputCurrents": [20.0],
            "switchingFrequency": 500_000.0,
            "ambientTemperature": 25.0,
        }
    ],
}


def _cllc_tas_dual_full_bridge() -> dict[str, Any]:
    """A CLLC TAS: HV primary bridge Q1-Q4 + LV sync-rect bridge Q5-Q8, T1."""
    fets = [
        {"name": q, "rds_on": 0.01, "qg_total": 20e-9}
        for q in ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8")
    ]
    t1 = {
        "name": "T1",
        "data": {
            "outputs": [
                {
                    "coreLosses": {"coreLosses": 0.5},
                    "windingLosses": {"windingLosses": [{"totalLosses": 0.4}]},
                }
            ],
        },
    }
    return {
        "topology": {
            "stages": [
                {"name": "resonant", "circuit": {"components": [*fets, t1]}},
            ],
        },
    }


def test_cllc_counts_lv_sync_rect_bridge_losses() -> None:
    """Regression: CLLC is a dual full bridge. The old dispatch used the
    half-bridge LLC budget (Q1/Q2 + diodes D1/D2), silently omitting the LV
    synchronous-rectifier bridge Q5-Q8 — the dominant conduction loss — which
    left a fabricated ~99.9% efficiency. All four LV FETs must be counted."""
    tas = _cllc_tas_dual_full_bridge()
    run_cllc_analyst(tas, _CLLC_SPEC)
    budget = tas["loss_budget"]

    # LV sync-rect bridge carries full Iout: conduction = 0.5 * Iout^2 * Rds_on.
    iout, rds_on = 20.0, 0.01
    expected_lv = 0.5 * iout**2 * rds_on
    for q in ("Q5", "Q6", "Q7", "Q8"):
        assert budget.get(f"{q}_conduction") == pytest.approx(expected_lv, rel=1e-6), (
            f"{q} (LV sync-rect FET) conduction loss must be counted"
        )

    # HV primary bridge (Q1-Q4) present too; ZVS => switching ~0.
    for q in ("Q1", "Q2", "Q3", "Q4"):
        assert f"{q}_conduction" in budget
        assert budget[f"{q}_switching"] == 0.0

    # The half-bridge LLC model's diode buckets must NOT appear.
    assert "D1_conduction" not in budget
    assert "D2_conduction" not in budget


def test_stamp_jt_no_op_without_loss_budget() -> None:
    """If the caller forgot to run the loss extractor first,
    stamp_junction_temperatures is a silent no-op (doesn't crash)."""
    tas = _buck_tas_with_picked_components()
    # Don't call run_buck_analyst — tas has no loss_budget key.
    stamp_junction_temperatures(tas, _BUCK_SPEC)
    # No Tj should be stamped.
    for c in tas["topology"]["stages"][0]["circuit"]["components"]:
        assert "tj" not in c
