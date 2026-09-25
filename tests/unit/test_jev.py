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
          "_tas_candidates": [{"mpn": "885012206095", "capacitance": 1e-7, "package": "0603",
                               "kelvin": {"status": "recommended", "grade": "drop_in"}},
                              {"mpn": "885012206102", "capacitance": 1e-7, "package": "0603",
                               "kelvin": {"status": "partial", "grade": "minor_review",
                                          "notes": ["X5R vs X7R"]}}]}


def test_crossref_pick_returns_the_chosen_candidate(fake_jev) -> None:
    from heaviside.pipeline.jev_decisions import jev_crossref_row

    calls, script = fake_jev
    script["pick"] = "c1"
    row = jev_crossref_row(_ENTRY, "Würth Elektronik")
    # Jev chose; the status is Kelvin's verdict for the chosen part.
    assert row["substitute_pn"] == "885012206102" and row["status"] == "partial"
    assert "X5R vs X7R" in row["notes"]
    assert "100nF" in str(calls[0]["questions"]["pick"]["criteria"]["c0"])  # SI shown readable
    assert len(calls) == 1  # no second (status) question


def test_crossref_pick_refuses_an_unranked_candidate(fake_jev) -> None:
    from heaviside.pipeline.jev_decisions import jev_crossref_row

    _, script = fake_jev
    script["pick"] = "c0"
    entry = {**_ENTRY, "_tas_candidates": [{"mpn": "X", "capacitance": 1e-7}]}
    with pytest.raises(JevError, match="Kelvin verdict"):
        jev_crossref_row(entry, "Würth Elektronik")


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


def _rescue_state():
    from heaviside.pipeline.crossref import CrossRefState

    return CrossRefState(source_bom=[{**_NOSUB, "value": "22uF"}], target_manufacturer="W")


def test_broadened_rescue_takes_kelvins_verdict(fake_jev, monkeypatch: pytest.MonkeyPatch) -> None:
    from heaviside.pipeline import crossref_pipeline as cp
    from heaviside.pipeline import kelvin_rank
    from heaviside.pipeline.jev_decisions import jev_broadened_rescue

    _, script = fake_jev
    script["pick"] = "c0"
    env = {"x": 1}
    monkeypatch.setattr(cp, "_target_manufacturer_envelopes", lambda *a, **k: [env])
    monkeypatch.setattr(kelvin_rank, "rank", lambda *a, **k: (
        [env], {"885012107014": {"status": "partial", "grade": "minor_review"}}))
    monkeypatch.setattr(cp, "_candidate_summaries_for_llm", lambda *a, **k: [
        {"mpn": "885012107014", "kelvin": {"status": "partial", "grade": "minor_review"}}])
    st = _rescue_state()
    row = jev_broadened_rescue(dict(_NOSUB), st, {})
    assert row is not None and row["status"] == "partial" and row["substitute_pn"] == "885012107014"
    assert st.kelvin_verdicts["C9"]["885012107014"]["status"] == "partial"


def test_broadened_rescue_none_leaves_the_row(fake_jev, monkeypatch: pytest.MonkeyPatch) -> None:
    from heaviside.pipeline import crossref_pipeline as cp
    from heaviside.pipeline import kelvin_rank
    from heaviside.pipeline.jev_decisions import jev_broadened_rescue

    _, script = fake_jev
    script["pick"] = "none"
    monkeypatch.setattr(cp, "_target_manufacturer_envelopes", lambda *a, **k: [{}])
    monkeypatch.setattr(kelvin_rank, "rank", lambda *a, **k: ([{}], {"A": {"status": "partial"}}))
    monkeypatch.setattr(cp, "_candidate_summaries_for_llm", lambda *a, **k: [
        {"mpn": "A", "kelvin": {"status": "partial"}}])
    assert jev_broadened_rescue(dict(_NOSUB), _rescue_state(), {}) is None


def test_broadened_rescue_with_nothing_kelvin_accepts(fake_jev, monkeypatch: pytest.MonkeyPatch) -> None:
    from heaviside.pipeline import crossref_pipeline as cp
    from heaviside.pipeline import kelvin_rank
    from heaviside.pipeline.jev_decisions import jev_broadened_rescue

    calls, _ = fake_jev
    monkeypatch.setattr(cp, "_target_manufacturer_envelopes", lambda *a, **k: [{}])
    monkeypatch.setattr(kelvin_rank, "rank", lambda *a, **k: ([], {}))
    assert jev_broadened_rescue(dict(_NOSUB), _rescue_state(), {}) is None
    assert calls == []  # nothing to choose from: Jev is not asked


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


# ── Stage 3b: correction re-pick ────────────────────────────────────────────

def _correction_state(cands: bool):
    from heaviside.pipeline.crossref import CrossRefState

    row = {"ref_des": "C1", "component_type": "capacitor", "original_pn": "ORIG",
           "substitute_pn": "BAD", "status": "recommended", "notes": ""}
    st = CrossRefState(source_bom=[{"ref_des": "C1", "value": "100nF"}],
                       target_manufacturer="W", crossref_result=[row])
    if cands:
        st.candidates_by_ref["C1"] = [{"x": 1}]
    return st


