"""Ipeak_worst coverage (master-plan step B3).

The frequency sweep's saturation gate needs a worst-case peak-current computer
for every hard-switched topology it sweeps. B3 extends bridge._IPEAK_WORST to
the energy-storage-inductor topologies whose stress id_stress is the
saturation-relevant inductor current (exactly, or a conservative upper bound),
and asserts:

  * each registered computer matches the realism-gate stress formula
    (one source of truth — the post-filter and the gate can't disagree);
  * transformer topologies stay UNregistered (their id_stress is primary load
    current, not the flux current) so the sweep raises rather than silently
    mis-gating;
  * a topology with no computer raises in the sweep (trap #7).
"""
from __future__ import annotations

import pytest

from heaviside import bridge
from heaviside.pipeline.stress import derive_stresses

# Topologies B3 adds, all sharing the energy-storage-inductor saturation class.
_NEWLY_COVERED = ["sepic", "zeta", "four_switch_buck_boost"]
# Already covered before B3 (regression guard).
_PREVIOUSLY_COVERED = ["buck", "boost", "cuk", "flyback"]
# Topologies that MUST stay unregistered for the loss SWEEP: resonant (fsw from
# the gain law, B6) + non-converter magnetics. (Transformers ARE now registered
# via the magnetizing-current computer — see the transformer tests below.)
_MUST_STAY_UNREGISTERED = [
    "llc", "cllc", "series_resonant", "dual_active_bridge", "common_mode_choke",
]


# Buck-boost-class spec (Vout between/below Vin) valid for sepic/zeta/cuk/
# four_switch_buck_boost — these are the topologies B3 newly registers.
def _spec():
    return {
        "inputVoltage": {"minimum": 9, "nominal": 12, "maximum": 16},
        "operatingPoints": [{"outputVoltages": [5.0], "outputCurrents": [2.0],
                             "switchingFrequency": 300_000, "ambientTemperature": 25}],
        "currentRippleRatio": 0.3,
    }


def _boost_spec():
    # boost needs Vout > Vin_max
    s = _spec()
    s["operatingPoints"][0]["outputVoltages"] = [24.0]
    return s


@pytest.mark.parametrize("topo", _NEWLY_COVERED)
def test_newly_covered_registered(topo):
    assert bridge._IPEAK_WORST.get(topo) is not None


@pytest.mark.parametrize("topo", _NEWLY_COVERED + ["cuk"])
def test_ipeak_worst_matches_stress_formula(topo):
    """The computer must return exactly the stress deriver's id_stress — one
    formula, no drift between the post-filter and the realism gate."""
    spec = _spec()
    expected = derive_stresses(topo, spec).id_stress
    got = bridge._IPEAK_WORST[topo](spec)
    assert got == pytest.approx(expected)
    assert got > 0


def test_boost_ipeak_worst_matches_stress_formula():
    """Regression for the pre-B3 boost computer (needs Vout > Vin)."""
    spec = _boost_spec()
    assert bridge._IPEAK_WORST["boost"](spec) == pytest.approx(
        derive_stresses("boost", spec).id_stress
    )


def test_sepic_zeta_ipeak_is_conservative_sum():
    """sepic/zeta use the switch sum current iin+iout — an upper bound on the
    per-winding inductor current, so the saturation gate can only over-reject,
    never pass a saturating core. Assert it exceeds the output current."""
    spec = _spec()
    iout = 2.0
    for topo in ("sepic", "zeta"):
        assert bridge._IPEAK_WORST[topo](spec) > iout


@pytest.mark.parametrize("topo", _MUST_STAY_UNREGISTERED)
def test_unregistered_for_sweep(topo):
    """Resonant topologies (gain-law fsw, not a loss sweep) + non-converter
    magnetics must NOT be in the sweep's Ipeak table."""
    assert bridge._IPEAK_WORST.get(topo) is None


@pytest.mark.parametrize("topo", _NEWLY_COVERED + ["cuk"])
def test_covered_topologies_have_a_stress_deriver(topo):
    # every covered buck-boost-class topology must also have a stress deriver
    # to read id_stress from (boost/flyback need topology-specific specs and
    # are covered by their own tests).
    assert derive_stresses(topo, _spec()) is not None


def test_sweep_raises_for_unregistered_topology():
    """Trap #7 end-to-end: a swept topology with no computer raises rather than
    silently claiming feasibility it never checked."""
    from heaviside.stages import frequency_sweep

    # llc is resonant — no sweep Ipeak computer (it uses the gain-law branch)
    with pytest.raises(frequency_sweep.FrequencySweepError, match="Ipeak_worst computer"):
        frequency_sweep.sweep("llc", _spec())


# --- transformer magnetizing-current Ipeak (forward/bridge/push-pull) --------

_TRANSFORMERS_UNI = ["single_switch_forward", "two_switch_forward", "active_clamp_forward"]
_TRANSFORMERS_BI = ["push_pull", "asymmetric_half_bridge",
                    "phase_shifted_full_bridge", "phase_shifted_half_bridge", "weinberg"]


def test_magnetizing_ipeak_formula():
    spec = {"inputVoltage": {"minimum": 36, "nominal": 48, "maximum": 60},
            "maximumDutyCycle": 0.45, "desiredInductance": 1e-3,
            "operatingPoints": [{"switchingFrequency": 200_000}]}
    uni = bridge._ipeak_worst_magnetizing(spec, bidirectional=False)
    bi = bridge._ipeak_worst_magnetizing(spec, bidirectional=True)
    assert uni == pytest.approx(60 * 0.45 / (1e-3 * 200_000))  # ΔIm
    assert bi == pytest.approx(uni / 2)                         # ±ΔIm/2
    # incomplete spec ⇒ None (no fabrication)
    assert bridge._ipeak_worst_magnetizing(
        {"inputVoltage": {"maximum": 60}, "maximumDutyCycle": 0.45,
         "operatingPoints": [{"switchingFrequency": 2e5}]}, bidirectional=False) is None


@pytest.mark.parametrize("topo", _TRANSFORMERS_UNI + _TRANSFORMERS_BI)
def test_transformer_registered_with_magnetizing(topo):
    assert bridge._IPEAK_WORST.get(topo) is not None


@pytest.mark.parametrize("topo", ["llc", "cllc", "clllc", "series_resonant", "dual_active_bridge"])
def test_resonant_still_unregistered(topo):
    # resonant fsw comes from the gain law (B6), not the loss sweep
    assert bridge._IPEAK_WORST.get(topo) is None
