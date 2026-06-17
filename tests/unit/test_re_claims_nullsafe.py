"""Regression: the RE claims-extraction stage must tolerate the LLM emitting
explicit nulls (`"efficiency_curve": null`, `"waveforms": null`, `"specs":
null`). `dict.get(k, default)` returns the default only when the key is ABSENT,
not when it is present-but-None — so iterating `.get("efficiency_curve", [])`
crashed on `None` for the um3491 reference design. The fix uses `... or []`."""

from __future__ import annotations

import heaviside.pipeline.re_pipeline as cp
from heaviside.pipeline.re_state import ReferenceSpec, REState


def _state() -> REState:
    st = REState(reference="um3491", pdf_text="x" * 100)
    st.ref_spec = ReferenceSpec(
        topology="buck", vin_min=6, vin_nom=12, vin_max=18,
        vout=6, iout=6, pout=36, fsw=400_000,
    )
    return st


def test_null_efficiency_curve_and_specs_do_not_crash(monkeypatch):
    monkeypatch.setattr(
        cp, "call_agent_json",
        lambda *a, **k: {"performance": {"efficiency_curve": None, "waveforms": None},
                         "specs": None},
    )
    out = cp._stage2_7_extract_claims(_state())
    assert out.ref_claims.efficiency == {}
    assert out.ref_claims.waveform_descriptions == []


def test_missing_keys_still_work(monkeypatch):
    monkeypatch.setattr(cp, "call_agent_json", lambda *a, **k: {})
    out = cp._stage2_7_extract_claims(_state())
    assert out.ref_claims.efficiency == {}


def test_real_efficiency_curve_is_parsed(monkeypatch):
    monkeypatch.setattr(
        cp, "call_agent_json",
        lambda *a, **k: {"performance": {"efficiency_curve": [
            {"load_pct": 50, "efficiency": 0.94},
            {"load_pct": 100, "efficiency": 0.917},
        ]}},
    )
    out = cp._stage2_7_extract_claims(_state())
    assert out.ref_claims.efficiency == {"50%": 0.94, "100%": 0.917}


def test_non_dict_entries_in_curve_are_skipped(monkeypatch):
    monkeypatch.setattr(
        cp, "call_agent_json",
        lambda *a, **k: {"performance": {"efficiency_curve": [None, "junk",
                         {"load_pct": 100, "efficiency": 0.9}]}},
    )
    out = cp._stage2_7_extract_claims(_state())
    assert out.ref_claims.efficiency == {"100%": 0.9}
