"""G8 (current stress) and G9 (saturation margin) must actually fire.

Both guardrails compare a substitute's TAS-catalogued rating against the
sim-derived peak stress. Their rating lookups previously returned None
unconditionally, so the checks silently never demoted anything despite real
stress_by_ref data. These tests drive the lookups against a stubbed TAS
envelope so the gate logic is exercised without the parts DB.
"""

from __future__ import annotations

from types import SimpleNamespace

from heaviside.pipeline import guardrails


def _env_mosfet(id_cont: float) -> dict:
    return {
        "semiconductor": {
            "mosfet": {
                "manufacturerInfo": {
                    "datasheetInfo": {"electrical": {"continuousDrainCurrent": id_cont}}
                }
            }
        }
    }


def _env_magnetic(isat: float) -> dict:
    return {
        "magnetic": {
            "manufacturerInfo": {
                "datasheetInfo": {"electrical": [{"saturationCurrentPeak": isat}]}
            }
        }
    }


def test_lookup_reads_current_and_isat_from_envelope(monkeypatch) -> None:
    monkeypatch.setattr(
        guardrails, "_lookup_tas_part", lambda pn, cat, tas_data_dir=None: {"raw_envelope": _env_mosfet(8.5)}
    )
    comp = {"substitute_pn": "STUB-FET", "component_type": "mosfet"}
    assert guardrails._lookup_substitute_current(comp, "mosfet", None) == 8.5

    monkeypatch.setattr(
        guardrails, "_lookup_tas_part", lambda pn, cat, tas_data_dir=None: {"raw_envelope": _env_magnetic(12.4)}
    )
    mag = {"substitute_pn": "STUB-L", "component_type": "magnetic"}
    assert guardrails._lookup_substitute_isat(mag, "magnetic", None) == 12.4


def test_g8_demotes_when_peak_exceeds_rating(monkeypatch) -> None:
    monkeypatch.setattr(
        guardrails, "_lookup_tas_part", lambda pn, cat, tas_data_dir=None: {"raw_envelope": _env_mosfet(5.0)}
    )
    comps = [{"ref_des": "Q1", "component_type": "mosfet", "substitute_pn": "STUB-FET", "status": "recommended"}]
    stress = {"Q1": SimpleNamespace(i_peak=8.0)}  # 8 A peak > 5 A rating
    fires: list[dict] = []
    guardrails._g8_current_stress(comps, stress, fires)
    assert comps[0]["status"] == "partial"
    assert any(f["guardrail_id"] == "G8_CurrentStress" for f in fires)


def test_g8_ok_when_rating_sufficient(monkeypatch) -> None:
    monkeypatch.setattr(
        guardrails, "_lookup_tas_part", lambda pn, cat, tas_data_dir=None: {"raw_envelope": _env_mosfet(20.0)}
    )
    comps = [{"ref_des": "Q1", "component_type": "mosfet", "substitute_pn": "STUB-FET", "status": "recommended"}]
    stress = {"Q1": SimpleNamespace(i_peak=8.0)}  # within 20 A rating
    fires: list[dict] = []
    guardrails._g8_current_stress(comps, stress, fires)
    assert comps[0]["status"] == "recommended"
    assert fires == []


def test_g9_demotes_when_peak_exceeds_isat_margin(monkeypatch) -> None:
    monkeypatch.setattr(
        guardrails, "_lookup_tas_part", lambda pn, cat, tas_data_dir=None: {"raw_envelope": _env_magnetic(10.0)}
    )
    comps = [{"ref_des": "L1", "component_type": "magnetic", "substitute_pn": "STUB-L", "status": "recommended"}]
    # i_peak beyond SATURATION_MARGIN * 10 A
    stress = {"L1": SimpleNamespace(i_peak=10.0 * guardrails.SATURATION_MARGIN + 1.0)}
    fires: list[dict] = []
    guardrails._g9_saturation_margin(comps, stress, fires)
    assert comps[0]["status"] == "partial"
    assert any(f["guardrail_id"] == "G9_SaturationMargin" for f in fires)
