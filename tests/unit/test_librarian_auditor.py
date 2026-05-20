"""Tests for :mod:`heaviside.librarian.auditor`.

Covers, in order:

  * AUDITABLE_CATEGORIES gate
  * envelope unwrap (incl. nested ``semiconductor.diode`` /
    ``semiconductor.igbt``) is soft (no throw on bad shape — the
    auditor reports missing fields)
  * subtype carve-outs: Schottky, MLCC, RF-inductor,
    transformer/CMC each drop the appropriate critical field
  * field-status classifier: present / missing_key / null / zero
  * thermal → electrical promotion (Tj_max)
  * dimensionWithTolerance scalar reduction
  * dcResistances plural fallback for magnetics
  * audit_component happy + failure paths
  * audit_category aggregation + corruption modes ('raise' /
    'report')
  * end-to-end against the live TAS NDJSON (one sample of each
    category; characterizes real corpus drift)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from heaviside.librarian import auditor as au
from heaviside.librarian import safe_access as sa
from heaviside.librarian.safe_access import LibrarianError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_tas(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Retarget TAS_DATA_DIR to a fresh tmp path and return it.

    Not autouse so the live-corpus characterization tests can opt
    out and read the real submodule data.
    """
    data_dir = tmp_path / "tas-data"
    data_dir.mkdir()
    monkeypatch.setattr(sa, "TAS_DATA_DIR", data_dir)
    return data_dir


def _seed_ndjson(data_dir: Path, category: str,
                  records: list[dict[str, Any] | str]) -> Path:
    """Write one NDJSON line per ``records`` entry (dict → JSON, str raw)."""
    path = data_dir / f"{category}.ndjson"
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            if isinstance(r, str):
                fh.write(r if r.endswith("\n") else r + "\n")
            else:
                fh.write(json.dumps(r) + "\n")
    return path


# ---------------------------------------------------------------------------
# Reference records — minimal but sufficient to exercise each rule.
# ---------------------------------------------------------------------------


def _mosfet(mpn: str = "TM1",
            **overrides: Any) -> dict[str, Any]:
    """A mosfet envelope that passes every CRITICAL_PARAMS field."""
    elec: dict[str, Any] = {
        "drainSourceVoltage": 100,
        "onResistance": 0.025,
        "continuousDrainCurrent": 30,
        "outputCapacitance": 250e-12,
        "totalGateCharge": 80e-9,
        "gateThresholdVoltage": {"minimum": 2, "nominal": 3, "maximum": 4},
        "junctionTemperatureMax": 150,
    }
    elec.update(overrides)
    return {"mosfet": {"manufacturerInfo": {
        "name": "TEST-MFR",
        "reference": mpn,
        "datasheetInfo": {
            "part": {"partNumber": mpn, "subType": "nChannel"},
            "electrical": elec,
        },
    }}}


def _diode(mpn: str = "TD1", subType: str = "fast",
           **overrides: Any) -> dict[str, Any]:
    elec = {
        "reverseVoltage": 200,
        "forwardVoltage": 0.9,
        "forwardCurrent": 5,
        "reverseRecoveryCharge": 30e-9,
    }
    elec.update(overrides)
    return {"semiconductor": {"diode": {"manufacturerInfo": {
        "reference": mpn,
        "datasheetInfo": {
            "part": {"partNumber": mpn, "subType": subType},
            "electrical": elec,
        },
    }}}}


def _capacitor(mpn: str = "TC1", **overrides: Any) -> dict[str, Any]:
    elec = {
        "capacitance": 1e-6,
        "ratedVoltage": 50,
        "esr": 0.05,
        "rippleCurrent": 1.5,
    }
    elec.update(overrides)
    return {"capacitor": {"manufacturerInfo": {
        "reference": mpn,
        "datasheetInfo": {
            "part": {"partNumber": mpn, "technology": "Tantalum"},
            "electrical": elec,
        },
    }}}


def _magnetic(mpn: str = "TM-IND-1", **overrides: Any) -> dict[str, Any]:
    elec = {
        "inductance": 10e-6,
        "dcResistance": 0.012,
        "saturationCurrentPeak": 8,
    }
    elec.update(overrides)
    return {"magnetic": {"manufacturerInfo": {
        "reference": mpn,
        "datasheetInfo": {
            "part": {"partNumber": mpn},
            "electrical": elec,
        },
    }}}


# ---------------------------------------------------------------------------
# Category gate
# ---------------------------------------------------------------------------


