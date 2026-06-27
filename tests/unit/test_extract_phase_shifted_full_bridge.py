"""Tests for the phase-shifted full bridge (PSFB) realism extractor.

PSFB = four primary switches (Q_A/Q_B leg A, Q_C/Q_D leg C) each leg at
50 % complementary duty, leg C phase-shifted relative to leg A;
series resonant/leakage inductor L_r in series with the primary;
transformer T1 (pri, sec0 — CT at GND); 2-diode CT-FW secondary
rectifier (D1, D2) feeding output choke L_out0 + C_out0.  Stencil at
stencils.py:2423.

Key invariants:

  * **Effective primary duty** ``D_eff = Vout / (n · Vin)`` with
    ``n = N_sec0 / N_pri``.  ``D_eff`` is the full-period power-
    delivery fraction (sum of both half-period overlaps), so it maps
    directly onto the L_out0 secondary on-fraction without an extra
    factor of 2.  Throws at Vin_min if D_eff > 1.0 (PSFB cannot
    synthesise negative phase shift).
  * **L_out0 binds inductor_isat_margin** (L_r and T1 deliberately
    unstamped — symmetric four-switch drive zeroes net DC flux).
  * **Output choke sees 2·fsw** (CT-FW two-pulse rectifier), worst-
    case ΔI at Vin_max.
  * **PROTEUS −20 %** L tolerance baked into ripple.

Per CLAUDE.md: every missing or invalid field raises EnrichmentError.
"""

from __future__ import annotations

import pytest

from heaviside.pipeline import evaluate_tas
from heaviside.pipeline.extract import EnrichmentError, enrich_tas_for_realism
from heaviside.pipeline.realism import CheckStatus
from tests.unit._real_mas import isat_of, real_magnetic

# ---------------------------------------------------------------------------
# Fixtures matching stencil roles: inverter / isolation / outputRectifier.
#
# All magnetics are now COMPLETE, PyOM-evaluable devices built by
# :func:`real_magnetic` (real core shape + material + gap + winding list,
# completed by ``magnetic_autocomplete``) so the extractor's Isat
# enrichment — which delegates entirely to PyOM's
# ``calculate_saturation_current`` and raises on rejection — gets genuine
# gap-aware MKF physics rather than synthetic minimal-MAS PyOM rejects.
# ---------------------------------------------------------------------------


def _lr_mas(N: int = 6) -> dict:
    # Series resonant/leakage inductor; deliberately NOT Isat-stamped, so a
    # complete real magnetic with the single named winding is all that's
    # needed (extractor harvests nothing isat-bound from it).
    return real_magnetic(
        shape="ETD 29/16/10",
        material="3C95",
        gap_mm=1.0,
        windings=[{"name": "winding", "turns": N, "side": "primary"}],
    )


def _t1_mas(*, N_pri: int = 10, N_sec0: int = 1) -> dict:
    """2-winding transformer.  ``n = N_sec0 / N_pri``."""
    return real_magnetic(
        shape="ETD 34/17/11",
        material="3C95",
        gap_mm=0.0,
        windings=[
            {"name": "pri", "turns": N_pri, "side": "primary"},
            {"name": "sec0", "turns": N_sec0, "side": "secondary"},
        ],
    )


def _lout_mas(N: int = 12, *, L: float | None = 50e-6, with_outputs: bool = True) -> dict:
    """Output choke MAS.

    A complete, PyOM-evaluable gapped magnetic (built by
    :func:`real_magnetic` so ``calculate_saturation_current`` returns real
    gap-aware MKF physics) PLUS the simulation-derived ``outputs`` envelope
    carrying the inductance MKF *actually achieved*.  The realism extractor
    harvests the inductance from the full MAS root's ``outputs`` envelope
    (``outputs[0].inductance.magnetizingInductance.magnetizingInductance.nominal``),
    never from the spec hint. The real bridge-attach phase produces this
    envelope; mirror its shape here so the fixture exercises the same
    honest path. ``L`` is the achieved inductance (default 50 µH, matching
    the spec's ``desiredInductance`` so the ripple/Isat assertions hold).
    Set ``with_outputs=False`` to model an attach phase that never ran.
    """
    mas = real_magnetic(
        shape="ETD 34/17/11",
        material="3C95",
        gap_mm=1.0,
        windings=[{"name": "winding", "turns": N, "side": "primary"}],
    )
    if with_outputs and L is not None:
        mas["outputs"] = [
            {"inductance": {"magnetizingInductance": {"magnetizingInductance": {"nominal": L}}}},
        ]
    return mas


