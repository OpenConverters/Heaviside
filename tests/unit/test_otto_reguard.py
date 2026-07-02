"""Otto's stage-6 re-crossref must re-run the guardrails (critically G5,
hallucination) on the substitutions it applies.

Stage 4's guardrails run BEFORE Otto, so before this fix an invented-but-
plausible Otto MPN was written straight into the result and reached
review/report unchecked — the exact failure G5 exists to catch.

The test mocks both Otto LLM calls (the challenge and the re-search) and spies
on the re-guard, asserting it now runs with the applied Otto substitution
present. It does not depend on the parts DB or a live LLM.
"""

from __future__ import annotations

import json

from heaviside.pipeline import crossref_pipeline as cp
from heaviside.pipeline.crossref import CrossRefState

_HALLUCINATED = "WCAP-TOTALLY-INVENTED-9999"


def _state_with_one_no_substitute() -> CrossRefState:
    state = CrossRefState(source_bom=[{"ref_des": "C1", "component_type": "capacitor"}],
                          target_manufacturer="Würth Elektronik")
    state.crossref_result = [
        {
            "ref_des": "C1",
            "component_type": "capacitor",
            "original_pn": "GRM188R71H104KA93D",
            "original_value": "100nF",
            "original_package": "0603",
            "status": "no_substitute",
            "substitute_pn": "no_substitute",
            "notes": "",
        }
    ]
    return state


def test_otto_applied_substitution_is_re_guarded(monkeypatch) -> None:
    state = _state_with_one_no_substitute()

    # Otto's challenge LLM: overturn the C1 no_substitute verdict.
    monkeypatch.setattr(
        cp,
        "call_agent",
        lambda *a, **k: json.dumps(
            {"challenges": [{"ref_des": "C1", "verdict": "OVERTURNED", "diagnosis": "too narrow"}]}
        ),
    )
    monkeypatch.setattr(cp, "extract_json_block", lambda raw: json.loads(raw))

    # Otto's re-search LLM: return a hallucinated MPN as a 'recommended' sub.
    monkeypatch.setattr(
        cp,
        "_crossref_llm_batched",
        lambda hints, prompt_fn, concurrent=True: (
            [{"ref_des": "C1", "substitute_pn": _HALLUCINATED, "status": "recommended"}],
            [],
        ),
    )

    # Spy on the re-guard: record the crossref_result it is handed.
    seen: dict[str, object] = {}

    def _spy_stage4(st: CrossRefState) -> CrossRefState:
        seen["called"] = True
        seen["rows"] = [dict(r) for r in st.crossref_result]
        return st

    monkeypatch.setattr(cp, "_stage4_guardrails", _spy_stage4)

    out = cp._stage6_otto(state)

    # The re-guard ran, and it saw the applied Otto substitution — so G5 gets
    # a chance to demote the hallucination (it did not, before this fix).
    assert seen.get("called") is True, "Otto must re-run the guardrails after applying"
    c1 = next(r for r in seen["rows"] if r["ref_des"] == "C1")
    assert c1["substitute_pn"] == _HALLUCINATED
    assert c1["status"] == "recommended"
    assert out.otto_log["re_crossref_applied"] == 1


def test_otto_no_reguard_when_nothing_applied(monkeypatch) -> None:
    """If Otto overturns nothing, the re-guard is not triggered (no spurious
    re-run / no wasted LLM retry)."""
    state = _state_with_one_no_substitute()
    monkeypatch.setattr(
        cp,
        "call_agent",
        lambda *a, **k: json.dumps(
            {"challenges": [{"ref_des": "C1", "verdict": "CONFIRMED", "diagnosis": "genuinely none"}]}
        ),
    )
    monkeypatch.setattr(cp, "extract_json_block", lambda raw: json.loads(raw))

    called = {"v": False}
    monkeypatch.setattr(cp, "_stage4_guardrails", lambda st: called.__setitem__("v", True) or st)

    cp._stage6_otto(state)
    assert called["v"] is False
