"""Tests for the boost and flyback realism extractors.

Mirrors the structure of ``test_extract.py`` (buck): closed-form math
pinning, failure-mode coverage per CLAUDE.md "throw, never default",
end-to-end realism-gate flip from INCOMPLETE → PASS.
"""

from __future__ import annotations

import pytest

from heaviside.pipeline import evaluate_tas
from heaviside.pipeline.extract import EnrichmentError, enrich_tas_for_realism
from heaviside.pipeline.realism import CheckStatus, RealismVerdict


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _basic_mas(*, windings: list[dict] | None = None) -> dict:
    """Minimal MAS shape sufficient for any single-magnetic extractor."""
    if windings is None:
        windings = [
            {"name": "Primary", "numberTurns": 20, "numberParallels": 1,
             "isolationSide": "primary"},
        ]
    return {
        "core": {
            "processedDescription": {
                "effectiveParameters": {
                    "effectiveArea": 8.0327e-5,
                    "effectiveLength": 0.0909,
                    "effectiveVolume": 7.3e-6,
                },
            },
            "functionalDescription": {
                "material": {
                    "saturation": [
                        {"magneticField": 393.0, "magneticFluxDensity": 0.4,
                         "temperature": 100.0},
                        {"magneticField": 392.0, "magneticFluxDensity": 0.473,
                         "temperature": 25.0},
                    ],
                },
            },
        },
        "coil": {"functionalDescription": windings},
    }


def _single_magnetic_tas(name: str, mas: dict) -> dict:
    return {"topology": {
        "stages": [{
            "name": "power_stage",
            "role": "switchingCell",
            "circuit": {"components": [
                {"name": "Q1", "data": "placeholder"},
                {"name": "D1", "data": "placeholder"},
                {"name": name, "category": "magnetic", "mas": mas},
                {"name": "C_out", "data": "placeholder"},
            ]},
        }],
        "interStageCircuit": [],
    }}


# ---------------------------------------------------------------------------
# Boost
# ---------------------------------------------------------------------------


def _boost_spec() -> dict:
    return {
        "inputVoltage": {"minimum": 18.0, "maximum": 36.0, "nominal": 24.0},
        "desiredInductance": 47e-6,
        "currentRippleRatio": 0.4,
        "efficiency": 0.95,
        "operatingPoints": [{
            "outputVoltages": [48.0],
            "outputCurrents": [2.0],
            "switchingFrequency": 250_000.0,
            "ambientTemperature": 25,
        }],
    }


def _boost_tas() -> dict:
    return _single_magnetic_tas("L1", _basic_mas())


class TestBoostMath:
    def test_duty_at_both_extremes(self):
        out = enrich_tas_for_realism(_boost_tas(), topology="boost", spec=_boost_spec())
        # D_max at Vin_min: 1 - 18/48 = 0.625
        assert out["duty_max"] == pytest.approx(1.0 - 18.0 / 48.0, abs=1e-6)
        # D_min at Vin_max: 1 - 36/48 = 0.25
        assert out["duty_min"] == pytest.approx(1.0 - 36.0 / 48.0, abs=1e-6)
        assert out["duty"] == out["duty_max"]

    def test_ripple_peaks_at_vout_over_2_when_interior(self):
        """For boost: ΔI_L(Vin) = (Vin - Vin²/Vout)/(L·fsw) is maximum at
        Vin = Vout/2. Here Vout/2 = 24 V which lies inside [18, 36], so
        the worst-case ripple must use Vin=24, NOT a boundary value.
        """
        out = enrich_tas_for_realism(_boost_tas(), topology="boost", spec=_boost_spec())
        l1 = out["topology"]["stages"][0]["circuit"]["components"][2]
        L_worst = 0.8 * 47e-6
        fsw = 250_000.0
        # ΔI_L at Vin=24, D=0.5: 24·0.5 / (L_worst·fsw)
        expected_ripple = 24.0 * 0.5 / (L_worst * fsw)
        assert l1["ipeak_provenance"]["ripple_worst_A_pp"] == pytest.approx(
            expected_ripple, rel=1e-6
        )

    def test_iL_avg_uses_vin_min(self):
        """I_L_avg = Iout · Vout / Vin; worst-case at Vin_min."""
        out = enrich_tas_for_realism(_boost_tas(), topology="boost", spec=_boost_spec())
        l1 = out["topology"]["stages"][0]["circuit"]["components"][2]
        # I_L_avg_max = 2.0 · 48 / 18 = 5.333…
        assert l1["ipeak_provenance"]["iL_avg_max_A"] == pytest.approx(
            2.0 * 48.0 / 18.0, rel=1e-6
        )

    def test_isat_closed_form(self):
        out = enrich_tas_for_realism(_boost_tas(), topology="boost", spec=_boost_spec())
        l1 = out["topology"]["stages"][0]["circuit"]["components"][2]
        # Isat = 0.4 · 20 · 8.0327e-5 / 47e-6
        expected = 0.4 * 20 * 8.0327e-5 / 47e-6
        assert l1["isat"] == pytest.approx(expected, rel=1e-4)

    def test_end_to_end_realism_passes(self):
        enriched = enrich_tas_for_realism(_boost_tas(), topology="boost", spec=_boost_spec())
        r = evaluate_tas(enriched, topology="boost", spec=_boost_spec())
        assert r.verdict is RealismVerdict.PASS
        passed = {c.name for c in r.checks if c.status is CheckStatus.PASS}
        assert {"duty_cycle_bounds", "inductor_isat_margin"}.issubset(passed)

    def test_provenance_recomputes_isat(self):
        out = enrich_tas_for_realism(_boost_tas(), topology="boost", spec=_boost_spec())
        l1 = out["topology"]["stages"][0]["circuit"]["components"][2]
        p = l1["isat_provenance"]
        recomputed = p["b_sat_T"] * p["n_turns"] * p["effective_area_m2"] / p["inductance_H"]
        assert recomputed == pytest.approx(l1["isat"], rel=1e-4)


