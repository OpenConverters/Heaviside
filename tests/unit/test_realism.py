"""Tests for ``heaviside.pipeline.realism``.

Layered:

  1. Primitive checks — input validation (must throw on bad inputs per
     CLAUDE.md "no fallbacks") + PASS / FAIL behaviour at boundaries.
  2. Orchestrator selection — every check must arrive at PASS, FAIL,
     NOT_APPLICABLE, or UNAVAILABLE; never silently disappear.
  3. End-to-end on the real ``/tmp/buck_out.tas.json`` shape: today the
     verdict is honestly INCOMPLETE; this test pins that contract so we
     notice when upstream agents enrich the pipeline.
"""

from __future__ import annotations

import pytest

from heaviside.pipeline.realism import (
    ALL_CHECKS,
    CheckStatus,
    RealismError,
    RealismVerdict,
    check_capacitor_voltage_derating,
    check_diode_voltage_derating,
    check_duty_cycle_bounds,
    check_efficiency_sanity,
    check_fet_voltage_derating,
    check_inductor_isat_margin,
    check_no_negative_losses,
    check_operating_point_converged,
    check_output_voltage_regulation,
    check_power_balance,
    check_thermal_limit,
    evaluate_tas,
)

# ---------------------------------------------------------------------------
# 1. Primitive checks
# ---------------------------------------------------------------------------


class TestPowerBalance:
    def test_balanced_passes(self):
        # 100 W in, 95 W out, 5 W losses → exact balance.
        r = check_power_balance(100.0, 95.0, 5.0)
        assert r.status is CheckStatus.PASS
        assert r.value == pytest.approx(0.0)
        assert r.margin == pytest.approx(0.05)

    def test_imbalance_fails(self):
        # 100 W in, 90 W out → 10 W gap; reporting only 2 W accounts for
        # 8 W / 90 W = 8.9% imbalance, above the default 5% tolerance.
        r = check_power_balance(100.0, 90.0, 2.0)
        assert r.status is CheckStatus.FAIL
        assert r.value > 0.05

    @pytest.mark.parametrize(
        "pin,pout,losses",
        [
            (0.0, 95.0, 5.0),  # pin <= 0
            (100.0, 0.0, 5.0),  # pout <= 0
            (-1.0, 95.0, 5.0),  # negative pin
            (100.0, 95.0, float("nan")),
            (100.0, 95.0, float("inf")),
        ],
    )
    def test_invalid_inputs_throw(self, pin, pout, losses):
        with pytest.raises(RealismError):
            check_power_balance(pin, pout, losses)

    def test_tolerance_must_be_positive(self):
        with pytest.raises(RealismError):
            check_power_balance(100, 95, 5, tolerance=0.0)


class TestVoltageDerating:
    def test_fet_pass_at_exact_min_ratio(self):
        r = check_fet_voltage_derating(150.0, 100.0)  # 1.5x exactly
        assert r.status is CheckStatus.PASS
        assert r.margin == pytest.approx(0.0)

    def test_fet_fail_below_min(self):
        r = check_fet_voltage_derating(140.0, 100.0)
        assert r.status is CheckStatus.FAIL

    def test_diode_uses_1p3(self):
        r = check_diode_voltage_derating(130.0, 100.0)
        assert r.status is CheckStatus.PASS
        r2 = check_diode_voltage_derating(129.0, 100.0)
        assert r2.status is CheckStatus.FAIL

    def test_cap_uses_1p5(self):
        assert check_capacitor_voltage_derating(15.0, 10.0).status is CheckStatus.PASS
        assert check_capacitor_voltage_derating(14.0, 10.0).status is CheckStatus.FAIL

    @pytest.mark.parametrize(
        "fn",
        [
            check_fet_voltage_derating,
            check_diode_voltage_derating,
            check_capacitor_voltage_derating,
        ],
    )
    def test_zero_stress_throws(self, fn):
        with pytest.raises(RealismError):
            fn(100.0, 0.0)

    @pytest.mark.parametrize(
        "fn",
        [
            check_fet_voltage_derating,
            check_diode_voltage_derating,
            check_capacitor_voltage_derating,
        ],
    )
    def test_negative_rated_throws(self, fn):
        with pytest.raises(RealismError):
            fn(-1.0, 10.0)


