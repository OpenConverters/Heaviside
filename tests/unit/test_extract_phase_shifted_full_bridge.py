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


# ---------------------------------------------------------------------------
# Fixtures matching stencil roles: inverter / isolation / outputRectifier.
# ---------------------------------------------------------------------------


def _lr_mas(N: int = 6) -> dict:
    return {
        "core": {
            "processedDescription": {
                "effectiveParameters": {
                    "effectiveArea": 1.0e-4,
                    "effectiveLength": 0.05,
                    "effectiveVolume": 5.0e-6,
                },
            },
            "functionalDescription": {
                "material": {"saturation": [
                    {"magneticField": 393.0, "magneticFluxDensity": 0.30,
                     "temperature": 100.0},
                ]},
            },
        },
        "coil": {"functionalDescription": [
            {"name": "winding", "numberTurns": N, "numberParallels": 1,
             "isolationSide": "primary"},
        ]},
    }


def _t1_mas(*, N_pri: int = 10, N_sec0: int = 1) -> dict:
    """2-winding transformer.  ``n = N_sec0 / N_pri``."""
    return {
        "core": {
            "processedDescription": {
                "effectiveParameters": {
                    "effectiveArea": 2.0e-4,
                    "effectiveLength": 0.10,
                    "effectiveVolume": 2.0e-5,
                },
            },
            "functionalDescription": {
                "material": {"saturation": [
                    {"magneticField": 393.0, "magneticFluxDensity": 0.30,
                     "temperature": 100.0},
                ]},
            },
        },
        "coil": {"functionalDescription": [
            {"name": "pri",  "numberTurns": N_pri,  "numberParallels": 1,
             "isolationSide": "primary"},
            {"name": "sec0", "numberTurns": N_sec0, "numberParallels": 1,
             "isolationSide": "secondary"},
        ]},
    }


def _lout_mas(N: int = 12) -> dict:
    return {
        "core": {
            "processedDescription": {
                "effectiveParameters": {
                    "effectiveArea": 1.5e-4,
                    "effectiveLength": 0.06,
                    "effectiveVolume": 9.0e-6,
                },
            },
            "functionalDescription": {
                "material": {"saturation": [
                    {"magneticField": 393.0, "magneticFluxDensity": 0.42,
                     "temperature": 100.0},
                ]},
            },
        },
        "coil": {"functionalDescription": [
            {"name": "winding", "numberTurns": N, "numberParallels": 1,
             "isolationSide": "primary"},
        ]},
    }