class TestBoostFailureModes:
    def test_step_down_design_throws(self):
        spec = _boost_spec()
        spec["operatingPoints"][0]["outputVoltages"] = [12.0]  # < Vin_max
        with pytest.raises(EnrichmentError, match="step down"):
            enrich_tas_for_realism(_boost_tas(), topology="boost", spec=spec)

    def test_missing_inductance_throws(self):
        spec = _boost_spec()
        del spec["desiredInductance"]
        with pytest.raises(EnrichmentError, match="desiredInductance"):
            enrich_tas_for_realism(_boost_tas(), topology="boost", spec=spec)

    def test_missing_vin_range_throws(self):
        spec = _boost_spec()
        del spec["inputVoltage"]["minimum"]
        with pytest.raises(EnrichmentError, match="min"):
            enrich_tas_for_realism(_boost_tas(), topology="boost", spec=spec)


# ---------------------------------------------------------------------------
# Flyback
# ---------------------------------------------------------------------------


def _flyback_spec() -> dict:
    return {
        "inputVoltage": {"minimum": 85.0, "maximum": 265.0, "nominal": 230.0},
        "desiredMagnetizingInductance": 1e-3,
        "efficiency": 0.85,
        "operatingPoints": [{
            "outputVoltages": [12.0],
            "outputCurrents": [2.0],
            "switchingFrequency": 100_000.0,
            "ambientTemperature": 25,
        }],
    }


def _flyback_mas() -> dict:
    return _basic_mas(windings=[
        {"name": "Primary",   "numberTurns": 60, "numberParallels": 1,
         "isolationSide": "primary"},
        {"name": "Secondary", "numberTurns": 6,  "numberParallels": 1,
         "isolationSide": "secondary"},
    ])


def _flyback_tas() -> dict:
    return _single_magnetic_tas("T1", _flyback_mas())


