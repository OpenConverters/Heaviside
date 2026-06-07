"""Regression: Heaviside CR pipeline vs Proteus 10-design Würth crossref.

Runs the deterministic stages (prefetch, preclassify, guardrails) on
each of the 10 Proteus golden BOMs. No LLM calls — the LLM crossref
stage is tested separately with mocked responses or live API.

Golden data at:
  /home/alf/OpenConverters/Proteus/tests/reference_designs/crossref_wurth/

Each design has:
  bom_full.json       — full BOM (all components)
  bom_wurth.json      — BOM filtered to Würth-addressable passives
  stage3_crossref.json — Proteus crossref result (raw LLM text)
  report.md           — Proteus human-readable report (unique format per design)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

_PROTEUS_DIR = Path("/home/alf/OpenConverters/Proteus/tests/reference_designs/crossref_wurth")

_DESIGNS = sorted(
    [d for d in _PROTEUS_DIR.iterdir() if d.is_dir() and (d / "bom_full.json").exists()]
)


def _load_bom(design_dir: Path) -> list[dict[str, Any]]:
    """Load and normalize a Proteus BOM to Heaviside field names."""
    raw = json.loads((design_dir / "bom_full.json").read_text())
    cat_map = {
        "capacitor": "capacitor",
        "inductor": "magnetic",
        "mosfet": "mosfet",
        "diode": "diode",
        "resistor": "resistor",
        "ic": "ic",
        "connector": "connector",
        "ferrite_bead": "magnetic",
    }
    return [
        {
            "ref_des": c.get("ref_des", "?"),
            "component_type": cat_map.get(c.get("type", ""), c.get("type", "")),
            "mpn": c.get("part", ""),
            "manufacturer": c.get("manufacturer", ""),
            "value": c.get("value", ""),
            "voltage": str(c.get("rated_voltage", "")),
            "package": c.get("package", ""),
        }
        for c in raw
    ]


def _load_wurth_bom(design_dir: Path) -> list[dict[str, Any]]:
    """Load the Proteus Würth-addressable BOM subset."""
    path = design_dir / "bom_wurth.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())


def _proteus_addressable_count(design_dir: Path) -> int:
    """How many components Proteus considered addressable for Würth crossref."""
    return len(_load_wurth_bom(design_dir))


@pytest.fixture(
    params=[d.name for d in _DESIGNS],
    ids=[d.name for d in _DESIGNS],
)
def design(request: pytest.FixtureRequest) -> Path:
    return _PROTEUS_DIR / request.param


# ---------------------------------------------------------------------------
# Deterministic stage tests
# ---------------------------------------------------------------------------


class TestPrefetchFindsWurthCandidates:
    """Stage 1: TAS prefetch should find Würth parts for addressable categories."""

    def test_capacitor_candidates_exist(self, design: Path) -> None:
        from heaviside.pipeline.crossref import CrossRefState
        from heaviside.pipeline.crossref_pipeline import _stage1_prefetch

        bom = _load_bom(design)
        state = CrossRefState(source_bom=bom, target_manufacturer="Wurth")
        state = _stage1_prefetch(state)

        cap_refs = [c["ref_des"] for c in bom if c["component_type"] == "capacitor"]
        if not cap_refs:
            pytest.skip("no capacitors in BOM")

        cap_candidates = sum(len(state.candidates_by_ref.get(r, [])) for r in cap_refs)
        assert cap_candidates > 0, (
            f"{design.name}: 0 Würth capacitor candidates for {len(cap_refs)} caps"
        )

    def test_resistor_candidates_exist(self, design: Path) -> None:
        from heaviside.pipeline.crossref import CrossRefState
        from heaviside.pipeline.crossref_pipeline import _stage1_prefetch

        bom = _load_bom(design)
        state = CrossRefState(source_bom=bom, target_manufacturer="Wurth")
        state = _stage1_prefetch(state)

        res_refs = [c["ref_des"] for c in bom if c["component_type"] == "resistor"]
        if not res_refs:
            pytest.skip("no resistors in BOM")

        res_candidates = sum(len(state.candidates_by_ref.get(r, [])) for r in res_refs)
        assert res_candidates > 0, (
            f"{design.name}: 0 Würth resistor candidates for {len(res_refs)} resistors"
        )


class TestPreclassify:
    """Stage 2: components already from Würth should be detected."""

    def test_wurth_components_detected(self, design: Path) -> None:
        from heaviside.pipeline.crossref import CrossRefState
        from heaviside.pipeline.crossref_pipeline import (
            _normalize_manufacturer,
            _stage2_preclassify,
        )

        bom = _load_bom(design)
        state = CrossRefState(source_bom=bom, target_manufacturer="Wurth")
        state = _stage2_preclassify(state)

        target_norm = _normalize_manufacturer("Wurth")
        expected_wurth = {
            c["ref_des"]
            for c in bom
            if target_norm in _normalize_manufacturer(c.get("manufacturer", ""))
        }
        # Preclassified includes Würth-manufacturer AND not-fitted components
        assert expected_wurth.issubset(set(state.preclassified.keys()))
        for ref in expected_wurth:
            assert "already" in state.preclassified[ref]["reason"].lower()
        for ref, info in state.preclassified.items():
            if ref not in expected_wurth:
                assert "not fitted" in info["reason"].lower()


class TestBOMConsistency:
    """Cross-check BOM loading between Heaviside and Proteus."""

    def test_bom_loads(self, design: Path) -> None:
        bom = _load_bom(design)
        assert len(bom) > 0

    def test_heaviside_sees_proteus_addressable_components(self, design: Path) -> None:
        """Heaviside's BOM should include at least as many addressable passives
        as Proteus identified in bom_wurth.json."""
        bom = _load_bom(design)
        addressable_types = {"capacitor", "resistor", "magnetic"}
        h_addressable = sum(1 for c in bom if c["component_type"] in addressable_types)
        p_addressable = _proteus_addressable_count(design)
        if p_addressable == 0:
            pytest.skip("Proteus bom_wurth.json empty or missing")
        assert h_addressable >= p_addressable, (
            f"{design.name}: Heaviside {h_addressable} < Proteus {p_addressable}"
        )


class TestGuardrailsOnProteusBOM:
    """Run guardrails on a synthetic crossref result built from the Proteus BOM.

    This tests that guardrails don't crash on real-world data, not that
    they produce specific outputs (that requires the LLM crossref stage).
    """

    def test_guardrails_dont_crash(self, design: Path) -> None:
        from heaviside.pipeline.guardrails import apply_guardrails

        wurth_bom = _load_wurth_bom(design)
        if not wurth_bom:
            pytest.skip("no wurth BOM")

        # Build a synthetic crossref result (all "recommended")
        crossref = []
        for c in wurth_bom:
            crossref.append(
                {
                    "ref_des": c.get("ref_des", "?"),
                    "component_type": c.get("type", ""),
                    "original_pn": c.get("part", ""),
                    "original_value": c.get("value", ""),
                    "original_voltage": str(c.get("rated_voltage", "")),
                    "original_package": c.get("package", ""),
                    "substitute_pn": "PLACEHOLDER_PN",
                    "substitute_value": c.get("value", ""),
                    "substitute_voltage": str(c.get("rated_voltage", "")),
                    "substitute_package": c.get("package", ""),
                    "status": "recommended",
                    "notes": "",
                }
            )

        corrected, fires = apply_guardrails(
            {"crossref": crossref},
            [{"ref_des": c.get("ref_des"), **c} for c in wurth_bom],
            "Wurth",
        )
        assert isinstance(corrected, dict)
        assert isinstance(fires, list)
