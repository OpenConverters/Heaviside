"""Tests for :mod:`heaviside.librarian.tas`.

Covers, in order:

  * SCHEMA_MAP gate + load_validator memoisation
  * envelope-shape validation per category (incl. nested
    ``semiconductor.diode`` / ``semiconductor.igbt``)
  * validate_component throws on bad payload — strict-mode (not bool)
  * component_exists across all known MPN envelope variants
  * component_exists throws on corrupt JSON (no silent continue)
  * add_component happy path
  * add_component rejects duplicates
  * add_component rejects unvalidated rows
  * end-to-end against the **real** TAS schemas (one record per
    category sampled from the live NDJSON)

Path isolation
--------------

These tests retarget ``safe_access.TAS_DATA_DIR`` + ``LOCK_DIR`` at
a fresh ``tmp_path`` per test so they cannot mutate the real TAS
database.  The schema map is left pointing at the real schemas
(read-only), so we exercise the production registry hydration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from heaviside.librarian import safe_access as sa
from heaviside.librarian import tas

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _retarget_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Retarget TAS_DATA_DIR + LOCK_DIR per test; drop validator cache."""
    data_dir = tmp_path / "tas-data"
    lock_dir = tmp_path / "locks"
    data_dir.mkdir()
    monkeypatch.setattr(sa, "TAS_DATA_DIR", data_dir)
    monkeypatch.setattr(sa, "LOCK_DIR", lock_dir)
    # The validator cache is module-level; it's safe across tests
    # (schemas don't change), but we drop it to keep test isolation
    # clean when individual tests monkeypatch SCHEMA_MAP.
    tas._clear_validator_cache()


# Sample records cloned from the live NDJSON (heads at session start).
# Kept inline so the tests don't depend on the submodule layout
# beyond the schema files themselves.

_VALID_RECORDS: dict[str, dict[str, Any]] = {
    "mosfets": {
        "semiconductor": {
            "mosfet": {
                "manufacturerInfo": {
                    "name": "TEST-MFR",
                    "reference": "TEST-MOSFET-001",
                    "status": "production",
                    "datasheetInfo": {
                        "part": {
                            "partNumber": "TEST-MOSFET-001",
                            "technology": "Si",
                            "subType": "nChannel",
                            "case": "TO-220",
                        },
                        "electrical": {
                            "drainSourceVoltage": 100,
                            "gateSourceVoltageMax": 20,
                            "continuousDrainCurrent": 30,
                            "pulsedDrainCurrent": 120,
                            "powerDissipation": 50,
                            "onResistance": 0.025,
                            # SAS schema tightening (May 2026): the three
                            # below are now schema-required for mosfets.
                            "gateThresholdVoltage": {
                                "minimum": 2.0,
                                "nominal": 3.0,
                                "maximum": 4.0,
                            },
                            "outputCapacitance": 250e-12,
                            "totalGateCharge": 80e-9,
                        },
                    },
                },
            },
        },
    },
}


def _load_first_real_record(category: str) -> dict[str, Any]:
    """Pull the first record from the live TAS NDJSON for ``category``.

    Used by tests that want to exercise the real schema end-to-end
    without inventing payloads.  Skips the test if the file isn't
    available (e.g. partial submodule init in CI).
    """
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / "TAS" / "data" / f"{category}.ndjson"
    if not path.exists():
        pytest.skip(f"TAS/data/{category}.ndjson not available")
    with path.open("r", encoding="utf-8") as fh:
        line = fh.readline().strip()
    if not line:
        pytest.skip(f"TAS/data/{category}.ndjson is empty (no records yet)")
    return json.loads(line)


# ---------------------------------------------------------------------------
# SCHEMA_MAP / load_validator
# ---------------------------------------------------------------------------


