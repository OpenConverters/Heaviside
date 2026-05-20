"""Tests for the CLLLC realism extractor.

CLLLC = bidirectional symmetric resonant: HV full bridge (Q1..Q4)
drives series tank C_r1 + L_r1, then 2-winding transformer T1
(pri, sec0), then secondary tank L_r2 + C_r2 feeding LV
synchronous-rectifier bridge (Q5..Q8).  Stencil at stencils.py:3477.
Stencil component order inside the isolation stage:
``[C_r1, L_r1, T1, L_r2, C_r2]``.

Key invariants pinned here:

  * **Duty = 0.5** at both Vin extremes — CLLLC regulates by
    frequency, identical to LLC.
  * **Voltage transfer** ``Vout = Vin / n`` (no factor of 2 — both
    bridges are full-bridges driving full Vin across each tank,
    unlike LLC's half-bridge).
  * **L_r1 binds Isat** (T1 + L_r2 deliberately NOT stamped, see
    extractor docstring rationale).
  * **L_r1 must be the first magnetic in the isolation stage** —
    extractor enforces stencil ordering (C_r1, L_r1, T1, L_r2, C_r2)
    by name check; rearranging throws.
  * **Ipeak_worst** = ``M_max·(π/2)·Iout/n + Vin_max/(4·Lm_worst·fsw)``
    with ``M_max = max(1, n·Vout/Vin_min)`` and PROTEUS −20 % L
    tolerance.  Magnetizing /4 (not /8 as LLC HB) because the FB
    primary drives full ±Vin across the magnetizing path.

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
# Fixtures
# ---------------------------------------------------------------------------


def _lr_mas(N: int = 12, *, A_e: float = 1.2e-4, B_sat: float = 0.36,
            L_e: float = 0.07) -> dict:
    return {
        "core": {
            "processedDescription": {
                "effectiveParameters": {
                    "effectiveArea": A_e,
                    "effectiveLength": L_e,
                    "effectiveVolume": A_e * L_e,
                },
            },
            "functionalDescription": {
                "material": {"saturation": [
                    {"magneticField": 393.0, "magneticFluxDensity": B_sat,
                     "temperature": 100.0},
                ]},
            },
        },
        "coil": {"functionalDescription": [
            {"name": "winding", "numberTurns": N, "numberParallels": 1,
             "isolationSide": "primary"},
        ]},
    }


def _t1_mas(*, N_pri: int = 8, N_sec0: int = 2) -> dict:
    """2-winding step-down transformer.  ``n = N_pri/N_sec0``."""
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


def _clllc_tas(*, t1_kwargs: dict | None = None,
               lr1_kwargs: dict | None = None,
               lr2_kwargs: dict | None = None,
               component_order: list[str] | None = None) -> dict:
    """Stencil-matching CLLLC TAS.

    ``component_order`` (optional) overrides the default isolation-stage
    component sequence ``[C_r1, L_r1, T1, L_r2, C_r2]`` — used by the
    structural-failure test that rearranges the order to verify the
    extractor's stencil-ordering invariant check.
    """
    t1_kwargs  = dict(t1_kwargs  or {})
    lr1_kwargs = dict(lr1_kwargs or {})
    lr2_kwargs = dict(lr2_kwargs or {})

    available = {
        "C_r1": {"name": "C_r1", "data": "placeholder"},
        "L_r1": {"name": "L_r1", "category": "magnetic",
                 "mas": _lr_mas(**lr1_kwargs)},
        "T1":   {"name": "T1",   "category": "magnetic",
                 "mas": _t1_mas(**t1_kwargs)},
        "L_r2": {"name": "L_r2", "category": "magnetic",
                 "mas": _lr_mas(**lr2_kwargs)},
        "C_r2": {"name": "C_r2", "data": "placeholder"},
    }
    order = component_order or ["C_r1", "L_r1", "T1", "L_r2", "C_r2"]
    components = [available[n] for n in order]

    return {"topology": {
        "stages": [
            {
                "name": "primary_bridge",
                "role": "inverter",
                "circuit": {"components": [
                    {"name": "Q1", "data": "p"}, {"name": "Q2", "data": "p"},
                    {"name": "Q3", "data": "p"}, {"name": "Q4", "data": "p"},
                ]},
            },
            {
                "name": "isolation",
                "role": "isolation",
                "circuit": {"components": components},
            },
            {
                "name": "secondary_bridge",
                "role": "outputRectifier",
                "circuit": {"components": [
                    {"name": "Q5", "data": "p"}, {"name": "Q6", "data": "p"},
                    {"name": "Q7", "data": "p"}, {"name": "Q8", "data": "p"},
                ]},
            },
            {
                "name": "output_filter",
                "role": "outputFilter",
                "circuit": {"components": [
                    {"name": "C_out0", "data": "p"},
                ]},
            },
        ],
        "interStageCircuit": [],
    }}


def _clllc_spec() -> dict:
    """400 V → 48 V / 10 A CLLLC, fsw 100 kHz, L_r1 30 µH, Lm 200 µH.

    n = 8/2 = 4.  Vout_at_resonance = Vin/n = Vin/4 ⇒ 100 V at
    nominal 400 V.  Spec asks 48 V ⇒ super-resonant (M_nom = 4·48/400
    = 0.48).  M_at_vmin = 4·48/360 ≈ 0.533, still super-resonant ⇒
    boost factor saturates at 1.0.
    """
    return {
        "inputVoltage": {"minimum": 360.0, "maximum": 440.0, "nominal": 400.0},
        "desiredInductance": 30e-6,                    # L_r1
        "desiredMagnetizingInductance": 200e-6,        # L_m
        "efficiency": 0.96,
        "operatingPoints": [{
            "outputVoltages": [48.0],
            "outputCurrents": [10.0],
            "switchingFrequency": 100_000.0,
            "ambientTemperature": 25,
        }],
    }


def _get_named(tas: dict, role: str, name: str) -> dict:
    for stage in tas["topology"]["stages"]:
        if stage.get("role") == role:
            for c in stage["circuit"]["components"]:
                if c.get("name") == name:
                    return c
    raise AssertionError(f"{name} not found in stage role={role!r}")


# ---------------------------------------------------------------------------
# Duty cycle
# ---------------------------------------------------------------------------


class TestDuty:

    def test_duty_is_50pct_at_both_vin_extremes(self):
        out = enrich_tas_for_realism(_clllc_tas(), topology="clllc",
                                     spec=_clllc_spec())
        assert out["duty"] == 0.5
        assert out["duty_min"] == 0.5
        assert out["duty_max"] == 0.5

    def test_duty_independent_of_turns_ratio(self):
        out = enrich_tas_for_realism(
            _clllc_tas(t1_kwargs={"N_pri": 20, "N_sec0": 1}),
            topology="clllc", spec=_clllc_spec(),
        )
        assert out["duty"] == 0.5

    def test_duty_independent_of_load(self):
        spec = _clllc_spec()
        spec["operatingPoints"][0]["outputCurrents"] = [0.5]
        out = enrich_tas_for_realism(_clllc_tas(), topology="clllc", spec=spec)
        assert out["duty"] == 0.5


# ---------------------------------------------------------------------------
# Turns ratio and gain
# ---------------------------------------------------------------------------


class TestTurnsRatioAndGain:

    def test_turns_ratio_recorded(self):
        out = enrich_tas_for_realism(
            _clllc_tas(t1_kwargs={"N_pri": 10, "N_sec0": 2}),
            topology="clllc", spec=_clllc_spec(),
        )
        lr1 = _get_named(out, "isolation", "L_r1")
        assert lr1["ipeak_provenance"][
            "turns_ratio_n_pri_over_n_sec"] == pytest.approx(5.0, rel=1e-6)
        assert lr1["ipeak_provenance"]["n_primary"] == 10
        assert lr1["ipeak_provenance"]["n_secondary"] == 2

    def test_gain_at_vin_min_recorded_super_resonant(self):
        out = enrich_tas_for_realism(_clllc_tas(), topology="clllc",
                                     spec=_clllc_spec())
        lr1 = _get_named(out, "isolation", "L_r1")
        # n = 4, Vout = 48, Vin_min = 360 ⇒ M = 4·48/360 ≈ 0.5333
        expected = 4.0 * 48.0 / 360.0
        assert lr1["ipeak_provenance"]["gain_at_vin_min"] == pytest.approx(
            expected, rel=1e-4)
        # Boost factor saturates at 1.0 when super-resonant.
        assert lr1["ipeak_provenance"]["boost_factor_M_max"] == 1.0

    def test_sub_resonant_boost_factor_engages(self):
        """Force low-line into sub-resonant region (M > 1)."""
        spec = _clllc_spec()
        # Vout = 120 V ⇒ M_at_vmin = 4·120/360 ≈ 1.333.
        spec["operatingPoints"][0]["outputVoltages"] = [120.0]
        out = enrich_tas_for_realism(_clllc_tas(), topology="clllc", spec=spec)
        lr1 = _get_named(out, "isolation", "L_r1")
        expected_M = 4.0 * 120.0 / 360.0
        assert lr1["ipeak_provenance"]["boost_factor_M_max"] == pytest.approx(
            expected_M, rel=1e-4)


# ---------------------------------------------------------------------------
# Ipeak components — FB primary => magnetizing /4 (not /8 like LLC HB).
# ---------------------------------------------------------------------------


class TestIpeak:

    def test_load_reflected_component(self):
        out = enrich_tas_for_realism(_clllc_tas(), topology="clllc",
                                     spec=_clllc_spec())
        lr1 = _get_named(out, "isolation", "L_r1")
        # I_load_pk = (π/2) · Iout/n = π/2 · 10/4
        expected = (math.pi / 2.0) * (10.0 / 4.0)
        assert lr1["ipeak_provenance"]["i_load_pk_A"] == pytest.approx(
            expected, rel=1e-4)

    def test_magnetizing_component_uses_vin_max_lm_worst_and_div_4(self):
        out = enrich_tas_for_realism(_clllc_tas(), topology="clllc",
                                     spec=_clllc_spec())
        lr1 = _get_named(out, "isolation", "L_r1")
        # Im_pk = Vin_max / (4 · Lm_worst · fsw)  ← /4 because FB
        Lm_worst = 0.8 * 200e-6
        expected = 440.0 / (4.0 * Lm_worst * 100_000.0)
        assert lr1["ipeak_provenance"]["i_mag_pk_A"] == pytest.approx(
            expected, rel=1e-4)
        assert lr1["ipeak_provenance"]["Lm_worst_H"] == pytest.approx(
            Lm_worst, rel=1e-12)

    def test_ipeak_combines_components_with_boost_factor(self):
        out = enrich_tas_for_realism(_clllc_tas(), topology="clllc",
                                     spec=_clllc_spec())
        lr1 = _get_named(out, "isolation", "L_r1")
        p = lr1["ipeak_provenance"]
        expected = p["boost_factor_M_max"] * p["i_load_pk_A"] + p["i_mag_pk_A"]
        assert lr1["ipeak_worst"] == pytest.approx(expected, rel=1e-4)


# ---------------------------------------------------------------------------
# Isat — closed form on L_r1 MAS; T1 + L_r2 deliberately NOT stamped.
# ---------------------------------------------------------------------------


class TestIsat:

    def test_isat_uses_lr1_mas(self):
        out = enrich_tas_for_realism(_clllc_tas(), topology="clllc",
                                     spec=_clllc_spec())
        lr1 = _get_named(out, "isolation", "L_r1")
        # B_sat = 0.36, N = 12, A_e = 1.2e-4, L_r1 = 30e-6
        expected = 0.36 * 12 * 1.2e-4 / 30e-6
        assert lr1["isat"] == pytest.approx(expected, rel=1e-4)
        assert "clllc" in lr1["isat_provenance"]["method"]

    def test_t1_is_not_isat_stamped(self):
        out = enrich_tas_for_realism(_clllc_tas(), topology="clllc",
                                     spec=_clllc_spec())
        t1 = _get_named(out, "isolation", "T1")
        assert "isat" not in t1
        assert "ipeak_worst" not in t1

    def test_l_r2_is_not_isat_stamped(self):
        out = enrich_tas_for_realism(_clllc_tas(), topology="clllc",
                                     spec=_clllc_spec())
        lr2 = _get_named(out, "isolation", "L_r2")
        assert "isat" not in lr2
        assert "ipeak_worst" not in lr2

    def test_provenance_flags_unstamped_magnetics(self):
        out = enrich_tas_for_realism(_clllc_tas(), topology="clllc",
                                     spec=_clllc_spec())
        lr1 = _get_named(out, "isolation", "L_r1")
        p = lr1["ipeak_provenance"]
        assert p["t1_isat_modelled"] is False
        assert p["l_r2_isat_modelled"] is False
        assert p["duty_50pct_complementary_FB"] is True


# ---------------------------------------------------------------------------
# End-to-end realism evaluation
# ---------------------------------------------------------------------------


class TestRealismIntegration:

    def test_end_to_end_realism_evaluates(self):
        spec = _clllc_spec()
        enriched = enrich_tas_for_realism(_clllc_tas(), topology="clllc",
                                          spec=spec)
        r = evaluate_tas(enriched, topology="clllc", spec=spec)
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
        tas = _clllc_tas()
        tas["topology"]["stages"] = [
            s for s in tas["topology"]["stages"] if s.get("role") != "isolation"
        ]
        with pytest.raises(EnrichmentError, match="isolation"):
            enrich_tas_for_realism(tas, topology="clllc", spec=_clllc_spec())

    def test_stencil_order_violation_throws(self):
        """If L_r2 ends up before L_r1 in the isolation stage, the
        extractor's name check on the first magnetic must throw rather
        than silently stamping the wrong inductor."""
        tas = _clllc_tas(
            component_order=["C_r1", "L_r2", "L_r1", "T1", "C_r2"]
        )
        with pytest.raises(EnrichmentError, match="L_r1"):
            enrich_tas_for_realism(tas, topology="clllc", spec=_clllc_spec())

    def test_missing_pri_winding_throws(self):
        tas = _clllc_tas()
        t1 = _get_named(tas, "isolation", "T1")
        t1["mas"]["coil"]["functionalDescription"][0]["name"] = "primary"
        with pytest.raises(EnrichmentError, match="'pri'"):
            enrich_tas_for_realism(tas, topology="clllc", spec=_clllc_spec())

    def test_missing_sec0_winding_throws(self):
        tas = _clllc_tas()
        t1 = _get_named(tas, "isolation", "T1")
        t1["mas"]["coil"]["functionalDescription"][1]["name"] = "secondary0"
        with pytest.raises(EnrichmentError, match="'sec0'"):
            enrich_tas_for_realism(tas, topology="clllc", spec=_clllc_spec())

    def test_missing_desiredInductance_throws(self):
        spec = _clllc_spec()
        del spec["desiredInductance"]
        with pytest.raises(EnrichmentError, match="desiredInductance"):
            enrich_tas_for_realism(_clllc_tas(), topology="clllc", spec=spec)

    def test_missing_desiredMagnetizingInductance_throws(self):
        spec = _clllc_spec()
        del spec["desiredMagnetizingInductance"]
        with pytest.raises(EnrichmentError,
                           match="desiredMagnetizingInductance"):
            enrich_tas_for_realism(_clllc_tas(), topology="clllc", spec=spec)

    def test_missing_lr1_mas_throws(self):
        tas = _clllc_tas()
        lr1 = _get_named(tas, "isolation", "L_r1")
        del lr1["mas"]
        with pytest.raises(EnrichmentError, match="MAS"):
            enrich_tas_for_realism(tas, topology="clllc", spec=_clllc_spec())
