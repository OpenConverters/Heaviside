"""Tests for the push-pull realism extractor.

Push-pull's output choke sees TWO ramps per switching period (one from
each primary half), so the extractor works in *effective* duty form
``D_eff = Vout·n / Vin = 2·D_q`` at ``fsw_eff = 2·fsw``.  Under that
substitution the output-side math is identical to the forward family,
but the throw threshold is at ``D_eff ≥ 1.0`` (per-switch overlap
shorts the transformer) — NOT 0.5 like SSF/2SF reset.

T1 itself is deliberately NOT Isat-stamped: the alternating-polarity
drive resets its core every cycle, same rationale as the forward
family.  L_out0 binds the ``inductor_isat_margin`` realism check via
its own MAS.

Per CLAUDE.md "throw, never default": every missing or invalid spec /
MAS field must raise EnrichmentError — no silent fallbacks.
"""

from __future__ import annotations

import pytest

from heaviside.pipeline import evaluate_tas
from heaviside.pipeline.extract import EnrichmentError, enrich_tas_for_realism
from heaviside.pipeline.realism import CheckStatus, RealismVerdict


# ---------------------------------------------------------------------------
# Fixtures (push-pull stencil: T1 has FOUR windings pri_top/pri_bot/
# sec_top/sec_bot in the isolation stage; L_out0 lives in
# outputRectifier — see stencils.py:2218)
# ---------------------------------------------------------------------------


def _lout_mas(N: int = 14) -> dict:
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
            {"name": "Primary", "numberTurns": N, "numberParallels": 1,
             "isolationSide": "primary"},
        ]},
    }


def _t1_mas(*, N_pri: int = 8, N_sec: int = 4) -> dict:
    """Push-pull T1: 4 windings — pri_top, pri_bot, sec_top, sec_bot.

    Symmetric center-tapped construction is the defining structural
    property of push-pull, so ``numberTurns`` is identical for the two
    primary halves and identical for the two secondary halves.
    """
    return {
        "core": {
            "processedDescription": {
                "effectiveParameters": {
                    "effectiveArea": 1.5e-4,
                    "effectiveLength": 0.05,
                    "effectiveVolume": 7.5e-6,
                },
            },
            "functionalDescription": {
                "material": {
                    "saturation": [
                        {"magneticField": 393.0, "magneticFluxDensity": 0.32,
                         "temperature": 100.0},
                    ],
                },
            },
        },
        "coil": {"functionalDescription": [
            {"name": "pri_top", "numberTurns": N_pri, "numberParallels": 1,
             "isolationSide": "primary"},
            {"name": "pri_bot", "numberTurns": N_pri, "numberParallels": 1,
             "isolationSide": "primary"},
            {"name": "sec_top", "numberTurns": N_sec, "numberParallels": 1,
             "isolationSide": "secondary"},
            {"name": "sec_bot", "numberTurns": N_sec, "numberParallels": 1,
             "isolationSide": "secondary"},
        ]},
    }


def _pp_tas(*, t1_kwargs: dict | None = None) -> dict:
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
                "name": "output_0",
                "role": "outputRectifier",
                "circuit": {"components": [
                    {"name": "D1",    "data": "placeholder"},
                    {"name": "D2",    "data": "placeholder"},
                    {"name": "L_out0", "category": "magnetic",
                     "mas": _lout_mas()},
                    {"name": "C_out0", "data": "placeholder"},
                ]},
            },
        ],
        "interStageCircuit": [],
    }}


def _pp_spec() -> dict:
    """Vin 36–60 V, Vout 12 V, Iout 5 A, fsw 100 kHz, L_out 4.7 µH.

    With N_pri/N_sec = 8/4 = 2 ⇒ D_eff_max = 12·2/36 = 0.667
    (per-switch D_q_max = 0.333, safely below 0.5).
    """
    return {
        "inputVoltage": {"minimum": 36.0, "maximum": 60.0, "nominal": 48.0},
        "desiredInductance": 4.7e-6,
        "efficiency": 0.92,
        "operatingPoints": [{
            "outputVoltages": [12.0],
            "outputCurrents": [5.0],
            "switchingFrequency": 100_000.0,
            "ambientTemperature": 25,
        }],
    }


def _get_lout(tas: dict) -> dict:
    for stage in tas["topology"]["stages"]:
        if stage.get("role") == "outputRectifier":
            for c in stage["circuit"]["components"]:
                if c.get("name") == "L_out0":
                    return c
    raise AssertionError("L_out0 not found in any outputRectifier stage")


# ---------------------------------------------------------------------------
# Math (D_eff form: identical to forward family but at 2·fsw)
# ---------------------------------------------------------------------------


