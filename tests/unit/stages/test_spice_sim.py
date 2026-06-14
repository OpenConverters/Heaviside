"""Unit tests for the spice_sim stage.

The engine (``simulate`` / ``_apply_knob``) is deterministic; the
ngspice-backed runs skip cleanly when ngspice is absent. ``calibrate``'s
no-LLM fallback is tested without a key (the real-LLM calibration path is
exercised separately, gated on the key + ngspice).
"""
from __future__ import annotations

import shutil

import pytest

from heaviside.stages.spice_sim import SpiceResult, _apply_knob, calibrate, simulate

HAS_NGSPICE = shutil.which("ngspice") is not None

# 1 V DC across a 1 ohm load through a 1 uH inductor + 100 uF cap (no PWM):
# after settling vout ~= 1 V, eff ~= 100%. Mirrors the runner smoke test.
_RC_DECK = """\
* simple LR-loaded DC test
Vin vin 0 DC 1
Vin_sense vin vin_dc 0
Vl_sense vin_dc l_in 0
L1 l_in vout 1u
Cout vout 0 100u IC=1
Vout_sense vout vout_load 0
Rload vout_load 0 1

.tran 1u 100m
.save v(vin_dc) v(vout) i(Vin_sense) i(Vl_sense)
.end
"""


@pytest.mark.skipif(not HAS_NGSPICE, reason="ngspice binary not on PATH")
def test_simulate_steady_state_no_target():
    r = simulate(_RC_DECK, timeout_s=30.0)
    assert isinstance(r, SpiceResult)
    assert r.closed_loop is False
    assert r.result["vout"] == pytest.approx(1.0, abs=0.02)
    assert r.converged is True  # no target -> trivially converged
    assert r.knobs == {}


@pytest.mark.skipif(not HAS_NGSPICE, reason="ngspice binary not on PATH")
def test_simulate_target_convergence_flag():
    # non-PWM deck -> steady-state path; converged judged vs the target.
    on = simulate(_RC_DECK, vout_target=1.0, tolerance=0.05, timeout_s=30.0)
    assert on.converged is True
    off = simulate(_RC_DECK, vout_target=5.0, tolerance=0.05, timeout_s=30.0)
    assert off.converged is False  # 1 V measured, 5 V wanted


def test_apply_knob_component_value_parses_si():
    out = _apply_knob(_RC_DECK, {"kind": "component_value", "refdes": "Cout", "value": "200uF"})
    assert "2.000000e-04" in out  # 200 uF written as SI float
    assert "100u" not in out.split("Cout")[1].split("\n")[0]


def test_apply_knob_unsupported_raises():
    with pytest.raises(ValueError, match="unsupported knob kind"):
        _apply_knob(_RC_DECK, {"kind": "bogus", "value": 1})


def test_apply_knob_unparseable_value_raises():
    with pytest.raises(ValueError, match="cannot parse knob value"):
        _apply_knob(_RC_DECK, {"kind": "component_value", "refdes": "Cout", "value": "abc"})


@pytest.mark.skipif(not HAS_NGSPICE, reason="ngspice binary not on PATH")
def test_calibrate_fallback_without_llm(monkeypatch):
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    r = calibrate(_RC_DECK, vout_target=1.0, efficiency_target=0.95, timeout_s=30.0)
    # no key -> pure engine baseline, no knobs touched
    assert r.knobs == {}
    assert r.result["vout"] == pytest.approx(1.0, abs=0.02)