def _psfb_tas(
    *, t1_kwargs: dict | None = None, lout_kwargs: dict | None = None, lr_kwargs: dict | None = None
) -> dict:
    t1_kwargs = dict(t1_kwargs or {})
    lout_kwargs = dict(lout_kwargs or {})
    lr_kwargs = dict(lr_kwargs or {})
    return {
        "topology": {
            "stages": [
                {
                    "name": "inverter",
                    "role": "inverter",
                    "circuit": {
                        "components": [
                            {"name": "Q_A", "data": "placeholder"},
                            {"name": "Q_B", "data": "placeholder"},
                            {"name": "Q_C", "data": "placeholder"},
                            {"name": "Q_D", "data": "placeholder"},
                            {"name": "L_r", "category": "magnetic", "mas": _lr_mas(**lr_kwargs)},
                        ]
                    },
                },
                {
                    "name": "isolation",
                    "role": "isolation",
                    "circuit": {
                        "components": [
                            {"name": "T1", "category": "magnetic", "mas": _t1_mas(**t1_kwargs)},
                        ]
                    },
                },
                {
                    "name": "output_0",
                    "role": "outputRectifier",
                    "circuit": {
                        "components": [
                            {"name": "D1", "data": "placeholder"},
                            {"name": "D2", "data": "placeholder"},
                            {
                                "name": "L_out0",
                                "category": "magnetic",
                                "mas": _lout_mas(**lout_kwargs),
                            },
                            {"name": "C_out0", "data": "placeholder"},
                        ]
                    },
                },
            ],
            "interStageConnections": [],
        }
    }


def _psfb_spec() -> dict:
    """400 V → 48 V / 25 A PSFB, 100 kHz.

    With N_pri/N_sec0 = 10/1 ⇒ n = 0.1.
    D_eff_max = 48 / (0.1 · 400) = 1.20 -- THROWS at Vin_min=400.

    Use n = 1/5 by setting N_pri=5, N_sec0=1 ⇒ n = 0.2:
    D_eff_max = 48 / (0.2 · 400) = 0.6   at Vin_min = 400
    D_eff_min = 48 / (0.2 · 420) = 0.571 at Vin_max = 420
    Both ≤ 1.0 ✓.  Spec defaults use N_pri=5 below.
    """
    return {
        "inputVoltage": {"minimum": 400.0, "maximum": 420.0, "nominal": 410.0},
        "desiredInductance": 50e-6,
        "efficiency": 0.95,
        "operatingPoints": [
            {
                "outputVoltages": [48.0],
                "outputCurrents": [25.0],
                "switchingFrequency": 100_000.0,
                "ambientTemperature": 25,
            }
        ],
    }


def _t1_default() -> dict:
    return {"N_pri": 5, "N_sec0": 1}


def _get_lout(tas: dict) -> dict:
    for stage in tas["topology"]["stages"]:
        if stage.get("role") == "outputRectifier":
            for c in stage["circuit"]["components"]:
                if c.get("name") == "L_out0":
                    return c
    raise AssertionError("L_out0 not found")


def _get_lr(tas: dict) -> dict:
    for stage in tas["topology"]["stages"]:
        if stage.get("role") == "inverter":
            for c in stage["circuit"]["components"]:
                if c.get("name") == "L_r":
                    return c
    raise AssertionError("L_r not found")


