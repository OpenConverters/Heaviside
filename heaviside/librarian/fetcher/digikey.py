"""Digi-Key Product Information API v3 client (strict mode).

Wraps two endpoints used by Heaviside's importer pipeline:

* ``POST /Search/v3/Products/Keyword`` — keyword search.
* ``GET  /Search/v3/Products/{mpn}`` — fetch a single product by
  manufacturer part number.

Plus the OAuth2 refresh-token grant at
``POST /v1/oauth2/token`` (used internally — callers never invoke
it directly).

Strict-mode departures from Proteus
-----------------------------------

* No hardcoded ``client_id`` / ``client_secret`` — credentials must
  arrive via :func:`heaviside.librarian.fetcher.auth.load_credentials`.
* Every non-2xx response raises a typed exception
  (:class:`RateLimitError` for 429, :class:`DistributorError`
  otherwise).  Proteus silently returned ``(empty, False)`` on every
  failure, hiding auth bugs from the caller.
* 401 triggers exactly one transparent refresh-and-retry; a second
  401 surfaces as :class:`DistributorError` (not retried again).
* The HTTP transport can be overridden via the ``transport``
  constructor argument — tests use :class:`httpx.MockTransport` and
  inject deterministic responses without monkeypatching the network.
"""

from __future__ import annotations

from typing import Any

import httpx

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

__all__ = [
    "DIGIKEY_PROD_BASE",
    "DIGIKEY_SANDBOX_BASE",
    "DigiKeyClient",
]


DIGIKEY_PROD_BASE = "https://api.digikey.com"
DIGIKEY_SANDBOX_BASE = "https://sandbox-api.digikey.com"

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_EXPIRES_IN = 1798  # matches Proteus fallback when API omits the field


def _parse_retry_after(header: str | None) -> float | None:
    """Parse a ``Retry-After`` header value as seconds.

    Per RFC 7231 the value can be either an integer number of seconds
    or an HTTP-date.  We only handle the integer form; an HTTP-date
    returns ``None`` (caller treats unknown as "back off briefly").
    """
    if header is None:
        return None
    stripped = header.strip()
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError:
        return None


