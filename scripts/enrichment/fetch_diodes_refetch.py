#!/usr/bin/env python
"""Real-diode refetch campaign (June 2026).

Replaces the synthetic coverage classes quarantined to
``diodes.quarantine_synthetic.ndjson`` with REAL parts fetched live
from the Digi-Key Product Information API and converted through the
canonical ``convert_digikey_to_tas_diode`` path.

Every accepted row:

* came from a live Digi-Key API response (no values invented here);
* passed ``guard_component('diodes', row)`` (schema + integrity
  patterns) BEFORE being written — a guard failure ABORTS the run;
* is recorded in ``scripts/enrichment/diodes_refetch_provenance.ndjson``
  with its source URL and the search that found it.

Usage:
    PYTHONPATH=. .venv-web/bin/python scripts/enrichment/fetch_diodes_refetch.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from heaviside.librarian.fetcher.auth import load_credentials
from heaviside.librarian.fetcher.base import IncompleteSourceError
from heaviside.librarian.fetcher.convert import (
    convert_digikey_to_tas_diode,
    detect_category,
)
from heaviside.librarian.fetcher.digikey import DigiKeyClient
from heaviside.librarian.guards import guard_component

REPO = Path(__file__).resolve().parents[2]
DB_PATH = REPO / "TAS" / "data" / "diodes.ndjson"
PROVENANCE_PATH = REPO / "scripts" / "enrichment" / "diodes_refetch_provenance.ndjson"

DIGIKEY_PRODUCT_URL = "https://www.digikey.com/en/products/result?keywords={mpn}"


# ---------------------------------------------------------------------------
# Campaign definition: (class name, search keywords, classifier, cap)
# ---------------------------------------------------------------------------


def _vr(row: dict[str, Any]) -> float:
    return float(
        row["semiconductor"]["diode"]["manufacturerInfo"]["datasheetInfo"]["electrical"][
            "reverseVoltage"
        ]
    )


def _subtype(row: dict[str, Any]) -> str:
    return str(
        row["semiconductor"]["diode"]["manufacturerInfo"]["datasheetInfo"]["part"]["subType"]
    )


CAMPAIGNS: list[dict[str, Any]] = [
    {
        "name": "schottky_25_100V",
        "keywords": [
            "PMEG6020 schottky",
            "PMEG schottky rectifier",
            "SS34 schottky",
            "MBRS340 schottky",
            "B340A schottky",
            "MBR20100 schottky",
            "SS54 schottky",
            "NRVTS260 schottky",
            "STPS340 schottky",
            "SK34 schottky",
        ],
        "accept": lambda row: _subtype(row) == "schottky" and 25 <= _vr(row) <= 100,
        "cap": 80,
    },
    {
        "name": "sic_schottky_1200V",
        "keywords": [
            "C4D10120 sic schottky",
            "C4D sic schottky 1200V",
            "1200V silicon carbide schottky diode",
            "IDW20G120 sic schottky",
            "MSC sic schottky 1200V",
            "STPSC10H12 sic schottky",
            "GP2D sic schottky 1200V",
            "FFSH sic schottky 1200V",
        ],
        "accept": lambda row: _subtype(row) == "sicSchottky" and _vr(row) == 1200,
        "cap": 80,
    },
    {
        "name": "ultrafast_200V",
        "keywords": [
            "ultrafast rectifier 200V",
            "diode ultrafast 200V",
            "ultrafast 200V 2A",
            "ultrafast 200V 1A",
            "ultrafast 200V 4A",
            "ultrafast 200V 8A",
            "MURS220",
            "MUR420",
            "MUR220",
            "MURA220",
            "MURB220",
            "US1D",
            "ES1D",
        ],
        "accept": lambda row: _subtype(row) == "ultrafast" and _vr(row) == 200,
        "cap": 80,
    },
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def load_existing_mpns(path: Path) -> set[str]:
    mpns: set[str] = set()
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            mpns.add(
                rec["semiconductor"]["diode"]["manufacturerInfo"]["datasheetInfo"]["part"][
                    "partNumber"
                ]
            )
    return mpns


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit-per-search", type=int, default=50)
    args = ap.parse_args()

    existing = load_existing_mpns(DB_PATH)
    print(f"existing diode MPNs: {len(existing)}")

    creds = load_credentials()
    if creds.digikey is None:
        raise SystemExit("No Digi-Key credentials available — cannot run live fetch.")

    accepted: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    seen_new: set[str] = set()
    skip_log: dict[str, int] = {}

    def skip(reason: str) -> None:
        skip_log[reason] = skip_log.get(reason, 0) + 1

    with DigiKeyClient(creds.digikey) as dk:
        for camp in CAMPAIGNS:
            n_before = len(accepted)
            accept: Callable[[dict[str, Any]], bool] = camp["accept"]
            for kw in camp["keywords"]:
                if len(accepted) - n_before >= camp["cap"]:
                    break
                payload = dk.search(kw, limit=args.limit_per_search)
                products = payload.get("Products") or []
                for product in products:
                    if len(accepted) - n_before >= camp["cap"]:
                        break
                    mpn = product.get("ManufacturerPartNumber")
                    if not mpn or mpn in existing or mpn in seen_new:
                        skip("duplicate-or-no-mpn")
                        continue
                    if detect_category(product, "digikey") != "diodes":
                        skip("not-a-diode-category")
                        continue
                    try:
                        row = convert_digikey_to_tas_diode(product)
                    except IncompleteSourceError as exc:
                        # Digi-Key did not publish a required rating for
                        # this product — the part is unusable as a source,
                        # which is a selection decision, not a silent
                        # validation skip.  Logged and counted.
                        skip(f"incomplete-source:{type(exc).__name__}")
                        continue
                    if not accept(row):
                        skip(f"outside-class:{camp['name']}")
                        continue
                    # HARD GATE: schema + integrity guard.  A failure here
                    # means the converter produced a bad row — abort loudly.
                    guard_component("diodes", row)
                    accepted.append(row)
                    seen_new.add(mpn)
                    provenance.append(
                        {
                            "mpn": mpn,
                            "class": camp["name"],
                            "source": "digikey-api-v3",
                            "search": kw,
                            "sourceUrl": product.get("ProductUrl")
                            or DIGIKEY_PRODUCT_URL.format(mpn=mpn),
                            "datasheetUrl": product.get("PrimaryDatasheet") or "",
                        }
                    )
            print(f"[{camp['name']}] accepted {len(accepted) - n_before} (cap {camp['cap']})")

    print(f"total accepted: {len(accepted)}")
    print("skip summary:", json.dumps(skip_log, indent=2, sort_keys=True))

    if args.dry_run:
        print("dry run — nothing written")
        return 0

    if not accepted:
        raise SystemExit("No rows accepted — refusing to write nothing.")

    # Re-validate EVERY row once more immediately before the append so a
    # bug between accept-time and write-time cannot slip a bad row in.
    for row in accepted:
        guard_component("diodes", row)

    with DB_PATH.open("a") as fh:
        for row in accepted:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    with PROVENANCE_PATH.open("a") as fh:
        for rec in provenance:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"appended {len(accepted)} rows to {DB_PATH}")
    print(f"provenance appended to {PROVENANCE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
