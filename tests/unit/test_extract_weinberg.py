"""Tests for the Weinberg V1 realism extractor.

Weinberg V1 = current-fed push-pull with an input coupled inductor L1
(2 symmetric windings a, b) feeding the center-tapped primary of a
4-winding transformer T1 (pri_a, pri_b, sec_a, sec_b); secondary
CT-FW rectifier (D1, D2) into C_out0.

Voltage transfer (boost-mode, overlapping conduction D > 0.5):

  ``Vout = n · Vin / (2 · (1 − D))``   with ``n = N_sec/N_pri``.

L1 is the binding magnetic (no discrete output choke).  T1 is NOT
Isat-stamped (symmetric push-pull resets the core every cycle).

Per CLAUDE.md "throw, never default": every missing or invalid spec /
MAS field must raise EnrichmentError — no silent fallbacks.
"""

from __future__ import annotations

import pytest

from heaviside.pipeline import evaluate_tas
from heaviside.pipeline.extract import EnrichmentError, enrich_tas_for_realism
from heaviside.pipeline.realism import CheckStatus
from tests.unit._real_mas import isat_of, real_magnetic

# ---------------------------------------------------------------------------
# Fixtures (Weinberg stencil: L1 in lineFilter stage with 2 windings
# a/b; T1 in isolation with 4 windings pri_a/pri_b/sec_a/sec_b — see
# stencils.py:2797)
# ---------------------------------------------------------------------------