class TestCategoryGate:

    def test_unknown_category_rejected_by_audit_component(self):
        with pytest.raises(LibrarianError, match="not auditable"):
            au.audit_component({"mosfet": {}}, "controllers")

    def test_unknown_category_rejected_by_audit_category(self, tmp_tas):
        with pytest.raises(LibrarianError, match="not auditable"):
            au.audit_category("controllers")

    def test_missing_file_throws(self, tmp_tas):
        with pytest.raises(LibrarianError, match="NDJSON not found"):
            au.audit_category("mosfets")

    def test_all_documented_categories_are_auditable(self):
        assert set(au.AUDITABLE_CATEGORIES) == set(au.CRITICAL_PARAMS)


# ---------------------------------------------------------------------------
# Soft envelope unwrap
# ---------------------------------------------------------------------------


class TestSoftUnwrap:
    """Auditor must keep going on a malformed envelope — bad shape
    just means every field is missing."""

    def test_missing_envelope_reports_all_critical_as_missing(self):
        res = au.audit_component({"wrong": {}}, "mosfets")
        assert not res.passed
        missing = {gap.field for gap in res.critical_failures}
        assert missing == set(au.CRITICAL_PARAMS["mosfets"])

    def test_diode_missing_outer_envelope_reports_missing(self):
        res = au.audit_component({"diode": {}}, "diodes")
        assert not res.passed
        # All four critical fields missing (Schottky carve-out
        # cannot apply to a body that's not even reachable).
        assert len(res.critical_failures) == len(
            au.CRITICAL_PARAMS["diodes"]
        )

    def test_non_dict_component_returns_unknown_mpn(self):
        # Auditor must not throw on bad input from upstream pipeline
        # stages — it must instead report it.
        res = au.audit_component(["not", "a", "dict"], "mosfets")  # type: ignore[arg-type]
        assert res.mpn == "UNKNOWN"


# ---------------------------------------------------------------------------
# Field-status classifier
# ---------------------------------------------------------------------------


class TestFieldStatus:

    def test_missing_key(self):
        assert au._field_status({}, "x") == au.FieldStatus.MISSING_KEY

    def test_null(self):
        assert au._field_status({"x": None}, "x") == au.FieldStatus.NULL

    def test_zero(self):
        assert au._field_status({"x": 0}, "x") == au.FieldStatus.ZERO
        assert au._field_status({"x": 0.0}, "x") == au.FieldStatus.ZERO

    def test_present(self):
        assert au._field_status({"x": 1.2}, "x") == au.FieldStatus.PRESENT

    def test_dcresistances_plural_fallback_for_magnetics(self):
        assert au._field_status(
            {"dcResistances": [{"nominal": 0.01}]}, "dcResistance",
        ) == au.FieldStatus.PRESENT

    def test_dcresistances_plural_zero_still_zero(self):
        assert au._field_status(
            {"dcResistances": 0}, "dcResistance",
        ) == au.FieldStatus.ZERO


# ---------------------------------------------------------------------------
# _extract_electrical: thermal merge + dimensionWithTolerance flatten
# ---------------------------------------------------------------------------


class TestExtractElectrical:

    def test_thermal_promotes_tj_max_into_electrical(self):
        comp = _mosfet()
        # Drop Tj from electrical, place it under thermal.
        del comp["mosfet"]["manufacturerInfo"]["datasheetInfo"][
            "electrical"]["junctionTemperatureMax"]
        comp["mosfet"]["manufacturerInfo"]["datasheetInfo"]["thermal"] = {
            "maximumJunctionTemperature": 175,
        }
        res = au.audit_component(comp, "mosfets")
        assert res.passed, res.critical_failures

    def test_dimension_with_tolerance_flattened_to_nominal(self):
        # gateThresholdVoltage is the canonical example — comes in as
        # {minimum, nominal, maximum} and is reduced to its nominal.
        comp = _mosfet()
        res = au.audit_component(comp, "mosfets")
        assert res.passed

    def test_dimension_with_tolerance_falls_back_to_minimum(self):
        comp = _mosfet(gateThresholdVoltage={"minimum": 2})
        res = au.audit_component(comp, "mosfets")
        assert res.passed


# ---------------------------------------------------------------------------
# Subtype carve-outs
# ---------------------------------------------------------------------------


