"""Tests for the four-switch buck-boost (4SBB) realism extractor.

4SBB shares a single inductor L1 (in the ``switchingCell`` stage)
between a buck half-bridge (Q1/Q2) and a boost half-bridge (Q3/Q4).
The controller selects mode by Vin/Vout regime:

  * buck  (Vin_min > Vout)
  * boost (Vin_max < Vout)
  * mixed (Vin_min < Vout < Vin_max) — pessimistic combination

Per CLAUDE.md "throw, never default": every missing or invalid spec /
MAS field must raise EnrichmentError — no silent fallbacks.
"""

from __future__ import annotations

import pytest

from heaviside.pipeline import evaluate_tas
from heaviside.pipeline.extract import EnrichmentError, enrich_tas_for_realism
from heaviside.pipeline.realism import CheckStatus, RealismVerdict
from tests.unit._real_mas import isat_of, real_magnetic

# ---------------------------------------------------------------------------
# Fixtures (4SBB stencil: L1 inside the single switchingCell stage —
# stencils.py:794)
# ---------------------------------------------------------------------------


def _l1_mas(N: int = 16) -> dict:
    """Complete, PyOM-evaluable gapped inductor for L1 (built by
    :func:`real_magnetic` so ``calculate_saturation_current`` returns real
    MKF physics).
    """
    return real_magnetic(
        shape="ETD 29/16/10",
        material="3C95",
        gap_mm=1.0,
        windings=[
            {"name": "Primary", "turns": N, "side": "primary"},
        ],
    )


def _fsbb_tas() -> dict:
    return {
        "topology": {
            "stages": [
                {
                    "name": "power_stage",
                    "role": "switchingCell",
                    "circuit": {
                        "components": [
                            {"name": "Q1", "data": "placeholder"},
                            {"name": "Q2", "data": "placeholder"},
                            {"name": "Q3", "data": "placeholder"},
                            {"name": "Q4", "data": "placeholder"},
                            {"name": "L1", "category": "magnetic", "mas": _l1_mas()},
                            {"name": "C_in", "data": "placeholder"},
                            {"name": "C_out", "data": "placeholder"},
                        ]
                    },
                },
            ],
            "interStageConnections": [],
        }
    }


def _spec(
    *,
    vmin: float,
    vmax: float,
    vout: float,
    iout: float = 5.0,
    fsw: float = 200_000.0,
    L: float = 10e-6,
) -> dict:
    return {
        "inputVoltage": {"minimum": vmin, "maximum": vmax, "nominal": (vmin + vmax) / 2.0},
        "desiredInductance": L,
        "efficiency": 0.95,
        "operatingPoints": [
            {
                "outputVoltages": [vout],
                "outputCurrents": [iout],
                "switchingFrequency": fsw,
                "ambientTemperature": 25,
            }
        ],
    }


def _get_l1(tas: dict) -> dict:
    for stage in tas["topology"]["stages"]:
        if stage.get("role") == "switchingCell":
            for c in stage["circuit"]["components"]:
                if c.get("name") == "L1":
                    return c
    raise AssertionError("L1 not found")


# ---------------------------------------------------------------------------
# Buck-only mode (Vin_min > Vout)
# ---------------------------------------------------------------------------


class TestBuckMode:
    def test_classified_as_buck(self):
        out = enrich_tas_for_realism(
            _fsbb_tas(),
            topology="four_switch_buck_boost",
            spec=_spec(vmin=24.0, vmax=60.0, vout=12.0),
        )
        l = _get_l1(out)
        assert l["ipeak_provenance"]["mode"] == "buck"

    def test_buck_math_matches_plain_buck(self):
        """Same D = Vout/Vin and ripple = Vout(1-D_min)/(L*fsw)."""
        out = enrich_tas_for_realism(
            _fsbb_tas(),
            topology="four_switch_buck_boost",
            spec=_spec(vmin=24.0, vmax=60.0, vout=12.0, L=10e-6),
        )
        l = _get_l1(out)
        # D_max = 12/24 = 0.5; D_min = 12/60 = 0.2
        assert out["duty_max"] == pytest.approx(0.5, rel=1e-5)
        assert out["duty_min"] == pytest.approx(0.2, rel=1e-5)
        L_worst = 0.8 * 10e-6
        expected_ripple = 12.0 * (1.0 - 0.2) / (L_worst * 200_000.0)
        assert l["ipeak_provenance"]["ripple_worst_A_pp"] == pytest.approx(
            expected_ripple, rel=1e-6
        )
        # In buck mode iL_avg = Iout (no Vout/Vin scaling).
        assert l["ipeak_provenance"]["iL_avg_max_A"] == pytest.approx(5.0)
        assert l["ipeak_worst"] == pytest.approx(5.0 + expected_ripple / 2.0, rel=1e-6)


# ---------------------------------------------------------------------------
# Boost-only mode (Vin_max < Vout)
# ---------------------------------------------------------------------------


class TestBoostMode:
    def test_classified_as_boost(self):
        out = enrich_tas_for_realism(
            _fsbb_tas(),
            topology="four_switch_buck_boost",
            spec=_spec(vmin=12.0, vmax=24.0, vout=48.0),
        )
        l = _get_l1(out)
        assert l["ipeak_provenance"]["mode"] == "boost"

    def test_boost_math_matches_plain_boost(self):
        out = enrich_tas_for_realism(
            _fsbb_tas(),
            topology="four_switch_buck_boost",
            spec=_spec(vmin=12.0, vmax=24.0, vout=48.0, L=10e-6),
        )
        l = _get_l1(out)
        # D_max at Vin_min: 1 - 12/48 = 0.75
        assert out["duty_max"] == pytest.approx(0.75, rel=1e-5)
        # D_min at Vin_max: 1 - 24/48 = 0.5
        assert out["duty_min"] == pytest.approx(0.5, rel=1e-5)
        # vout/2 = 24 sits at Vin_max boundary (not strictly inside) — so
        # ripple peak is at Vin_max where parabola hits its open-interval
        # boundary maximum.
        L_worst = 0.8 * 10e-6

        def ripple_at(v):
            return v * (1.0 - v / 48.0) / (L_worst * 200_000.0)

        expected = max(ripple_at(12.0), ripple_at(24.0))
        assert l["ipeak_provenance"]["ripple_worst_A_pp"] == pytest.approx(expected, rel=1e-6)
        # iL_avg_max = Iout * Vout / Vin_min = 5 * 48 / 12 = 20
        assert l["ipeak_provenance"]["iL_avg_max_A"] == pytest.approx(20.0)

    def test_boost_ripple_uses_interior_peak_when_in_range(self):
        """Vin_min=8, Vin_max=40, Vout=48 ⇒ vout/2=24 lies inside (8,40)
        and gives the highest ripple."""
        out = enrich_tas_for_realism(
            _fsbb_tas(),
            topology="four_switch_buck_boost",
            spec=_spec(vmin=8.0, vmax=40.0, vout=48.0, L=10e-6),
        )
        l = _get_l1(out)
        L_worst = 0.8 * 10e-6

        def ripple_at(v):
            return v * (1.0 - v / 48.0) / (L_worst * 200_000.0)

        candidates = [ripple_at(8.0), ripple_at(40.0), ripple_at(24.0)]
        # Interior peak must win.
        assert max(candidates) == ripple_at(24.0)
        assert l["ipeak_provenance"]["ripple_worst_A_pp"] == pytest.approx(
            ripple_at(24.0), rel=1e-6
        )


# ---------------------------------------------------------------------------
# Mixed mode (Vin_min < Vout < Vin_max)
# ---------------------------------------------------------------------------


class TestMixedMode:
    def test_classified_as_mixed(self):
        out = enrich_tas_for_realism(
            _fsbb_tas(),
            topology="four_switch_buck_boost",
            spec=_spec(vmin=18.0, vmax=36.0, vout=24.0),
        )
        l = _get_l1(out)
        assert l["ipeak_provenance"]["mode"] == "mixed"

    def test_mixed_picks_worst_of_buck_and_boost(self):
        out = enrich_tas_for_realism(
            _fsbb_tas(),
            topology="four_switch_buck_boost",
            spec=_spec(vmin=18.0, vmax=36.0, vout=24.0, L=10e-6),
        )
        l = _get_l1(out)
        L_worst = 0.8 * 10e-6
        # Buck sub-region ripple at vmax: 24·(1 − 24/36) / (L*0.8·fsw)
        ripple_buck = 24.0 * (1.0 - 24.0 / 36.0) / (L_worst * 200_000.0)
        # Boost sub-region: candidates = [18, 12 (vout/2)?]. vout/2 = 12,
        # need 18 < 12 < 24 → false (12 < 18). So only candidate is 18.
        ripple_boost = 18.0 * (1.0 - 18.0 / 24.0) / (L_worst * 200_000.0)
        expected = max(ripple_buck, ripple_boost)
        assert l["ipeak_provenance"]["ripple_worst_A_pp"] == pytest.approx(expected, rel=1e-6)

    def test_mixed_iL_avg_uses_boost_side_worst(self):
        """Boost-side avg I_L = Iout*Vout/Vin_min always exceeds
        buck-side I_L = Iout, so the mixed combination must report the
        boost figure."""
        out = enrich_tas_for_realism(
            _fsbb_tas(),
            topology="four_switch_buck_boost",
            spec=_spec(vmin=18.0, vmax=36.0, vout=24.0, iout=5.0),
        )
        l = _get_l1(out)
        # 5 * 24 / 18 = 6.6667
        assert l["ipeak_provenance"]["iL_avg_max_A"] == pytest.approx(5.0 * 24.0 / 18.0, rel=1e-6)

    def test_mixed_end_to_end_realism_passes(self):
        spec = _spec(vmin=18.0, vmax=36.0, vout=24.0, L=22e-6)
        enriched = enrich_tas_for_realism(
            _fsbb_tas(),
            topology="four_switch_buck_boost",
            spec=spec,
        )
        r = evaluate_tas(enriched, topology="four_switch_buck_boost", spec=spec)
        assert r.verdict is RealismVerdict.PASS
        passed = {c.name for c in r.checks if c.status is CheckStatus.PASS}
        assert {"duty_cycle_bounds", "inductor_isat_margin"}.issubset(passed)


