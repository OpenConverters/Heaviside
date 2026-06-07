"""Tests for the active-clamp forward (ACF) realism extractor.

ACF shares the forward family's output-side analytics (same buck-shaped
L_out, same Faraday Isat on the output choke, T1 deliberately
unstamped) but differs in the reset mechanism: the clamp cap +
auxiliary FET absorb the magnetising volt-seconds, so duty is NOT
bounded above by 0.5.  These tests therefore:

  1. Re-verify the shared math at a high-duty operating point that
     would throw for SSF/2SF.
  2. Pin the "no half-duty enforcement" behaviour with a regression
     test (D_max ≈ 0.74).
  3. Confirm the realism gate's generic 0.05 < D < 0.95 CCM bound
     still applies (we throw nothing internally, the gate handles it).

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
# Fixtures (ACF stencil: T1 has pri+sec0 only — no demag winding, since
# reset is provided externally by the clamp cap)
# ---------------------------------------------------------------------------


def _lout_mas(N: int = 18, *, L: float = 4.7e-6) -> dict:
    """Full MAS root for the output choke L_out0, matching the shape the
    real bridge-attach phase produces: a **complete, PyOM-evaluable**
    wound/gapped magnetic under ``core``/``coil`` (built by
    :func:`real_magnetic` so ``calculate_saturation_current`` returns real
    MKF physics) PLUS an ``outputs`` envelope carrying the inductance MKF
    actually achieved.  The extractor harvests the achieved ``L`` from
    ``outputs[*].inductance.magnetizingInductance.magnetizingInductance
    .nominal`` (and would also accept
    ``inputs.designRequirements.magnetizingInductance.nominal``); both are
    provided here so the fixture survives either harvest path.  A gapped
    ETD 49/25/16 keeps the choke Isat high enough to clear the realism gate.
    """
    mas = real_magnetic(
        shape="ETD 49/25/16",
        material="3C95",
        gap_mm=1.0,
        windings=[
            {"name": "Primary", "turns": N, "side": "primary"},
        ],
    )
    mas["inputs"] = {
        "designRequirements": {"magnetizingInductance": {"nominal": L}},
    }
    mas["outputs"] = [
        {
            "inductance": {
                "magnetizingInductance": {
                    "magnetizingInductance": {"nominal": L},
                }
            }
        },
    ]
    return mas


def _t1_mas(*, N_pri: int = 20, N_sec: int = 10) -> dict:
    """ACF T1: 2 windings (pri, sec0).  Clamp cap handles reset, so no
    demag winding is required.  T1 is deliberately NOT Isat-stamped; the
    extractor harvests only its winding turns, so a complete real
    transformer with the named windings is all that's needed.
    """
    return real_magnetic(
        shape="ETD 34/17/11",
        material="3C95",
        gap_mm=0.0,
        windings=[
            {"name": "pri", "turns": N_pri, "side": "primary"},
            {"name": "sec0", "turns": N_sec, "side": "secondary"},
        ],
    )


def _acf_tas(*, t1_kwargs: dict | None = None) -> dict:
    """ACF TAS shape mirroring the stencil at stencils.py:1330.

    Stages: switchingCell (Q1 + Q_clamp + C_clamp) + isolation (T1) +
    outputRectifier (D_fwd, D_fw, L_out0, C_out0).
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
                            {"name": "Q_clamp", "data": "placeholder"},
                            {"name": "C_clamp", "data": "placeholder"},
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
                            {"name": "D_fwd", "data": "placeholder"},
                            {"name": "D_fw", "data": "placeholder"},
                            {"name": "L_out0", "category": "magnetic", "mas": _lout_mas()},
                            {"name": "C_out0", "data": "placeholder"},
                        ]
                    },
                },
            ],
            "interStageCircuit": [],
        }
    }


def _acf_spec_high_duty() -> dict:
    """Vin 36–60 V, Vout 5 V, Iout 10 A, fsw 250 kHz, L_out 4.7 µH.

    With N_pri/N_sec = 20/10 = 2 ⇒ D_max = 5·2/36 = 0.278 — that's
    safely below 0.5 too.  To exercise the "ACF actually allows
    D > 0.5" path we lower the turns ratio AND Vin_min so D_max
    lands around 0.74 (well above the SSF/2SF reset window but
    well below the 0.95 CCM ceiling).
    """
    return {
        "inputVoltage": {"minimum": 27.0, "maximum": 75.0, "nominal": 48.0},
        "desiredInductance": 4.7e-6,
        "efficiency": 0.92,
        "operatingPoints": [
            {
                "outputVoltages": [5.0],
                "outputCurrents": [10.0],
                "switchingFrequency": 250_000.0,
                "ambientTemperature": 25,
            }
        ],
    }


