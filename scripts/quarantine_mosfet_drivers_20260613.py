#!/usr/bin/env python3
"""Quarantine pure gate-driver ICs misfiled in mosfets.ndjson.

The original report (mosfets_report.md, session 2) mentioned "~45 TI gate
drivers/misfiled (UCC*, LM*)" as an approximation.  After systematic
analysis of all rows, the confirmed count is 2:

    UCC27511  Texas Instruments  Low-side gate driver (single-channel, high-speed)
    UCC27321  Texas Instruments  Low-side gate driver (single 9-A high-speed)

The LMG* rows (LMG3410, LMG3422, LMG3424, LMG3526, LMG3522, LMG3600,
LMG3635, LMG3642, LMG2100, LMG2610 …) are TI GaN-FET power stages with
integrated drivers — they carry VDS/ID/Ron specs and belong in mosfets.
LMS1225 is a "MOSFET with Integrated Driver" — same reasoning.

Criterion: part number starts with "UCC" (TI Universal Control Circuit /
gate-driver line).  No other TI part-number prefixes in the database
qualify as pure drivers.

Destination: TAS/data/mosfets.quarantine_misfiled_drivers.ndjson
(created fresh; never overwrites existing content — appends if the file
already exists, which is the same safe pattern used in
quarantine_tas_20260612.py).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "TAS" / "data"
SRC = DATA / "mosfets.ndjson"
DEST = DATA / "mosfets.quarantine_misfiled_drivers.ndjson"


def _is_misfiled_driver(row: dict) -> str | None:
    """Return a reason string if the row is a misfiled gate driver, else None."""
    body = row.get("semiconductor", row).get("mosfet", row.get("mosfet", row))
    mi = body.get("manufacturerInfo", {})
    part = mi.get("datasheetInfo", {}).get("part", {})
    mpn: str = part.get("partNumber") or mi.get("reference") or ""

    if mpn.upper().startswith("UCC"):
        return f"TI gate driver (UCC series): {mpn}"
    return None


def main() -> int:
    keep: list[str] = []
    junk: list[str] = []
    reasons: list[str] = []

    for line in SRC.open(encoding="utf-8"):
        raw = line.rstrip("\n")
        if not raw.strip():
            continue
        row = json.loads(raw)
        reason = _is_misfiled_driver(row)
        if reason:
            junk.append(raw)
            reasons.append(reason)
        else:
            keep.append(raw)

    if not junk:
        print("mosfets: no misfiled gate drivers found — nothing quarantined")
        return 0

    # Append to (or create) the quarantine file
    existing = DEST.read_text(encoding="utf-8") if DEST.exists() else ""
    DEST.write_text(existing + "\n".join(junk) + "\n", encoding="utf-8")

    # Overwrite mosfets.ndjson atomically
    tmp = SRC.with_suffix(".ndjson.quarantining")
    tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
    tmp.replace(SRC)

    print(f"mosfets  kept={len(keep)}  quarantined={len(junk)} -> {DEST.name}")
    for r in reasons:
        print(f"           {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
