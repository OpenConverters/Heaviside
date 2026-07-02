#!/usr/bin/env python3
"""Digi-Key OAuth2 authorization-code flow (token provisioning).

The librarian's :class:`DigiKeyClient` only ever performs the
*refresh-token* grant.  When the refresh token itself expires or is
revoked (HTTP 400 ``invalid_grant``), there is no way back without a
fresh authorization-code flow — which needs a human to approve the app
in a browser.  This script is that flow, referenced by
``heaviside/librarian/fetcher/auth.py`` but historically missing.

Two-step, copy-paste friendly (no local web server required):

    # 1. Print the consent URL.  Open it, sign in, approve.  Digi-Key
    #    redirects to <redirect-uri>?code=XXXX — copy that code (the
    #    page itself need not load; read it from the address bar).
    scripts/librarian_auth.py --redirect-uri https://localhost

    # 2. Exchange the code for tokens.  Writes the token cache and
    #    updates credentials.json's refresh_token in place.
    scripts/librarian_auth.py --redirect-uri https://localhost --code XXXX

Client id/secret come from ``~/.heaviside/credentials.json`` (or the
``HEAVISIDE_DIGIKEY_CLIENT_ID`` / ``HEAVISIDE_DIGIKEY_CLIENT_SECRET``
env vars), same precedence as the library.  The ``redirect_uri`` MUST
exactly match the callback registered for the app in the Digi-Key
developer portal, or Digi-Key rejects both steps.

Per workspace policy this throws loudly on any non-2xx response rather
than swallowing it — a broken auth must be visible, not papered over.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from heaviside.librarian.fetcher.auth import (
    CONFIG_DIR,
    CREDENTIALS_PATH,
    DigiKeyCredentials,
    TokenCache,
    load_credentials,
)
from heaviside.librarian.fetcher.digikey import (
    DIGIKEY_PROD_BASE,
    DIGIKEY_SANDBOX_BASE,
)


def _client() -> DigiKeyCredentials:
    creds = load_credentials(require_digikey=True)
    dk = creds.digikey
    if not dk or not dk.client_id or not dk.client_secret:
        sys.exit("No Digi-Key client_id/client_secret found (credentials.json / env).")
    return dk


def authorize_url(base: str, client_id: str, redirect_uri: str) -> str:
    q = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
        }
    )
    return f"{base}/v1/oauth2/authorize?{q}"


def exchange_code(
    base: str,
    dk: DigiKeyCredentials,
    code: str,
    redirect_uri: str,
) -> dict:
    resp = httpx.post(
        f"{base}/v1/oauth2/token",
        data={
            "code": code,
            "client_id": dk.client_id,
            "client_secret": dk.client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30.0,
    )
    if resp.status_code != 200:
        raise SystemExit(
            f"Digi-Key authorization-code exchange failed (HTTP {resp.status_code}): {resp.text}"
        )
    return resp.json()


def _persist_refresh_token(refresh_token: str) -> None:
    """Update credentials.json's digikey.refresh_token in place (0o600)."""
    if not CREDENTIALS_PATH.exists():
        return
    data = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    data.setdefault("digikey", {})["refresh_token"] = refresh_token
    tmp = CREDENTIALS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    import os

    if os.name == "posix":
        os.chmod(tmp, 0o600)
    os.replace(tmp, CREDENTIALS_PATH)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--redirect-uri",
        required=True,
        help="Callback registered for the app in the Digi-Key portal "
        "(e.g. https://localhost). Must match exactly.",
    )
    ap.add_argument(
        "--code",
        help="Authorization code copied from the redirect URL. "
        "Omit to print the consent URL (step 1).",
    )
    ap.add_argument(
        "--sandbox",
        action="store_true",
        help="Use the Digi-Key sandbox host instead of production.",
    )
    args = ap.parse_args()

    base = DIGIKEY_SANDBOX_BASE if args.sandbox else DIGIKEY_PROD_BASE
    dk = _client()

    if not args.code:
        url = authorize_url(base, dk.client_id, args.redirect_uri)
        print("\n1. Open this URL, sign in, and approve the app:\n")
        print(f"   {url}\n")
        print(
            "2. Digi-Key redirects to "
            f"{args.redirect_uri}?code=XXXX — copy the `code` value\n"
            "   and re-run with:  --code XXXX\n"
        )
        return 0

    payload = exchange_code(base, dk, args.code, args.redirect_uri)
    access = payload["access_token"]
    refresh = payload["refresh_token"]
    expires_in = int(payload.get("expires_in", 0))

    TokenCache().save(access, refresh, expires_in)
    _persist_refresh_token(refresh)

    print(f"OK: token cache written to {CONFIG_DIR / 'digikey-token.json'}")
    print(f"    refresh_token updated in {CREDENTIALS_PATH}")
    print(f"    access token valid for ~{expires_in}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
