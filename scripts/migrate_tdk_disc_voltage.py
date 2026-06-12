#!/usr/bin/env python3
"""Fill missing ``electrical.ratedVoltage`` for TDK CD/CS leaded safety-disc rows.

318 rows (TDK CD10..CD95, CS11..CS95 with dielectric letter B/E/F/SL/ZU) carry
the rated-voltage code ``2GA`` in the MPN but no ``ratedVoltage``.

Per TDK's PART NUMBER CONSTRUCTION in the official series catalogs, ``2GA``
is the safety AC rated-voltage code:

- CD series: "X1: 440V AC / Y1: 400V AC"
  https://product.tdk.com/system/files/dam/doc/product/capacitor/ceramic/lead-disc/catalog/leaddisc_commercial_cd_en.pdf
- CS series: "X1: 440V AC / Y2: 300V AC"
  https://product.tdk.com/system/files/dam/doc/product/capacitor/ceramic/lead-disc/catalog/leaddisc_commercial_cs_en.pdf

TDK publishes no DC rated voltage for these parts (only withstanding voltage),
so ``voltageRatedDcMax`` is left unset. We store 440 — the X1 rated voltage
common to both series and the value TDK's product pages list first under
"Rated Voltage" — matching the existing DB convention of storing the AC
safety rating for safety-certified caps (cf. WCAP-CSSA rows).

Spot-checks (2026-06-12, TDK product pages):
- CD45SL2GA100JYGKA  -> Rated Voltage X1/440VAC, Y1/400VAC
  https://product.tdk.com/en/search/capacitor/ceramic/lead-disc/info?part_no=CD45SL2GA100JYGKA
- CS11ZU2GA472MAGKA  -> Rated Voltage X1/440VAC, Y2/300VAC
  https://product.tdk.com/en/search/capacitor/ceramic/lead-disc/info?part_no=CS11ZU2GA472MAGKA
- CD12-E2GA222MYGS   -> TDK page: E(+20,-55%), withstanding 4kVAC (DigiKey's
  "250V" on this legacy MPN reflects the pre-IEC60384-14-ed.4 AC250V line
  rating; TDK's current rating for the 2GA family is X1/440VAC)
  https://product.tdk.com/en/search/capacitor/ceramic/lead-disc/info?part_no=CD12-E2GA222MYGS

Untouched rows are passed through byte-identical.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "TAS" / "data" / "capacitors.ndjson"

MPN_RE = re.compile(r"^C[DS]\d{2}(SL|ZU|-[BEF])2GA")
RATED_VOLTAGE_AC = 440  # X1 rating, TDK CD/CS catalogs (see module docstring)


def main() -> int:
    filled = 0
    out: list[str] = []
    for line in DATA.open():
        raw = line.rstrip("\n")
        if not raw.strip():
            continue
        row = json.loads(raw)
        body = row.get("capacitor", row)
        mi = body.get("manufacturerInfo", {})
        dsi = mi.get("datasheetInfo", {})
        part = dsi.get("part", {})
        mpn = part.get("partNumber") or ""
        elec = dsi.get("electrical")
        if (
            mi.get("name") != "TDK"
            or not MPN_RE.match(mpn)
            or elec is None
            or "ratedVoltage" in elec
        ):
            out.append(raw)  # byte-identical pass-through
            continue
        elec["ratedVoltage"] = RATED_VOLTAGE_AC
        filled += 1
        out.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
    tmp = DATA.with_suffix(".ndjson.migrating")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    tmp.replace(DATA)
    print(f"filled ratedVoltage=440 (X1 AC) on {filled} TDK CD/CS 2GA disc rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