class TestIsatMargin:
    def test_pass(self):
        r = check_inductor_isat_margin(12.0, 10.0)  # 1.2x exactly
        assert r.status is CheckStatus.PASS

    def test_fail(self):
        assert check_inductor_isat_margin(11.0, 10.0).status is CheckStatus.FAIL

    def test_zero_ipeak_throws(self):
        with pytest.raises(RealismError):
            check_inductor_isat_margin(10.0, 0.0)


class TestVoutRegulation:
    def test_pass(self):
        assert check_output_voltage_regulation(12.1, 12.0).status is CheckStatus.PASS

    def test_fail_high(self):
        assert check_output_voltage_regulation(12.7, 12.0).status is CheckStatus.FAIL

    def test_zero_target_throws(self):
        with pytest.raises(RealismError):
            check_output_voltage_regulation(12.0, 0.0)


class TestEfficiencySanity:
    @pytest.mark.parametrize("eta", [0.71, 0.85, 0.99])
    def test_plausible(self, eta):
        assert check_efficiency_sanity(eta).status is CheckStatus.PASS

    @pytest.mark.parametrize("eta", [0.5, 0.7, 0.995, 1.0, 1.5])
    def test_implausible(self, eta):
        assert check_efficiency_sanity(eta).status is CheckStatus.FAIL

    def test_nan_throws(self):
        with pytest.raises(RealismError):
            check_efficiency_sanity(float("nan"))

    def test_inverted_window_throws(self):
        with pytest.raises(RealismError):
            check_efficiency_sanity(0.9, low=0.9, high=0.8)


class TestDutyCycleBounds:
    def test_forward_capped_at_half(self):
        assert check_duty_cycle_bounds(0.45, "forward").status is CheckStatus.PASS
        assert check_duty_cycle_bounds(0.55, "forward").status is CheckStatus.FAIL

    def test_buck_allows_up_to_095(self):
        assert check_duty_cycle_bounds(0.55, "buck").status is CheckStatus.PASS
        assert check_duty_cycle_bounds(0.94, "buck").status is CheckStatus.PASS
        assert check_duty_cycle_bounds(0.96, "buck").status is CheckStatus.FAIL

    def test_topology_normalisation(self):
        # "Forward Converter" / "FORWARD" / "forward-converter" all hit the same key.
        # Only the bare "forward" / "single_switch_forward" alias triggers 0.5 cap.
        # Anything with an extra word (e.g. "two-switch forward") gets the 0.95 cap.
        assert check_duty_cycle_bounds(0.55, "forward").status is CheckStatus.FAIL
        assert check_duty_cycle_bounds(0.55, "two_switch_forward").status is CheckStatus.PASS

    def test_empty_topology_throws(self):
        with pytest.raises(RealismError):
            check_duty_cycle_bounds(0.5, "")

    def test_nan_duty_throws(self):
        with pytest.raises(RealismError):
            check_duty_cycle_bounds(float("nan"), "buck")


class TestNoNegativeLosses:
    def test_all_nonnegative_passes(self):
        r = check_no_negative_losses({"conduction": 1.0, "switching": 0.5, "core": 0.0})
        assert r.status is CheckStatus.PASS

    def test_negative_fails_with_violator_list(self):
        r = check_no_negative_losses({"conduction": 1.0, "switching": -0.2})
        assert r.status is CheckStatus.FAIL
        assert r.extra["violators"] == {"switching": -0.2}

    def test_ignores_none_and_strings(self):
        r = check_no_negative_losses({"conduction": 1.0, "missing": None, "label": "tag"})
        assert r.status is CheckStatus.PASS

    def test_tiny_negative_is_rounding_noise(self):
        r = check_no_negative_losses({"core": -1e-6})
        assert r.status is CheckStatus.PASS

    def test_nan_loss_throws(self):
        with pytest.raises(RealismError):
            check_no_negative_losses({"core": float("nan")})

    def test_non_mapping_throws(self):
        with pytest.raises(RealismError):
            check_no_negative_losses([1.0, 2.0])  # type: ignore[arg-type]