class TestSchemaMap:
    def test_all_known_categories_load(self):
        for cat in tas.SCHEMA_MAP:
            v = tas.load_validator(cat)
            # Smoke: validator carries a schema and a registry.
            assert v.schema is not None

    def test_unknown_category_rejected(self):
        with pytest.raises(sa.UnknownCategoryError):
            tas.load_validator("not-a-category")

    def test_category_without_schema_rejected(self):
        # ``controllers`` is in CATEGORIES but deliberately absent
        # from SCHEMA_MAP — strict-mode refuses to validate it.
        assert "controllers" in sa.CATEGORIES
        assert "controllers" not in tas.SCHEMA_MAP
        with pytest.raises(tas.SchemaNotFoundError, match="controllers"):
            tas.load_validator("controllers")

    def test_validator_is_memoised(self):
        v1 = tas.load_validator("mosfets")
        v2 = tas.load_validator("mosfets")
        assert v1 is v2

    def test_missing_schema_file_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ):
        bogus = tmp_path / "missing.json"
        monkeypatch.setitem(
            tas.SCHEMA_MAP,
            "mosfets",
            (bogus, tas.SCHEMA_MAP["mosfets"][1]),
        )
        tas._clear_validator_cache()
        with pytest.raises(tas.SchemaNotFoundError, match=r"missing\.json"):
            tas.load_validator("mosfets")


# ---------------------------------------------------------------------------
# validate_component (envelope + payload)
# ---------------------------------------------------------------------------


class TestValidateEnvelope:
    def test_missing_top_envelope_throws(self):
        with pytest.raises(tas.ValidationError, match="semiconductor"):
            tas.validate_component("mosfets", {"wrong": {}})

    def test_diode_missing_outer_envelope_throws(self):
        with pytest.raises(tas.ValidationError, match="semiconductor"):
            tas.validate_component("diodes", {"diode": {}})

    def test_diode_missing_inner_envelope_throws(self):
        with pytest.raises(tas.ValidationError, match="diode"):
            tas.validate_component("diodes", {"semiconductor": {"mosfet": {}}})

    def test_igbt_two_level_envelope_required(self):
        with pytest.raises(tas.ValidationError, match="igbt"):
            tas.validate_component("igbts", {"semiconductor": {}})

    def test_non_dict_component_rejected(self):
        with pytest.raises(tas.ValidationError):
            tas.validate_component("mosfets", {"mosfet": "not-an-object"})


class TestValidateAgainstRealSchemas:
    """Exercise the live schema for each category with a real record."""

    @pytest.mark.parametrize("category", sorted(tas.SCHEMA_MAP))
    def test_first_live_record_validates(self, category: str):
        rec = _load_first_real_record(category)
        tas.validate_component(category, rec)  # must not raise

    def test_synthetic_mosfet_validates(self):
        tas.validate_component("mosfets", _VALID_RECORDS["mosfets"])

    def test_missing_required_field_raises_with_path(self):
        bad = json.loads(json.dumps(_VALID_RECORDS["mosfets"]))  # deep copy
        del bad["semiconductor"]["mosfet"]["manufacturerInfo"]
        with pytest.raises(tas.ValidationError) as exc_info:
            tas.validate_component("mosfets", bad)
        # Error list is exposed for programmatic inspection.
        assert exc_info.value.errors
        assert exc_info.value.category == "mosfets"


# ---------------------------------------------------------------------------
# component_exists
# ---------------------------------------------------------------------------


def _seed(category: str, records: list[dict[str, Any]]) -> Path:
    path = sa.TAS_DATA_DIR / f"{category}.ndjson"
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return path


