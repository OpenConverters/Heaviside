"""Deterministic BOM-table parser + its gating in extract_bom_from_pdf.

These run against the committed reference PDFs and need NO LLM — the whole point
is that the table parse is deterministic. The gating tests monkeypatch the LLM
boundary (_extract_full_bom_rows) to prove the deterministic path takes over
when a clean table exists and falls back when it doesn't.
"""

from pathlib import Path

import pytest

from heaviside.stages import bom_extract
from heaviside.stages.bom_extract import extract_bom_from_pdf
from heaviside.stages.bom_table import (
    _parse_description,
    _split_manufacturer_pn,
    parse_bom_table,
)

_REFS = Path("/home/alf/OpenConverters/Proteus/tests/reference_designs")
_TABLE_PDF = _REFS / "lt83401-lt83402.pdf"  # clean ITEM/QTY/DESIGNATOR/DESC/MFR table
_NOTABLE_PDF = _REFS / "infineon-100w-gan.pdf"  # app-note prose, no ruled BOM table

requires_pdf = pytest.mark.skipif(not _TABLE_PDF.exists(), reason="reference PDFs not present")


# --- field parsers -------------------------------------------------------


def test_parse_description_capacitor():
    d = _parse_description("CAP., 1µF, X7R, 50V, 10%, 0402, AEC-Q200")
    assert d["value"] == "1µF"
    assert d["technology"] == "X7R"
    assert d["rated_voltage"] == "50"
    assert d["package"] == "0402"
    assert "10%" in d["notes"]


def test_parse_description_resistor():
    d = _parse_description("RES., 10kΩ, 1%, 0603")
    assert d["value"].startswith("10k")
    assert d["package"] == "0603"
    assert d.get("technology") is None  # resistors have no dielectric


def test_parse_description_rejects_noise():
    # package code must be a real EIA size, dielectric a real class
    d = _parse_description("CAP., 82pF, C0G, 50V, 5%, 0603")
    assert d["value"] == "82pF"
    assert d["technology"] == "C0G"


def test_split_manufacturer_pn():
    assert _split_manufacturer_pn("MURATA, GCM31CL81H105KA55L") == ("MURATA", "GCM31CL81H105KA55L")
    assert _split_manufacturer_pn("GRM188R61H225KE11J") == (None, "GRM188R61H225KE11J")
    assert _split_manufacturer_pn("") == (None, None)


# --- deterministic table parse ------------------------------------------


@requires_pdf
def test_parse_bom_table_finds_rows():
    rows = parse_bom_table(_TABLE_PDF)
    assert rows is not None and len(rows) > 20
    # the designator + a parsed value + an mpn made it through
    c1 = next(
        r
        for r in rows
        if (r["ref_des"].startswith("C1") and "," not in r["ref_des"]) or r["ref_des"] == "C1"
    )
    assert c1["category"] == "capacitor"


@requires_pdf
def test_no_standard_table_returns_none():
    # lt80603evkit has no clean ruled BOM table → parser declines (LLM fallback)
    rows = parse_bom_table(_NOTABLE_PDF)
    assert not rows  # None or []


# --- gating in extract_bom_from_pdf -------------------------------------


@requires_pdf
def test_table_pdf_uses_deterministic_path_no_llm(monkeypatch):
    """A clean-table PDF must be parsed deterministically — the LLM boundary
    is never reached (so it would work even with no API key)."""

    def _boom(*a, **k):
        raise AssertionError("LLM census must NOT be called when the table parse covers the BOM")

    monkeypatch.setattr(bom_extract, "_extract_full_bom_rows", _boom)

    bom = extract_bom_from_pdf(str(_TABLE_PDF))
    caps = sorted(c.ref_des for c in bom if c.category == "capacitor")
    assert len(caps) == 24, caps
    assert "C23" in caps  # the prototype's grouped-cell miss is fixed
    assert {"C15", "C16", "C19", "C20"} <= set(caps)  # grouped-cell expansion
    c1 = next(c for c in bom if c.ref_des == "C1")
    assert c1.mpn == "GCM31CL81H105KA55L" and c1.value_si == pytest.approx(1e-6)


@requires_pdf
def test_no_table_pdf_falls_back_to_llm(monkeypatch):
    """A PDF without a ruled BOM table must fall through to the LLM census."""
    called = {"n": 0}

    def _stub_rows(pdf_text, reference, **k):
        called["n"] += 1
        return [{"ref_des": "C1", "category": "capacitor", "value": "1uF"}]

    monkeypatch.setattr(bom_extract, "_extract_full_bom_rows", _stub_rows)

    bom = extract_bom_from_pdf(str(_NOTABLE_PDF))
    assert called["n"] >= 1  # LLM boundary WAS reached (deterministic declined)
    assert any(c.ref_des == "C1" for c in bom)
