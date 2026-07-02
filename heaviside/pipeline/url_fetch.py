"""Robust document fetch for the cross-reference-from-URL flow.

Manufacturer CDNs (Analog Devices, TI, Infineon — all behind Akamai/Cloudflare
bot protection) reject the bare ``User-Agent: python-httpx`` / minimal-header
requests with a ``403 Forbidden``. :func:`fetch_document` defeats that in two
layers, escalating only when needed:

1. **httpx with a full browser header profile** — a real Chrome User-Agent plus
   the ``Accept`` / ``Accept-Language`` / ``Sec-Fetch-*`` / ``Sec-CH-UA`` headers
   a browser always sends. This alone clears the majority of CDN heuristics
   (they check for a browser-shaped header set, not just the UA string).
2. **headless stealth Chromium** — when layer 1 still hits a bot-block status
   (403/429/503), re-fetch through a real headless browser (``playwright`` +
   ``playwright-stealth``). The browser presents a genuine TLS fingerprint and
   JS environment, which is what Akamai's stricter rules actually gate on.

If both layers fail the function raises ``DocumentFetchError`` with the status
and the layers tried — it never returns a partial/empty body or a CDN error
page masquerading as the document (CLAUDE.md: surface problems, no silent
shortcuts).
"""

from __future__ import annotations

import contextlib
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

__all__ = [
    "DocumentFetchError",
    "FetchedDocument",
    "UnsafeURLError",
    "fetch_document",
    "guard_public_url",
]

# Redirects are followed manually so each hop can be re-validated (a public URL
# can 302 to a private one — the SSRF happens at the connection, before we ever
# see the final URL).
_MAX_REDIRECTS = 5


class UnsafeURLError(RuntimeError):
    """Raised when a URL targets a non-public / non-http(s) endpoint (SSRF)."""