def _get_t1(tas: dict) -> dict:
    for stage in tas["topology"]["stages"]:
        if stage.get("role") == "isolation":
            for c in stage["circuit"]["components"]:
                if c.get("name") == "T1":
                    return c
    raise AssertionError("T1 not found")


# ---------------------------------------------------------------------------
# Voltage transfer & duty
# ---------------------------------------------------------------------------


class TestDuty:
    def test_d_eff_at_vin_extremes(self):
        out = enrich_tas_for_realism(
            _psfb_tas(t1_kwargs=_t1_default()),
            topology="phase_shifted_full_bridge",
            spec=_psfb_spec(),
        )
        # n = 1/5 = 0.2
        d_eff_max = 48.0 / (0.2 * 400.0)  # = 0.6
        d_eff_min = 48.0 / (0.2 * 420.0)  # ≈ 0.5714
        assert out["duty_max"] == pytest.approx(d_eff_max, abs=1e-5)
        assert out["duty_min"] == pytest.approx(d_eff_min, abs=1e-5)
        assert out["duty"] == out["duty_max"]

    def test_voltage_transfer_round_trip(self):
        out = enrich_tas_for_realism(
            _psfb_tas(t1_kwargs=_t1_default()),
            topology="phase_shifted_full_bridge",
            spec=_psfb_spec(),
        )
        # Vout = n · Vin · D_eff with n = 0.2, Vin = 400, D = 0.6
        vout_reconstructed = 0.2 * 400.0 * out["duty_max"]
        assert vout_reconstructed == pytest.approx(48.0, rel=1e-4)

    def test_d_eff_consistent_with_voltage_transfer(self):
        out = enrich_tas_for_realism(
            _psfb_tas(t1_kwargs=_t1_default()),
            topology="phase_shifted_full_bridge",
            spec=_psfb_spec(),
        )
        lout = _get_lout(out)
        p = lout["ipeak_provenance"]
        # D_eff_max at Vin_min, D_eff_min at Vin_max.
        assert p["d_eff_max"] == pytest.approx(48.0 / (0.2 * 400.0), abs=1e-5)
        assert p["d_eff_min"] == pytest.approx(48.0 / (0.2 * 420.0), abs=1e-5)


# ---------------------------------------------------------------------------
# Ripple, Isat
# ---------------------------------------------------------------------------


class TestRippleAndIsat:
    def test_ripple_at_d_eff_min(self):
        out = enrich_tas_for_realism(
            _psfb_tas(t1_kwargs=_t1_default()),
            topology="phase_shifted_full_bridge",
            spec=_psfb_spec(),
        )
        lout = _get_lout(out)
        p = lout["ipeak_provenance"]
        L_worst = 0.8 * 50e-6
        fsw_eff = 2.0 * 100_000.0
        # Recompute from raw spec inputs (not from the rounded
        # provenance value) so floating-point round-trip noise on
        # d_eff_min doesn't masquerade as a physics mismatch.
        d_eff_min_raw = 48.0 / (0.2 * 420.0)
        expected = 48.0 * (1.0 - d_eff_min_raw) / (L_worst * fsw_eff)
        assert p["ripple_worst_A_pp"] == pytest.approx(expected, rel=1e-5)
        assert p["L_worst_H"] == pytest.approx(L_worst, rel=1e-12)
        assert p["fsw_effective_Hz"] == fsw_eff

    def test_ipeak_is_iout_plus_half_ripple(self):
        out = enrich_tas_for_realism(
            _psfb_tas(t1_kwargs=_t1_default()),
            topology="phase_shifted_full_bridge",
            spec=_psfb_spec(),
        )
        lout = _get_lout(out)
        ripple = lout["ipeak_provenance"]["ripple_worst_A_pp"]
        assert lout["ipeak_worst"] == pytest.approx(25.0 + ripple / 2.0, rel=1e-6)

    def test_isat_uses_lout_mas(self):
        out = enrich_tas_for_realism(
            _psfb_tas(t1_kwargs=_t1_default()),
            topology="phase_shifted_full_bridge",
            spec=_psfb_spec(),
        )
        lout = _get_lout(out)
        # Ground truth = MKF: the stamped Isat must equal PyOM's saturation
        # current for the L_out0 magnetic at the hot operating corner (100 °C, _ISAT_DESIGN_TEMP_C),
        # NOT an analytical formula. Computing it here on the same L_out0
        # MAS the extractor harvested also proves L_out0 was the source.
        expected = isat_of(_lout_mas(), temperature_c=100.0)
        assert lout["isat"] == pytest.approx(expected, rel=1e-3)
        assert "PyOM" in lout["isat_provenance"]["method"]
        assert "phase_shifted_full_bridge" in lout["isat_provenance"]["method"]


