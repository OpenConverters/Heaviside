"""Credential loading and Digi-Key OAuth2 token cache.

Strict precedence — no hardcoded fallbacks:

1. Environment variables (highest priority):

   * ``HEAVISIDE_DIGIKEY_CLIENT_ID``
   * ``HEAVISIDE_DIGIKEY_CLIENT_SECRET``
   * ``HEAVISIDE_DIGIKEY_REFRESH_TOKEN`` (optional; without it the
     first :meth:`DigiKeyClient.get_access_token` call requires a
     fresh authorization-code flow, which is out of scope for the
     library — provision tokens with ``scripts/librarian_auth.py``
     or equivalent).
   * ``HEAVISIDE_MOUSER_API_KEY``

2. Credentials file: ``~/.heaviside/credentials.json`` with shape::

       {
         "digikey": {
           "client_id": "...",
           "client_secret": "...",
           "refresh_token": "..."
         },
         "mouser": {"api_key": "..."}
       }

3. Missing → :class:`MissingCredentialError`.

The token cache lives at ``~/.heaviside/digikey-token.json`` and is
the single source of truth for the current access token + expiry.
Both files are user-only (mode ``0o600``); the library refuses to
read a credentials file with broader permissions on POSIX, matching
the standard `ssh-add` posture.
"""

from __future__ import annotations

import json
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from heaviside.librarian.fetcher.base import FetcherError

__all__ = [
    "CONFIG_DIR",
    "CREDENTIALS_PATH",
    "TOKEN_CACHE_PATH",
    "CredentialError",
    "Credentials",
    "DigiKeyCredentials",
    "MissingCredentialError",
    "MouserCredentials",
    "TokenCache",
    "load_credentials",
]


CONFIG_DIR: Path = Path(os.environ.get("HEAVISIDE_CONFIG_DIR", "") or Path.home() / ".heaviside")
CREDENTIALS_PATH: Path = CONFIG_DIR / "credentials.json"
TOKEN_CACHE_PATH: Path = CONFIG_DIR / "digikey-token.json"


class CredentialError(FetcherError):
    """Base for credential-loading failures (parent of
    :class:`MissingCredentialError`)."""


class MissingCredentialError(CredentialError):
    """A required credential is absent from both env and file."""


@dataclass(frozen=True)
class DigiKeyCredentials:
    client_id: str
    client_secret: str
    refresh_token: str | None = None


@dataclass(frozen=True)
class MouserCredentials:
    api_key: str


@dataclass(frozen=True)
class Credentials:
    """Container holding whichever distributor credentials were resolved."""

    digikey: DigiKeyCredentials | None = None
    mouser: MouserCredentials | None = None


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _read_credentials_file(path: Path) -> dict[str, Any]:
    """Read the user credentials file with a strict permission check."""
    if not path.exists():
        return {}
    if os.name == "posix":
        mode = path.stat().st_mode & 0o777
        # 0o600 is the only acceptable mode; widen-to-group/world is rejected.
        if mode & (stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH):
            raise CredentialError(
                f"{path} has permissions {oct(mode)} — credentials file "
                "must be mode 0600 (user read/write only).  Run: "
                f"chmod 600 {path}"
            )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CredentialError(f"{path}: invalid JSON: {exc.msg} (line {exc.lineno})") from exc
    if not isinstance(data, dict):
        raise CredentialError(
            f"{path}: top-level JSON must be an object, got {type(data).__name__}"
        )
    return data


def _digikey_from_sources(
    env: dict[str, str],
    file_data: dict[str, Any],
) -> DigiKeyCredentials | None:
    """Resolve Digi-Key credentials with env > file precedence."""
    file_dk = file_data.get("digikey") or {}
    if not isinstance(file_dk, dict):
        raise CredentialError(
            f"{CREDENTIALS_PATH}: 'digikey' key must be an object, got {type(file_dk).__name__}"
        )

    client_id = env.get("HEAVISIDE_DIGIKEY_CLIENT_ID") or file_dk.get("client_id")
    client_secret = env.get("HEAVISIDE_DIGIKEY_CLIENT_SECRET") or file_dk.get("client_secret")
    refresh = env.get("HEAVISIDE_DIGIKEY_REFRESH_TOKEN") or file_dk.get("refresh_token")

    # Neither configured → no Digi-Key credentials available.
    if client_id is None and client_secret is None:
        return None
    if not client_id or not client_secret:
        raise MissingCredentialError(
            "Digi-Key credentials are incomplete: client_id and "
            "client_secret are both required.  Set "
            "HEAVISIDE_DIGIKEY_CLIENT_ID / HEAVISIDE_DIGIKEY_CLIENT_SECRET "
            f"or populate {CREDENTIALS_PATH}.digikey.{{client_id,client_secret}}."
        )
    return DigiKeyCredentials(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh or None,
    )