def _get_lout(tas: dict) -> dict:
    for stage in tas["topology"]["stages"]:
        if stage.get("role") == "outputRectifier":
            for c in stage["circuit"]["components"]:
                if c.get("name") == "L_out0":
                    return c
    raise AssertionError("L_out0 not found in any outputRectifier stage")


# ---------------------------------------------------------------------------
# Shared math (still has to hold at the high-duty ACF operating point)
# ---------------------------------------------------------------------------


class TestACFMath:
    def test_duty_uses_turns_ratio_and_vin_min(self):
        out = enrich_tas_for_realism(
            _acf_tas(t1_kwargs={"N_pri": 4, "N_sec": 1}),
            topology="active_clamp_forward",
            spec=_acf_spec_high_duty(),
        )
        # n = 4. D_max = Vout·n / Vin_min = 5·4 / 27 = 0.7407
        assert out["duty_max"] == pytest.approx(5.0 * 4.0 / 27.0, rel=1e-5)
        # D_min = 20 / 75 = 0.2667
        assert out["duty_min"] == pytest.approx(5.0 * 4.0 / 75.0, rel=1e-5)
        assert out["duty"] == out["duty_max"]

    def test_ripple_uses_d_min_buck_shape(self):
        out = enrich_tas_for_realism(
            _acf_tas(t1_kwargs={"N_pri": 4, "N_sec": 1}),
            topology="active_clamp_forward",
            spec=_acf_spec_high_duty(),
        )
        l = _get_lout(out)
        L_worst = 0.8 * 4.7e-6
        d_min = 5.0 * 4.0 / 75.0
        expected = 5.0 * (1.0 - d_min) / (L_worst * 250_000.0)
        assert l["ipeak_provenance"]["ripple_worst_A_pp"] == pytest.approx(expected, rel=1e-6)

    def test_ipeak_is_iout_plus_half_ripple(self):
        out = enrich_tas_for_realism(
            _acf_tas(t1_kwargs={"N_pri": 4, "N_sec": 1}),
            topology="active_clamp_forward",
            spec=_acf_spec_high_duty(),
        )
        l = _get_lout(out)
        ripple = l["ipeak_provenance"]["ripple_worst_A_pp"]
        assert l["ipeak_worst"] == pytest.approx(10.0 + ripple / 2.0, rel=1e-6)

    def test_isat_uses_lout_mas_not_t1(self):
        out = enrich_tas_for_realism(
            _acf_tas(t1_kwargs={"N_pri": 4, "N_sec": 1}),
            topology="active_clamp_forward",
            spec=_acf_spec_high_duty(),
        )
        l = _get_lout(out)
        # Ground truth = MKF: stamped Isat must equal PyOM's saturation
        # current for the L_out magnetic at the op-point ambient (25 °C),
        # NOT an analytical formula.
        expected = isat_of(_lout_mas(), temperature_c=25.0)
        assert l["isat"] == pytest.approx(expected, rel=1e-3)
        # Confirm extractor used the L_out MAS by reading the real shape's
        # effective area and material B_sat back out of the magnetic.
        lout = _lout_mas()
        ae_expected = lout["core"]["processedDescription"]["effectiveParameters"]["effectiveArea"]
        bsat_expected = min(
            p["magneticFluxDensity"]
            for p in lout["core"]["functionalDescription"]["material"]["saturation"]
        )
        assert l["isat_provenance"]["effective_area_m2"] == pytest.approx(ae_expected)
        assert l["isat_provenance"]["b_sat_T"] == pytest.approx(bsat_expected, rel=1e-3)
        assert 0.2 < l["isat_provenance"]["b_sat_T"] < 0.6  # plausible ferrite
        # Provenance must mark this as the ACF variant (so a future
        # refactor can't silently re-use the SSF/2SF wrapper).
        assert "active_clamp_forward" in l["isat_provenance"]["method"]

    def test_t1_is_not_isat_stamped(self):
        out = enrich_tas_for_realism(
            _acf_tas(t1_kwargs={"N_pri": 4, "N_sec": 1}),
            topology="active_clamp_forward",
            spec=_acf_spec_high_duty(),
        )
        for stage in out["topology"]["stages"]:
            if stage.get("role") == "isolation":
                t1 = stage["circuit"]["components"][0]
                assert "isat" not in t1
                assert "ipeak_worst" not in t1
                return
        raise AssertionError("isolation stage missing")

    def test_end_to_end_realism_passes(self):
        spec = _acf_spec_high_duty()
        enriched = enrich_tas_for_realism(
            _acf_tas(t1_kwargs={"N_pri": 4, "N_sec": 1}),
            topology="active_clamp_forward",
            spec=spec,
        )
        r = evaluate_tas(enriched, topology="active_clamp_forward", spec=spec)
        assert r.verdict is RealismVerdict.PASS
        passed = {c.name for c in r.checks if c.status is CheckStatus.PASS}
        assert {"duty_cycle_bounds", "inductor_isat_margin"}.issubset(passed)


