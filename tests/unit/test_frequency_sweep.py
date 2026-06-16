"""frequency_sweep stage (master-plan step B4).

The engine logic (coarse grid → bracket → golden-section → feasible argmin of
worst-OP TOTAL loss) is tested HERMETICALLY by injecting a controlled magnetic
"landscape" in place of MKF, so the optimisation is deterministic and fast and
asserts the master-plan traps directly:

  * trap #1 (L ∝ 1/fsw): the real bridge seam, in a guarded integration test.
  * trap #2 (total = magnetic + switching): dropping the switching term moves
    fsw* — asserted on the injected landscape.
  * trap #7 (no Ipeak_worst computer ⇒ raise, never a silent no-op).

Plus: the real TAS envelope-FET pick, and the analyst worst-OP loss reader.
"""
from __future__ import annotations

import math

import pytest

from heaviside.pipeline import analyst
from heaviside.stages import frequency_sweep as fs


# ---------------------------------------------------------------------------
# analyst worst-OP magnetic loss reader (pure)
# ---------------------------------------------------------------------------


def test_worst_op_loss_maxes_each_bucket_independently():
    comp = {"data": {"outputs": [
        {"coreLosses": {"coreLosses": 1.0},
         "windingLosses": {"windingLosses": [{"totalLosses": 0.5}]}},
        {"coreLosses": {"coreLosses": 3.0},
         "windingLosses": {"windingLosses": [{"totalLosses": 0.2}]}},
    ]}}
    # op0 reader unchanged
    assert analyst._inductor_loss_from_mas(comp) == {"L1_core": 1.0, "L1_dcr": 0.5}
    # worst-OP maxes core and winding independently (3.0 core from op1, 0.5 dcr from op0)
    assert analyst.inductor_loss_worst_op(comp) == {"L1_core": 3.0, "L1_dcr": 0.5}


def test_worst_op_loss_reads_fast_path_scalar_winding():
    """FAST path (calculate_advised_magnetics_fast) reports total winding loss
    as a SCALAR windingLosses.windingLosses (W), with per-component detail in
    windingLossesPerWinding — unlike the slow path's list. The reader must
    handle both, else every fast-path candidate is 'unrankable (no loss)'."""
    fast = {"data": {"outputs": [{
        "coreLosses": {"coreLosses": 0.0049},
        "windingLosses": {
            "windingLosses": 0.766,  # scalar total (fast path)
            "windingLossesPerWinding": [{"name": "Primary",
                "ohmicLosses": {"losses": 0.766}}],
            "dcResistancePerWinding": [0.084],  # OHMS, must NOT be read as loss
        },
    }]}}
    assert analyst.inductor_loss_worst_op(fast) == {"L1_core": 0.0049, "L1_dcr": 0.766}


def test_worst_op_loss_per_winding_component_fallback():
    """When neither scalar nor list total is present, sum the per-winding
    ohmic+skin+proximity components."""
    comp = {"data": {"outputs": [{
        "coreLosses": {"coreLosses": 1.0},
        "windingLosses": {"windingLossesPerWinding": [
            {"ohmicLosses": {"losses": 0.5}, "skinEffectLosses": {"losses": 0.1},
             "proximityEffectLosses": {"losses": 0.2}},
        ]},
    }]}}
    assert analyst.inductor_loss_worst_op(comp)["L1_dcr"] == pytest.approx(0.8)


def test_worst_op_loss_none_when_no_outputs():
    assert analyst.inductor_loss_worst_op({"data": {"outputs": []}}) == {
        "L1_core": None, "L1_dcr": None
    }
    assert analyst.inductor_loss_worst_op({}) == {"L1_core": None, "L1_dcr": None}


# ---------------------------------------------------------------------------
# switching-loss surrogate (pure)
# ---------------------------------------------------------------------------


def test_switching_loss_formula():
    # 0.5 * Vds * Id * (Qg/Ig) * fsw, Ig = analyst._GATE_DRIVE_CURRENT_A
    p = fs._switching_loss_w(48.0, 3.0, 20e-9, 500e3)
    expected = 0.5 * 48.0 * 3.0 * (20e-9 / analyst._GATE_DRIVE_CURRENT_A) * 500e3
    assert p == pytest.approx(expected)
    # monotone increasing in fsw
    assert fs._switching_loss_w(48, 3, 20e-9, 1e6) > fs._switching_loss_w(48, 3, 20e-9, 5e5)


# ---------------------------------------------------------------------------
# Hermetic engine: inject a magnetic landscape in place of MKF
# ---------------------------------------------------------------------------


