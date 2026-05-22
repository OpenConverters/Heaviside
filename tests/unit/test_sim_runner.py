"""Tests for ``heaviside.sim.runner``.

Mix of pure-Python tests for parsing/helpers and an integration test
that actually shells out to ngspice (skipped if the binary isn't present).
"""

from __future__ import annotations

import shutil

import pytest

from heaviside.sim.runner import (
    SimError,
    SimResult,
    _inject_meas,
    _parse_meas_output,
    _patch_tran_for_steady_state,
    _read_pwm_pulse,
    _rewrite_lossy_testbench,
    _rewrite_pwm_duty,
    _saved_probes,
    _select_probes,
    _spice_time,
    simulate_steady_state,
    stamp_simulation_results,
)

# ---------------------------------------------------------------------------
# _spice_time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token,expected", [
    ("2.5e-8", 2.5e-8),
    ("250u",   250e-6),
    ("250us",  250e-6),
    ("100n",   100e-9),
    ("1m",     1e-3),
    ("5k",     5e3),
    ("3meg",   3e6),
    ("0.5",    0.5),
])
def test_spice_time_parses_common_forms(token: str, expected: float) -> None:
    assert _spice_time(token) == pytest.approx(expected)


def test_spice_time_rejects_garbage() -> None:
    with pytest.raises(SimError):
        _spice_time("not_a_number")


# ---------------------------------------------------------------------------
# _saved_probes
# ---------------------------------------------------------------------------


def test_saved_probes_collects_v_and_i_refs() -> None:
    deck = ".save v(vin_dc) i(Vin_sense) v(VOUT)\n"
    assert _saved_probes(deck) == {"v(vin_dc)", "i(vin_sense)", "v(vout)"}


def test_saved_probes_folds_plus_continuations() -> None:
    deck = ".save v(vin_dc) v(vout)\n+ i(Vin_sense) i(Vout_sense)\n"
    probes = _saved_probes(deck)
    assert {"v(vin_dc)", "v(vout)", "i(vin_sense)", "i(vout_sense)"} <= probes


# ---------------------------------------------------------------------------
# _select_probes
# ---------------------------------------------------------------------------


def test_select_probes_buck_family() -> None:
    deck = ".save v(vin_dc) v(vout) i(Vin_sense) i(Vl_sense)\n"
    quad = _select_probes(deck)
    assert quad == ("v(vin_dc)", "i(vin_sense)", "v(vout)", "i(vl_sense)")


def test_select_probes_raises_on_unrecognised_deck() -> None:
    deck = ".save v(nonsense_probe)\n"
    with pytest.raises(SimError, match="could not match any probe"):
        _select_probes(deck)


# ---------------------------------------------------------------------------
# _patch_tran_for_steady_state
# ---------------------------------------------------------------------------


def test_patch_tran_extends_short_window_and_adds_uic() -> None:
    """Buck-class .tran with 275 us is too short for L-C settling.
    Patcher should stretch it (>= 10 ms target) and add UIC."""
    deck = ".tran 2.5e-8 2.75e-4\nfoo bar\n.end\n"
    patched, t_start, t_stop = _patch_tran_for_steady_state(deck)
    assert "UIC" in patched
    assert t_stop >= 10e-3
    assert 0 < t_start < t_stop
    # Steady-state window is at least 25 % of t_stop (last quarter).
    assert (t_stop - t_start) >= 0.2 * t_stop


def test_patch_tran_preserves_longer_existing_window() -> None:
    """If the deck already runs for 50 ms, don't shrink it."""
    deck = ".tran 1e-7 5e-2\n.end\n"
    _, _, t_stop = _patch_tran_for_steady_state(deck)
    assert t_stop >= 5e-2


def test_patch_tran_raises_on_missing_tran() -> None:
    with pytest.raises(SimError, match=r"no '\.tran'"):
        _patch_tran_for_steady_state("* just a comment\n.end\n")


# ---------------------------------------------------------------------------
# _inject_meas
# ---------------------------------------------------------------------------


def test_inject_meas_splices_before_end() -> None:
    deck = ".tran 1u 10m\nL1 a b 1m\n.end\n"
    annotated = _inject_meas(
        deck, t_start=7.5e-3, t_stop=10e-3,
        vin="v(vin_dc)", iin="i(vin_sense)",
        vout="v(vout)", iout="i(vl_sense)",
    )
    # All four .meas directives present.
    assert annotated.count(".meas tran hsv_") == 4
    # Spliced BEFORE the .end (no .meas after .end).
    body, after_end = annotated.split(".end", 1)
    assert ".meas tran hsv_vout" in body
    assert ".meas tran" not in after_end


# ---------------------------------------------------------------------------
# _parse_meas_output
# ---------------------------------------------------------------------------


def test_parse_meas_output_extracts_named_values() -> None:
    stdout = (
        "Total elapsed time: 1.234 s\n"
        "hsv_vin              =  4.799e+01 FROM=  2.5e-04 TO=  2.75e-04\n"
        "hsv_iin              =  1.234567   FROM=  2.5e-04 TO=  2.75e-04\n"
        "hsv_vout             =  1.13e+01\n"
        "hsv_iout             = -4.7e+00 FROM=...\n"
        "Note: some other text\n"
    )
    out = _parse_meas_output(stdout)
    assert out["hsv_vin"] == pytest.approx(47.99)
    assert out["hsv_iin"] == pytest.approx(1.234567)
    assert out["hsv_vout"] == pytest.approx(11.3)
    assert out["hsv_iout"] == pytest.approx(-4.7)


def test_parse_meas_output_ignores_unrelated_lines() -> None:
    assert _parse_meas_output("") == {}
    assert _parse_meas_output("just noise\n") == {}