class TestThermalLimit:
    def test_under_limit_passes(self):
        r = check_thermal_limit(100.0, 150.0)
        assert r.status is CheckStatus.PASS
        assert r.margin == pytest.approx(50.0)

    def test_over_limit_fails(self):
        assert check_thermal_limit(160.0, 150.0).status is CheckStatus.FAIL

    def test_exactly_at_limit_fails(self):
        # margin > 0 strictly, not >=
        assert check_thermal_limit(150.0, 150.0).status is CheckStatus.FAIL


# ---------------------------------------------------------------------------
# 2. Orchestrator selection
# ---------------------------------------------------------------------------


def _empty_tas() -> dict:
    return {"topology": {"stages": [], "interStageConnections": []}}


def _buck_shaped_tas() -> dict:
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
                            {"name": "L1", "category": "magnetic", "mas": {}},
                            {"name": "C_out", "data": "placeholder"},
                        ]
                    },
                }
            ],
            "interStageConnections": [],
        }
    }


class TestOrchestratorContract:
    def test_rejects_non_mapping_tas(self):
        with pytest.raises(RealismError):
            evaluate_tas([], topology="buck")  # type: ignore[arg-type]

    def test_rejects_empty_topology(self):
        with pytest.raises(RealismError):
            evaluate_tas(_empty_tas(), topology="")

    def test_every_known_check_is_classified(self):
        """No check name from ``ALL_CHECKS`` may silently disappear."""
        r = evaluate_tas(_buck_shaped_tas(), topology="buck")
        names = {c.name for c in r.checks}
        # ``no_negative_losses`` can run once or many times; the others appear
        # exactly once.
        for name in ALL_CHECKS:
            assert name in names, f"check {name!r} missing from report"

    def test_empty_tas_yields_incomplete(self):
        r = evaluate_tas(_empty_tas(), topology="buck")
        assert r.verdict is RealismVerdict.INCOMPLETE
        # Component-keyed checks → NOT_APPLICABLE because the TAS has no
        # components at all.  Sim / loss-budget checks → UNAVAILABLE.
        na = {c.name for c in r.checks if c.status is CheckStatus.NOT_APPLICABLE}
        assert {
            "fet_voltage_derating",
            "diode_voltage_derating",
            "capacitor_voltage_derating",
            "inductor_isat_margin",
        }.issubset(na)


class TestFalsySentinelResolution:
    """A measured 0.0 V output (dead converter) must FAIL regulation, not be
    treated as a missing value and downgraded to UNAVAILABLE."""

    def test_zero_vout_fails_regulation_not_unavailable(self):
        tas = _buck_shaped_tas()
        # No controller stage in _buck_shaped_tas, so the check reaches the
        # FAIL branch (not the open-loop NOT_APPLICABLE carve-out).
        tas["simulation_results"] = {"op0": {"vout": 0.0}}
        spec = {
            "operatingPoints": [
                {"outputVoltages": [12.0], "outputCurrents": [5.0]},
            ]
        }
        r = evaluate_tas(tas, topology="buck", spec=spec)
        reg = next(c for c in r.checks if c.name == "output_voltage_regulation")
        assert reg.status is CheckStatus.FAIL, (
            f"0.0 V vout must FAIL regulation, got {reg.status} ({reg.detail})"
        )
        assert r.verdict is RealismVerdict.FAIL


