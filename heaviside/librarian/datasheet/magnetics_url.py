"""Resolve a fetchable datasheet-PDF URL for a *non-Würth* power inductor.

Companion to :mod:`heaviside.librarian.datasheet.magnetics_we_url` (which owns
Würth). Given ``(manufacturer, mpn)`` this builds candidate PDF URLs from each
vendor's known pattern and returns the first that VERIFIES as a real PDF (``%PDF-``
magic bytes) through the :class:`PdfCache`. It returns ``None`` when no candidate
resolves — the caller then reports the original as un-enriched rather than
inventing a URL or a value.

Which vendors have a derivable pattern (validated July 2026)
------------------------------------------------------------
* **MPS / Monolithic Power** — DERIVABLE, keyed on the *exact* SKU::

      https://www.monolithicpower.com/en/documentview/productdocument/index/
        version/2/document_type/Datasheet/lang/en/sku/{MPN}/

  (validated: ``.../sku/MPL-AL6050-1R5/`` → 542 KB ``%PDF``).
* **Bourns** — DERIVABLE, one PDF per *series*::

      https://www.bourns.com/docs/Product-Datasheets/{SERIES}.pdf

  where ``SERIES`` is the MPN up to the first dash (``SRP1245A-2R2M`` →
  ``SRP1245A``; validated: ``.../SRP1245A.pdf`` → 186 KB ``%PDF``). The dash
  suffix is the inductance/tolerance code, not part of the datasheet key — this
  is series-keying, NOT a value-changing MPN rewrite.

Which vendors are NOT derivable (return ``None`` — no guessing)
---------------------------------------------------------------
* **Coilcraft** — the PDF lives at ``/getmedia/<GUID>/<series>.pdf`` and the GUID
  is opaque (must be scraped from the product page). No template.
* **Vishay IHLP** — the PDF lives at ``/docs/<docnumber>/<slug>.pdf`` and the
  docnumber is an internal Vishay id, not derivable from the MPN.
* **TDK** — one catalog PDF per series at
  ``/info/en/catalog/datasheets/<taxonomy>_<series>_en.pdf``; the taxonomy prefix
  is not derivable from the MPN (and product.tdk.com IP-blocks datacenters).

For these three the datasheet URL must come from a distributor payload / product
search elsewhere; this resolver honestly reports ``None`` rather than fabricate
one. The **exact MPN is never transformed** (the Würth ``R33`` vs ``033`` trap).
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path

from heaviside.librarian.datasheet.base import DatasheetDownloadError
from heaviside.librarian.datasheet.cache import PdfCache

__all__ = [
    "datasheet_candidate_urls",
    "normalize_manufacturer",
    "resolve_datasheet_pdf_url",
]


_MPS_TMPL = (
    "https://www.monolithicpower.com/en/documentview/productdocument/index/"
    "version/2/document_type/Datasheet/lang/en/sku/{mpn}/"
)
_BOURNS_TMPL = "https://www.bourns.com/docs/Product-Datasheets/{series}.pdf"


def normalize_manufacturer(manufacturer: str) -> str | None:
    """Map a manufacturer name to a canonical vendor tag (or ``None``).

    Accepts the many spellings a distributor payload carries ("Monolithic Power
    Systems", "MPS", "Vishay Dale", "Bourns Inc.", …).
    """
    m = (manufacturer or "").strip().lower()
    if not m:
        return None
    if "monolithic" in m or m == "mps" or m.startswith("mps "):
        return "mps"
    if "bourns" in m:
        return "bourns"
    if "coilcraft" in m:
        return "coilcraft"
    if "vishay" in m:
        return "vishay"
    if "tdk" in m:
        return "tdk"
    if "würth" in m or "wurth" in m or "wuerth" in m:
        return "wurth"
    if "taiyo" in m:
        return "taiyo_yuden"
    return None


def _bourns_series(mpn: str) -> str:
    """Return the Bourns datasheet series key = MPN up to the first separator.

    ``SRP1245A-2R2M`` → ``SRP1245A``. The suffix is the inductance/tolerance code,
    not part of the per-series datasheet key. The alphanumeric series (incl. any
    trailing letter, e.g. the ``A`` in ``SRP1245A``) is kept verbatim.
    """
    return re.split(r"[-_ ]", mpn.strip())[0]


def datasheet_candidate_urls(manufacturer: str, mpn: str) -> list[str]:
    """Ordered candidate PDF URLs for ``(manufacturer, mpn)`` — pure, no IO.

    Empty for vendors with no derivable pattern (Coilcraft, Vishay, TDK) and for
    Würth (use :mod:`magnetics_we_url`). The exact MPN is never transformed; for
    Bourns only the trailing value/tolerance code is dropped to form the series
    key.
    """
    ref = (mpn or "").strip()
    if not ref:
        return []
    vendor = normalize_manufacturer(manufacturer)
    if vendor == "mps":
        return [_MPS_TMPL.format(mpn=ref)]
    if vendor == "bourns":
        series = _bourns_series(ref)
        return [_BOURNS_TMPL.format(series=series)] if series else []
    # coilcraft / vishay / tdk / wurth / unknown → no derivable template.
    return []


def _looks_like_pdf(path: Path) -> bool:
    """True iff the file begins with the ``%PDF-`` magic bytes."""
    try:
        with open(path, "rb") as fh:
            return fh.read(5) == b"%PDF-"
    except OSError:
        return False


def resolve_datasheet_pdf_url(
    manufacturer: str,
    mpn: str,
    *,
    cache: PdfCache | None = None,
) -> str | None:
    """Return a VERIFIED datasheet-PDF URL for ``(manufacturer, mpn)``, or ``None``.

    Builds the vendor's candidate URL(s) (see :func:`datasheet_candidate_urls`),
    fetches each through ``cache``, and returns the first whose bytes are a real
    PDF. Returns ``None`` when the vendor has no derivable pattern, or every
    candidate 404s / serves HTML (e.g. a bot-gate) — never a guessed URL.

    Parameters
    ----------
    manufacturer, mpn : str
        The original's manufacturer name and part number.
    cache : PdfCache, optional
        PDF cache used to fetch + verify. Defaults to a fresh :class:`PdfCache`.
        A non-PDF (HTML) 200 response is discarded and its poisoned cache entry
        removed, so a later run re-downloads cleanly.
    """
    pdf_cache = cache if cache is not None else PdfCache()
    for url in datasheet_candidate_urls(manufacturer, mpn):
        try:
            path = pdf_cache.fetch(url)
        except DatasheetDownloadError:
            continue
        if _looks_like_pdf(Path(path)):
            return url
        with contextlib.suppress(OSError):
            Path(path).unlink()
    return None