class TestCarveOuts:

    def test_schottky_diode_does_not_require_qrr(self):
        # No Qrr in the electrical block at all.
        comp = _diode("STPS30L60CT", subType="schottky")
        del comp["semiconductor"]["diode"]["manufacturerInfo"][
            "datasheetInfo"]["electrical"]["reverseRecoveryCharge"]
        res = au.audit_component(comp, "diodes")
        assert res.passed
        # Sanity: the same record without the Schottky subType would fail.
        comp2 = _diode("FOO", subType="fast")
        del comp2["semiconductor"]["diode"]["manufacturerInfo"][
            "datasheetInfo"]["electrical"]["reverseRecoveryCharge"]
        assert not au.audit_component(comp2, "diodes").passed

    def test_schottky_by_mpn_prefix(self):
        comp = _diode("MBRD340", subType="fast")  # MBR* = Schottky
        del comp["semiconductor"]["diode"]["manufacturerInfo"][
            "datasheetInfo"]["electrical"]["reverseRecoveryCharge"]
        assert au.audit_component(comp, "diodes").passed

    def test_mlcc_capacitor_does_not_require_esr_or_ripple_current(self):
        comp = _capacitor("GRM188R71C")  # GRM* = Murata MLCC
        elec = comp["capacitor"]["manufacturerInfo"]["datasheetInfo"][
            "electrical"]
        del elec["esr"]
        del elec["rippleCurrent"]
        assert au.audit_component(comp, "capacitors").passed

    def test_non_mlcc_still_requires_esr_and_ripple(self):
        comp = _capacitor("UPW1H102MHD")  # electrolytic, MPN unknown to prefix list
        elec = comp["capacitor"]["manufacturerInfo"]["datasheetInfo"][
            "electrical"]
        del elec["esr"]
        del elec["rippleCurrent"]
        gaps = {gap.field for gap in au.audit_component(
            comp, "capacitors").critical_failures}
        assert "esr" in gaps and "rippleCurrent" in gaps

    def test_rf_inductor_does_not_require_isat(self):
        comp = _magnetic("LQG18HN10NJ00")  # LQG* = Murata RF
        del comp["magnetic"]["manufacturerInfo"]["datasheetInfo"][
            "electrical"]["saturationCurrentPeak"]
        assert au.audit_component(comp, "magnetics").passed

    def test_transformer_does_not_require_isat(self):
        comp = _magnetic("XFMR-1", turnsRatio=2.5)
        del comp["magnetic"]["manufacturerInfo"]["datasheetInfo"][
            "electrical"]["saturationCurrentPeak"]
        assert au.audit_component(comp, "magnetics").passed

    def test_cmc_by_description_does_not_require_isat(self):
        comp = _magnetic("CM-CHOKE-1")
        comp["magnetic"]["manufacturerInfo"]["datasheetInfo"]["part"][
            "description"] = "Common Mode Choke 1mH"
        del comp["magnetic"]["manufacturerInfo"]["datasheetInfo"][
            "electrical"]["saturationCurrentPeak"]
        assert au.audit_component(comp, "magnetics").passed

    def test_ferrite_bead_by_impedance_points_does_not_require_isat(self):
        comp = _magnetic("BLM-1")
        comp["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"][
            "impedancePoints"] = [[100e6, 600]]
        del comp["magnetic"]["manufacturerInfo"]["datasheetInfo"][
            "electrical"]["saturationCurrentPeak"]
        assert au.audit_component(comp, "magnetics").passed


# ---------------------------------------------------------------------------
# audit_component happy / failure paths
# ---------------------------------------------------------------------------


class TestAuditComponent:

    def test_complete_mosfet_passes(self):
        res = au.audit_component(_mosfet(), "mosfets")
        assert res.passed
        assert res.critical_failures == []
        assert res.mpn == "TM1"
        assert res.category == "mosfets"

    def test_missing_field_reports_gap_with_status(self):
        comp = _mosfet()
        del comp["mosfet"]["manufacturerInfo"]["datasheetInfo"][
            "electrical"]["outputCapacitance"]
        res = au.audit_component(comp, "mosfets")
        assert not res.passed
        gaps = {g.field: g.status for g in res.critical_failures}
        assert gaps == {"outputCapacitance": au.FieldStatus.MISSING_KEY}

    def test_null_field_distinguished_from_missing(self):
        comp = _mosfet()
        comp["mosfet"]["manufacturerInfo"]["datasheetInfo"][
            "electrical"]["outputCapacitance"] = None
        res = au.audit_component(comp, "mosfets")
        gaps = {g.field: g.status for g in res.critical_failures}
        assert gaps == {"outputCapacitance": au.FieldStatus.NULL}

    def test_zero_field_distinguished_from_null(self):
        comp = _mosfet()
        comp["mosfet"]["manufacturerInfo"]["datasheetInfo"][
            "electrical"]["onResistance"] = 0
        res = au.audit_component(comp, "mosfets")
        gaps = {g.field: g.status for g in res.critical_failures}
        assert gaps == {"onResistance": au.FieldStatus.ZERO}

    def test_required_failures_do_not_flip_passed(self):
        # bodyDiodeForwardVoltage is REQUIRED (warning), not critical.
        # A mosfet without it must still pass.
        res = au.audit_component(_mosfet(), "mosfets")
        assert res.passed
        warn = {g.field for g in res.required_failures}
        assert "bodyDiodeForwardVoltage" in warn


