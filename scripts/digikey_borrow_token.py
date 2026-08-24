#!/usr/bin/env python3
"""Borrow a short-lived Digi-Key ACCESS token from prod (ABT #875).

We have exactly ONE Digi-Key application. Digi-Key rotates the refresh token on
every refresh and invalidates the previous one, so two machines holding the same
refresh token silently kill each other: whichever refreshed last works, the other
gets ``401 Invalid RefreshToken`` and stays dead until a human re-seeds it. That
is what took prod's librarian offline from 2026-07-03 until 2026-08-24 — every
unknown original silently unsourceable, on the user-facing service.

The fix that works with one key: **prod owns the refresh token and is the only
host that ever rotates it.** A developer machine borrows the ACCESS token prod
already holds. Access tokens are opaque bearer strings with a ~30 minute life and
rotating them is not a thing — borrowing one costs prod nothing and cannot break
its chain.

    python scripts/digikey_borrow_token.py            # borrow, ~30 min of access
    python scripts/digikey_borrow_token.py --status   # who holds what, no changes

The borrowed cache is written WITHOUT a refresh token on purpose. When it
expires, the local client fails loudly ("No Digi-Key refresh token available")
instead of quietly rotating prod's token out from under it — run this again.

Never copy the refresh token itself to a second machine. If prod's chain really
is dead, re-seed prod from the Digi-Key developer portal, not from a laptop.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROD_HOST = os.environ.get("PROD_HOST", "root@51.15.253.66")
SSH_KEY = os.environ.get("SSH_KEY", str(Path.home() / ".ssh" / "om_scaleway"))
PROD_APP = os.environ.get("PROD_APP", "/home/alf/OpenConverters/Heaviside")
PROD_USER = os.environ.get("PROD_APP_USER", "alf")

LOCAL_CACHE = Path.home() / ".heaviside" / "digikey-token.json"

# Runs ON prod, as the service user, using prod's own credentials. Prints one
# JSON line. Prod refreshes (and rotates) exactly as it would for its own work.
_REMOTE = """
import json, sys
sys.path.insert(0, {app!r})
from heaviside.librarian.fetcher import DigiKeyClient, load_credentials
creds = load_credentials()
dk = DigiKeyClient(creds.digikey)
token = dk.get_access_token()
cached = dk.token_cache.load() or {{}}
print(json.dumps({{
    "access_token": token,
    "expires_at": cached.get("expires_at"),
    "token_type": cached.get("token_type", "Bearer"),
    # A flag, never the token: the refresh token must not leave prod.
    "has_refresh": bool(cached.get("refresh_token")),
}}))
"""


def _ssh(script: str) -> str:
    cmd = [
        "ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
        PROD_HOST, f"sudo -u {PROD_USER} {PROD_APP}/.venv/bin/python -",
    ]
    done = subprocess.run(cmd, input=script, capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise SystemExit(
            f"prod call failed (exit {done.returncode}):\n{done.stderr.strip()[:1000]}"
        )
    return done.stdout.strip()


def _describe(payload: dict | None) -> str:
    if not payload:
        return "no token cache"
    expires_at = payload.get("expires_at")
    holder = (
        "holds the refresh chain"
        if (payload.get("refresh_token") or payload.get("has_refresh"))
        else "borrowed access only"
    )
    if expires_at is None:
        return f"cache present, no expiry recorded, {holder}"
    left = float(expires_at) - time.time()
    if left <= 0:
        return f"access token EXPIRED {abs(left) / 60:.0f} min ago, {holder}"
    return f"access token valid {left / 60:.0f} more min, {holder}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--status", action="store_true", help="report only; change nothing")
    args = ap.parse_args()

    local = json.loads(LOCAL_CACHE.read_text()) if LOCAL_CACHE.exists() else None
    print(f"local  {LOCAL_CACHE}: {_describe(local)}")

    if args.status:
        remote_raw = _ssh(_REMOTE.format(app=PROD_APP))
        remote = json.loads(remote_raw.splitlines()[-1])
        print(f"prod   {PROD_HOST}: {_describe(remote)}")
        return 0

    print(f"borrowing an access token from {PROD_HOST} ...")
    remote = json.loads(_ssh(_REMOTE.format(app=PROD_APP)).splitlines()[-1])
    if not remote.get("access_token"):
        raise SystemExit("prod returned no access token")

    LOCAL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": remote["access_token"],
        # No refresh token, deliberately: see the module docstring.
        "expires_at": remote.get("expires_at") or (time.time() + 1500),
        "token_type": remote.get("token_type", "Bearer"),
        "_borrowed_from": PROD_HOST,
    }
    tmp = LOCAL_CACHE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, LOCAL_CACHE)
    print(f"local  {LOCAL_CACHE}: {_describe(payload)}")
    print("prod's refresh chain is untouched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
