"""Jev decision client and the pipeline decisions routed through it.

The HTTP endpoint is replaced by a fake that answers from a script, so these
run offline and pin what each decision sends and how it reads the answers.
"""

from __future__ import annotations

from typing import Any

import pytest

from heaviside.llm import jev
from heaviside.llm.jev import JevError


@pytest.fixture
def fake_jev(monkeypatch: pytest.MonkeyPatch):
    """Route jev._post to a scripted answerer; record every request body."""
    calls: list[dict[str, Any]] = []
    script: dict[str, Any] = {}

    def post(body: dict[str, Any], **_: Any) -> dict[str, Any]:
        calls.append(body)
        answers = {}
        for qid, q in body["questions"].items():
            ans = script.get(qid)
            if callable(ans):
                ans = ans(body, q)
            if ans is None:
                continue
            if q["type"] == "choice":
                answers[qid] = {"type": "choice", "choice": ans, "probabilities": {ans: 0.9},
                                "confidence": 0.9}
            else:
                answers[qid] = {"type": "noul", "noul": ans}
        return {"answers": answers, "usage": {"input_tokens": 10, "cost": 1e-6}}

    monkeypatch.setattr(jev, "_post", post)
    monkeypatch.setenv("HEAVISIDE_JEV", "1")
    return calls, script


def test_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(JevError, match="OPENROUTER_API_KEY"):
        jev._post({"model": "x", "state": "s", "questions": {}})


def test_unanswered_question_raises(fake_jev) -> None:
    _, script = fake_jev
    script["a"] = 0.1  # "b" left unanswered
    with pytest.raises(JevError, match="no answer"):
        jev.decide("s", {"a": jev.noul_question("A?"), "b": jev.noul_question("B?")})


def test_choice_outside_options_raises(fake_jev) -> None:
    _, script = fake_jev
    script["q"] = "invented"
    with pytest.raises(JevError, match="not one of"):
        jev.decide_choice("s", "pick", {"x": "X", "y": "Y"})


def test_noul_needs_both_sides() -> None:
    with pytest.raises(JevError):
        jev.noul_question("Q?", true="yes")


def test_oversized_state_raises(fake_jev) -> None:
    with pytest.raises(JevError, match="32k"):
        jev.decide("x" * 200_000, {"a": jev.noul_question("A?")})


def test_many_questions_are_chunked(fake_jev) -> None:
    calls, script = fake_jev
    qs = {f"q{i}": jev.noul_question(f"Q{i}?") for i in range(130)}
    for k in qs:
        script[k] = 0.2
    out = jev.decide("s", qs)
    assert len(out) == 130 and len(calls) == 3


# ── Stage 7 gate ────────────────────────────────────────────────────────────

def test_review_gate_clears_only_when_every_parameter_matches(fake_jev) -> None:
    from heaviside.pipeline.jev_decisions import REVIEW_GATE_THRESHOLD, jev_review_gate

    _, script = fake_jev
    params = [{"name": "value", "original": "10uF", "substitute": "10µF", "verdict": "exact"},
              {"name": "voltage", "original": "25V", "substitute": "16V", "verdict": "lower"}]
    script["p0"], script["p1"] = 0.05, REVIEW_GATE_THRESHOLD + 0.01
    cleared, probs = jev_review_gate({"ref_des": "C1"}, params)
    assert not cleared and probs == {"value": 0.05, "voltage": REVIEW_GATE_THRESHOLD + 0.01}
    script["p1"] = REVIEW_GATE_THRESHOLD
    assert jev_review_gate({"ref_des": "C1"}, params)[0]


def test_review_gate_hides_deterministic_verdicts(fake_jev) -> None:
    from heaviside.pipeline.jev_decisions import jev_review_gate

    calls, script = fake_jev
    script["p0"] = 0.0
    jev_review_gate({"ref_des": "R1"}, [{"name": "value", "original": "10k",
                                         "substitute": "10k", "verdict": "exact"}])
    assert "verdict" not in str(calls[-1]["state"])


def test_review_gate_never_clears_a_row_with_nothing_compared(fake_jev) -> None:
    from heaviside.pipeline.jev_decisions import jev_review_gate

    assert jev_review_gate({"ref_des": "U1"}, []) == (False, {})


# ── Stage 3 pick ────────────────────────────────────────────────────────────

