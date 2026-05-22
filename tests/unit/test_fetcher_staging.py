"""Tests for ``heaviside.librarian.fetcher.staging``.

Covers:

* :func:`stage_fetch` — writes the expected JSON envelope, creates
  parent dirs, rejects unknown categories / sources, sanitises
  awkward MPN characters, and is atomic (no ``*.tmp`` debris).
* :func:`apply_staged` — schema-validates the staged record,
  appends to TAS via ``add_component``, and archives the staging
  file to ``applied/``.  Failures leave the staging file in place.
* :func:`list_staged` — filters by category, ignores the
  ``applied/`` archive, ignores non-JSON debris.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from heaviside.librarian import safe_access as sa
from heaviside.librarian import tas as tas_mod
from heaviside.librarian.fetcher import staging as staging_mod
from heaviside.librarian.fetcher.staging import (
    StagedRecord,
    StagingError,
    apply_staged,
    list_staged,
    stage_fetch,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _retarget_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Retarget TAS_DATA_DIR + LOCK_DIR + STAGING_DIR per test."""
    data_dir = tmp_path / "tas-data"
    lock_dir = tmp_path / "locks"
    staging_dir = tmp_path / "staging"
    data_dir.mkdir()
    monkeypatch.setattr(sa, "TAS_DATA_DIR", data_dir)
    monkeypatch.setattr(sa, "LOCK_DIR", lock_dir)
    monkeypatch.setattr(staging_mod, "STAGING_DIR", staging_dir)
    tas_mod._clear_validator_cache()


def _valid_mosfet(mpn: str = "TEST-MOSFET-001") -> dict[str, Any]:
    """Schema-valid mosfet envelope (mirrors test_librarian_tas's fixture)."""
    return {
        "semiconductor": {
            "mosfet": {
                "manufacturerInfo": {
                    "name": "TEST-MFR",
                    "reference": mpn,
                    "status": "production",
                    "datasheetInfo": {
                        "part": {
                            "partNumber": mpn,
                            "technology": "Si",
                            "subType": "nChannel",
                            "case": "TO-220",
                        },
                        "electrical": {
                            "drainSourceVoltage": 100,
                            "onResistance": 0.025,
                            "continuousDrainCurrent": 30,
                            "gateThresholdVoltage": {
                                "minimum": 2.0, "nominal": 3.0, "maximum": 4.0,
                            },
                            "outputCapacitance": 250e-12,
                            "totalGateCharge": 80e-9,
                        },
                    },
                },
            },
        },
    }


# ---------------------------------------------------------------------------
# stage_fetch
# ---------------------------------------------------------------------------


def test_stage_fetch_writes_expected_envelope(tmp_path: Path) -> None:
    target = stage_fetch(
        "mosfets",
        "TEST-MOSFET-001",
        _valid_mosfet(),
        source="digikey",
    )
    assert target.exists()
    assert target.parent.name == "mosfets"
    assert target.name == "digikey-TEST-MOSFET-001.json"

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["category"] == "mosfets"
    assert payload["source"] == "digikey"
    assert payload["mpn"] == "TEST-MOSFET-001"
    assert payload["component"]["semiconductor"]["mosfet"]["manufacturerInfo"]["name"] == "TEST-MFR"
    assert isinstance(payload["staged_at"], float)
    assert payload["staged_at"] == pytest.approx(time.time(), abs=10.0)
    assert "raw_response" not in payload  # not provided


def test_stage_fetch_persists_raw_response_when_supplied() -> None:
    raw = {"DigiKeyPartNumber": "abc", "Manufacturer": {"Value": "X"}}
    target = stage_fetch(
        "mosfets", "MPN-A", _valid_mosfet("MPN-A"), source="digikey", raw_response=raw,
    )
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["raw_response"] == raw


def test_stage_fetch_sanitises_awkward_mpn() -> None:
    target = stage_fetch(
        "mosfets", "IRF/B007+ABC", _valid_mosfet("IRF/B007+ABC"), source="mouser",
    )
    # Slashes and plus signs are unsafe; underscore-replaced.
    assert target.name == "mouser-IRF_B007_ABC.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    # Original MPN preserved in the payload.
    assert payload["mpn"] == "IRF/B007+ABC"


