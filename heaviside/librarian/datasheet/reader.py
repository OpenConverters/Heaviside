"""High-level orchestrator: URL → strict-mode params dict.

Glues :class:`PdfCache` and :func:`extract_params` together so
callers can write::

    reader = DatasheetReader()
    params = reader.extract("https://example.com/ds.pdf", category="mosfets")

The same strict-mode contract applies as in the underlying modules:
download failures raise :class:`DatasheetDownloadError`, parse
failures raise :class:`DatasheetParseError`, missing required fields
raise :class:`IncompleteDatasheetError`.

This is the API surface the ``component-librarian`` agent calls
during the enrichment loop.  The auditor's repair-recipes (slice F,
forthcoming) will reference this entry point by name.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from heaviside.librarian.datasheet.cache import DEFAULT_CACHE_DIR, PdfCache
from heaviside.librarian.datasheet.extract import (
    extract_params,
    extract_required_params,
    extract_tables,
)


__all__ = ["DatasheetReader"]


class DatasheetReader:
    """URL → params orchestrator.

    Parameters
    ----------
    cache_dir : Path | str | None
        Override the PDF cache directory.  Defaults to
        :data:`heaviside.librarian.datasheet.cache.DEFAULT_CACHE_DIR`.
    transport : httpx.BaseTransport, optional
        Transport override for testing (typically
        :class:`httpx.MockTransport`).
    timeout_seconds : float
        Per-request HTTP timeout passed through to the cache.
    """

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 30.0,
        cache: PdfCache | None = None,
    ) -> None:
        if cache is not None:
            if cache_dir is not None or transport is not None:
                raise ValueError(
                    "pass either `cache` or `cache_dir`/`transport`, not both"
                )
            self.cache = cache
        else:
            self.cache = PdfCache(
                cache_dir=cache_dir,
                transport=transport,
                timeout_seconds=timeout_seconds,
            )

    # ------------------------------------------------------------------
    # Sparse extraction (returns whatever was found)
    # ------------------------------------------------------------------

    def extract(
        self,
        url: str,
        *,
        category: str,
        force_download: bool = False,
        require_section: bool = True,
    ) -> dict[str, float]:
        """Download (or load from cache), parse, and return params.

        Returns a possibly-sparse dict.  Does *not* raise on missing
        required fields — use :meth:`extract_required` for that.
        """
        pdf_path = self.cache.fetch(url, force=force_download)
        tables = extract_tables(pdf_path)
        return extract_params(
            tables, category=category, require_section=require_section,
        )

    # ------------------------------------------------------------------
    # Strict extraction (raises on any missing required field)
    # ------------------------------------------------------------------

    def extract_required(
        self,
        url: str,
        *,
        category: str,
        mpn: str,
        force_download: bool = False,
        require_section: bool = True,
    ) -> dict[str, float]:
        """Like :meth:`extract` but raises
        :class:`IncompleteDatasheetError` when a schema-required
        field is missing for ``category``.

        The ``mpn`` is required so the raised exception carries the
        same fields as the converter-layer
        :class:`IncompleteSourceError`, letting the librarian agent's
        repair-recipe loop treat distributor-side and datasheet-side
        gaps identically.
        """
        pdf_path = self.cache.fetch(url, force=force_download)
        tables = extract_tables(pdf_path)
        return extract_required_params(
            tables, category=category, mpn=mpn,
            require_section=require_section,
        )

    # ------------------------------------------------------------------
    # Path-based shortcut (skip cache entirely)
    # ------------------------------------------------------------------

    def extract_from_path(
        self,
        pdf_path: Path | str,
        *,
        category: str,
        require_section: bool = True,
    ) -> dict[str, float]:
        """Bypass the cache and read an already-local PDF directly.

        Useful for offline reprocessing of a corpus that has already
        been downloaded — the librarian agent's batch repair
        workflows use this to avoid re-hitting manufacturer CDNs.
        """
        tables = extract_tables(pdf_path)
        return extract_params(
            tables, category=category, require_section=require_section,
        )
