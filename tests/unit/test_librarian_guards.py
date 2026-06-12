"""Tests for :mod:`heaviside.librarian.guards`.

Covers, in order:

  * integrity_issues — one test per junk class the June 2026 cleanup
    quarantined (synthetic series taxonomy, placeholder / value-encoding
    MPNs, partNumber == series stubs, junk datasheet URLs, telemetry
    shapes, anonymous rows) plus the good-row baseline.
  * False-positive regression guards: legitimate MPNs that embed 'NF'
    letter runs (ST STP40NF03L, Samsung CL05B102KB5NFNC, Mitsubishi
    CM100DU-24NF) must NOT trip the value-encoding pattern.
  * guard_component — throws GuardRejectionError with every reason
    listed; schema validation skippable for staging.
  * add_component wiring — a schema-valid but junk row is rejected and
    nothing is written.
  * stage_fetch wiring — junk is rejected before it reaches staging,
    while partial (anonymous) payloads are still accepted by contract.
  * integrity_scan — read-only report of guard failures, exact
    duplicates, over-copied MPNs, and known manufacturer/domain
    mismatches.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from heaviside.librarian import safe_access as sa
from heaviside.librarian import tas
from heaviside.librarian.fetcher import staging as staging_mod
from heaviside.librarian.fetcher.staging import StagedRecord, stage_fetch
from heaviside.librarian.guards import (
    GuardRejectionError,
    guard_component,
    integrity_issues,
    integrity_scan,
)

# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _retarget_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate TAS_DATA_DIR / LOCK_DIR / STAGING_DIR per test."""
    data_dir = tmp_path / "tas-data"
    lock_dir = tmp_path / "locks"
    data_dir.mkdir()
    monkeypatch.setattr(sa, "TAS_DATA_DIR", data_dir)
    monkeypatch.setattr(sa, "LOCK_DIR", lock_dir)
    monkeypatch.setattr(staging_mod, "STAGING_DIR", tmp_path / "staging")
    tas._clear_validator_cache()


def _diode(
    part_number: str = "STPS3L60",
    *,
    series: str | None = None,
    datasheet_url: str | None = None,
) -> dict[str, Any]:
    """Minimal diode envelope for pattern checks (not schema-valid)."""
    part: dict[str, Any] = {
        "partNumber": part_number,
        "technology": "Si",
        "subType": "schottky",
        "case": "SMB",
    }
    if series is not None:
        part["series"] = series
    mi: dict[str, Any] = {
        "name": "STMicroelectronics",
        "datasheetInfo": {
            "part": part,
            "electrical": {"reverseVoltage": 60, "forwardCurrent": 3},
        },
    }
    if datasheet_url is not None:
        mi["datasheetUrl"] = datasheet_url
    return {"semiconductor": {"diode": {"manufacturerInfo": mi}}}


# ---------------------------------------------------------------------------
# integrity_issues — good row baseline
# ---------------------------------------------------------------------------


class TestIntegrityIssuesGood:
    def test_clean_row_has_no_issues(self):
        assert integrity_issues(_diode()) == []

    def test_clean_row_with_real_docs_url(self):
        comp = _diode(datasheet_url="https://www.vishay.com/docs/88751/ss12.pdf")
        assert integrity_issues(comp) == []

    def test_distinct_series_is_fine(self):
        comp = _diode("VS-8EWF06SLHM3", series="FRED Pt")
        assert integrity_issues(comp) == []

    @pytest.mark.parametrize(
        "mpn",
        [
            # Legit MPNs with embedded letter runs the loose 'uF/nF/pF'
            # heuristic would false-positive on — pinned here so the
            # placeholder pattern stays hyphen-token-bounded.
            "STP40NF03L",  # ST 'NF' series MOSFET
            "CL05B102KB5NFNC",  # Samsung MLCC suffix
            "CM100DU-24NF",  # Mitsubishi IGBT module ('-24NF' is uppercase)
            "LQM18PN1R0NF0",  # Murata inductor
            "2EDL05N06PF",  # Infineon gate driver
        ],
    )
    def test_legit_mpn_with_embedded_nf_not_flagged(self, mpn: str):
        assert integrity_issues(_diode(mpn)) == []


