"""Tests for the dual active bridge (DAB) realism extractor.

DAB = two full bridges (Q1..Q4 primary, Q5..Q8 secondary synchronous
rectifier) coupled through a series resonant/leakage inductor L_r and
a transformer T1 (pri, sec0).  Both bridges run at 50 % complementary
duty; single-phase-shift (SPS) modulation regulates power via the
phase-shift angle between the two square waves.  Stencil at
stencils.py:3179.

Key invariants:

  * **Duty = 0.5** per switch (each bridge runs square-wave 50 %);
    operationally interesting handle is the phase shift ``d``, which
    is recorded in ``ipeak_provenance.phase_shift_d_normalised``.
  * **L_r binds inductor_isat_margin** (T1 not Isat-stamped — both
    bridges symmetric ⇒ bipolar primary current, zero net DC).
  * **SPS power transfer**: ``P = (n·V1·V2·d·(1−|d|)) / (2·fsw·L_r)``;
    solved for ``d`` per Vin extreme via the smaller quadratic root.
    Throws if the required load exceeds ``P_max = n·Vin/(8·fsw·L_r)``
    at Vin_min (k ≥ 0.25 collapses the discriminant).
  * **Peak tank current**:
    ``I_pk = (Vin + n·Vout) · d / (4·fsw·L_r_worst)`` at Vin_min
    (worst case for d, with PROTEUS −20 % on L_r).

Per CLAUDE.md: every missing or invalid field raises EnrichmentError.
"""

from __future__ import annotations

import math

import pytest

from heaviside.pipeline import evaluate_tas
from heaviside.pipeline.extract import EnrichmentError, enrich_tas_for_realism
from heaviside.pipeline.realism import CheckStatus
from tests.unit._real_mas import isat_of, real_magnetic

# ---------------------------------------------------------------------------
# Fixtures matching stencil roles: inverter / isolation / outputRectifier /
# outputFilter.  In isolation: L_r appears BEFORE T1 (matches stencil order).
#
# All magnetics are now COMPLETE, PyOM-evaluable devices built by
# :func:`real_magnetic` (real core shape + material + gap + winding list,
# completed by ``magnetic_autocomplete``) so the extractor's Isat
# enrichment — which delegates entirely to PyOM's
# ``calculate_saturation_current`` and raises on rejection — gets genuine
# gap-aware MKF physics rather than synthetic minimal-MAS PyOM rejects.
# ---------------------------------------------------------------------------


def _lr_mas(N: int = 8, *, inductance: float = 20e-6) -> dict:
    """Full MAS root for the series commutation inductor L_r.

    A complete, PyOM-evaluable gapped magnetic (built by
    :func:`real_magnetic` so ``calculate_saturation_current`` returns real
    gap-aware MKF physics — L_r binds inductor_isat_margin) PLUS the
    simulation-derived ``outputs`` envelope carrying the inductance MKF
    actually achieved.  The DAB extractor harvests L from this MAS root
    (``_harvest_inductance``) — it does NOT read ``spec.desiredInductance``
    for the magnetic — so the fixture must supply it here.
    """
    mas = real_magnetic(
        shape="ETD 29/16/10",
        material="3C95",
        gap_mm=1.0,
        windings=[{"name": "winding", "turns": N, "side": "primary"}],
    )
    # Full-root envelopes MKF returns from the bridge-attach phase.
    mas["inputs"] = {
        "designRequirements": {
            "magnetizingInductance": {"nominal": inductance},
        }
    }
    mas["outputs"] = [
        {
            "inductance": {
                "magnetizingInductance": {
                    "magnetizingInductance": {"nominal": inductance},
                }
            },
        }
    ]
    return mas


def _t1_mas(*, N_pri: int = 4, N_sec0: int = 1) -> dict:
    """2-winding step-down transformer.  DAB convention:
    ``n = N_pri / N_sec0`` (reflected V2 on primary side = n · Vout).
    Deliberately NOT Isat-stamped; the extractor harvests only its winding
    turns for the turns ratio."""
    return real_magnetic(
        shape="ETD 34/17/11",
        material="3C95",
        gap_mm=0.0,
        windings=[
            {"name": "pri", "turns": N_pri, "side": "primary"},
            {"name": "sec0", "turns": N_sec0, "side": "secondary"},
        ],
    )


