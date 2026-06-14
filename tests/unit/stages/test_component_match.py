"""Unit tests for the component_match engine (deterministic, no LLM).

These pin the in-technology matching invariant — the supercap-vs-ceramic
regression — at the stage level, over the real TAS data.
"""
from __future__ import annotations

import pytest

from heaviside.stages.component_match import Candidate, find_candidates, select_candidate

_CERAMIC_FAMILY = "ceramic"


def _families(cands):
    from heaviside.pipeline.crossref_pipeline import _capacitor_technology_family
    return {_capacitor_technology_family(c.technology) for c in cands if c.technology}


def test_ceramic_query_returns_only_ceramics():
    cands = find_candidates(
        category="capacitor", target_manufacturer="Würth Elektronik",
        value_si=1e-7, technology="ceramic", min_voltage=100, max_results=30,
    )
    assert cands, "no Würth ceramic candidates for 0.1uF/100V"
    fams = _families(cands)
    assert fams == {_CERAMIC_FAMILY}, f"non-ceramic leaked in: {fams}"
    # voltage floor respected
    assert all((c.voltage or 0) >= 100 for c in cands)


def test_4u7_ceramic_excludes_supercaps_and_electrolytics():
    cands = find_candidates(
        category="capacitor", target_manufacturer="Würth Elektronik",
        value_si=4.7e-6, technology="X7R", min_voltage=100, max_results=30,
    )
    fams = _families(cands)
    assert "supercapacitor" not in fams and "aluminum" not in fams, fams


def test_candidates_are_peas_aligned():
    cands = find_candidates(
        category="capacitor", target_manufacturer="Würth Elektronik",
        value_si=1e-7, technology="ceramic", min_voltage=50, max_results=5,
    )
    assert all(isinstance(c, Candidate) and c.mpn and c.category == "capacitor"
               for c in cands)
    # ranks are contiguous from 0
    assert [c.rank for c in cands] == list(range(len(cands)))


def test_unsupported_category_raises():
    with pytest.raises(ValueError, match="not yet supported"):
        find_candidates(category="semiconductor", target_manufacturer="Würth")


def test_select_candidate_fallback_without_llm(monkeypatch):
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    cands = find_candidates(
        category="capacitor", target_manufacturer="Würth Elektronik",
        value_si=1e-7, technology="ceramic", min_voltage=100, max_results=5,
    )
    chosen = select_candidate(cands, original_mpn="GRM188R72A104K")
    assert chosen is cands[0]  # deterministic top-rank fallback
    assert select_candidate([], original_mpn="X") is None
