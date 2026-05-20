"""Tests for the LLC realism extractor.

LLC = half-bridge primary inverter (Q_HI/Q_LO complementary 50 % duty),
series resonant tank Cr+Lr feeding a 3-winding transformer T1 (pri,
sec1, sec2 — center-tapped secondary), full-wave secondary rectifier
(D1, D2) into C_out0.  Stencil at stencils.py:1975.

Key invariants pinned here:

  * **Duty = 0.5** for every Vin — LLC regulates by frequency, not
    duty.  Stamping a real number (not UNAVAILABLE) lets the realism
    duty-cycle-bounds check evaluate cleanly.
  * **Voltage transfer** ``Vout = Vin / (2·n)`` with
    ``n = N_pri/N_sec1`` (each half of the CT secondary).
  * **L_r is the binding magnetic** (T1 is intentionally NOT
    Isat-stamped: HB symmetric drive + Cr DC-block ⇒ no DC saturation).
  * **Ipeak_worst** combines FHA load-reflected sinusoidal envelope
    ``(π/2)·Iout/n`` scaled by sub-resonant boost factor
    ``M_max = max(1, 2·n·Vout/Vin_min)`` with the magnetizing
    triangular peak ``Vin_max/(8·Lm_worst·fsw)`` and PROTEUS −20 % L
    tolerance on Lm.

Per CLAUDE.md "throw, never default": every missing or invalid spec /
MAS field raises EnrichmentError — no silent fallbacks.
"""

from __future__ import annotations

import math

import pytest

from heaviside.pipeline import evaluate_tas
from heaviside.pipeline.extract import EnrichmentError, enrich_tas_for_realism
from heaviside.pipeline.realism import CheckStatus


# ---------------------------------------------------------------------------
# Fixtures — minimal LLC TAS matching the stencil's (inverter, isolation,
# outputRectifier, control) role structure.
# ---------------------------------------------------------------------------


def _lr_mas(N: int = 18) -> dict:
    return {
        "core": {
            "processedDescription": {
                "effectiveParameters": {
                    "effectiveArea": 1.2e-4,
                    "effectiveLength": 0.07,
                    "effectiveVolume": 8.4e-6,
                },
            },
            "functionalDescription": {
                "material": {
                    "saturation": [
                        {"magneticField": 393.0, "magneticFluxDensity": 0.36,
                         "temperature": 100.0},
                    ],
                },
            },
        },
        "coil": {"functionalDescription": [
            {"name": "winding", "numberTurns": N, "numberParallels": 1,
             "isolationSide": "primary"},
        ]},
    }


def _t1_mas(*, N_pri: int = 24, N_sec1: int = 2) -> dict:
    """3-winding CT-secondary transformer.  ``n = N_pri/N_sec1``."""
    return {
        "core": {
            "processedDescription": {
                "effectiveParameters": {
                    "effectiveArea": 1.8e-4,
                    "effectiveLength": 0.09,
                    "effectiveVolume": 1.62e-5,
                },
            },
            "functionalDescription": {
                "material": {
                    "saturation": [
                        {"magneticField": 393.0, "magneticFluxDensity": 0.30,
                         "temperature": 100.0},
                    ],
                },
            },
        },
        "coil": {"functionalDescription": [
            {"name": "pri",  "numberTurns": N_pri,  "numberParallels": 1,
             "isolationSide": "primary"},
            {"name": "sec1", "numberTurns": N_sec1, "numberParallels": 1,
             "isolationSide": "secondary"},
            {"name": "sec2", "numberTurns": N_sec1, "numberParallels": 1,
             "isolationSide": "secondary"},
        ]},
    }


