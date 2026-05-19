"""Tests for the isolated_buck (flybuck) realism extractor.

Flybuck is structurally a synchronous buck on the primary winding of
a coupled inductor T1; the secondary winding rectifies into a
magnetically-isolated open-loop output that follows by turns ratio.
The controller regulates the primary rail (``Vout_pri``), which is
what this extractor solves the duty cycle around.

Key contrast with the forward family extractor:

  * Forward family: T1 is intentionally NOT Isat-stamped (the reset
    mechanism drives the core back to B~0 every cycle); the binding
    magnetic is L_out0 in the outputRectifier stage.
  * Flybuck: T1 IS the binding magnetic — its primary winding *is*
    the buck inductor, so we stamp Isat / Ipeak on T1 directly.

v0.1 scope: reflected secondary load is not modelled; the
provenance flag ``secondary_reflected_current_modelled: false``
makes that explicit.  This test file pins that flag so a future
extension cannot silently change it without updating the test.

Per CLAUDE.md "throw, never default": every missing or invalid spec /
MAS field must raise EnrichmentError.
"""

from __future__ import annotations

import pytest

from heaviside.pipeline import evaluate_tas
from heaviside.pipeline.extract import EnrichmentError, enrich_tas_for_realism
from heaviside.pipeline.realism import CheckStatus, RealismVerdict


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _t1_mas(*, N_pri: int = 12, N_sec: int = 6) -> dict:
    """Flybuck T1 MAS: 2 windings (pri, sec0), one core."""
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
        "coil": {"functionalDescription": [
            {"name": "pri",  "numberTurns": N_pri, "numberParallels": 1,
             "isolationSide": "primary"},
            {"name": "sec0", "numberTurns": N_sec, "numberParallels": 1,
             "isolationSide": "secondary"},
        ]},
    }