def _mouser_from_sources(
    env: dict[str, str],
    file_data: dict[str, Any],
) -> MouserCredentials | None:
    file_m = file_data.get("mouser") or {}
    if not isinstance(file_m, dict):
        raise CredentialError(
            f"{CREDENTIALS_PATH}: 'mouser' key must be an object, got {type(file_m).__name__}"
        )
    api_key = env.get("HEAVISIDE_MOUSER_API_KEY") or file_m.get("api_key")
    if not api_key:
        return None
    return MouserCredentials(api_key=api_key)


def load_credentials(
    *,
    require_digikey: bool = False,
    require_mouser: bool = False,
    env: dict[str, str] | None = None,
    credentials_path: Path | None = None,
) -> Credentials:
    """Resolve credentials from env vars + on-disk file.

    Args:
        require_digikey: Raise :class:`MissingCredentialError` if no
            Digi-Key credentials are configured.
        require_mouser: Same, for Mouser.
        env: Override ``os.environ`` (test hook).
        credentials_path: Override :data:`CREDENTIALS_PATH` (test hook).

    Returns:
        A :class:`Credentials` container with ``.digikey`` and/or
        ``.mouser`` populated.  Either field is ``None`` if that
        distributor was not configured and not required.
    """
    real_env = env if env is not None else dict(os.environ)
    path = credentials_path if credentials_path is not None else CREDENTIALS_PATH
    file_data = _read_credentials_file(path)

    dk = _digikey_from_sources(real_env, file_data)
    mo = _mouser_from_sources(real_env, file_data)

    if require_digikey and dk is None:
        raise MissingCredentialError(
            "Digi-Key credentials required but not found.  Set "
            "HEAVISIDE_DIGIKEY_CLIENT_ID / HEAVISIDE_DIGIKEY_CLIENT_SECRET "
            f"or populate {path}.digikey."
        )
    if require_mouser and mo is None:
        raise MissingCredentialError(
            "Mouser credentials required but not found.  Set "
            f"HEAVISIDE_MOUSER_API_KEY or populate {path}.mouser.api_key."
        )

    return Credentials(digikey=dk, mouser=mo)


# ---------------------------------------------------------------------------
# Token cache (Digi-Key)
# ---------------------------------------------------------------------------


@dataclass
class TokenCache:
    """On-disk cache for the Digi-Key OAuth2 access token.

    Schema::

        {
          "access_token": "...",
          "refresh_token": "...",
          "expires_at": 1716235812.0,
          "token_type": "Bearer"
        }

    ``expires_at`` is a POSIX timestamp; the consumer treats the
    token as expired if ``time.time() >= expires_at - skew_seconds``
    (default skew 60 s, matching the Proteus behaviour).
    """

    path: Path = TOKEN_CACHE_PATH

    def load(self) -> dict[str, Any] | None:
        """Return the cached payload, or ``None`` if no cache exists."""
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CredentialError(f"{self.path}: corrupt token cache: {exc.msg}") from exc
        if not isinstance(data, dict):
            raise CredentialError(
                f"{self.path}: token cache must be a JSON object, got {type(data).__name__}"
            )
        return data

    def save(
        self,
        access_token: str,
        refresh_token: str,
        expires_in: int,
        *,
        token_type: str = "Bearer",
    ) -> None:
        """Atomically write a fresh token payload to disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": time.time() + float(expires_in),
            "expires_in": int(expires_in),
            "token_type": token_type,
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def is_fresh(self, payload: dict[str, Any], *, skew_seconds: float = 60.0) -> bool:
        """Whether the cached access token is still usable."""
        token = payload.get("access_token")
        expires_at = payload.get("expires_at")
        if not token or expires_at is None:
            return False
        return time.time() < float(expires_at) - skew_seconds