# ---------------------------------------------------------------------------
# Isat math (closed form independent of mode)
# ---------------------------------------------------------------------------


class TestIsat:
    def test_isat_uses_l1_mas(self):
        """Ground truth = MKF: the stamped Isat must equal PyOM's
        saturation current for the L1 magnetic at the hot operating corner
        (25 °C), NOT an analytical formula.
        """
        out = enrich_tas_for_realism(
            _fsbb_tas(),
            topology="four_switch_buck_boost",
            spec=_spec(vmin=24.0, vmax=60.0, vout=12.0, L=10e-6),
        )
        l = _get_l1(out)
        expected = isat_of(_l1_mas(), temperature_c=100.0)
        assert l["isat"] == pytest.approx(expected, rel=1e-3)
        assert "PyOM" in l["isat_provenance"]["method"]
        b_sat_T = l["isat_provenance"]["b_sat_T"]
        assert 0.2 < b_sat_T < 0.6
        assert "four_switch_buck_boost" in l["isat_provenance"]["method"]


# ---------------------------------------------------------------------------
# Degenerate boundary throws
# ---------------------------------------------------------------------------


class TestDegenerateBoundary:
    def test_vin_min_equal_to_vout_throws(self):
        with pytest.raises(EnrichmentError, match="degenerate"):
            enrich_tas_for_realism(
                _fsbb_tas(),
                topology="four_switch_buck_boost",
                spec=_spec(vmin=12.0, vmax=24.0, vout=12.0),
            )

    def test_vin_max_equal_to_vout_throws(self):
        with pytest.raises(EnrichmentError, match="degenerate"):
            enrich_tas_for_realism(
                _fsbb_tas(),
                topology="four_switch_buck_boost",
                spec=_spec(vmin=6.0, vmax=12.0, vout=12.0),
            )


# ---------------------------------------------------------------------------
# Structural failures
# ---------------------------------------------------------------------------


class TestStructuralFailures:
    def test_missing_desiredInductance_throws(self):
        spec = _spec(vmin=24.0, vmax=60.0, vout=12.0)
        del spec["desiredInductance"]
        with pytest.raises(EnrichmentError, match="desiredInductance"):
            enrich_tas_for_realism(
                _fsbb_tas(),
                topology="four_switch_buck_boost",
                spec=spec,
            )

    def test_missing_l1_mas_throws(self):
        tas = _fsbb_tas()
        for stage in tas["topology"]["stages"]:
            for c in stage["circuit"]["components"]:
                if c.get("name") == "L1":
                    del c["mas"]
        with pytest.raises(EnrichmentError):
            enrich_tas_for_realism(
                tas,
                topology="four_switch_buck_boost",
                spec=_spec(vmin=24.0, vmax=60.0, vout=12.0),
            )

    def test_missing_magnetic_throws(self):
        tas = _fsbb_tas()
        for stage in tas["topology"]["stages"]:
            stage["circuit"]["components"] = [
                c for c in stage["circuit"]["components"] if c.get("name") != "L1"
            ]
        with pytest.raises(EnrichmentError):
            enrich_tas_for_realism(
                tas,
                topology="four_switch_buck_boost",
                spec=_spec(vmin=24.0, vmax=60.0, vout=12.0),
            )