class TestOrchestratorVerdict:
    def test_all_unavailable_is_incomplete(self):
        r = evaluate_tas(_buck_shaped_tas(), topology="buck")
        assert r.verdict is RealismVerdict.INCOMPLETE
        assert r.summary["pass"] == 0
        assert r.summary["fail"] == 0

    def test_one_pass_no_fail_is_pass(self):
        # Inject a duty cycle into the TAS so duty_cycle_bounds runs PASS.
        tas = _buck_shaped_tas()
        tas["duty"] = 0.25
        r = evaluate_tas(tas, topology="buck")
        assert r.verdict is RealismVerdict.PASS
        assert any(c.name == "duty_cycle_bounds" and c.status is CheckStatus.PASS for c in r.checks)

    def test_metadata_only_pass_does_not_carry_verdict(self):
        """Regression: a design whose ONLY passing check is the audit/meta
        ``selection_provenance_complete`` (every physics check UNAVAILABLE
        because sim/BOM never produced inputs) must be INCOMPLETE, not PASS.

        This is the fail-open hole that let a degraded TAS read as realistic on
        bookkeeping alone. ``stage3_realize`` now raises before emitting such a
        TAS; this pins the gate's own defence-in-depth regardless of producer.
        """
        from heaviside import provenance

        tas = _buck_shaped_tas()
        # Stamp Q1 with a complete provenance envelope but NO ratings/stress and
        # NO simulation_results — so provenance PASSes and every physics check
        # stays UNAVAILABLE.
        q1 = tas["topology"]["stages"][0]["circuit"]["components"][0]
        q1["mpn"] = "FAKE-FET-100V"
        q1["selection_provenance"] = provenance.make(
            producer="test", method="manual", source_ref="unit-test", inputs={"x": 1}
        )
        r = evaluate_tas(tas, topology="buck")
        prov = next(c for c in r.checks if c.name == "selection_provenance_complete")
        assert prov.status is CheckStatus.PASS  # the only PASS, and it's meta
        assert not any(
            c.status is CheckStatus.PASS and c.name in ALL_CHECKS for c in r.checks
        )
        assert r.verdict is RealismVerdict.INCOMPLETE  # NOT pass

    def test_any_fail_is_fail(self):
        tas = _buck_shaped_tas()
        tas["duty"] = 0.99  # buck max is 0.95 → FAIL
        r = evaluate_tas(tas, topology="buck")
        assert r.verdict is RealismVerdict.FAIL

    def test_fet_rating_drives_pass(self):
        tas = _buck_shaped_tas()
        tas["topology"]["stages"][0]["circuit"]["components"][0].update(
            {
                "vds_rated": 150.0,
                "vds_stress": 60.0,
            }
        )
        r = evaluate_tas(tas, topology="buck")
        fet = [c for c in r.checks if c.name == "fet_voltage_derating"]
        assert len(fet) == 1
        assert fet[0].status is CheckStatus.PASS
        assert fet[0].extra["component"] == "Q1"

    def test_loss_budget_runs_no_negative_losses(self):
        tas = _buck_shaped_tas()
        tas["loss_budget"] = {"conduction": 1.0, "switching": 0.5}
        r = evaluate_tas(tas, topology="buck")
        nnl = [c for c in r.checks if c.name == "no_negative_losses"]
        assert len(nnl) == 1
        assert nnl[0].status is CheckStatus.PASS

    def test_nested_loss_budget_runs_per_line(self):
        tas = _buck_shaped_tas()
        tas["loss_budget"] = {
            "vin_min": {"conduction": 1.0, "switching": 0.5},
            "vin_max": {"conduction": 0.8, "switching": -0.3},
        }
        r = evaluate_tas(tas, topology="buck")
        nnl = [c for c in r.checks if c.name == "no_negative_losses"]
        assert len(nnl) == 2
        statuses = {c.extra["line"]: c.status for c in nnl}
        assert statuses["vin_min"] is CheckStatus.PASS
        assert statuses["vin_max"] is CheckStatus.FAIL
        assert r.verdict is RealismVerdict.FAIL

    def test_vout_regulation_from_spec_and_sim(self):
        tas = _buck_shaped_tas()
        tas["simulation_results"] = {"nominal": {"vout": 12.1}}
        spec = {"operatingPoints": [{"outputVoltages": [12.0]}]}
        r = evaluate_tas(tas, topology="buck", spec=spec)
        ovr = [c for c in r.checks if c.name == "output_voltage_regulation"]
        assert len(ovr) == 1
        assert ovr[0].status is CheckStatus.PASS

    def test_efficiency_percent_form_is_not_silently_normalised(self):
        # The realism gate refuses to silently coerce a percent-form
        # efficiency (92.5) into a ratio (0.925) — silent normalization is
        # a forbidden fallback (no-coercion rule). An out-of-(0,1) value is
        # surfaced as UNAVAILABLE (a sim-measurement bug to fix upstream),
        # never normalized, passed, or invented; value stays None.
        tas = _buck_shaped_tas()
        tas["simulation_results"] = {"nominal": {"efficiency": 92.5}}  # percent form
        r = evaluate_tas(tas, topology="buck")
        eff = [c for c in r.checks if c.name == "efficiency_sanity"]
        assert eff[0].status is CheckStatus.UNAVAILABLE
        assert eff[0].value is None
        assert "ratio in (0,1)" in eff[0].detail


