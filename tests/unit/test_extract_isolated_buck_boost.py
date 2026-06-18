"""Tests for the isolated_buck_boost (inverting) realism extractor.

Isolated buck-boost is an inverting topology: the primary rail
``Vout_pri`` is negative; the spec carries its magnitude per
the extractor family's convention.  T1 is the binding magnetic —
same shape as flybuck — but the analytics are buck-boost rather
than buck:

  * D = |Vout_pri| / (Vin + |Vout_pri|)
  * Ripple peaks at Vin_max (monotone increasing in Vin)
  * Avg primary current peaks at Vin_min (D_max)
  * Ipeak_worst combines both, exactly like the boost extractor

This gives a pessimistic upper bound because a real cycle cannot
hit both worst cases simultaneously, but stamping anything less
would allow a real cycle to exceed the stamped value.

Per CLAUDE.md "throw, never default": every missing or invalid spec /
MAS field must raise EnrichmentError.
"""

from __future__ import annotations

import pytest

from heaviside.pipeline import evaluate_tas
from heaviside.pipeline.extract import EnrichmentError, enrich_tas_for_realism
from heaviside.pipeline.realism import CheckStatus, RealismVerdict
from tests.unit._real_mas import isat_of, real_magnetic

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _t1_mas(*, N_pri: int = 20, N_sec: int = 10, L_pri: float | None = 47e-6) -> dict:
    """Isolated buck-boost T1 MAS: 2 windings (pri, sec0), one **complete,
    PyOM-evaluable** gapped coupled-inductor magnetic (built by
    :func:`real_magnetic` so ``calculate_saturation_current`` returns real
    MKF physics).

    T1 is the binding magnetic — its primary winding *is* the buck-boost
    inductor — so it is gapped (``gap_mm > 0``) and Isat-stamped. Higher
    N_pri (vs the flybuck fixture) gives more Isat headroom since
    buck-boost primary current is higher (I_L_avg = Iout/(1-D)) than
    a pure buck at the same load.

    The extractor harvests the MKF-*achieved* primary inductance from the
    full MAS root (``outputs[*].inductance.magnetizingInductance.
    magnetizingInductance.nominal``), the shape the real bridge-attach
    phase produces — not from ``spec.desiredInductance``. ``L_pri`` seeds
    that envelope (set ``None`` to omit it, exercising the extractor's
    "no achieved inductance → throw" guard).
    """
    mas = real_magnetic(
        shape="ETD 29/16/10",
        material="3C95",
        gap_mm=1.0,
        windings=[
            {"name": "pri", "turns": N_pri, "side": "primary"},
            {"name": "sec0", "turns": N_sec, "side": "secondary"},
        ],
    )
    if L_pri is not None:
        # Full MAS root: the simulation-derived magnetizing inductance MKF
        # actually achieved for the wound + gapped core (mirrors the real
        # design_magnetics output envelope).
        mas["outputs"] = [
            {
                "inductance": {
                    "magnetizingInductance": {
                        "magnetizingInductance": {"nominal": L_pri},
                    },
                },
            },
        ]
    return mas


