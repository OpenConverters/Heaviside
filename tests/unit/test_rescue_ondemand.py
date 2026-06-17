"""Regression: the deterministic in-kind rescue must not silently skip a
no_substitute row just because PREFETCH left it no candidates (a ref-des /
category mismatch). It now fetches from TAS on demand. This is what left
um3491's L3 (300 nH EMC inductor) unmatched at 13/14 even though TAS has 500+
300 nH Würth inductors — the parts were reachable, prefetch just hadn't keyed
them to L3's ref."""

from __future__ import annotations

from heaviside.pipeline.crossref import CrossRefState
from heaviside.pipeline.crossref_pipeline import (
    _ondemand_candidates,
    _stage6_5_deterministic_rescue,
)


def test_ondemand_fetches_300nH_wurth_inductors():
    comp = {"ref_des": "L3", "component_type": "magnetic", "value": "300nH", "value_si": 3e-7}
    cands = _ondemand_candidates("Würth Elektronik", "magnetic", comp, {})
    assert cands, "expected on-demand TAS fetch to find 300 nH Würth inductors"


def test_ondemand_caches_per_category():
    cache: dict = {}
    comp = {"component_type": "magnetic", "value": "300nH", "value_si": 3e-7}
    _ondemand_candidates("Würth Elektronik", "magnetic", comp, cache)
    assert "magnetic" in cache  # loaded once, reused on the next call
    n = len(cache["magnetic"])
    _ondemand_candidates("Würth Elektronik", "magnetic", comp, cache)
    assert len(cache["magnetic"]) == n  # not re-loaded


def test_ondemand_unknown_category_returns_empty():
    assert _ondemand_candidates("Würth Elektronik", "mosfet", {"value": "x"}, {}) == []


def test_rescue_recovers_L3_when_prefetch_left_no_candidates():
    # The exact real-run condition: L3 no_substitute, candidates_by_ref EMPTY.
    st = CrossRefState(
        source_bom=[{"ref_des": "L3", "component_type": "magnetic",
                     "value": "300nH", "value_si": 3e-7}],
        target_manufacturer="Würth Elektronik",
    )
    st.crossref_result = [{"ref_des": "L3", "component_type": "magnetic",
                           "status": "no_substitute", "value": "300nH"}]
    out = _stage6_5_deterministic_rescue(st)
    row = out.crossref_result[0]
    assert row["status"] in ("recommended", "partial"), row
    assert row.get("substitute_pn"), "L3 should have been rescued to a real Würth MPN"


def test_rescue_still_skips_when_truly_no_tas_part():
    # A nonsense manufacturer → on-demand fetch finds nothing → stays no_substitute
    # (no fabrication; the floor only promotes parts that actually exist).
    st = CrossRefState(
        source_bom=[{"ref_des": "L9", "component_type": "magnetic",
                     "value": "300nH", "value_si": 3e-7}],
        target_manufacturer="NoSuchManufacturerXYZ",
    )
    st.crossref_result = [{"ref_des": "L9", "component_type": "magnetic",
                           "status": "no_substitute", "value": "300nH"}]
    out = _stage6_5_deterministic_rescue(st)
    assert out.crossref_result[0]["status"] == "no_substitute"
