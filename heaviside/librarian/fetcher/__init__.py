"""TAS-librarian fetcher: distributor-API clients.

Strict-mode HTTP clients for Digi-Key (OAuth2 client-credentials +
refresh-token flow) and Mouser (API-key flow).  Per CLAUDE.md *"no
fallbacks, no defaults, no silent shortcuts — throw"*:

* Missing credentials raise :class:`MissingCredentialError` — there
  are **no hardcoded fallbacks**.  Both distributors require
  explicit configuration via environment variables or
  ``~/.heaviside/credentials.json``.
* HTTP non-2xx responses raise :class:`DistributorError` with the
  full response body in the message — no silent ``return None``.
* Rate-limit responses (HTTP 429) raise :class:`RateLimitError`
  with any ``Retry-After`` header surfaced — the caller decides
  whether to back off.
* Malformed JSON or missing required envelope fields raise
  :class:`MalformedResponseError`.

The Proteus equivalents (``scripts/librarian_tas.py`` lines 358–558)
swallowed every exception and returned tuples like ``(None, False)``,
which silently degraded to "Mouser fallback" or zero results without
informing the caller of the actual failure cause.  Heaviside refuses
that pattern — every failure surfaces with a specific exception type.
"""

from __future__ import annotations

from heaviside.librarian.fetcher.auth import (
    CredentialError,
    Credentials,
    DigiKeyCredentials,
    MissingCredentialError,
    MouserCredentials,
    TokenCache,
    load_credentials,
)
from heaviside.librarian.fetcher.base import (
    DistributorError,
    FetcherError,
    MalformedResponseError,
    RateLimitError,
)
from heaviside.librarian.fetcher.digikey import (
    DIGIKEY_PROD_BASE,
    DIGIKEY_SANDBOX_BASE,
    DigiKeyClient,
)
from heaviside.librarian.fetcher.mouser import (
    MOUSER_API_BASE,
    MouserClient,
)

__all__ = [
    "CredentialError",
    "Credentials",
    "DIGIKEY_PROD_BASE",
    "DIGIKEY_SANDBOX_BASE",
    "DigiKeyClient",
    "DigiKeyCredentials",
    "DistributorError",
    "FetcherError",
    "MOUSER_API_BASE",
    "MalformedResponseError",
    "MissingCredentialError",
    "MouserClient",
    "MouserCredentials",
    "RateLimitError",
    "TokenCache",
    "load_credentials",
]
