"""Deterministic-rescue unit tests (no LLM).

_stage6_5_deterministic_rescue is the floor under the two stochastic LLM CR
stages (crossref + otto): a no_substitute is only kept when NO prefetched
candidate provably meets the in-kind criteria. This removes the run-to-run
variance (e.g. um3491's 22µF X7T caps that the LLM intermittently dropped)."""
from __future__ import annotations

from heaviside.pipeline.crossref_pipeline import (
    _best_inkind_candidate,
    _stage6_5_deterministic_rescue,
)
from heaviside.stages.component_match import find_candidates


def _wurth_envs(value_si, technology, min_voltage, category="capacitor"):
    return [c.env for c in find_candidates(
        category=category, target_manufacturer="Würth Elektronik",
        value_si=value_si, technology=technology, min_voltage=min_voltage, max_results=10)]


def test_promotes_valid_inkind_ceramic() -> None:
    # 22uF X7T 10V original -> a Würth ceramic of adequate V/value must be found.
    comp = {"value_si": 22e-6, "rated_voltage": 10.0, "technology": "X7T"}
    cands = _wurth_envs(22e-6, "X7T", 10)
    patch = _best_inkind_candidate(comp, "capacitor", cands)
    assert patch is not None
    assert patch["substitute_pn"]
    assert patch["status"] in ("recommended", "partial")


def test_no_candidates_stays_none() -> None:
    assert _best_inkind_candidate({"value_si": 1e-6}, "capacitor", []) is None


def test_family_mismatch_not_rescued() -> None:
    # Original is tantalum; only ceramic candidates -> chemistry gate blocks them.
    comp = {"value_si": 22e-6, "rated_voltage": 10.0, "technology": "tantalum-polymer"}
    ceramic_cands = _wurth_envs(22e-6, "X7R", 10)
    assert _best_inkind_candidate(comp, "capacitor", ceramic_cands) is None


def test_stage_rescues_no_substitute_row() -> None:
    class _State:
        pass

    st = _State()
    st.source_bom = [{"ref_des": "C1", "component_type": "capacitor",
                      "value_si": 22e-6, "rated_voltage": 10.0, "technology": "X7T"}]
    st.crossref_result = [{"ref_des": "C1", "component_type": "capacitor",
                           "status": "no_substitute", "notes": "LLM dropped it"}]
    st.candidates_by_ref = {"C1": _wurth_envs(22e-6, "X7T", 10)}
    _stage6_5_deterministic_rescue(st)
    row = st.crossref_result[0]
    assert row["status"] in ("recommended", "partial")
    assert row["substitute_pn"]
    assert "deterministic in-kind rescue" in row["notes"]