# ---------------------------------------------------------------------------
# stamp_simulation_results
# ---------------------------------------------------------------------------


def test_stamp_simulation_results_writes_op0_block() -> None:
    tas: dict = {}
    result = SimResult(
        vin=48.0, iin=1.5, vout=12.0, iout=5.0,
        pin=72.0, pout=60.0, total_losses=12.0, efficiency=0.833,
    )
    stamp_simulation_results(tas, result)
    assert tas["simulation_results"]["op0"]["vin"] == 48.0
    assert tas["simulation_results"]["op0"]["efficiency"] == pytest.approx(0.833)


def test_stamp_simulation_results_refuses_non_mapping() -> None:
    tas: dict = {"simulation_results": "not a mapping"}
    result = SimResult(vin=1, iin=1, vout=1, iout=1, pin=1, pout=1, total_losses=0, efficiency=1.0)
    with pytest.raises(SimError, match="not a mapping"):
        stamp_simulation_results(tas, result)


# ---------------------------------------------------------------------------
# Integration: actually shell out to ngspice
# ---------------------------------------------------------------------------


HAS_NGSPICE = shutil.which("ngspice") is not None


@pytest.mark.skipif(not HAS_NGSPICE, reason="ngspice binary not on PATH")
def test_simulate_steady_state_runs_simple_rc_circuit() -> None:
    """Smoke test: 1 V DC across a 1 ohm load through a 1 uH inductor +
    100 uF cap. After settling, vout ~= 1 V, iout ~= 1 A, eff ~= 100%."""
    deck = """\
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
    result = simulate_steady_state(deck, timeout_s=30.0)
    assert result.vin == pytest.approx(1.0, abs=0.01)
    assert result.vout == pytest.approx(1.0, abs=0.02)
    assert 0.9 <= result.efficiency <= 1.05


@pytest.mark.skipif(not HAS_NGSPICE, reason="ngspice binary not on PATH")
def test_simulate_steady_state_raises_on_garbage_deck() -> None:
    """Non-circuit text fails .tran parsing and the runner raises cleanly."""
    with pytest.raises(SimError, match=r"no '\.tran'"):
        simulate_steady_state("not a spice deck\n.end\n")


# ---------------------------------------------------------------------------
# _rewrite_lossy_testbench
# ---------------------------------------------------------------------------


def test_rewrite_snubber_resistor_to_realistic_value() -> None:
    """MKF's 100 Ω snubber gets bumped to 10 kΩ so it stops dominating
    the deck's measured efficiency."""
    deck = (
        "Rsnub_s1 sw 0 100.000000\n"
        "Csnub_s1 sw 0 1.000000e-10\n"
        ".end\n"
    )
    out = _rewrite_lossy_testbench(deck)
    assert "100.000000" not in out  # original gone
    # New value present in scientific notation.
    assert "1.000000e+04" in out


def test_rewrite_idealised_diode_model() -> None:
    """DIDEAL gets a realistic Schottky-class model."""
    deck = (
        ".model DIDEAL D(IS=1.000000e-14 RS=1.000000e-06)\n"
        ".end\n"
    )
    out = _rewrite_lossy_testbench(deck)
    assert "1.000000e-14" not in out
    assert "RS=0.05" in out


def test_rewrite_is_no_op_on_clean_deck() -> None:
    """Decks without Rsnub_/Csnub_/DIDEAL should pass through unchanged."""
    deck = "Vin vin 0 12\nL1 vin out 1u\nCout out 0 100u\n.end\n"
    assert _rewrite_lossy_testbench(deck) == deck


# ---------------------------------------------------------------------------
# PWM PULSE rewrite (closed-loop duty search building block)
# ---------------------------------------------------------------------------


def test_read_pwm_pulse_extracts_pw_and_per() -> None:
    deck = "Vpwm pwm_ctrl 0 PULSE(0 5 0 1e-08 1e-08 1.5e-06 5.0e-06)\n.end\n"
    pulse = _read_pwm_pulse(deck)
    assert pulse is not None
    pw, per = pulse
    assert pw == pytest.approx(1.5e-6)
    assert per == pytest.approx(5.0e-6)


def test_read_pwm_pulse_returns_none_when_absent() -> None:
    assert _read_pwm_pulse("Vin in 0 12\n.end\n") is None


def test_rewrite_pwm_duty_changes_only_pw_field() -> None:
    deck = "Vpwm pwm_ctrl 0 PULSE(0 5 0 1e-08 1e-08 1.5e-06 5.0e-06)\n"
    out = _rewrite_pwm_duty(deck, new_duty=0.40, period_s=5.0e-6)
    pulse = _read_pwm_pulse(out)
    assert pulse is not None
    pw, per = pulse
    # New duty = 0.40 -> PW = 0.40 * 5 us = 2 us.
    assert pw == pytest.approx(2.0e-6, rel=1e-6)
    assert per == pytest.approx(5.0e-6)
    # The other PULSE fields (V1, V2, TD, TR, TF) must be preserved.
    assert "PULSE(0 5 0" in out


def test_rewrite_pwm_duty_rejects_out_of_range() -> None:
    deck = "Vpwm pwm_ctrl 0 PULSE(0 5 0 1e-08 1e-08 1.5e-06 5.0e-06)\n"
    with pytest.raises(SimError, match="must be in"):
        _rewrite_pwm_duty(deck, new_duty=1.5, period_s=5.0e-6)


def test_rewrite_pwm_duty_raises_when_no_pwm_source() -> None:
    with pytest.raises(SimError, match="no PWM PULSE source"):
        _rewrite_pwm_duty("Vin in 0 12\n.end\n", new_duty=0.4, period_s=5e-6)
