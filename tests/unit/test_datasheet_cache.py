"""Tests for ``heaviside.librarian.datasheet.cache``.

Covers:

* Content-addressed naming (same URL → same path; different URL → different).
* Atomic write semantics (no leftover ``.tmp`` files on success).
* Cache hit on second call (no second HTTP request).
* ``force=True`` re-downloads even when cached.
* HTTP non-2xx, transport failure, and empty-body responses all raise
  :class:`DatasheetDownloadError` with the URL preserved.
* ``is_cached`` correctly reports empty files as absent.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from heaviside.librarian.datasheet.base import DatasheetDownloadError
from heaviside.librarian.datasheet.cache import PdfCache, _url_digest


_SAMPLE_PDF_BYTES = b"%PDF-1.4\n%fake-pdf-for-tests\n%%EOF\n"


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    d = tmp_path / "pdf-cache"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Path lookup
# ---------------------------------------------------------------------------


def test_path_for_is_url_addressed(cache_dir: Path) -> None:
    cache = PdfCache(cache_dir=cache_dir)
    p1 = cache.path_for("https://example.com/a.pdf")
    p2 = cache.path_for("https://example.com/a.pdf")
    p3 = cache.path_for("https://example.com/b.pdf")
    assert p1 == p2
    assert p1 != p3
    assert p1.parent == cache_dir
    assert p1.suffix == ".pdf"
    # The digest in the filename matches the public _url_digest helper.
    assert _url_digest("https://example.com/a.pdf") in p1.name


def test_is_cached_false_for_missing(cache_dir: Path) -> None:
    cache = PdfCache(cache_dir=cache_dir)
    assert cache.is_cached("https://example.com/nope.pdf") is False


def test_is_cached_false_for_empty_file(cache_dir: Path) -> None:
    cache = PdfCache(cache_dir=cache_dir)
    path = cache.path_for("https://example.com/empty.pdf")
    path.write_bytes(b"")
    assert cache.is_cached("https://example.com/empty.pdf") is False


# ---------------------------------------------------------------------------
# Fetch (happy path)
# ---------------------------------------------------------------------------


def _ok_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_SAMPLE_PDF_BYTES)
    return httpx.MockTransport(handler)


def test_fetch_writes_to_addressed_path(cache_dir: Path) -> None:
    cache = PdfCache(cache_dir=cache_dir, transport=_ok_transport())
    url = "https://example.com/datasheet.pdf"
    path = cache.fetch(url)
    assert path == cache.path_for(url)
    assert path.read_bytes() == _SAMPLE_PDF_BYTES


def test_fetch_creates_cache_dir_lazily(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "cache"
    assert not nested.exists()
    cache = PdfCache(cache_dir=nested, transport=_ok_transport())
    cache.fetch("https://example.com/x.pdf")
    assert nested.is_dir()


def test_fetch_is_cache_hit_on_second_call(cache_dir: Path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=_SAMPLE_PDF_BYTES)

    cache = PdfCache(cache_dir=cache_dir, transport=httpx.MockTransport(handler))
    url = "https://example.com/cached.pdf"
    cache.fetch(url)
    cache.fetch(url)
    assert calls["n"] == 1, "second call should hit the cache, not the transport"


def test_fetch_force_redownloads(cache_dir: Path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=_SAMPLE_PDF_BYTES)

    cache = PdfCache(cache_dir=cache_dir, transport=httpx.MockTransport(handler))
    url = "https://example.com/forced.pdf"
    cache.fetch(url)
    cache.fetch(url, force=True)
    assert calls["n"] == 2


def test_fetch_leaves_no_tmp_files_on_success(cache_dir: Path) -> None:
    cache = PdfCache(cache_dir=cache_dir, transport=_ok_transport())
    cache.fetch("https://example.com/clean.pdf")
    leftovers = [p for p in cache_dir.iterdir() if p.name.startswith(".dl-")]
    assert leftovers == []


# ---------------------------------------------------------------------------
# Fetch (failure paths)
# ---------------------------------------------------------------------------


def test_fetch_http_4xx_raises(cache_dir: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    cache = PdfCache(cache_dir=cache_dir, transport=httpx.MockTransport(handler))
    with pytest.raises(DatasheetDownloadError) as excinfo:
        cache.fetch("https://example.com/missing.pdf")
    assert excinfo.value.status_code == 404
    assert excinfo.value.url == "https://example.com/missing.pdf"


def test_fetch_http_5xx_raises(cache_dir: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    cache = PdfCache(cache_dir=cache_dir, transport=httpx.MockTransport(handler))
    with pytest.raises(DatasheetDownloadError) as excinfo:
        cache.fetch("https://example.com/down.pdf")
    assert excinfo.value.status_code == 503


def test_fetch_transport_error_raises(cache_dir: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated DNS failure")

    cache = PdfCache(cache_dir=cache_dir, transport=httpx.MockTransport(handler))
    with pytest.raises(DatasheetDownloadError) as excinfo:
        cache.fetch("https://example.com/dns.pdf")
    assert excinfo.value.status_code is None
    assert "transport error" in str(excinfo.value)


def test_fetch_empty_body_raises(cache_dir: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    cache = PdfCache(cache_dir=cache_dir, transport=httpx.MockTransport(handler))
    with pytest.raises(DatasheetDownloadError) as excinfo:
        cache.fetch("https://example.com/empty.pdf")
    assert "empty body" in str(excinfo.value)


def test_fetch_follows_redirects(cache_dir: Path) -> None:
    """Manufacturer CDNs (TI, Wolfspeed) commonly 302 to a CDN host."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/redirect":
            return httpx.Response(
                302, headers={"Location": "https://cdn.example.com/final.pdf"},
            )
        return httpx.Response(200, content=_SAMPLE_PDF_BYTES)

    cache = PdfCache(cache_dir=cache_dir, transport=httpx.MockTransport(handler))
    path = cache.fetch("https://example.com/redirect")
    assert path.read_bytes() == _SAMPLE_PDF_BYTES