def _llc_tas(*, t1_kwargs: dict | None = None,
             lr_kwargs: dict | None = None) -> dict:
    t1_kwargs = dict(t1_kwargs or {})
    lr_kwargs = dict(lr_kwargs or {})
    return {"topology": {
        "stages": [
            {
                "name": "inverter",
                "role": "inverter",
                "circuit": {"components": [
                    {"name": "Q_HI", "data": "placeholder"},
                    {"name": "Q_LO", "data": "placeholder"},
                    {"name": "C_r",  "data": "placeholder"},
                    {"name": "L_r",  "category": "magnetic",
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
                    {"name": "C_out0", "data": "placeholder"},
                ]},
            },
        ],
        "interStageCircuit": [],
    }}


def _llc_spec() -> dict:
    """400 V → 12 V / 20 A LLC.

    n = 24/2 = 12.  Resonance gain Vout = Vin/(2·n) = Vin/24.
    At Vin_nom = 400 ⇒ Vout = 16.67 V at resonance; spec asks 12 V
    ⇒ super-resonant nominal (M_nom = 24·12/400 = 0.72).
    At Vin_min = 350 ⇒ M = 24·12/350 = 0.823 — still super-resonant.
    M_max = max(1, 0.823) = 1.0 (boost factor saturates at 1.0).
    """
    return {
        "inputVoltage": {"minimum": 350.0, "maximum": 420.0, "nominal": 400.0},
        "desiredInductance": 60e-6,                      # L_r
        "desiredMagnetizingInductance": 300e-6,          # L_m
        "efficiency": 0.96,
        "operatingPoints": [{
            "outputVoltages": [12.0],
            "outputCurrents": [20.0],
            "switchingFrequency": 100_000.0,
            "ambientTemperature": 25,
        }],
    }


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
# Duty cycle: LLC is fixed 50 % complementary half-bridge.
# ---------------------------------------------------------------------------


class TestDuty:

    def test_duty_is_50pct_at_both_vin_extremes(self):
        out = enrich_tas_for_realism(_llc_tas(), topology="llc",
                                     spec=_llc_spec())
        assert out["duty"] == 0.5
        assert out["duty_min"] == 0.5
        assert out["duty_max"] == 0.5

    def test_duty_independent_of_turns_ratio(self):
        out = enrich_tas_for_realism(
            _llc_tas(t1_kwargs={"N_pri": 40, "N_sec1": 1}),
            topology="llc", spec=_llc_spec(),
        )
        assert out["duty"] == 0.5

    def test_duty_independent_of_load(self):
        spec = _llc_spec()
        spec["operatingPoints"][0]["outputCurrents"] = [0.5]
        out = enrich_tas_for_realism(_llc_tas(), topology="llc", spec=spec)
        assert out["duty"] == 0.5


# ---------------------------------------------------------------------------
# Turns ratio and gain
# ---------------------------------------------------------------------------


class TestTurnsRatioAndGain:

    def test_turns_ratio_recorded(self):
        out = enrich_tas_for_realism(
            _llc_tas(t1_kwargs={"N_pri": 30, "N_sec1": 3}),
            topology="llc", spec=_llc_spec(),
        )
        lr = _get_lr(out)
        assert lr["ipeak_provenance"][
            "turns_ratio_n_pri_over_n_sec1"] == pytest.approx(10.0, rel=1e-6)
        assert lr["ipeak_provenance"]["n_primary"] == 30
        assert lr["ipeak_provenance"]["n_secondary_half"] == 3

    def test_gain_at_vin_min_recorded(self):
        out = enrich_tas_for_realism(_llc_tas(), topology="llc",
                                     spec=_llc_spec())
        lr = _get_lr(out)
        # n = 12, Vout = 12, Vin_min = 350 ⇒ M = 24·12/350 ≈ 0.8229
        expected = 24.0 * 12.0 / 350.0
        assert lr["ipeak_provenance"]["gain_at_vin_min"] == pytest.approx(
            expected, rel=1e-4)
        # Boost factor saturates at 1.0 when super-resonant.
        assert lr["ipeak_provenance"]["boost_factor_M_max"] == 1.0

    def test_sub_resonant_boost_factor_engages(self):
        """Force low-line into sub-resonant region (M > 1)."""
        spec = _llc_spec()
        # Vout = 24 V instead of 12 ⇒ M_at_vmin = 24·24/350 ≈ 1.646
        spec["operatingPoints"][0]["outputVoltages"] = [24.0]
        out = enrich_tas_for_realism(_llc_tas(), topology="llc", spec=spec)
        lr = _get_lr(out)
        expected_M = 24.0 * 24.0 / 350.0
        assert lr["ipeak_provenance"]["boost_factor_M_max"] == pytest.approx(
            expected_M, rel=1e-4)


# ---------------------------------------------------------------------------
# Ipeak components
# ---------------------------------------------------------------------------


class TestIpeak:

    def test_load_reflected_component(self):
        out = enrich_tas_for_realism(_llc_tas(), topology="llc",
                                     spec=_llc_spec())
        lr = _get_lr(out)
        # I_load_pk = (π/2) · Iout/n = π/2 · 20/12
        expected = (math.pi / 2.0) * (20.0 / 12.0)
        assert lr["ipeak_provenance"]["i_load_pk_A"] == pytest.approx(
            expected, rel=1e-6)

    def test_magnetizing_component_uses_vin_max_and_l_worst(self):
        out = enrich_tas_for_realism(_llc_tas(), topology="llc",
                                     spec=_llc_spec())
        lr = _get_lr(out)
        # Im_pk = Vin_max / (8 · Lm_worst · fsw)
        Lm_worst = 0.8 * 300e-6
        expected = 420.0 / (8.0 * Lm_worst * 100_000.0)
        assert lr["ipeak_provenance"]["i_mag_pk_A"] == pytest.approx(
            expected, rel=1e-6)
        assert lr["ipeak_provenance"]["Lm_worst_H"] == pytest.approx(
            Lm_worst, rel=1e-12)

    def test_ipeak_combines_components_with_boost_factor(self):
        out = enrich_tas_for_realism(_llc_tas(), topology="llc",
                                     spec=_llc_spec())
        lr = _get_lr(out)
        p = lr["ipeak_provenance"]
        expected = p["boost_factor_M_max"] * p["i_load_pk_A"] + p["i_mag_pk_A"]
        assert lr["ipeak_worst"] == pytest.approx(expected, rel=1e-6)


# ---------------------------------------------------------------------------
# Isat — closed form on L_r MAS; T1 not stamped.
# ---------------------------------------------------------------------------


class TestIsat:

    def test_isat_uses_lr_mas(self):
        out = enrich_tas_for_realism(_llc_tas(), topology="llc",
                                     spec=_llc_spec())
        lr = _get_lr(out)
        # B_sat = 0.36, N = 18, A_e = 1.2e-4, L = 60e-6
        expected = 0.36 * 18 * 1.2e-4 / 60e-6
        assert lr["isat"] == pytest.approx(expected, rel=1e-4)
        assert "llc" in lr["isat_provenance"]["method"]

    def test_t1_is_not_isat_stamped(self):
        out = enrich_tas_for_realism(_llc_tas(), topology="llc",
                                     spec=_llc_spec())
        t1 = _get_t1(out)
        assert "isat" not in t1
        assert "ipeak_worst" not in t1


# ---------------------------------------------------------------------------
# End-to-end realism evaluation
# ---------------------------------------------------------------------------


class TestRealismIntegration:

    def test_end_to_end_realism_evaluates(self):
        spec = _llc_spec()
        enriched = enrich_tas_for_realism(_llc_tas(), topology="llc",
                                          spec=spec)
        r = evaluate_tas(enriched, topology="llc", spec=spec)
        check_status = {c.name: c.status for c in r.checks}
        for name in ("duty_cycle_bounds", "inductor_isat_margin"):
            assert check_status.get(name) in (CheckStatus.PASS,
                                              CheckStatus.FAIL), (
                f"{name} must be evaluated (PASS/FAIL), got "
                f"{check_status.get(name)}"
            )


# ---------------------------------------------------------------------------
# Structural failures — throw, never default.
# ---------------------------------------------------------------------------


class TestStructuralFailures:

    def test_missing_isolation_stage_throws(self):
        tas = _llc_tas()
        tas["topology"]["stages"] = [
            s for s in tas["topology"]["stages"] if s.get("role") != "isolation"
        ]
        with pytest.raises(EnrichmentError, match="isolation"):
            enrich_tas_for_realism(tas, topology="llc", spec=_llc_spec())

    def test_missing_inverter_stage_throws(self):
        tas = _llc_tas()
        tas["topology"]["stages"] = [
            s for s in tas["topology"]["stages"] if s.get("role") != "inverter"
        ]
        with pytest.raises(EnrichmentError, match="inverter"):
            enrich_tas_for_realism(tas, topology="llc", spec=_llc_spec())

    def test_missing_pri_winding_throws(self):
        tas = _llc_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "isolation":
                stage["circuit"]["components"][0]["mas"]["coil"][
                    "functionalDescription"][0]["name"] = "primary"
        with pytest.raises(EnrichmentError, match="'pri'"):
            enrich_tas_for_realism(tas, topology="llc", spec=_llc_spec())

    def test_missing_sec1_winding_throws(self):
        tas = _llc_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "isolation":
                stage["circuit"]["components"][0]["mas"]["coil"][
                    "functionalDescription"][1]["name"] = "secondary1"
        with pytest.raises(EnrichmentError, match="'sec1'"):
            enrich_tas_for_realism(tas, topology="llc", spec=_llc_spec())

    def test_missing_desiredInductance_throws(self):
        spec = _llc_spec()
        del spec["desiredInductance"]
        with pytest.raises(EnrichmentError, match="desiredInductance"):
            enrich_tas_for_realism(_llc_tas(), topology="llc", spec=spec)

    def test_missing_desiredMagnetizingInductance_throws(self):
        spec = _llc_spec()
        del spec["desiredMagnetizingInductance"]
        with pytest.raises(EnrichmentError,
                           match="desiredMagnetizingInductance"):
            enrich_tas_for_realism(_llc_tas(), topology="llc", spec=spec)

    def test_missing_lr_mas_throws(self):
        tas = _llc_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "inverter":
                for c in stage["circuit"]["components"]:
                    if c.get("name") == "L_r":
                        del c["mas"]
        with pytest.raises(EnrichmentError, match="MAS"):
            enrich_tas_for_realism(tas, topology="llc", spec=_llc_spec())