class TestPushPullMath:

    def test_duty_uses_effective_form(self):
        """D_eff_max = Vout·n / Vin_min, D_eff_min = Vout·n / Vin_max."""
        out = enrich_tas_for_realism(
            _pp_tas(), topology="push_pull", spec=_pp_spec(),
        )
        # n = 8/4 = 2 ⇒ D_eff_max = 12·2/36 = 0.6667, D_eff_min = 12·2/60 = 0.4
        assert out["duty_max"] == pytest.approx(12.0 * 2.0 / 36.0, rel=1e-5)
        assert out["duty_min"] == pytest.approx(12.0 * 2.0 / 60.0, rel=1e-5)
        assert out["duty"] == out["duty_max"]

    def test_per_switch_duty_is_half_of_effective(self):
        out = enrich_tas_for_realism(
            _pp_tas(), topology="push_pull", spec=_pp_spec(),
        )
        l = _get_lout(out)
        # D_q_max = D_eff_max / 2 = 0.3333
        assert l["ipeak_provenance"]["d_per_switch_max"] == pytest.approx(
            out["duty_max"] / 2.0, abs=1e-5
        )
        assert l["ipeak_provenance"]["d_per_switch_max"] < 0.5

    def test_ripple_uses_2x_fsw_and_dmin_buck_shape(self):
        """ΔI_L = Vout·(1 − D_eff_min) / (L·0.8 · 2·fsw).

        This is the central regression: forget the 2× and the ripple
        doubles, silently inflating reported Ipeak by a factor of two.
        """
        out = enrich_tas_for_realism(
            _pp_tas(), topology="push_pull", spec=_pp_spec(),
        )
        l = _get_lout(out)
        L_worst = 0.8 * 4.7e-6
        d_eff_min = 12.0 * 2.0 / 60.0
        fsw_eff = 2.0 * 100_000.0
        expected = 12.0 * (1.0 - d_eff_min) / (L_worst * fsw_eff)
        assert l["ipeak_provenance"]["ripple_worst_A_pp"] == pytest.approx(
            expected, rel=1e-6
        )
        # Provenance must explicitly record the doubled output frequency.
        assert l["ipeak_provenance"]["fsw_effective_Hz"] == pytest.approx(
            2.0 * 100_000.0
        )
        assert l["ipeak_provenance"]["fsw_per_switch_Hz"] == pytest.approx(
            100_000.0
        )

    def test_ipeak_is_iout_plus_half_ripple(self):
        out = enrich_tas_for_realism(
            _pp_tas(), topology="push_pull", spec=_pp_spec(),
        )
        l = _get_lout(out)
        ripple = l["ipeak_provenance"]["ripple_worst_A_pp"]
        assert l["ipeak_worst"] == pytest.approx(5.0 + ripple / 2.0, rel=1e-6)

    def test_isat_uses_lout_mas_not_t1(self):
        """B_sat·N·A_e / L_out using L_out0's own MAS, not T1's."""
        out = enrich_tas_for_realism(
            _pp_tas(), topology="push_pull", spec=_pp_spec(),
        )
        l = _get_lout(out)
        expected = 0.4 * 14 * 8.0327e-5 / 4.7e-6
        assert l["isat"] == pytest.approx(expected, rel=1e-4)
        assert l["isat_provenance"]["effective_area_m2"] == 8.0327e-5
        assert l["isat_provenance"]["b_sat_T"] == pytest.approx(0.4)
        # Provenance must mark this as the push_pull variant so a
        # future refactor can't silently re-use the forward wrapper.
        assert "push_pull" in l["isat_provenance"]["method"]

    def test_t1_is_not_isat_stamped(self):
        """Alternating-polarity drive resets T1's core every cycle."""
        out = enrich_tas_for_realism(
            _pp_tas(), topology="push_pull", spec=_pp_spec(),
        )
        for stage in out["topology"]["stages"]:
            if stage.get("role") == "isolation":
                t1 = stage["circuit"]["components"][0]
                assert "isat" not in t1
                assert "ipeak_worst" not in t1
                return
        raise AssertionError("isolation stage missing")

    def test_turns_ratio_recorded_in_provenance(self):
        # n = N_pri/N_sec = 4/2 = 2 ⇒ D_eff_max = 12·2/36 = 0.667 (safe).
        out = enrich_tas_for_realism(
            _pp_tas(t1_kwargs={"N_pri": 4, "N_sec": 2}),
            topology="push_pull",
            spec=_pp_spec(),
        )
        l = _get_lout(out)
        assert l["ipeak_provenance"][
            "turns_ratio_n_pri_top_over_n_sec_top"
        ] == pytest.approx(2.0, rel=1e-6)
        assert l["ipeak_provenance"]["n_primary_half"] == 4
        assert l["ipeak_provenance"]["n_secondary_half"] == 2

    def test_end_to_end_realism_passes(self):
        spec = _pp_spec()
        enriched = enrich_tas_for_realism(
            _pp_tas(), topology="push_pull", spec=spec,
        )
        r = evaluate_tas(enriched, topology="push_pull", spec=spec)
        assert r.verdict is RealismVerdict.PASS
        passed = {c.name for c in r.checks if c.status is CheckStatus.PASS}
        assert {"duty_cycle_bounds", "inductor_isat_margin"}.issubset(passed)