# ---------------------------------------------------------------------------
# integrity_issues — each junk class
# ---------------------------------------------------------------------------


class TestIntegrityIssuesJunk:
    # Junk class 1 — synthetic series taxonomy
    @pytest.mark.parametrize(
        "series",
        ["Schottky_25V", "TVS_5V", "SiC_Schottky_1200V", "Zener_12V", "Si_600V"],
    )
    def test_synthetic_series_rejected(self, series: str):
        issues = integrity_issues(_diode("InUF0240N003SOD-3234321", series=series))
        assert any("synthetic" in i for i in issues), issues

    def test_synthetic_family_rejected(self):
        comp = _diode()
        comp["semiconductor"]["diode"]["manufacturerInfo"]["family"] = "Ultrafast_200V"
        issues = integrity_issues(comp)
        assert any("synthetic" in i for i in issues), issues

    # Junk class 2 — placeholder / value-encoding MPNs
    def test_partnumber_equals_series_rejected(self):
        issues = integrity_issues(_diode("TR3", series="TR3"))
        assert any("equals its series" in i for i in issues), issues

    @pytest.mark.parametrize(
        "mpn",
        [
            "WCAP-MLCC-1nF-50V",
            "WCAP-ATH-10uF-25V",
            "GENERIC-4.7uF-25V",
            "L-2.2uH-3A",
        ],
    )
    def test_value_encoding_pseudo_mpn_rejected(self, mpn: str):
        issues = integrity_issues(_diode(mpn))
        assert any("pseudo-MPN" in i for i in issues), issues

    def test_missing_partnumber_rejected(self):
        issues = integrity_issues({"capacitor": {"manufacturerInfo": {"name": "X"}}})
        assert any("no non-empty partNumber" in i for i in issues), issues

    def test_missing_partnumber_allowed_when_not_required(self):
        comp = {"capacitor": {"manufacturerInfo": {"name": "X"}}}
        assert integrity_issues(comp, require_mpn=False) == []

    # Junk class 3 — junk datasheet URLs
    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/datasheet.pdf",
            "https://www.vishay.com/en/search/?type=inv&query=TR3",
            "https://datasheetpdf.com/search?q=IKW75N65H5",
        ],
    )
    def test_junk_datasheet_url_rejected(self, url: str):
        issues = integrity_issues(_diode(datasheet_url=url))
        assert issues, f"expected rejection for {url!r}"

    def test_non_http_datasheet_url_rejected(self):
        issues = integrity_issues(_diode(datasheet_url="ftp://files.example.org/x.pdf"))
        assert any("not an http(s) URL" in i for i in issues), issues

    # Junk class 5 — telemetry shape
    def test_telemetry_shaped_object_rejected(self):
        telemetry = {
            "id": "20260517-100807-68f9a7",
            "status": "completed",
            "tas": {"inputs": {}},
            "telemetry": {"invocations": 28},
        }
        issues = integrity_issues(telemetry)
        assert any("telemetry-shaped" in i for i in issues), issues

    def test_multiple_reasons_all_reported(self):
        comp = _diode(
            "WCAP-MLCC-1nF-50V",
            series="Schottky_25V",
            datasheet_url="https://example.com/x.pdf",
        )
        issues = integrity_issues(comp)
        assert len(issues) >= 3, issues


# ---------------------------------------------------------------------------
# guard_component
# ---------------------------------------------------------------------------


