"""Integration tests for RE and CR pipelines with mock LLM responses.

These tests replace the real LLM call with deterministic mock responses
so the full pipeline logic (spec extraction, MPN verification, guardrails,
match scoring, verdict parsing) is exercised without API credentials.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from heaviside.pipeline.crossref import SubstitutionStatus
from heaviside.pipeline.re_state import ReferenceSpec
from heaviside.pipeline.value_parse import (
    parse_capacitance,
    parse_inductance,
    parse_resistance,
    parse_voltage,
)
from heaviside.pipeline.verdict import parse_verdict

# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------


class TestValueParse:
    def test_capacitance_uf(self) -> None:
        assert abs(parse_capacitance("22uF") - 22e-6) < 1e-12

    def test_capacitance_nf(self) -> None:
        assert abs(parse_capacitance("100nF") - 100e-9) < 1e-15

    def test_capacitance_pf(self) -> None:
        assert abs(parse_capacitance("47pF") - 47e-12) < 1e-18

    def test_capacitance_scientific(self) -> None:
        assert abs(parse_capacitance("4.7e-06") - 4.7e-6) < 1e-12

    def test_inductance_uh(self) -> None:
        assert abs(parse_inductance("4.7uH") - 4.7e-6) < 1e-12

    def test_resistance_eia_r(self) -> None:
        assert abs(parse_resistance("4R7") - 4.7) < 1e-6

    def test_resistance_kohm(self) -> None:
        assert abs(parse_resistance("10k") - 10e3) < 1

    def test_voltage_plain(self) -> None:
        assert abs(parse_voltage("48V") - 48.0) < 0.01

    def test_unparseable_returns_zero(self) -> None:
        assert parse_capacitance("N/A") == 0.0
        assert parse_inductance("") == 0.0


# ---------------------------------------------------------------------------
# Verdict parsing
# ---------------------------------------------------------------------------


class TestVerdictParse:
    def test_xml_tag(self) -> None:
        assert parse_verdict("<verdict>APPROVED</verdict>") == "APPROVED"

    def test_xml_rejected(self) -> None:
        assert parse_verdict("<verdict>REJECTED</verdict>") == "REJECTED"

    def test_keyword_approved(self) -> None:
        assert parse_verdict("The design is APPROVED.") == "APPROVED"

    def test_keyword_rejected(self) -> None:
        assert parse_verdict("DESIGN REJECTED due to Vds.") == "REJECTED"

    def test_unknown(self) -> None:
        assert parse_verdict("I need more information.") == "UNKNOWN"

    def test_empty(self) -> None:
        assert parse_verdict("") == "UNKNOWN"


# ---------------------------------------------------------------------------
# ReferenceSpec → Heaviside spec
# ---------------------------------------------------------------------------


class TestReferenceSpec:
    def test_to_heaviside_spec(self) -> None:
        ref = ReferenceSpec(
            topology="flyback",
            vin_min=85,
            vin_nom=230,
            vin_max=265,
            vout=20,
            iout=3.25,
            pout=65,
            fsw=65000,
            efficiency_target=0.93,
            isolation_required=True,
            turns_ratio=5.0,
        )
        spec = ref.to_heaviside_spec()
        assert spec["inputVoltage"]["minimum"] == 85
        assert spec["inputVoltage"]["maximum"] == 265
        assert spec["operatingPoints"][0]["outputVoltages"] == [20]
        assert spec["operatingPoints"][0]["outputCurrents"] == [3.25]
        assert spec["efficiency"] == 0.93
        assert spec["desiredTurnsRatios"] == [5.0]

    def test_no_optional_fields(self) -> None:
        ref = ReferenceSpec(
            topology="buck",
            vin_min=36,
            vin_nom=48,
            vin_max=60,
            vout=12,
            iout=5,
            pout=60,
            fsw=200000,
        )
        spec = ref.to_heaviside_spec()
        assert spec["efficiency"] == 0.9
        assert "desiredTurnsRatios" not in spec


# ---------------------------------------------------------------------------
# RE pipeline with mock LLM
# ---------------------------------------------------------------------------


_MOCK_COMPETITOR_RESPONSE = json.dumps(
    {
        "specs": {
            "topology": "buck",
            "vin_min": 36,
            "vin_max": 60,
            "outputs": [{"voltage": 12, "current": 5, "power": 60}],
            "switching_frequency": 200000,
        },
        "performance": {"efficiency": 0.92, "efficiency_type": "measured"},
    }
)

_MOCK_RE_RESPONSE = json.dumps(
    {
        "topology": "buck",
        "specs": {
            "topology": "buck",
            "vin_min": 36,
            "vin_max": 60,
            "vout": 12,
            "iout": 5,
            "pout": 60,
            "fsw": 200000,
        },
        "bom": [
            {
                "ref_des": "Q1",
                "role": "primarySwitch",
                "mpn": "CSD19536KTT",
                "category": "mosfet",
                "package": "TO-220",
                "value": "",
            },
            {
                "ref_des": "D1",
                "role": "freewheelDiode",
                "mpn": "SS34",
                "category": "diode",
                "package": "DO-214AB",
                "value": "",
            },
        ],
    }
)

_MOCK_REVIEWER_RESPONSE = json.dumps(
    {
        "verdict": "APPROVED",
        "objections": [],
        "warnings": ["Q1 Vds margin tight at 1.55x"],
        "summary": "Design acceptable.",
    }
)


class TestCREPipelineMock:
    """Test the RE pipeline with mocked LLM calls."""

    def test_re_pipeline_extracts_spec(self) -> None:
        from heaviside.pipeline.re_pipeline import _stage1_spec_extract
        from heaviside.pipeline.re_state import REState

        state = REState(reference="test-buck-60W")

        with patch("heaviside.pipeline.re_pipeline.call_agent_json") as mock:
            mock.return_value = json.loads(_MOCK_COMPETITOR_RESPONSE)
            state = _stage1_spec_extract(state)

        assert state.ref_spec is not None
        assert state.ref_spec.topology == "buck"
        assert state.ref_spec.vout == 12.0
        assert state.ref_spec.pout == 60.0

    def test_re_pipeline_extracts_bom(self) -> None:
        from heaviside.pipeline.re_pipeline import _stage2_reverse_engineer
        from heaviside.pipeline.re_state import REState

        state = REState(reference="test-buck-60W")

        with patch("heaviside.pipeline.re_pipeline.call_agent_json") as mock:
            mock.return_value = json.loads(_MOCK_RE_RESPONSE)
            state = _stage2_reverse_engineer(state)

        assert len(state.ref_bom) == 2
        assert state.ref_bom[0]["mpn"] == "CSD19536KTT"


# ---------------------------------------------------------------------------
# Crossref pipeline with mock LLM
# ---------------------------------------------------------------------------


_MOCK_CROSSREF_RESPONSE = json.dumps(
    {
        "crossref": [
            {
                "ref_des": "Q1",
                "component_type": "mosfet",
                "original_pn": "CSD19536KTT",
                "original_value": "",
                "original_voltage": "100V",
                "original_package": "TO-220",
                "substitute_pn": "IPB033N10N5",
                "substitute_value": "",
                "substitute_voltage": "100V",
                "substitute_package": "TO-263",
                "status": "recommended",
                "notes": "Lower Rds_on",
            },
        ],
    }
)


class TestCrossRefPipelineMock:
    """Test crossref stages with mocked LLM calls."""

    def test_preclassify_keeps_original(self) -> None:
        from heaviside.pipeline.crossref import CrossRefState
        from heaviside.pipeline.crossref_pipeline import _stage2_preclassify

        state = CrossRefState(
            source_bom=[
                {"ref_des": "C1", "manufacturer": "Wurth", "component_type": "capacitor"},
                {"ref_des": "Q1", "manufacturer": "TI", "component_type": "mosfet"},
            ],
            target_manufacturer="Wurth",
        )
        state = _stage2_preclassify(state)
        assert "C1" in state.preclassified
        assert "Q1" not in state.preclassified

    def test_crossref_stage3_calls_llm(self) -> None:
        from heaviside.pipeline.crossref import CrossRefState
        from heaviside.pipeline.crossref_pipeline import _stage3_crossref

        state = CrossRefState(
            source_bom=[
                {
                    "ref_des": "Q1",
                    "component_type": "mosfet",
                    "mpn": "CSD19536KTT",
                    "manufacturer": "TI",
                },
            ],
            target_manufacturer="Infineon",
        )

        with patch("heaviside.pipeline.crossref_pipeline.call_agent_json") as mock:
            mock.return_value = json.loads(_MOCK_CROSSREF_RESPONSE)
            state = _stage3_crossref(state)

        assert len(state.crossref_result) == 1
        assert state.crossref_result[0]["substitute_pn"] == "IPB033N10N5"

    def test_outcome_from_state(self) -> None:
        from heaviside.pipeline.crossref import CrossRefOutcome, CrossRefState

        state = CrossRefState(
            source_bom=[{"ref_des": "Q1"}],
            target_manufacturer="Infineon",
            crossref_result=[
                {
                    "ref_des": "Q1",
                    "component_type": "mosfet",
                    "original_pn": "X",
                    "original_value": "",
                    "original_voltage": "100V",
                    "original_package": "TO-220",
                    "substitute_pn": "Y",
                    "substitute_value": "",
                    "substitute_voltage": "100V",
                    "substitute_package": "TO-220",
                    "status": "recommended",
                    "notes": "",
                }
            ],
            passed=True,
        )
        outcome = CrossRefOutcome.from_state(state)
        assert len(outcome.components) == 1
        assert outcome.components[0].status == SubstitutionStatus.RECOMMENDED