_ENTRY = {"ref_des": "C1", "component_type": "capacitor", "original_mpn": "GRM188R71H104KA93D",
          "value": "100nF", "voltage": "50V", "package": "0603",
          "_tas_candidates": [{"mpn": "885012206095", "capacitance": 1e-7, "package": "0603"},
                              {"mpn": "885012206102", "capacitance": 1e-7, "package": "0603"}]}


def test_crossref_pick_returns_the_chosen_candidate(fake_jev) -> None:
    from heaviside.pipeline.jev_decisions import jev_crossref_row

    calls, script = fake_jev
    script["pick"], script["status"] = "c1", "recommended"
    row = jev_crossref_row(_ENTRY, "Würth Elektronik")
    assert row["substitute_pn"] == "885012206102" and row["status"] == "recommended"
    assert "100nF" in str(calls[0]["questions"]["pick"]["criteria"]["c0"])  # SI shown readable


def test_crossref_pick_none_is_no_substitute(fake_jev) -> None:
    from heaviside.pipeline.jev_decisions import jev_crossref_row

    _, script = fake_jev
    script["pick"] = "none"
    row = jev_crossref_row(_ENTRY, "Würth Elektronik")
    assert row["status"] == "no_substitute" and row["substitute_pn"] is None


def test_crossref_pick_needs_candidates(fake_jev) -> None:
    from heaviside.pipeline.jev_decisions import jev_crossref_row

    with pytest.raises(JevError):
        jev_crossref_row({"ref_des": "U1"}, "Würth Elektronik")


def test_stage3_splits_between_jev_and_kimi(fake_jev, monkeypatch: pytest.MonkeyPatch) -> None:
    from heaviside.pipeline import crossref_pipeline as cp
    from heaviside.pipeline.crossref import CrossRefState

    _, script = fake_jev
    script["pick"], script["status"] = "c0", "exact"
    sent: list[str] = []

    def fake_kimi(agent: str, msg: str, **_: Any) -> dict[str, Any]:
        import json

        batch = json.loads(msg)["source_bom"]
        sent.extend(e["ref_des"] for e in batch)
        return {"crossref": [{"ref_des": e["ref_des"], "status": "no_substitute"} for e in batch]}

    monkeypatch.setattr(cp, "call_agent_json", fake_kimi)
    state = CrossRefState(source_bom=[], target_manufacturer="Würth Elektronik")
    rows, failed = cp._run_crossref_batches(state, [_ENTRY, {"ref_des": "U7"}])
    assert sent == ["U7"] and failed == 0
    assert {r["ref_des"] for r in rows} == {"C1", "U7"}


def test_jev_off_sends_everything_to_kimi(monkeypatch: pytest.MonkeyPatch) -> None:
    from heaviside.pipeline import crossref_pipeline as cp
    from heaviside.pipeline.crossref import CrossRefState

    monkeypatch.setenv("HEAVISIDE_JEV", "0")
    sent: list[str] = []

    def fake_kimi(agent: str, msg: str, **_: Any) -> dict[str, Any]:
        import json

        sent.extend(e["ref_des"] for e in json.loads(msg)["source_bom"])
        return {"crossref": []}

    monkeypatch.setattr(cp, "call_agent_json", fake_kimi)
    cp._run_crossref_batches(CrossRefState(source_bom=[], target_manufacturer="W"), [_ENTRY])
    assert sent == ["C1"]


# ── BOM header mapping ──────────────────────────────────────────────────────

def test_header_mapper_names_only_real_columns(fake_jev) -> None:
    from heaviside.pipeline.bom_import import _jev_header_overrides

    _, script = fake_jev
    headers = ["WW_PN", " MFG_PN", "LOCATION"]
    for f in ("manufacturer", "description", "value", "rated_voltage", "quantity",
              "component_type", "notes"):
        script[f] = "none"
    script["original_mpn"], script["ref_des"] = "h1", "h2"
    out = _jev_header_overrides(headers, [["1", "GRM188", "C1"]])
    assert out == {"original_mpn": " MFG_PN", "ref_des": "LOCATION"}


def test_header_mapper_does_not_reuse_a_column(fake_jev) -> None:
    from heaviside.pipeline.bom_import import _jev_header_overrides

    _, script = fake_jev
    for f in ("manufacturer", "ref_des", "value", "rated_voltage", "quantity", "notes"):
        script[f] = "none"
    script["original_mpn"] = script["description"] = script["component_type"] = "h0"
    out = _jev_header_overrides(["MPN"], [["X1"]])
    assert out == {"original_mpn": "MPN"}


