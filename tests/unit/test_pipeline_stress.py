"""Tests for ``heaviside.pipeline.stress``: analytical stress derivations."""

from __future__ import annotations

import pytest

from heaviside.pipeline.stress import (
    StressDerivationError,
    buck_stresses,
    derive_stresses,
)

_BUCK_OK = {
    "inputVoltage": {"nominal": 48.0, "minimum": 36.0, "maximum": 60.0},
    "currentRippleRatio": 0.4,
    "desiredInductance": 22e-6,
    "operatingPoints": [
        {
            "outputVoltages": [12.0],
            "outputCurrents": [5.0],
            "switchingFrequency": 200_000.0,
            "ambientTemperature": 25.0,
        }
    ],
}


def test_buck_stresses_match_hand_calc() -> None:
    """Buck 48->12@5A with ripple ratio 0.4:
    Vds_off = Vin_max = 60 V
    Id_peak = Iout * (1 + ripple/2) = 5 * 1.2 = 6 A
    Vr     = Vin_max = 60 V
    D_min  = Vout/Vin_max = 12/60 = 0.2
    If_avg = Iout * (1 - D_min) = 5 * 0.8 = 4 A
    V_working = Vout = 12 V
    I_ripple_rms = Iout * ripple / (2*sqrt(3)) = 5 * 0.4 / 3.464 ~= 0.577 A
    """
    s = buck_stresses(_BUCK_OK)
    assert s.vds_stress == 60.0
    assert s.id_stress == pytest.approx(6.0)
    assert s.vr_stress == 60.0
    assert s.if_avg_stress == pytest.approx(4.0)
    assert s.v_working == 12.0
    assert s.i_ripple == pytest.approx(0.5773, abs=1e-3)


def test_buck_stresses_throws_on_step_up_spec() -> None:
    bad = {
        **_BUCK_OK,
        "operatingPoints": [
            {
                **_BUCK_OK["operatingPoints"][0],
                "outputVoltages": [80.0],  # Vout > Vin_min, cannot step up
            }
        ],
    }
    with pytest.raises(StressDerivationError, match="step up"):
        buck_stresses(bad)


@pytest.mark.parametrize(
    "bad,where",
    [
        ({}, "inputVoltage"),
        ({**_BUCK_OK, "inputVoltage": {"nominal": 48}}, "maximum"),
        ({**_BUCK_OK, "operatingPoints": []}, "operatingPoints"),
    ],
)
def test_buck_stresses_throws_on_missing_spec_fields(
    bad: dict,
    where: str,
) -> None:
    with pytest.raises(StressDerivationError, match=where):
        buck_stresses(bad)


def test_derive_stresses_dispatches_per_topology() -> None:
    s = derive_stresses("buck", _BUCK_OK)
    assert s is not None
    assert s.vds_stress == 60.0


def test_derive_stresses_returns_none_for_unported_topology() -> None:
    # No deriver registered yet for an arbitrary unknown topology.
    assert derive_stresses("totally_made_up", _BUCK_OK) is None


# ---------------------------------------------------------------------------
# Boost
# ---------------------------------------------------------------------------


_BOOST_OK = {
    "inputVoltage": {"nominal": 12.0, "minimum": 9.0, "maximum": 15.0},
    "currentRippleRatio": 0.4,
    "operatingPoints": [
        {
            "outputVoltages": [24.0],
            "outputCurrents": [2.0],
            "switchingFrequency": 150_000.0,
            "ambientTemperature": 25.0,
        }
    ],
}


def test_boost_stresses_match_hand_calc() -> None:
    """Boost 9->24V@2A, ripple 0.4: Q1 sees Vout=24V. Iin = Iout*Vout/Vin_min
    = 2 * 24 / 9 = 5.33 A. Id_pk = Iin * 1.2 = 6.4 A."""
    from heaviside.pipeline.stress import boost_stresses

    s = boost_stresses(_BOOST_OK)
    assert s.vds_stress == 24.0
    assert s.id_stress == pytest.approx(5.333 * 1.2, abs=0.01)
    assert s.vr_stress == 24.0
    assert s.if_avg_stress == 2.0
    assert s.v_working == 24.0


def test_boost_throws_on_step_down_spec() -> None:
    from heaviside.pipeline.stress import boost_stresses

    bad = {
        **_BOOST_OK,
        "operatingPoints": [
            {
                **_BOOST_OK["operatingPoints"][0],
                "outputVoltages": [5.0],
            }
        ],
    }
    with pytest.raises(StressDerivationError, match="step down"):
        boost_stresses(bad)


