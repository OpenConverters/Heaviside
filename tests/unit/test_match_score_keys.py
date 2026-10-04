"""match_score must read the real row/DB keys, and the score must reach the
outcome.

Three drifts made stage-5 scoring silently dead:
  * ctype read `type` but rows carry `component_type` -> no value branch fired,
    value_pct_delta always None;
  * substitute voltage read `vdsMax`/`vrrm`, which don't exist in TAS (the
    fields are `drainSourceVoltage` / `reverseVoltage`) -> semiconductor
    voltage scoring dead;
  * CrossRefOutcome.from_state never copied `match_score` off the row.
"""

from __future__ import annotations

from heaviside.pipeline.crossref import CrossRefOutcome, CrossRefState
from heaviside.pipeline.match_score import compute_match_score


def _mosfet_env(vds: float) -> dict:
    return {
        "semiconductor": {
            "mosfet": {
                "manufacturerInfo": {
                    "datasheetInfo": {
                        "electrical": {"drainSourceVoltage": vds},
                        "part": {"caseCode": "TO-220"},
                    }
                }
            }
        }
    }


def _cap_env(cap_f: float) -> dict:
    return {
        "capacitor": {
            "manufacturerInfo": {
                "datasheetInfo": {
                    "electrical": {"capacitance": cap_f, "ratedVoltage": 50.0},
                    "part": {"caseCode": "0805"},
                }
            }
        }
    }


def test_mosfet_voltage_scored_from_drainsourcevoltage() -> None:
    comp = {"component_type": "mosfet", "substitute_pn": "SUB-FET"}
    src = {"component_type": "mosfet", "voltage": "60"}
    score = compute_match_score(comp, src, _mosfet_env(100.0))
    # 100 V sub vs 60 V source -> upgrade (was "unknown" when the key was wrong).
    assert score["voltage"] == "upgrade"


def test_capacitor_value_delta_uses_component_type() -> None:
    comp = {"component_type": "capacitor", "substitute_pn": "SUB-CAP"}
    src = {"component_type": "capacitor", "value": "10uF"}
    score = compute_match_score(comp, src, _cap_env(10e-6))
    # Same value -> ~0% delta (was None when ctype was empty).
    assert score["value_pct_delta"] is not None
    assert abs(score["value_pct_delta"]) < 1.0


def test_from_state_carries_match_score() -> None:
    state = CrossRefState(source_bom=[], target_manufacturer="X")
    state.crossref_result = [
        {
            "ref_des": "C1",
            "component_type": "capacitor",
            "status": "recommended",
            "substitute_pn": "SUB-CAP",
            "match_score": {"overall": 0.87, "voltage": "match"},
        }
    ]
    outcome = CrossRefOutcome.from_state(state)
    assert outcome.components[0].match_score == {"overall": 0.87, "voltage": "match"}


def test_scoring_finds_a_part_catalogued_by_part_number_only(tmp_path) -> None:
    """Capacitors and resistors carry their MPN in part.partNumber, not in
    manufacturerInfo.reference. Scoring now reads through the guardrails' index,
    which keys both — its own index keyed `reference` only and scored every such
    substitute as uncatalogued."""
    import json

    from heaviside.pipeline import guardrails
    from heaviside.pipeline.match_score import annotate_match_scores

    env = {"capacitor": {"manufacturerInfo": {"name": "Würth Elektronik", "datasheetInfo": {
        "electrical": {"capacitance": 1e-7, "ratedVoltage": 50.0},
        "part": {"partNumber": "885012206071", "caseCode": "0603"}}}}}
    (tmp_path / "capacitors.ndjson").write_text(json.dumps(env) + "\n")
    rows = [{"ref_des": "C3", "component_type": "capacitor", "substitute_pn": "885012206071"}]
    try:
        annotate_match_scores(rows, [{"ref_des": "C3", "component_type": "capacitor",
                                      "value": "100nF", "voltage": "25"}], tas_data_dir=tmp_path)
    finally:
        guardrails._TAS_INDEX_CACHE.clear(); guardrails._TAS_LOOKUP_CACHE.clear()
    score = rows[0]["match_score"]
    assert score["voltage"] == "upgrade" and abs(score["value_pct_delta"]) < 1.0