def _dab_tas(*, t1_kwargs: dict | None = None, lr_kwargs: dict | None = None) -> dict:
    """Stencil-matching DAB TAS: L_r FIRST in isolation stage so the
    realism gate's first-magnetic dispatch lands on it."""
    t1_kwargs = dict(t1_kwargs or {})
    lr_kwargs = dict(lr_kwargs or {})
    return {
        "topology": {
            "stages": [
                {
                    "name": "primary_bridge",
                    "role": "inverter",
                    "circuit": {
                        "components": [
                            {"name": "Q1", "data": "p"},
                            {"name": "Q2", "data": "p"},
                            {"name": "Q3", "data": "p"},
                            {"name": "Q4", "data": "p"},
                        ]
                    },
                },
                {
                    "name": "isolation",
                    "role": "isolation",
                    "circuit": {
                        "components": [
                            {"name": "L_r", "category": "magnetic", "mas": _lr_mas(**lr_kwargs)},
                            {"name": "T1", "category": "magnetic", "mas": _t1_mas(**t1_kwargs)},
                        ]
                    },
                },
                {
                    "name": "secondary_bridge",
                    "role": "outputRectifier",
                    "circuit": {
                        "components": [
                            {"name": "Q5", "data": "p"},
                            {"name": "Q6", "data": "p"},
                            {"name": "Q7", "data": "p"},
                            {"name": "Q8", "data": "p"},
                        ]
                    },
                },
                {
                    "name": "output_filter",
                    "role": "outputFilter",
                    "circuit": {
                        "components": [
                            {"name": "C_out0", "data": "p"},
                        ]
                    },
                },
            ],
            "interStageConnections": [],
        }
    }


def _dab_spec() -> dict:
    """400 V → 48 V / 5 A DAB, fsw 100 kHz, L_r 20 µH.

    n = 4/1 = 4.  n·Vin = 1600 at nominal.
    k = 2·100e3·20e-6·5 / (4·400) = 0.025  → d ≈ 0.0252 (well inside).
    P_max = 4·400/(8·100e3·20e-6) = 100 W.  Spec asks 240 W ... wait.

    Recompute: P_required = 48·5 = 240 W.
    P_max(Vin=400) = n·Vin/(8·fsw·L_r) · Vout = 4·400·48/(8·100e3·20e-6)
                   = 76800 / 16 = 4800 W.  So Iout_max = 100 A. OK.
    k = 2·fsw·L_r·Iout/(n·Vin) = 0.025 at Vin=400. d ≈ 0.025.
    """
    return {
        "inputVoltage": {"minimum": 360.0, "maximum": 440.0, "nominal": 400.0},
        "desiredInductance": 20e-6,
        "efficiency": 0.96,
        "operatingPoints": [
            {
                "outputVoltages": [48.0],
                "outputCurrents": [5.0],
                "switchingFrequency": 100_000.0,
                "ambientTemperature": 25,
            }
        ],
    }


def _get_lr(tas: dict) -> dict:
    for stage in tas["topology"]["stages"]:
        if stage.get("role") == "isolation":
            for c in stage["circuit"]["components"]:
                if c.get("name") == "L_r":
                    return c
    raise AssertionError("L_r not found")


def _get_t1(tas: dict) -> dict:
    for stage in tas["topology"]["stages"]:
        if stage.get("role") == "isolation":
            for c in stage["circuit"]["components"]:
                if c.get("name") == "T1":
                    return c
    raise AssertionError("T1 not found")


# ---------------------------------------------------------------------------
# Duty + phase-shift solve
# ---------------------------------------------------------------------------


