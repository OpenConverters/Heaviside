#!/usr/bin/env python3
"""Backfill mechanical dimensions for the Coilcraft LPS5030 series.

The LPS5030 records in the internal DB were imported from a distributor feed
that carried no mechanical drawing (``mechanical: null``), so the cross-
referencer had no footprint to enforce "the substitute must fit the original's
board space" against — which let a 12x12 mm Würth part rank as an equivalent
for a 4.9x4.9 mm LPS5030.

Dimensions are taken straight from the Coilcraft LPS5030 datasheet
(Document 581, rev 05/28/24): "4.9 x 4.9 mm footprint; less than 3 mm tall".
Mechanical drawing: body 4.80 x 4.80 x 2.9 mm + 0.13 mm termination ->
4.93 x 4.93 mm max overall, 3.0 mm max height. We store the max overall body
size (the board space the part actually occupies), in metres.

No value is invented: every number here is read from the datasheet. Idempotent
— re-running leaves already-correct rows untouched.
"""
from __future__ import annotations

import json
from pathlib import Path

# Max overall body size (incl. termination) from the LPS5030 datasheet, metres.
LPS5030_DIMS = {
    "length": {"nominal": 0.00493},
    "width": {"nominal": 0.00493},
    "height": {"nominal": 0.0030},
}
SERIES_PREFIX = "LPS5030-"  # standard series (not the LPS5030E variant)

MAGNETICS = Path(__file__).resolve().parents[1] / "TAS" / "data" / "magnetics.ndjson"


def main() -> None:
    lines = MAGNETICS.read_text(encoding="utf-8").splitlines()
    patched = 0
    out: list[str] = []
    for line in lines:
        if not line.strip():
            out.append(line)
            continue
        env = json.loads(line)
        mi = env.get("magnetic", {}).get("manufacturerInfo", {})
        ref = str(mi.get("reference", ""))
        ds = mi.get("datasheetInfo", {})
        if ref.startswith(SERIES_PREFIX) and ds.get("mechanical") != LPS5030_DIMS:
            ds["mechanical"] = dict(LPS5030_DIMS)
            patched += 1
            out.append(json.dumps(env, ensure_ascii=False))
        else:
            out.append(line)
    MAGNETICS.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Patched {patched} LPS5030 records with mechanical dimensions.")


if __name__ == "__main__":
    main()
