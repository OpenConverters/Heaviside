"""Unit tests for the extraction-completeness guard in ``extract_bom_from_pdf``
(no live LLM — the extraction boundary ``_extract_full_bom_rows`` is mocked).

The guard exists because one LLM draw of a complex/multi-page BOM table
occasionally drops a block of rows ("bad draw"), which is the real cause of
CR run-to-run variance. These tests pin the recovery behaviour:

* a draw that is already complete returns immediately — NO second LLM call;
* an under-covered draw is detected (via the source-text designator count),
  re-extracted, and MERGED so the dropped rows are recovered;
* the reference-designator detector counts real designators (C1, R12, ...) but
  not dielectric codes (C0G), package sizes (0402), or qualification tags
  (AEC-Q200).
"""
from __future__ import annotations

from unittest.mock import patch

import heaviside.stages.bom_extract as bx
from heaviside.stages.bom_extract import (
    BomComponent,
    _detect_expected_refdes,
    _merge_boms,
    extract_bom_from_pdf,
)


def _make_full_rows() -> list[dict]:
    """A complete LT83401-style draw: 20 real ceramic caps + 4 OPTION caps
    (24 total) plus the rest of the board (resistors / inductors / IC / bead)."""
    rows: list[dict] = []
    for i in range(1, 21):  # 20 real ceramics, fully populated
        rows.append({
            "ref_des": f"C{i}", "category": "capacitor", "mpn": f"GRM{i}KA",
            "value": "1uF", "technology": "X7R", "rated_voltage": "25V",
            "package": "0603", "manufacturer": "MURATA",
        })
    for i in range(21, 25):  # 4 OPTION (DNP) caps — sparse rows, still designators
        rows.append({"ref_des": f"C{i}", "category": "capacitor",
                     "value": "OPTION", "package": "0603"})
    for i in range(1, 9):  # 8 resistors
        rows.append({"ref_des": f"R{i}", "category": "resistor",
                     "mpn": f"ERJ{i}", "value": "10k"})
    rows += [
        {"ref_des": "L1", "category": "inductor", "mpn": "XEL4020", "value": "2.2uH"},
        {"ref_des": "L2", "category": "inductor", "mpn": "744438", "value": "4.7uH"},
        {"ref_des": "U1", "category": "ic", "mpn": "LT83401RUDB"},
        {"ref_des": "FB1", "category": "ferrite bead", "mpn": "BLM31", "value": "800Ohm"},
    ]
    return rows


def _pdf_text_for(rows: list[dict]) -> str:
    """A BOM-table-like source text that mentions every row's designator, so the
    detector recovers the full expected designator set from the text alone."""
    body = "\n".join(
        f'{r["ref_des"]} 1 {r.get("mpn", "")} {r.get("value", "")}' for r in rows
    )
    return "Bill of Materials\nRef Qty MPN Value\n" + body


def _cap_refs(bom: list[BomComponent]) -> set[str]:
    return {c.ref_des for c in bom if c.category == "capacitor"}


# --- the headline recovery test -----------------------------------------

def test_under_covered_first_draw_is_recovered_by_merge():
    """First draw drops the 20 real ceramics (leaving only the 4 OPTION caps);
    the second draw is complete. The guard must DETECT the under-coverage,
    re-extract, and the MERGED result must contain all 24 cap designators."""
    full = _make_full_rows()
    # "the fixture with the 20 real ceramics removed, leaving the 4 OPTION caps"
    partial = [r for r in full
               if not (r["category"] == "capacitor" and r.get("value") != "OPTION")]
    assert len(_cap_refs(extract_full := bx.extract_bom_from_rows(partial))) == 4
    del extract_full

    pdf_text = _pdf_text_for(full)

    with patch.object(bx, "_extract_full_bom_rows", side_effect=[partial, full]) as m:
        bom = extract_bom_from_pdf(pdf_text=pdf_text, reference="lt83401-lt83402")

    # It took a second draw (the bad first draw was detected and recovered)...
    assert m.call_count == 2
    # ...and the merge recovered every one of the 24 caps the text expects.
    assert _cap_refs(bom) == {f"C{i}" for i in range(1, 25)}
    # The recovered rows are the fully-populated ones (merge prefers richer rows).
    c1 = next(c for c in bom if c.ref_des == "C1")
    assert c1.mpn == "GRM1KA" and c1.value_si is not None


