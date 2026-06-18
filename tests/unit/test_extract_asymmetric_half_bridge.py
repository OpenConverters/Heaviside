"""Tests for the asymmetric half-bridge (AHB) realism extractor.

AHB voltage transfer: ``Vout = 2·n·D·(1 − D)·Vin`` with
``n = N_sec / N_pri``.  Output choke sees the full-bridge rectifier's
two pulses per primary period at ``fsw_eff = 2·fsw`` (same shape as
push-pull).  D solved from the smaller (practical) root of the
quadratic; throws when discriminant collapses at Vin_min (k ≥ 0.25).

T1 is intentionally NOT Isat-stamped (asymmetric drive + C_b cancel
volt-seconds, same rationale as forward / push-pull T1).

Per CLAUDE.md "throw, never default": every missing or invalid spec /
MAS field must raise EnrichmentError — no silent fallbacks.
"""

from __future__ import annotations

import math

import pytest

from heaviside.pipeline import evaluate_tas
from heaviside.pipeline.extract import EnrichmentError, enrich_tas_for_realism
from heaviside.pipeline.realism import CheckStatus, RealismVerdict
from tests.unit._real_mas import isat_of, real_magnetic

# ---------------------------------------------------------------------------
# Fixtures (AHB stencil: T1 has pri+sec0 only; L_out0 in
# outputRectifier — stencils.py:2642)
# ---------------------------------------------------------------------------


