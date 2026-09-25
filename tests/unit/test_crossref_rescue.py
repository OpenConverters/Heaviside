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
    from heaviside.pipeline.crossref import CrossRefState
    from heaviside.pipeline.crossref_pipeline import _rank_candidates

    comp = {"ref_des": "C1", "component_type": "capacitor",
            "value_si": 22e-6, "rated_voltage": 10.0, "technology": "X7T"}
    st = CrossRefState(source_bom=[comp], target_manufacturer="Würth Elektronik",
                       crossref_result=[{"ref_des": "C1", "component_type": "capacitor",
                                         "status": "no_substitute", "notes": "LLM dropped it"}])
    # ranked the way stage 1 ranks, so each candidate carries Kelvin's verdict
    st.candidates_by_ref["C1"] = _rank_candidates(
        dict(comp), "capacitor", _wurth_envs(22e-6, "X7T", 10),
        verdicts_out=st.kelvin_verdicts.setdefault("C1", {}))
    _stage6_5_deterministic_rescue(st)
    row = st.crossref_result[0]
    assert row["status"] in ("recommended", "partial")
    assert row["substitute_pn"]
    assert "deterministic in-kind rescue" in row["notes"]


def test_value_gate_demotion_is_rescued_after_param_check() -> None:
    """Ordering regression: the primary-value gate (_stage_param_check) demotes an
    off-value LLM pick to no_substitute, but it runs AFTER the deterministic rescue
    (stage 6.5). A 10 kΩ resistor whose LLM pick was an 11.1 kΩ therefore came back
    no_substitute even though YAGEO's exact 10 kΩ 0603 was prefetched and top-ranked
    (user-reported 2026-08-02). The fix runs a rescue AFTER the gate, so the tail
    _stage_param_check -> _stage6_5_deterministic_rescue -> _stage_param_check must
    land on the exact-value in-catalogue part."""
    from heaviside.pipeline.crossref_pipeline import _rank_candidates, _stage_param_check
    from heaviside.stages.component_match import find_candidates

    # prefetched, value-ranked YAGEO 0603 candidates (exact 10 kΩ present + the
    # 11.1 kΩ the LLM wrongly picked)
    cands = [c.env for c in find_candidates(
        category="resistor", target_manufacturer="YAGEO",
        value_si=10000.0, max_results=40)]
    comp = {"ref_des": "R1", "component_type": "resistor",
            "value": "10kΩ", "package": "0603", "original_mpn": "CRCW060310K0FKED"}
    from heaviside.pipeline.crossref import CrossRefState

    st = CrossRefState(source_bom=[dict(comp)], target_manufacturer="YAGEO")
    ranked = _rank_candidates(dict(comp), "resistor", cands, max_results=50,
                              verdicts_out=st.kelvin_verdicts.setdefault("R1", {}))
    st.crossref_result = [{"ref_des": "R1", "component_type": "resistor",
                           "original_pn": "CRCW060310K0FKED", "original_value": "10kΩ",
                           "substitute_pn": "NT0603BRD0711K1L", "status": "recommended",
                           "notes": ""}]
    st.candidates_by_ref = {"R1": ranked}

    # 1) the value gate demotes the off-value pick — this is the (correct) trigger
    _stage_param_check(st)
    assert st.crossref_result[0]["status"] == "no_substitute"

    # 2) the post-gate rescue must promote the exact in-catalogue 10 kΩ
    _stage6_5_deterministic_rescue(st)
    _stage_param_check(st)
    row = st.crossref_result[0]
    assert row["status"] in ("recommended", "partial"), row
    assert row["substitute_pn"] and row["substitute_pn"] != "NT0603BRD0711K1L"
    # the promoted part is a genuine 10 kΩ 0603 YAGEO (AA0603*10KL family)
    assert row["substitute_pn"].startswith("AA0603") and "10K" in row["substitute_pn"], row


def test_rescued_oversize_part_carries_kelvins_status_not_recommended() -> None:
    """lt80603evkit C12: a 4.7 uF / 100 V 1206 original, and the only Würth parts
    that meet 100 V are larger. The in-kind rescue verified value/voltage and
    marked a 2220 "recommended"; the status must be Kelvin's verdict (partial,
    with the footprint named), never a drop-in claim."""
    from heaviside.pipeline.crossref import CrossRefState
    from heaviside.pipeline.crossref_pipeline import _rank_candidates

    comp = {"ref_des": "C12", "component_type": "capacitor", "value": "4.7uF",
            "rated_voltage": "100V", "package": "1206", "technology": "X7R",
            "_source_dims_m": (0.0032, 0.0016, 0.0019)}
    st = CrossRefState(source_bom=[comp], target_manufacturer="Würth Elektronik",
                       crossref_result=[{"ref_des": "C12", "component_type": "capacitor",
                                         "status": "no_substitute", "notes": ""}])
    st.candidates_by_ref["C12"] = _rank_candidates(
        dict(comp), "capacitor", _wurth_envs(4.7e-6, "X7R", 100, ),
        verdicts_out=st.kelvin_verdicts.setdefault("C12", {}))
    _stage6_5_deterministic_rescue(st)
    row = st.crossref_result[0]
    if row["status"] == "no_substitute":
        return  # nothing in-kind at all: nothing to mislabel
    verdict = st.kelvin_verdicts["C12"][row["substitute_pn"]]
    assert row["status"] == verdict["status"]
    assert "Kelvin grade" in row["notes"]
    if verdict.get("footprint") in ("overflows", "one_size_larger"):
        assert row["status"] == "partial"