def test_complete_first_draw_does_not_retry():
    """A first draw that already covers the detected designators must return
    immediately — exactly ONE extraction call, no extra LLM cost."""
    full = _make_full_rows()
    pdf_text = _pdf_text_for(full)

    # side_effect has a SINGLE element: a spurious 2nd call would raise
    # StopIteration and fail the test loudly.
    with patch.object(bx, "_extract_full_bom_rows", side_effect=[full]) as m:
        bom = extract_bom_from_pdf(pdf_text=pdf_text, reference="lt83401-lt83402")

    assert m.call_count == 1
    assert _cap_refs(bom) == {f"C{i}" for i in range(1, 25)}


def test_guard_skipped_for_tiny_input():
    """Too few detected designators -> no trustworthy coverage signal -> accept
    the single draw as-is (no retry), even if it looks sparse."""
    rows = [{"ref_des": "C1", "category": "capacitor", "value": "1uF"},
            {"ref_des": "U1", "category": "ic", "mpn": "LT8640"}]
    pdf_text = "U1 1 LT8640\nC1 1 1uF"  # only 2 designators (< the guard minimum)

    with patch.object(bx, "_extract_full_bom_rows", side_effect=[rows]) as m:
        bom = extract_bom_from_pdf(pdf_text=pdf_text, reference="tiny")

    assert m.call_count == 1
    assert {c.ref_des for c in bom} == {"C1", "U1"}


def test_persistent_under_coverage_logs_known_incomplete(caplog):
    """If every draw is short, the guard exhausts its retries and surfaces a
    LOUD 'KNOWN-INCOMPLETE' warning rather than silently passing it off as
    complete (per the no-silent-shortcuts rule)."""
    full = _make_full_rows()
    partial = [r for r in full
               if not (r["category"] == "capacitor" and r.get("value") != "OPTION")]
    pdf_text = _pdf_text_for(full)

    # Every draw is the same bad partial -> coverage never reaches the threshold.
    with patch.object(bx, "_extract_full_bom_rows", side_effect=[partial, partial, partial]) as m:
        with caplog.at_level("WARNING"):
            bom = extract_bom_from_pdf(pdf_text=pdf_text, reference="lt83401-lt83402")

    assert m.call_count == 3  # first draw + _MAX_EXTRA_DRAWS (2)
    assert any("KNOWN-INCOMPLETE" in r.message for r in caplog.records)
    # Still returns what it has — it does NOT hard-raise.
    assert _cap_refs(bom) == {"C21", "C22", "C23", "C24"}


# --- the reference-designator detector ----------------------------------

def test_refdes_detector_finds_designators_not_codes():
    """The detector counts real designators (C1..C25, R1..R22, FB1, U1, L2,
    MH4) but NOT dielectric codes (C0G), package sizes (0402/0603/1206), or
    qualification tags (AEC-Q200)."""
    designators = (
        [f"C{i}" for i in range(1, 26)]
        + [f"R{i}" for i in range(1, 23)]
        + ["FB1", "U1", "L2", "MH4"]
    )
    text = (
        "Bill of Materials\n"
        + " ".join(designators)
        + "\nNotes: dielectric C0G, package 0402 0603 1206, X7R, 50V, AEC-Q200, "
          "MPN GCM31CL81H105KA55L ERJ-3EKF1001V, controller LT83401"
    )
    refs = _detect_expected_refdes(text)

    assert refs == set(designators)  # exactly the designators, nothing spurious
    for noise in ("C0G", "0402", "0603", "1206", "X7R", "Q200", "JP1"):
        assert noise not in refs, noise


def test_refdes_detector_ignores_lowercase_and_ranges_start_only():
    """Upper-case only (lower-case prose is not a designator), and a written
    range contributes only its leading token (conservative under-count)."""
    refs = _detect_expected_refdes("see u1 and d3 in the figure; populate C22-C24")
    assert "u1" not in refs and "d3" not in refs  # lower-case prose ignored
    assert refs == {"C22"}  # only the range start, not C23/C24


# --- the merge primitive -------------------------------------------------

def test_merge_prefers_richer_row_and_unions_refs():
    sparse_c1 = BomComponent(ref_des="C1", category="capacitor")  # bare
    rich_c1 = BomComponent(ref_des="C1", category="capacitor", mpn="GRM1",
                           value="1uF", value_si=1e-6, technology="X7R")
    only_in_b = BomComponent(ref_des="C2", category="capacitor", mpn="GRM2")

    merged = _merge_boms([sparse_c1], [rich_c1, only_in_b])
    by_ref = {c.ref_des: c for c in merged}

    assert set(by_ref) == {"C1", "C2"}  # union of designators
    assert by_ref["C1"].mpn == "GRM1"   # the richer C1 row won the collision
    # symmetric: richer row in the PRIMARY position must also be kept
    merged2 = _merge_boms([rich_c1], [sparse_c1])
    assert {c.ref_des: c for c in merged2}["C1"].mpn == "GRM1"
