"""Integration: the _stage_param_check pipeline stage resolves original +
substitute electrical params from the internal DB, attaches per-parameter
verdicts to each row, and demotes a substitute that fails a critical parameter.
"""
from __future__ import annotations

import json

from heaviside.pipeline import crossref_pipeline as cp
from heaviside.pipeline.crossref import CrossRefState


def _cap_env(mpn, *, esr, ripple, tech, cap=1e-5, v=25.0, case="0805",
             sat_mlcc=None, vth_mlcc=None):
    elec = {"capacitance": {"nominal": cap}, "ratedVoltage": v,
            "esr": esr, "rippleCurrent": ripple}
    if sat_mlcc is not None:
        elec["capacitanceSaturationMLCC"] = sat_mlcc
    if vth_mlcc is not None:
        elec["vthMLCC"] = vth_mlcc
    return {"capacitor": {"manufacturerInfo": {
        "reference": mpn, "name": "TestMfr", "status": "active",
        "datasheetInfo": {
            "electrical": elec,
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


def test_stage_mlcc_bias_uses_operating_voltage(tmp_path, monkeypatch):
    # Both pass ESR/ripple/dielectric, but the substitute derates hard under
    # bias. With a 10V operating point (from sim stress) the effective-C check
    # fires and demotes the substitute.
    _write_caps(tmp_path, [
        _cap_env("ORIGM", esr=0.05, ripple=2.0, tech="X7R", v=6.3, sat_mlcc=0.9, vth_mlcc=50),
        _cap_env("SUBSM", esr=0.05, ripple=2.0, tech="X7R", v=6.3, sat_mlcc=0.6, vth_mlcc=8),
    ])
    monkeypatch.setattr(cp, "_tas_data_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("heaviside.catalogue.selector._tas_data_dir", lambda: tmp_path)

    from heaviside.pipeline.crossref import SimDerivedStress
    state = CrossRefState(source_bom=[], target_manufacturer="TestMfr")
    state.stress_by_ref = {"C9": SimDerivedStress(ref_des="C9", role="output", v_peak=10.0)}
    state.crossref_result = [{
        "ref_des": "C9", "component_type": "capacitor",
        "original_pn": "ORIGM", "substitute_pn": "SUBSM",
        "status": "recommended", "notes": "",
    }]
    cp._stage_param_check(state)
    row = state.crossref_result[0]
    verdicts = {r["name"]: r["verdict"] for r in row["_param_results"]}
    assert verdicts["c_bias"] == "fail"
    assert row["status"] == "partial"


def test_stage_mlcc_bias_skipped_without_stress(tmp_path, monkeypatch):
    # Same parts but no operating voltage → bias not computed (no estimate).
    _write_caps(tmp_path, [
        _cap_env("ORIGN", esr=0.05, ripple=2.0, tech="X7R", v=6.3, sat_mlcc=0.9, vth_mlcc=50),
        _cap_env("SUBSN", esr=0.05, ripple=2.0, tech="X7R", v=6.3, sat_mlcc=0.6, vth_mlcc=8),
    ])
    monkeypatch.setattr(cp, "_tas_data_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("heaviside.catalogue.selector._tas_data_dir", lambda: tmp_path)

    state = CrossRefState(source_bom=[], target_manufacturer="TestMfr")
    state.crossref_result = [{
        "ref_des": "C10", "component_type": "capacitor",
        "original_pn": "ORIGN", "substitute_pn": "SUBSN",
        "status": "recommended", "notes": "",
    }]
    cp._stage_param_check(state)
    row = state.crossref_result[0]
    names = {r["name"] for r in row.get("_param_results", [])}
    assert "c_bias" not in names
    assert row["status"] == "recommended"


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


def _mag_env(mpn, *, ind=1e-6, dcr=0.05, isat=5.0):
    # MAS magnetic: datasheetInfo.electrical is an ARRAY of winding entries.
    return {"magnetic": {"manufacturerInfo": {
        "reference": mpn, "name": "TestMfr", "status": "active",
        "datasheetInfo": {
            "part": {"partNumber": mpn, "description": "inductor"},
            "electrical": [{
                "subtype": "inductor",
                "inductance": {"nominal": ind, "minimum": ind * 0.8, "maximum": ind * 1.2},
                "dcResistance": {"maximum": dcr},
                "saturationCurrentPeak": isat,
            }],
        }}}}


def _write_mags(tmp_path, envs):
    (tmp_path / "magnetics.ndjson").write_text("\n".join(json.dumps(e) for e in envs) + "\n")


def _mag_state(tmp_path, monkeypatch, row):
    monkeypatch.setattr(cp, "_tas_data_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("heaviside.catalogue.selector._tas_data_dir", lambda: tmp_path)
    state = CrossRefState(source_bom=[], target_manufacturer="TestMfr")
    state.crossref_result = [row]
    cp._stage_param_check(state)
    return state.crossref_result[0]


def test_value_matched_no_original_forces_no_substitute(tmp_path, monkeypatch):
    # Only the substitute is in the DB; the original is unknown AND the BOM has no
    # value → the substitute was matched against nothing → no_substitute.
    _write_mags(tmp_path, [_mag_env("SUB_IND", ind=3.3e-7, dcr=0.0085, isat=12.4)])
    row = _mag_state(tmp_path, monkeypatch, {
        "ref_des": "L1", "component_type": "magnetic",
        "original_pn": "IHLP1616ABER1R5M11", "substitute_pn": "SUB_IND",
        "original_value": "", "status": "partial", "notes": "",
    })
    assert row["status"] == "no_substitute"
    assert row["substitute_pn"] is None
    assert "NO_ORIGINAL_DATA" in row["guardrail_fires"]
    assert "no resolvable specs" in row["notes"]


def test_value_matched_sourced_original_not_demoted(tmp_path, monkeypatch):
    # When the original IS in the DB, the normal value check runs — the guardrail
    # must NOT fire (this is the sourced-IHLP case after the converter fix).
    _write_mags(tmp_path, [
        _mag_env("IHLP1616ABER1R5M11", ind=1.5e-6, dcr=0.075, isat=3.25),
        _mag_env("SUB_IND", ind=1.5e-6, dcr=0.06, isat=4.0),
    ])
    row = _mag_state(tmp_path, monkeypatch, {
        "ref_des": "L1", "component_type": "magnetic",
        "original_pn": "IHLP1616ABER1R5M11", "substitute_pn": "SUB_IND",
        "original_value": "", "status": "recommended", "notes": "",
    })
    assert row["status"] != "no_substitute"  # sourced original → verified normally
    assert "NO_ORIGINAL_DATA" not in row.get("guardrail_fires", [])