class DigiKeyClient:
    """Strict-mode Digi-Key Product Information API v3 client."""

    def __init__(
        self,
        credentials: DigiKeyCredentials,
        *,
        base_url: str = DIGIKEY_PROD_BASE,
        token_cache: TokenCache | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        if not credentials.client_id or not credentials.client_secret:
            raise MissingCredentialError("DigiKeyClient requires both client_id and client_secret.")
        self.credentials = credentials
        self.base_url = base_url.rstrip("/")
        self.token_cache = token_cache or TokenCache()
        # Build an explicit Client so the transport can be swapped for
        # MockTransport in tests without monkeypatching anything.
        self._client = httpx.Client(transport=transport, timeout=timeout)

    # ------------------------------------------------------------------
    # Context management — close the underlying httpx.Client cleanly.
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> DigiKeyClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def get_access_token(self) -> str:
        """Return a fresh access token, refreshing via the cache if stale.

        Raises:
            MissingCredentialError: No refresh token is available
                (caller must run the authorization-code flow first).
            DistributorError: Digi-Key rejected the refresh request.
        """
        cached = self.token_cache.load()
        if cached is not None and self.token_cache.is_fresh(cached):
            return str(cached["access_token"])

        # Choose a refresh token: cache wins (it's the most recent one
        # Digi-Key issued) and we fall back to the credentials file.
        refresh_token: str | None = None
        if cached is not None:
            cached_refresh = cached.get("refresh_token")
            if isinstance(cached_refresh, str) and cached_refresh:
                refresh_token = cached_refresh
        if refresh_token is None:
            refresh_token = self.credentials.refresh_token
        if not refresh_token:
            raise MissingCredentialError(
                "No Digi-Key refresh token available.  Run the "
                "authorization-code flow once to provision one."
            )
        return self._refresh_token(refresh_token)

    def _refresh_token(self, refresh_token: str) -> str:
        """Exchange a refresh token for a new access token, updating the cache."""
        response = self._client.post(
            f"{self.base_url}/v1/oauth2/token",
            data={
                "client_id": self.credentials.client_id,
                "client_secret": self.credentials.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            headers={"accept": "application/json"},
        )
        if response.status_code != 200:
            raise DistributorError(
                "digikey",
                response.status_code,
                response.text,
                message=(
                    f"Digi-Key token refresh failed with HTTP "
                    f"{response.status_code}: {response.text[:512]}"
                ),
            )
        payload = self._safe_json(response, context="token refresh")
        try:
            access_token = payload["access_token"]
        except (KeyError, TypeError) as exc:
            raise MalformedResponseError(
                f"Digi-Key token refresh response missing 'access_token': {payload!r}"
            ) from exc
        new_refresh = payload.get("refresh_token", refresh_token)
        expires_in = int(payload.get("expires_in", _DEFAULT_EXPIRES_IN))
        token_type = payload.get("token_type", "Bearer")
        self.token_cache.save(
            access_token=str(access_token),
            refresh_token=str(new_refresh),
            expires_in=expires_in,
            token_type=str(token_type),
        )
        return str(access_token)

    # ------------------------------------------------------------------
    # Product endpoints
    # ------------------------------------------------------------------

    def get_product(self, mpn: str) -> dict[str, Any]:
        """Fetch a single product by manufacturer part number."""
        if not mpn or not isinstance(mpn, str):
            raise ValueError(f"get_product requires a non-empty MPN, got {mpn!r}")
        url = f"{self.base_url}/Search/v3/Products/{mpn}"
        response = self._request_with_refresh("GET", url)
        return self._safe_json(response, context=f"get_product({mpn!r})")

    def search(
        self,
        keywords: str,
        *,
        limit: int = 50,
        offset: int = 0,
        record_count: int | None = None,
    ) -> dict[str, Any]:
        """Keyword search across the Digi-Key catalogue.

        ``limit`` becomes ``RecordCount`` in the Digi-Key request body;
        Proteus hardcoded ``RecordCount=50`` and used the parameter
        for nothing — Heaviside actually honours it.
        """
        if not keywords or not isinstance(keywords, str):
            raise ValueError(f"search requires a non-empty keyword string, got {keywords!r}")
        body = {
            "Keywords": keywords,
            "RecordCount": record_count if record_count is not None else limit,
            "RecordStartPosition": offset,
            "Filters": {},
            "Sort": {
                "SortOption": "SortByDigiKeyPartNumber",
                "Direction": "Ascending",
            },
            "SearchOptions": ["ManufacturerPartSearch"],
            "ExcludeMarketPlaceProducts": True,
        }
        url = f"{self.base_url}/Search/v3/Products/Keyword"
        response = self._request_with_refresh("POST", url, json_body=body)
        payload = self._safe_json(response, context=f"search({keywords!r})")
        if "Products" not in payload:
            raise MalformedResponseError(
                "Digi-Key search response missing 'Products' key.  "
                f"Top-level keys: {sorted(payload.keys())!r}"
            )
        return payload

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _request_with_refresh(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Issue an authenticated request, refreshing once on 401."""
        access_token = self.get_access_token()
        response = self._send(method, url, access_token, json_body)
        if response.status_code == 401:
            # Force a refresh by invalidating the cache, then retry once.
            access_token = self._force_refresh()
            response = self._send(method, url, access_token, json_body)
        if response.status_code == 429:
            raise RateLimitError(
                "digikey",
                response.text,
                retry_after_seconds=_parse_retry_after(response.headers.get("Retry-After")),
            )
        if response.status_code >= 400:
            raise DistributorError("digikey", response.status_code, response.text)
        return response

    def _force_refresh(self) -> str:
        refresh = self.credentials.refresh_token
        cached = self.token_cache.load()
        if cached is not None:
            cached_refresh = cached.get("refresh_token")
            if isinstance(cached_refresh, str) and cached_refresh:
                refresh = cached_refresh
        if not refresh:
            raise MissingCredentialError(
                "Digi-Key returned 401 and no refresh token is available for a recovery refresh."
            )
        return self._refresh_token(refresh)

    def _send(
        self,
        method: str,
        url: str,
        access_token: str,
        json_body: dict[str, Any] | None,
    ) -> httpx.Response:
        headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            "X-DIGIKEY-Client-Id": self.credentials.client_id,
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            return self._client.request(method, url, headers=headers, json=json_body)
        return self._client.request(method, url, headers=headers)

    @staticmethod
    def _safe_json(response: httpx.Response, *, context: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise MalformedResponseError(
                f"Digi-Key {context} returned non-JSON body "
                f"({len(response.text)} bytes): {response.text[:256]!r}"
            ) from exc
        if not isinstance(payload, dict):
            raise MalformedResponseError(
                f"Digi-Key {context} returned non-object JSON: {type(payload).__name__}"
            )
        return payload
