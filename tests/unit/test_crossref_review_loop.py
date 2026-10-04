"""Review / correction-loop behaviour of the CR pipeline (job 48ad52a57d5c).

Prod run 2026-10-04: a 2-line BOM (C1,C2 already WE -> exact; R1 Yageo
RC0603FR-0710KL) spent 8.5 min, three correction loops alternating between two
rejected substitutes, and still returned the rejected 0402 part as
``recommended``. These tests pin the four fixes; every LLM call is mocked.
"""

from __future__ import annotations

import json
import threading

import pytest

from heaviside.pipeline import crossref_pipeline as cp
from heaviside.pipeline.crossref import CrossRefState

_YAGEO_ENV = {"resistor": {"manufacturerInfo": {
    "name": "YAGEO", "reference": "RC0603FR-0710KL",
    "datasheetInfo": {"part": {"partNumber": "RC0603FR-0710KL", "technology": "thickFilm"},
                      "electrical": {"resistance": {"nominal": 10000.0}, "tolerance": 0.01,
                                     "powerRating": 0.1},
                      "mechanical": {"length": {"nominal": 0.0016},
                                     "height": {"nominal": 0.00045}}}}}}


def _we_res(mpn: str, case: str) -> dict:
    length, width = {"0402": (0.001, 0.0005), "0603": (0.0016, 0.0008)}[case]
    return {"resistor": {"manufacturerInfo": {
        "name": "Würth Elektronik", "reference": mpn,
        "datasheetInfo": {"part": {"partNumber": mpn, "case": case},
                          "electrical": {"resistance": {"nominal": 10000.0},
                                         "tolerance": 0.01, "powerRating": 0.1},
                          "mechanical": {"length": {"nominal": length},
                                         "width": {"nominal": width}}}}}}


def _rows(r1_sub: str = "560112110020", r1_status: str = "recommended") -> list[dict]:
    return [
        {"ref_des": "C1, C2", "component_type": "capacitor", "original_pn": "885012205037",
         "substitute_pn": "885012205037", "status": "exact", "notes": "already WE"},
        {"ref_des": "R1", "component_type": "resistor", "original_pn": "RC0603FR-0710KL",
         "substitute_pn": r1_sub, "status": r1_status, "notes": "10k 1% thick film"},
    ]


def _bom(r1_dims=(0.0016, 0.0008, 0.00045)) -> list[dict]:
    return [
        {"ref_des": "C1, C2", "original_mpn": "885012205037", "component_type": "capacitor",
         "_source_dims_m": (0.002, 0.00125, None)},
        {"ref_des": "R1", "original_mpn": "RC0603FR-0710KL", "component_type": "resistor",
         "_source_dims_m": r1_dims, "_source_env": _YAGEO_ENV},
    ]


def _wire_pipeline(monkeypatch, *, bom, review, correct):
    """Replace every stage but the review loop's own control flow and the final
    status gates with deterministic fakes."""
    ident = lambda state, *a, **k: state  # noqa: E731

    for name in ("_stage0_resolve_parts", "_stage1_prefetch", "_stage1_5_librarian",
                 "_stage1_6_fetch_originals", "_stage1_7_reprefetch_late",
                 "_stage1_8_explain_unresolved", "_stage2_preclassify", "_stage4_guardrails",
                 "_stage5_score", "_stage6_otto", "_stage6_5_deterministic_rescue",
                 "_stage_footprint_caveat"):
        monkeypatch.setattr(cp, name, ident)
    for name in ("_stage8_learn", "_ground_row_fields_in_catalogue", "_stage_param_check",
                 "_annotate_match_detail"):
        monkeypatch.setattr(cp, name, lambda state, *a, **k: None)
    monkeypatch.setattr(cp, "_normalize_bom", lambda b: b)

    def stage3(state):
        state.crossref_result = _rows()
        state.candidates_by_ref = {"C1, C2": [{}], "R1": [{}]}
        return state

    monkeypatch.setattr(cp, "_stage3_crossref", stage3)
    monkeypatch.setattr(cp, "_stage7_review", review)
    monkeypatch.setattr(cp, "_stage3b_correct", correct)


class _Reviewer:
    """Ray + Nicola always reject R1; objections vary per round unless fixed."""

    def __init__(self, *, same_objections: bool = False, approve: bool = False):
        self.calls = 0
        self.same = same_objections
        self.approve = approve

    def __call__(self, state, *a, **k):
        self.calls += 1
        tag = "" if self.same else f" (round {self.calls})"
        if self.approve:
            ray = {"reviewer": "ray", "verdict": "APPROVED", "objections": [],
                   "reviewed_refs": ["R1"]}
        else:
            ray = {"reviewer": "ray", "verdict": "REJECTED", "reviewed_refs": ["R1"],
                   "objections": [f"R1 substitute changes package from 0603 to 0402{tag}"]}
        nicola = {"reviewer": "nicola", "verdict": ray["verdict"], "reviewed_refs": ["R1"],
                  "objections": [] if self.approve else [f"R1 footprint unverified{tag}"]}
        state.review_verdicts += [ray, nicola]
        state.passed = self.approve
        return state