# ---------------------------------------------------------------------------
# Cuk
# ---------------------------------------------------------------------------


def test_cuk_stresses_voltage_is_sum_of_rails() -> None:
    """Cuk Vds = Vin_min + |Vout|: for 18->12V Cuk that's 18+12=30V."""
    from heaviside.pipeline.stress import cuk_stresses

    spec = {
        "inputVoltage": {"nominal": 24.0, "minimum": 18.0, "maximum": 30.0},
        "currentRippleRatio": 0.4,
        "operatingPoints": [
            {
                "outputVoltages": [12.0],
                "outputCurrents": [2.0],
                "switchingFrequency": 150_000.0,
                "ambientTemperature": 25.0,
            }
        ],
    }
    s = cuk_stresses(spec)
    assert s.vds_stress == 18.0 + 12.0
    assert s.v_working == 12.0


# ---------------------------------------------------------------------------
# Flyback
# ---------------------------------------------------------------------------


_FLYBACK_OK = {
    "inputVoltage": {"nominal": 48.0, "minimum": 36.0, "maximum": 60.0},
    "currentRippleRatio": 0.4,
    "desiredTurnsRatios": [5.0],
    "maximumDutyCycle": 0.5,
    "efficiency": 0.85,
    "desiredMagnetizingInductance": 200e-6,
    "operatingPoints": [
        {
            "outputVoltages": [12.0],
            "outputCurrents": [2.0],
            "switchingFrequency": 100_000.0,
            "ambientTemperature": 25.0,
        }
    ],
}


def test_flyback_vds_includes_reflected_secondary() -> None:
    """Vds = Vin_max + n * Vout: 60 + 5*12 = 120 V."""
    from heaviside.pipeline.stress import flyback_stresses

    s = flyback_stresses(_FLYBACK_OK)
    assert s.vds_stress == 60.0 + 5.0 * 12.0


def test_flyback_throws_on_missing_turns_ratio() -> None:
    from heaviside.pipeline.stress import flyback_stresses

    bad = {k: v for k, v in _FLYBACK_OK.items() if k != "desiredTurnsRatios"}
    with pytest.raises(StressDerivationError, match="desiredTurnsRatios"):
        flyback_stresses(bad)


def test_flyback_throws_on_bad_duty() -> None:
    from heaviside.pipeline.stress import flyback_stresses

    bad = {**_FLYBACK_OK, "maximumDutyCycle": 1.5}
    with pytest.raises(StressDerivationError, match="maximumDutyCycle"):
        flyback_stresses(bad)


def test_flyback_output_cap_ripple_is_iout_sqrt_d_over_1md() -> None:
    """Output-cap RMS ripple = Iout*sqrt(D/(1-D)), same as boost. Chosen at
    D=0.6 (not 0.5, where the reciprocal would look identical): the reciprocal
    sqrt((1-D)/D) undersizes the cap. Iout=2, D=0.6 -> 2*sqrt(1.5) ~= 2.449."""
    from heaviside.pipeline.stress import flyback_stresses

    s = flyback_stresses({**_FLYBACK_OK, "maximumDutyCycle": 0.6})
    iout, d = 2.0, 0.6
    assert s.i_ripple == pytest.approx(iout * (d / (1.0 - d)) ** 0.5, rel=1e-9)
    # Not the reciprocal (which would undersize for D > 0.5).
    assert s.i_ripple != pytest.approx(iout * ((1.0 - d) / d) ** 0.5, rel=1e-9)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def test_derive_stresses_dispatches_to_each_registered_topology() -> None:
    """All four registered derivers fire through derive_stresses()."""
    assert derive_stresses("buck", _BUCK_OK) is not None
    assert derive_stresses("boost", _BOOST_OK) is not None
    assert derive_stresses("flyback", _FLYBACK_OK) is not None


# ---------------------------------------------------------------------------
# Multi-op-point sweep
# ---------------------------------------------------------------------------