# ── topology selector ───────────────────────────────────────────────────────

def test_topology_selector_ranks_viable_by_probability(fake_jev) -> None:
    from heaviside.agents.topology_selector_llm import _TOPOLOGY_NICHES, _jev_topology_selector

    _, script = fake_jev
    for name in _TOPOLOGY_NICHES:
        script[name] = 0.1
    script["buck"], script["flyback"], script["sepic"] = 0.9, 0.6, 0.95
    names, _ = _jev_topology_selector({"operatingPoints": [{"outputVoltages": [5],
                                                              "outputCurrents": [2]}]})
    assert names == ["sepic", "buck", "flyback"]


# ── Stage 6: broadened search + Jev before Otto ─────────────────────────────

_NOSUB = {"ref_des": "C9", "component_type": "capacitor", "original_pn": "X", "status": "no_substitute",
          "original_value": "22uF", "original_voltage": "25V", "original_package": "0805"}


def test_broadened_rescue_marks_the_pick_partial(fake_jev, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    import heaviside.agents.tools as tools
    from heaviside.pipeline.jev_decisions import jev_broadened_rescue

    _, script = fake_jev
    script["pick"], script["status"] = "c0", "recommended"
    seen: dict[str, Any] = {}

    def fake_search(cat: str, target: str, **kw: Any) -> str:
        seen.update(kw, cat=cat)
        return json.dumps({"candidates": [{"mpn": "885012107014", "capacitance": 2.2e-5}]})

    monkeypatch.setattr(tools, "_crossref_search_impl", fake_search)
    row = jev_broadened_rescue(dict(_NOSUB), "Würth Elektronik")
    assert row is not None and row["status"] == "partial" and row["substitute_pn"] == "885012107014"
    assert seen["value_tolerance_pct"] == 20.0 and seen["min_voltage"] == 25.0
    assert abs(seen["value"] - 2.2e-5) < 1e-12


def test_broadened_rescue_none_leaves_the_row(fake_jev, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    import heaviside.agents.tools as tools
    from heaviside.pipeline.jev_decisions import jev_broadened_rescue

    _, script = fake_jev
    script["pick"] = "none"
    monkeypatch.setattr(tools, "_crossref_search_impl",
                        lambda *a, **k: json.dumps({"candidates": [{"mpn": "A"}]}))
    assert jev_broadened_rescue(dict(_NOSUB), "Würth Elektronik") is None


def test_broadened_rescue_skips_rows_without_a_value(fake_jev) -> None:
    from heaviside.pipeline.jev_decisions import jev_broadened_rescue

    assert jev_broadened_rescue({**_NOSUB, "original_value": ""}, "W") is None
    assert jev_broadened_rescue({**_NOSUB, "component_type": "connector"}, "W") is None


def test_otto_only_sees_rows_the_rescue_left(fake_jev, monkeypatch: pytest.MonkeyPatch) -> None:
    from heaviside.pipeline import crossref_pipeline as cp
    from heaviside.pipeline import jev_decisions as jd
    from heaviside.pipeline.crossref import CrossRefState

    rows = [dict(_NOSUB, ref_des="C1"), dict(_NOSUB, ref_des="C2")]
    monkeypatch.setattr(cp, "_stage6_5_deterministic_rescue", lambda s: s)
    monkeypatch.setattr(jd, "jev_broadened_rescue",
                        lambda row, *a, **k: ({**row, "status": "partial", "substitute_pn": "P"}
                                              if row["ref_des"] == "C1" else None))
    monkeypatch.setattr(jd, "jev_otto_triage", lambda rows, target: rows)
    sent: list[str] = []

    def fake_otto(name: str, msg: str, **kw: Any) -> str:
        import json

        sent.extend(i["ref_des"] for i in json.loads(msg)["no_substitute_items"])
        return '{"challenges": []}'

    monkeypatch.setattr(cp, "call_agent", fake_otto)
    state = CrossRefState(source_bom=[], target_manufacturer="W", crossref_result=rows)
    cp._stage6_otto(state)
    assert sent == ["C2"]
    assert state.otto_log["jev_rescued_refs"] == ["C1"]