class TestOperatingPointConverged:
    """A non-converged / non-regulated rated operating point must not read as
    validated — the numbers off a non-converged point are meaningless."""

    def test_primitive_pass_fail(self):
        assert check_operating_point_converged(True).status is CheckStatus.PASS
        assert check_operating_point_converged(False).status is CheckStatus.FAIL
        # Converged but did not regulate → still FAIL.
        assert (
            check_operating_point_converged(True, regulated=False).status is CheckStatus.FAIL
        )
        assert (
            check_operating_point_converged(True, regulated=True).status is CheckStatus.PASS
        )

    def test_primitive_rejects_non_bool(self):
        with pytest.raises(RealismError):
            check_operating_point_converged("yes")  # type: ignore[arg-type]

    def test_gate_fails_a_non_converged_point(self):
        tas = _buck_shaped_tas()
        tas["simulation_results"] = {
            "op0": {"efficiency": 0.9, "vout": 12.0, "converged": False, "regulated": False}
        }
        r = evaluate_tas(tas, topology="buck")
        conv = [c for c in r.checks if c.name == "operating_point_converged"]
        assert conv and conv[0].status is CheckStatus.FAIL
        assert r.verdict is RealismVerdict.FAIL

    def test_gate_passes_a_converged_regulated_point(self):
        tas = _buck_shaped_tas()
        tas["simulation_results"] = {
            "op0": {"efficiency": 0.9, "vout": 12.0, "converged": True, "regulated": True}
        }
        r = evaluate_tas(tas, topology="buck")
        conv = [c for c in r.checks if c.name == "operating_point_converged"]
        assert conv and conv[0].status is CheckStatus.PASS

    def test_marker_absent_is_unavailable_not_silently_passed(self):
        # An older stamp path that recorded no convergence marker must surface as
        # UNAVAILABLE, never as a silent pass.
        tas = _buck_shaped_tas()
        tas["simulation_results"] = {"op0": {"efficiency": 0.9, "vout": 12.0}}
        r = evaluate_tas(tas, topology="buck")
        conv = [c for c in r.checks if c.name == "operating_point_converged"]
        assert conv and conv[0].status is CheckStatus.UNAVAILABLE