def test_derive_stresses_returns_worst_case_across_ops() -> None:
    """Two-op spec: op0 has Iout=5A, op1 has Iout=10A. derive_stresses
    must return the higher of the two (10A * (1 + ripple/2) = 12A)."""
    spec = {
        **_BUCK_OK,
        "operatingPoints": [
            {**_BUCK_OK["operatingPoints"][0], "outputCurrents": [5.0]},
            {**_BUCK_OK["operatingPoints"][0], "outputCurrents": [10.0]},
        ],
    }
    s = derive_stresses("buck", spec)
    assert s is not None
    # Worst-case Id is from the 10A op: 10 * (1 + 0.4/2) = 12.0
    assert s.id_stress == pytest.approx(12.0)


def test_derive_stresses_per_op_returns_one_per_op() -> None:
    from heaviside.pipeline.stress import derive_stresses_per_op

    spec = {
        **_BUCK_OK,
        "operatingPoints": [
            {**_BUCK_OK["operatingPoints"][0], "outputCurrents": [3.0]},
            {**_BUCK_OK["operatingPoints"][0], "outputCurrents": [7.0]},
            {**_BUCK_OK["operatingPoints"][0], "outputCurrents": [5.0]},
        ],
    }
    per_op = derive_stresses_per_op("buck", spec)
    assert per_op is not None
    assert len(per_op) == 3
    # First op: 3A; second: 7A; third: 5A
    assert per_op[0].id_stress == pytest.approx(3.0 * 1.2)
    assert per_op[1].id_stress == pytest.approx(7.0 * 1.2)
    assert per_op[2].id_stress == pytest.approx(5.0 * 1.2)


# ---------------------------------------------------------------------------
# Reference-design → per-ref operating-point stress (derive_stress_by_ref)
#
# This is the "start from the reference design, not a BOM" path: given a
# topology + operating spec + BOM, tag each BOM row with the CIRCUIT stress its
# ref_des actually sees. The cross-reference gates then judge substitutes
# against real operating current/voltage — how an FAE resolves an unlabelled
# part — instead of only comparing catalogue values.
# ---------------------------------------------------------------------------


def test_derive_stress_by_ref_tags_each_class_from_topology() -> None:
    from heaviside.pipeline.stress import derive_stress_by_ref

    bom = [
        {"ref_des": "Q1", "component_type": "mosfet"},
        {"ref_des": "D1", "component_type": "diode"},
        {"ref_des": "L1", "component_type": "magnetic"},
        {"ref_des": "C1", "component_type": "capacitor"},
        {"ref_des": "U1", "component_type": "controller"},  # no per-class stress
    ]
    by_ref = derive_stress_by_ref("buck", _BUCK_OK, bom)

    # Buck 48->12@5A, ripple 0.4 (same hand-calc as test_buck_stresses_*).
    assert by_ref["Q1"].v_peak == 60.0
    assert by_ref["Q1"].i_peak == pytest.approx(6.0)
    assert by_ref["D1"].v_peak == 60.0
    assert by_ref["D1"].i_avg == pytest.approx(4.0)
    # The switching inductor carries the switch peak current; RMS ~= Iout.
    assert by_ref["L1"].i_peak == pytest.approx(6.0)
    assert by_ref["L1"].i_rms == pytest.approx(5.0)
    assert by_ref["C1"].v_peak == 12.0
    assert by_ref["C1"].i_rms == pytest.approx(0.5773, abs=1e-3)
    # A class with no per-component stress mapping is simply omitted.
    assert "U1" not in by_ref


def test_derive_stress_by_ref_result_is_immutable() -> None:
    """SimDerivedStress is a frozen dataclass — the deriver must build it in one
    construction, never mutate after (the FrozenInstanceError regression)."""
    import dataclasses

    from heaviside.pipeline.stress import derive_stress_by_ref

    by_ref = derive_stress_by_ref(
        "buck", _BUCK_OK, [{"ref_des": "L1", "component_type": "magnetic"}]
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        by_ref["L1"].i_peak = 999.0


def test_derive_stress_by_ref_empty_for_unported_topology() -> None:
    from heaviside.pipeline.stress import derive_stress_by_ref

    by_ref = derive_stress_by_ref(
        "totally_made_up", _BUCK_OK, [{"ref_des": "L1", "component_type": "magnetic"}]
    )
    assert by_ref == {}


def test_derive_stress_by_ref_skips_rows_without_ref_des() -> None:
    from heaviside.pipeline.stress import derive_stress_by_ref

    by_ref = derive_stress_by_ref(
        "buck",
        _BUCK_OK,
        [{"component_type": "magnetic"}, {"ref_des": "L2", "component_type": "magnetic"}],
    )
    assert set(by_ref) == {"L2"}
