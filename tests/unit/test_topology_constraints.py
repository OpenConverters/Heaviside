"""topology-constraint-proposer (master-plan step B2).

Two-layer: a deterministic band-guarded fallback and an LLM proposer whose
output is validated against the same band + a real TAS switch class. The guard
RAISES on a violation (never silently clamps); with no API key the deterministic
fallback is used. The LLM is exercised via a fake (monkeypatched call_agent_json)
for determinism; a real-LLM smoke test is opt-in.
"""

from __future__ import annotations

import os

import pytest

from heaviside.stages import converter_spec_build
from heaviside.stages import topology_constraints as tc


def _spec():
    return {
        "inputVoltage": {"minimum": 9, "nominal": 12, "maximum": 16},
        "operatingPoints": [
            {
                "outputVoltages": [3.3],
                "outputCurrents": [3],
                "switchingFrequency": 300_000,
                "ambientTemperature": 25,
            }
        ],
        "currentRippleRatio": 0.3,
    }


# ---------------------------------------------------------------------------
# deterministic fallback
# ---------------------------------------------------------------------------


def test_deterministic_is_in_band():
    c = tc.deterministic(_spec(), "buck")
    assert c.source == "deterministic"
    assert c.maximum_duty_cycle == 0.5
    assert c.maximum_drain_source_voltage == pytest.approx(16 * 3.0)
    # in-band by construction
    tc.validate(c, _spec(), check_tas=False)


def test_deterministic_requires_vmax():
    with pytest.raises(tc.TopologyConstraintError):
        tc.deterministic({"operatingPoints": []}, "buck")


# ---------------------------------------------------------------------------
# band guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("duty", [0.0, 0.05, 0.95, 1.0, 1.5])
def test_validate_rejects_out_of_band_duty(duty):
    c = tc.DesignConstraints(duty, 48.0, "llm")
    with pytest.raises(tc.TopologyConstraintError, match="maximumDutyCycle"):
        tc.validate(c, _spec(), check_tas=False)


@pytest.mark.parametrize("vds", [16.0, 10.0, 16 * 20 + 1, -5.0])
def test_validate_rejects_out_of_band_vds(vds):
    # at or below Vmax, or above 20*Vmax, or negative
    c = tc.DesignConstraints(0.5, vds, "llm")
    with pytest.raises(tc.TopologyConstraintError, match="maximumDrainSourceVoltage"):
        tc.validate(c, _spec(), check_tas=False)


def test_validate_accepts_in_band():
    c = tc.DesignConstraints(0.45, 60.0, "llm")
    tc.validate(c, _spec(), check_tas=False)  # no raise


def test_validate_real_switch_class_present():
    # 48 V class is abundantly stocked in TAS — should pass the TAS check
    pytest.importorskip("heaviside.catalogue.selector")
    tc.validate(tc.DesignConstraints(0.5, 48.0, "llm"), _spec(), check_tas=True)


def test_validate_real_switch_class_absent_raises(monkeypatch):
    # When TAS has no production FET for the proposed Vds class, validate must
    # raise (avoid Stage-G thrash) rather than pass a class no real part backs.
    # TAS stocks a wide voltage range, so force the "absent" branch deterministically.
    monkeypatch.setattr(tc, "_real_switch_class_exists", lambda vds, spec: False)
    c = tc.DesignConstraints(0.5, 60.0, "llm")  # in-band, but no real switch (forced)
    with pytest.raises(tc.TopologyConstraintError, match="no production MOSFET"):
        tc.validate(c, _spec(), check_tas=True)


# ---------------------------------------------------------------------------
# propose — LLM path via a fake, and no-key fallback
# ---------------------------------------------------------------------------


def test_propose_no_key_uses_deterministic(monkeypatch):
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    c = tc.propose(_spec(), "buck", check_tas=False)
    assert c.source == "deterministic"
    assert c.maximum_duty_cycle == 0.5


def test_propose_llm_path_with_fake(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "fake-key")
    monkeypatch.setattr(
        tc,
        "_propose_llm",
        lambda spec, topo: tc.DesignConstraints(0.45, 60.0, "llm", "fake"),
    )
    c = tc.propose(_spec(), "flyback", check_tas=False)
    assert c.source == "llm"
    assert c.maximum_duty_cycle == 0.45
    assert c.maximum_drain_source_voltage == 60.0


def test_propose_llm_out_of_band_raises(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "fake-key")
    monkeypatch.setattr(
        tc,
        "_propose_llm",
        lambda spec, topo: tc.DesignConstraints(0.99, 60.0, "llm"),  # duty out of band
    )
    with pytest.raises(tc.TopologyConstraintError, match="maximumDutyCycle"):
        tc.propose(_spec(), "buck", check_tas=False)


def test_propose_llm_malformed_response_raises(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "fake-key")
    monkeypatch.setattr(
        tc,
        "call_agent_json" if hasattr(tc, "call_agent_json") else "_propose_llm",
        lambda *a, **k: (_ for _ in ()).throw(tc.TopologyConstraintError("malformed")),
        raising=False,
    )
    # _propose_llm itself raises on a malformed object; simulate via patch
    monkeypatch.setattr(
        tc,
        "_propose_llm",
        lambda spec, topo: (_ for _ in ()).throw(
            tc.TopologyConstraintError("malformed constraint object")
        ),
    )
    with pytest.raises(tc.TopologyConstraintError, match="malformed"):
        tc.propose(_spec(), "buck", check_tas=False)


# ---------------------------------------------------------------------------
# converter_spec_build integration — constraints centralised, not hardcoded
# ---------------------------------------------------------------------------


def test_converter_spec_build_uses_deterministic_when_no_constraints():
    s = converter_spec_build.build(_spec(), "buck")
    assert s["maximumDutyCycle"] == 0.5
    assert s["maximumDrainSourceVoltage"] == pytest.approx(48.0)


def test_converter_spec_build_accepts_explicit_constraints():
    c = tc.DesignConstraints(0.42, 80.0, "llm")
    s = converter_spec_build.build(_spec(), "buck", constraints=c)
    assert s["maximumDutyCycle"] == 0.42
    assert s["maximumDrainSourceVoltage"] == 80.0
    # per-OP duty seeded from the (constraint) ceiling
    assert s["operatingPoints"][0]["dutyCycle"] == 0.42


def test_converter_spec_build_no_inputvoltage_no_constraints():
    # a spec with no inputVoltage must not fabricate a Vds (no silent default)
    s = converter_spec_build.build({"operatingPoints": []}, "buck")
    assert "maximumDrainSourceVoltage" not in s


def test_no_hardcoded_literals_left_in_builder():
    """The 0.5 / 3·Vmax literals must live in topology_constraints, not as
    magic numbers in converter_spec_build (regression guard for the B2 move)."""
    import inspect

    src = inspect.getsource(converter_spec_build.build)
    assert "* 3.0" not in src and "*3.0" not in src


@pytest.mark.skipif(not os.environ.get("HEAVISIDE_RUN_LLM"), reason="opt-in real-LLM smoke")
def test_propose_llm_smoke_real():
    """Real proposer emits plausible, in-band, TAS-backed constraints."""
    c = tc.propose(_spec(), "buck", use_llm=True, check_tas=True)
    assert c.source == "llm"
    assert tc.DUTY_MIN < c.maximum_duty_cycle < tc.DUTY_MAX
