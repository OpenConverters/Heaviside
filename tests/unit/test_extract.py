"""Tests for ``heaviside.pipeline.extract`` — topology-aware enrichment
that gives the realism gate something to actually check.

Per CLAUDE.md "no fallbacks, throw": every missing / malformed spec or
MAS field must raise :class:`EnrichmentError`, never silently default.
"""

from __future__ import annotations

import copy

import pytest

from heaviside.pipeline import evaluate_tas
from heaviside.pipeline.extract import EnrichmentError, enrich_tas_for_realism
from heaviside.pipeline.realism import CheckStatus, RealismVerdict


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
        "operatingPoints": [{
            "outputVoltages": [12.0],
            "outputCurrents": [5.0],
            "switchingFrequency": 200_000.0,
            "ambientTemperature": 25,
        }],
    }


def _buck_mas() -> dict:
    """Minimal MAS shape sufficient for the buck extractor."""
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
                        {"magneticField": 393.0, "magneticFluxDensity": 0.4, "temperature": 100.0},
                        {"magneticField": 392.0, "magneticFluxDensity": 0.473, "temperature": 25.0},
                    ],
                },
            },
        },
        "coil": {
            "functionalDescription": [
                {"name": "Primary", "numberTurns": 9, "numberParallels": 1,
                 "isolationSide": "primary"},
            ],
        },
    }


def _buck_tas() -> dict:
    return {"topology": {
        "stages": [{
            "name": "power_stage",
            "role": "switchingCell",
            "circuit": {"components": [
                {"name": "Q1", "data": "placeholder"},
                {"name": "D1", "data": "placeholder"},
                {"name": "L1", "category": "magnetic", "mas": _buck_mas()},
                {"name": "C_out", "data": "placeholder"},
            ]},
        }],
        "interStageCircuit": [],
    }}


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
        assert l1["ipeak_worst"] == pytest.approx(5.0 + (12.0 * 0.8) / (0.8 * 22e-6 * 200_000) / 2.0,
                                                  rel=1e-6)
        # And worst-case L stamped in provenance must be 0.8·L exactly.
        assert l1["ipeak_provenance"]["L_worst_H"] == pytest.approx(0.8 * 22e-6)

    def test_isat_picks_lowest_bsat_across_temperatures(self):
        """Material has 0.4 T at 100°C and 0.473 T at 25°C; the
        conservative pick is 0.4 T."""
        out = enrich_tas_for_realism(_buck_tas(), topology="buck", spec=_buck_spec())
        l1 = out["topology"]["stages"][0]["circuit"]["components"][2]
        # Isat = B_sat · N · A_e / L = 0.4 · 9 · 8.0327e-5 / 22e-6
        expected = 0.4 * 9 * 8.0327e-5 / 22e-6
        assert l1["isat"] == pytest.approx(expected, rel=1e-4)
        assert l1["isat_provenance"]["b_sat_T"] == pytest.approx(0.4)
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
        provenance dict alone — no implicit inputs."""
        out = enrich_tas_for_realism(_buck_tas(), topology="buck", spec=_buck_spec())
        l1 = out["topology"]["stages"][0]["circuit"]["components"][2]
        p = l1["isat_provenance"]
        recomputed = p["b_sat_T"] * p["n_turns"] * p["effective_area_m2"] / p["inductance_H"]
        assert recomputed == pytest.approx(l1["isat"], rel=1e-4)


# ---------------------------------------------------------------------------
# Buck failure modes — fail-closed per CLAUDE.md
# ---------------------------------------------------------------------------


class TestBuckEnrichmentFailureModes:
    def test_step_up_design_throws(self):
        spec = _buck_spec()
        spec["operatingPoints"][0]["outputVoltages"] = [48.0]   # Vout >= Vin_min
        with pytest.raises(EnrichmentError, match="step up"):
            enrich_tas_for_realism(_buck_tas(), topology="buck", spec=spec)

    @pytest.mark.parametrize("mutate,match", [
        (lambda s: s.pop("inputVoltage"), "inputVoltage"),
        (lambda s: s["inputVoltage"].pop("minimum"), "min"),
        (lambda s: s.pop("desiredInductance"), "desiredInductance"),
        (lambda s: s.pop("operatingPoints"), "operatingPoints"),
        (lambda s: s["operatingPoints"][0].pop("switchingFrequency"), "switchingFrequency"),
        (lambda s: s["operatingPoints"][0].update({"outputCurrents": []}), "outputCurrents"),
    ])
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
            "name": "L1", "category": "magnetic",
        }
        with pytest.raises(EnrichmentError, match="no MAS"):
            enrich_tas_for_realism(tas, topology="buck", spec=_buck_spec())

    @pytest.mark.parametrize("path,value", [
        (("core", "processedDescription", "effectiveParameters", "effectiveArea"), 0.0),
        (("coil", "functionalDescription", 0, "numberTurns"), 0),
    ])
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
        tas["topology"]["stages"][0]["circuit"]["components"][2]["mas"]["core"]["functionalDescription"][
            "material"]["saturation"] = []
        with pytest.raises(EnrichmentError, match="saturation"):
            enrich_tas_for_realism(tas, topology="buck", spec=_buck_spec())

    def test_negative_bsat_throws(self):
        tas = _buck_tas()
        tas["topology"]["stages"][0]["circuit"]["components"][2]["mas"]["core"]["functionalDescription"][
            "material"]["saturation"][0]["magneticFluxDensity"] = -0.1
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
        # ``cuk`` has no extractor registered yet, so the dispatcher must
        # leave the TAS structurally untouched (deep-copied) rather than
        # raising or silently stamping bogus fields.
        out = enrich_tas_for_realism(tas, topology="cuk", spec=_buck_spec())
        # No enrichment for cuk yet — should be a structural deep copy
        # of the input (no ``duty`` stamped, no ``isat`` on L1).
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


def test_real_buck_enrichment_flips_verdict_to_pass(tmp_path):
    """End-to-end: load the real buck output produced by ``heaviside design
    buck``, enrich it, and verify the realism gate flips from INCOMPLETE
    (today's bare-pipeline verdict) to PASS.  Both real checks (duty
    cycle, Isat margin) must run with sane numbers.
    """
    pytest.importorskip("PyOpenMagnetics")
    import json
    from pathlib import Path

    out_fp = Path("/tmp/buck_out.tas.json")
    spec_fp = Path("/tmp/buck_spec.json")
    if not (out_fp.is_file() and spec_fp.is_file()):
        pytest.skip("regen with `heaviside design buck --spec /tmp/buck_spec.json -o /tmp/buck_out.tas.json`")

    tas = json.loads(out_fp.read_text())
    spec = json.loads(spec_fp.read_text())

    enriched = enrich_tas_for_realism(tas, topology="buck", spec=spec)
    r = evaluate_tas(enriched, topology="buck", spec=spec)
    assert r.verdict is RealismVerdict.PASS
    passed = {c.name for c in r.checks if c.status is CheckStatus.PASS}
    assert "duty_cycle_bounds" in passed
    assert "inductor_isat_margin" in passed
    # Sanity: Isat ratio should sit comfortably above 1.2.
    isat = next(c for c in r.checks if c.name == "inductor_isat_margin")
    assert isat.value is not None and isat.value > 1.5