# ---------------------------------------------------------------------------
# audit_category aggregation
# ---------------------------------------------------------------------------


class TestAuditCategoryAggregation:

    def test_pass_rate_and_field_misses(self, tmp_tas):
        _seed_ndjson(tmp_tas, "mosfets", [
            _mosfet("A"),                                   # full pass
            _mosfet("B", outputCapacitance=None),           # null Coss
            _mosfet("C", totalGateCharge=0),                # zero Qg
        ])
        rep = au.audit_category("mosfets")
        assert rep.total == 3
        assert rep.passed == 1
        assert rep.failed == 2
        assert rep.pass_rate_pct == pytest.approx(100/3)
        assert rep.critical_field_misses == {
            "outputCapacitance": 1, "totalGateCharge": 1,
        }
        # Line numbers preserved for failures.
        lines = {f.mpn: f.line for f in rep.failures}
        assert lines == {"B": 2, "C": 3}

    def test_blank_lines_skipped(self, tmp_tas):
        path = tmp_tas / "mosfets.ndjson"
        path.write_text(
            "\n"
            + json.dumps(_mosfet("ONE"))
            + "\n\n"
            + json.dumps(_mosfet("TWO"))
            + "\n",
            encoding="utf-8",
        )
        rep = au.audit_category("mosfets")
        assert rep.total == 2
        assert rep.passed == 2

    def test_sample_limits_lines_read(self, tmp_tas):
        _seed_ndjson(tmp_tas, "mosfets",
                     [_mosfet(f"M{i}") for i in range(50)])
        rep = au.audit_category("mosfets", sample=10)
        assert rep.total == 10

    def test_warnings_only_collected_for_passing_rows(self, tmp_tas):
        # Mosfet passes critical but lacks bodyDiodeForwardVoltage.
        _seed_ndjson(tmp_tas, "mosfets", [_mosfet("PASS")])
        rep = au.audit_category("mosfets")
        assert len(rep.warnings_only) == 1
        assert rep.required_field_misses["bodyDiodeForwardVoltage"] == 1


# ---------------------------------------------------------------------------
# Corruption modes (strict by default; report mode for repair workflows)
# ---------------------------------------------------------------------------


class TestCorruptionModes:

    def test_corrupt_line_raises_by_default(self, tmp_tas):
        _seed_ndjson(tmp_tas, "mosfets", [
            _mosfet("OK"),
            "{not json}",
        ])
        with pytest.raises(LibrarianError, match="corrupt JSON"):
            au.audit_category("mosfets")

    def test_non_object_line_raises_by_default(self, tmp_tas):
        _seed_ndjson(tmp_tas, "mosfets", ["[1,2,3]"])
        with pytest.raises(LibrarianError, match="expected JSON object"):
            au.audit_category("mosfets")

    def test_report_mode_collects_corruption_first_class(self, tmp_tas):
        _seed_ndjson(tmp_tas, "mosfets", [
            _mosfet("A"),
            "{not json}",
            _mosfet("B"),
            "<<<<<<< HEAD merge conflict marker",  # mimics mosfets.ndjson:L2802
            _mosfet("C"),
        ])
        rep = au.audit_category("mosfets", on_corruption="report")
        assert rep.total == 3            # corrupt lines not counted
        assert rep.passed == 3
        assert len(rep.corrupt_lines) == 2
        # Line numbers preserved for the repair tool.
        assert [c.line for c in rep.corrupt_lines] == [2, 4]
        # Reason text is non-empty and identifies the failure mode.
        for c in rep.corrupt_lines:
            assert c.reason and ("JSONDecodeError" in c.reason
                                 or "expected JSON object" in c.reason)

    def test_invalid_on_corruption_value_rejected(self, tmp_tas):
        _seed_ndjson(tmp_tas, "mosfets", [_mosfet("A")])
        with pytest.raises(LibrarianError,
                           match="on_corruption must be 'raise' or 'report'"):
            au.audit_category("mosfets", on_corruption="ignore")