def test_correction_repicks_with_the_objection_in_state(fake_jev, monkeypatch: pytest.MonkeyPatch) -> None:
    from heaviside.pipeline import crossref_pipeline as cp

    calls, script = fake_jev
    script["pick"] = "c0"
    monkeypatch.setattr(cp, "_candidate_summaries_for_llm", lambda *a, **k: [
        {"mpn": "GOOD", "kelvin": {"status": "recommended", "grade": "drop_in"}}])
    monkeypatch.setattr(cp, "call_agent_json", lambda *a, **k: pytest.fail("must not call Kimi"))
    st = cp._stage3b_correct(_correction_state(True), ["C1: voltage rating too low"])
    assert st.crossref_result[0]["substitute_pn"] == "GOOD"
    assert calls[0]["state"]["reviewer_objections"] == ["C1: voltage rating too low"]
    assert calls[0]["state"]["rejected_substitute"] == "BAD"


def test_correction_to_none_clears_the_rejected_part(fake_jev, monkeypatch: pytest.MonkeyPatch) -> None:
    from heaviside.pipeline import crossref_pipeline as cp

    _, script = fake_jev
    script["pick"] = "none"
    monkeypatch.setattr(cp, "_candidate_summaries_for_llm", lambda *a, **k: [
        {"mpn": "X", "kelvin": {"status": "partial"}}])
    st = cp._stage3b_correct(_correction_state(True), ["C1: wrong value"])
    row = st.crossref_result[0]
    assert row["status"] == "no_substitute" and row["substitute_pn"] is None


# ── Stage 7 gate eligibility ────────────────────────────────────────────────

def _gate_state(rows, verdicts=()):
    from heaviside.pipeline.crossref import CrossRefState

    st = CrossRefState(source_bom=[], target_manufacturer="W", crossref_result=rows)
    st.review_verdicts.extend(verdicts)
    return st


def _full_row(ref: str) -> dict[str, Any]:
    return {"ref_des": ref, "component_type": "resistor", "original_pn": "O", "substitute_pn": "S",
            "status": "recommended", "original_value": "10k", "substitute_value": "10k",
            "original_package": "0603", "substitute_package": "0603"}


@pytest.fixture
def gate_env(fake_jev, monkeypatch: pytest.MonkeyPatch):
    from heaviside.pipeline import crossref_pipeline as cp

    monkeypatch.setattr(cp, "_ground_row_fields_in_catalogue", lambda s: None)
    monkeypatch.setattr(cp, "_stage_param_check", lambda s: None)
    calls, script = fake_jev
    for i in range(8):
        script[f"p{i}"] = 0.01
    return cp, calls


def test_gate_clears_a_fully_comparable_row(gate_env) -> None:
    cp, _ = gate_env
    cleared, rec = cp._jev_review_gate(_gate_state([_full_row("R1")]))
    assert cleared == {"R1"}


def test_gate_sends_one_sided_rows_to_ray(gate_env) -> None:
    cp, calls = gate_env
    row = {**_full_row("R1"), "original_package": ""}  # original package unknown
    cleared, rec = cp._jev_review_gate(_gate_state([row]))
    assert cleared == set() and calls == []
    assert "package" in rec["rows"]["R1"]["sent_to_ray_because"]


def test_gate_never_answers_a_ray_objection(gate_env) -> None:
    cp, calls = gate_env
    ray = {"reviewer": "ray", "verdict": "REJECTED", "objections": ["R1: TCR unknown"]}
    cleared, rec = cp._jev_review_gate(_gate_state([_full_row("R1"), _full_row("R2")], [ray]))
    assert cleared == {"R2"}
    assert rec["rows"]["R1"]["sent_to_ray_because"].startswith("objected to by Ray")


def test_correction_can_keep_the_current_substitute(fake_jev, monkeypatch: pytest.MonkeyPatch) -> None:
    """trap C1: Ray objected, and the re-pick had no way to say "the current part
    is fine" — it swapped an exact 0402 drop-in for a larger 0603. Keeping it is
    an option now, and keeping changes nothing."""
    from heaviside.pipeline import crossref_pipeline as cp

    calls, script = fake_jev
    script["pick"] = "keep"
    monkeypatch.setattr(cp, "_candidate_summaries_for_llm", lambda *a, **k: [
        {"mpn": "BAD", "kelvin": {"status": "recommended", "grade": "drop_in"}},
        {"mpn": "BIGGER", "kelvin": {"status": "partial", "grade": "minor_review"}}])
    st = _correction_state(True)
    st = cp._stage3b_correct(st, ["C1: dielectric code unverified"])
    row = st.crossref_result[0]
    assert row["substitute_pn"] == "BAD" and row["status"] == "recommended"
    assert "keep" in calls[0]["questions"]["pick"]["criteria"]