class TestGuardComponent:
    def test_junk_raises_guard_rejection_with_all_reasons(self):
        comp = _diode(
            "WCAP-MLCC-1nF-50V",
            series="Schottky_25V",
            datasheet_url="https://example.com/x.pdf",
        )
        with pytest.raises(GuardRejectionError) as exc_info:
            guard_component("diodes", comp, validate_schema=False)
        err = exc_info.value
        assert err.category == "diodes"
        assert err.mpn == "WCAP-MLCC-1nF-50V"
        assert len(err.reasons) >= 3
        assert "quarantine" in str(err)

    def test_unknown_category_rejected(self):
        with pytest.raises(sa.UnknownCategoryError):
            guard_component("mosftets", _diode())

    def test_non_dict_rejected(self):
        with pytest.raises(GuardRejectionError, match=r"must be a dict"):
            guard_component("diodes", ["not", "a", "dict"], validate_schema=False)  # type: ignore[arg-type]

    def test_schema_validation_runs_when_enabled(self):
        # A pattern-clean envelope with a type-broken electrical field:
        # the real SAS diode schema must reject it through the guard.
        comp = _diode()
        comp["semiconductor"]["diode"]["manufacturerInfo"]["datasheetInfo"]["electrical"][
            "forwardCurrent"
        ] = "three amps"
        with pytest.raises(tas.ValidationError):
            guard_component("diodes", comp, validate_schema=True)

    def test_schema_validation_skippable_for_staging(self):
        guard_component("diodes", _diode(), validate_schema=False)  # no raise


# ---------------------------------------------------------------------------
# add_component wiring (schema-valid junk must still be rejected)
# ---------------------------------------------------------------------------


class _AcceptAll:
    def iter_errors(self, _payload: Any):
        return []


class TestAddComponentWiring:
    def test_schema_valid_junk_rejected_and_not_written(self):
        with tas._VALIDATOR_LOCK:
            tas._VALIDATOR_CACHE["diodes"] = _AcceptAll()
        try:
            with pytest.raises(GuardRejectionError, match="synthetic"):
                tas.add_component("diodes", _diode("ViSC0028N009SOD-323001", series="Schottky_25V"))
        finally:
            tas._clear_validator_cache()
        assert not (sa.TAS_DATA_DIR / "diodes.ndjson").exists()

    def test_clean_row_still_writes(self):
        with tas._VALIDATOR_LOCK:
            tas._VALIDATOR_CACHE["diodes"] = _AcceptAll()
        try:
            tas.add_component("diodes", _diode("STPS3L60"))
        finally:
            tas._clear_validator_cache()
        written = (sa.TAS_DATA_DIR / "diodes.ndjson").read_text().strip()
        assert "STPS3L60" in written


# ---------------------------------------------------------------------------
# stage_fetch wiring
# ---------------------------------------------------------------------------


class TestStagingWiring:
    def test_junk_rejected_before_staging(self):
        with pytest.raises(GuardRejectionError, match="pseudo-MPN"):
            stage_fetch(
                "capacitors",
                "WCAP-MLCC-1nF-50V",
                {
                    "capacitor": {
                        "manufacturerInfo": {
                            "name": "Würth Elektronik",
                            "datasheetInfo": {"part": {"partNumber": "WCAP-MLCC-1nF-50V"}},
                        }
                    }
                },
                source="digikey",
            )
        assert not (staging_mod.STAGING_DIR / "capacitors").exists()

    def test_partial_payload_still_stageable(self):
        # Staging accepts anonymous/partial payloads by contract; the
        # anonymous-row check bites at apply_staged → add_component.
        path = stage_fetch(
            "capacitors",
            "CAP-1",
            {"capacitor": {"manufacturerInfo": {"name": "X"}}},
            source="digikey",
        )
        assert StagedRecord.from_path(path).mpn == "CAP-1"


# ---------------------------------------------------------------------------
# integrity_scan
# ---------------------------------------------------------------------------


def _seed(category: str, records: list[dict[str, Any]]) -> Path:
    path = sa.TAS_DATA_DIR / f"{category}.ndjson"
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    return path