class TestEveryComponentIsChecked:
    """Regression: the gate used to short-circuit after the FIRST component of
    each kind, silently passing under-rated / over-temp siblings (a 4-FET bridge
    only checked Q1; an over-temp D2 hid behind a cool D1). Every qualifying
    component must now be gated."""

    def _two_diode_tas(self) -> dict:
        return {
            "topology": {
                "stages": [
                    {
                        "name": "rectifier",
                        "role": "rectifier",
                        "circuit": {
                            "components": [
                                {"name": "D1", "category": "diode"},
                                {"name": "D2", "category": "diode"},
                            ]
                        },
                    }
                ],
                "interStageConnections": [],
            }
        }

    def test_second_diode_overtemp_is_not_hidden_by_first(self):
        tas = self._two_diode_tas()
        comps = tas["topology"]["stages"][0]["circuit"]["components"]
        comps[0].update({"tj": 100.0, "tj_max": 150.0})   # cool — PASS
        comps[1].update({"tj": 160.0, "tj_max": 150.0})   # over-temp — FAIL
        r = evaluate_tas(tas, topology="phase_shifted_full_bridge")
        thermal = [c for c in r.checks if c.name == "thermal_limit"]
        assert len(thermal) == 2                          # BOTH diodes gated
        by = {c.extra["component"]: c.status for c in thermal}
        assert by["D1"] is CheckStatus.PASS
        assert by["D2"] is CheckStatus.FAIL
        assert r.verdict is RealismVerdict.FAIL

    def test_all_four_bridge_fets_are_derated(self):
        tas = {
            "topology": {
                "stages": [
                    {
                        "name": "bridge",
                        "role": "switchingCell",
                        "circuit": {
                            "components": [
                                {"name": f"Q{i}", "category": "mosfet",
                                 "vds_rated": 150.0, "vds_stress": 60.0}
                                for i in range(1, 5)
                            ]
                        },
                    }
                ],
                "interStageConnections": [],
            }
        }
        # Under-rate the LAST switch only — the bug would have checked Q1 (fine)
        # and never looked at Q4.
        tas["topology"]["stages"][0]["circuit"]["components"][3]["vds_rated"] = 70.0
        r = evaluate_tas(tas, topology="phase_shifted_full_bridge")
        fet = [c for c in r.checks if c.name == "fet_voltage_derating"]
        assert len(fet) == 4
        by = {c.extra["component"]: c.status for c in fet}
        assert by["Q1"] is CheckStatus.PASS
        assert by["Q4"] is CheckStatus.FAIL             # 70/60 = 1.17 < 1.5
        assert r.verdict is RealismVerdict.FAIL


# ---------------------------------------------------------------------------
# 3. Report serialisation
# ---------------------------------------------------------------------------


class TestReportSerialisation:
    def test_to_dict_round_trip(self):
        r = evaluate_tas(_buck_shaped_tas(), topology="buck")
        d = r.to_dict()
        assert d["verdict"] in {"pass", "fail", "incomplete"}
        assert isinstance(d["summary"], dict)
        assert isinstance(d["checks"], list)
        assert all(isinstance(c["name"], str) for c in d["checks"])
        assert all(
            c["status"] in {"pass", "fail", "not_applicable", "unavailable"} for c in d["checks"]
        )

    def test_to_dict_emits_tuple_limit_as_list(self):
        # efficiency_sanity uses a (low, high) tuple limit.
        tas = _buck_shaped_tas()
        tas["simulation_results"] = {"nominal": {"efficiency": 0.92}}
        r = evaluate_tas(tas, topology="buck")
        d = r.to_dict()
        eff = next(c for c in d["checks"] if c["name"] == "efficiency_sanity")
        assert isinstance(eff["limit"], list)
        assert eff["limit"] == [0.70, 0.995]


# ---------------------------------------------------------------------------
# 4. Integration: real buck pipeline output
# ---------------------------------------------------------------------------


def test_real_buck_output_passes_the_realism_gate(tmp_path):
    """The full design pipeline (selector + analyst + thermal stages) now
    populates stress / ratings / loss data, so a real 48→12 V buck design
    must earn a PASS verdict. (Historically this asserted INCOMPLETE while
    the pipeline emitted magnetics-only enrichment — flipped per its own
    instruction once the component agents landed.)
    """
    pytest.importorskip("PyOpenMagnetics")
    import json
    from pathlib import Path

    fp = Path("/tmp/buck_out.tas.json")
    if not fp.is_file():
        pytest.skip("/tmp/buck_out.tas.json not present (regenerate with `heaviside design buck`)")
    tas = json.loads(fp.read_text())
    spec = json.loads(Path("/tmp/buck_spec.json").read_text())
    r = evaluate_tas(tas, topology="buck", spec=spec)
    assert r.verdict is RealismVerdict.PASS
    # Every reported check must explain why it could not run.
    for c in r.checks:
        if c.status in (CheckStatus.UNAVAILABLE, CheckStatus.NOT_APPLICABLE):
            assert c.detail, f"{c.name}: missing explanation for {c.status.value}"