def _ibb_tas(*, t1_kwargs: dict | None = None) -> dict:
    """Isolated buck-boost TAS shape mirroring the stencil at
    stencils.py:1611.

    Stages: switchingCell (Q1) + isolation (T1) + outputRectifier
    (D_pri, C_pri at Vout_pri) + outputRectifier (D_out0, C_out0 at
    Vout0) + controller.
    """
    t1_kwargs = dict(t1_kwargs or {})
    return {
        "topology": {
            "stages": [
                {
                    "name": "primary_switch",
                    "role": "switchingCell",
                    "circuit": {
                        "components": [
                            {"name": "Q1", "data": "placeholder"},
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
                    "name": "output_pri",
                    "role": "outputRectifier",
                    "circuit": {
                        "components": [
                            {"name": "D_pri", "data": "placeholder"},
                            {"name": "C_pri", "data": "placeholder"},
                        ]
                    },
                },
                {
                    "name": "output_0",
                    "role": "outputRectifier",
                    "circuit": {
                        "components": [
                            {"name": "D_out0", "data": "placeholder"},
                            {"name": "C_out0", "data": "placeholder"},
                        ]
                    },
                },
            ],
            "interStageConnections": [],
        }
    }


def _ibb_spec() -> dict:
    """Vin 12-24 V, |Vout_pri| 12 V, Iout 1.5 A, fsw 250 kHz, L_pri 47 µH.

    Numbers:
      D_max = 12 / (12+12) = 0.5
      D_min = 12 / (24+12) = 0.333
      L_worst = 0.8 * 47e-6 = 37.6e-6
      ripple_worst = 24 * 0.333 / (37.6e-6 * 250e3) = 0.851 A
      iL_avg_max = 1.5 / (1 - 0.5) = 3.0 A
      Ipeak_worst = 3.0 + 0.851/2 = 3.426 A
      Isat = PyOM ground truth for the real gapped T1 magnetic (see
             _t1_mas); the Isat margin = Isat / (1.2 * 3.426) must pass.
    """
    return {
        "inputVoltage": {"minimum": 12.0, "maximum": 24.0, "nominal": 18.0},
        "desiredInductance": 47e-6,
        "efficiency": 0.88,
        "operatingPoints": [
            {
                "outputVoltages": [12.0],  # magnitude of (negative) Vout_pri
                "outputCurrents": [1.5],
                "switchingFrequency": 250_000.0,
                "ambientTemperature": 25,
            }
        ],
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


class TestIBBMath:
    def test_duty_is_buck_boost_shaped(self):
        out = enrich_tas_for_realism(_ibb_tas(), topology="isolated_buck_boost", spec=_ibb_spec())
        # D_max = |Vout|/(Vin_min+|Vout|) = 12/24 = 0.5
        assert out["duty_max"] == pytest.approx(0.5, rel=1e-5)
        # D_min = 12/36 = 0.333
        assert out["duty_min"] == pytest.approx(12.0 / 36.0, rel=1e-5)
        assert out["duty"] == out["duty_max"]

    def test_avg_current_uses_d_max_not_d_min(self):
        """I_L_avg_max = Iout / (1 - D_max) — must use the high-duty
        extreme (Vin_min), not the low-duty one."""
        out = enrich_tas_for_realism(_ibb_tas(), topology="isolated_buck_boost", spec=_ibb_spec())
        prov = _get_t1(out)["ipeak_provenance"]
        assert prov["iL_avg_max_A"] == pytest.approx(1.5 / (1.0 - 0.5), rel=1e-5)

    def test_ripple_peaks_at_vin_max(self):
        """For inverting buck-boost ΔI is monotone increasing in Vin,
        so the worst case lives at Vin_max — not Vin_min."""
        out = enrich_tas_for_realism(_ibb_tas(), topology="isolated_buck_boost", spec=_ibb_spec())
        prov = _get_t1(out)["ipeak_provenance"]
        L_worst = 0.8 * 47e-6
        d_min = 12.0 / 36.0
        expected = 24.0 * d_min / (L_worst * 250_000.0)
        assert prov["ripple_worst_A_pp"] == pytest.approx(expected, rel=1e-6)

    def test_ipeak_combines_opposite_vin_extremes(self):
        """Avg current is taken at Vin_min, ripple at Vin_max — the
        pessimistic upper bound is the sum.  A real cycle cannot hit
        both, but stamping anything less would let a real cycle
        exceed Ipeak_worst."""
        out = enrich_tas_for_realism(_ibb_tas(), topology="isolated_buck_boost", spec=_ibb_spec())
        t1 = _get_t1(out)
        ripple = t1["ipeak_provenance"]["ripple_worst_A_pp"]
        avg = t1["ipeak_provenance"]["iL_avg_max_A"]
        assert t1["ipeak_worst"] == pytest.approx(avg + ripple / 2.0, rel=1e-6)

    def test_isat_uses_primary_turns(self):
        out = enrich_tas_for_realism(_ibb_tas(), topology="isolated_buck_boost", spec=_ibb_spec())
        t1 = _get_t1(out)
        # Ground truth = MKF: stamped Isat must equal PyOM's saturation
        # current for the real gapped T1 magnetic at the op-point ambient
        # (25 °C), NOT an analytical formula.
        expected = isat_of(_t1_mas(), temperature_c=25.0)
        assert t1["isat"] == pytest.approx(expected, rel=1e-3)
        assert t1["isat_provenance"]["n_turns"] == 20
        assert "isolated_buck_boost" in t1["isat_provenance"]["method"]

    def test_end_to_end_realism_passes(self):
        spec = _ibb_spec()
        enriched = enrich_tas_for_realism(_ibb_tas(), topology="isolated_buck_boost", spec=spec)
        r = evaluate_tas(enriched, topology="isolated_buck_boost", spec=spec)
        assert r.verdict is RealismVerdict.PASS
        passed = {c.name for c in r.checks if c.status is CheckStatus.PASS}
        assert {"duty_cycle_bounds", "inductor_isat_margin"}.issubset(passed)


# ---------------------------------------------------------------------------
# Scope-limit pins + sign convention
# ---------------------------------------------------------------------------


class TestScopeLimits:
    def test_secondary_reflected_flag_is_false(self):
        out = enrich_tas_for_realism(_ibb_tas(), topology="isolated_buck_boost", spec=_ibb_spec())
        prov = _get_t1(out)["ipeak_provenance"]
        assert prov["secondary_reflected_current_modelled"] is False

    def test_negative_vout_in_spec_throws(self):
        """The spec convention is to carry the |Vout_pri| magnitude;
        feeding a negative number must throw (not silently produce a
        negative duty)."""
        spec = _ibb_spec()
        spec["operatingPoints"][0]["outputVoltages"] = [-12.0]
        with pytest.raises(EnrichmentError, match="magnitude"):
            enrich_tas_for_realism(_ibb_tas(), topology="isolated_buck_boost", spec=spec)

    def test_extreme_step_up_still_bounded_by_realism_gate(self):
        """If |Vout| >> Vin_min, D approaches 1 — extractor stamps it
        without throwing; the realism gate must catch it via the CCM
        duty ceiling."""
        spec = _ibb_spec()
        spec["operatingPoints"][0]["outputVoltages"] = [240.0]  # D_max = 240/252 = 0.95
        enriched = enrich_tas_for_realism(_ibb_tas(), topology="isolated_buck_boost", spec=spec)
        r = evaluate_tas(enriched, topology="isolated_buck_boost", spec=spec)
        duty = [c for c in r.checks if c.name == "duty_cycle_bounds"]
        assert duty and duty[0].status is not CheckStatus.PASS


# ---------------------------------------------------------------------------
# Structural failures
# ---------------------------------------------------------------------------


class TestStructuralFailures:
    def test_missing_isolation_stage_throws(self):
        tas = _ibb_tas()
        tas["topology"]["stages"] = [
            s for s in tas["topology"]["stages"] if s.get("role") != "isolation"
        ]
        with pytest.raises(EnrichmentError, match="isolation"):
            enrich_tas_for_realism(tas, topology="isolated_buck_boost", spec=_ibb_spec())

    def test_missing_pri_winding_throws(self):
        tas = _ibb_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "isolation":
                stage["circuit"]["components"][0]["mas"]["coil"]["functionalDescription"][0][
                    "name"
                ] = "primary"
        with pytest.raises(EnrichmentError, match="'pri'"):
            enrich_tas_for_realism(tas, topology="isolated_buck_boost", spec=_ibb_spec())

    def test_missing_achieved_inductance_throws(self):
        """L_pri comes from the MKF-achieved inductance in the full MAS
        root, not from spec.desiredInductance. Omitting that envelope
        must throw (no silent fallback)."""
        tas = _ibb_tas(t1_kwargs={"L_pri": None})
        with pytest.raises(EnrichmentError, match="MAS"):
            enrich_tas_for_realism(tas, topology="isolated_buck_boost", spec=_ibb_spec())
