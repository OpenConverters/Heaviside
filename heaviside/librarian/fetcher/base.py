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
    "DistributorError",
    "FetcherError",
    "IncompleteSourceError",
    "MalformedResponseError",
    "RateLimitError",
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
        text = message or (f"{distributor} API returned HTTP {status_code}: {truncated}")
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


class IncompleteSourceError(FetcherError):
    """Distributor returned a well-formed payload that lacks a
    schema-required field.

    The Digi-Key / Mouser parameter dictionaries omit fields
    silently — Proteus papered over the gap by defaulting the
    missing values to ``0.0`` via ``_parse_value`` and then
    appending the row anyway, polluting TAS with junk.  In strict
    mode the converter raises this error so the caller can either
    enrich the payload (via ``component-librarian`` datasheet
    parsing) or quarantine the part.

    Attributes
    ----------
    source : str
        ``"digikey"`` or ``"mouser"``.
    mpn : str
        Manufacturer part number that failed conversion.
    missing_field : str
        Dotted SAS/CAS/RAS path that was unfilled (e.g.
        ``"electrical.outputCapacitance"``).
    """

    def __init__(
        self,
        source: str,
        mpn: str,
        missing_field: str,
        *,
        detail: str | None = None,
    ) -> None:
        suffix = f" ({detail})" if detail else ""
        super().__init__(
            f"{source} payload for {mpn!r} is missing required field {missing_field!r}{suffix}"
        )
        self.source = source
        self.mpn = mpn
        self.missing_field = missing_field
