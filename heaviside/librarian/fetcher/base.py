"""Shared exception hierarchy for the fetcher layer.

Kept in its own module so :mod:`heaviside.librarian.fetcher.auth`,
:mod:`heaviside.librarian.fetcher.digikey`, and
:mod:`heaviside.librarian.fetcher.mouser` can import without a
circular dependency.

All fetcher exceptions descend from
:class:`heaviside.librarian.safe_access.LibrarianError` so callers
that already broadly catch librarian failures see fetcher failures
too.
"""

from __future__ import annotations

from heaviside.librarian.safe_access import LibrarianError


__all__ = [
    "FetcherError",
    "DistributorError",
    "RateLimitError",
    "MalformedResponseError",
]


class FetcherError(LibrarianError):
    """Base class for every fetcher-layer failure."""


class DistributorError(FetcherError):
    """Distributor API returned a non-success HTTP status.

    Attributes
    ----------
    distributor : str
        ``"digikey"`` or ``"mouser"``.
    status_code : int
        HTTP status that triggered the error.
    body : str
        Truncated response body for diagnostics.
    """

    def __init__(
        self,
        distributor: str,
        status_code: int,
        body: str,
        *,
        message: str | None = None,
    ) -> None:
        truncated = body[:1024] + ("..." if len(body) > 1024 else "")
        text = message or (
            f"{distributor} API returned HTTP {status_code}: {truncated}"
        )
        super().__init__(text)
        self.distributor = distributor
        self.status_code = status_code
        self.body = body


class RateLimitError(DistributorError):
    """Distributor API returned HTTP 429.

    The optional :attr:`retry_after_seconds` mirrors the
    ``Retry-After`` response header when present.  Callers may sleep
    that long before retrying; the fetcher itself never retries
    silently (strict mode).
    """

    def __init__(
        self,
        distributor: str,
        body: str,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        message = (
            f"{distributor} API rate-limited (HTTP 429). "
            f"Retry-After: {retry_after_seconds!r} seconds."
        )
        super().__init__(distributor, 429, body, message=message)
        self.retry_after_seconds = retry_after_seconds


class MalformedResponseError(FetcherError):
    """Distributor API returned HTTP 2xx but the body was not the expected shape.

    Distinct from :class:`DistributorError` so the caller can tell
    a transport failure (bad credentials, 5xx, rate-limit) apart
    from a contract violation (e.g. Digi-Key search returned no
    ``Products`` key).
    """