def guard_public_url(url: str) -> None:
    """Reject a URL that is not http(s) or resolves to a non-public address.

    Blocks the SSRF class where a user-supplied URL points the server at
    localhost, the cloud metadata endpoint (169.254.169.254), or any private /
    link-local / reserved range. Every resolved address must be global; a host
    that resolves to a mix of public and private addresses is rejected.

    Note: local DNS resolution here can differ from what the HTTP client later
    connects to (DNS rebinding / a configured outbound proxy) — that residual
    is out of scope; this closes the direct and per-redirect-hop vectors.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeURLError(f"unsupported URL scheme {parts.scheme!r} (only http/https)")
    host = parts.hostname
    if not host:
        raise UnsafeURLError(f"URL has no host: {url!r}")
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise UnsafeURLError(f"cannot resolve host {host!r}: {exc}") from exc
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        # An IPv4-mapped IPv6 address (::ffff:a.b.c.d) must be judged on the v4.
        if getattr(ip, "ipv4_mapped", None) is not None:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise UnsafeURLError(
                f"host {host!r} resolves to non-public address {addr} — refused (SSRF guard)"
            )

# A current desktop-Chrome header set. Kept together so the UA and the
# Sec-CH-UA client hints agree (a mismatch is itself a bot tell).
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_BROWSER_HEADERS = {
    "User-Agent": _CHROME_UA,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "application/pdf,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Sec-CH-UA": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
}

# Statuses that signal "blocked as a bot" rather than "genuinely gone" — only
# these escalate to the browser layer (a real 404 should not spin up Chromium).
_BOT_BLOCK_STATUSES = frozenset({401, 403, 429, 503})


class DocumentFetchError(RuntimeError):
    """Raised when a URL could not be fetched after every available layer."""


@dataclass(frozen=True)
class FetchedDocument:
    """A successfully fetched document body + its declared content type."""

    content: bytes
    content_type: str
    final_url: str
    via: str  # which layer succeeded: "httpx" | "browser"


def fetch_document(url: str, *, timeout: float = 90.0) -> FetchedDocument:
    """Fetch ``url`` as bytes, escalating httpx → headless browser on a CDN
    bot-block. Raises :class:`DocumentFetchError` if no layer succeeds."""
    guard_public_url(url)  # SSRF: refuse non-public targets before connecting
    httpx_status: int | None = None
    httpx_error: str | None = None
    try:
        return _fetch_httpx(url, timeout=timeout)
    except _BotBlocked as blocked:
        httpx_status = blocked.status  # escalate to the browser layer
    except DocumentFetchError as exc:
        # transport error / non-block HTTP error — keep the detail, but still
        # give the browser a try (some hosts reset non-browser connections).
        httpx_error = str(exc)

    try:
        return _fetch_browser(url, timeout=timeout)
    except DocumentFetchError as browser_exc:
        detail = []
        if httpx_status is not None:
            detail.append(f"httpx got HTTP {httpx_status} (bot-block)")
        if httpx_error is not None:
            detail.append(f"httpx error: {httpx_error}")
        detail.append(f"browser layer: {browser_exc}")
        hint = ""
        # A bot-block that survives the real-browser layer is an edge/IP-level
        # block (the CDN flags this host's IP before any challenge runs) — no
        # client-side trick gets past it. Point the user at the upload path.
        if httpx_status in (401, 403, 429) or "403" in str(browser_exc):
            hint = (
                " — this CDN is blocking this server's IP at the edge (Akamai/"
                "Cloudflare bot management); download the file in your own browser "
                "and use the PDF upload instead, or set HEAVISIDE_HTTP_PROXY to a "
                "residential proxy."
            )
        raise DocumentFetchError(
            f"could not fetch {url!r} — {'; '.join(detail)}{hint}"
        ) from browser_exc


class _BotBlocked(Exception):
    """Internal: httpx got a status that warrants the browser escalation."""

    def __init__(self, status: int) -> None:
        super().__init__(f"bot-block status {status}")
        self.status = status


def _proxy() -> str | None:
    """An optional outbound proxy (residential, to escape datacenter-IP CDN
    blocks). Read from HEAVISIDE_HTTP_PROXY; applies to both fetch layers."""
    import os

    p = os.environ.get("HEAVISIDE_HTTP_PROXY", "").strip()
    return p or None


def _fetch_httpx(url: str, *, timeout: float) -> FetchedDocument:
    import httpx

    # Redirects are followed MANUALLY so each hop is SSRF-validated before we
    # connect to it (httpx's own follow_redirects would connect first).
    kwargs: dict = {
        "follow_redirects": False, "timeout": timeout, "headers": _BROWSER_HEADERS,
    }
    proxy = _proxy()
    if proxy:
        kwargs["proxy"] = proxy
    current = url
    try:
        with httpx.Client(**kwargs) as client:
            for _hop in range(_MAX_REDIRECTS + 1):
                guard_public_url(current)  # re-validate every hop
                resp = client.get(current)
                nxt = resp.next_request
                if resp.is_redirect and nxt is not None:
                    current = str(nxt.url)
                    continue
                break
            else:
                raise DocumentFetchError(
                    f"too many redirects (> {_MAX_REDIRECTS}) fetching {url!r}"
                )
    except httpx.HTTPError as exc:
        raise DocumentFetchError(f"transport error fetching {url!r}: {exc}") from exc

    if resp.status_code in _BOT_BLOCK_STATUSES:
        raise _BotBlocked(resp.status_code)
    if resp.status_code >= 400:
        raise DocumentFetchError(
            f"HTTP {resp.status_code} fetching {url!r}: {resp.text[:200]!r}"
        )
    if not resp.content:
        raise DocumentFetchError(f"fetched {url!r} but body was empty")
    return FetchedDocument(
        content=resp.content,
        content_type=resp.headers.get("content-type", ""),
        final_url=str(resp.url),
        via="httpx",
    )


def _fetch_browser(url: str, *, timeout: float) -> FetchedDocument:
    """Fetch through headless stealth Chromium — a real TLS fingerprint + JS
    environment, which is what Akamai/Cloudflare strict rules gate on.

    Headless ALWAYS (CLAUDE.md). Raises :class:`DocumentFetchError` if
    playwright is unavailable or the navigation fails/blocks."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - dep present in web env
        raise DocumentFetchError(
            "playwright is not installed — cannot escalate past the CDN "
            "bot-block to a real browser fetch"
        ) from exc

    try:
        from playwright_stealth import Stealth
    except ImportError:  # stealth is optional; navigate without it if absent
        Stealth = None  # type: ignore[assignment]

    from urllib.parse import urlsplit

    timeout_ms = int(timeout * 1000)
    proxy = _proxy()
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(url))
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx_kwargs: dict = {
                    "user_agent": _CHROME_UA,
                    "locale": "en-US",
                    "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9"},
                }
                if proxy:
                    ctx_kwargs["proxy"] = {"server": proxy}
                context = browser.new_context(**ctx_kwargs)
                if Stealth is not None:
                    Stealth().apply_stealth_sync(context)
                page = context.new_page()
                # Warm up on the origin first so a JS bot-challenge can run and
                # set its cookie in the context; then navigate to the real URL
                # with the browser's own network stack (real TLS fingerprint).
                # page.goto returns raw bytes for PDFs too (before the viewer).
                with contextlib.suppress(Exception):
                    page.goto(origin, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(1500)
                resp = page.goto(url, wait_until="commit", timeout=timeout_ms)
                status = resp.status if resp else None
                if status is None or status in _BOT_BLOCK_STATUSES or status >= 400:
                    raise DocumentFetchError(
                        f"browser fetch of {url!r} returned HTTP {status}"
                    )
                body = resp.body()
                if not body:
                    raise DocumentFetchError(
                        f"browser fetched {url!r} but body was empty"
                    )
                ctype = resp.headers.get("content-type", "")
                return FetchedDocument(
                    content=body, content_type=ctype, final_url=resp.url, via="browser"
                )
            finally:
                browser.close()
    except DocumentFetchError:
        raise
    except Exception as exc:
        raise DocumentFetchError(f"browser fetch of {url!r} failed: {exc}") from exc
