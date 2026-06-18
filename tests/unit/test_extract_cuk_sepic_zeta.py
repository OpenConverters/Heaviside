"""Tests for the cuk / SEPIC / zeta realism extractors.

All three share the same closed form (``D = Vout/(Vin+Vout)``,
``ΔI_L = Vin·D/(L·fsw)`` for both inductors, L1 carries input current,
L2 carries output current) so the tests parametrise over topology name.
Per CLAUDE.md "throw, never default": every missing / invalid spec
field must raise EnrichmentError.
"""

from __future__ import annotations

import pytest

from heaviside.pipeline import evaluate_tas
from heaviside.pipeline.extract import EnrichmentError, enrich_tas_for_realism
from heaviside.pipeline.realism import CheckStatus, RealismVerdict
from tests.unit._real_mas import isat_of, real_magnetic

_TOPOLOGIES = ["cuk", "sepic", "zeta"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mas(numberTurns: int = 22) -> dict:
    """A complete, PyOM-evaluable gapped inductor for cuk / SEPIC / zeta.

    Both L1 and L2 are discrete (uncoupled) gapped inductors with a
    single ``Primary`` winding. Built by :func:`real_magnetic` so the
    extractor's ``calculate_saturation_current`` call returns genuine,
    gap-aware MKF physics (the analytical ``B_sat·N·A_e/L`` fallback was
    deleted — magnetics math must come from MKF, see ~/.claude/CLAUDE.md).

    The extractor harvests ``numberTurns`` from this MAS for the Isat
    provenance, while the inductance value comes from the spec
    (``desiredInductance`` / ``desiredOutputInductance``), so the turn
    count must match the per-inductor expectations (22 for L1, 30 for L2).
    """
    return real_magnetic(
        shape="ETD 29/16/10",
        material="3C95",
        gap_mm=1.0,
        windings=[
            {"name": "Primary", "turns": numberTurns, "side": "primary"},
        ],
    )


def _spec() -> dict:
    return {
        "inputVoltage": {"minimum": 18.0, "maximum": 36.0, "nominal": 24.0},
        "desiredInductance": 47e-6,
        "desiredOutputInductance": 100e-6,
        "currentRippleRatio": 0.4,
        "efficiency": 0.92,
        "operatingPoints": [
            {
                "outputVoltages": [12.0],
                "outputCurrents": [3.0],
                "switchingFrequency": 200_000.0,
                "ambientTemperature": 25,
            }
        ],
    }


def _tas() -> dict:
    """Six-component cuk-shaped TAS (also valid for SEPIC / zeta — the
    extractor only cares about the L1, L2 declaration order)."""
    return {
        "topology": {
            "stages": [
                {
                    "name": "power_stage",
                    "role": "switchingCell",
                    "circuit": {
                        "components": [
                            {"name": "Q1", "data": "placeholder"},
                            {"name": "D1", "data": "placeholder"},
                            {"name": "L1", "category": "magnetic", "mas": _mas(numberTurns=22)},
                            {"name": "L2", "category": "magnetic", "mas": _mas(numberTurns=30)},
                            {"name": "C_flying", "data": "placeholder"},
                            {"name": "C_out", "data": "placeholder"},
                        ]
                    },
                }
            ],
            "interStageConnections": [],
        }
    }


def _get_inductors(tas: dict) -> tuple[dict, dict]:
    comps = tas["topology"]["stages"][0]["circuit"]["components"]
    return comps[2], comps[3]


# ---------------------------------------------------------------------------
# Math (parametrised over topology)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("topology", _TOPOLOGIES)
class TestCukSepicZetaMath:
    def test_duty_uses_vin_min(self, topology):
        out = enrich_tas_for_realism(_tas(), topology=topology, spec=_spec())
        # D_max = Vout/(Vin_min+Vout) = 12/30 = 0.4
        assert out["duty_max"] == pytest.approx(12.0 / 30.0, abs=1e-6)
        # D_min = Vout/(Vin_max+Vout) = 12/48 = 0.25
        assert out["duty_min"] == pytest.approx(12.0 / 48.0, abs=1e-6)
        assert out["duty"] == out["duty_max"]

    def test_L1_ripple_uses_vin_max(self, topology):
        """Ripple ∝ Vin·D = Vin·Vout/(Vin+Vout), monotone increasing
        in Vin ⇒ worst case at Vin_max=36."""
        out = enrich_tas_for_realism(_tas(), topology=topology, spec=_spec())
        l1, _ = _get_inductors(out)
        L1_worst = 0.8 * 47e-6
        d_at_vmax = 12.0 / 48.0
        expected = 36.0 * d_at_vmax / (L1_worst * 200_000.0)
        assert l1["ipeak_provenance"]["ripple_worst_A_pp"] == pytest.approx(expected, rel=1e-6)

    def test_L1_avg_current_uses_efficiency_and_vin_min(self, topology):
        """I_L1_avg = Pout / (η · Vin_min)."""
        out = enrich_tas_for_realism(_tas(), topology=topology, spec=_spec())
        l1, _ = _get_inductors(out)
        expected = (12.0 * 3.0) / (0.92 * 18.0)
        assert l1["ipeak_provenance"]["iL1_avg_max_A"] == pytest.approx(expected, rel=1e-6)

    def test_L2_avg_current_equals_iout(self, topology):
        out = enrich_tas_for_realism(_tas(), topology=topology, spec=_spec())
        _, l2 = _get_inductors(out)
        assert l2["ipeak_provenance"]["iout_A"] == pytest.approx(3.0)
        # L2 peak = Iout + ripple/2
        L2_worst = 0.8 * 100e-6
        d_at_vmax = 12.0 / 48.0
        ripple = 36.0 * d_at_vmax / (L2_worst * 200_000.0)
        assert l2["ipeak_worst"] == pytest.approx(3.0 + ripple / 2.0, rel=1e-5)

    def test_both_inductors_have_independent_isat(self, topology):
        out = enrich_tas_for_realism(_tas(), topology=topology, spec=_spec())
        l1, l2 = _get_inductors(out)
        # Ground truth = MKF: each stamped Isat must equal PyOM's
        # saturation current for that inductor's own MAS at the op-point
        # ambient (25 °C), NOT an analytical formula. Each inductor has its
        # own core/winding (uncoupled), so each gets its own PyOM Isat.
        # L1: 22 turns; L2: 30 turns (turn count harvested from the MAS).
        assert l1["isat"] == pytest.approx(
            isat_of(_mas(numberTurns=22), temperature_c=25.0), rel=1e-3
        )
        assert l2["isat"] == pytest.approx(
            isat_of(_mas(numberTurns=30), temperature_c=25.0), rel=1e-3
        )
        assert "PyOM" in l1["isat_provenance"]["method"]
        assert "PyOM" in l2["isat_provenance"]["method"]
        # And the provenance must trace each input.
        assert l1["isat_provenance"]["n_turns"] == 22
        assert l2["isat_provenance"]["n_turns"] == 30
        assert l1["isat_provenance"]["inductance_H"] == 47e-6
        assert l2["isat_provenance"]["inductance_H"] == 100e-6

    def test_end_to_end_realism_passes(self, topology):
        enriched = enrich_tas_for_realism(_tas(), topology=topology, spec=_spec())
        r = evaluate_tas(enriched, topology=topology, spec=_spec())
        assert r.verdict is RealismVerdict.PASS
        passed = {c.name for c in r.checks if c.status is CheckStatus.PASS}
        assert {"duty_cycle_bounds", "inductor_isat_margin"}.issubset(passed)


# ---------------------------------------------------------------------------
# L2 inductance source
# ---------------------------------------------------------------------------


class TestL2InductanceSource:
    def test_omitted_L2_falls_back_to_L1_with_provenance(self):
        spec = _spec()
        del spec["desiredOutputInductance"]
        out = enrich_tas_for_realism(_tas(), topology="cuk", spec=spec)
        _, l2 = _get_inductors(out)
        # L2 inductance == L1 (47 µH)
        assert l2["isat_provenance"]["inductance_H"] == 47e-6
        assert "defaulted_to_L1" in l2["isat_provenance"]["inductance_source"]

    def test_explicit_L2_source_recorded(self):
        out = enrich_tas_for_realism(_tas(), topology="cuk", spec=_spec())
        _, l2 = _get_inductors(out)
        assert l2["isat_provenance"]["inductance_source"] == "spec.desiredOutputInductance"


# ---------------------------------------------------------------------------
# Failure modes — fail-closed per CLAUDE.md
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("topology", _TOPOLOGIES)
class TestCukSepicZetaFailureModes:
    def test_missing_efficiency_throws(self, topology):
        spec = _spec()
        del spec["efficiency"]
        with pytest.raises(EnrichmentError, match="efficiency"):
            enrich_tas_for_realism(_tas(), topology=topology, spec=spec)

    def test_missing_L1_inductance_throws(self, topology):
        spec = _spec()
        del spec["desiredInductance"]
        with pytest.raises(EnrichmentError, match="desiredInductance"):
            enrich_tas_for_realism(_tas(), topology=topology, spec=spec)

    def test_negative_L2_inductance_throws(self, topology):
        spec = _spec()
        spec["desiredOutputInductance"] = -1e-6
        with pytest.raises(EnrichmentError, match="desiredOutputInductance"):
            enrich_tas_for_realism(_tas(), topology=topology, spec=spec)

    def test_only_one_magnetic_throws(self, topology):
        tas = _tas()
        # Strip L2
        tas["topology"]["stages"][0]["circuit"]["components"] = [
            c
            for c in tas["topology"]["stages"][0]["circuit"]["components"]
            if c.get("name") != "L2"
        ]
        with pytest.raises(EnrichmentError, match="expected 2 magnetic"):
            enrich_tas_for_realism(tas, topology=topology, spec=_spec())

    def test_missing_vin_range_throws(self, topology):
        spec = _spec()
        del spec["inputVoltage"]["minimum"]
        with pytest.raises(EnrichmentError, match="min"):
            enrich_tas_for_realism(_tas(), topology=topology, spec=spec)