def test_stage_fetch_rejects_unknown_category() -> None:
    with pytest.raises(StagingError, match="unknown category"):
        stage_fetch("widgets", "X", _valid_mosfet(), source="digikey")


def test_stage_fetch_rejects_unknown_source() -> None:
    with pytest.raises(StagingError, match="unknown source"):
        stage_fetch("mosfets", "X", _valid_mosfet(), source="scraper")


def test_stage_fetch_rejects_empty_mpn() -> None:
    with pytest.raises(StagingError, match="non-empty string"):
        stage_fetch("mosfets", "", _valid_mosfet(), source="digikey")


def test_stage_fetch_rejects_empty_component() -> None:
    with pytest.raises(StagingError, match="non-empty dict"):
        stage_fetch("mosfets", "X", {}, source="digikey")


def test_stage_fetch_is_atomic_no_tmp_debris() -> None:
    target = stage_fetch("mosfets", "A", _valid_mosfet("A"), source="digikey")
    leftovers = list(target.parent.glob("*.tmp"))
    assert leftovers == []


def test_stage_fetch_overwrites_prior_record_for_same_mpn(tmp_path: Path) -> None:
    """Re-fetching the same MPN replaces the staging file (last-fetch wins)."""
    first = stage_fetch("mosfets", "X", _valid_mosfet("X"), source="digikey")
    first_payload = json.loads(first.read_text())
    time.sleep(0.01)
    second = stage_fetch("mosfets", "X", _valid_mosfet("X"), source="digikey")
    assert first == second
    second_payload = json.loads(second.read_text())
    assert second_payload["staged_at"] >= first_payload["staged_at"]


# ---------------------------------------------------------------------------
# StagedRecord
# ---------------------------------------------------------------------------


def test_staged_record_round_trips() -> None:
    target = stage_fetch("mosfets", "A", _valid_mosfet("A"), source="digikey")
    record = StagedRecord.from_path(target)
    assert record.category == "mosfets"
    assert record.source == "digikey"
    assert record.mpn == "A"
    assert record.component["semiconductor"]["mosfet"]["manufacturerInfo"]["reference"] == "A"


def test_staged_record_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(StagingError, match="does not exist"):
        StagedRecord.from_path(tmp_path / "nope.json")


def test_staged_record_invalid_json_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(StagingError, match="invalid JSON"):
        StagedRecord.from_path(bad)


def test_staged_record_non_object_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(StagingError, match="must be an object"):
        StagedRecord.from_path(bad)


def test_staged_record_missing_required_key_raises(tmp_path: Path) -> None:
    bad = tmp_path / "incomplete.json"
    bad.write_text(json.dumps({"category": "mosfets"}), encoding="utf-8")
    with pytest.raises(StagingError, match="missing required key"):
        StagedRecord.from_path(bad)


