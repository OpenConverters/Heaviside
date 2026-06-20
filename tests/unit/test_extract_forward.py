"""Tests for the single-switch and two-switch forward realism extractors.

Forward family differs from the buck/boost/flyback extractors in two
important ways:

  1. The TAS holds TWO magnetics (T1 transformer + L_out output choke)
     across TWO stages (``isolation`` + ``outputRectifier``).  The
     extractor disambiguates by stage role + winding name, so the
     index-based discovery used by the single-magnetic extractors does
     not apply.
  2. T1 is intentionally NOT Isat-checked because the demag winding (or
     two-switch reset diodes) clamp its core every cycle; only L_out0 is
     stamped, and the realism gate's single-magnetic check therefore
     binds on the output choke.

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


def _t1_mas(*, N_pri: int = 40, N_sec: int = 10, include_demag: bool = True) -> dict:
    """MAS for T1 with named windings.

    ``include_demag=True`` mirrors single-switch forward (3 windings:
    pri, demag, sec0); ``False`` mirrors two-switch forward (2
    windings: pri, sec0).  The extractor must succeed for both shapes
    because it looks windings up by name, not index.  T1 is deliberately
    NOT Isat-stamped (the reset mechanism clamps the core every cycle);
    the extractor harvests only its winding turns, so a complete real
    transformer with the named windings is all that's needed.
    """
    windings = [{"name": "pri", "turns": N_pri, "side": "primary"}]
    if include_demag:
        windings.append({"name": "demag", "turns": N_pri, "side": "primary"})
    windings.append({"name": "sec0", "turns": N_sec, "side": "secondary"})
    return real_magnetic(
        shape="ETD 34/17/11",
        material="3C95",
        gap_mm=0.0,
        windings=windings,
    )


def _ssf_tas(*, t1_kwargs: dict | None = None) -> dict:
    """single-switch forward TAS shape (3 stages: primary_switch +
    isolation + outputRectifier)."""
    t1_kwargs = dict(t1_kwargs or {})
    t1_kwargs.setdefault("include_demag", True)
    return {
        "topology": {
            "stages": [
                {
                    "name": "primary_switch",
                    "role": "switchingCell",
                    "circuit": {
                        "components": [
                            {"name": "Q1", "data": "placeholder"},
                            {"name": "D_demag", "data": "placeholder"},
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
            "interStageConnections": [],
        }
    }


def _2sf_tas(*, t1_kwargs: dict | None = None) -> dict:
    """two-switch forward TAS shape — same 3-stage layout, T1 has only
    pri+sec0 (no demag winding)."""
    t1_kwargs = dict(t1_kwargs or {})
    t1_kwargs.setdefault("include_demag", False)
    tas = _ssf_tas(t1_kwargs=t1_kwargs)
    # Replace the T1 MAS with the 2-winding variant (the SSF factory
    # forced include_demag=True; override here).
    for stage in tas["topology"]["stages"]:
        if stage.get("role") == "isolation":
            stage["circuit"]["components"][0]["mas"] = _t1_mas(**t1_kwargs)
    tas["topology"]["stages"][0]["circuit"]["components"] = [
        {"name": "Q1", "data": "placeholder"},
        {"name": "Q2", "data": "placeholder"},
        {"name": "D1", "data": "placeholder"},
        {"name": "D2", "data": "placeholder"},
    ]
    return tas


def _forward_spec() -> dict:
    """Vin 36–60V, Vout 5V, Iout 10A, fsw 250 kHz, L_out 4.7 µH.

    With N_pri/N_sec = 40/10 = 4 ⇒ V_sec_on = Vin/4 ∈ [9, 15] V,
    plenty of headroom over Vout=5.  D_max = 5·4/36 = 0.556 — this
    actually violates the half-duty bound, so the happy-path fixture
    uses a higher Vin_min (54) below.
    """
    return {
        "inputVoltage": {"minimum": 54.0, "maximum": 75.0, "nominal": 60.0},
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
    """Return the enriched L_out0 component from the outputRectifier stage."""
    for stage in tas["topology"]["stages"]:
        if stage.get("role") == "outputRectifier":
            for c in stage["circuit"]["components"]:
                if c.get("name") == "L_out0":
                    return c
    raise AssertionError("L_out0 not found in any outputRectifier stage")


# ---------------------------------------------------------------------------
# Math (parametrised across the two variants — both share the extractor)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "topology,tas_factory",
    [
        ("single_switch_forward", _ssf_tas),
        ("two_switch_forward", _2sf_tas),
    ],
)
class TestForwardMath:
    def test_duty_uses_turns_ratio_and_vin_min(self, topology, tas_factory):
        out = enrich_tas_for_realism(tas_factory(), topology=topology, spec=_forward_spec())
        # n = 40/10 = 4. D_max = Vout·n / Vin_min = 5·4 / 54 = 0.3704
        assert out["duty_max"] == pytest.approx(5.0 * 4.0 / 54.0, rel=1e-5)
        # D_min = Vout·n / Vin_max = 20 / 75 = 0.2667
        assert out["duty_min"] == pytest.approx(5.0 * 4.0 / 75.0, rel=1e-5)
        assert out["duty"] == out["duty_max"]

    def test_ripple_uses_d_min_buck_shape(self, topology, tas_factory):
        out = enrich_tas_for_realism(tas_factory(), topology=topology, spec=_forward_spec())
        l = _get_lout(out)
        L_worst = 0.8 * 4.7e-6
        d_min = 5.0 * 4.0 / 75.0
        expected = 5.0 * (1.0 - d_min) / (L_worst * 250_000.0)
        assert l["ipeak_provenance"]["ripple_worst_A_pp"] == pytest.approx(expected, rel=1e-6)

    def test_ipeak_is_iout_plus_half_ripple(self, topology, tas_factory):
        out = enrich_tas_for_realism(tas_factory(), topology=topology, spec=_forward_spec())
        l = _get_lout(out)
        ripple = l["ipeak_provenance"]["ripple_worst_A_pp"]
        assert l["ipeak_worst"] == pytest.approx(10.0 + ripple / 2.0, rel=1e-6)

    def test_isat_uses_lout_mas_not_t1(self, topology, tas_factory):
        out = enrich_tas_for_realism(tas_factory(), topology=topology, spec=_forward_spec())
        l = _get_lout(out)
        # Ground truth = MKF: stamped Isat must equal PyOM's saturation
        # current for the L_out magnetic at the op-point ambient (25 °C),
        # NOT an analytical formula.  Computing it here on the same L_out
        # MAS the extractor harvested also proves L_out (not T1) was used.
        expected = isat_of(_lout_mas(), temperature_c=100.0)
        assert l["isat"] == pytest.approx(expected, rel=1e-3)
        # Confirm extractor used the L_out MAS, NOT T1's (different A_e),
        # by reading the real shape's effective area and material B_sat back
        # out of the L_out magnetic itself.
        lout = _lout_mas()
        ae_expected = lout["core"]["processedDescription"]["effectiveParameters"]["effectiveArea"]
        bsat_expected = min(
            p["magneticFluxDensity"]
            for p in lout["core"]["functionalDescription"]["material"]["saturation"]
        )
        assert l["isat_provenance"]["effective_area_m2"] == pytest.approx(ae_expected)
        assert l["isat_provenance"]["b_sat_T"] == pytest.approx(bsat_expected, rel=1e-3)
        assert 0.2 < l["isat_provenance"]["b_sat_T"] < 0.6  # plausible ferrite

    def test_t1_is_not_isat_stamped(self, topology, tas_factory):
        """The demag mechanism resets T1 each cycle, so we deliberately
        skip Isat on it.  The TAS must come out with T1 unchanged."""
        out = enrich_tas_for_realism(tas_factory(), topology=topology, spec=_forward_spec())
        for stage in out["topology"]["stages"]:
            if stage.get("role") == "isolation":
                t1 = stage["circuit"]["components"][0]
                assert "isat" not in t1
                assert "ipeak_worst" not in t1
                return
        raise AssertionError("isolation stage missing")

    def test_end_to_end_realism_passes(self, topology, tas_factory):
        enriched = enrich_tas_for_realism(tas_factory(), topology=topology, spec=_forward_spec())
        r = evaluate_tas(enriched, topology=topology, spec=_forward_spec())
        assert r.verdict is RealismVerdict.PASS
        passed = {c.name for c in r.checks if c.status is CheckStatus.PASS}
        assert {"duty_cycle_bounds", "inductor_isat_margin"}.issubset(passed)


# ---------------------------------------------------------------------------
# Reset-window violation: D ≥ 0.5 must throw
# ---------------------------------------------------------------------------


class TestResetWindowGuard:
    def test_high_duty_throws_for_ssf(self):
        spec = _forward_spec()
        # Lower Vin_min to push D_max above 0.5: 5·4 / 36 = 0.556
        spec["inputVoltage"]["minimum"] = 36.0
        with pytest.raises(EnrichmentError, match="reset"):
            enrich_tas_for_realism(_ssf_tas(), topology="single_switch_forward", spec=spec)

    def test_high_duty_throws_for_2sf(self):
        spec = _forward_spec()
        spec["inputVoltage"]["minimum"] = 36.0
        with pytest.raises(EnrichmentError, match="reset"):
            enrich_tas_for_realism(_2sf_tas(), topology="two_switch_forward", spec=spec)


# ---------------------------------------------------------------------------
# Winding lookup by name (single-switch has 3 windings, two-switch has 2)
# ---------------------------------------------------------------------------


class TestWindingLookup:
    def test_ssf_with_demag_winding_works(self):
        """3-winding T1 (pri, demag, sec0) must extract pri / sec0
        correctly via name lookup even though sec0 is at index 2."""
        out = enrich_tas_for_realism(
            _ssf_tas(), topology="single_switch_forward", spec=_forward_spec()
        )
        l = _get_lout(out)
        assert l["ipeak_provenance"]["n_primary"] == 40
        assert l["ipeak_provenance"]["n_secondary"] == 10

    def test_2sf_without_demag_winding_works(self):
        """2-winding T1 (pri, sec0) must extract identically — same
        names, different index."""
        out = enrich_tas_for_realism(
            _2sf_tas(), topology="two_switch_forward", spec=_forward_spec()
        )
        l = _get_lout(out)
        assert l["ipeak_provenance"]["n_primary"] == 40
        assert l["ipeak_provenance"]["n_secondary"] == 10

    def test_missing_pri_winding_throws(self):
        tas = _ssf_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "isolation":
                stage["circuit"]["components"][0]["mas"]["coil"]["functionalDescription"][0][
                    "name"
                ] = "primary"  # not "pri"
        with pytest.raises(EnrichmentError, match="'pri'"):
            enrich_tas_for_realism(tas, topology="single_switch_forward", spec=_forward_spec())

    def test_missing_sec0_winding_throws(self):
        tas = _ssf_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "isolation":
                fd = stage["circuit"]["components"][0]["mas"]["coil"]["functionalDescription"]
                fd[-1]["name"] = "secondary"  # not "sec0"
        with pytest.raises(EnrichmentError, match="'sec0'"):
            enrich_tas_for_realism(tas, topology="single_switch_forward", spec=_forward_spec())


# ---------------------------------------------------------------------------
# Structural failures
# ---------------------------------------------------------------------------


class TestStructuralFailures:
    def test_missing_outputRectifier_stage_throws(self):
        tas = _ssf_tas()
        tas["topology"]["stages"] = [
            s for s in tas["topology"]["stages"] if s.get("role") != "outputRectifier"
        ]
        with pytest.raises(EnrichmentError, match="outputRectifier"):
            enrich_tas_for_realism(tas, topology="single_switch_forward", spec=_forward_spec())

    def test_missing_isolation_stage_throws(self):
        tas = _ssf_tas()
        tas["topology"]["stages"] = [
            s for s in tas["topology"]["stages"] if s.get("role") != "isolation"
        ]
        with pytest.raises(EnrichmentError, match="isolation"):
            enrich_tas_for_realism(tas, topology="single_switch_forward", spec=_forward_spec())

    def test_missing_achieved_inductance_throws(self):
        """The forward family harvests the achieved choke inductance from
        the L_out0 MAS (``outputs`` envelope / ``designRequirements``), not
        the spec request.  Strip both inductance sources from the L_out MAS
        and enrichment must throw rather than silently default."""
        tas = _ssf_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "outputRectifier":
                for c in stage["circuit"]["components"]:
                    if c.get("name") == "L_out0":
                        c["mas"].pop("outputs", None)
                        c["mas"].get("inputs", {}).get("designRequirements", {}).pop(
                            "magnetizingInductance", None
                        )
        with pytest.raises(EnrichmentError, match=r"full MAS root|inductance"):
            enrich_tas_for_realism(tas, topology="single_switch_forward", spec=_forward_spec())