class _FakeCand:
    """Stand-in for bridge.MagneticDesign with controllable isat / loss."""

    def __init__(self, fsw: float, ind_h: float, isat_a: float, p_mag_w: float, scoring: float):
        self.scoring = scoring
        self.core_shape_name = "FAKE-CORE"
        self.core_material_name = "FAKE-MAT"
        # stash the controllable physics on the mas so the patched helpers read it back
        self.mas = {"_isat": isat_a, "_L": ind_h, "_pmag": p_mag_w, "outputs": []}

    @property
    def magnetic(self):
        return self.mas


def _install_landscape(monkeypatch, *, isat_a: float, pmag_at):
    """Patch the bridge/analyst hooks the sweep calls so a magnetic at frequency
    ``f`` has worst-OP magnetic loss ``pmag_at(f)`` and saturation ``isat_a``.

    ``pmag_at`` lets a test shape the magnetic-loss-vs-fsw curve (smaller core
    at higher fsw ⇒ lower copper but we keep it simple: decreasing in fsw), so
    that with the (increasing) switching surrogate the TOTAL has an interior
    minimum.
    """
    from heaviside import bridge

    IPEAK = 1.0  # fixed worst-case peak so isat margin is a pure function of isat_a

    def fake_design_at_fsw(entry, spec, fsw, *, max_results=5, **kw):
        # three identical-isat candidates with slightly different loss so the
        # "min feasible total" selection has something to choose from
        base = pmag_at(fsw)
        return [
            _FakeCand(fsw, 1e-5, isat_a, base * (1.0 + 0.01 * i), scoring=1.0 - 0.01 * i)
            for i in range(min(3, max_results))
        ]

    def fake_margin_inputs(entry, spec, cand):
        return IPEAK, cand.mas["_L"]

    def fake_isat_from_mas(magnetic, L, **kw):
        return magnetic["_isat"]

    def fake_worst_op(comp):
        mas = comp["data"]
        return {"L1_core": mas["_pmag"], "L1_dcr": 0.0}

    monkeypatch.setattr(bridge, "design_magnetics_at_fsw", fake_design_at_fsw)
    monkeypatch.setattr(bridge, "_isat_margin_inputs", fake_margin_inputs)
    monkeypatch.setattr(bridge, "_isat_from_mas", fake_isat_from_mas)
    monkeypatch.setattr(analyst, "inductor_loss_worst_op", fake_worst_op)
    # a fixed real-ish envelope FET (no TAS dependency in the engine test)
    monkeypatch.setattr(fs, "select_envelope_fet", lambda vds, idd: fs.EnvelopeFet(
        mpn="FAKE-FET", manufacturer="ACME", qg_total_c=20e-9,
        vds_rated_v=100.0, id_continuous_a=10.0, technology="Si",
    ))
    # stress engine: give the sweep a switch class without needing a real deriver
    from heaviside.stages import stress_extract
    from heaviside.pipeline.stress import ComponentStresses

    monkeypatch.setattr(stress_extract, "analytical", lambda topo, spec: ComponentStresses(
        vds_stress=48.0, id_stress=1.0, vr_stress=48.0, if_avg_stress=1.0,
        v_working=3.3, i_ripple=1.0,
    ))
    return IPEAK


def _spec():
    return {
        "inputVoltage": {"minimum": 9, "nominal": 12, "maximum": 16},
        "operatingPoints": [{"outputVoltages": [3.3], "outputCurrents": [3],
                             "switchingFrequency": 5e5, "ambientTemperature": 25}],
        "currentRippleRatio": 0.3,
    }


# magnetic-loss coefficient chosen so a/f (magnetic) and ~f (switching) are
# comparable across [50k,1M] ⇒ analytic optimum ≈ 228 kHz (interior).
_PMAG_A = 2.5e4


def test_sweep_finds_interior_total_loss_minimum(monkeypatch):
    # Magnetic loss falls ~1/fsw; switching loss rises ~fsw ⇒ interior optimum.
    _install_landscape(monkeypatch, isat_a=10.0, pmag_at=lambda f: _PMAG_A / f)

    res = fs.sweep("buck", _spec(), f_lo_hz=50e3, f_hi_hz=1e6,
                   n_coarse=7, golden_iters=8, top_k=3)

    # brute-force the same landscape: total(f) = _PMAG_A/f + Psw(f)
    def total(f):
        return _PMAG_A / f + fs._switching_loss_w(48.0, 1.0, 20e-9, f)
    fine = [50e3 * (1e6 / 50e3) ** (i / 400) for i in range(401)]
    brute = min(fine, key=total)
    # golden-section should land within a few % of the true argmin
    assert res.fsw_star_hz == pytest.approx(brute, rel=0.06)
    assert 50e3 < res.fsw_star_hz < 1e6  # interior, not clamped to an endpoint
    assert res.front and res.best.total_loss_w > 0
    assert res.best.switching_loss_w > 0
    # total really is magnetic + switching
    assert res.best.total_loss_w == pytest.approx(
        res.best.magnetic_loss_w + res.best.switching_loss_w
    )