# ---------------------------------------------------------------------------
# Overlap throw: D_eff ≥ 1.0 means per-switch D_q ≥ 0.5, i.e. both Q1
# and Q2 simultaneously ON shorts the transformer (volt-second
# imbalance, infinite primary current).  Hard physical limit; throw.
# ---------------------------------------------------------------------------


class TestOverlapGuard:

    def test_d_eff_above_one_throws(self):
        """n=4 ⇒ D_eff_max = 12·4/36 = 1.333 — must throw."""
        with pytest.raises(EnrichmentError, match=r"D_eff_max"):
            enrich_tas_for_realism(
                _pp_tas(t1_kwargs={"N_pri": 8, "N_sec": 2}),
                topology="push_pull",
                spec=_pp_spec(),
            )

    def test_d_eff_at_one_throws(self):
        """Exactly 1.0 (per-switch D_q = 0.5) is the hard limit."""
        # n=3, Vin_min=36 ⇒ D_eff_max = 12·3/36 = 1.0
        with pytest.raises(EnrichmentError, match=r"shorting the transformer"):
            enrich_tas_for_realism(
                _pp_tas(t1_kwargs={"N_pri": 6, "N_sec": 2}),
                topology="push_pull",
                spec=_pp_spec(),
            )

    def test_just_below_overlap_does_not_throw(self):
        """n that puts D_eff_max just under 1.0 must succeed cleanly.

        Realism gate's 0.95 CCM ceiling may still fail-close, but the
        extractor itself only throws on the hard 1.0 overlap line.
        """
        # n=2.9: not integer-realisable, use n=2 ⇒ D_eff_max = 0.667 (safe)
        out = enrich_tas_for_realism(
            _pp_tas(t1_kwargs={"N_pri": 8, "N_sec": 4}),
            topology="push_pull",
            spec=_pp_spec(),
        )
        assert out["duty_max"] < 1.0


# ---------------------------------------------------------------------------
# Structural failures — every missing piece of input data must throw.
# ---------------------------------------------------------------------------


class TestStructuralFailures:

    def test_missing_isolation_stage_throws(self):
        tas = _pp_tas()
        tas["topology"]["stages"] = [
            s for s in tas["topology"]["stages"] if s.get("role") != "isolation"
        ]
        with pytest.raises(EnrichmentError, match="isolation"):
            enrich_tas_for_realism(tas, topology="push_pull", spec=_pp_spec())

    def test_missing_outputRectifier_stage_throws(self):
        tas = _pp_tas()
        tas["topology"]["stages"] = [
            s for s in tas["topology"]["stages"]
            if s.get("role") != "outputRectifier"
        ]
        with pytest.raises(EnrichmentError, match="outputRectifier"):
            enrich_tas_for_realism(tas, topology="push_pull", spec=_pp_spec())

    def test_missing_pri_top_winding_throws(self):
        tas = _pp_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "isolation":
                stage["circuit"]["components"][0]["mas"]["coil"][
                    "functionalDescription"][0]["name"] = "primary_upper"
        with pytest.raises(EnrichmentError, match="'pri_top'"):
            enrich_tas_for_realism(tas, topology="push_pull", spec=_pp_spec())

    def test_missing_sec_top_winding_throws(self):
        tas = _pp_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "isolation":
                stage["circuit"]["components"][0]["mas"]["coil"][
                    "functionalDescription"][2]["name"] = "secondary_upper"
        with pytest.raises(EnrichmentError, match="'sec_top'"):
            enrich_tas_for_realism(tas, topology="push_pull", spec=_pp_spec())

    def test_missing_desiredInductance_throws(self):
        spec = _pp_spec()
        del spec["desiredInductance"]
        with pytest.raises(EnrichmentError, match="desiredInductance"):
            enrich_tas_for_realism(_pp_tas(), topology="push_pull", spec=spec)

    def test_missing_lout_mas_throws(self):
        tas = _pp_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "outputRectifier":
                for c in stage["circuit"]["components"]:
                    if c.get("name") == "L_out0":
                        del c["mas"]
        with pytest.raises(EnrichmentError):
            enrich_tas_for_realism(tas, topology="push_pull", spec=_pp_spec())