# ---------------------------------------------------------------------------
# audit_all
# ---------------------------------------------------------------------------


class TestAuditAll:

    def test_audit_all_covers_every_category(self, tmp_tas):
        # Seed one trivially-passing row per category.
        _seed_ndjson(tmp_tas, "mosfets",    [_mosfet()])
        _seed_ndjson(tmp_tas, "diodes",     [_diode()])
        _seed_ndjson(tmp_tas, "capacitors", [_capacitor()])
        _seed_ndjson(tmp_tas, "resistors",  [{"resistor": {"manufacturerInfo": {
            "reference": "R1",
            "datasheetInfo": {"part": {"partNumber": "R1"}, "electrical": {
                "resistance": 1e3, "tolerance": 0.01, "powerRating": 0.25,
            }},
        }}}])
        _seed_ndjson(tmp_tas, "magnetics",  [_magnetic()])
        _seed_ndjson(tmp_tas, "igbts",      [{"semiconductor": {"igbt": {
            "manufacturerInfo": {
                "reference": "IGBT1",
                "datasheetInfo": {
                    "part": {"partNumber": "IGBT1"},
                    "electrical": {
                        "collectorEmitterVoltage": 1200,
                        "continuousCollectorCurrent": 100,
                        "collectorEmitterSaturation": 1.4,
                    },
                },
            },
        }}}])
        results = au.audit_all()
        assert set(results) == set(au.AUDITABLE_CATEGORIES)
        for cat, rep in results.items():
            assert rep.total == 1, cat
            assert rep.passed == 1, cat

    def test_audit_all_propagates_missing_file_error(self, tmp_tas):
        # mosfets.ndjson missing → LibrarianError, no silent elision.
        _seed_ndjson(tmp_tas, "diodes", [_diode()])
        with pytest.raises(LibrarianError, match="NDJSON not found"):
            au.audit_all()


# ---------------------------------------------------------------------------
# Live-corpus characterization (read-only; uses the real submodule)
# ---------------------------------------------------------------------------


class TestLiveCorpus:
    """These tests exercise the auditor against the *real* TAS NDJSON.

    They are deliberately tolerant — corpus drift over time would
    otherwise make them flap.  The assertions check structural
    invariants only (auditor completes, returns sane counts, the
    known corruption surfaces in report mode).
    """

    @pytest.mark.parametrize("category", au.AUDITABLE_CATEGORIES)
    def test_full_corpus_audit_completes_in_report_mode(self, category):
        path = (Path(__file__).resolve().parents[2]
                / "TAS" / "data" / f"{category}.ndjson")
        if not path.exists():
            pytest.skip(f"{path} not present — submodule not initialised")
        rep = au.audit_category(category, on_corruption="report")
        # At minimum: some rows decoded, pass-rate is a valid percent.
        assert rep.total > 0
        assert 0.0 <= rep.pass_rate_pct <= 100.0
        # Every reported gap targets a CRITICAL_PARAMS field.
        for field_name in rep.critical_field_misses:
            assert field_name in au.CRITICAL_PARAMS[category] or \
                   field_name == "dcResistance"  # dcResistances fallback

    def test_known_mosfets_corruption_surfaces_in_report_mode(self):
        path = (Path(__file__).resolve().parents[2]
                / "TAS" / "data" / "mosfets.ndjson")
        if not path.exists():
            pytest.skip("mosfets.ndjson not present")
        rep = au.audit_category("mosfets", on_corruption="report")
        # The xfail tests pin the corruption at L2802/L2806/L2810.
        # Auditor must surface at least one of those in report mode.
        if rep.corrupt_lines:
            known = {2802, 2806, 2810}
            seen = {c.line for c in rep.corrupt_lines}
            assert known & seen, (
                f"expected at least one of {known} in corrupt_lines, "
                f"got {seen}"
            )
        # If no corruption: the librarian has repaired the file —
        # then the xfail tests upstream will flip green and this
        # test correctly does nothing.
