#!/usr/bin/env python3
"""Backfill missing required ``electrical`` fields on TAS mosfet records.

Sources, in order of preference per part:

1. **Digi-Key** Search v3 ``ManufacturerPartSearch`` (best coverage for
   Vishay / Infineon / TI). ~1000 calls/day quota.
2. **Mouser** ``SearchByPartNumber`` fallback for parts Digi-Key misses.

Only *missing* schema-required fields are written; existing values are
never overwritten (no silent clobbering). Values come straight from the
distributor's published parameters via the same extractors the converter
uses — no estimation, no fabrication. Parts neither source can supply are
recorded as ``notfound`` and left untouched.

Resumability & rate limits
---------------------------
A JSON checkpoint (``~/.heaviside/jobs/mosfet_backfill_checkpoint.json``)
records every part's outcome so the campaign resumes mid-stream across
the daily-quota windows. When Digi-Key's quota is (nearly) exhausted the
run stops issuing Digi-Key calls, finishes any Mouser-only work, flushes,
and exits with status ``rate_limited`` (exit code 2) so the orchestrator
can resume after the daily reset. Completion exits 0; any-still-pending
but quota-exhausted exits 2.

Usage::

    scripts/backfill_semiconductor_electrical.py --category mosfets
    scripts/backfill_semiconductor_electrical.py --category mosfets --limit 50 --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from heaviside.librarian.fetcher import (  # noqa: E402
    DigiKeyClient,
    MouserClient,
    load_credentials,
)
from heaviside.librarian.fetcher.convert import (  # noqa: E402
    _extract_gate_threshold,
    _extract_required_numeric,
)
from heaviside.librarian.fetcher.base import IncompleteSourceError  # noqa: E402

DATA = REPO / "TAS" / "data"
JOBS = Path.home() / ".heaviside" / "jobs"

# Schema-required electrical fields for a mosfet (SAS/mosfet.json $defs.electrical.required).
REQUIRED = [
    "drainSourceVoltage",
    "onResistance",
    "continuousDrainCurrent",
    "gateThresholdVoltage",
    "totalGateCharge",
]

# Candidate (parameter-name, unit) lists, merging Digi-Key + Mouser spellings.
# Superset of DIGIKEY_MOSFET_PARAM_MAP with the "(Max)" Qg variant the stock
# map omits (observed live on Infineon/Vishay parts).
CANDIDATES: dict[str, tuple[tuple[str, str], ...]] = {
    "drainSourceVoltage": (
        ("Drain to Source Voltage (Vdss)", "V"),
        ("Drain to Source Voltage Vdss", "V"),
    ),
    "onResistance": (
        ("Rds On (Max) @ Id, Vgs", "Ω"),
        ("Rds On (Max)", "Ω"),
    ),
    "continuousDrainCurrent": (
        ("Current - Continuous Drain (Id) @ 25°C", "A"),
        ("Current - Continuous Drain (Id) @ 25 C", "A"),
    ),
    "totalGateCharge": (
        ("Gate Charge (Qg) (Max) @ Vgs", "C"),
        ("Gate Charge (Qg) @ Vgs", "C"),
        ("Gate Charge (Qg)", "C"),
    ),
}

DK_KEYWORD_URL = "https://api.digikey.com/Search/v3/Products/Keyword"


def required_missing(electrical: dict) -> list[str]:
    return [f for f in REQUIRED if f not in electrical or electrical[f] in (None, "")]


def extract_fields(params: dict[str, str], want: list[str]) -> dict[str, Any]:
    """Pull only the wanted fields from a distributor param/attr dict.

    Returns {field: value} for whatever was present; silently skips a
    field the source doesn't publish (caught IncompleteSourceError).
    """
    out: dict[str, Any] = {}
    for field in want:
        try:
            if field == "gateThresholdVoltage":
                out[field] = _extract_gate_threshold(source="src", mpn="x", params=params)
            else:
                out[field] = _extract_required_numeric(
                    source="src", mpn="x", params=params, field=field,
                    candidates=CANDIDATES[field],
                )
        except IncompleteSourceError:
            continue
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
# Distributor lookups
# ---------------------------------------------------------------------------


class QuotaExhausted(Exception):
    pass


class TransientNetworkError(Exception):
    """A network call failed repeatedly; the part should be retried later,
    not marked notfound and not crash the campaign."""


def digikey_params(client: DigiKeyClient, token_holder: dict, mpn: str, margin: int) -> dict[str, str] | None:
    """Return Digi-Key parameter dict for the best MPN match, or None.

    Raises QuotaExhausted when the daily rate-limit budget is spent.
    """
    tok = client.get_access_token()
    creds = token_holder["creds"]
    hdr = {
        "Authorization": f"Bearer {tok}",
        "X-DIGIKEY-Client-Id": creds.digikey.client_id,
        "Content-Type": "application/json",
        "accept": "application/json",
    }
    body = {
        "Keywords": mpn,
        "RecordCount": 1,
        "RecordStartPosition": 0,
        "Filters": {},
        "SearchOptions": ["ManufacturerPartSearch"],
    }
    for attempt in range(6):
        try:
            r = httpx.post(DK_KEYWORD_URL, headers=hdr, json=body, timeout=40)
        except httpx.TransportError as exc:
            # Transient network blip (ConnectTimeout, ReadTimeout, etc.) —
            # never fatal to the campaign. Back off and retry; give up to None.
            if attempt >= 4:
                raise TransientNetworkError(str(exc)) from exc
            time.sleep(2 * (attempt + 1))
            continue
        remaining = r.headers.get("x-ratelimit-remaining")
        if remaining is not None:
            token_holder["dk_remaining"] = int(remaining)
        if r.status_code == 429:
            retry_after = r.headers.get("Retry-After")
            if retry_after and retry_after.isdigit() and int(retry_after) <= 120:
                time.sleep(int(retry_after) + 1)
                continue
            raise QuotaExhausted("Digi-Key 429")
        if r.status_code != 200:
            return None
        if remaining is not None and int(remaining) <= margin:
            # Use this response, but signal exhaustion to the caller next call.
            token_holder["dk_exhausted"] = True
        d = r.json()
        products = d.get("Products") or []
        if not products:
            return None
        raw = products[0].get("Parameters") or []
        params = {}
        for e in raw:
            if isinstance(e, dict) and isinstance(e.get("Parameter"), str) and isinstance(e.get("Value"), str):
                params[e["Parameter"]] = e["Value"]
        return params
    return None


def mouser_params(client: MouserClient, mpn: str) -> dict[str, str] | None:
    p = client.get_product(mpn)
    if not p:
        return None
    raw = p.get("ProductAttributes") or p.get("Attributes") or []
    attrs = {}
    for e in raw:
        if not isinstance(e, dict):
            continue
        name = e.get("AttributeName") or e.get("Name")
        val = e.get("AttributeValue") or e.get("Value")
        if isinstance(name, str) and isinstance(val, str):
            attrs[name] = val
    return attrs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--category", default="mosfets", choices=["mosfets"])
    ap.add_argument("--limit", type=int, default=0, help="cap parts processed this run (0=all)")
    ap.add_argument("--margin", type=int, default=10, help="stop Digi-Key when remaining<=margin")
    ap.add_argument("--flush-every", type=int, default=25)
    ap.add_argument("--no-mouser", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="don't write the ndjson")
    args = ap.parse_args()

    ndjson = DATA / f"{args.category}.ndjson"
    disc = {"mosfets": ("semiconductor", "mosfet")}[args.category]
    JOBS.mkdir(parents=True, exist_ok=True)
    ckpt_path = JOBS / f"{args.category}_backfill_checkpoint.json"
    checkpoint: dict[str, Any] = {}
    if ckpt_path.exists():
        checkpoint = json.loads(ckpt_path.read_text())

    # Load all records; identify failing ones.
    lines = ndjson.read_text().splitlines()
    failing: list[tuple[int, str, list[str]]] = []  # (idx, mpn, missing)
    # Distributor API coverage varies by manufacturer; process highest-yield
    # first so the daily quota lands on parts most likely to resolve. Pure
    # ordering — every failing part is still attempted.
    PRIORITY = {"Vishay": 0, "Texas Instruments": 1, "onsemi": 2,
                "ON Semiconductor": 2, "Infineon": 3}
    for idx, line in enumerate(lines):
        if not line.strip():
            continue
        rec = json.loads(line)
        mf = rec[disc[0]][disc[1]]
        mi = mf.get("manufacturerInfo", {})
        el = mi.get("datasheetInfo", {}).get("electrical", {})
        miss = required_missing(el)
        if miss:
            mpn = mi.get("datasheetInfo", {}).get("part", {}).get("partNumber")
            if mpn:
                man = mi.get("manufacturer") or mi.get("name") or ""
                failing.append((PRIORITY.get(man, 5), idx, mpn, miss))
    failing.sort(key=lambda t: t[0])
    failing = [(idx, mpn, miss) for _prio, idx, mpn, miss in failing]

    todo = [t for t in failing if checkpoint.get(t[1], {}).get("status") not in ("done", "notfound")]
    print(f"[{args.category}] {len(failing)} failing, {len(todo)} to attempt this run "
          f"({len(failing)-len(todo)} already resolved/exhausted in checkpoint)")

    creds = load_credentials()
    dk = DigiKeyClient(creds.digikey)
    holder = {"creds": creds, "dk_remaining": None, "dk_exhausted": False}
    mu = None if args.no_mouser else MouserClient(creds.mouser)

    stats = {"found_full": 0, "found_partial": 0, "notfound": 0, "error": 0, "deferred": 0}
    patches: dict[int, dict[str, Any]] = {}  # idx -> {field: value}
    dk_dead = False
    processed = 0

    def flush():
        if args.dry_run or not patches:
            ckpt_path.write_text(json.dumps(checkpoint, indent=1))
            return
        for idx, fields in patches.items():
            rec = json.loads(lines[idx])
            el = rec[disc[0]][disc[1]].setdefault("manufacturerInfo", {}).setdefault(
                "datasheetInfo", {}).setdefault("electrical", {})
            for k, v in fields.items():
                if k not in el or el[k] in (None, ""):
                    el[k] = v
            lines[idx] = json.dumps(rec)
        tmp = ndjson.with_suffix(".ndjson.tmp")
        tmp.write_text("\n".join(lines) + "\n")
        tmp.replace(ndjson)
        patches.clear()
        ckpt_path.write_text(json.dumps(checkpoint, indent=1))

    try:
        for idx, mpn, miss in todo:
            if args.limit and processed >= args.limit:
                break
            processed += 1
            found: dict[str, Any] = {}
            transient = False
            dk_tried = False  # was Digi-Key actually consulted live for this part?
            # 1. Digi-Key
            if not dk_dead:
                try:
                    params = digikey_params(dk, holder, mpn, args.margin)
                    dk_tried = True
                    if params:
                        found.update(extract_fields(params, miss))
                except QuotaExhausted:
                    dk_dead = True
                    print(f"  ! Digi-Key quota exhausted at part {processed}; switching to Mouser-only")
                except TransientNetworkError as exc:
                    transient = True
                    print(f"  ~ transient network error on {mpn}: {exc} (will retry next run)")
                except Exception as exc:  # never let one part crash the campaign
                    transient = True
                    print(f"  ~ unexpected error on {mpn}: {type(exc).__name__}: {str(exc)[:80]}")
                if holder.get("dk_exhausted"):
                    dk_dead = True
            # 2. Mouser fallback for still-missing fields
            still = [f for f in miss if f not in found]
            if still and mu is not None:
                try:
                    attrs = mouser_params(mu, mpn)
                    if attrs:
                        found.update({k: v for k, v in extract_fields(attrs, still).items()})
                except Exception:
                    pass

            if not found and transient:
                # Don't poison the checkpoint with a network-blip 'notfound';
                # leave the part pending so the next run retries it.
                stats["error"] += 1
                checkpoint[mpn] = {"status": "error", "fields": [], "remaining": miss}
            elif not found and not dk_tried:
                # Digi-Key quota was dead, so this part only saw Mouser.
                # 'deferred' is retryable — the next daily window tries
                # Digi-Key. Marking it 'notfound' here would skip it forever.
                stats["deferred"] += 1
                checkpoint[mpn] = {"status": "deferred", "fields": [], "remaining": miss}
            elif found:
                patches[idx] = found
                got = set(found)
                status = "done" if not (set(miss) - got) else "partial"
                stats["found_full" if status == "done" else "found_partial"] += 1
                checkpoint[mpn] = {"status": status, "fields": sorted(got),
                                   "remaining": sorted(set(miss) - got)}
            else:
                stats["notfound"] += 1
                checkpoint[mpn] = {"status": "notfound", "fields": [], "remaining": miss}

            if processed % args.flush_every == 0:
                flush()
                print(f"  .. {processed}/{len(todo)} | dk_remaining={holder.get('dk_remaining')} | {stats}")

            if dk_dead and (mu is None):
                print("  ! Both sources exhausted; stopping.")
                break
    finally:
        flush()
        dk.close()
        if mu is not None:
            mu.close()

    print(f"\n[{args.category}] done this run: processed={processed} {stats} "
          f"dk_remaining={holder.get('dk_remaining')}")
    # Determine remaining work for orchestration.
    still_pending = [t for t in failing if checkpoint.get(t[1], {}).get("status") not in ("done", "notfound")]
    print(f"[{args.category}] still pending after run: {len(still_pending)}")
    if still_pending and (dk_dead or (args.limit and processed >= args.limit)):
        return 2  # rate-limited / capped — resume later
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
