"""Exceptions for the librarian datasheet reader.

Strict-mode contract — every failure surfaces as a typed exception
descended from :class:`heaviside.librarian.fetcher.FetcherError` so
the librarian agent can catch all enrichment failures uniformly.

The Proteus reader (``scripts/librarian_datasheet_reader.py``)
returned ``None`` from every failure path — download errors, missing
``pdfplumber``, malformed tables, unparseable values — without
distinguishing them.  That made it impossible to tell *why* a row
failed to enrich.  Heaviside refuses that pattern.
"""

from __future__ import annotations

from heaviside.librarian.fetcher.base import FetcherError, IncompleteSourceError

__all__ = [
    "DatasheetDownloadError",
    "DatasheetError",
    "DatasheetParseError",
    "IncompleteDatasheetError",
    "MissingDependencyError",
]


class DatasheetError(FetcherError):
    """Base class for every datasheet-reader failure."""


class DatasheetDownloadError(DatasheetError):
    """HTTP transport, timeout, or filesystem failure during PDF fetch.

    Attributes
    ----------
    url : str
        The PDF URL that failed.
    status_code : int | None
        HTTP status when the failure was an HTTP response; ``None``
        for transport-level failures (DNS, timeout, IO error).
    """

    def __init__(
        self,
        url: str,
        *,
        status_code: int | None = None,
        message: str | None = None,
    ) -> None:
        suffix = f" (HTTP {status_code})" if status_code is not None else ""
        text = message or f"failed to download datasheet {url!r}{suffix}"
        super().__init__(text)
        self.url = url
        self.status_code = status_code


class DatasheetParseError(DatasheetError):
    """``pdfplumber`` opened the PDF but no usable tables were
    extracted — either the PDF is image-only (scanned), encrypted, or
    its tables are sufficiently non-tabular that the extractor
    returned nothing.

    Distinct from :class:`IncompleteDatasheetError` so the caller can
    tell "we got no tables at all" apart from "we got tables but the
    field we wanted wasn't in them".
    """


class IncompleteDatasheetError(IncompleteSourceError):
    """The datasheet was successfully parsed but did not yield a
    required field for the requested category.

    Inherits :class:`IncompleteSourceError` so existing handlers that
    catch distributor-payload gaps catch datasheet gaps too —
    enrichment that fails for the same reason on both sides should
    look identical to the caller.

    ``source`` is set to ``"datasheet"`` to distinguish from the
    distributor payload sources (``"digikey"``, ``"mouser"``).
    """

    def __init__(
        self,
        mpn: str,
        missing_field: str,
        *,
        detail: str | None = None,
    ) -> None:
        super().__init__(
            "datasheet",
            mpn,
            missing_field,
            detail=detail,
        )


class MissingDependencyError(DatasheetError):
    """A runtime dependency required for PDF parsing is not installed.

    Heaviside lists ``pdfplumber`` as a required dependency in
    ``pyproject.toml``; this exception fires only in stripped-down
    environments (e.g. PyOM-only wheel builds) where the dependency
    was deliberately excluded.
    """

    def __init__(self, package: str, *, message: str | None = None) -> None:
        text = message or (
            f"required package {package!r} is not installed; install with `pip install {package}`"
        )
        super().__init__(text)
        self.package = package