def _corrector(sequence):
    it = iter(sequence)

    def correct(state, objections, rejected_by_ref=None, **kw):
        nxt = next(it)
        for row in state.crossref_result:
            if row["ref_des"] == "R1":
                row["substitute_pn"] = nxt
                row["status"] = "recommended"
        return state

    return correct


# --- Fix 1: a rejected row never comes back `recommended` -------------------


def test_rejected_row_is_returned_rejected_not_recommended(monkeypatch):
    review = _Reviewer()
    _wire_pipeline(monkeypatch, bom=_bom(), review=review,
                   correct=_corrector(["WE-B", "WE-C", "WE-D"]))
    out = cp.run_crossref_pipeline(_bom(), "Würth Elektronik")

    r1 = next(c for c in out.components if c.ref_des == "R1")
    assert r1.status.value == "no_substitute", (
        "a row the gating reviewer still rejects after the loop must not be a substitute"
    )
    assert r1.substitute_mpn is None
    assert "REJECTED by the engineering review" in r1.notes
    assert "0603 to 0402" in r1.notes, "the reviewer's objection must travel with the row"
    assert "WE-D" in r1.notes
    assert out.passed is False
    assert out.diagnostics[0].startswith("REVIEW REJECTED"), (
        "the failure must be visible at the top of the result, not only as passed=false"
    )
    c = next(c for c in out.components if c.ref_des == "C1, C2")
    assert c.status.value == "exact", "a row the reviewer did not reject keeps its verdict"


# --- Fix 2: stop a loop that is not making progress -------------------------


def test_loop_stops_when_correction_returns_to_a_rejected_substitute(monkeypatch):
    review = _Reviewer()
    # 560112110020 (round-0 pick) -> 560112116005 -> back to 560112110020: the
    # prod oscillation.
    _wire_pipeline(monkeypatch, bom=_bom(), review=review,
                   correct=_corrector(["560112116005", "560112110020", "560112116005"]))
    out = cp.run_crossref_pipeline(_bom(), "Würth Elektronik")

    assert review.calls == 2, (
        f"re-proposing an already rejected substitute must end the loop without another "
        f"review round; got {review.calls} review rounds"
    )
    assert any("produced no new substitute" in d for d in out.diagnostics)
    r1 = next(c for c in out.components if c.ref_des == "R1")
    assert r1.status.value == "no_substitute"
    assert "560112110020" in r1.notes and "560112116005" in r1.notes


def test_loop_stops_when_objections_repeat_unchanged(monkeypatch):
    review = _Reviewer(same_objections=True)
    _wire_pipeline(monkeypatch, bom=_bom(), review=review,
                   correct=_corrector(["WE-B", "WE-C", "WE-D"]))
    out = cp.run_crossref_pipeline(_bom(), "Würth Elektronik")

    assert review.calls == 2, (
        f"a round whose objections repeat the previous round's must stop the loop; got "
        f"{review.calls} review rounds"
    )
    assert any("repeated the previous round's objections" in d for d in out.diagnostics)


def test_stage3b_never_reapplies_a_rejected_substitute(monkeypatch):
    monkeypatch.setenv("HEAVISIDE_JEV", "0")
    seen_candidates: list[list[str]] = []

    def fake_llm(items, build_payload, **kw):
        payload = build_payload(items)
        for comp in payload["components_to_fix"]:
            seen_candidates.append([c.get("mpn") for c in comp.get("_tas_candidates", [])])
            assert comp["previously_rejected_substitutes"] == ["560112110020"]
        assert "previously_rejected_substitutes" in payload["instructions"]
        return ([{"ref_des": "R1", "substitute_pn": "560112110020",
                  "status": "recommended", "notes": "back to the 0402"}], [])

    monkeypatch.setattr(cp, "_crossref_llm_batched", fake_llm)
    state = CrossRefState(source_bom=_bom(), target_manufacturer="Würth Elektronik")
    state.crossref_result = _rows(r1_sub="560112116005")
    state.candidates_by_ref = {"R1": [_we_res("560112110020", "0402"),
                                      _we_res("560112116005", "0603")]}
    cp._stage3b_correct(state, ["R1 objection"], {"R1": {"560112110020"}})

    r1 = state.crossref_result[1]
    assert r1["substitute_pn"] == "560112116005", "a rejected part must not be re-applied"
    assert all("560112110020" not in c for c in seen_candidates), (
        "an already rejected part must not even be offered as a candidate"
    )
    assert any("already rejected" in d for d in state.diagnostics)


