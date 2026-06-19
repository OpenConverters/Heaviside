#!/usr/bin/env python3
"""
Reclassify 31 WE-CBA records in magnetics.ndjson from subtype:inductor
to subtype:chipBead and backfill impedance curves from Heimdall data.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.import_chip_beads import _clean_record  # noqa: E402

DEST    = REPO / "TAS" / "data" / "magnetics.ndjson"
SOURCE  = Path("/tmp/heaviside_chip_beads.ndjson")

WE_CBA_FAMILIES = {"WE-CBA High Current", "WE-CBA Wide Band", "WE-CBA High Speed"}


def main() -> None:
    # Build ref → cleaned_heimdall_record for WE-CBA parts
    patch: dict[str, dict] = {}
    with open(SOURCE) as f:
        for line in f:
            rec = json.loads(line)
            mi = rec["magnetic"]["manufacturerInfo"]
            if mi.get("family") in WE_CBA_FAMILIES:
                ref = str(mi.get("orderCode") or mi.get("reference") or "")
                if ref:
                    patch[ref] = _clean_record(rec)

    print(f"WE-CBA patch records loaded: {len(patch)}")

    patched = 0
    tmp = DEST.with_suffix(".ndjson.tmp")
    with open(DEST) as src, open(tmp, "w") as dst:
        for line in src:
            line = line.rstrip("\n")
            if not line:
                dst.write("\n")
                continue
            rec = json.loads(line)
            mi = rec.get("magnetic", {}).get("manufacturerInfo", {})
            ref = str(mi.get("orderCode") or mi.get("reference") or "")
            if ref in patch:
                dst.write(json.dumps(patch[ref]) + "\n")
                patched += 1
            else:
                dst.write(line + "\n")

    os.replace(tmp, DEST)
    print(f"Patched {patched} WE-CBA records → chipBead subtype + impedance curves")


if __name__ == "__main__":
    main()
