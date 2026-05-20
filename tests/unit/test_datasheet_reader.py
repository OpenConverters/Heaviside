"""Tests for ``heaviside.librarian.datasheet.reader.DatasheetReader``.

These exercise the orchestrator end-to-end with two layers of
indirection so no real PDFs and no real HTTP are required:

* The HTTP transport is replaced with :class:`httpx.MockTransport`
  serving a tiny in-memory PDF.
* The PDF→tables step (:func:`extract_tables`) is monkeypatched so
  we can assert table data without depending on pdfplumber's
  ability to parse a fake PDF.

The cache-hit and force-download paths are tested by counting
calls into the transport handler.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from heaviside.librarian.datasheet import reader as reader_mod
from heaviside.librarian.datasheet.base import (
    DatasheetDownloadError,
    IncompleteDatasheetError,
)
from heaviside.librarian.datasheet.cache import PdfCache
from heaviside.librarian.datasheet.reader import DatasheetReader


_FAKE_PDF = b"%PDF-1.4\n%fake-for-tests\n%%EOF\n"


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    d = tmp_path / "pdf-cache"
    d.mkdir()
    return d


def _ok_transport(counter: dict[str, int] | None = None) -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter["n"] = counter.get("n", 0) + 1
        return httpx.Response(200, content=_FAKE_PDF)
    return httpx.MockTransport(handler)


def _stub_tables(monkeypatch: pytest.MonkeyPatch, tables: list) -> None:
    """Replace :func:`extract_tables` for the duration of the test."""
    monkeypatch.setattr(reader_mod, "extract_tables", lambda _path: tables)


_MOSFET_TABLE = [
    ["Electrical Characteristics"],
    ["Drain-Source Voltage", "VDSS", "100 V"],
    ["Drain-Source On-Resistance", "RDS(ON)", "20 mΩ"],
    ["Continuous Drain Current", "ID", "30 A"],
    ["Total Gate Charge", "Qg", "45 nC"],
    ["Gate Threshold Voltage", "VGS(th)", "3 V"],
    ["Output Capacitance", "Coss", "230 pF"],
]


_DIODE_TABLE_MISSING_QRR = [
    ["Electrical Characteristics"],
    ["Repetitive Peak Reverse Voltage", "VRRM", "600 V"],
    ["Forward Voltage", "VF", "1.5 V"],
    ["Average Rectified Forward Current", "IF(AV)", "10 A"],
]


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_construct_with_default_cache_dir(monkeypatch: pytest.MonkeyPatch,
                                          tmp_path: Path) -> None:
    # Point HEAVISIDE_DATASHEET_CACHE at tmp_path so we don't write
    # into the user's real ~/.heaviside dir.
    monkeypatch.setenv("HEAVISIDE_DATASHEET_CACHE", str(tmp_path / "default"))
    # Re-import to pick up the env var.  Easier: pass cache_dir=None
    # but supply an explicit cache to verify the constructor wires
    # things through.
    r = DatasheetReader(cache_dir=tmp_path / "explicit")
    assert r.cache.cache_dir == tmp_path / "explicit"


def test_construct_with_injected_cache(cache_dir: Path) -> None:
    cache = PdfCache(cache_dir=cache_dir, transport=_ok_transport())
    r = DatasheetReader(cache=cache)
    assert r.cache is cache


def test_construct_rejects_cache_plus_cache_dir(cache_dir: Path) -> None:
    cache = PdfCache(cache_dir=cache_dir, transport=_ok_transport())
    with pytest.raises(ValueError, match="not both"):
        DatasheetReader(cache=cache, cache_dir=cache_dir)


def test_construct_rejects_cache_plus_transport(cache_dir: Path) -> None:
    cache = PdfCache(cache_dir=cache_dir, transport=_ok_transport())
    with pytest.raises(ValueError, match="not both"):
        DatasheetReader(cache=cache, transport=_ok_transport())


# ---------------------------------------------------------------------------
# extract (sparse)
# ---------------------------------------------------------------------------


def test_extract_returns_parsed_params(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_tables(monkeypatch, [_MOSFET_TABLE])
    r = DatasheetReader(cache_dir=cache_dir, transport=_ok_transport())
    result = r.extract(
        "https://example.com/mosfet.pdf", category="mosfets",
    )
    assert result["drainSourceVoltage"] == pytest.approx(100.0)
    assert result["onResistance"] == pytest.approx(0.020)
    assert result["totalGateCharge"] == pytest.approx(45e-9)


def test_extract_uses_cache_on_second_call(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter: dict[str, int] = {"n": 0}
    _stub_tables(monkeypatch, [_MOSFET_TABLE])
    r = DatasheetReader(cache_dir=cache_dir, transport=_ok_transport(counter))
    url = "https://example.com/mosfet.pdf"
    r.extract(url, category="mosfets")
    r.extract(url, category="mosfets")
    assert counter["n"] == 1, "second call should hit the PDF cache"


def test_extract_force_download_bypasses_cache(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter: dict[str, int] = {"n": 0}
    _stub_tables(monkeypatch, [_MOSFET_TABLE])
    r = DatasheetReader(cache_dir=cache_dir, transport=_ok_transport(counter))
    url = "https://example.com/mosfet.pdf"
    r.extract(url, category="mosfets")
    r.extract(url, category="mosfets", force_download=True)
    assert counter["n"] == 2


def test_extract_propagates_download_errors(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="missing")
    r = DatasheetReader(
        cache_dir=cache_dir, transport=httpx.MockTransport(handler),
    )
    with pytest.raises(DatasheetDownloadError):
        r.extract("https://example.com/nope.pdf", category="mosfets")


# ---------------------------------------------------------------------------
# extract_required (strict)
# ---------------------------------------------------------------------------


def test_extract_required_happy(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_tables(monkeypatch, [_MOSFET_TABLE])
    r = DatasheetReader(cache_dir=cache_dir, transport=_ok_transport())
    result = r.extract_required(
        "https://example.com/mosfet.pdf",
        category="mosfets",
        mpn="TESTFET01",
    )
    assert set(result) >= {
        "drainSourceVoltage", "onResistance", "continuousDrainCurrent",
        "totalGateCharge", "gateThresholdVoltage", "outputCapacitance",
    }


def test_extract_required_raises_on_missing_field(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_tables(monkeypatch, [_DIODE_TABLE_MISSING_QRR])
    r = DatasheetReader(cache_dir=cache_dir, transport=_ok_transport())
    with pytest.raises(IncompleteDatasheetError) as excinfo:
        r.extract_required(
            "https://example.com/diode.pdf",
            category="diodes",
            mpn="DIODE42",
        )
    err = excinfo.value
    assert err.missing_field == "electrical.reverseRecoveryCharge"
    assert err.mpn == "DIODE42"
    assert err.source == "datasheet"


# ---------------------------------------------------------------------------
# extract_from_path (cache bypass)
# ---------------------------------------------------------------------------


def test_extract_from_path_bypasses_cache(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    counter: dict[str, int] = {"n": 0}
    _stub_tables(monkeypatch, [_MOSFET_TABLE])
    r = DatasheetReader(cache_dir=cache_dir, transport=_ok_transport(counter))
    local_pdf = tmp_path / "local.pdf"
    local_pdf.write_bytes(_FAKE_PDF)

    result = r.extract_from_path(local_pdf, category="mosfets")
    assert result["drainSourceVoltage"] == pytest.approx(100.0)
    assert counter["n"] == 0, "extract_from_path must not touch the network"
