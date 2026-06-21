"""GFoot / GStack must not relabel a found-but-caveated part as no_substitute.

A larger-package or multiply-caveated substitute is a REAL part that exists —
it's a `partial` substitution (engineer weighs the footprint/board-space cost),
never `no_substitute` (which must mean "no electrically-valid part exists").
Regression guard: a 06-19 commit hard-rejected big footprint jumps to
no_substitute and tanked CR-vs-Proteus coverage 10/10 → 5/10.
"""
from __future__ import annotations

from heaviside.pipeline import guardrails as g


def test_large_footprint_jump_is_partial_not_no_substitute(monkeypatch):
    # Substitute is 0402 -> 2220 (7 size classes): a real part, just much bigger.
    monkeypatch.setattr(g, "_lookup_tas_part", lambda pn, _t, tas_data_dir=None: {"package": "2220"})
    comp = {"ref_des": "C1", "type": "capacitor", "status": "recommended",
            "substitute_pn": "885012214003", "notes": ""}
    fires: list = []
    g._gfoot_footprint_compatibility([comp], {"C1": {"package": "0402"}}, fires)
    assert comp["status"] == "partial"               # NOT no_substitute
    assert comp["substitute_pn"] == "885012214003"   # part preserved
    assert "redesign" in comp["notes"].lower()
    assert fires and fires[0]["after"] == "partial"


def test_moderate_footprint_jump_is_partial():
    comp = {"ref_des": "C2", "type": "capacitor", "status": "recommended",
            "substitute_pn": "X", "notes": ""}
    # 0402 -> 1206 = 3 classes → partial
    import heaviside.pipeline.guardrails as gg
    orig = gg._lookup_tas_part
    gg._lookup_tas_part = lambda pn, _t, tas_data_dir=None: {"package": "1206"}
    try:
        fires: list = []
        gg._gfoot_footprint_compatibility([comp], {"C2": {"package": "0402"}}, fires)
    finally:
        gg._lookup_tas_part = orig
    assert comp["status"] == "partial"


def test_smd_to_leaded_still_rejected(monkeypatch):
    # Mount-type incompatibility is a different class of problem; keep as-is.
    monkeypatch.setattr(g, "_lookup_tas_part", lambda pn, _t, tas_data_dir=None: {"package": "DIP-8"})
    comp = {"ref_des": "C3", "type": "capacitor", "status": "recommended",
            "substitute_pn": "Y", "notes": ""}
    fires: list = []
    g._gfoot_footprint_compatibility([comp], {"C3": {"package": "0603"}}, fires)
    assert comp["status"] == "no_substitute"


def test_gstack_multiple_caveats_is_partial_not_no_substitute():
    # Two stacked guardrail caveats → partial with MULTIPLE COMPROMISES, not gone.
    comp = {"ref_des": "C4", "status": "partial", "substitute_pn": "Z",
            "notes": "GUARDRAIL G3: voltage downrate. GUARDRAIL GFoot: footprint jump."}
    fires: list = []
    g._gstack_multiple_caveats([comp], fires)
    assert comp["status"] == "partial"
    assert comp["substitute_pn"] == "Z"
    assert "MULTIPLE COMPROMISES" in comp["notes"]