# --- Objections that cite no row apply to the rows under review ------------


def test_uncited_objection_corrects_the_row_under_review(monkeypatch):
    """Job 8218ca50f104: Ray rejected R1's 0402 in prose that never wrote "R1".
    The loop found "no ref_des in objections", ran zero rounds and never tried
    the 0603 alternative. R1 was the only row under review, so the objection is
    R1's; the exact C1,C2 row (not under review) is never touched."""
    monkeypatch.setenv("HEAVISIDE_JEV", "0")
    real_correct = cp._stage3b_correct
    rounds: list[int] = []

    class Reviewer:
        calls = 0

        def __call__(self, state, *a, **k):
            self.calls += 1
            if self.calls == 1:
                ray = {"reviewer": "ray", "verdict": "REJECTED", "reviewed_refs": ["R1"],
                       "objections": ["The proposed substitute 560112110020 is an 0402 "
                                      "part; the original is 0603 and will not fit the pads."]}
            else:
                ray = {"reviewer": "ray", "verdict": "APPROVED", "objections": [],
                       "reviewed_refs": ["R1"]}
            state.review_verdicts += [ray, {"reviewer": "nicola", "verdict": ray["verdict"],
                                            "reviewed_refs": ["R1"], "objections": []}]
            state.passed = ray["verdict"] == "APPROVED"
            return state

    def correct(state, objections, rejected_by_ref=None, **kw):
        rounds.append(1)
        state.candidates_by_ref = {"R1": [_we_res("560112110020", "0402"),
                                          _we_res("560112116005", "0603")]}
        return real_correct(state, objections, rejected_by_ref, **kw)

    def fake_llm(items, build_payload, **kw):
        payload = build_payload(items)
        assert [c["ref_des"] for c in payload["components_to_fix"]] == ["R1"], (
            "only the row under review is corrected, never the exact C1,C2 row"
        )
        comp = payload["components_to_fix"][0]
        assert comp["previously_rejected_substitutes"] == ["560112110020"]
        assert all(c.get("mpn") != "560112110020" for c in comp.get("_tas_candidates", []))
        return ([{"ref_des": "R1", "substitute_pn": "560112116005",
                  "status": "recommended", "notes": "0603 like the original"}], [])

    review = Reviewer()
    _wire_pipeline(monkeypatch, bom=_bom(), review=review, correct=correct)
    monkeypatch.setattr(cp, "_crossref_llm_batched", fake_llm)
    out = cp.run_crossref_pipeline(_bom(), "Würth Elektronik")

    assert rounds == [1], "an uncited objection on the only reviewed row must run a round"
    assert review.calls == 2
    assert not any("no ref_des found" in d for d in out.diagnostics)
    assert any("cite no row; applied to the row under review (R1)" in d
               for d in out.diagnostics)
    r1 = next(c for c in out.components if c.ref_des == "R1")
    assert r1.substitute_mpn == "560112116005"
    c = next(c for c in out.components if c.ref_des == "C1, C2")
    assert c.status.value == "exact" and c.substitute_mpn == "885012205037"


def test_rejection_targets_never_reach_rows_outside_review():
    known = {"C1, C2", "R1", "R2"}
    ray = {"reviewer": "ray", "verdict": "REJECTED", "reviewed_refs": ["R1", "R2"],
           "objections": ["wrong package"]}
    targets, note = cp._rejection_targets(ray, known)
    assert targets == {"R1", "R2"}
    assert "all 2 rows under review" in note
    ray["objections"] = ["R2 wrong package", "C1, C2 also suspicious"]
    assert cp._rejection_targets(ray, known) == ({"R2"}, None)
    with pytest.raises(cp.CrossRefPipelineError):
        cp._rejection_targets({"reviewer": "ray", "objections": ["x"]}, known)


# --- Fix 3: Ray and Nicola run concurrently ---------------------------------


def _review_state() -> CrossRefState:
    state = CrossRefState(source_bom=_bom(), target_manufacturer="Würth Elektronik")
    state.crossref_result = _rows()
    return state


