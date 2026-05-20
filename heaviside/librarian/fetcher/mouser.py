"""Mouser Search API v1 client (strict mode).

Wraps the ``POST /api/v1/search/keyword`` endpoint and exposes a
single-product lookup built on top of it.  Mouser does not have a
dedicated ``GET /products/{mpn}`` route — the documented approach
is to keyword-search for the part and pick the row whose
``ManufacturerPartNumber`` matches exactly.

Strict-mode departures from Proteus
-----------------------------------

* The Mouser API key is mandatory.  No silent ``apiKey=None``.
* Every non-2xx response raises a typed exception
  (:class:`RateLimitError` for 429, :class:`DistributorError`
  otherwise).  Mouser also returns ``200 OK`` with a populated
  ``Errors`` array on application-level failures — those raise
  :class:`DistributorError` with ``status_code=200`` so callers can
  still pattern-match on the distributor name.
* The HTTP transport can be swapped via ``transport=`` so tests use
  :class:`httpx.MockTransport`.
"""

from __future__ import annotations

from typing import Any

import httpx

from heaviside.librarian.fetcher.auth import MouserCredentials
from heaviside.librarian.fetcher.base import (
    DistributorError,
    MalformedResponseError,
    RateLimitError,
)


__all__ = [
    "MOUSER_API_BASE",
    "MouserClient",
]


MOUSER_API_BASE = "https://api.mouser.com/api/v1"

_DEFAULT_TIMEOUT = 30.0


class MouserClient:
    """Strict-mode Mouser Search API v1 client."""

    def __init__(
        self,
        credentials: MouserCredentials,
        *,
        base_url: str = MOUSER_API_BASE,
        transport: httpx.BaseTransport | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        if not credentials.api_key:
            raise ValueError("MouserClient requires a non-empty api_key.")
        self.credentials = credentials
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(transport=transport, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> MouserClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(
        self,
        keywords: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Keyword search; returns the raw JSON envelope."""
        if not keywords or not isinstance(keywords, str):
            raise ValueError(
                f"search requires a non-empty keyword string, got {keywords!r}"
            )
        body = {
            "SearchByKeywordRequest": {
                "keyword": keywords,
                "records": limit,
                "startingRecord": offset,
            }
        }
        url = f"{self.base_url}/search/keyword"
        response = self._client.post(
            url,
            headers={
                "accept": "application/json",
                "Content-Type": "application/json",
            },
            params={"apiKey": self.credentials.api_key},
            json=body,
        )
        return self._handle(response, context=f"search({keywords!r})")

    def get_product(self, mpn: str) -> dict[str, Any] | None:
        """Return the search row whose MPN exactly matches ``mpn``, else ``None``.

        Strict-mode note: the "not found" case returns ``None`` because
        that is genuinely valid information ("Mouser does not stock this
        part"), not a hidden failure.  Transport-level problems still
        raise.
        """
        if not mpn or not isinstance(mpn, str):
            raise ValueError(f"get_product requires a non-empty MPN, got {mpn!r}")
        payload = self.search(mpn, limit=10)
        results = payload.get("SearchResults") or {}
        if not isinstance(results, dict):
            raise MalformedResponseError(
                f"Mouser search returned non-object 'SearchResults': "
                f"{type(results).__name__}"
            )
        parts = results.get("Parts") or []
        if not isinstance(parts, list):
            raise MalformedResponseError(
                f"Mouser search returned non-list 'Parts': {type(parts).__name__}"
            )
        target = mpn.upper()
        for part in parts:
            if not isinstance(part, dict):
                continue
            candidate = part.get("ManufacturerPartNumber", "")
            if isinstance(candidate, str) and candidate.upper() == target:
                return part
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _handle(self, response: httpx.Response, *, context: str) -> dict[str, Any]:
        if response.status_code == 429:
            raise RateLimitError(
                "mouser",
                response.text,
                retry_after_seconds=_parse_retry_after(
                    response.headers.get("Retry-After")
                ),
            )
        if response.status_code >= 400:
            raise DistributorError("mouser", response.status_code, response.text)
        try:
            payload = response.json()
        except ValueError as exc:
            raise MalformedResponseError(
                f"Mouser {context} returned non-JSON body "
                f"({len(response.text)} bytes): {response.text[:256]!r}"
            ) from exc
        if not isinstance(payload, dict):
            raise MalformedResponseError(
                f"Mouser {context} returned non-object JSON: "
                f"{type(payload).__name__}"
            )
        # Mouser surfaces application errors in a 200 body — promote them.
        errors = payload.get("Errors")
        if isinstance(errors, list) and errors:
            raise DistributorError(
                "mouser",
                response.status_code,
                response.text,
                message=(
                    f"Mouser {context} returned application errors: {errors!r}"
                ),
            )
        return payload


def _parse_retry_after(header: str | None) -> float | None:
    """Local copy to avoid a circular import with ``digikey`` for one helper."""
    if header is None:
        return None
    stripped = header.strip()
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError:
        return None