# ---------------------------------------------------------------------------
# The whole point of ACF: D > 0.5 must NOT throw (the SSF reset-window
# guard must be disabled).  Pin both the negative case here and the
# fact that the same spec WOULD throw for SSF, as a regression
# anchor.
# ---------------------------------------------------------------------------


class TestNoHalfDutyEnforcement:
    def test_high_duty_does_not_throw_for_acf(self):
        """D_max ≈ 0.74 — clamp cap absorbs reset volt-seconds, so
        enrichment must succeed cleanly."""
        out = enrich_tas_for_realism(
            _acf_tas(t1_kwargs={"N_pri": 4, "N_sec": 1}),
            topology="active_clamp_forward",
            spec=_acf_spec_high_duty(),
        )
        assert out["duty_max"] > 0.5  # sanity: we really are in the forbidden-for-SSF region

    def test_same_spec_still_throws_for_ssf(self):
        """Regression anchor: if anyone weakens the half-duty guard
        for SSF/2SF, this test fails."""
        from tests.unit.test_extract_forward import _ssf_tas

        with pytest.raises(EnrichmentError, match="reset"):
            enrich_tas_for_realism(
                _ssf_tas(t1_kwargs={"N_pri": 4, "N_sec": 1}),
                topology="single_switch_forward",
                spec=_acf_spec_high_duty(),
            )

    def test_extreme_duty_realism_gate_still_catches_over_ceiling(self):
        """If turns ratio is set so D_max ≥ 0.95, the extractor itself
        won't throw, but the realism gate must FAIL the duty-cycle
        bounds check — fail-closed, not silently pass."""
        # n=8, Vin_min=27 ⇒ D_max = 5·8/27 = 1.48 — well above any
        # CCM ceiling.  Extractor stamps it; gate must reject.
        spec = _acf_spec_high_duty()
        enriched = enrich_tas_for_realism(
            _acf_tas(t1_kwargs={"N_pri": 8, "N_sec": 1}),
            topology="active_clamp_forward",
            spec=spec,
        )
        r = evaluate_tas(enriched, topology="active_clamp_forward", spec=spec)
        # At least one of the duty checks must NOT be PASS.
        duty = [c for c in r.checks if c.name == "duty_cycle_bounds"]
        assert duty and duty[0].status is not CheckStatus.PASS


# ---------------------------------------------------------------------------
# Structural failures (same throw contract as the rest of the family)
# ---------------------------------------------------------------------------


class TestStructuralFailures:
    def test_missing_outputRectifier_stage_throws(self):
        tas = _acf_tas()
        tas["topology"]["stages"] = [
            s for s in tas["topology"]["stages"] if s.get("role") != "outputRectifier"
        ]
        with pytest.raises(EnrichmentError, match="outputRectifier"):
            enrich_tas_for_realism(tas, topology="active_clamp_forward", spec=_acf_spec_high_duty())

    def test_missing_isolation_stage_throws(self):
        tas = _acf_tas()
        tas["topology"]["stages"] = [
            s for s in tas["topology"]["stages"] if s.get("role") != "isolation"
        ]
        with pytest.raises(EnrichmentError, match="isolation"):
            enrich_tas_for_realism(tas, topology="active_clamp_forward", spec=_acf_spec_high_duty())

    def test_missing_pri_winding_throws(self):
        tas = _acf_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "isolation":
                stage["circuit"]["components"][0]["mas"]["coil"]["functionalDescription"][0][
                    "name"
                ] = "primary"
        with pytest.raises(EnrichmentError, match="'pri'"):
            enrich_tas_for_realism(tas, topology="active_clamp_forward", spec=_acf_spec_high_duty())

    def test_missing_sec0_winding_throws(self):
        tas = _acf_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "isolation":
                stage["circuit"]["components"][0]["mas"]["coil"]["functionalDescription"][-1][
                    "name"
                ] = "secondary"
        with pytest.raises(EnrichmentError, match="'sec0'"):
            enrich_tas_for_realism(tas, topology="active_clamp_forward", spec=_acf_spec_high_duty())

    def test_missing_achieved_inductance_throws(self):
        """The forward family harvests the achieved choke inductance from
        the L_out MAS root (the figure MKF actually realised), NOT from a
        spec request.  Strip both inductance sources from the L_out MAS and
        the extractor must throw rather than silently default."""
        tas = _acf_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "outputRectifier":
                for c in stage["circuit"]["components"]:
                    if c.get("name") == "L_out0":
                        c["mas"].pop("outputs", None)
                        c["mas"].pop("inputs", None)
        with pytest.raises(EnrichmentError, match=r"full MAS root|inductance"):
            enrich_tas_for_realism(tas, topology="active_clamp_forward", spec=_acf_spec_high_duty())
