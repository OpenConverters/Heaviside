"""Tests for ``heaviside.librarian.fetcher.digikey``.

Uses :class:`httpx.MockTransport` to inject deterministic responses
so we never touch the network.  Each test owns a fresh
:class:`TokenCache` rooted in ``tmp_path``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from heaviside.librarian.fetcher.auth import (
    DigiKeyCredentials,
    MissingCredentialError,
    TokenCache,
)
from heaviside.librarian.fetcher.base import (
    DistributorError,
    MalformedResponseError,
    RateLimitError,
)
from heaviside.librarian.fetcher.digikey import (
    DIGIKEY_PROD_BASE,
    DigiKeyClient,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _creds(refresh: str | None = "refresh-tok") -> DigiKeyCredentials:
    return DigiKeyCredentials(
        client_id="cid",
        client_secret="secret",
        refresh_token=refresh,
    )


def _cache(tmp_path: Path) -> TokenCache:
    return TokenCache(path=tmp_path / "tok.json")


def _seed_fresh_token(cache: TokenCache, *, token: str = "live-access") -> None:
    cache.save(
        access_token=token,
        refresh_token="cached-refresh",
        expires_in=1800,
    )


def _make_client(
    handler,
    *,
    tmp_path: Path,
    creds: DigiKeyCredentials | None = None,
) -> DigiKeyClient:
    transport = httpx.MockTransport(handler)
    return DigiKeyClient(
        creds or _creds(),
        token_cache=_cache(tmp_path),
        transport=transport,
    )


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_rejects_missing_client_id(tmp_path: Path) -> None:
    with pytest.raises(MissingCredentialError, match="client_id"):
        DigiKeyClient(
            DigiKeyCredentials(client_id="", client_secret="x"),
            token_cache=_cache(tmp_path),
            transport=httpx.MockTransport(lambda _r: httpx.Response(200)),
        )


def test_default_base_url_is_production() -> None:
    assert DIGIKEY_PROD_BASE == "https://api.digikey.com"


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------


def test_get_access_token_returns_cached_when_fresh(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _seed_fresh_token(cache, token="from-cache")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Unexpected HTTP call: {request.url}")

    client = DigiKeyClient(
        _creds(),
        token_cache=cache,
        transport=httpx.MockTransport(handler),
    )
    assert client.get_access_token() == "from-cache"


def test_get_access_token_refreshes_when_cache_stale(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    # Seed an expired token.
    cache.path.write_text(
        json.dumps(
            {
                "access_token": "old",
                "refresh_token": "cached-refresh",
                "expires_at": time.time() - 100.0,
            }
        ),
        encoding="utf-8",
    )

    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.path == "/v1/oauth2/token"
        body = request.content.decode()
        # The refresh token in the cache must win over the credentials one.
        assert "refresh_token=cached-refresh" in body
        assert "grant_type=refresh_token" in body
        return httpx.Response(
            200,
            json={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 1800,
                "token_type": "Bearer",
            },
        )

    client = DigiKeyClient(
        _creds(),
        token_cache=cache,
        transport=httpx.MockTransport(handler),
    )
    token = client.get_access_token()
    assert token == "new-access"
    assert len(calls) == 1
    # The cache was updated with the new refresh token.
    payload = cache.load()
    assert payload is not None
    assert payload["access_token"] == "new-access"
    assert payload["refresh_token"] == "new-refresh"


def test_get_access_token_uses_credentials_refresh_when_no_cache(tmp_path: Path) -> None:
    cache = _cache(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        assert "refresh_token=refresh-tok" in request.content.decode()
        return httpx.Response(
            200,
            json={"access_token": "fresh", "refresh_token": "r2", "expires_in": 1800},
        )

    client = DigiKeyClient(
        _creds(),
        token_cache=cache,
        transport=httpx.MockTransport(handler),
    )
    assert client.get_access_token() == "fresh"


def test_refresh_token_failure_raises_distributor_error(tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad_grant")

    client = _make_client(handler, tmp_path=tmp_path)
    with pytest.raises(DistributorError) as excinfo:
        client.get_access_token()
    err = excinfo.value
    assert err.status_code == 400
    assert err.distributor == "digikey"
    assert "bad_grant" in err.body


def test_refresh_token_response_missing_access_token(tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"refresh_token": "r"})

    client = _make_client(handler, tmp_path=tmp_path)
    with pytest.raises(MalformedResponseError, match="access_token"):
        client.get_access_token()


def test_get_access_token_no_refresh_token_anywhere_raises(tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not be called — no refresh token")

    client = DigiKeyClient(
        _creds(refresh=None),
        token_cache=_cache(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(MissingCredentialError, match="No Digi-Key refresh token"):
        client.get_access_token()


# ---------------------------------------------------------------------------
# get_product
# ---------------------------------------------------------------------------


def test_get_product_happy_path(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _seed_fresh_token(cache)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/Search/v3/Products/C3M0020075K"
        assert request.headers["Authorization"] == "Bearer live-access"
        assert request.headers["X-DIGIKEY-Client-Id"] == "cid"
        return httpx.Response(200, json={"Product": {"ManufacturerPartNumber": "C3M0020075K"}})

    client = DigiKeyClient(
        _creds(),
        token_cache=cache,
        transport=httpx.MockTransport(handler),
    )
    payload = client.get_product("C3M0020075K")
    assert payload["Product"]["ManufacturerPartNumber"] == "C3M0020075K"


def test_get_product_rejects_empty_mpn(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _seed_fresh_token(cache)
    client = DigiKeyClient(
        _creds(),
        token_cache=cache,
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={})),
    )
    with pytest.raises(ValueError, match="non-empty MPN"):
        client.get_product("")


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def test_search_happy_path_sends_expected_body(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _seed_fresh_token(cache)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/Search/v3/Products/Keyword"
        body = json.loads(request.content)
        assert body["Keywords"] == "SiC MOSFET"
        assert body["RecordCount"] == 20
        assert body["RecordStartPosition"] == 5
        assert "ManufacturerPartSearch" in body["SearchOptions"]
        return httpx.Response(200, json={"Products": [{"Mpn": "x"}], "ProductsCount": 1})

    client = DigiKeyClient(
        _creds(),
        token_cache=cache,
        transport=httpx.MockTransport(handler),
    )
    payload = client.search("SiC MOSFET", limit=20, offset=5)
    assert payload["ProductsCount"] == 1
    assert payload["Products"][0]["Mpn"] == "x"


def test_search_missing_products_key_raises_malformed(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _seed_fresh_token(cache)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ProductsCount": 0})

    client = DigiKeyClient(
        _creds(),
        token_cache=cache,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(MalformedResponseError, match="Products"):
        client.search("anything")


def test_search_rejects_empty_keywords(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _seed_fresh_token(cache)
    client = DigiKeyClient(
        _creds(),
        token_cache=cache,
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={"Products": []})),
    )
    with pytest.raises(ValueError, match="non-empty keyword"):
        client.search("")


# ---------------------------------------------------------------------------
# 401 → refresh → retry once
# ---------------------------------------------------------------------------


def test_401_triggers_single_refresh_and_retry(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _seed_fresh_token(cache, token="stale-but-cached")

    state = {"product_calls": 0, "refresh_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            state["refresh_calls"] += 1
            return httpx.Response(
                200,
                json={
                    "access_token": "rotated",
                    "refresh_token": "rotated-refresh",
                    "expires_in": 1800,
                },
            )
        if request.url.path.startswith("/Search/v3/Products/"):
            state["product_calls"] += 1
            if state["product_calls"] == 1:
                assert request.headers["Authorization"] == "Bearer stale-but-cached"
                return httpx.Response(401, text="token expired")
            # Retried call must use the rotated token.
            assert request.headers["Authorization"] == "Bearer rotated"
            return httpx.Response(200, json={"Product": {"Mpn": "x"}})
        raise AssertionError(f"Unexpected URL {request.url}")

    client = DigiKeyClient(
        _creds(),
        token_cache=cache,
        transport=httpx.MockTransport(handler),
    )
    payload = client.get_product("X")
    assert payload == {"Product": {"Mpn": "x"}}
    assert state["product_calls"] == 2
    assert state["refresh_calls"] == 1


def test_second_401_after_refresh_surfaces_as_distributor_error(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _seed_fresh_token(cache)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(
                200,
                json={"access_token": "still-bad", "refresh_token": "r", "expires_in": 1800},
            )
        # Product endpoint always returns 401.
        return httpx.Response(401, text="invalid_token")

    client = DigiKeyClient(
        _creds(),
        token_cache=cache,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(DistributorError) as excinfo:
        client.get_product("X")
    assert excinfo.value.status_code == 401


# ---------------------------------------------------------------------------
# Rate limiting & other error mapping
# ---------------------------------------------------------------------------


def test_429_raises_rate_limit_error_with_retry_after(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _seed_fresh_token(cache)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down", headers={"Retry-After": "42"})

    client = DigiKeyClient(
        _creds(),
        token_cache=cache,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RateLimitError) as excinfo:
        client.get_product("X")
    assert excinfo.value.retry_after_seconds == 42.0
    assert excinfo.value.distributor == "digikey"


def test_500_raises_distributor_error(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _seed_fresh_token(cache)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    client = DigiKeyClient(
        _creds(),
        token_cache=cache,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(DistributorError) as excinfo:
        client.get_product("X")
    assert excinfo.value.status_code == 503
    assert "upstream down" in excinfo.value.body


def test_non_json_body_raises_malformed(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _seed_fresh_token(cache)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    client = DigiKeyClient(
        _creds(),
        token_cache=cache,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(MalformedResponseError, match="non-JSON"):
        client.get_product("X")
