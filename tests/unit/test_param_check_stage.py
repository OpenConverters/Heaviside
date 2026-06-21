"""Integration: the _stage_param_check pipeline stage resolves original +
substitute electrical params from the internal DB, attaches per-parameter
verdicts to each row, and demotes a substitute that fails a critical parameter.
"""
from __future__ import annotations

import json

from heaviside.pipeline import crossref_pipeline as cp
from heaviside.pipeline.crossref import CrossRefState


def _cap_env(mpn, *, esr, ripple, tech, cap=1e-5, v=25.0, case="0805"):
    return {"capacitor": {"manufacturerInfo": {
        "reference": mpn, "name": "TestMfr", "status": "active",
        "datasheetInfo": {
            "electrical": {"capacitance": {"nominal": cap}, "ratedVoltage": v,
                           "esr": esr, "rippleCurrent": ripple},
            "part": {"technology": tech, "case": case},
        }}}}


def _write_caps(tmp_path, envs):
    (tmp_path / "capacitors.ndjson").write_text(
        "\n".join(json.dumps(e) for e in envs) + "\n"
    )


def test_stage_demotes_on_esr_fail_and_renders(tmp_path, monkeypatch):
    # Original low-ESR X7R; substitute much higher ESR + dielectric downgrade.
    _write_caps(tmp_path, [
        _cap_env("ORIG1", esr=0.05, ripple=2.0, tech="X7R"),
        _cap_env("SUBS1", esr=0.40, ripple=2.0, tech="X5R"),
    ])
    monkeypatch.setattr(cp, "_tas_data_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("heaviside.catalogue.selector._tas_data_dir", lambda: tmp_path)

    state = CrossRefState(source_bom=[], target_manufacturer="TestMfr")
    state.crossref_result = [{
        "ref_des": "C1", "component_type": "capacitor",
        "original_pn": "ORIG1", "substitute_pn": "SUBS1",
        "status": "recommended", "notes": "",
    }]
    cp._stage_param_check(state)
    row = state.crossref_result[0]

    # ESR fails (0.40 > 0.05·1.5) and dielectric downgrade fails → demoted.
    assert row["status"] == "partial"
    verdicts = {r["name"]: r["verdict"] for r in row["_param_results"]}
    assert verdicts["esr"] == "fail"
    assert verdicts["technology"] == "fail"
    assert any(f.startswith("PARAM:esr") for f in row["guardrail_fires"])
    assert "parameter check" in row["notes"]

    # match_detail renders the ESR row so it appears on the report.
    md = cp.build_match_detail(row)
    labels = [p["name"] for p in md["params"]]
    assert "ESR" in labels and "Dielectric" in labels


def test_stage_keeps_good_substitute(tmp_path, monkeypatch):
    _write_caps(tmp_path, [
        _cap_env("ORIG2", esr=0.40, ripple=1.0, tech="X5R"),
        _cap_env("SUBS2", esr=0.10, ripple=1.5, tech="X7R"),  # better on all
    ])
    monkeypatch.setattr(cp, "_tas_data_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("heaviside.catalogue.selector._tas_data_dir", lambda: tmp_path)

    state = CrossRefState(source_bom=[], target_manufacturer="TestMfr")
    state.crossref_result = [{
        "ref_des": "C2", "component_type": "capacitor",
        "original_pn": "ORIG2", "substitute_pn": "SUBS2",
        "status": "recommended", "notes": "",
    }]
    cp._stage_param_check(state)
    row = state.crossref_result[0]

    assert row["status"] == "recommended"  # not demoted
    verdicts = {r["name"]: r["verdict"] for r in row["_param_results"]}
    assert verdicts["esr"] == "pass"
    assert verdicts["ripple_current"] == "pass"
    assert verdicts["technology"] == "pass"


def test_stage_missing_substitute_esr_fails(tmp_path, monkeypatch):
    # Substitute has no ESR field → "don't use it" → fail (cannot verify).
    sub = _cap_env("SUBS3", esr=None, ripple=1.0, tech="X7R")
    del sub["capacitor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]["esr"]
    _write_caps(tmp_path, [_cap_env("ORIG3", esr=0.1, ripple=1.0, tech="X7R"), sub])
    monkeypatch.setattr(cp, "_tas_data_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("heaviside.catalogue.selector._tas_data_dir", lambda: tmp_path)

    state = CrossRefState(source_bom=[], target_manufacturer="TestMfr")
    state.crossref_result = [{
        "ref_des": "C3", "component_type": "capacitor",
        "original_pn": "ORIG3", "substitute_pn": "SUBS3",
        "status": "recommended", "notes": "",
    }]
    cp._stage_param_check(state)
    row = state.crossref_result[0]
    verdicts = {r["name"]: r["verdict"] for r in row["_param_results"]}
    assert verdicts["esr"] == "fail"
    assert row["status"] == "partial"