class TestIntegrityScan:
    def test_reports_guard_failures(self):
        _seed(
            "diodes",
            [
                _diode("STPS3L60"),
                _diode("ViSC0028N009SOD-323001", series="Schottky_25V"),
            ],
        )
        report = integrity_scan("diodes")
        assert report.total == 2
        assert len(report.guard_failures) == 1
        assert report.guard_failures[0].line == 2
        assert report.guard_failures[0].mpn == "ViSC0028N009SOD-323001"
        assert not report.clean

    def test_reports_exact_duplicates(self):
        row = _diode("STPS3L60")
        _seed("diodes", [row, row, _diode("BAT54W")])
        report = integrity_scan("diodes")
        assert len(report.exact_duplicates) == 1
        (lines,) = report.exact_duplicates.values()
        assert lines == [1, 2]

    def test_reports_mpn_over_limit(self):
        # Same MPN, different payloads — exact-dup detection misses it,
        # the MPN-copy counter must not.
        a = _diode("STPS3L60")
        b = _diode("STPS3L60", datasheet_url="https://www.st.com/resource/stps3l60.pdf")
        _seed("diodes", [a, b])
        report = integrity_scan("diodes", max_mpn_copies=1)
        assert report.mpn_over_limit == {"STPS3L60": 2}
        report_relaxed = integrity_scan("diodes", max_mpn_copies=2)
        assert report_relaxed.mpn_over_limit == {}

    def test_reports_known_domain_mismatch(self):
        # Vishay row pointing at a Nexperia PDF — the quarantined
        # wrong-part-URL junk class.
        comp = _diode(
            "VS-8EWF06SLHM3",
            datasheet_url="https://assets.nexperia.com/documents/data-sheet/GAN041-650WSB.pdf",
        )
        comp["semiconductor"]["diode"]["manufacturerInfo"]["name"] = "Vishay"
        _seed("diodes", [comp])
        report = integrity_scan("diodes")
        assert len(report.domain_mismatches) == 1
        assert "nexperia" in report.domain_mismatches[0].reasons[0]

    def test_unknown_manufacturer_never_flagged(self):
        comp = _diode(
            "XYZ123",
            datasheet_url="https://assets.nexperia.com/documents/data-sheet/x.pdf",
        )
        comp["semiconductor"]["diode"]["manufacturerInfo"]["name"] = "Obscure Devices Inc"
        _seed("diodes", [comp])
        report = integrity_scan("diodes")
        assert report.domain_mismatches == []

    def test_distributor_host_never_flagged(self):
        comp = _diode(
            "VS-8EWF06SLHM3",
            datasheet_url="https://www.mouser.com/datasheet/2/427/vs8ewf06.pdf",
        )
        comp["semiconductor"]["diode"]["manufacturerInfo"]["name"] = "Vishay"
        _seed("diodes", [comp])
        report = integrity_scan("diodes")
        assert report.domain_mismatches == []

    def test_telemetry_in_converters_flagged_without_mpn_noise(self):
        _seed(
            "converters",
            [
                {"name": "_empty", "inputs": {}, "topology": "Flyback"},
                {"id": "20260517-1", "status": "completed", "tas": {}},
            ],
        )
        report = integrity_scan("converters")
        assert len(report.guard_failures) == 1
        assert "telemetry-shaped" in report.guard_failures[0].reasons[0]

    def test_corrupt_line_raises(self):
        path = sa.TAS_DATA_DIR / "diodes.ndjson"
        path.write_text('{"semiconductor": {broken\n', encoding="utf-8")
        with pytest.raises(sa.LibrarianError, match="corrupt JSON"):
            integrity_scan("diodes")

    def test_missing_file_raises(self):
        with pytest.raises(sa.LibrarianError, match="not found"):
            integrity_scan("diodes")

    def test_unknown_category_raises(self):
        with pytest.raises(sa.UnknownCategoryError):
            integrity_scan("mosftets")

    def test_clean_file_is_clean(self):
        _seed("diodes", [_diode("STPS3L60"), _diode("BAT54W")])
        report = integrity_scan("diodes")
        assert report.clean
        assert report.total == 2
