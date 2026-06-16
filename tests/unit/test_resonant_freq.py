"""resonant_freq stage (master-plan step B6).

fsw for a resonant converter is set by the tank GAIN LAW within MKF's fsw
window, NOT by a loss argmin (which would run to the EMI ceiling under ZVS).
Tested with injected tanks so it is independent of MKF's resonant solver.
"""
from __future__ import annotations

import math

import pytest

from heaviside.stages import resonant_freq as rf


def _resonant_spec(fnom=200_000):
    # converter_spec_build centres the window on fnom: min=fnom*0.5, max=fnom*2
    return {
        "inputVoltage": {"minimum": 380, "nominal": 400, "maximum": 420},
        "operatingPoints": [{"outputVoltages": [48.0], "outputCurrents": [10.0],
                             "switchingFrequency": fnom}],
        "minSwitchingFrequency": fnom * 0.5,
        "maxSwitchingFrequency": fnom * 2.0,
    }


def _tank(fr=200_000, m=6.0, q=0.4):
    # choose Lr, Cr to hit fr; Lm from m; Rac from Q
    # fr = 1/(2π√(Lr Cr)); pick Lr then Cr
    lr = 50e-6
    cr = 1.0 / (lr * (2 * math.pi * fr) ** 2)
    lm = (m - 1.0) * lr
    rac = math.sqrt(lr / cr) / q
    return rf.ResonantTank(lr_h=lr, cr_f=cr, lm_h=lm, rac_ohm=rac)


# ---------------------------------------------------------------------------
# tank algebra
# ---------------------------------------------------------------------------


def test_tank_derived_quantities():
    t = _tank(fr=200_000, m=6.0, q=0.4)
    assert t.fr_hz == pytest.approx(200_000, rel=1e-6)
    assert t.m_ratio == pytest.approx(6.0, rel=1e-6)
    assert t.q_factor == pytest.approx(0.4, rel=1e-6)


def test_fha_gain_is_unity_at_resonance():
    # defining property: M(fn=1) == 1 for ANY Q and m
    for q in (0.2, 0.5, 1.0):
        for m in (3.0, 6.0, 10.0):
            assert rf.fha_gain_llc(1.0, m, q) == pytest.approx(1.0, rel=1e-9)


def test_fha_gain_monotone_decreasing_above_resonance():
    m, q = 6.0, 0.4
    g = [rf.fha_gain_llc(fn, m, q) for fn in (1.0, 1.2, 1.5, 2.0)]
    assert all(g[i] > g[i + 1] for i in range(len(g) - 1))


# ---------------------------------------------------------------------------
# resonant frequency from the window
# ---------------------------------------------------------------------------


def test_resonant_frequency_is_geometric_mean():
    assert rf.resonant_frequency(_resonant_spec(200_000)) == pytest.approx(200_000, rel=1e-9)


def test_window_required():
    with pytest.raises(rf.ResonantFrequencyError, match="minSwitchingFrequency"):
        rf.resonant_frequency({"inputVoltage": {"maximum": 400}})


# ---------------------------------------------------------------------------
# fsw selection
# ---------------------------------------------------------------------------


def test_unity_gain_returns_resonant_point():
    op = rf.select_resonant_fsw(_resonant_spec(200_000))
    assert op.fsw_hz == pytest.approx(200_000, rel=1e-9)
    assert op.gain == pytest.approx(1.0)
    assert op.in_window
    assert op.switching_loss_w == 0.0  # ZVS, not swept


def test_stepdown_gain_lands_above_resonance_and_in_window():
    spec = _resonant_spec(200_000)
    tank = _tank(fr=200_000, m=6.0, q=0.4)
    op = rf.select_resonant_fsw(spec, tank=tank, required_gain=0.85)
    assert op.fsw_hz > tank.fr_hz  # step-down ⇒ above resonance
    assert spec["minSwitchingFrequency"] <= op.fsw_hz <= spec["maxSwitchingFrequency"]
    assert op.gain == pytest.approx(0.85, rel=1e-3)


def test_boost_gain_lands_below_resonance():
    spec = _resonant_spec(200_000)
    tank = _tank(fr=200_000, m=6.0, q=0.4)
    op = rf.select_resonant_fsw(spec, tank=tank, required_gain=1.15)
    assert op.fsw_hz < tank.fr_hz
    assert spec["minSwitchingFrequency"] <= op.fsw_hz <= spec["maxSwitchingFrequency"]
    assert op.gain == pytest.approx(1.15, rel=1e-3)


def test_unachievable_gain_raises_not_clamps():
    spec = _resonant_spec(200_000)
    tank = _tank(fr=200_000, m=6.0, q=0.4)
    # a gain far below what the window's upper fsw can reach
    with pytest.raises(rf.ResonantFrequencyError, match="unachievable"):
        rf.select_resonant_fsw(spec, tank=tank, required_gain=0.05)


def test_no_emi_ceiling_runaway():
    """The hallmark resonant bug: a loss model with P_sw=0 would push fsw to
    fmax. The gain law instead pins fsw at/near fr regardless — assert the
    unity-gain pick is the resonant point, never the window ceiling."""
    spec = _resonant_spec(200_000)
    op = rf.select_resonant_fsw(spec)
    assert op.fsw_hz < spec["maxSwitchingFrequency"]
    assert op.fsw_hz == pytest.approx(rf.resonant_frequency(spec))


# ---------------------------------------------------------------------------
# routing predicate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("topo,expected", [
    ("llc", True), ("series_resonant", True), ("cllc", True),
    ("buck", False), ("boost", False), ("flyback", False),
])
def test_is_resonant_predicate(topo, expected):
    assert rf.is_resonant(topo) is expected