def _lout_mas(N: int = 14, *, L: float = 10e-6) -> dict:
    """Full MAS root for the output choke L_out0, matching the shape the
    real bridge-attach phase produces: a **complete, PyOM-evaluable**
    wound/gapped magnetic under ``core``/``coil`` (built by
    :func:`real_magnetic` so ``calculate_saturation_current`` returns real
    gap-aware MKF physics) PLUS an ``outputs`` envelope carrying the
    inductance MKF actually achieved.  The extractor harvests the achieved
    ``L`` from ``outputs[*].inductance.magnetizingInductance
    .magnetizingInductance.nominal`` (and would also accept
    ``inputs.designRequirements.magnetizingInductance.nominal``); both are
    provided here so the fixture survives either harvest path.  The choke
    is gapped (~1 mm) as a real output inductor must be.
    """
    mas = real_magnetic(
        shape="ETD 29/16/10",
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


def _t1_mas(*, N_pri: int = 10, N_sec: int = 4) -> dict:
    # T1 is deliberately NOT Isat-stamped (asymmetric drive + C_b cancel
    # volt-seconds); the extractor harvests only its winding turns for the
    # turns ratio, so a complete real (ungapped) transformer with the two
    # named windings is all that's needed.
    return real_magnetic(
        shape="ETD 34/17/11",
        material="3C95",
        gap_mm=0.0,
        windings=[
            {"name": "pri", "turns": N_pri, "side": "primary"},
            {"name": "sec0", "turns": N_sec, "side": "secondary"},
        ],
    )


def _ahb_tas(*, t1_kwargs: dict | None = None) -> dict:
    t1_kwargs = dict(t1_kwargs or {})
    return {
        "topology": {
            "stages": [
                {
                    "name": "inverter",
                    "role": "inverter",
                    "circuit": {
                        "components": [
                            {"name": "Q1", "data": "placeholder"},
                            {"name": "Q2", "data": "placeholder"},
                            {"name": "C_b", "data": "placeholder"},
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
                            {"name": "D3", "data": "placeholder"},
                            {"name": "D4", "data": "placeholder"},
                            {"name": "L_out0", "category": "magnetic", "mas": _lout_mas()},
                            {"name": "C_out0", "data": "placeholder"},
                        ]
                    },
                },
            ],
            "interStageConnections": [],
        }
    }


def _ahb_spec() -> dict:
    """Vin 200-400 V, Vout 12 V, Iout 10 A, fsw 100 kHz, L_out 10 µH.

    With N_pri/N_sec = 10/4 ⇒ n = 0.4.
    k(Vin_min=200) = 12 / (2·0.4·200) = 0.075 ⇒ D = (1 − sqrt(0.7))/2
    ≈ 0.0917.  k(Vin_max=400) = 0.0375 ⇒ D ≈ 0.0388.  Well below 0.5.
    """
    return {
        "inputVoltage": {"minimum": 200.0, "maximum": 400.0, "nominal": 300.0},
        "desiredInductance": 10e-6,
        "efficiency": 0.93,
        "operatingPoints": [
            {
                "outputVoltages": [12.0],
                "outputCurrents": [10.0],
                "switchingFrequency": 100_000.0,
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
    raise AssertionError("L_out0 not found")


def _d_solve(*, vout: float, vin: float, n: float) -> float:
    k = vout / (2.0 * n * vin)
    return (1.0 - math.sqrt(1.0 - 4.0 * k)) / 2.0


# ---------------------------------------------------------------------------
# Voltage-transfer math
# ---------------------------------------------------------------------------


class TestAHBMath:
    def test_duty_smaller_root_at_both_vin_extremes(self):
        out = enrich_tas_for_realism(
            _ahb_tas(),
            topology="asymmetric_half_bridge",
            spec=_ahb_spec(),
        )
        n = 4.0 / 10.0
        d_q_max_expected = _d_solve(vout=12.0, vin=200.0, n=n)
        d_q_min_expected = _d_solve(vout=12.0, vin=400.0, n=n)
        l = _get_lout(out)
        assert l["ipeak_provenance"]["d_per_switch_max"] == pytest.approx(
            d_q_max_expected, abs=1e-5
        )
        assert l["ipeak_provenance"]["d_per_switch_min"] == pytest.approx(
            d_q_min_expected, abs=1e-5
        )
        # duty_max/min on TAS root use D_eff = 2·D_q
        assert out["duty_max"] == pytest.approx(2.0 * d_q_max_expected, abs=1e-5)
        assert out["duty_min"] == pytest.approx(2.0 * d_q_min_expected, abs=1e-5)
        assert out["duty"] == out["duty_max"]

    def test_d_solution_reproduces_vout_transfer(self):
        """Round-trip: plug solved D back into Vout = 2·n·D·(1−D)·Vin."""
        out = enrich_tas_for_realism(
            _ahb_tas(),
            topology="asymmetric_half_bridge",
            spec=_ahb_spec(),
        )
        n = 4.0 / 10.0
        l = _get_lout(out)
        d_q_max = l["ipeak_provenance"]["d_per_switch_max"]
        # At Vin_min the synthesised Vout must equal the spec Vout.
        vout_reconstructed = 2.0 * n * d_q_max * (1.0 - d_q_max) * 200.0
        assert vout_reconstructed == pytest.approx(12.0, rel=1e-4)

    def test_per_switch_duty_stays_below_half(self):
        """Practical AHB always operates in the smaller-root regime."""
        out = enrich_tas_for_realism(
            _ahb_tas(),
            topology="asymmetric_half_bridge",
            spec=_ahb_spec(),
        )
        l = _get_lout(out)
        assert l["ipeak_provenance"]["d_per_switch_max"] < 0.5

    def test_ripple_uses_2x_fsw_and_d_eff_min_buck_shape(self):
        out = enrich_tas_for_realism(
            _ahb_tas(),
            topology="asymmetric_half_bridge",
            spec=_ahb_spec(),
        )
        l = _get_lout(out)
        n = 4.0 / 10.0
        d_eff_min = 2.0 * _d_solve(vout=12.0, vin=400.0, n=n)
        L_worst = 0.8 * 10e-6
        fsw_eff = 2.0 * 100_000.0
        expected = 12.0 * (1.0 - d_eff_min) / (L_worst * fsw_eff)
        assert l["ipeak_provenance"]["ripple_worst_A_pp"] == pytest.approx(expected, rel=1e-5)
        assert l["ipeak_provenance"]["fsw_effective_Hz"] == pytest.approx(2.0 * 100_000.0)
        assert l["ipeak_provenance"]["fsw_per_switch_Hz"] == pytest.approx(100_000.0)

    def test_ipeak_is_iout_plus_half_ripple(self):
        out = enrich_tas_for_realism(
            _ahb_tas(),
            topology="asymmetric_half_bridge",
            spec=_ahb_spec(),
        )
        l = _get_lout(out)
        ripple = l["ipeak_provenance"]["ripple_worst_A_pp"]
        assert l["ipeak_worst"] == pytest.approx(10.0 + ripple / 2.0, rel=1e-6)

    def test_turns_ratio_recorded_in_provenance(self):
        out = enrich_tas_for_realism(
            _ahb_tas(t1_kwargs={"N_pri": 20, "N_sec": 4}),
            topology="asymmetric_half_bridge",
            spec=_ahb_spec(),
        )
        l = _get_lout(out)
        assert l["ipeak_provenance"]["turns_ratio_n_sec_over_n_pri"] == pytest.approx(0.2, rel=1e-6)
        assert l["ipeak_provenance"]["n_primary"] == 20
        assert l["ipeak_provenance"]["n_secondary"] == 4

    def test_isat_uses_lout_mas_not_t1(self):
        out = enrich_tas_for_realism(
            _ahb_tas(),
            topology="asymmetric_half_bridge",
            spec=_ahb_spec(),
        )
        l = _get_lout(out)
        # Ground truth = MKF: the stamped Isat must equal PyOM's saturation
        # current for the L_out magnetic at the op-point ambient (25 °C),
        # NOT an analytical formula.  Recomputing it here on the same L_out
        # MAS the extractor harvested also proves L_out (not T1) was the
        # source.
        expected = isat_of(_lout_mas(), temperature_c=25.0)
        assert l["isat"] == pytest.approx(expected, rel=1e-3)
        assert "PyOM" in l["isat_provenance"]["method"]
        assert "asymmetric_half_bridge" in l["isat_provenance"]["method"]

    def test_t1_is_not_isat_stamped(self):
        out = enrich_tas_for_realism(
            _ahb_tas(),
            topology="asymmetric_half_bridge",
            spec=_ahb_spec(),
        )
        for stage in out["topology"]["stages"]:
            if stage.get("role") == "isolation":
                t1 = stage["circuit"]["components"][0]
                assert "isat" not in t1
                assert "ipeak_worst" not in t1
                return
        raise AssertionError("isolation stage missing")

    def test_end_to_end_realism_passes(self):
        spec = _ahb_spec()
        enriched = enrich_tas_for_realism(
            _ahb_tas(),
            topology="asymmetric_half_bridge",
            spec=spec,
        )
        r = evaluate_tas(enriched, topology="asymmetric_half_bridge", spec=spec)
        assert r.verdict is RealismVerdict.PASS
        passed = {c.name for c in r.checks if c.status is CheckStatus.PASS}
        assert {"duty_cycle_bounds", "inductor_isat_margin"}.issubset(passed)


# ---------------------------------------------------------------------------
# Discriminant collapse: k ≥ 0.25 means n·Vin < 2·Vout → no real D
# ---------------------------------------------------------------------------


class TestDiscriminantGuard:
    def test_k_at_quarter_throws(self):
        """k = Vout/(2·n·Vin) = 0.25 exactly: D would be 0.5, hard limit."""
        # n = 4/10 = 0.4.  Need Vout/(2·0.4·Vin) = 0.25 ⇒ Vin = 5·Vout.
        # With Vout = 12 ⇒ Vin = 60.  Set Vin_min = 60.
        spec = _ahb_spec()
        spec["inputVoltage"]["minimum"] = 60.0
        with pytest.raises(EnrichmentError, match=r"k = Vout"):
            enrich_tas_for_realism(
                _ahb_tas(),
                topology="asymmetric_half_bridge",
                spec=spec,
            )

    def test_k_above_quarter_throws(self):
        """Vin too low: D root goes imaginary."""
        spec = _ahb_spec()
        spec["inputVoltage"]["minimum"] = 40.0  # k = 12/(0.8·40)=0.375
        with pytest.raises(EnrichmentError, match=r"no real root"):
            enrich_tas_for_realism(
                _ahb_tas(),
                topology="asymmetric_half_bridge",
                spec=spec,
            )

    def test_just_above_quarter_throws_at_vin_min_only(self):
        """Vin_min collapses, Vin_max fine — must throw before reaching
        Vin_max evaluation."""
        spec = _ahb_spec()
        spec["inputVoltage"]["minimum"] = 50.0  # k_min = 0.3
        with pytest.raises(EnrichmentError, match=r"Vin = 50"):
            enrich_tas_for_realism(
                _ahb_tas(),
                topology="asymmetric_half_bridge",
                spec=spec,
            )


# ---------------------------------------------------------------------------
# Structural failures
# ---------------------------------------------------------------------------


class TestStructuralFailures:
    def test_missing_isolation_stage_throws(self):
        tas = _ahb_tas()
        tas["topology"]["stages"] = [
            s for s in tas["topology"]["stages"] if s.get("role") != "isolation"
        ]
        with pytest.raises(EnrichmentError, match="isolation"):
            enrich_tas_for_realism(tas, topology="asymmetric_half_bridge", spec=_ahb_spec())

    def test_missing_outputRectifier_stage_throws(self):
        tas = _ahb_tas()
        tas["topology"]["stages"] = [
            s for s in tas["topology"]["stages"] if s.get("role") != "outputRectifier"
        ]
        with pytest.raises(EnrichmentError, match="outputRectifier"):
            enrich_tas_for_realism(tas, topology="asymmetric_half_bridge", spec=_ahb_spec())

    def test_missing_pri_winding_throws(self):
        tas = _ahb_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "isolation":
                stage["circuit"]["components"][0]["mas"]["coil"]["functionalDescription"][0][
                    "name"
                ] = "primary"
        with pytest.raises(EnrichmentError, match="'pri'"):
            enrich_tas_for_realism(tas, topology="asymmetric_half_bridge", spec=_ahb_spec())

    def test_missing_sec0_winding_throws(self):
        tas = _ahb_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "isolation":
                stage["circuit"]["components"][0]["mas"]["coil"]["functionalDescription"][-1][
                    "name"
                ] = "secondary"
        with pytest.raises(EnrichmentError, match="'sec0'"):
            enrich_tas_for_realism(tas, topology="asymmetric_half_bridge", spec=_ahb_spec())

    def test_missing_lout_inductance_throws(self):
        """The AHB extractor harvests the output-choke inductance from the
        L_out MAS root MKF actually achieved (outputs / designRequirements
        envelopes) — NOT from spec.desiredInductance.  An L_out MAS with no
        usable inductance must raise (no silent fallback), per CLAUDE.md.
        """
        tas = _ahb_tas()
        lout = _get_lout(tas)
        # Strip both harvest paths: no outputs envelope, no inputs root.
        lout["mas"].pop("outputs", None)
        lout["mas"].pop("inputs", None)
        with pytest.raises(EnrichmentError, match=r"full MAS root|usable inductance"):
            enrich_tas_for_realism(tas, topology="asymmetric_half_bridge", spec=_ahb_spec())

    def test_desiredInductance_not_required_lout_mas_is_authoritative(self):
        """desiredInductance in spec is irrelevant to the AHB extractor:
        the achieved L_out comes from the L_out MAS root.  Removing it from
        the spec must NOT break enrichment."""
        spec = _ahb_spec()
        del spec["desiredInductance"]
        out = enrich_tas_for_realism(
            _ahb_tas(),
            topology="asymmetric_half_bridge",
            spec=spec,
        )
        # L_out harvested from MAS (10 µH) drives the ripple math.
        assert _get_lout(out)["ipeak_provenance"]["L_worst_H"] == pytest.approx(
            0.8 * 10e-6, rel=1e-9
        )
