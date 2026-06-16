"""Tests for ``heaviside.pipeline.extract`` — topology-aware enrichment
that gives the realism gate something to actually check.

Per CLAUDE.md "no fallbacks, throw": every missing / malformed spec or
MAS field must raise :class:`EnrichmentError`, never silently default.
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


def _buck_spec() -> dict:
    return {
        "inputVoltage": {"minimum": 36.0, "maximum": 60.0, "nominal": 48.0},
        "desiredInductance": 22e-6,
        "currentRippleRatio": 0.4,
        "diodeVoltageDrop": 0.7,
        "efficiency": 0.95,
        "operatingPoints": [
            {
                "outputVoltages": [12.0],
                "outputCurrents": [5.0],
                "switchingFrequency": 200_000.0,
                "ambientTemperature": 25,
            }
        ],
    }


def _buck_mas() -> dict:
    """Complete, PyOM-evaluable MAS for the buck inductor.

    A real gapped inductor (single primary winding, 9 turns) built by
    :func:`real_magnetic` so the extractor's
    ``calculate_saturation_current`` call returns genuine MKF physics
    instead of silently exercising the (now-deleted) analytical fallback.
    The harvest paths the extractor reads —
    ``core.processedDescription.effectiveParameters.effectiveArea``,
    ``coil.functionalDescription[0].numberTurns``, and
    ``core.functionalDescription.material.saturation`` — are all populated
    by autocomplete.
    """
    return real_magnetic(
        shape="ETD 29/16/10",
        material="3C95",
        gap_mm=1.0,
        windings=[{"name": "Primary", "turns": 9, "side": "primary"}],
    )


def _buck_tas() -> dict:
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
                            {"name": "L1", "category": "magnetic", "mas": _buck_mas()},
                            {"name": "C_out", "data": "placeholder"},
                        ]
                    },
                }
            ],
            "interStageCircuit": [],
        }
    }


# ---------------------------------------------------------------------------
# Buck happy path: numerical correctness
# ---------------------------------------------------------------------------


class TestBuckEnrichmentMath:
    """Pin the closed-form values so any future refactor that drifts the
    math by even a percent will trip a test.
    """

    def test_duty_cycle_at_both_extremes(self):
        out = enrich_tas_for_realism(_buck_tas(), topology="buck", spec=_buck_spec())
        # D_max = 12 / 36 = 0.333…
        assert out["duty_max"] == pytest.approx(12.0 / 36.0, abs=1e-6)
        # D_min = 12 / 60 = 0.2
        assert out["duty_min"] == pytest.approx(0.2, abs=1e-6)
        # ``duty`` stamped for the orchestrator is the worst-case (max).
        assert out["duty"] == out["duty_max"]

    def test_ipeak_worst_uses_vin_max_and_minus20_inductance(self):
        out = enrich_tas_for_realism(_buck_tas(), topology="buck", spec=_buck_spec())
        l1 = out["topology"]["stages"][0]["circuit"]["components"][2]
        # ΔIL_worst = Vout · (1 − D_min) / (0.8·L · fsw)
        #           = 12 · 0.8 / (0.8 · 22e-6 · 200_000)
        #           = 9.6 / 3.52  = 2.7272…  A_pp
        # Ipeak_worst = 5 + 2.7272/2 = 6.3636…
        assert l1["ipeak_worst"] == pytest.approx(
            5.0 + (12.0 * 0.8) / (0.8 * 22e-6 * 200_000) / 2.0, rel=1e-6
        )
        # And worst-case L stamped in provenance must be 0.8·L exactly.
        assert l1["ipeak_provenance"]["L_worst_H"] == pytest.approx(0.8 * 22e-6)

    def test_isat_matches_pyom_ground_truth(self):
        """Ground truth = MKF: the stamped Isat must equal PyOM's
        saturation current for the real L1 magnetic, evaluated at the
        worst-case HOT junction (100 °C — ferrite B_sat falls with T), NOT
        the 25 °C ambient and NOT an analytical B_sat·N·A_e/L formula. The
        hot corner matches the frequency sweep's saturation gate
        (bridge._isat_from_mas) so a swept-feasible core passes here; the
        25 °C value (~45.7 A) over-stated Isat vs the hot 100 °C value
        (~35.3 A) and let saturation-marginal cores through. Provenance still
        records the conservative material B_sat (0.2–0.6 T) + turns count."""
        themas = _buck_mas()
        out = enrich_tas_for_realism(_buck_tas(), topology="buck", spec=_buck_spec())
        l1 = out["topology"]["stages"][0]["circuit"]["components"][2]
        # Isat now evaluated at the hot design corner (100 °C), not the
        # op-point's 25 °C ambient — see extract._ISAT_DESIGN_TEMP_C.
        expected = isat_of(themas, temperature_c=100.0)
        assert l1["isat"] == pytest.approx(expected, rel=1e-3)
        assert 0.2 < l1["isat_provenance"]["b_sat_T"] < 0.6
        assert l1["isat_provenance"]["n_turns"] == 9

    def test_runs_realism_end_to_end_to_pass(self):
        """The whole reason this module exists: enrich → evaluate → PASS."""
        enriched = enrich_tas_for_realism(_buck_tas(), topology="buck", spec=_buck_spec())
        r = evaluate_tas(enriched, topology="buck", spec=_buck_spec())
        assert r.verdict is RealismVerdict.PASS
        passed = {c.name for c in r.checks if c.status is CheckStatus.PASS}
        assert {"duty_cycle_bounds", "inductor_isat_margin"}.issubset(passed)

    def test_isat_provenance_traces_every_input(self):
        """A reviewer must be able to reproduce the Isat number from the
        provenance dict alone — no implicit inputs.  Ground truth = MKF:
        re-running PyOM on the same L1 MAS at the recorded temperature must
        reproduce the stamped Isat (never the analytical formula)."""
        themas = _buck_mas()
        out = enrich_tas_for_realism(_buck_tas(), topology="buck", spec=_buck_spec())
        l1 = out["topology"]["stages"][0]["circuit"]["components"][2]
        p = l1["isat_provenance"]
        # Every input the extractor used is in the provenance dict, and the
        # method is PyOM — reproduce it from those alone.
        assert "PyOM" in p["method"]
        assert p["n_turns"] == 9
        recomputed = isat_of(themas, temperature_c=p["temperature_c"])
        assert recomputed == pytest.approx(l1["isat"], rel=1e-3)


# ---------------------------------------------------------------------------
# Buck failure modes — fail-closed per CLAUDE.md
# ---------------------------------------------------------------------------


class TestBuckEnrichmentFailureModes:
    def test_step_up_design_throws(self):
        spec = _buck_spec()
        spec["operatingPoints"][0]["outputVoltages"] = [48.0]  # Vout >= Vin_min
        with pytest.raises(EnrichmentError, match="step up"):
            enrich_tas_for_realism(_buck_tas(), topology="buck", spec=spec)

    @pytest.mark.parametrize(
        "mutate,match",
        [
            (lambda s: s.pop("inputVoltage"), "inputVoltage"),
            (lambda s: s["inputVoltage"].pop("minimum"), "min"),
            (lambda s: s.pop("desiredInductance"), "desiredInductance"),
            (lambda s: s.pop("operatingPoints"), "operatingPoints"),
            (lambda s: s["operatingPoints"][0].pop("switchingFrequency"), "switchingFrequency"),
            (lambda s: s["operatingPoints"][0].update({"outputCurrents": []}), "outputCurrents"),
        ],
    )
    def test_missing_required_spec_fields_throw(self, mutate, match):
        spec = _buck_spec()
        mutate(spec)
        with pytest.raises(EnrichmentError, match=match):
            enrich_tas_for_realism(_buck_tas(), topology="buck", spec=spec)

    def test_inverted_vin_range_throws(self):
        spec = _buck_spec()
        spec["inputVoltage"]["minimum"] = 100.0
        spec["inputVoltage"]["maximum"] = 50.0
        with pytest.raises(EnrichmentError, match="inverted"):
            enrich_tas_for_realism(_buck_tas(), topology="buck", spec=spec)

    def test_negative_inductance_throws(self):
        spec = _buck_spec()
        spec["desiredInductance"] = -1e-6
        with pytest.raises(EnrichmentError, match="desiredInductance"):
            enrich_tas_for_realism(_buck_tas(), topology="buck", spec=spec)

    def test_zero_switching_frequency_throws(self):
        spec = _buck_spec()
        spec["operatingPoints"][0]["switchingFrequency"] = 0
        with pytest.raises(EnrichmentError, match="switchingFrequency"):
            enrich_tas_for_realism(_buck_tas(), topology="buck", spec=spec)

    def test_no_magnetic_component_throws(self):
        tas = _buck_tas()
        tas["topology"]["stages"][0]["circuit"]["components"] = [
            {"name": "Q1", "data": "placeholder"},
        ]
        with pytest.raises(EnrichmentError, match="magnetic"):
            enrich_tas_for_realism(tas, topology="buck", spec=_buck_spec())

    def test_magnetic_without_mas_throws(self):
        tas = _buck_tas()
        tas["topology"]["stages"][0]["circuit"]["components"][2] = {
            "name": "L1",
            "category": "magnetic",
        }
        with pytest.raises(EnrichmentError, match="no MAS"):
            enrich_tas_for_realism(tas, topology="buck", spec=_buck_spec())

    @pytest.mark.parametrize(
        "path,value",
        [
            (("core", "processedDescription", "effectiveParameters", "effectiveArea"), 0.0),
            (("coil", "functionalDescription", 0, "numberTurns"), 0),
        ],
    )
    def test_invalid_mas_fields_throw(self, path, value):
        tas = _buck_tas()
        mas = tas["topology"]["stages"][0]["circuit"]["components"][2]["mas"]
        cur = mas
        for k in path[:-1]:
            cur = cur[k]
        cur[path[-1]] = value
        with pytest.raises(EnrichmentError):
            enrich_tas_for_realism(tas, topology="buck", spec=_buck_spec())

    def test_empty_saturation_curve_throws(self):
        tas = _buck_tas()
        tas["topology"]["stages"][0]["circuit"]["components"][2]["mas"]["core"][
            "functionalDescription"
        ]["material"]["saturation"] = []
        with pytest.raises(EnrichmentError, match="saturation"):
            enrich_tas_for_realism(tas, topology="buck", spec=_buck_spec())

    def test_negative_bsat_throws(self):
        tas = _buck_tas()
        tas["topology"]["stages"][0]["circuit"]["components"][2]["mas"]["core"][
            "functionalDescription"
        ]["material"]["saturation"][0]["magneticFluxDensity"] = -0.1
        with pytest.raises(EnrichmentError, match="magneticFluxDensity"):
            enrich_tas_for_realism(tas, topology="buck", spec=_buck_spec())


# ---------------------------------------------------------------------------
# Dispatcher contract
# ---------------------------------------------------------------------------


class TestDispatcher:
    def test_returns_deep_copy_not_alias(self):
        tas = _buck_tas()
        out = enrich_tas_for_realism(tas, topology="buck", spec=_buck_spec())
        assert out is not tas
        # Mutating ``out`` must not touch ``tas``.
        out["topology"]["stages"][0]["name"] = "MUTATED"
        assert tas["topology"]["stages"][0]["name"] == "power_stage"

    def test_unknown_topology_passes_through_unchanged(self):
        tas = _buck_tas()
        # A synthetic topology name with no extractor registered.  We
        # used to use real topology names here (``push_pull`` etc.)
        # but every time an extractor was added, this test had to be
        # updated to a different unregistered name.  A clearly-synthetic
        # name pins the contract permanently: the dispatcher must leave
        # the TAS structurally untouched (deep-copied) rather than
        # raising or silently stamping bogus fields.
        out = enrich_tas_for_realism(
            tas,
            topology="__synthetic_unregistered_topology__",
            spec=_buck_spec(),
        )
        # No enrichment — should be a structural deep copy of the input
        # (no ``duty`` stamped, no ``isat`` on L1).
        assert "duty" not in out
        assert "isat" not in out["topology"]["stages"][0]["circuit"]["components"][2]

    def test_topology_is_case_insensitive(self):
        tas = _buck_tas()
        out = enrich_tas_for_realism(tas, topology="BUCK", spec=_buck_spec())
        assert "duty" in out

    @pytest.mark.parametrize("bad", ["", "   ", 42, None])
    def test_invalid_topology_throws(self, bad):
        with pytest.raises(EnrichmentError):
            enrich_tas_for_realism(_buck_tas(), topology=bad, spec=_buck_spec())  # type: ignore[arg-type]

    def test_non_mapping_tas_throws(self):
        with pytest.raises(EnrichmentError):
            enrich_tas_for_realism([], topology="buck", spec=_buck_spec())  # type: ignore[arg-type]

    def test_non_mapping_spec_throws(self):
        with pytest.raises(EnrichmentError):
            enrich_tas_for_realism(_buck_tas(), topology="buck", spec="not a dict")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Integration: real /tmp/buck_out.tas.json
# ---------------------------------------------------------------------------


def test_real_buck_enrichment_runs_both_real_checks(tmp_path):
    """End-to-end: load the real buck output produced by ``heaviside design
    buck``, enrich it, and verify both Phase 3 checks (duty_cycle_bounds,
    inductor_isat_margin) are *evaluated with real numbers* — not left as
    UNAVAILABLE or NOT_APPLICABLE.

    The check verdicts themselves are NOT pinned: PyMKF's stock 48->12@5A
    buck design picks an EP 17/3C96 core whose Isat (~4.1 A) is well below
    the spec's ~6.4 A peak demand, so inductor_isat_margin honestly FAILs.
    See ``docs/handoff`` 2026-05-22 — this is an upstream design-quality
    issue; PyMKF's core library lacks bigger options for this current range.
    The test exists to confirm enrichment + extraction wires up correctly,
    not to certify PyMKF's design output.
    """
    pytest.importorskip("PyOpenMagnetics")
    import json
    from pathlib import Path

    out_fp = Path("/tmp/buck_out.tas.json")
    spec_fp = Path("/tmp/buck_spec.json")
    if not (out_fp.is_file() and spec_fp.is_file()):
        pytest.skip(
            "regen with `heaviside design buck --spec /tmp/buck_spec.json -o /tmp/buck_out.tas.json`"
        )

    tas = json.loads(out_fp.read_text())
    spec = json.loads(spec_fp.read_text())

    enriched = enrich_tas_for_realism(tas, topology="buck", spec=spec)
    r = evaluate_tas(enriched, topology="buck", spec=spec)

    evaluated = {c.name for c in r.checks if c.status in (CheckStatus.PASS, CheckStatus.FAIL)}
    assert "duty_cycle_bounds" in evaluated, "duty extractor regressed"
    assert "inductor_isat_margin" in evaluated, "isat enrichment regressed"

    # Duty cycle must always PASS for a well-formed buck spec.
    duty = next(c for c in r.checks if c.name == "duty_cycle_bounds")
    assert duty.status is CheckStatus.PASS, f"duty unexpectedly {duty.status}"

    # Isat ratio should be a finite positive number even when it FAILs.
    isat = next(c for c in r.checks if c.name == "inductor_isat_margin")
    assert isat.value is not None and isat.value > 0