class TestFlybackMath:
    def test_turns_ratio_from_mas(self):
        out = enrich_tas_for_realism(_flyback_tas(), topology="flyback", spec=_flyback_spec())
        t1 = out["topology"]["stages"][0]["circuit"]["components"][2]
        # n = 60/6 = 10
        assert t1["ipeak_provenance"]["turns_ratio_n"] == pytest.approx(10.0)

    def test_duty_at_vin_min(self):
        """D_max = Vout·n / (Vin_min + Vout·n) = 120 / (85 + 120) = 0.5854"""
        out = enrich_tas_for_realism(_flyback_tas(), topology="flyback", spec=_flyback_spec())
        n = 10.0
        d_max_expected = (12.0 * n) / (85.0 + 12.0 * n)
        assert out["duty_max"] == pytest.approx(d_max_expected, rel=1e-5)
        assert out["duty"] == out["duty_max"]

    def test_ipeak_uses_efficiency(self):
        out = enrich_tas_for_realism(_flyback_tas(), topology="flyback", spec=_flyback_spec())
        t1 = out["topology"]["stages"][0]["circuit"]["components"][2]
        # I_in_max = Pout/(η·Vin_min) = 24/(0.85·85)
        i_in_expected = (12.0 * 2.0) / (0.85 * 85.0)
        assert t1["ipeak_provenance"]["i_in_max_A"] == pytest.approx(i_in_expected, rel=1e-5)

    def test_isat_uses_primary_turns_and_lm(self):
        out = enrich_tas_for_realism(_flyback_tas(), topology="flyback", spec=_flyback_spec())
        t1 = out["topology"]["stages"][0]["circuit"]["components"][2]
        # Isat = 0.4 · 60 · 8.0327e-5 / 1e-3
        expected = 0.4 * 60 * 8.0327e-5 / 1e-3
        assert t1["isat"] == pytest.approx(expected, rel=1e-4)

    def test_end_to_end_realism_passes(self):
        enriched = enrich_tas_for_realism(_flyback_tas(), topology="flyback", spec=_flyback_spec())
        r = evaluate_tas(enriched, topology="flyback", spec=_flyback_spec())
        assert r.verdict is RealismVerdict.PASS
        passed = {c.name for c in r.checks if c.status is CheckStatus.PASS}
        assert {"duty_cycle_bounds", "inductor_isat_margin"}.issubset(passed)


class TestFlybackFailureModes:
    def test_missing_magnetizing_inductance_throws(self):
        spec = _flyback_spec()
        del spec["desiredMagnetizingInductance"]
        with pytest.raises(EnrichmentError, match="desiredMagnetizingInductance"):
            enrich_tas_for_realism(_flyback_tas(), topology="flyback", spec=spec)

    def test_missing_efficiency_throws(self):
        spec = _flyback_spec()
        del spec["efficiency"]
        with pytest.raises(EnrichmentError, match="efficiency"):
            enrich_tas_for_realism(_flyback_tas(), topology="flyback", spec=spec)

    @pytest.mark.parametrize("bad_eff", [0.0, -0.1, 1.5, "high"])
    def test_invalid_efficiency_throws(self, bad_eff):
        spec = _flyback_spec()
        spec["efficiency"] = bad_eff
        with pytest.raises(EnrichmentError, match="efficiency"):
            enrich_tas_for_realism(_flyback_tas(), topology="flyback", spec=spec)

    def test_multi_output_enriches_on_total_power(self):
        """Multi-output flyback now enriches: the regulated rail (0) sets the
        duty, and the primary current / saturation are referred from the TOTAL
        throughput power summed across rails (per-secondary diode/cap stresses
        are attributed downstream by the analyst)."""
        single = _flyback_spec()
        single["operatingPoints"][0]["outputVoltages"] = [12.0]
        single["operatingPoints"][0]["outputCurrents"] = [2.0]
        multi = _flyback_spec()
        multi["operatingPoints"][0]["outputVoltages"] = [12.0, 5.0]
        multi["operatingPoints"][0]["outputCurrents"] = [2.0, 1.0]

        tas_s = enrich_tas_for_realism(_flyback_tas(), topology="flyback", spec=single)
        tas_m = enrich_tas_for_realism(_flyback_tas(), topology="flyback", spec=multi)

        def _ipeak(tas):
            for st in tas["topology"]["stages"]:
                for c in st["circuit"]["components"]:
                    if "ipeak_worst" in c:
                        return c["ipeak_worst"]
            raise AssertionError("no ipeak_worst stamped")

        # Adding a 5 W rail to a 24 W rail raises the primary peak current.
        assert _ipeak(tas_m) > _ipeak(tas_s)

    def test_single_winding_transformer_throws(self):
        tas = _flyback_tas()
        # Strip secondary
        tas["topology"]["stages"][0]["circuit"]["components"][2]["mas"][
            "coil"]["functionalDescription"] = [
                {"name": "Primary", "numberTurns": 60, "numberParallels": 1,
                 "isolationSide": "primary"},
        ]
        with pytest.raises(EnrichmentError, match="primary \\+ secondary"):
            enrich_tas_for_realism(tas, topology="flyback", spec=_flyback_spec())

    def test_zero_secondary_turns_throws(self):
        tas = _flyback_tas()
        tas["topology"]["stages"][0]["circuit"]["components"][2]["mas"][
            "coil"]["functionalDescription"][1]["numberTurns"] = 0
        with pytest.raises(EnrichmentError, match="secondary numberTurns"):
            enrich_tas_for_realism(tas, topology="flyback", spec=_flyback_spec())
