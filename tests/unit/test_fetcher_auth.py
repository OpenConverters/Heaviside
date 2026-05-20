"""Tests for ``heaviside.librarian.fetcher.auth``.

Covers credential resolution precedence (env > file > missing),
file-permission enforcement on POSIX, malformed credential files,
and :class:`TokenCache` load/save/is_fresh semantics including
the 60-second expiry skew.
"""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

import pytest

from heaviside.librarian.fetcher import auth as auth_mod
from heaviside.librarian.fetcher.auth import (
    CredentialError,
    MissingCredentialError,
    TokenCache,
    load_credentials,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_creds(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)


# ---------------------------------------------------------------------------
# load_credentials — env precedence
# ---------------------------------------------------------------------------


def test_load_credentials_env_only(tmp_path: Path) -> None:
    creds = load_credentials(
        env={
            "HEAVISIDE_DIGIKEY_CLIENT_ID": "env-cid",
            "HEAVISIDE_DIGIKEY_CLIENT_SECRET": "env-secret",
            "HEAVISIDE_DIGIKEY_REFRESH_TOKEN": "env-refresh",
            "HEAVISIDE_MOUSER_API_KEY": "env-mouser",
        },
        credentials_path=tmp_path / "missing.json",
    )
    assert creds.digikey is not None
    assert creds.digikey.client_id == "env-cid"
    assert creds.digikey.client_secret == "env-secret"
    assert creds.digikey.refresh_token == "env-refresh"
    assert creds.mouser is not None
    assert creds.mouser.api_key == "env-mouser"


def test_load_credentials_file_only(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    _write_creds(
        path,
        {
            "digikey": {
                "client_id": "file-cid",
                "client_secret": "file-secret",
                "refresh_token": "file-refresh",
            },
            "mouser": {"api_key": "file-mouser"},
        },
    )
    creds = load_credentials(env={}, credentials_path=path)
    assert creds.digikey is not None
    assert creds.digikey.client_id == "file-cid"
    assert creds.digikey.refresh_token == "file-refresh"
    assert creds.mouser is not None
    assert creds.mouser.api_key == "file-mouser"


def test_env_overrides_file(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    _write_creds(
        path,
        {
            "digikey": {
                "client_id": "file-cid",
                "client_secret": "file-secret",
                "refresh_token": "file-refresh",
            },
            "mouser": {"api_key": "file-mouser"},
        },
    )
    creds = load_credentials(
        env={
            "HEAVISIDE_DIGIKEY_CLIENT_ID": "env-cid",
            "HEAVISIDE_DIGIKEY_CLIENT_SECRET": "env-secret",
        },
        credentials_path=path,
    )
    assert creds.digikey is not None
    assert creds.digikey.client_id == "env-cid"
    assert creds.digikey.client_secret == "env-secret"
    # refresh_token still comes from file since env did not set it.
    assert creds.digikey.refresh_token == "file-refresh"


# ---------------------------------------------------------------------------
# load_credentials — missing / partial
# ---------------------------------------------------------------------------


def test_no_credentials_anywhere_returns_empty(tmp_path: Path) -> None:
    creds = load_credentials(env={}, credentials_path=tmp_path / "missing.json")
    assert creds.digikey is None
    assert creds.mouser is None


def test_require_digikey_raises_when_missing(tmp_path: Path) -> None:
    with pytest.raises(MissingCredentialError, match="Digi-Key credentials required"):
        load_credentials(
            env={},
            credentials_path=tmp_path / "missing.json",
            require_digikey=True,
        )


def test_require_mouser_raises_when_missing(tmp_path: Path) -> None:
    with pytest.raises(MissingCredentialError, match="Mouser credentials required"):
        load_credentials(
            env={},
            credentials_path=tmp_path / "missing.json",
            require_mouser=True,
        )


def test_digikey_partial_env_raises(tmp_path: Path) -> None:
    """client_id without client_secret is a hard error, never a silent partial."""
    with pytest.raises(MissingCredentialError, match="incomplete"):
        load_credentials(
            env={"HEAVISIDE_DIGIKEY_CLIENT_ID": "only-id"},
            credentials_path=tmp_path / "missing.json",
        )


# ---------------------------------------------------------------------------
# Credentials file integrity
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="POSIX file-mode check")
def test_credentials_file_rejected_when_world_readable(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    _write_creds(path, {"mouser": {"api_key": "k"}})
    os.chmod(path, 0o644)  # widen permissions to "world-readable"
    with pytest.raises(CredentialError, match="permissions"):
        load_credentials(env={}, credentials_path=path)


def test_credentials_file_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    path.write_text("{not json", encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)
    with pytest.raises(CredentialError, match="invalid JSON"):
        load_credentials(env={}, credentials_path=path)


def test_credentials_file_non_object(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)
    with pytest.raises(CredentialError, match="must be an object"):
        load_credentials(env={}, credentials_path=path)


def test_credentials_file_digikey_section_wrong_type(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    _write_creds(path, {"digikey": "wrong"})
    with pytest.raises(CredentialError, match="'digikey' key must be an object"):
        load_credentials(env={}, credentials_path=path)


# ---------------------------------------------------------------------------
# No hardcoded fallback
# ---------------------------------------------------------------------------


def test_no_hardcoded_proteus_credentials_leaked() -> None:
    """The Proteus client_id/secret must not appear anywhere in the module."""
    source = Path(auth_mod.__file__).read_text(encoding="utf-8")
    # Substrings from Proteus's hardcoded constants.
    assert "cN8i6L6KnNGJB2h3zsQgC7KvWf8AccsC" not in source
    assert "8QpIINW6VK9loIeF" not in source


# ---------------------------------------------------------------------------
# TokenCache
# ---------------------------------------------------------------------------


def test_token_cache_load_returns_none_when_absent(tmp_path: Path) -> None:
    cache = TokenCache(path=tmp_path / "tok.json")
    assert cache.load() is None


def test_token_cache_save_roundtrip(tmp_path: Path) -> None:
    cache = TokenCache(path=tmp_path / "tok.json")
    cache.save(
        access_token="abc",
        refresh_token="ref",
        expires_in=1800,
    )
    payload = cache.load()
    assert payload is not None
    assert payload["access_token"] == "abc"
    assert payload["refresh_token"] == "ref"
    assert payload["expires_in"] == 1800
    assert payload["token_type"] == "Bearer"
    # expires_at is wall-clock now+1800.
    assert payload["expires_at"] == pytest.approx(time.time() + 1800, abs=5.0)


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits")
def test_token_cache_save_writes_0600(tmp_path: Path) -> None:
    cache = TokenCache(path=tmp_path / "tok.json")
    cache.save(access_token="a", refresh_token="r", expires_in=10)
    mode = cache.path.stat().st_mode & 0o777
    # No group / other bits set.
    assert not mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH)


def test_token_cache_is_fresh_within_skew(tmp_path: Path) -> None:
    cache = TokenCache(path=tmp_path / "tok.json")
    payload = {
        "access_token": "abc",
        "expires_at": time.time() + 120.0,  # 2 minutes from now
    }
    assert cache.is_fresh(payload) is True


def test_token_cache_is_fresh_inside_skew_returns_false(tmp_path: Path) -> None:
    cache = TokenCache(path=tmp_path / "tok.json")
    # 30 s out, default skew is 60 s → treat as expired.
    payload = {"access_token": "abc", "expires_at": time.time() + 30.0}
    assert cache.is_fresh(payload) is False


def test_token_cache_is_fresh_missing_fields(tmp_path: Path) -> None:
    cache = TokenCache(path=tmp_path / "tok.json")
    assert cache.is_fresh({}) is False
    assert cache.is_fresh({"access_token": "abc"}) is False
    assert cache.is_fresh({"expires_at": time.time() + 1000}) is False


def test_token_cache_corrupt_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "tok.json"
    path.write_text("not-json", encoding="utf-8")
    cache = TokenCache(path=path)
    with pytest.raises(CredentialError, match="corrupt token cache"):
        cache.load()


def test_token_cache_non_object_raises(tmp_path: Path) -> None:
    path = tmp_path / "tok.json"
    path.write_text('"a-string"', encoding="utf-8")
    cache = TokenCache(path=path)
    with pytest.raises(CredentialError, match="must be a JSON object"):
        cache.load()


def test_token_cache_save_is_atomic(tmp_path: Path) -> None:
    """A successful save leaves no ``*.tmp`` debris behind."""
    cache = TokenCache(path=tmp_path / "tok.json")
    cache.save(access_token="a", refresh_token="r", expires_in=10)
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []
