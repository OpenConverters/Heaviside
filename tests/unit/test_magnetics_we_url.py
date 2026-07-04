"""Tests for the Würth-magnetics datasheet URL resolver.

The corrector must fetch the datasheet for the EXACT article number and never
transform the MPN to dodge a 404 — ``744383560R33`` (0.33 µH) and
``74438356033`` (3.3 µH) are different parts (10× the inductance). A part whose
exact article has no published PDF is skipped and reported, never guessed.
"""

from __future__ import annotations

from pathlib import Path

from heaviside.librarian.datasheet.base import DatasheetDownloadError
from heaviside.librarian.datasheet.magnetics_we_url import (
    resolve_we_datasheet_pdf,
    we_datasheet_candidate_urls,
)

_DS = "https://www.we-online.com/components/products/datasheet/{}.pdf"


# ---------------------------------------------------------------------------
# candidate URL generation
# ---------------------------------------------------------------------------

def test_stored_pdf_is_first_candidate():
    url = _DS.format("74438356015")
    cands = we_datasheet_candidate_urls("74438356015", url)
    assert cands[0] == url


def test_empty_url_synthesises_from_exact_article():
    cands = we_datasheet_candidate_urls("744030002", "")
    assert cands == [_DS.format("744030002")]


def test_redexpert_page_is_not_returned_but_synthesises_pdf():
    # The spec-page URL is HTML, not a PDF; it must never be returned as-is.
    page = "https://www.we-online.com/redexpert/spec/744230121?ad"
    cands = we_datasheet_candidate_urls("744230121", page)
    assert page not in cands
    assert cands == [_DS.format("744230121")]


def test_katalog_url_kept_as_redirect_fallback_after_synthesised():
    kat = "https://www.we-online.com/katalog/en/datasheet/7447713015"
    cands = we_datasheet_candidate_urls("7447713015", kat)
    assert cands[0] == _DS.format("7447713015")
    assert kat in cands  # last-resort redirect fallback


def test_article_number_is_never_transformed():
    # R33 (0.33 µH) must NOT become 033 (3.3 µH). The only synthesised URL keys
    # on the verbatim article.
    cands = we_datasheet_candidate_urls("744383560R33", _DS.format("744383560R33"))
    assert all("744383560R33" in c for c in cands)
    assert _DS.format("74438356033") not in cands


def test_no_duplicate_candidates():
    url = _DS.format("74438356015")
    cands = we_datasheet_candidate_urls("74438356015", url)
    assert len(cands) == len(set(cands))


# ---------------------------------------------------------------------------
# resolver (fetch first candidate that serves a real PDF)
# ---------------------------------------------------------------------------

class _FakeCache:
    """Stub PdfCache: ``ok`` URLs write a %PDF file, everything else 404s."""

    def __init__(self, tmp: Path, ok: set[str], *, html: set[str] | None = None):
        self._tmp = tmp
        self._ok = ok
        self._html = html or set()
        self.fetched: list[str] = []

    def _path(self, url: str) -> Path:
        import hashlib

        return self._tmp / (hashlib.sha256(url.encode()).hexdigest() + ".pdf")

    def is_cached(self, url: str) -> bool:
        return self._path(url).is_file()

    def fetch(self, url: str, *, force: bool = False) -> Path:
        self.fetched.append(url)
        p = self._path(url)
        if url in self._ok:
            p.write_bytes(b"%PDF-1.7\n...")
            return p
        if url in self._html:
            p.write_bytes(b"<!DOCTYPE html><html>404</html>")
            return p
        raise DatasheetDownloadError(url, status_code=404)


def test_resolver_returns_first_pdf(tmp_path: Path):
    ref = "744230121"
    good = _DS.format(ref)
    cache = _FakeCache(tmp_path, ok={good})
    result = resolve_we_datasheet_pdf(cache, ref, "https://www.we-online.com/redexpert/spec/744230121?ad")
    assert result is not None
    url, path = result
    assert url == good
    assert path.read_bytes().startswith(b"%PDF-")


def test_resolver_skips_404_and_reports_none_for_r_suffix(tmp_path: Path):
    # 744383560R33: stored .pdf 404s and the only synthesised URL is the same →
    # no candidate resolves → None (caller skips + reports, no fabrication).
    ref = "744383560R33"
    cache = _FakeCache(tmp_path, ok=set())  # nothing resolves
    result = resolve_we_datasheet_pdf(cache, ref, _DS.format(ref))
    assert result is None


def test_resolver_rejects_200_html_and_tries_next(tmp_path: Path):
    # First candidate (stored .pdf) serves HTML-200 (not a PDF); resolver must
    # discard it (and purge the poisoned cache entry) and fall through.
    ref = "7447713015"
    stored_pdf = _DS.format(ref)  # pretend this one serves HTML
    kat = "https://www.we-online.com/katalog/en/datasheet/7447713015"
    cache = _FakeCache(tmp_path, ok={kat}, html={stored_pdf})
    result = resolve_we_datasheet_pdf(cache, ref, kat)
    # candidate order: stored .pdf (html) → synthesised (== stored, cached html,
    # deduped) → katalog (pdf). The html entry is purged and katalog wins.
    assert result is not None
    url, _path = result
    assert url == kat
    assert not cache._path(stored_pdf).exists()  # poisoned entry removed
