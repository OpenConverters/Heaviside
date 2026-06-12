#!/usr/bin/env python3
"""Restore verified Vishay HV ceramic capacitor stubs to the main TAS DB.

Context
-------
TAS/data/capacitors.quarantine_stubs.ndjson contains ~3 118 Vishay ceramic
class-1 stubs whose ``part.partNumber == part.series`` triggered quarantine.
For these parts, the quarantine detection was a false-positive: the partNumber
IS the real orderable MPN (e.g. ``PD0070WH16133BH1``); the catalog source
just set ``series`` to the same full MPN string instead of the product-family
name.  We also need to fix the ``datasheetUrl`` (currently a search page).

Algorithm
---------
For every stub:
1.  Call the Vishay predictive search API to verify the MPN exists.
2.  If the API returns the exact MPN as a hit → real part confirmed.
3.  Fetch the product page to resolve doc_no → PDF datasheet URL.
4.  Patch: ``part.series`` = real series name; ``datasheetUrl`` = PDF URL.
5.  Run ``guard_component`` — must pass, else skip.
6.  Write restored row to capacitors.ndjson; leave not-found rows quarantined.

Idempotency: skips any MPN already present in the main DB.

Usage:
    .venv-web/bin/python -m scripts.enrichment.vishay_hv_ceramic_restore
    # or directly:
    PYTHONPATH=. .venv-web/bin/python scripts/enrichment/vishay_hv_ceramic_restore.py
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO = Path(__file__).resolve().parent.parent.parent
TAS_MAIN = REPO / "TAS" / "data" / "capacitors.ndjson"
TAS_QUARANTINE = REPO / "TAS" / "data" / "capacitors.quarantine_stubs.ndjson"

sys.path.insert(0, str(REPO))
from heaviside.librarian.guards import GuardRejectionError, guard_component

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_UA = {"User-Agent": "Mozilla/5.0"}
_DOC_URL_CACHE: dict[str | int, str | None] = {}

# Ceramic technologies that MAY be mislabeled (class-1 parts in a class-2 row)
_VISHAY_HV_CERAMIC_TECHS = {"ceramic-class-1", "ceramic-class-2", "Film Capacitor"}


def vishay_search(pn: str) -> list[dict]:
    """Return Vishay predictive-search hits for *pn* (3 max)."""
    url = (
        "https://www.vishay.com/api/search-predictive/"
        f"?searchChoice=part&query={urllib.parse.quote(pn)}"
    )
    req = urllib.request.Request(url, headers=_UA)
    resp = urllib.request.urlopen(req, timeout=6)
    data = json.loads(resp.read())
    return data.get("hits", [])


def get_datasheet_url(doc_no: str | int) -> str | None:
    """Resolve Vishay *doc_no* to a full PDF URL, caching the result."""
    key = str(doc_no)
    if key in _DOC_URL_CACHE:
        return _DOC_URL_CACHE[key]
    url = f"https://www.vishay.com/en/product/{doc_no}/"
    req = urllib.request.Request(url, headers=_UA)
    try:
        resp = urllib.request.urlopen(req, timeout=6)
        html = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        _DOC_URL_CACHE[key] = None
        return None
    # First PDF link under /docs/ is the primary datasheet
    m = re.search(r"/docs/\d+/[^\",\s<>]+\.pdf", html)
    result = ("https://www.vishay.com" + m.group(0)) if m else None
    _DOC_URL_CACHE[key] = result
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    # ---- load existing main-DB MPNs to detect duplicates ----
    existing_mpns: set[str] = set()
    with TAS_MAIN.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                pn = (
                    row.get("capacitor", {})
                    .get("manufacturerInfo", {})
                    .get("datasheetInfo", {})
                    .get("part", {})
                    .get("partNumber")
                )
                if pn:
                    existing_mpns.add(pn)
            except Exception:
                pass
    print(f"Main DB: {len(existing_mpns)} existing MPNs", flush=True)

    # ---- load quarantine stubs ----
    all_stubs: list[dict] = []
    with TAS_QUARANTINE.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                all_stubs.append(json.loads(line))

    ceramic_stubs = [
        r
        for r in all_stubs
        if (
            r.get("capacitor", {})
            .get("manufacturerInfo", {})
            .get("name")
            == "Vishay"
            and r["capacitor"]["manufacturerInfo"]
            .get("datasheetInfo", {})
            .get("part", {})
            .get("technology")
            in _VISHAY_HV_CERAMIC_TECHS
        )
    ]
    non_ceramic_stubs = [r for r in all_stubs if r not in ceramic_stubs]
    print(
        f"Quarantine: {len(all_stubs)} total, "
        f"{len(ceramic_stubs)} Vishay HV ceramic, "
        f"{len(non_ceramic_stubs)} other",
        flush=True,
    )

    # ---- process ceramic stubs ----
    restored: list[dict] = []
    quarantine_keep: list[dict] = []
    stats = {
        "already_in_main": 0,
        "api_not_found": 0,
        "no_ds_url": 0,
        "guard_failed": 0,
        "restored": 0,
    }

    for i, stub in enumerate(ceramic_stubs):
        pn = (
            stub["capacitor"]["manufacturerInfo"]
            .get("datasheetInfo", {})
            .get("part", {})
            .get("partNumber", "")
        )

        # Skip if already in main DB
        if pn in existing_mpns:
            stats["already_in_main"] += 1
            quarantine_keep.append(stub)
            continue

        # API verification
        try:
            hits = vishay_search(pn)
        except Exception as exc:
            print(f"  API error for {pn}: {exc}", flush=True)
            quarantine_keep.append(stub)
            stats["api_not_found"] += 1
            time.sleep(0.5)
            continue
        time.sleep(0.12)

        # Exact match check
        exact_hit = next(
            (h["_source"] for h in hits if h["_source"].get("mat_no") == pn), None
        )
        if exact_hit is None:
            quarantine_keep.append(stub)
            stats["api_not_found"] += 1
            continue

        doc_no = exact_hit.get("doc_no")
        series_name = exact_hit.get("p1001", "")

        # Resolve datasheet URL
        ds_url = get_datasheet_url(doc_no) if doc_no else None
        if doc_no:
            time.sleep(0.15)
        if ds_url is None:
            quarantine_keep.append(stub)
            stats["no_ds_url"] += 1
            continue

        # Build patched row (deep copy)
        updated = json.loads(json.dumps(stub))
        cap = updated["capacitor"]
        cap["manufacturerInfo"]["datasheetUrl"] = ds_url
        cap["manufacturerInfo"]["datasheetInfo"]["part"]["series"] = series_name
        # Also fix the 'reference' and 'family' fields if they mirror the MPN
        if cap["manufacturerInfo"].get("reference") == pn:
            cap["manufacturerInfo"]["reference"] = series_name
        if cap["manufacturerInfo"].get("family") == pn:
            cap["manufacturerInfo"]["family"] = series_name
        # Fix technology if mislabeled (Vishay HV ceramics are always class-1)
        current_tech = cap["manufacturerInfo"]["datasheetInfo"]["part"].get("technology")
        if current_tech != "ceramic-class-1":
            cap["manufacturerInfo"]["datasheetInfo"]["part"]["technology"] = "ceramic-class-1"

        # Guard check
        try:
            guard_component("capacitors", updated)
        except GuardRejectionError as exc:
            print(f"  guard failed for {pn}: {exc.reasons}", flush=True)
            quarantine_keep.append(stub)
            stats["guard_failed"] += 1
            continue

        restored.append(updated)
        existing_mpns.add(pn)
        stats["restored"] += 1

        if (i + 1) % 50 == 0:
            print(
                f"  Progress {i + 1}/{len(ceramic_stubs)}: "
                f"restored={stats['restored']} not_found={stats['api_not_found']} "
                f"no_url={stats['no_ds_url']}",
                flush=True,
            )

    print(
        f"\nCeramic restore stats: {stats}",
        flush=True,
    )

    if not restored:
        print("Nothing to restore — no changes written.", flush=True)
        return

    # ---- write restored rows to main DB ----
    with TAS_MAIN.open("a") as fh:
        for row in restored:
            fh.write(json.dumps(row) + "\n")
    print(f"Appended {len(restored)} rows to {TAS_MAIN}", flush=True)

    # ---- rewrite quarantine file (non-ceramic unchanged + unresolved ceramics) ----
    new_quarantine = non_ceramic_stubs + quarantine_keep
    with TAS_QUARANTINE.open("w") as fh:
        for row in new_quarantine:
            fh.write(json.dumps(row) + "\n")
    print(
        f"Rewrote quarantine: {len(new_quarantine)} rows "
        f"({len(quarantine_keep)} ceramic kept, {len(non_ceramic_stubs)} non-ceramic unchanged)",
        flush=True,
    )

    # ---- summary ----
    print("\n=== SUMMARY ===")
    print(f"  Restored to main DB:   {stats['restored']}")
    print(f"  Already in main DB:    {stats['already_in_main']}")
    print(f"  API not found:         {stats['api_not_found']}")
    print(f"  No datasheet URL:      {stats['no_ds_url']}")
    print(f"  Guard rejected:        {stats['guard_failed']}")
    print(f"  Left quarantined:      {len(quarantine_keep)}")
    print(f"  Non-ceramic unchanged: {len(non_ceramic_stubs)}")


if __name__ == "__main__":
    main()