def test_reviewers_run_concurrently(monkeypatch):
    monkeypatch.setenv("HEAVISIDE_JEV", "0")
    # Each call blocks until the OTHER reviewer has also started. Run one after
    # the other, the first call would wait forever (here: time out).
    barrier = threading.Barrier(2, timeout=10)
    started: list[str] = []

    def fake_call(name, prompt, **kw):
        started.append(name)
        barrier.wait()
        return {"verdict": "APPROVED", "objections": []}

    monkeypatch.setattr(cp, "call_agent_json", fake_call)
    state = cp._stage7_review(_review_state())

    assert sorted(started) == ["nicola", "ray"]
    assert [v["reviewer"] for v in state.review_verdicts] == ["ray", "nicola"], (
        "verdicts are recorded Ray-then-Nicola whichever call finished first"
    )
    assert state.passed is True


# --- The "only R1 shown" objection: the prompt must say why -----------------


def test_review_prompt_explains_rows_cleared_by_the_gate(monkeypatch):
    monkeypatch.setenv("HEAVISIDE_JEV", "1")
    monkeypatch.setattr(cp, "_jev_review_gate",
                        lambda state: ({"C1, C2"}, {"reviewer": "jev-gate", "verdict": "GATE",
                                                    "objections": []}))
    import heaviside.llm.usage as usage

    monkeypatch.setattr(usage, "record_avoided", lambda *a, **k: None)
    prompts: list[dict] = []

    def fake_call(name, prompt, **kw):
        prompts.append(json.loads(prompt.split("CROSS-REFERENCE REVIEW\n\n", 1)[1]))
        return {"verdict": "APPROVED", "objections": []}

    monkeypatch.setattr(cp, "call_agent_json", fake_call)
    state = cp._stage7_review(_review_state())

    assert len(prompts) == 2
    for p in prompts:
        assert [r["ref_des"] for r in p["crossref"]] == ["R1"]
        assert p["total_components"] == 2 and p["rows_under_review"] == 1
        assert [r["ref_des"] for r in p["not_under_review"]] == ["C1, C2"], (
            "a row deliberately not reviewed must be named, or the reviewer objects "
            "every round that the BOM's other component was never reviewed"
        )
        assert "must not be objected to" in p["review_scope"]
    ray = cp._latest_ray_verdict(state.review_verdicts)
    assert ray["reviewed_refs"] == ["R1"]


# --- Fix 4: no resolvable original footprint -> never `recommended` ----------


def test_unresolvable_original_footprint_is_unverified_not_recommended():
    state = CrossRefState(source_bom=_bom(r1_dims=None), target_manufacturer="Würth Elektronik")
    state.crossref_result = _rows()
    cp._stage_footprint_unverified(state)

    r1 = state.crossref_result[1]
    assert r1["status"] == "partial"
    assert "Footprint fit UNVERIFIED" in r1["notes"]
    # The reason is read from the original's own catalogue record, not guessed
    # from "0603" in the part number.
    assert "length 1.6 mm" in r1["footprint_unverified"]
    assert "no width" in r1["footprint_unverified"]
    assert "0603" not in r1["footprint_unverified"]
    assert state.crossref_result[0]["status"] == "exact", "a part kept as-is is untouched"


def test_resolvable_original_footprint_is_left_alone():
    state = CrossRefState(source_bom=_bom(), target_manufacturer="Würth Elektronik")
    state.crossref_result = _rows()
    cp._stage_footprint_unverified(state)
    assert state.crossref_result[1]["status"] == "recommended"
    assert "footprint_unverified" not in state.crossref_result[1]


def test_pipeline_never_returns_recommended_for_unverified_footprint(monkeypatch):
    # Reviewer approves; the post-loop stages may re-write `recommended` — the
    # final picks must still carry the unverified demotion.
    review = _Reviewer(approve=True)
    bom = _bom(r1_dims=None)
    _wire_pipeline(monkeypatch, bom=bom, review=review, correct=_corrector([]))

    def rescue(state, *a, **k):  # the post-loop rescue writing `recommended` again
        for row in state.crossref_result:
            if row["ref_des"] == "R1":
                row["status"] = "recommended"
        return state

    monkeypatch.setattr(cp, "_stage6_5_deterministic_rescue", rescue)
    out = cp.run_crossref_pipeline(bom, "Würth Elektronik")
    r1 = next(c for c in out.components if c.ref_des == "R1")
    assert r1.status.value == "partial"
    assert "UNVERIFIED" in r1.notes


@pytest.mark.parametrize("cat", ["connector", "analog", "timeBase"])
def test_identity_matched_categories_are_not_footprint_gated(cat):
    bom = _bom(r1_dims=None)
    bom[1]["component_type"] = cat
    state = CrossRefState(source_bom=bom, target_manufacturer="Würth Elektronik")
    state.crossref_result = _rows()
    cp._stage_footprint_unverified(state)
    assert state.crossref_result[1]["status"] == "recommended"