class TestComponentExistsLookup:
    def test_returns_false_when_file_missing(self):
        assert tas.component_exists("mosfets", "ANY") is False

    def test_finds_mosfet_via_manufacturer_reference(self):
        _seed(
            "mosfets",
            [
                {
                    "semiconductor": {
                        "mosfet": {
                            "manufacturerInfo": {"reference": "EPC2019"},
                        }
                    },
                }
            ],
        )
        assert tas.component_exists("mosfets", "EPC2019") is True
        assert tas.component_exists("mosfets", "epc2019") is True  # case-insens
        assert tas.component_exists("mosfets", "NOPE") is False

    def test_finds_diode_via_nested_semiconductor_envelope(self):
        _seed(
            "diodes",
            [
                {
                    "semiconductor": {
                        "diode": {
                            "manufacturerInfo": {"reference": "STPS30L60CT"},
                        }
                    },
                }
            ],
        )
        assert tas.component_exists("diodes", "STPS30L60CT") is True

    def test_finds_igbt_via_nested_envelope(self):
        _seed(
            "igbts",
            [
                {
                    "semiconductor": {
                        "igbt": {
                            "manufacturerInfo": {"reference": "2MBI100XAA120-50"},
                        }
                    },
                }
            ],
        )
        assert tas.component_exists("igbts", "2MBI100XAA120-50") is True

    def test_finds_capacitor_via_reference_field(self):
        _seed(
            "capacitors",
            [
                {
                    "capacitor": {"manufacturerInfo": {"reference": "GRM188R71C"}},
                }
            ],
        )
        assert tas.component_exists("capacitors", "GRM188R71C") is True

    def test_finds_capacitor_via_datasheet_part_number(self):
        _seed(
            "capacitors",
            [
                {
                    "capacitor": {
                        "manufacturerInfo": {
                            "datasheetInfo": {"part": {"partNumber": "UPW1H102MHD"}},
                        }
                    },
                }
            ],
        )
        assert tas.component_exists("capacitors", "UPW1H102MHD") is True

    def test_finds_resistor_via_datasheet_part_number(self):
        _seed(
            "resistors",
            [
                {
                    "resistor": {
                        "manufacturerInfo": {
                            "datasheetInfo": {"part": {"partNumber": "WSL2512"}},
                        }
                    },
                }
            ],
        )
        assert tas.component_exists("resistors", "WSL2512") is True

    def test_finds_magnetic_via_reference(self):
        _seed(
            "magnetics",
            [
                {
                    "magnetic": {"manufacturerInfo": {"reference": "744383560R33"}},
                }
            ],
        )
        assert tas.component_exists("magnetics", "744383560R33") is True

    def test_blank_lines_are_skipped(self):
        path = sa.TAS_DATA_DIR / "mosfets.ndjson"
        path.write_text(
            "\n"
            + json.dumps(
                {
                    "semiconductor": {
                        "mosfet": {
                            "manufacturerInfo": {"reference": "X"},
                        }
                    }
                }
            )
            + "\n\n",
            encoding="utf-8",
        )
        assert tas.component_exists("mosfets", "X") is True

    def test_empty_part_number_rejected(self):
        with pytest.raises(tas.LibrarianError, match="non-empty"):
            tas.component_exists("mosfets", "")


class TestComponentExistsCorruption:
    """Strict-mode: corrupt rows are an error, never silently skipped."""

    def test_invalid_json_line_throws(self):
        path = sa.TAS_DATA_DIR / "mosfets.ndjson"
        path.write_text("{not json}\n", encoding="utf-8")
        with pytest.raises(tas.LibrarianError, match="corrupt JSON"):
            tas.component_exists("mosfets", "ANY")

    def test_non_object_line_throws(self):
        path = sa.TAS_DATA_DIR / "mosfets.ndjson"
        path.write_text("[1,2,3]\n", encoding="utf-8")
        with pytest.raises(tas.LibrarianError, match="expected JSON object"):
            tas.component_exists("mosfets", "ANY")


# ---------------------------------------------------------------------------
# add_component
# ---------------------------------------------------------------------------