# ---------------------------------------------------------------------------
# Non-binding magnetics — L_r and T1 not stamped.
# ---------------------------------------------------------------------------


class TestNonBindingMagnetics:
    def test_lr_not_isat_stamped(self):
        out = enrich_tas_for_realism(
            _psfb_tas(t1_kwargs=_t1_default()),
            topology="phase_shifted_full_bridge",
            spec=_psfb_spec(),
        )
        lr = _get_lr(out)
        assert "isat" not in lr
        assert "ipeak_worst" not in lr

    def test_t1_not_isat_stamped(self):
        out = enrich_tas_for_realism(
            _psfb_tas(t1_kwargs=_t1_default()),
            topology="phase_shifted_full_bridge",
            spec=_psfb_spec(),
        )
        t1 = _get_t1(out)
        assert "isat" not in t1
        assert "ipeak_worst" not in t1

    def test_provenance_flags_record_unmodelled(self):
        out = enrich_tas_for_realism(
            _psfb_tas(t1_kwargs=_t1_default()),
            topology="phase_shifted_full_bridge",
            spec=_psfb_spec(),
        )
        lout = _get_lout(out)
        p = lout["ipeak_provenance"]
        assert p["l_r_isat_modelled"] is False
        assert p["t1_isat_modelled"] is False


# ---------------------------------------------------------------------------
# Turns-ratio + provenance
# ---------------------------------------------------------------------------


class TestTurnsRatio:
    def test_turns_ratio_recorded(self):
        out = enrich_tas_for_realism(
            _psfb_tas(t1_kwargs={"N_pri": 8, "N_sec0": 2}),
            topology="phase_shifted_full_bridge",
            spec=_psfb_spec(),
        )
        lout = _get_lout(out)
        p = lout["ipeak_provenance"]
        assert p["turns_ratio_n_sec0_over_n_pri"] == pytest.approx(0.25, rel=1e-6)
        assert p["n_primary"] == 8
        assert p["n_secondary"] == 2


# ---------------------------------------------------------------------------
# End-to-end realism evaluation
# ---------------------------------------------------------------------------


class TestRealismIntegration:
    def test_end_to_end_realism_evaluates(self):
        spec = _psfb_spec()
        enriched = enrich_tas_for_realism(
            _psfb_tas(t1_kwargs=_t1_default()),
            topology="phase_shifted_full_bridge",
            spec=spec,
        )
        r = evaluate_tas(enriched, topology="phase_shifted_full_bridge", spec=spec)
        check_status = {c.name: c.status for c in r.checks}
        for name in ("duty_cycle_bounds", "inductor_isat_margin"):
            assert check_status.get(name) in (CheckStatus.PASS, CheckStatus.FAIL), (
                f"{name} must be evaluated (PASS/FAIL), got {check_status.get(name)}"
            )


# ---------------------------------------------------------------------------
# Gain-impossible: D_eff > 1.0 throws.
# ---------------------------------------------------------------------------


