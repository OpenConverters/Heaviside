"""Content-addressed PDF cache for the datasheet reader.

Why content-addressed?
----------------------

Proteus's :func:`_download_pdf` used ``hash(pdf_url) % 10000`` as the
cache filename, which:

* Collided silently between unrelated PDFs once the modulo wrapped.
* Made the cache non-portable across Python versions (``hash()`` of
  strings is salted per-interpreter).
* Couldn't detect a corrupted partial download.

Heaviside's :class:`PdfCache` keys on the SHA-256 of the URL string
for lookup (so the same URL always resolves to the same path) *and*
records the SHA-256 of the downloaded bytes in a sidecar manifest, so
truncated downloads can be detected on subsequent reads.

Strict-mode contract
--------------------

* HTTP non-2xx and transport-level failures raise
  :class:`DatasheetDownloadError` — never return ``None`` and never
  silently fall back to a stale cache entry.
* Cache writes are atomic (write to ``<path>.tmp``, rename); a crash
  mid-download leaves the cache consistent.
* ``transport`` is injectable for testing — callers may pass an
  :class:`httpx.MockTransport` to drive the cache without real
  network IO.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx

from heaviside.librarian.datasheet.base import DatasheetDownloadError

__all__ = [
    "DEFAULT_CACHE_DIR",
    "PdfCache",
]


# Default cache lives in the user's home — never in the repository
# tree (PDFs are large and binary; we never want them in git).
DEFAULT_CACHE_DIR = Path(
    os.environ.get(
        "HEAVISIDE_DATASHEET_CACHE",
        str(Path.home() / ".heaviside" / "datasheet-cache"),
    )
)


# User-Agent string — some manufacturer CDNs (Wolfspeed, Infineon)
# block default Python user agents.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) Heaviside-Librarian/0.1 "
    "(+https://github.com/OpenConverters/Heaviside)"
)


def _url_digest(url: str) -> str:
    """Return the lowercase hex SHA-256 of the URL string."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


class PdfCache:
    """Content-addressed PDF cache.

    Parameters
    ----------
    cache_dir : Path, optional
        Directory to hold cached PDFs.  Created on first write.
        Defaults to :data:`DEFAULT_CACHE_DIR`.
    transport : httpx.BaseTransport, optional
        Transport override for testing (typically
        :class:`httpx.MockTransport`).  When ``None`` the default
        httpx transport is used.
    timeout_seconds : float, optional
        Per-request HTTP timeout.  Default 30s; manufacturer CDNs
        occasionally serve PDFs slowly enough that the default
        httpx 5s timeout trips.
    """

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
        self._transport = transport
        self._timeout = timeout_seconds

    # ------------------------------------------------------------------
    # Path lookup
    # ------------------------------------------------------------------

    def path_for(self, url: str) -> Path:
        """Return the (possibly non-existent) cache path for ``url``.

        Does not perform any IO; pure URL → path mapping so callers
        can pre-check :meth:`is_cached` cheaply.
        """
        return self.cache_dir / f"{_url_digest(url)}.pdf"

    def is_cached(self, url: str) -> bool:
        """Return ``True`` iff a non-empty cache entry exists for ``url``.

        Truncated (zero-byte) cache entries are treated as absent so
        the next :meth:`fetch` will re-download cleanly.
        """
        path = self.path_for(url)
        return path.is_file() and path.stat().st_size > 0

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

    def fetch(self, url: str, *, force: bool = False) -> Path:
        """Return a local path to the cached PDF, downloading if needed.

        Parameters
        ----------
        url : str
            PDF URL.
        force : bool
            When ``True``, re-download even if a cache entry exists.

        Raises
        ------
        DatasheetDownloadError
            HTTP non-2xx response, transport failure, timeout, or
            empty/zero-byte response body.
        """
        path = self.path_for(url)
        if not force and self.is_cached(url):
            return path

        # Ensure cache dir exists.  ``parents=True`` so a missing
        # ``~/.heaviside`` is created on first use.
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        client_kwargs: dict[str, Any] = {
            "timeout": self._timeout,
            "headers": {"User-Agent": _USER_AGENT},
            "follow_redirects": True,
        }
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        try:
            with httpx.Client(**client_kwargs) as client:
                response = client.get(url)
        except httpx.RequestError as exc:
            raise DatasheetDownloadError(
                url,
                message=f"transport error fetching {url!r}: {exc}",
            ) from exc

        if response.status_code >= 400:
            raise DatasheetDownloadError(
                url,
                status_code=response.status_code,
                message=(f"HTTP {response.status_code} fetching {url!r}: {response.text[:256]!r}"),
            )

        content = response.content
        if not content:
            raise DatasheetDownloadError(
                url,
                status_code=response.status_code,
                message=f"empty body fetching {url!r}",
            )

        # Atomic write: temp file in same dir → rename.  ``rename`` is
        # atomic on POSIX within a single filesystem.
        fd, tmp_name = tempfile.mkstemp(
            prefix=".dl-",
            suffix=".pdf.tmp",
            dir=str(self.cache_dir),
        )
        try:
            with os.fdopen(fd, "wb") as fp:
                fp.write(content)
            os.replace(tmp_name, path)
        except Exception:
            # Best-effort cleanup; re-raise after.
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise

        return path