class TestDutyAndPhaseShift:
    def test_per_switch_duty_is_50pct(self):
        out = enrich_tas_for_realism(_dab_tas(), topology="dab", spec=_dab_spec())
        assert out["duty"] == 0.5
        assert out["duty_min"] == 0.5
        assert out["duty_max"] == 0.5

    def test_phase_shift_solved_per_vin_extreme(self):
        out = enrich_tas_for_realism(_dab_tas(), topology="dab", spec=_dab_spec())
        lr = _get_lr(out)
        # k = 2·fsw·L·Iout/(n·Vin); d = (1-sqrt(1-4k))/2
        for vin, key in ((360.0, "phase_shift_d_at_vin_min"), (440.0, "phase_shift_d_at_vin_max")):
            k = 2.0 * 1e5 * 20e-6 * 5.0 / (4.0 * vin)
            d_expected = (1.0 - math.sqrt(1.0 - 4.0 * k)) / 2.0
            assert lr["ipeak_provenance"][key] == pytest.approx(d_expected, rel=1e-4)

    def test_worst_case_d_is_at_vin_min(self):
        out = enrich_tas_for_realism(_dab_tas(), topology="dab", spec=_dab_spec())
        lr = _get_lr(out)
        p = lr["ipeak_provenance"]
        assert p["phase_shift_d_normalised"] == p["phase_shift_d_at_vin_min"]
        assert p["phase_shift_d_at_vin_min"] >= p["phase_shift_d_at_vin_max"]


# ---------------------------------------------------------------------------
# Turns ratio + provenance
# ---------------------------------------------------------------------------


class TestTurnsRatio:
    def test_turns_ratio_recorded(self):
        out = enrich_tas_for_realism(
            _dab_tas(t1_kwargs={"N_pri": 6, "N_sec0": 2}),
            topology="dab",
            spec=_dab_spec(),
        )
        lr = _get_lr(out)
        p = lr["ipeak_provenance"]
        assert p["turns_ratio_n_pri_over_n_sec"] == pytest.approx(3.0, rel=1e-6)
        assert p["n_primary"] == 6
        assert p["n_secondary"] == 2

    def test_n_times_vout_recorded(self):
        out = enrich_tas_for_realism(_dab_tas(), topology="dab", spec=_dab_spec())
        lr = _get_lr(out)
        # n = 4, Vout = 48 ⇒ n·Vout = 192
        assert lr["ipeak_provenance"]["n_times_vout_V"] == pytest.approx(192.0, rel=1e-6)


# ---------------------------------------------------------------------------
# Ipeak + Isat
# ---------------------------------------------------------------------------


class TestIpeakAndIsat:
    def test_ipeak_at_vin_min(self):
        out = enrich_tas_for_realism(_dab_tas(), topology="dab", spec=_dab_spec())
        lr = _get_lr(out)
        # I_pk = (Vin + n·Vout) · d / (4·fsw·L_worst) at Vin_min
        L_worst = 0.8 * 20e-6
        k = 2.0 * 1e5 * 20e-6 * 5.0 / (4.0 * 360.0)
        d = (1.0 - math.sqrt(1.0 - 4.0 * k)) / 2.0
        expected = (360.0 + 4.0 * 48.0) * d / (4.0 * 1e5 * L_worst)
        assert lr["ipeak_worst"] == pytest.approx(expected, rel=1e-4)

    def test_isat_uses_lr_mas(self):
        out = enrich_tas_for_realism(_dab_tas(), topology="dab", spec=_dab_spec())
        lr = _get_lr(out)
        # Ground truth = MKF: the stamped Isat must equal PyOM's saturation
        # current for the L_r magnetic at the op-point ambient (25 °C), NOT
        # an analytical formula. Computing it here on the same L_r MAS the
        # extractor harvested also proves L_r (not T1) was the source.
        expected = isat_of(_lr_mas(), temperature_c=25.0)
        assert lr["isat"] == pytest.approx(expected, rel=1e-3)
        assert "PyOM" in lr["isat_provenance"]["method"]
        assert "dual_active_bridge" in lr["isat_provenance"]["method"]

    def test_t1_not_isat_stamped(self):
        out = enrich_tas_for_realism(_dab_tas(), topology="dab", spec=_dab_spec())
        t1 = _get_t1(out)
        assert "isat" not in t1
        assert "ipeak_worst" not in t1
        lr = _get_lr(out)
        assert lr["ipeak_provenance"]["t1_isat_modelled"] is False

    def test_sps_modulation_flag_pinned(self):
        out = enrich_tas_for_realism(_dab_tas(), topology="dab", spec=_dab_spec())
        lr = _get_lr(out)
        assert lr["ipeak_provenance"]["sps_modulation"] is True