class TestGainGuard:
    def test_d_eff_above_unity_throws(self):
        """n = 1/10 = 0.1 ⇒ D_eff = 48/(0.1·400) = 1.20 > 1.0."""
        with pytest.raises(EnrichmentError, match=r"D_eff"):
            enrich_tas_for_realism(
                _psfb_tas(t1_kwargs={"N_pri": 10, "N_sec0": 1}),
                topology="phase_shifted_full_bridge",
                spec=_psfb_spec(),
            )

    def test_d_eff_exactly_unity_passes(self):
        """n = 48/Vin_min = 48/400 = 0.12 ⇒ D_eff = 1.0 exactly.
        Use N_pri=25, N_sec0=3 ⇒ n = 0.12."""
        spec = _psfb_spec()
        out = enrich_tas_for_realism(
            _psfb_tas(t1_kwargs={"N_pri": 25, "N_sec0": 3}),
            topology="phase_shifted_full_bridge",
            spec=spec,
        )
        assert out["duty_max"] == pytest.approx(1.0, abs=1e-5)


# ---------------------------------------------------------------------------
# Structural failures — throw, never default.
# ---------------------------------------------------------------------------


class TestStructuralFailures:
    def test_missing_isolation_stage_throws(self):
        tas = _psfb_tas(t1_kwargs=_t1_default())
        tas["topology"]["stages"] = [
            s for s in tas["topology"]["stages"] if s.get("role") != "isolation"
        ]
        with pytest.raises(EnrichmentError, match="isolation"):
            enrich_tas_for_realism(tas, topology="phase_shifted_full_bridge", spec=_psfb_spec())

    def test_missing_outputrectifier_stage_throws(self):
        tas = _psfb_tas(t1_kwargs=_t1_default())
        tas["topology"]["stages"] = [
            s for s in tas["topology"]["stages"] if s.get("role") != "outputRectifier"
        ]
        with pytest.raises(EnrichmentError, match="outputRectifier"):
            enrich_tas_for_realism(tas, topology="phase_shifted_full_bridge", spec=_psfb_spec())

    def test_missing_pri_winding_throws(self):
        tas = _psfb_tas(t1_kwargs=_t1_default())
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "isolation":
                stage["circuit"]["components"][0]["mas"]["coil"]["functionalDescription"][0][
                    "name"
                ] = "primary"
        with pytest.raises(EnrichmentError, match="'pri'"):
            enrich_tas_for_realism(tas, topology="phase_shifted_full_bridge", spec=_psfb_spec())

    def test_missing_sec0_winding_throws(self):
        tas = _psfb_tas(t1_kwargs=_t1_default())
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "isolation":
                stage["circuit"]["components"][0]["mas"]["coil"]["functionalDescription"][1][
                    "name"
                ] = "secondary0"
        with pytest.raises(EnrichmentError, match="'sec0'"):
            enrich_tas_for_realism(tas, topology="phase_shifted_full_bridge", spec=_psfb_spec())

    def test_missing_achieved_inductance_throws(self):
        """The extractor harvests L from L_out0's own MAS ``outputs``
        envelope (the inductance MKF achieved), never from the spec hint.
        A fixture whose attach phase never ran (no outputs envelope) must
        throw rather than silently fall back to the spec request."""
        tas = _psfb_tas(t1_kwargs=_t1_default(), lout_kwargs={"with_outputs": False})
        with pytest.raises(EnrichmentError, match="full MAS root"):
            enrich_tas_for_realism(tas, topology="phase_shifted_full_bridge", spec=_psfb_spec())

    def test_missing_lout_mas_throws(self):
        tas = _psfb_tas(t1_kwargs=_t1_default())
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "outputRectifier":
                for c in stage["circuit"]["components"]:
                    if c.get("name") == "L_out0":
                        del c["mas"]
        with pytest.raises(EnrichmentError, match="MAS"):
            enrich_tas_for_realism(tas, topology="phase_shifted_full_bridge", spec=_psfb_spec())
