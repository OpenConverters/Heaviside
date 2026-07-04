"""Resolve the fetchable datasheet-PDF URL for a Würth Elektronik magnetic part.

Why this exists
---------------
The internal DB's ``datasheetUrl`` for a WE magnetic is not reliably a fetchable
PDF:

* Most parts store ``.../components/products/datasheet/<article>.pdf`` — a direct
  PDF that resolves (HTTP 200, ``application/pdf``).
* ~1.7k store a REDEXPERT *spec page* ``.../redexpert/spec/<article>?ad`` — HTML,
  not a PDF.
* ~1.1k store a ``.../katalog/en/datasheet/<article>`` URL — HTML that 302s to the
  real PDF.
* ~700 store no URL at all.
* A handful of "R"-decimal parts (e.g. ``744383560R33`` = 0.33 µH) store a
  synthesized ``<article>.pdf`` URL that **404s** — WE never published that exact
  article's datasheet under that path.

Every one of those (except the genuine 404s) resolves to the same canonical PDF
if we key on the **exact article number**: ``.../datasheet/<article>.pdf``. That
holds for empty-URL, REDEXPERT-page, and katalog parts alike (verified against
we-online.com).

The hard rule — key on the EXACT article, never transform it
------------------------------------------------------------
``744383560R33`` (0.33 µH, "R" = decimal point) is a *different part* from
``74438356033`` (3.3 µH, "033" = inductance code). Rewriting ``R33`` → ``033`` to
dodge the 404 would fetch a datasheet with 10× the inductance — a silent
data-corruption trap. This module therefore only ever builds URLs from the
verbatim article number. If no candidate resolves to a real PDF, the caller must
**skip and report** the part — never invent a URL or a value.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from heaviside.librarian.datasheet.base import DatasheetDownloadError

__all__ = [
    "resolve_we_datasheet_pdf",
    "we_datasheet_candidate_urls",
]

# Canonical WE datasheet template, keyed on the exact article number.
_DATASHEET_TMPL = "https://www.we-online.com/components/products/datasheet/{ref}.pdf"


def we_datasheet_candidate_urls(
    reference: str | None,
    datasheet_url: str | None = None,
) -> list[str]:
    """Ordered, de-duplicated candidate PDF URLs to try for one WE article.

    Order (first hit wins in :func:`resolve_we_datasheet_pdf`):

    1. A stored ``datasheetUrl`` that already ends in ``.pdf`` — authoritative
       when the DB has a real PDF link.
    2. The canonical synthesized ``.../datasheet/<article>.pdf`` — built from the
       **verbatim** article number (covers empty-URL / REDEXPERT-page parts).
    3. A stored non-PDF ``we-online.com`` URL (e.g. ``/katalog/...``) that
       302-redirects to the PDF — tried last as a redirect fallback.

    REDEXPERT *spec pages* (``/redexpert/spec/...``) are deliberately never
    returned as-is: they serve HTML, not a PDF. The article number is never
    altered (see module docstring).
    """
    ref = (reference or "").strip()
    stored = (datasheet_url or "").strip()
    stored_low = stored.lower()

    cands: list[str] = []

    def _add(url: str) -> None:
        if url and url not in cands:
            cands.append(url)

    if stored_low.endswith(".pdf"):
        _add(stored)
    if ref:
        _add(_DATASHEET_TMPL.format(ref=ref))
    if (
        stored
        and not stored_low.endswith(".pdf")
        and "we-online.com" in stored_low
        and "/redexpert/" not in stored_low
    ):
        _add(stored)

    return cands


def _looks_like_pdf(path: Path) -> bool:
    """True iff the file begins with the ``%PDF-`` magic bytes."""
    try:
        with open(path, "rb") as fh:
            return fh.read(5) == b"%PDF-"
    except OSError:
        return False


def resolve_we_datasheet_pdf(
    cache,
    reference: str | None,
    datasheet_url: str | None = None,
) -> tuple[str, Path] | None:
    """Fetch the first candidate URL that serves a real PDF for this article.

    Returns ``(url, path)`` for the winning candidate, or ``None`` if no
    candidate resolves to a PDF (a genuinely un-fetchable part — the caller
    skips and reports it; nothing is fabricated).

    A candidate that returns HTTP≥400 (``DatasheetDownloadError``) or 200-but-not
    a PDF is discarded and the next is tried; a poisoned non-PDF cache entry is
    removed so a later run re-downloads cleanly.
    """
    for url in we_datasheet_candidate_urls(reference, datasheet_url):
        try:
            path = cache.fetch(url)
        except DatasheetDownloadError:
            continue
        if _looks_like_pdf(Path(path)):
            return url, Path(path)
        # 200 but not a PDF (e.g. an HTML page served without a 404): drop the
        # poisoned cache entry and try the next candidate.
        with contextlib.suppress(OSError):
            Path(path).unlink()
    return None