# ---------------------------------------------------------------------------
# Power-demand guard: k >= 0.25 throws.
# ---------------------------------------------------------------------------


class TestPowerGuard:
    def test_excessive_load_throws(self):
        """Force k > 0.25 by spiking Iout and shrinking n."""
        spec = _dab_spec()
        spec["operatingPoints"][0]["outputCurrents"] = [50.0]
        with pytest.raises(EnrichmentError, match=r"≥ 0\.25"):
            enrich_tas_for_realism(
                _dab_tas(t1_kwargs={"N_pri": 1, "N_sec0": 4}),
                topology="dab",
                spec=spec,
            )


# ---------------------------------------------------------------------------
# End-to-end realism evaluation
# ---------------------------------------------------------------------------


class TestRealismIntegration:
    def test_end_to_end_realism_evaluates(self):
        spec = _dab_spec()
        enriched = enrich_tas_for_realism(_dab_tas(), topology="dab", spec=spec)
        r = evaluate_tas(enriched, topology="dab", spec=spec)
        check_status = {c.name: c.status for c in r.checks}
        for name in ("duty_cycle_bounds", "inductor_isat_margin"):
            assert check_status.get(name) in (CheckStatus.PASS, CheckStatus.FAIL), (
                f"{name} must be evaluated (PASS/FAIL), got {check_status.get(name)}"
            )

    def test_topology_alias_dual_active_bridge(self):
        """Both 'dab' and 'dual_active_bridge' must dispatch the same."""
        spec = _dab_spec()
        a = enrich_tas_for_realism(_dab_tas(), topology="dab", spec=spec)
        b = enrich_tas_for_realism(_dab_tas(), topology="dual_active_bridge", spec=spec)
        assert a["duty"] == b["duty"]
        assert _get_lr(a)["ipeak_worst"] == _get_lr(b)["ipeak_worst"]


# ---------------------------------------------------------------------------
# Structural failures
# ---------------------------------------------------------------------------


class TestStructuralFailures:
    def test_missing_isolation_stage_throws(self):
        tas = _dab_tas()
        tas["topology"]["stages"] = [
            s for s in tas["topology"]["stages"] if s.get("role") != "isolation"
        ]
        with pytest.raises(EnrichmentError, match="isolation"):
            enrich_tas_for_realism(tas, topology="dab", spec=_dab_spec())

    def test_missing_pri_winding_throws(self):
        tas = _dab_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "isolation":
                for c in stage["circuit"]["components"]:
                    if c.get("name") == "T1":
                        c["mas"]["coil"]["functionalDescription"][0]["name"] = "primary"
        with pytest.raises(EnrichmentError, match="'pri'"):
            enrich_tas_for_realism(tas, topology="dab", spec=_dab_spec())

    def test_missing_sec0_winding_throws(self):
        tas = _dab_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "isolation":
                for c in stage["circuit"]["components"]:
                    if c.get("name") == "T1":
                        c["mas"]["coil"]["functionalDescription"][1]["name"] = "secondary0"
        with pytest.raises(EnrichmentError, match="'sec0'"):
            enrich_tas_for_realism(tas, topology="dab", spec=_dab_spec())

    def test_missing_lr_inductance_throws(self):
        """The DAB extractor harvests L_r from the magnetic's full MAS
        root (achieved inductance), NOT from ``spec.desiredInductance``.
        A MAS root with no usable inductance must therefore throw."""
        tas = _dab_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "isolation":
                for c in stage["circuit"]["components"]:
                    if c.get("name") == "L_r":
                        # Strip both inductance envelopes, keep the device
                        # sub-doc so _read_full_mas_root still resolves.
                        c["mas"]["outputs"] = [{}]
                        del c["mas"]["inputs"]
        with pytest.raises(EnrichmentError, match="inductance"):
            enrich_tas_for_realism(tas, topology="dab", spec=_dab_spec())

    def test_missing_lr_mas_throws(self):
        tas = _dab_tas()
        for stage in tas["topology"]["stages"]:
            if stage.get("role") == "isolation":
                for c in stage["circuit"]["components"]:
                    if c.get("name") == "L_r":
                        del c["mas"]
        with pytest.raises(EnrichmentError, match="MAS"):
            enrich_tas_for_realism(tas, topology="dab", spec=_dab_spec())