def _psfb_tas(*, t1_kwargs: dict | None = None,
              lout_kwargs: dict | None = None,
              lr_kwargs: dict | None = None) -> dict:
    t1_kwargs = dict(t1_kwargs or {})
    lout_kwargs = dict(lout_kwargs or {})
    lr_kwargs = dict(lr_kwargs or {})
    return {"topology": {
        "stages": [
            {
                "name": "inverter",
                "role": "inverter",
                "circuit": {"components": [
                    {"name": "Q_A", "data": "placeholder"},
                    {"name": "Q_B", "data": "placeholder"},
                    {"name": "Q_C", "data": "placeholder"},
                    {"name": "Q_D", "data": "placeholder"},
                    {"name": "L_r", "category": "magnetic",
                     "mas": _lr_mas(**lr_kwargs)},
                ]},
            },
            {
                "name": "isolation",
                "role": "isolation",
                "circuit": {"components": [
                    {"name": "T1", "category": "magnetic",
                     "mas": _t1_mas(**t1_kwargs)},
                ]},
            },
            {
                "name": "output_0",
                "role": "outputRectifier",
                "circuit": {"components": [
                    {"name": "D1",     "data": "placeholder"},
                    {"name": "D2",     "data": "placeholder"},
                    {"name": "L_out0", "category": "magnetic",
                     "mas": _lout_mas(**lout_kwargs)},
                    {"name": "C_out0", "data": "placeholder"},
                ]},
            },
        ],
        "interStageCircuit": [],
    }}


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
        "operatingPoints": [{
            "outputVoltages": [48.0],
            "outputCurrents": [25.0],
            "switchingFrequency": 100_000.0,
            "ambientTemperature": 25,
        }],
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
            topology="phase_shifted_full_bridge", spec=_psfb_spec(),
        )
        # n = 1/5 = 0.2
        d_eff_max = 48.0 / (0.2 * 400.0)   # = 0.6
        d_eff_min = 48.0 / (0.2 * 420.0)   # ≈ 0.5714
        assert out["duty_max"] == pytest.approx(d_eff_max, abs=1e-5)
        assert out["duty_min"] == pytest.approx(d_eff_min, abs=1e-5)
        assert out["duty"] == out["duty_max"]

    def test_voltage_transfer_round_trip(self):
        out = enrich_tas_for_realism(
            _psfb_tas(t1_kwargs=_t1_default()),
            topology="phase_shifted_full_bridge", spec=_psfb_spec(),
        )
        # Vout = n · Vin · D_eff with n = 0.2, Vin = 400, D = 0.6
        vout_reconstructed = 0.2 * 400.0 * out["duty_max"]
        assert vout_reconstructed == pytest.approx(48.0, rel=1e-4)

    def test_d_eff_consistent_with_voltage_transfer(self):
        out = enrich_tas_for_realism(
            _psfb_tas(t1_kwargs=_t1_default()),
            topology="phase_shifted_full_bridge", spec=_psfb_spec(),
        )
        lout = _get_lout(out)
        p = lout["ipeak_provenance"]
        # D_eff_max at Vin_min, D_eff_min at Vin_max.
        assert p["d_eff_max"] == pytest.approx(48.0 / (0.2 * 400.0),
                                               abs=1e-5)
        assert p["d_eff_min"] == pytest.approx(48.0 / (0.2 * 420.0),
                                               abs=1e-5)


# ---------------------------------------------------------------------------
# Ripple, Isat
# ---------------------------------------------------------------------------


class TestRippleAndIsat:

    def test_ripple_at_d_eff_min(self):
        out = enrich_tas_for_realism(
            _psfb_tas(t1_kwargs=_t1_default()),
            topology="phase_shifted_full_bridge", spec=_psfb_spec(),
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
            topology="phase_shifted_full_bridge", spec=_psfb_spec(),
        )
        lout = _get_lout(out)
        ripple = lout["ipeak_provenance"]["ripple_worst_A_pp"]
        assert lout["ipeak_worst"] == pytest.approx(25.0 + ripple / 2.0,
                                                    rel=1e-6)

    def test_isat_uses_lout_mas(self):
        out = enrich_tas_for_realism(
            _psfb_tas(t1_kwargs=_t1_default()),
            topology="phase_shifted_full_bridge", spec=_psfb_spec(),
        )
        lout = _get_lout(out)
        # B_sat = 0.42, N = 12, A_e = 1.5e-4, L = 50e-6
        expected = 0.42 * 12 * 1.5e-4 / 50e-6
        assert lout["isat"] == pytest.approx(expected, rel=1e-4)
        assert "phase_shifted_full_bridge" in lout["isat_provenance"]["method"]


# ---------------------------------------------------------------------------
# Non-binding magnetics — L_r and T1 not stamped.
# ---------------------------------------------------------------------------


