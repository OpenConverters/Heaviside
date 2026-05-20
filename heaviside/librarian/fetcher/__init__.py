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
    IncompleteSourceError,
    MalformedResponseError,
    RateLimitError,
)
from heaviside.librarian.fetcher.convert import (
    DIGIKEY_MOSFET_PARAM_MAP,
    convert_digikey_to_tas_mosfet,
    convert_mouser_to_tas_mosfet,
    parse_si_value,
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
from heaviside.librarian.fetcher.staging import (
    STAGING_DIR,
    StagedRecord,
    StagingError,
    apply_staged,
    list_staged,
    stage_fetch,
)

__all__ = [
    "CredentialError",
    "Credentials",
    "DIGIKEY_MOSFET_PARAM_MAP",
    "DIGIKEY_PROD_BASE",
    "DIGIKEY_SANDBOX_BASE",
    "DigiKeyClient",
    "DigiKeyCredentials",
    "DistributorError",
    "FetcherError",
    "IncompleteSourceError",
    "MOUSER_API_BASE",
    "MalformedResponseError",
    "MissingCredentialError",
    "MouserClient",
    "MouserCredentials",
    "RateLimitError",
    "STAGING_DIR",
    "StagedRecord",
    "StagingError",
    "TokenCache",
    "apply_staged",
    "convert_digikey_to_tas_mosfet",
    "convert_mouser_to_tas_mosfet",
    "list_staged",
    "load_credentials",
    "parse_si_value",
    "stage_fetch",
]
