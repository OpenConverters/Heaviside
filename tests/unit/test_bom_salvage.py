"""When the deterministic BOM parser fails, an LLM salvages the REAL rows from
the raw text — strictly transcribing, never inventing parts. No network."""

from __future__ import annotations

import heaviside.agents.llm_call as L
import heaviside.pipeline.bom_import as B


def _mapper_and_salvage(salvage_rows):
    def fake(agent, msg, **k):
        if agent == "bom-header-mapper":
            return {}
        if agent == "bom-salvage":
            return {"rows": salvage_rows}
        return {}
    return fake


def test_salvage_transcribes_and_drops_empty(monkeypatch):
    monkeypatch.setattr(
        L, "call_agent_json",
        _mapper_and_salvage([
            {"ref_des": "C1", "mpn": "GRM155", "manufacturer": "Murata", "value": "100nF"},
            {"ref_des": "J1", "mpn": "1707654", "manufacturer": "Phoenix Contact", "value": ""},
            {"ref_des": "X9", "mpn": "", "manufacturer": "", "value": ""},  # empty -> dropped
        ]),
    )
    monkeypatch.setattr(B, "_llm_available", lambda: True)
    out = B.parse_bom_bytes(b"Notes\nline one\nline two\n", "bom.csv")
    mpns = [r["original_mpn"] for r in out]
    assert "1707654" in mpns and "GRM155" in mpns
    assert "" not in mpns  # no empty/fabricated row survives


def test_empty_salvage_reraises_original_error(monkeypatch):
    monkeypatch.setattr(L, "call_agent_json", _mapper_and_salvage([]))
    monkeypatch.setattr(B, "_llm_available", lambda: True)
    import pytest
    with pytest.raises(B.BomImportError):
        B.parse_bom_bytes(b"Notes\nline one\n", "bom.csv")


def test_no_llm_key_reraises(monkeypatch):
    monkeypatch.setattr(B, "_llm_available", lambda: False)
    import pytest
    with pytest.raises(B.BomImportError):
        B.parse_bom_bytes(b"Notes\nline one\n", "bom.csv")


def test_good_file_never_hits_salvage(monkeypatch):
    # A clean CSV parses deterministically; salvage must not be called.
    def guard(agent, msg, **k):
        assert agent != "bom-salvage", "salvage should not run on a parseable file"
        return {}
    monkeypatch.setattr(L, "call_agent_json", guard)
    monkeypatch.setattr(B, "_llm_available", lambda: True)
    out = B.parse_bom_bytes(b"Designator,MPN,Manufacturer\nC1,GRM155,Murata\n", "bom.csv")
    assert out and out[0]["original_mpn"] == "GRM155"