class TestNonBindingMagnetics:

    def test_lr_not_isat_stamped(self):
        out = enrich_tas_for_realism(
            _psfb_tas(t1_kwargs=_t1_default()),
            topology="phase_shifted_full_bridge", spec=_psfb_spec(),
        )
        lr = _get_lr(out)
        assert "isat" not in lr
        assert "ipeak_worst" not in lr

    def test_t1_not_isat_stamped(self):
        out = enrich_tas_for_realism(
            _psfb_tas(t1_kwargs=_t1_default()),
            topology="phase_shifted_full_bridge", spec=_psfb_spec(),
        )
        t1 = _get_t1(out)
        assert "isat" not in t1
        assert "ipeak_worst" not in t1

    def test_provenance_flags_record_unmodelled(self):
        out = enrich_tas_for_realism(
            _psfb_tas(t1_kwargs=_t1_default()),
            topology="phase_shifted_full_bridge", spec=_psfb_spec(),
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
            topology="phase_shifted_full_bridge", spec=_psfb_spec(),
        )
        lout = _get_lout(out)
        p = lout["ipeak_provenance"]
        assert p["turns_ratio_n_sec0_over_n_pri"] == pytest.approx(0.25,
                                                                    rel=1e-6)
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
            topology="phase_shifted_full_bridge", spec=spec,
        )
        r = evaluate_tas(enriched, topology="phase_shifted_full_bridge",
                         spec=spec)
        check_status = {c.name: c.status for c in r.checks}
        for name in ("duty_cycle_bounds", "inductor_isat_margin"):
            assert check_status.get(name) in (CheckStatus.PASS,
                                              CheckStatus.FAIL), (
                f"{name} must be evaluated (PASS/FAIL), got "
                f"{check_status.get(name)}"
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
                topology="phase_shifted_full_bridge", spec=_psfb_spec(),
            )

    def test_d_eff_exactly_unity_passes(self):
        """n = 48/Vin_min = 48/400 = 0.12 ⇒ D_eff = 1.0 exactly.
        Use N_pri=25, N_sec0=3 ⇒ n = 0.12."""
        spec = _psfb_spec()
        out = enrich_tas_for_realism(
            _psfb_tas(t1_kwargs={"N_pri": 25, "N_sec0": 3}),
            topology="phase_shifted_full_bridge", spec=spec,
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
            enrich_tas_for_realism(tas,
                                   topology="phase_shifted_full_bridge",
                                   spec=_psfb_spec())

    def test_missing_outputrectifier_stage_throws(self):
        tas = _psfb_tas(t1_kwargs=_t1_default())
        tas["topology"]["stages"] = [
            s for s in tas["topology"]["stages"]
            if s.get("role") != "outputRectifier"
        ]
        with pytest.raises(EnrichmentError, match="outputRectifier"):
            enrich_tas_for_realism(tas,
                                   topology="phase_shifted_full_bridge",
                                   spec=_psfb_spec())

    def test_missing_pri_winding_throws(self):
        tas = _psfb_tas(t1_kwargs=_t1_default())
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "isolation":
                stage["circuit"]["components"][0]["mas"]["coil"][
                    "functionalDescription"][0]["name"] = "primary"
        with pytest.raises(EnrichmentError, match="'pri'"):
            enrich_tas_for_realism(tas,
                                   topology="phase_shifted_full_bridge",
                                   spec=_psfb_spec())

    def test_missing_sec0_winding_throws(self):
        tas = _psfb_tas(t1_kwargs=_t1_default())
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "isolation":
                stage["circuit"]["components"][0]["mas"]["coil"][
                    "functionalDescription"][1]["name"] = "secondary0"
        with pytest.raises(EnrichmentError, match="'sec0'"):
            enrich_tas_for_realism(tas,
                                   topology="phase_shifted_full_bridge",
                                   spec=_psfb_spec())

    def test_missing_desiredInductance_throws(self):
        spec = _psfb_spec()
        del spec["desiredInductance"]
        with pytest.raises(EnrichmentError, match="desiredInductance"):
            enrich_tas_for_realism(_psfb_tas(t1_kwargs=_t1_default()),
                                   topology="phase_shifted_full_bridge",
                                   spec=spec)

    def test_missing_lout_mas_throws(self):
        tas = _psfb_tas(t1_kwargs=_t1_default())
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "outputRectifier":
                for c in stage["circuit"]["components"]:
                    if c.get("name") == "L_out0":
                        del c["mas"]
        with pytest.raises(EnrichmentError, match="MAS"):
            enrich_tas_for_realism(tas,
                                   topology="phase_shifted_full_bridge",
                                   spec=_psfb_spec())