class TestAddComponent:
    def test_happy_path_appends_validated_row(self):
        tas.add_component("mosfets", _VALID_RECORDS["mosfets"])
        path = sa.TAS_DATA_DIR / "mosfets.ndjson"
        assert path.exists()
        lines = path.read_text().splitlines()
        assert len(lines) == 1
        round_trip = json.loads(lines[0])
        assert (
            round_trip["semiconductor"]["mosfet"]["manufacturerInfo"]["reference"]
            == "TEST-MOSFET-001"
        )

    def test_compact_json_no_whitespace(self):
        tas.add_component("mosfets", _VALID_RECORDS["mosfets"])
        line = (sa.TAS_DATA_DIR / "mosfets.ndjson").read_text().splitlines()[0]
        # Compact form has no ": " separator and no ", " — strict
        # AGENTS.md format guarantee for the live NDJSON.
        assert ", " not in line
        assert ": " not in line

    def test_duplicate_rejected(self):
        tas.add_component("mosfets", _VALID_RECORDS["mosfets"])
        with pytest.raises(tas.DuplicateComponentError, match="TEST-MOSFET-001"):
            tas.add_component("mosfets", _VALID_RECORDS["mosfets"])
        # Only one row written.
        lines = (sa.TAS_DATA_DIR / "mosfets.ndjson").read_text().splitlines()
        assert len(lines) == 1

    def test_unvalidated_payload_rejected_before_write(self):
        bad = {"mosfet": {"manufacturerInfo": {"reference": "FOO"}}}  # missing required electrical
        with pytest.raises(tas.ValidationError):
            tas.add_component("mosfets", bad)
        # File must not have been created.
        assert not (sa.TAS_DATA_DIR / "mosfets.ndjson").exists()

    def test_anonymous_row_rejected(self):
        # A schema-valid payload with no extractable MPN should also
        # be refused — we do not write rows that can't be looked up.
        # Easiest construction: an envelope where validation would
        # pass for a different category.  We synthesise by stubbing
        # the validator to accept anything.
        class _AcceptAll:
            def iter_errors(self, _):
                return []

        # Override the validator cache for "mosfets" only for this test.
        with tas._VALIDATOR_LOCK:
            tas._VALIDATOR_CACHE["mosfets"] = _AcceptAll()
        try:
            # The insert guard (heaviside.librarian.guards) now rejects
            # anonymous rows before add_component's own MPN extraction;
            # GuardRejectionError is a LibrarianError subclass.
            with pytest.raises(tas.LibrarianError, match="no non-empty partNumber"):
                tas.add_component("mosfets", {"semiconductor": {"mosfet": {}}})
        finally:
            tas._clear_validator_cache()

    def test_wrong_envelope_rejected_at_validation(self):
        with pytest.raises(tas.ValidationError):
            tas.add_component(
                "mosfets",
                {"diode": {"manufacturerInfo": {"reference": "X"}}},
            )

    def test_non_dict_component_rejected(self):
        with pytest.raises(tas.LibrarianError, match="must be a dict"):
            tas.add_component("mosfets", "not a dict")  # type: ignore[arg-type]

    def test_unknown_category_rejected(self):
        with pytest.raises(sa.UnknownCategoryError):
            tas.add_component("typo", _VALID_RECORDS["mosfets"])

    def test_category_without_schema_rejected(self):
        with pytest.raises(tas.SchemaNotFoundError):
            tas.add_component("controllers", {})

    def test_add_then_exists_round_trip(self):
        tas.add_component("mosfets", _VALID_RECORDS["mosfets"])
        assert tas.component_exists("mosfets", "TEST-MOSFET-001") is True
        assert tas.component_exists("mosfets", "test-mosfet-001") is True


# ---------------------------------------------------------------------------
# _extract_mpn (internal but worth exercising — used in error labels)
# ---------------------------------------------------------------------------


class TestExtractMpn:
    def test_mosfet_envelope(self):
        assert (
            tas._extract_mpn(
                {
                    "mosfet": {"manufacturerInfo": {"reference": "M1"}},
                }
            )
            == "M1"
        )

    def test_diode_nested_envelope(self):
        assert (
            tas._extract_mpn(
                {
                    "semiconductor": {
                        "diode": {
                            "manufacturerInfo": {"reference": "D1"},
                        }
                    },
                }
            )
            == "D1"
        )

    def test_falls_back_to_datasheet_part_number(self):
        assert (
            tas._extract_mpn(
                {
                    "resistor": {
                        "manufacturerInfo": {
                            "datasheetInfo": {"part": {"partNumber": "R1"}},
                        }
                    },
                }
            )
            == "R1"
        )

    def test_legacy_top_level_manufacturer_info(self):
        assert (
            tas._extract_mpn(
                {
                    "manufacturerInfo": {"reference": "LEGACY"},
                }
            )
            == "LEGACY"
        )

    def test_unknown_when_nothing_present(self):
        assert tas._extract_mpn({"unrelated": 42}) == "UNKNOWN"