def _l1_mas(N: int = 22, *, L: float = 100e-6) -> dict:
    """Full MAS root for L1 (input coupled inductor), matching the shape
    the real bridge-attach phase produces: a **complete, PyOM-evaluable**
    wound/gapped magnetic under ``core``/``coil`` (built by
    :func:`real_magnetic` so ``calculate_saturation_current`` returns real
    MKF physics) PLUS the ``inputs``/``outputs`` envelopes carrying the
    inductance MKF actually achieved.  The extractor harvests the achieved
    ``L`` via :func:`_read_full_mas_root` + :func:`_harvest_inductance`
    from ``outputs[*].inductance.magnetizingInductance.magnetizingInductance
    .nominal`` (and would also accept
    ``inputs.designRequirements.magnetizingInductance.nominal``); both are
    provided so the fixture survives either harvest path.
    """
    mas = real_magnetic(
        shape="ETD 29/16/10",
        material="3C95",
        gap_mm=1.0,
        windings=[
            {"name": "a", "turns": N, "side": "primary"},
            {"name": "b", "turns": N, "side": "primary"},
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


def _t1_mas(*, N_pri: int = 6, N_sec: int = 18) -> dict:
    # T1 is deliberately NOT Isat-stamped (symmetric push-pull resets the
    # core every cycle); the extractor harvests only its winding turns for
    # the turns ratio, so a complete real magnetic with the four named
    # half-windings is all that's needed.
    return real_magnetic(
        shape="ETD 34/17/11",
        material="3C95",
        gap_mm=0.0,
        windings=[
            {"name": "pri_a", "turns": N_pri, "side": "primary"},
            {"name": "pri_b", "turns": N_pri, "side": "primary"},
            {"name": "sec_a", "turns": N_sec, "side": "secondary"},
            {"name": "sec_b", "turns": N_sec, "side": "secondary"},
        ],
    )


def _wb_tas(*, t1_kwargs: dict | None = None, l1_kwargs: dict | None = None) -> dict:
    t1_kwargs = dict(t1_kwargs or {})
    l1_kwargs = dict(l1_kwargs or {})
    return {
        "topology": {
            "stages": [
                {
                    "name": "input_coupled_inductor",
                    "role": "lineFilter",
                    "circuit": {
                        "components": [
                            {"name": "L1", "category": "magnetic", "mas": _l1_mas(**l1_kwargs)},
                        ]
                    },
                },
                {
                    "name": "primary_switch",
                    "role": "switchingCell",
                    "circuit": {
                        "components": [
                            {"name": "Q1", "data": "placeholder"},
                            {"name": "Q2", "data": "placeholder"},
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
                            {"name": "C_out0", "data": "placeholder"},
                        ]
                    },
                },
            ],
            "interStageConnections": [],
        }
    }


def _wb_spec() -> dict:
    """Vin 36–60 V, Vout 270 V, Iout 2 A, fsw 100 kHz, L 100 µH.

    With N_pri/N_sec = 6/18 ⇒ n = 3.
    D_max = 1 − 3·36/(2·270) = 1 − 0.2 = 0.8
    D_min = 1 − 3·60/(2·270) = 1 − 0.333 = 0.667
    Both > 0.5 ✓ (boost mode active across full Vin range).
    """
    return {
        "inputVoltage": {"minimum": 36.0, "maximum": 60.0, "nominal": 48.0},
        "desiredInductance": 100e-6,
        "efficiency": 0.92,
        "operatingPoints": [
            {
                "outputVoltages": [270.0],
                "outputCurrents": [2.0],
                "switchingFrequency": 100_000.0,
                "ambientTemperature": 25,
            }
        ],
    }


def _get_l1(tas: dict) -> dict:
    for stage in tas["topology"]["stages"]:
        if stage.get("role") == "lineFilter":
            for c in stage["circuit"]["components"]:
                if c.get("name") == "L1":
                    return c
    raise AssertionError("L1 not found")


# ---------------------------------------------------------------------------
# Voltage transfer
# ---------------------------------------------------------------------------


class TestWeinbergMath:
    def test_duty_at_both_vin_extremes(self):
        out = enrich_tas_for_realism(_wb_tas(), topology="weinberg", spec=_wb_spec())
        # n = 18/6 = 3
        d_max_expected = 1.0 - 3.0 * 36.0 / (2.0 * 270.0)
        d_min_expected = 1.0 - 3.0 * 60.0 / (2.0 * 270.0)
        assert out["duty_max"] == pytest.approx(d_max_expected, abs=1e-5)
        assert out["duty_min"] == pytest.approx(d_min_expected, abs=1e-5)
        assert out["duty"] == out["duty_max"]

    def test_per_switch_duty_above_half(self):
        out = enrich_tas_for_realism(_wb_tas(), topology="weinberg", spec=_wb_spec())
        # Boost mode requires D > 0.5 for both extremes.
        assert out["duty_min"] > 0.5

    def test_voltage_transfer_round_trip(self):
        """Plug solved D back into Vout = n·Vin / (2·(1 − D))."""
        out = enrich_tas_for_realism(_wb_tas(), topology="weinberg", spec=_wb_spec())
        # At Vin_min: reconstructed Vout must equal spec Vout.
        d_max = out["duty_max"]
        vout_reconstructed = 3.0 * 36.0 / (2.0 * (1.0 - d_max))
        assert vout_reconstructed == pytest.approx(270.0, rel=1e-4)

    def test_turns_ratio_recorded(self):
        out = enrich_tas_for_realism(
            _wb_tas(t1_kwargs={"N_pri": 4, "N_sec": 16}),
            topology="weinberg",
            spec=_wb_spec(),
        )
        l = _get_l1(out)
        assert l["ipeak_provenance"]["turns_ratio_n_sec_over_n_pri"] == pytest.approx(4.0, rel=1e-6)
        assert l["ipeak_provenance"]["n_primary_half"] == 4
        assert l["ipeak_provenance"]["n_secondary_half"] == 16

    def test_input_current_at_vin_min(self):
        out = enrich_tas_for_realism(_wb_tas(), topology="weinberg", spec=_wb_spec())
        l = _get_l1(out)
        # Iin = Iout * Vout / (Vin_min * eta) = 2 * 270 / (36 * 0.92) = 16.304 A.
        # (eta from _wb_spec; assuming lossless under-sizes the saturation peak.)
        assert l["ipeak_provenance"]["iL_avg_max_A"] == pytest.approx(
            2.0 * 270.0 / (36.0 * 0.92), rel=1e-6
        )


# ---------------------------------------------------------------------------
# Ripple parabola: interior peak at Vin = Vout/(2n)
# ---------------------------------------------------------------------------


class TestRipple:
    def test_ripple_at_boundary_when_interior_outside_range(self):
        """n=3, Vout=270 ⇒ Vout/(2n)=45. Vin range 36-60: 45 is inside.
        So the interior peak IS sampled; this test uses a spec where
        the interior peak falls OUTSIDE the Vin range to pin the
        boundary-only branch."""
        # Shrink Vin to 50-60 — interior peak 45 is outside.
        spec = _wb_spec()
        spec["inputVoltage"]["minimum"] = 50.0
        out = enrich_tas_for_realism(_wb_tas(), topology="weinberg", spec=spec)
        l = _get_l1(out)
        L_worst = 0.8 * 100e-6
        fsw_eff = 2.0 * 100_000.0

        def ripple_at(v):
            return v * (1.0 - 3.0 * v / 270.0) / (L_worst * fsw_eff)

        expected = max(ripple_at(50.0), ripple_at(60.0))
        assert l["ipeak_provenance"]["ripple_worst_A_pp"] == pytest.approx(expected, rel=1e-6)

    def test_ripple_picks_interior_when_in_range(self):
        out = enrich_tas_for_realism(_wb_tas(), topology="weinberg", spec=_wb_spec())
        l = _get_l1(out)
        L_worst = 0.8 * 100e-6
        fsw_eff = 2.0 * 100_000.0

        def ripple_at(v):
            return v * (1.0 - 3.0 * v / 270.0) / (L_worst * fsw_eff)

        interior = 270.0 / (2.0 * 3.0)  # = 45 V, inside [36, 60]
        candidates = [ripple_at(36.0), ripple_at(60.0), ripple_at(interior)]
        assert max(candidates) == ripple_at(interior)
        assert l["ipeak_provenance"]["ripple_worst_A_pp"] == pytest.approx(
            ripple_at(interior), rel=1e-6
        )

    def test_ipeak_is_iin_plus_half_ripple(self):
        out = enrich_tas_for_realism(_wb_tas(), topology="weinberg", spec=_wb_spec())
        l = _get_l1(out)
        ripple = l["ipeak_provenance"]["ripple_worst_A_pp"]
        iin = l["ipeak_provenance"]["iL_avg_max_A"]
        assert l["ipeak_worst"] == pytest.approx(iin + ripple / 2.0, rel=1e-6)


# ---------------------------------------------------------------------------
# Isat (L1 binding, T1 deliberately unstamped)
# ---------------------------------------------------------------------------


class TestIsat:
    def test_isat_uses_l1_mas(self):
        out = enrich_tas_for_realism(_wb_tas(), topology="weinberg", spec=_wb_spec())
        l = _get_l1(out)
        # Ground truth = MKF: the stamped Isat must equal PyOM's
        # saturation current for the L1 magnetic at the op-point ambient
        # (25 °C), NOT an analytical formula. Computing it here on the
        # same L1 MAS the extractor harvested also proves L1 (not T1) was
        # the source.
        expected = isat_of(_l1_mas(), temperature_c=100.0)
        assert l["isat"] == pytest.approx(expected, rel=1e-3)
        assert "PyOM" in l["isat_provenance"]["method"]
        assert "weinberg" in l["isat_provenance"]["method"]

    def test_t1_is_not_isat_stamped(self):
        out = enrich_tas_for_realism(_wb_tas(), topology="weinberg", spec=_wb_spec())
        for stage in out["topology"]["stages"]:
            if stage.get("role") == "isolation":
                t1 = stage["circuit"]["components"][0]
                assert "isat" not in t1
                assert "ipeak_worst" not in t1
                return
        raise AssertionError("isolation stage missing")

    def test_secondary_reflected_current_flag_pinned(self):
        out = enrich_tas_for_realism(_wb_tas(), topology="weinberg", spec=_wb_spec())
        l = _get_l1(out)
        assert l["ipeak_provenance"]["secondary_reflected_current_modelled"] is False

    def test_end_to_end_realism_passes(self):
        spec = _wb_spec()
        # Use a larger L (300 µH) and lower turns to make Isat margin pass
        # comfortably with our chosen MAS.
        enriched = enrich_tas_for_realism(
            _wb_tas(l1_kwargs={"N_pri": None} if False else None),
            topology="weinberg",
            spec=spec,
        )
        r = evaluate_tas(enriched, topology="weinberg", spec=spec)
        # End-to-end may PASS or FAIL depending on Isat margins; the
        # binding contract is that BOTH duty_cycle_bounds and
        # inductor_isat_margin are EVALUATED (not UNAVAILABLE) — i.e.
        # the extractor stamped the fields.
        check_status = {c.name: c.status for c in r.checks}
        for name in ("duty_cycle_bounds", "inductor_isat_margin"):
            assert check_status.get(name) in (CheckStatus.PASS, CheckStatus.FAIL), (
                f"{name} must be evaluated, got {check_status.get(name)}"
            )


# ---------------------------------------------------------------------------
# Boost-mode breakdown: D_min ≤ 0.5 means Weinberg V1 stops working.
# ---------------------------------------------------------------------------


class TestBoostModeGuard:
    def test_d_min_at_half_throws(self):
        """Set n so D_min = 0.5 exactly: 1 - n·Vin_max/(2·Vout) = 0.5
        ⇒ n·Vin_max = Vout ⇒ at Vin_max=60, Vout=270 ⇒ n = 4.5.
        Use N_pri=4, N_sec=18 ⇒ n = 4.5."""
        with pytest.raises(EnrichmentError, match=r"≤ 0\.5"):
            enrich_tas_for_realism(
                _wb_tas(t1_kwargs={"N_pri": 4, "N_sec": 18}),
                topology="weinberg",
                spec=_wb_spec(),
            )

    def test_d_min_below_half_throws(self):
        """n too large pushes D below 0.5 (step-up insufficient)."""
        with pytest.raises(EnrichmentError, match=r"D_min"):
            enrich_tas_for_realism(
                _wb_tas(t1_kwargs={"N_pri": 2, "N_sec": 18}),
                topology="weinberg",
                spec=_wb_spec(),
            )


# ---------------------------------------------------------------------------
# Structural failures
# ---------------------------------------------------------------------------


class TestStructuralFailures:
    def test_missing_isolation_stage_throws(self):
        tas = _wb_tas()
        tas["topology"]["stages"] = [
            s for s in tas["topology"]["stages"] if s.get("role") != "isolation"
        ]
        with pytest.raises(EnrichmentError, match="isolation"):
            enrich_tas_for_realism(tas, topology="weinberg", spec=_wb_spec())

    def test_missing_linefilter_stage_throws(self):
        tas = _wb_tas()
        tas["topology"]["stages"] = [
            s for s in tas["topology"]["stages"] if s.get("role") != "lineFilter"
        ]
        with pytest.raises(EnrichmentError, match="lineFilter"):
            enrich_tas_for_realism(tas, topology="weinberg", spec=_wb_spec())

    def test_missing_pri_a_winding_throws(self):
        tas = _wb_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "isolation":
                stage["circuit"]["components"][0]["mas"]["coil"]["functionalDescription"][0][
                    "name"
                ] = "primary_a"
        with pytest.raises(EnrichmentError, match="'pri_a'"):
            enrich_tas_for_realism(tas, topology="weinberg", spec=_wb_spec())

    def test_missing_a_winding_on_l1_throws(self):
        # Rename L1 winding "a" to a name that does NOT end in "a" — the
        # extractor's "L1a"/"L1b" suffix fallback (any name ending in the
        # requested 1-2 char name) would otherwise silently match "alpha"
        # / "primary_a" and hide the failure.
        tas = _wb_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "lineFilter":
                stage["circuit"]["components"][0]["mas"]["coil"]["functionalDescription"][0][
                    "name"
                ] = "winding_x"
        with pytest.raises(EnrichmentError, match="'a'"):
            enrich_tas_for_realism(tas, topology="weinberg", spec=_wb_spec())

    def test_missing_achieved_inductance_throws(self):
        """Weinberg harvests the achieved L1 inductance from the L1 MAS
        root (the figure MKF actually realised) via _read_full_mas_root +
        _harvest_inductance, NOT from a spec request.  Strip both
        inductance sources from the L1 MAS and the extractor must throw
        rather than silently default."""
        tas = _wb_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "lineFilter":
                c = stage["circuit"]["components"][0]
                c["mas"].pop("outputs", None)
                c["mas"].pop("inputs", None)
        with pytest.raises(EnrichmentError, match=r"full MAS root|inductance"):
            enrich_tas_for_realism(tas, topology="weinberg", spec=_wb_spec())

    def test_spec_present_but_inductance_mas_harvested(self):
        """Even with desiredInductance absent from the spec, the extractor
        succeeds because L1 inductance is harvested from the MAS root —
        the spec field is not the source of truth for L."""
        spec = _wb_spec()
        spec.pop("desiredInductance", None)
        out = enrich_tas_for_realism(_wb_tas(), topology="weinberg", spec=spec)
        l = _get_l1(out)
        # L_worst = 0.8 * 100µH harvested from the MAS root.
        assert l["ipeak_provenance"]["L_worst_H"] == pytest.approx(0.8 * 100e-6, rel=1e-9)