def test_dropping_switching_term_moves_fsw_higher(monkeypatch):
    """Trap #2: the switching term must matter. With P_sw the optimum is
    interior; with the magnetic loss alone (1/fsw, monotone) the argmin runs to
    the top of the band. So fsw*(with P_sw) must be strictly below fsw_hi."""
    _install_landscape(monkeypatch, isat_a=10.0, pmag_at=lambda f: _PMAG_A / f)
    res = fs.sweep("buck", _spec(), f_lo_hz=50e3, f_hi_hz=1e6, n_coarse=7, golden_iters=8)
    assert res.fsw_star_hz < 1e6 * 0.95  # P_sw pulled the optimum off the ceiling


def test_sweep_raises_when_everything_saturates(monkeypatch):
    """No feasible (magnetic, fsw): isat below the 1.2x margin everywhere ⇒
    FrequencySweepError carrying per-frequency reasons (never a silent clamp)."""
    _install_landscape(monkeypatch, isat_a=0.5, pmag_at=lambda f: 1e11 / f)  # isat 0.5 < 1.2*IPEAK(1.0)
    with pytest.raises(fs.FrequencySweepError) as ei:
        fs.sweep("buck", _spec(), f_lo_hz=50e3, f_hi_hz=1e6, n_coarse=4)
    assert ei.value.reasons  # populated
    assert "isat margin" in str(ei.value)


def test_trap7_unregistered_topology_raises(monkeypatch):
    """A topology with no Ipeak_worst computer must raise, not silently pass the
    saturation gate. push_pull is registered as a topology but not in
    bridge._IPEAK_WORST."""
    with pytest.raises(fs.FrequencySweepError, match="Ipeak_worst computer"):
        fs.sweep("push_pull", _spec())


def test_bad_band_raises():
    with pytest.raises(fs.FrequencySweepError, match="f_lo < f_hi"):
        fs.sweep("buck", _spec(), f_lo_hz=1e6, f_hi_hz=50e3)


# ---------------------------------------------------------------------------
# Real TAS envelope-FET pick (needs the catalogue; 3.11+/3.12 runtime)
# ---------------------------------------------------------------------------


def test_envelope_fet_from_real_tas():
    """Lowest-Qg real MOSFET carrying the class; a real part, never fabricated."""
    pytest.importorskip("heaviside.catalogue.selector")
    fet = fs.select_envelope_fet(48.0, 4.0)
    assert fet.qg_total_c > 0
    assert fet.vds_rated_v >= 48.0
    assert fet.id_continuous_a >= 4.0
    assert fet.mpn  # a concrete MPN


def test_envelope_fet_rejects_nonpositive():
    with pytest.raises(fs.FrequencySweepError):
        fs.select_envelope_fet(0.0, 4.0)
    with pytest.raises(fs.FrequencySweepError):
        fs.select_envelope_fet(48.0, -1.0)


# ---------------------------------------------------------------------------
# Real MKF seam: L ∝ 1/fsw (trap #1). Guarded — skipped if PyOM unavailable.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_seam_inductance_scales_inverse_with_fsw():
    """bridge.design_magnetics_at_fsw lets MKF re-derive L per frequency; at a
    fixed ripple budget L ∝ 1/fsw. This proves the sweep's loop-order trap is
    closed at the seam (the magnetic IS re-derived inside the loop)."""
    try:
        from heaviside import bridge
        from heaviside.stages import converter_spec_build
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"import failed: {exc}")

    base = converter_spec_build.build(
        {**_spec(), "efficiency": 0.92, "diodeVoltageDrop": 0.7}, "buck"
    )
    try:
        a = bridge.design_magnetics_at_fsw("buck", base, 200_000, max_results=1)
        b = bridge.design_magnetics_at_fsw("buck", base, 400_000, max_results=1)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"MKF unavailable: {exc}")
    La = bridge._harvest_authoritative_inductance(a[0].mas)
    Lb = bridge._harvest_authoritative_inductance(b[0].mas)
    # doubling fsw roughly halves L (MKF re-derives; allow 15% for core quantisation)
    assert La / Lb == pytest.approx(2.0, rel=0.15)


@pytest.mark.integration
def test_seam_rejects_desired_inductance():
    """The seam must refuse a BASE-schema violation loudly (house rule)."""
    try:
        from heaviside import bridge
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"import failed: {exc}")
    spec = {**_spec(), "desiredInductance": 1e-5}
    with pytest.raises(bridge.BridgeError, match="desiredInductance"):
        bridge.design_magnetics_at_fsw("buck", spec, 200_000)
