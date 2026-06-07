"""Tests for ``heaviside.librarian.datasheet.extract``.

Covers, with synthetic table inputs (no real PDFs):

* ``match_param_name`` — happy + miss + whitespace-normalised forms.
* ``pick_value_from_row`` — happy path, symbol-column skip,
  annotation stripping, multiline cells, raises on no numeric.
* ``filter_electrical_tables`` — section detection, terminator
  handling, and specifically the thermal-vs-catch-all precedence
  fix that motivated the strict ordering of
  :data:`SECTION_TERMINATORS` checks before
  :data:`ELECTRICAL_SECTION_HEADERS`.
* ``extract_params`` — end-to-end per category, first-occurrence-
  wins, merged-section-banner skip, unknown-category raises.
* ``extract_required_params`` — raises
  :class:`IncompleteDatasheetError` with a converter-shaped
  ``missing_field`` dotted path.
* ``extract_tables`` — file-not-found raises
  :class:`DatasheetParseError`; zero-table PDF raises the same.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from heaviside.librarian.datasheet.base import (
    DatasheetParseError,
    IncompleteDatasheetError,
)
from heaviside.librarian.datasheet.extract import (
    ELECTRICAL_SECTION_HEADERS,
    SECTION_TERMINATORS,
    extract_params,
    extract_required_params,
    extract_tables,
    filter_electrical_tables,
    match_param_name,
    pick_value_from_row,
)
from heaviside.librarian.fetcher.base import IncompleteSourceError

# ---------------------------------------------------------------------------
# match_param_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "category", "expected"),
    [
        ("Drain-Source Voltage", "mosfets", "drainSourceVoltage"),
        ("VDSS", "mosfets", "drainSourceVoltage"),
        ("RDS(ON)", "mosfets", "onResistance"),
        ("Drain-Source On-Resistance", "mosfets", "onResistance"),
        ("Total Gate Charge", "mosfets", "totalGateCharge"),
        ("Output Capacitance", "mosfets", "outputCapacitance"),
        ("Coss", "mosfets", "outputCapacitance"),
        ("Repetitive Peak Reverse Voltage", "diodes", "reverseVoltage"),
        ("VRRM", "diodes", "reverseVoltage"),
        ("Forward Voltage", "diodes", "forwardVoltage"),
        ("Reverse Recovery Charge", "diodes", "reverseRecoveryCharge"),
        ("Qrr", "diodes", "reverseRecoveryCharge"),
        ("Collector-Emitter Voltage", "igbts", "collectorEmitterVoltage"),
        ("VCE(sat)", "igbts", "collectorEmitterSaturation"),
        ("Continuous Collector Current", "igbts", "continuousCollectorCurrent"),
        ("Rated Voltage", "capacitors", "ratedVoltage"),
        ("ESR", "capacitors", "esr"),
        ("Ripple Current", "capacitors", "rippleCurrent"),
        ("Capacitance", "capacitors", "capacitance"),
        ("Tolerance", "resistors", "tolerance"),
        ("Power Rating", "resistors", "powerRating"),
        ("Resistance Value", "resistors", "resistance"),
    ],
)
def test_match_param_name_happy(text: str, category: str, expected: str) -> None:
    assert match_param_name(text, category) == expected


def test_match_param_name_handles_embedded_whitespace() -> None:
    # "V\nDS" should still match the VDSS pattern via the
    # whitespace-stripped candidate form.
    assert match_param_name("V\nDSS", "mosfets") == "drainSourceVoltage"
    assert match_param_name("R DS (ON)", "mosfets") == "onResistance"


def test_match_param_name_miss_returns_none() -> None:
    assert match_param_name("Some Random Cell Text", "mosfets") is None
    assert match_param_name("", "mosfets") is None


def test_match_param_name_unknown_category_raises() -> None:
    with pytest.raises(ValueError, match="unknown category"):
        match_param_name("VDSS", "transistors")


def test_match_param_name_case_insensitive() -> None:
    assert match_param_name("vdss", "mosfets") == "drainSourceVoltage"
    assert match_param_name("rds(on)", "mosfets") == "onResistance"


# ---------------------------------------------------------------------------
# pick_value_from_row
# ---------------------------------------------------------------------------


def test_pick_value_from_row_happy() -> None:
    row = ["Drain-Source Voltage", "VDSS", "100 V"]
    assert pick_value_from_row(row, "drainSourceVoltage") == pytest.approx(100.0)


def test_pick_value_from_row_skips_symbol_column() -> None:
    # Symbol column "RDS(ON)" must be skipped — the value is in the
    # third cell.
    row = ["Drain-Source On-Resistance", "RDS(ON)", "20 mΩ"]
    assert pick_value_from_row(row, "onResistance") == pytest.approx(0.020)


def test_pick_value_from_row_strips_footnote_markers() -> None:
    row = ["Total Gate Charge", "Qg", "45 nC (1)"]
    assert pick_value_from_row(row, "totalGateCharge") == pytest.approx(45e-9)


def test_pick_value_from_row_strips_bracketed_notes() -> None:
    row = ["Output Capacitance", "Coss", "230 pF [Note 3]"]
    assert pick_value_from_row(row, "outputCapacitance") == pytest.approx(230e-12)


def test_pick_value_from_row_multiline_cell_takes_first_parseable() -> None:
    # When a cell carries multiple lines (typical "min\ntyp\nmax"
    # layout), the first numerically-parseable line wins.
    row = ["Forward Voltage", "VF", "0.7\n1.0\n1.2 V"]
    assert pick_value_from_row(row, "forwardVoltage") == pytest.approx(0.7)


def test_pick_value_from_row_raises_on_no_numeric() -> None:
    row = ["Reverse Recovery Charge", "Qrr", "TBD"]
    with pytest.raises(ValueError, match="no parseable numeric value"):
        pick_value_from_row(row, "reverseRecoveryCharge")


def test_pick_value_from_row_raises_on_short_row() -> None:
    with pytest.raises(ValueError, match="fewer than 2 cells"):
        pick_value_from_row(["only one cell"], "anything")


def test_pick_value_from_row_skips_unit_only_header_cells() -> None:
    # Some tables put the unit on its own row above the value
    # column.  A cell that's just "V" should not be parsed as 0 V;
    # the next cell is taken instead.
    row = ["VDSS", "V", "100"]
    # First non-name cell "V" is symbol-shaped, gets skipped; "100"
    # parses as 100.0.
    assert pick_value_from_row(row, "drainSourceVoltage") == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# filter_electrical_tables
# ---------------------------------------------------------------------------


def _tbl(*rows: list[str | None]) -> list[list[str | None]]:
    return list(rows)


def test_filter_picks_up_electrical_section() -> None:
    elec = _tbl(["Electrical Characteristics"], ["VDSS", "100 V"])
    other = _tbl(["Some Random Section"], ["foo", "bar"])
    result = filter_electrical_tables([other, elec])
    assert result == [elec]


def test_filter_handles_static_dynamic_switching_headers() -> None:
    static = _tbl(["Static Characteristics"], ["RDS(ON)", "20 mΩ"])
    dynamic = _tbl(["Dynamic Characteristics"], ["Qg", "45 nC"])
    switching = _tbl(["Switching Characteristics"], ["trr", "100 ns"])
    result = filter_electrical_tables([static, dynamic, switching])
    assert result == [static, dynamic, switching]


def test_filter_continues_in_section_until_terminator() -> None:
    # Once we see Electrical Characteristics, subsequent unlabelled
    # tables belong to the section — until a terminator is hit.
    elec = _tbl(["Electrical Characteristics"], ["VDSS", "100 V"])
    cont = _tbl(["IDS", "10 A"])  # unlabelled continuation
    thermal = _tbl(["Thermal Characteristics"], ["RthJC", "0.5"])
    post = _tbl(["Mechanical"], ["weight", "1 g"])  # after terminator
    result = filter_electrical_tables([elec, cont, thermal, post])
    assert elec in result
    assert cont in result
    assert thermal not in result
    assert post not in result


def test_filter_terminators_beat_catchall_characteristics() -> None:
    """Regression: "Thermal Characteristics" would otherwise match
    the generic ``"characteristics"`` catch-all in
    :data:`ELECTRICAL_SECTION_HEADERS` and be wrongly classified
    as electrical.  Strict-mode tests terminators FIRST.
    """
    thermal = _tbl(["Thermal Characteristics"], ["RthJC", "0.5"])
    result = filter_electrical_tables([thermal])
    assert result == []


def test_filter_catchall_characteristics_still_matches() -> None:
    # A bare "Characteristics" header (no qualifier) should still
    # be picked up via the catch-all — this is the *intended* use
    # of that entry.
    chr_tbl = _tbl(["Characteristics"], ["VDSS", "100 V"])
    result = filter_electrical_tables([chr_tbl])
    assert result == [chr_tbl]


def test_filter_ignores_empty_tables() -> None:
    elec = _tbl(["Electrical Characteristics"], ["VDSS", "100 V"])
    result = filter_electrical_tables([[], elec, []])
    assert result == [elec]


def test_filter_terminators_set_membership() -> None:
    # Sanity: every documented terminator includes a recognisable
    # word.  Guards against a typo that silently breaks termination.
    assert "thermal characteristics" in SECTION_TERMINATORS
    assert "package" in SECTION_TERMINATORS
    assert "characteristics" in ELECTRICAL_SECTION_HEADERS


# ---------------------------------------------------------------------------
# extract_params
# ---------------------------------------------------------------------------


def test_extract_params_mosfet_end_to_end() -> None:
    table = _tbl(
        ["Electrical Characteristics"],
        ["Drain-Source Voltage", "VDSS", "100 V"],
        ["Drain-Source On-Resistance", "RDS(ON)", "20 mΩ"],
        ["Continuous Drain Current", "ID", "30 A"],
        ["Total Gate Charge", "Qg", "45 nC"],
        ["Gate Threshold Voltage", "VGS(th)", "3 V"],
        ["Output Capacitance", "Coss", "230 pF"],
    )
    result = extract_params([table], category="mosfets")
    assert result["drainSourceVoltage"] == pytest.approx(100.0)
    assert result["onResistance"] == pytest.approx(0.020)
    assert result["continuousDrainCurrent"] == pytest.approx(30.0)
    assert result["totalGateCharge"] == pytest.approx(45e-9)
    assert result["gateThresholdVoltage"] == pytest.approx(3.0)
    assert result["outputCapacitance"] == pytest.approx(230e-12)


def test_extract_params_diode_end_to_end() -> None:
    table = _tbl(
        ["Electrical Characteristics"],
        ["Repetitive Peak Reverse Voltage", "VRRM", "600 V"],
        ["Forward Voltage", "VF", "1.5 V"],
        ["Average Rectified Forward Current", "IF(AV)", "10 A"],
        ["Reverse Recovery Charge", "Qrr", "20 nC"],
    )
    result = extract_params([table], category="diodes")
    assert result == pytest.approx(
        {
            "reverseVoltage": 600.0,
            "forwardVoltage": 1.5,
            "forwardCurrent": 10.0,
            "reverseRecoveryCharge": 20e-9,
        }
    )


def test_extract_params_first_occurrence_wins() -> None:
    # If the same parameter appears in two tables (e.g. typical in
    # Static then again in a summary table), the first one wins.
    first = _tbl(
        ["Static Characteristics"],
        ["RDS(ON)", "RDS(ON)", "20 mΩ"],
    )
    second = _tbl(
        ["Dynamic Characteristics"],
        ["RDS(ON)", "RDS(ON)", "50 mΩ"],  # would override if we let it
    )
    result = extract_params([first, second], category="mosfets")
    assert result["onResistance"] == pytest.approx(0.020)


def test_extract_params_skips_merged_section_banner() -> None:
    # pdfplumber sometimes merges two adjacent banner rows into one
    # cell.  Such cells must not be matched as parameter names.
    table = _tbl(
        ["Electrical Characteristics"],
        ["Static Characteristics  Dynamic Characteristics", "", ""],
        ["VDSS", "VDSS", "100 V"],
    )
    result = extract_params([table], category="mosfets")
    assert result == {"drainSourceVoltage": pytest.approx(100.0)}


def test_extract_params_raises_when_no_electrical_section() -> None:
    # No section header anywhere → strict-mode refuses to scan.
    table = _tbl(
        ["Random Table"],
        ["VDSS", "100 V"],
    )
    with pytest.raises(DatasheetParseError, match="no Electrical"):
        extract_params([table], category="mosfets")


def test_extract_params_require_section_false_scans_anyway() -> None:
    # Escape hatch for genuinely section-less datasheets.
    table = _tbl(
        ["Random Table"],
        ["VDSS", "VDSS", "100 V"],
    )
    result = extract_params([table], category="mosfets", require_section=False)
    assert result["drainSourceVoltage"] == pytest.approx(100.0)


def test_extract_params_unknown_category_raises() -> None:
    with pytest.raises(ValueError, match="unknown category"):
        extract_params([], category="transistors")


def test_extract_params_skips_rows_with_unparseable_value() -> None:
    # A recognised row whose value cell is unparseable ("TBD")
    # silently drops out of the result — it's the auditor's job to
    # notice the gap, not the extractor's.
    table = _tbl(
        ["Electrical Characteristics"],
        ["VDSS", "VDSS", "100 V"],
        ["Qrr", "Qrr", "TBD"],
    )
    result = extract_params([table], category="mosfets")
    assert "drainSourceVoltage" in result
    assert "reverseRecoveryCharge" not in result


# ---------------------------------------------------------------------------
# extract_required_params
# ---------------------------------------------------------------------------


def test_extract_required_params_happy() -> None:
    table = _tbl(
        ["Electrical Characteristics"],
        ["Drain-Source Voltage", "VDSS", "100 V"],
        ["Drain-Source On-Resistance", "RDS(ON)", "20 mΩ"],
        ["Continuous Drain Current", "ID", "30 A"],
        ["Total Gate Charge", "Qg", "45 nC"],
        ["Gate Threshold Voltage", "VGS(th)", "3 V"],
        ["Output Capacitance", "Coss", "230 pF"],
    )
    result = extract_required_params([table], category="mosfets", mpn="TEST123")
    assert set(result) >= {
        "drainSourceVoltage",
        "onResistance",
        "continuousDrainCurrent",
        "totalGateCharge",
        "gateThresholdVoltage",
        "outputCapacitance",
    }


def test_extract_required_params_raises_with_dotted_field() -> None:
    # Missing reverseRecoveryCharge (the chronic diode gap).
    table = _tbl(
        ["Electrical Characteristics"],
        ["VRRM", "VRRM", "600 V"],
        ["VF", "VF", "1.5 V"],
        ["IF(AV)", "IF(AV)", "10 A"],
    )
    with pytest.raises(IncompleteDatasheetError) as excinfo:
        extract_required_params([table], category="diodes", mpn="DIODE99")
    err = excinfo.value
    assert err.missing_field == "electrical.reverseRecoveryCharge"
    assert err.mpn == "DIODE99"
    assert err.source == "datasheet"
    # Subclasses IncompleteSourceError so generic handlers catch it.
    assert isinstance(err, IncompleteSourceError)


def test_extract_required_params_reports_first_missing_alphabetically() -> None:
    # Two fields missing → the one that sorts first wins so the
    # error message is deterministic across runs.
    table = _tbl(
        ["Electrical Characteristics"],
        ["VRRM", "VRRM", "600 V"],
        ["VF", "VF", "1.5 V"],
        # missing both forwardCurrent and reverseRecoveryCharge
    )
    with pytest.raises(IncompleteDatasheetError) as excinfo:
        extract_required_params([table], category="diodes", mpn="DIODE99")
    # sorted(["forwardCurrent", "reverseRecoveryCharge"])[0] == "forwardCurrent"
    assert excinfo.value.missing_field == "electrical.forwardCurrent"


# ---------------------------------------------------------------------------
# extract_tables
# ---------------------------------------------------------------------------


def test_extract_tables_file_not_found_raises(tmp_path: Path) -> None:
    with pytest.raises(DatasheetParseError, match="not found"):
        extract_tables(tmp_path / "nope.pdf")


def test_extract_tables_unparseable_pdf_raises(tmp_path: Path) -> None:
    # Real pdfplumber call on a bogus file — either it raises (and
    # we wrap as DatasheetParseError) or it parses zero tables (and
    # we raise DatasheetParseError ourselves).  Either way the
    # caller sees one exception type.
    bogus = tmp_path / "bogus.pdf"
    bogus.write_bytes(b"not really a pdf\n")
    with pytest.raises(DatasheetParseError):
        extract_tables(bogus)
