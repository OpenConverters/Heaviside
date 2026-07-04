"""Tests for the non-Würth datasheet-PDF URL resolver.

Only vendors with a genuinely derivable pattern build a candidate URL (MPS by
exact SKU, Bourns by series). Coilcraft / Vishay / TDK have no MPN-derivable URL
(opaque GUID / internal doc-number / catalog taxonomy) and MUST return None
rather than fabricate one. The exact MPN is never transformed.
"""

from __future__ import annotations

from pathlib import Path

from heaviside.librarian.datasheet.base import DatasheetDownloadError
from heaviside.librarian.datasheet.magnetics_url import (
    datasheet_candidate_urls,
    normalize_manufacturer,
    resolve_datasheet_pdf_url,
)

_MPS = (
    "https://www.monolithicpower.com/en/documentview/productdocument/index/"
    "version/2/document_type/Datasheet/lang/en/sku/{}/"
)
_BOURNS = "https://www.bourns.com/docs/Product-Datasheets/{}.pdf"


# ---------------------------------------------------------------------------
# manufacturer-name normalisation
# ---------------------------------------------------------------------------

def test_manufacturer_name_variants_normalise():
    assert normalize_manufacturer("Monolithic Power Systems") == "mps"
    assert normalize_manufacturer("MPS") == "mps"
    assert normalize_manufacturer("Bourns Inc.") == "bourns"
    assert normalize_manufacturer("Coilcraft") == "coilcraft"
    assert normalize_manufacturer("Vishay Dale") == "vishay"
    assert normalize_manufacturer("TDK Corporation") == "tdk"
    assert normalize_manufacturer("Nichicon") is None


# ---------------------------------------------------------------------------
# candidate URL construction (pure, no IO)
# ---------------------------------------------------------------------------

def test_mps_candidate_keyed_on_exact_sku():
    cands = datasheet_candidate_urls("Monolithic Power", "MPL-AL6050-1R5")
    assert cands == [_MPS.format("MPL-AL6050-1R5")]


def test_bourns_candidate_strips_value_suffix_to_series():
    # SRP1245A-2R2M → series SRP1245A (the -2R2M value/tolerance code is dropped).
    cands = datasheet_candidate_urls("Bourns", "SRP1245A-2R2M")
    assert cands == [_BOURNS.format("SRP1245A")]


def test_bourns_series_letter_suffix_kept_verbatim():
    # The trailing 'A' is part of the series, NOT a value code — keep it.
    assert datasheet_candidate_urls("Bourns", "SRP1245A") == [_BOURNS.format("SRP1245A")]


def test_non_derivable_vendors_have_no_candidates():
    assert datasheet_candidate_urls("Coilcraft", "XGL6060-822") == []
    assert datasheet_candidate_urls("Vishay", "IHLP2020BZER2R2M01") == []
    assert datasheet_candidate_urls("TDK", "SPM6530T-1R5M") == []
    # Würth is owned by magnetics_we_url, not this resolver.
    assert datasheet_candidate_urls("Würth Elektronik", "74438356015") == []


def test_empty_mpn_yields_no_candidate():
    assert datasheet_candidate_urls("MPS", "") == []


# ---------------------------------------------------------------------------
# resolver (verify candidate bytes are a real PDF)
# ---------------------------------------------------------------------------

class _FakeCache:
    """Stub PdfCache: ``ok`` URLs write a %PDF file, ``html`` write HTML, else 404."""

    def __init__(self, tmp: Path, ok: set[str], *, html: set[str] | None = None):
        self._tmp = tmp
        self._ok = ok
        self._html = html or set()
        self.fetched: list[str] = []

    def _path(self, url: str) -> Path:
        import hashlib

        return self._tmp / (hashlib.sha256(url.encode()).hexdigest() + ".pdf")

    def fetch(self, url: str, *, force: bool = False) -> Path:
        self.fetched.append(url)
        p = self._path(url)
        if url in self._ok:
            p.write_bytes(b"%PDF-1.5\n...")
            return p
        if url in self._html:
            p.write_bytes(b"<!DOCTYPE html><html>bot challenge</html>")
            return p
        raise DatasheetDownloadError(url, status_code=404)


def test_resolver_returns_verified_mps_url(tmp_path: Path):
    good = _MPS.format("MPL-AL6050-1R5")
    cache = _FakeCache(tmp_path, ok={good})
    assert resolve_datasheet_pdf_url("MPS", "MPL-AL6050-1R5", cache=cache) == good


def test_resolver_returns_verified_bourns_url(tmp_path: Path):
    good = _BOURNS.format("SRP1245A")
    cache = _FakeCache(tmp_path, ok={good})
    assert resolve_datasheet_pdf_url("Bourns", "SRP1245A-2R2M", cache=cache) == good


def test_resolver_rejects_bot_gate_html_and_returns_none(tmp_path: Path):
    # MPS/Bourns are bot-gated; a 200-but-HTML response must NOT count as a PDF.
    url = _MPS.format("MPL-AL6050-1R5")
    cache = _FakeCache(tmp_path, ok=set(), html={url})
    assert resolve_datasheet_pdf_url("MPS", "MPL-AL6050-1R5", cache=cache) is None
    assert not cache._path(url).exists()  # poisoned entry purged


def test_resolver_none_for_non_derivable_vendor(tmp_path: Path):
    cache = _FakeCache(tmp_path, ok=set())
    assert resolve_datasheet_pdf_url("Coilcraft", "XGL6060-822", cache=cache) is None
    assert cache.fetched == []  # no candidate even attempted
