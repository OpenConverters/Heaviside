"""Unit tests for heaviside.pipeline.url_fetch — the two-layer document fetch
that defeats manufacturer-CDN (Akamai/Cloudflare) bot blocks."""

from __future__ import annotations

import pytest

from heaviside.pipeline import url_fetch
from heaviside.pipeline.url_fetch import (
    DocumentFetchError,
    FetchedDocument,
    fetch_document,
)


def test_browser_headers_look_like_a_real_browser():
    h = url_fetch._BROWSER_HEADERS
    assert "Chrome/" in h["User-Agent"]
    # Akamai checks for the browser-shaped header SET, not just the UA.
    for key in ("Accept", "Accept-Language", "Sec-Fetch-Mode", "Sec-CH-UA"):
        assert key in h


def test_httpx_success_does_not_touch_browser(monkeypatch):
    monkeypatch.setattr(
        url_fetch, "_fetch_httpx",
        lambda url, *, timeout: FetchedDocument(b"%PDF-1.7", "application/pdf", url, "httpx"),
    )
    def _no_browser(*a, **k):
        raise AssertionError("browser layer must not run when httpx succeeds")
    monkeypatch.setattr(url_fetch, "_fetch_browser", _no_browser)
    doc = fetch_document("https://www.analog.com/x.pdf")
    assert doc.via == "httpx" and doc.content == b"%PDF-1.7"


def test_bot_block_escalates_to_browser(monkeypatch):
    def _blocked(url, *, timeout):
        raise url_fetch._BotBlocked(403)
    monkeypatch.setattr(url_fetch, "_fetch_httpx", _blocked)
    monkeypatch.setattr(
        url_fetch, "_fetch_browser",
        lambda url, *, timeout: FetchedDocument(b"%PDF-1.7", "application/pdf", url, "browser"),
    )
    doc = fetch_document("https://www.analog.com/x.pdf")
    assert doc.via == "browser"


def test_both_layers_fail_raises_with_detail(monkeypatch):
    def _blocked(url, *, timeout):
        raise url_fetch._BotBlocked(403)
    def _browser_fail(url, *, timeout):
        raise DocumentFetchError("browser fetch returned HTTP 403")
    monkeypatch.setattr(url_fetch, "_fetch_httpx", _blocked)
    monkeypatch.setattr(url_fetch, "_fetch_browser", _browser_fail)
    with pytest.raises(DocumentFetchError, match="403"):
        fetch_document("https://www.analog.com/x.pdf")


def test_httpx_404_is_not_a_bot_block(monkeypatch):
    """A real 404 is a DocumentFetchError from the httpx layer (not a _BotBlocked
    escalation). Use httpx.MockTransport to exercise the real _fetch_httpx body."""
    import httpx

    transport = httpx.MockTransport(lambda req: httpx.Response(404, text="not found"))
    real_client = httpx.Client

    def _client(**kwargs):
        kwargs["transport"] = transport
        return real_client(**kwargs)

    monkeypatch.setattr(httpx, "Client", _client)
    with pytest.raises(DocumentFetchError, match="404"):
        url_fetch._fetch_httpx("https://x/404", timeout=5)


def test_httpx_403_classifies_as_bot_block(monkeypatch):
    import httpx

    transport = httpx.MockTransport(lambda req: httpx.Response(403, text="denied"))
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(**{**kw, "transport": transport})
    )
    with pytest.raises(url_fetch._BotBlocked):
        url_fetch._fetch_httpx("https://x", timeout=5)


def test_bot_block_statuses_cover_the_usual_suspects():
    assert {403, 429, 503}.issubset(url_fetch._BOT_BLOCK_STATUSES)
