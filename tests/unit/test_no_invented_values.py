"""No LLM-invented data may reach a cross-reference result.

Locks in the safeguards added after a pasted bare MPN (850617021001, a 5 F /
2.7 V supercapacitor) shipped as "100nF / 630V" with two different fabricated
packages and a "connector" category:

* the catalogue index must see parts keyed by ``part.partNumber`` (capacitors /
  resistors carry no ``reference``) and must cover connectors;
* ``lookup_mpn_category`` answers "which kind of part is this MPN" from the
  catalogue, so untyped BOM rows are never left for the LLM to guess;
* ``_restore_component_types`` re-imposes the engine-derived category on every
  LLM-returned row;
* ``_ground_row_fields_in_catalogue`` replaces LLM-echoed value / voltage /
  package fields with BOM or catalogue data — or blanks them (honest unknown);
* G5c demotes a real-but-wrong-CATEGORY substitute, G5d a real-but-wrong-
  MANUFACTURER substitute;
* dielectric comparison passes two EQUAL non-ceramic technologies.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from heaviside.pipeline import guardrails
from heaviside.pipeline.crossref import CrossRefState
from heaviside.pipeline.crossref_pipeline import (
    _fields_from_catalogue,
    _ground_row_fields_in_catalogue,
    _restore_component_types,
)
from heaviside.pipeline.guardrails import (
    _g5_substitute_existence,
    _mpn_exists_in_tas,
    lookup_mpn_category,
    lookup_part_fields,
)

pytestmark = pytest.mark.unit

# ── Fixture catalogue ────────────────────────────────────────────────────────
# Minimal NDJSON corpus exercising both MPN keying styles:
#  * capacitors / resistors: MPN only in part.partNumber (no reference)
#  * magnetics / connectors: MPN in manufacturerInfo.reference

WURTH_CAP = "885012208001"  # Würth, 10 µF 25 V, 0805
MURATA_CAP = "GRM21BR61E106KA73"  # Murata — real part, WRONG manufacturer
WURTH_IND = "744770147"  # Würth, 47 µH, keyed by reference
CONNECTOR = "CONN-0001"  # Molex connector, keyed by reference
UNKNOWN = "TOTALLY-INVENTED-99"  # not catalogued anywhere


def _cap_env(mpn: str, manufacturer: str) -> dict:
    return {
        "capacitor": {
            "manufacturerInfo": {
                "name": manufacturer,
                "datasheetInfo": {
                    "part": {"partNumber": mpn, "case": "0805"},
                    "electrical": {"capacitance": {"nominal": 1e-05}, "ratedVoltage": 25.0},
                },
            }
        }
    }


def _mag_env(mpn: str, manufacturer: str) -> dict:
    return {
        "magnetic": {
            "manufacturerInfo": {
                "name": manufacturer,
                "reference": mpn,
                "datasheetInfo": {
                    "part": {"caseCode": "1280"},
                    "electrical": [{"inductance": {"nominal": 4.7e-05}}],
                },
            }
        }
    }


def _conn_env(mpn: str, manufacturer: str) -> dict:
    return {
        "connector": {
            "manufacturerInfo": {
                "name": manufacturer,
                "reference": mpn,
                "datasheetInfo": {
                    "part": {"partNumber": mpn},
                    "electrical": {"ratedVoltage": 250.0},
                },
            }
        }
    }


WURTH_RES_10R = "560050310009"  # Würth, 10 Ω — wrong-value bait for a 10 kΩ row


def _res_env(mpn: str, manufacturer: str, ohms: float) -> dict:
    return {
        "resistor": {
            "manufacturerInfo": {
                "name": manufacturer,
                "datasheetInfo": {
                    "part": {"partNumber": mpn, "case": "0402"},
                    "electrical": {"resistance": {"nominal": ohms}},
                },
            }
        }
    }


@pytest.fixture()
def tas_dir(tmp_path: Path) -> Path:
    (tmp_path / "capacitors.ndjson").write_text(
        json.dumps(_cap_env(WURTH_CAP, "Würth Elektronik"))
        + "\n"
        + json.dumps(_cap_env(MURATA_CAP, "Murata"))
        + "\n"
    )
    (tmp_path / "magnetics.ndjson").write_text(
        json.dumps(_mag_env(WURTH_IND, "Würth Elektronik")) + "\n"
    )
    (tmp_path / "connectors.ndjson").write_text(json.dumps(_conn_env(CONNECTOR, "Molex")) + "\n")
    (tmp_path / "resistors.ndjson").write_text(
        json.dumps(_res_env(WURTH_RES_10R, "Würth Elektronik", 10.0)) + "\n"
    )
    return tmp_path


# ── Index keying ─────────────────────────────────────────────────────────────


def test_index_sees_partnumber_keyed_and_connector_parts(tas_dir: Path) -> None:
    # Capacitors have no manufacturerInfo.reference — keyed by part.partNumber.
    assert _mpn_exists_in_tas(WURTH_CAP, tas_data_dir=tas_dir)
    # Connectors were previously not indexed at all.
    assert _mpn_exists_in_tas(CONNECTOR, tas_data_dir=tas_dir)
    assert not _mpn_exists_in_tas(UNKNOWN, tas_data_dir=tas_dir)


def test_lookup_mpn_category_is_authoritative(tas_dir: Path) -> None:
    assert lookup_mpn_category(WURTH_CAP, tas_data_dir=tas_dir) == "capacitor"
    assert lookup_mpn_category(WURTH_IND, tas_data_dir=tas_dir) == "magnetic"
    assert lookup_mpn_category(CONNECTOR, tas_data_dir=tas_dir) == "connector"
    assert lookup_mpn_category(UNKNOWN, tas_data_dir=tas_dir) is None


def test_lookup_part_fields_flat_record(tas_dir: Path) -> None:
    rec = lookup_part_fields(WURTH_IND, "magnetic", tas_data_dir=tas_dir)
    assert rec is not None
    assert rec["inductance"] == pytest.approx(4.7e-05)
    assert rec["package"] == "1280"


# ── LLM may not relabel component types ──────────────────────────────────────


def test_restore_component_types_overrides_llm_relabel() -> None:
    state = CrossRefState(source_bom=[], target_manufacturer="Würth Elektronik")
    state.crossref_result = [
        {"ref_des": "C1", "component_type": "connector"},  # LLM's guess
        {"ref_des": "R1", "component_type": "resistor"},  # already right
        {"ref_des": "X9"},  # untyped input stays untouched
    ]
    bom_for_llm = [
        {"ref_des": "C1", "component_type": "capacitor"},
        {"ref_des": "R1", "component_type": "resistor"},
        {"ref_des": "X9"},
    ]
    _restore_component_types(state, bom_for_llm)
    assert state.crossref_result[0]["component_type"] == "capacitor"
    assert state.crossref_result[1]["component_type"] == "resistor"
    assert "component_type" not in state.crossref_result[2]


# ── Report fields are grounded, never echoed ─────────────────────────────────


def test_grounding_replaces_fabricated_fields(
    tas_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(guardrails, "_TAS_DATA_DEFAULT", tas_dir)
    state = CrossRefState(
        source_bom=[{"ref_des": "CMP#0"}],  # bare MPN row: no value/package
        target_manufacturer="Würth Elektronik",
    )
    state.crossref_result = [
        {
            "ref_des": "CMP#0",
            "component_type": "capacitor",
            "original_pn": WURTH_CAP,
            "substitute_pn": WURTH_CAP,
            "status": "exact",
            # LLM fabrications — none of this matches the catalogue:
            "original_value": "100nF",
            "original_voltage": "630V",
            "original_package": "13.0x4.0x9.0",
            "substitute_value": "100nF",
            "substitute_voltage": "630V",
            "substitute_package": "18.0x5.0x11.0",
        }
    ]
    _ground_row_fields_in_catalogue(state)
    row = state.crossref_result[0]
    assert row["original_value"] == "10.0µF"
    assert row["substitute_value"] == "10.0µF"
    assert row["original_voltage"] == "25V"
    assert row["substitute_voltage"] == "25V"
    assert row["original_package"] == "0805"
    assert row["substitute_package"] == "0805"


def test_grounding_blanks_unknown_parts_instead_of_echoing(
    tas_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(guardrails, "_TAS_DATA_DEFAULT", tas_dir)
    state = CrossRefState(
        source_bom=[{"ref_des": "U7"}],
        target_manufacturer="Würth Elektronik",
    )
    state.crossref_result = [
        {
            "ref_des": "U7",
            "component_type": "capacitor",
            "original_pn": UNKNOWN,
            "substitute_pn": "no_substitute",
            "status": "no_substitute",
            "original_value": "4.7uF",  # LLM invention — part is uncatalogued
            "original_voltage": "50V",
            "original_package": "0603",
        }
    ]
    _ground_row_fields_in_catalogue(state)
    row = state.crossref_result[0]
    assert row["original_value"] == ""
    assert row["original_voltage"] == ""
    assert row["original_package"] == ""


def test_grounding_bom_value_beats_catalogue(
    tas_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(guardrails, "_TAS_DATA_DEFAULT", tas_dir)
    state = CrossRefState(
        source_bom=[{"ref_des": "C3", "value": "22uF", "package": "1210"}],
        target_manufacturer="Würth Elektronik",
    )
    state.crossref_result = [
        {
            "ref_des": "C3",
            "component_type": "capacitor",
            "original_pn": UNKNOWN,
            "substitute_pn": WURTH_CAP,
            "status": "recommended",
            "original_value": "totally-else",
            "substitute_value": "totally-else",
        }
    ]
    _ground_row_fields_in_catalogue(state)
    row = state.crossref_result[0]
    # The user's BOM is the requirement — it wins for the original side.
    assert row["original_value"] == "22uF"
    assert row["original_package"] == "1210"
    # The substitute side always comes from the catalogue.
    assert row["substitute_value"] == "10.0µF"
    assert row["substitute_package"] == "0805"


def test_fields_from_catalogue_unknown_is_none(
    tas_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(guardrails, "_TAS_DATA_DEFAULT", tas_dir)
    assert _fields_from_catalogue(UNKNOWN, "capacitor") is None


# ── G5c / G5d: real part, wrong category / manufacturer ─────────────────────


def _g5_row(sub_pn: str, component_type: str = "capacitor") -> dict:
    return {
        "ref_des": "C1",
        "component_type": component_type,
        "original_pn": "SOMETHING",
        "substitute_pn": sub_pn,
        "status": "recommended",
    }


def test_g5c_wrong_category_substitute_demoted(tas_dir: Path) -> None:
    comp = _g5_row(CONNECTOR, component_type="capacitor")
    fires: list[dict] = []
    _g5_substitute_existence(
        [comp], fires, target_manufacturer="Würth Elektronik", tas_data_dir=tas_dir
    )
    assert comp["status"] == "no_substitute"
    assert comp["substitute_pn"] == "no_substitute"
    assert any(f["guardrail_id"] == "5c" for f in fires)


def test_g5d_wrong_manufacturer_substitute_demoted(tas_dir: Path) -> None:
    comp = _g5_row(MURATA_CAP, component_type="capacitor")
    fires: list[dict] = []
    _g5_substitute_existence(
        [comp], fires, target_manufacturer="Würth Elektronik", tas_data_dir=tas_dir
    )
    assert comp["status"] == "no_substitute"
    assert any(f["guardrail_id"] == "5d" for f in fires)


def test_g5_valid_target_substitute_untouched(tas_dir: Path) -> None:
    comp = _g5_row(WURTH_CAP, component_type="capacitor")
    fires: list[dict] = []
    _g5_substitute_existence(
        [comp], fires, target_manufacturer="Würth Elektronik", tas_data_dir=tas_dir
    )
    assert comp["status"] == "recommended"
    assert comp["substitute_pn"] == WURTH_CAP
    assert fires == []


def test_g5_uncatalogued_substitute_demoted(tas_dir: Path) -> None:
    comp = _g5_row(UNKNOWN)
    fires: list[dict] = []
    _g5_substitute_existence(
        [comp], fires, target_manufacturer="Würth Elektronik", tas_data_dir=tas_dir
    )
    assert comp["status"] == "no_substitute"
    assert any(f["guardrail_id"] in ("5", "5b") for f in fires)


# ── Bare-MPN rows are fully specified from the catalogue at normalize time ──


def test_normalize_backfills_bare_mpn_from_catalogue(
    tas_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from heaviside.pipeline.crossref_pipeline import _normalize_bom

    monkeypatch.setattr(guardrails, "_TAS_DATA_DEFAULT", tas_dir)
    rows = _normalize_bom([{"original_mpn": WURTH_CAP}])
    row = rows[0]
    # Category, value and package all come from the catalogue — candidate
    # ranking can value-filter and G1/G2 have something to check against.
    assert row["component_type"] == "capacitor"
    assert row["value"] == "10.0µF"
    assert row["package"] == "0805"
    assert row["rated_voltage"] == "25V"


# ── G2 must see component_type rows (a 10 Ω pick for a 10 kΩ row) ───────────


def test_g2_wrong_value_resistor_demoted_on_component_type_row(tas_dir: Path) -> None:
    from heaviside.pipeline.guardrails import _g2_resistor_value_drift

    comp = {
        "ref_des": "R1",
        "component_type": "resistor",
        "original_pn": "SOME-10K-PART",
        "substitute_pn": WURTH_RES_10R,  # catalogued as 10 Ω
        "status": "recommended",
    }
    bom_by_ref = {"R1": {"ref_des": "R1", "component_type": "resistor", "value": "10k"}}
    fires: list[dict] = []
    _g2_resistor_value_drift([comp], bom_by_ref, fires, tas_data_dir=tas_dir)
    assert comp["status"] == "no_substitute"
    assert any(f["guardrail_id"] == "2a" for f in fires)


# ── Dielectric: equal technologies pass ──────────────────────────────────────


def test_dielectric_same_non_ceramic_technology_passes() -> None:
    from heaviside.pipeline.param_check import PARAM_SPECS, compare_param

    spec = next(s for s in PARAM_SPECS["capacitor"] if s.key == "technology")
    result = compare_param(spec, "supercapacitor-edlc", "supercapacitor-edlc")
    assert result["verdict"] == "pass"


def test_dielectric_different_technology_warns() -> None:
    from heaviside.pipeline.param_check import PARAM_SPECS, compare_param

    spec = next(s for s in PARAM_SPECS["capacitor"] if s.key == "technology")
    result = compare_param(spec, "film-polypropylene", "aluminum-electrolytic-wet")
    assert result["verdict"] == "warn"