def test_staged_record_component_wrong_type_raises(tmp_path: Path) -> None:
    bad = tmp_path / "wrong.json"
    bad.write_text(
        json.dumps(
            {
                "category": "mosfets",
                "source": "digikey",
                "mpn": "X",
                "component": "not-a-dict",
                "staged_at": 0.0,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(StagingError, match="'component' must be an object"):
        StagedRecord.from_path(bad)


# ---------------------------------------------------------------------------
# apply_staged
# ---------------------------------------------------------------------------


def test_apply_staged_writes_to_tas_and_archives() -> None:
    target = stage_fetch(
        "mosfets", "TEST-MOSFET-001", _valid_mosfet(), source="digikey",
    )
    result = apply_staged(target)

    # Component landed in the temp TAS ndjson.
    tas_file = sa.TAS_DATA_DIR / "mosfets.ndjson"
    assert tas_file.exists()
    line = tas_file.read_text(encoding="utf-8").splitlines()[0]
    record = json.loads(line)
    assert record["semiconductor"]["mosfet"]["manufacturerInfo"]["reference"] == "TEST-MOSFET-001"

    # Staging file got moved to applied/<ts>-<name>.
    assert not target.exists()
    archive_path = Path(result["archive_path"])
    assert archive_path.exists()
    assert archive_path.parent.name == "applied"
    assert archive_path.name.endswith(target.name)
    assert result["category"] == "mosfets"
    assert result["mpn"] == "TEST-MOSFET-001"


def test_apply_staged_archive_false_leaves_file_in_place() -> None:
    target = stage_fetch(
        "mosfets", "MPN-Z", _valid_mosfet("MPN-Z"), source="digikey",
    )
    result = apply_staged(target, archive=False)
    assert target.exists()
    assert result["archive_path"] is None


def test_apply_staged_validation_failure_leaves_file_in_place(tmp_path: Path) -> None:
    """A staged component that fails SAS validation must surface the error
    and not silently archive."""
    broken = _valid_mosfet("BROKEN")
    # Drop the required electrical.onResistance field.
    del broken["semiconductor"]["mosfet"]["manufacturerInfo"]["datasheetInfo"]["electrical"][
        "onResistance"
    ]
    target = stage_fetch("mosfets", "BROKEN", broken, source="digikey")

    with pytest.raises(tas_mod.ValidationError):
        apply_staged(target)

    # File still in place — not archived, not deleted.
    assert target.exists()
    # And nothing landed in TAS.
    tas_file = sa.TAS_DATA_DIR / "mosfets.ndjson"
    assert not tas_file.exists() or tas_file.read_text() == ""


def test_apply_staged_duplicate_propagates() -> None:
    """Re-applying the same MPN must raise DuplicateComponentError
    rather than silently double-inserting."""
    target = stage_fetch("mosfets", "DUP", _valid_mosfet("DUP"), source="digikey")
    apply_staged(target, archive=False)
    # Stage again (same MPN) and try to apply — second insert is the duplicate.
    target2 = stage_fetch("mosfets", "DUP", _valid_mosfet("DUP"), source="digikey")
    with pytest.raises(tas_mod.DuplicateComponentError):
        apply_staged(target2)
    assert target2.exists()  # not archived on failure


# ---------------------------------------------------------------------------
# list_staged
# ---------------------------------------------------------------------------


def test_list_staged_empty_when_root_missing(tmp_path: Path) -> None:
    assert list_staged() == []


def test_list_staged_returns_pending_only_skips_applied() -> None:
    a = stage_fetch("mosfets", "A", _valid_mosfet("A"), source="digikey")
    b = stage_fetch("mosfets", "B", _valid_mosfet("B"), source="digikey")
    apply_staged(a)  # moves to applied/

    pending = list_staged("mosfets")
    mpns = sorted(r.mpn for r in pending)
    assert mpns == ["B"]
    # And the applied dir's archive must NOT be returned.
    assert (b.parent / "applied").is_dir()
    assert all(r.path.parent.name == "mosfets" for r in pending)


def test_list_staged_filters_by_category() -> None:
    # Need at least one schema-valid record per category we touch.
    stage_fetch("mosfets", "M1", _valid_mosfet("M1"), source="digikey")
    # Drop a synthetic capacitor staging record (no apply, just discover).
    stage_fetch(
        "capacitors",
        "CAP-1",
        {"capacitor": {"manufacturerInfo": {"name": "X"}}},
        source="digikey",
    )

    mosfets_only = list_staged("mosfets")
    assert [r.mpn for r in mosfets_only] == ["M1"]
    caps_only = list_staged("capacitors")
    assert [r.mpn for r in caps_only] == ["CAP-1"]
    all_records = list_staged()
    assert sorted(r.mpn for r in all_records) == ["CAP-1", "M1"]


def test_list_staged_ignores_non_json_debris(tmp_path: Path) -> None:
    stage_fetch("mosfets", "OK", _valid_mosfet("OK"), source="digikey")
    debris = staging_mod.STAGING_DIR / "mosfets" / "README.txt"
    debris.write_text("notes", encoding="utf-8")
    records = list_staged("mosfets")
    assert [r.mpn for r in records] == ["OK"]


def test_list_staged_unknown_category_raises() -> None:
    with pytest.raises(StagingError, match="unknown category"):
        list_staged("widgets")
