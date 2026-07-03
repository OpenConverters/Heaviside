"""The part-resolver stage LLM-cleans messy pasted BOM cells (mfr+code mashed,
leading separators) into {manufacturer, mpn} — never fabricating, only fixing.
No network: the resolver agent is mocked."""

from __future__ import annotations

import heaviside.agents.llm_call as L
from heaviside.pipeline import crossref_pipeline as cp
from heaviside.pipeline.crossref import CrossRefState


def test_detects_only_messy_rows():
    assert cp._needs_part_resolution({"original_mpn": "Phoenix C  1707654"})
    assert cp._needs_part_resolution({"original_mpn": "/IHLP1616ABER1R5M11", "manufacturer": "VISHAY"})
    assert cp._needs_part_resolution({"original_mpn": "MURATA-GRM155", "manufacturer": "Murata"})
    assert not cp._needs_part_resolution({"original_mpn": "GRM155R71C104KA88D", "manufacturer": "Murata"})
    assert not cp._needs_part_resolution({"original_mpn": "CRCW060310K0FKEA"})


def test_resolves_mashed_rows(monkeypatch):
    def fake(agent, msg, **k):
        import json
        d = json.loads(msg)
        out = []
        for r in d["rows"]:
            if r["ref_des"] == "J1":
                out.append({"ref_des": "J1", "manufacturer": "Phoenix Contact", "mpn": "1707654"})
            if r["ref_des"] == "L3":
                out.append({"ref_des": "L3", "manufacturer": "Vishay", "mpn": "IHLP1616ABER1R5M11"})
        return {"resolved": out}

    monkeypatch.setattr(L, "call_agent_json", fake)
    st = CrossRefState(
        source_bom=[
            {"ref_des": "J1", "original_mpn": "Phoenix C  1707654"},
            {"ref_des": "L3", "original_mpn": "/IHLP1616ABER1R5M11", "manufacturer": "VISHAY"},
            {"ref_des": "C1", "original_mpn": "GRM155R71C104KA88D", "manufacturer": "Murata"},
        ],
        target_manufacturer="Würth Elektronik",
    )
    cp._stage0_resolve_parts(st)
    by = {r["ref_des"]: r for r in st.source_bom}
    assert by["J1"]["original_mpn"] == "1707654" and by["J1"]["manufacturer"] == "Phoenix Contact"
    assert by["L3"]["original_mpn"] == "IHLP1616ABER1R5M11" and by["L3"]["manufacturer"] == "Vishay"
    assert by["C1"]["original_mpn"] == "GRM155R71C104KA88D"  # clean row untouched


def test_rejects_whitespace_mpn_from_llm(monkeypatch):
    # If the LLM returns a still-messy mpn, don't apply it (guard against noise).
    monkeypatch.setattr(
        L, "call_agent_json",
        lambda a, m, **k: {"resolved": [{"ref_des": "J1", "manufacturer": "X", "mpn": "still messy"}]},
    )
    st = CrossRefState(
        source_bom=[{"ref_des": "J1", "original_mpn": "Phoenix C 1707654"}],
        target_manufacturer="W",
    )
    cp._stage0_resolve_parts(st)
    assert st.source_bom[0]["original_mpn"] == "Phoenix C 1707654"  # unchanged


def test_no_llm_leaves_rows_untouched(monkeypatch):
    def boom(*a, **k):
        from heaviside.agents.llm_call import LLMCallError
        raise LLMCallError("no key")

    monkeypatch.setattr(L, "call_agent_json", boom)
    st = CrossRefState(
        source_bom=[{"ref_des": "J1", "original_mpn": "Phoenix C  1707654"}],
        target_manufacturer="W",
    )
    cp._stage0_resolve_parts(st)
    assert st.source_bom[0]["original_mpn"] == "Phoenix C  1707654"