def _flybuck_tas(*, t1_kwargs: dict | None = None) -> dict:
    """Flybuck TAS shape mirroring the stencil at stencils.py:1469.

    Stages: switchingCell (Q1 HS + Q2 LS sync) + isolation (T1) +
    outputFilter (C_pri at Vout_pri) + outputRectifier (D_out0,
    C_out0 at Vout0) + controller.
    """
    t1_kwargs = dict(t1_kwargs or {})
    return {"topology": {
        "stages": [
            {
                "name": "primary_switch",
                "role": "switchingCell",
                "circuit": {"components": [
                    {"name": "Q1", "data": "placeholder"},
                    {"name": "Q2", "data": "placeholder"},
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
                "name": "output_pri",
                "role": "outputFilter",
                "circuit": {"components": [
                    {"name": "C_pri", "data": "placeholder"},
                ]},
            },
            {
                "name": "output_0",
                "role": "outputRectifier",
                "circuit": {"components": [
                    {"name": "D_out0", "data": "placeholder"},
                    {"name": "C_out0", "data": "placeholder"},
                ]},
            },
        ],
        "interStageCircuit": [],
    }}


def _flybuck_spec() -> dict:
    """Vin 18-36 V, Vout_pri 5 V, Iout_pri 4 A, fsw 400 kHz, L_pri 22 µH.

    With these numbers:
      D_max = 5 / 18 = 0.2778
      D_min = 5 / 36 = 0.1389
      L_worst = 0.8 * 22e-6 = 17.6e-6
      ripple_worst = 5 * (1 - 0.1389) / (17.6e-6 * 400e3) = 0.6116 A
      Ipeak_worst = 4 + 0.6116/2 = 4.306 A
      Isat = 0.4 * 12 * 8.0327e-5 / 22e-6 = 17.527 A
      Margin = 17.527 / (1.2 * 4.306) = 3.39 — passes
    """
    return {
        "inputVoltage": {"minimum": 18.0, "maximum": 36.0, "nominal": 24.0},
        "desiredInductance": 22e-6,
        "efficiency": 0.92,
        "operatingPoints": [{
            "outputVoltages": [5.0],
            "outputCurrents": [4.0],
            "switchingFrequency": 400_000.0,
            "ambientTemperature": 25,
        }],
    }


def _get_t1(tas: dict) -> dict:
    for stage in tas["topology"]["stages"]:
        if stage.get("role") == "isolation":
            for c in stage["circuit"]["components"]:
                if c.get("name") == "T1":
                    return c
    raise AssertionError("T1 not found in isolation stage")


# ---------------------------------------------------------------------------
# Shared math
# ---------------------------------------------------------------------------


class TestFlybuckMath:

    def test_duty_is_buck_shaped(self):
        out = enrich_tas_for_realism(_flybuck_tas(), topology="isolated_buck",
                                     spec=_flybuck_spec())
        # D_max = Vout / Vin_min = 5/18
        assert out["duty_max"] == pytest.approx(5.0 / 18.0, rel=1e-5)
        # D_min = Vout / Vin_max = 5/36
        assert out["duty_min"] == pytest.approx(5.0 / 36.0, rel=1e-5)
        assert out["duty"] == out["duty_max"]

    def test_duty_independent_of_turns_ratio(self):
        """Primary loop is a buck — duty is set by Vout_pri/Vin and
        does NOT depend on the secondary winding's turn count."""
        a = enrich_tas_for_realism(
            _flybuck_tas(t1_kwargs={"N_pri": 12, "N_sec": 6}),
            topology="isolated_buck", spec=_flybuck_spec(),
        )
        b = enrich_tas_for_realism(
            _flybuck_tas(t1_kwargs={"N_pri": 12, "N_sec": 24}),
            topology="isolated_buck", spec=_flybuck_spec(),
        )
        assert a["duty"] == b["duty"]
        assert a["duty_min"] == b["duty_min"]

    def test_ripple_uses_d_min(self):
        out = enrich_tas_for_realism(_flybuck_tas(), topology="isolated_buck",
                                     spec=_flybuck_spec())
        t1 = _get_t1(out)
        L_worst = 0.8 * 22e-6
        d_min = 5.0 / 36.0
        expected = 5.0 * (1.0 - d_min) / (L_worst * 400_000.0)
        assert t1["ipeak_provenance"]["ripple_worst_A_pp"] == pytest.approx(expected, rel=1e-6)

    def test_ipeak_is_iout_plus_half_ripple(self):
        out = enrich_tas_for_realism(_flybuck_tas(), topology="isolated_buck",
                                     spec=_flybuck_spec())
        t1 = _get_t1(out)
        ripple = t1["ipeak_provenance"]["ripple_worst_A_pp"]
        assert t1["ipeak_worst"] == pytest.approx(4.0 + ripple / 2.0, rel=1e-6)

    def test_isat_uses_primary_winding_turns(self):
        out = enrich_tas_for_realism(_flybuck_tas(), topology="isolated_buck",
                                     spec=_flybuck_spec())
        t1 = _get_t1(out)
        # Isat = B_sat * N_pri * A_e / L_pri = 0.4 * 12 * 8.0327e-5 / 22e-6
        expected = 0.4 * 12 * 8.0327e-5 / 22e-6
        assert t1["isat"] == pytest.approx(expected, rel=1e-4)
        assert t1["isat_provenance"]["n_turns"] == 12
        assert "isolated_buck" in t1["isat_provenance"]["method"]

    def test_isat_ignores_secondary_winding_turns(self):
        """Changing N_sec must not affect the Isat (set by N_pri only)."""
        a = enrich_tas_for_realism(
            _flybuck_tas(t1_kwargs={"N_pri": 12, "N_sec": 6}),
            topology="isolated_buck", spec=_flybuck_spec(),
        )
        b = enrich_tas_for_realism(
            _flybuck_tas(t1_kwargs={"N_pri": 12, "N_sec": 30}),
            topology="isolated_buck", spec=_flybuck_spec(),
        )
        assert _get_t1(a)["isat"] == _get_t1(b)["isat"]

    def test_end_to_end_realism_passes(self):
        spec = _flybuck_spec()
        enriched = enrich_tas_for_realism(_flybuck_tas(), topology="isolated_buck",
                                          spec=spec)
        r = evaluate_tas(enriched, topology="isolated_buck", spec=spec)
        assert r.verdict is RealismVerdict.PASS
        passed = {c.name for c in r.checks if c.status is CheckStatus.PASS}
        assert {"duty_cycle_bounds", "inductor_isat_margin"}.issubset(passed)


# ---------------------------------------------------------------------------
# Scope-limit pin: reflected secondary load is NOT modelled in v0.1
# ---------------------------------------------------------------------------


class TestScopeLimits:
    def test_secondary_reflected_flag_is_false(self):
        """Regression anchor: v0.1 explicitly does NOT model
        secondary-load reflection.  If a future extension adds it,
        this test must be updated in the same commit, not left to
        drift silently."""
        out = enrich_tas_for_realism(_flybuck_tas(), topology="isolated_buck",
                                     spec=_flybuck_spec())
        prov = _get_t1(out)["ipeak_provenance"]
        assert prov["secondary_reflected_current_modelled"] is False

    def test_step_up_request_throws(self):
        """Primary loop is a buck — Vout_pri >= Vin_min cannot be
        achieved.  Must throw, not silently produce D >= 1."""
        spec = _flybuck_spec()
        spec["inputVoltage"]["minimum"] = 3.0  # below 5 V Vout_pri
        with pytest.raises(EnrichmentError, match="cannot step up"):
            enrich_tas_for_realism(_flybuck_tas(), topology="isolated_buck",
                                   spec=spec)


# ---------------------------------------------------------------------------
# Structural failures
# ---------------------------------------------------------------------------


class TestStructuralFailures:
    def test_missing_isolation_stage_throws(self):
        tas = _flybuck_tas()
        tas["topology"]["stages"] = [
            s for s in tas["topology"]["stages"] if s.get("role") != "isolation"
        ]
        with pytest.raises(EnrichmentError, match="isolation"):
            enrich_tas_for_realism(tas, topology="isolated_buck",
                                   spec=_flybuck_spec())

    def test_missing_pri_winding_throws(self):
        tas = _flybuck_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "isolation":
                stage["circuit"]["components"][0]["mas"]["coil"][
                    "functionalDescription"][0]["name"] = "primary"  # not "pri"
        with pytest.raises(EnrichmentError, match="'pri'"):
            enrich_tas_for_realism(tas, topology="isolated_buck",
                                   spec=_flybuck_spec())

    def test_missing_desiredInductance_throws(self):
        spec = _flybuck_spec()
        del spec["desiredInductance"]
        with pytest.raises(EnrichmentError, match="desiredInductance"):
            enrich_tas_for_realism(_flybuck_tas(), topology="isolated_buck",
                                   spec=spec)

    def test_missing_switchingFrequency_throws(self):
        spec = _flybuck_spec()
        del spec["operatingPoints"][0]["switchingFrequency"]
        with pytest.raises(EnrichmentError, match="switchingFrequency"):
            enrich_tas_for_realism(_flybuck_tas(), topology="isolated_buck",
                                   spec=spec)
