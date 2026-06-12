#!/usr/bin/env python3
"""Repair the 33 invalid Vishay VS-* IGBT module rows in TAS/data/igbts.ndjson.

Corruption pattern (scraper artifact, diagnosed 2026-06-12):
- ``part.case`` held the voltage class as a NUMBER (600/650/1200) instead of
  the package string;
- ``electrical.collectorEmitterVoltage`` held I_C@Tc=80..90C (or other stray
  table numbers), NOT V_CES;
- ``electrical.continuousCollectorCurrent`` held the Tc value (80/90...);
- required ``electrical.collectorEmitterSaturation`` was missing.

Every value below was extracted from the part's Vishay datasheet PDF
(fetched 2026-06-12; URL recorded per part). No value is guessed; parts
without a readable datasheet would be left untouched (none, as it happens).

I_C is at Tc=25C; V_CE(sat) is the typical value at V_GE=15V, Tj=25C, at the
datasheet's specified test current.

Untouched rows are passed through byte-identical.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "TAS" / "data" / "igbts.ndjson"

# mpn -> (case/package, V_CES [V], I_C@Tc=25C [A], VCE(sat) typ [V], datasheet URL)
FIXES: dict[str, tuple[str, float, float, float, str]] = {
    "VS-20MT120PFP":     ("MTP", 1200, 57, 1.84, "https://www.vishay.com/docs/96725/vs-20mt120pfp.pdf"),
    "VS-40MT120PHAPbF":  ("MTP", 1200, 75, 2.24, "https://www.vishay.com/docs/96762/vs-40mt120phapbf.pdf"),
    "VS-50MT060PHTAPbF": ("MTP", 600, 121, 1.41, "https://www.vishay.com/docs/96734/vs-50mt060phtapbf.pdf"),
    "VS-50MT060TFT":     ("MTP", 600, 55, 1.81, "https://www.vishay.com/docs/96858/vs-50mt060tft.pdf"),
    "VS-GT100DA120UF":   ("SOT-227", 1200, 187, 1.93, "https://www.vishay.com/docs/96079/vs-gt100da120uf.pdf"),
    "VS-GT100LA65UF":    ("SOT-227", 650, 94, 1.72, "https://www.vishay.com/docs/96787/vs-gt100la65uf.pdf"),
    "VS-GT100TS065N":    ("INT-A-PAK", 650, 96, 1.82, "https://www.vishay.com/docs/97007/vs-gt100ts065n.pdf"),
    "VS-GT100TS065S":    ("INT-A-PAK", 650, 264, 1.05, "https://www.vishay.com/docs/97011/vs-gt100ts065s.pdf"),
    "VS-GT100YG120UT":   ("ECONO 3", 1200, 170, 2.12, "https://www.vishay.com/docs/96994/vs-gt100yg120ut.pdf"),
    "VS-GT150TS065S":    ("INT-A-PAK", 650, 372, 1.07, "https://www.vishay.com/docs/97061/vs-gt150ts065s.pdf"),
    "VS-GT150YG120NT":   ("ECONO 3", 1200, 244, 2.18, "https://www.vishay.com/docs/96993/vs-gt150yg120nt.pdf"),
    "VS-GT180DA120U":    ("SOT-227", 1200, 281, 1.55, "https://www.vishay.com/docs/96044/vs-gt180da120u.pdf"),
    "VS-GT200TS065N":    ("INT-A-PAK", 650, 193, 1.83, "https://www.vishay.com/docs/96700/vs-gt200ts065n.pdf"),
    "VS-GT200TS065S":    ("INT-A-PAK", 650, 476, 1.09, "https://www.vishay.com/docs/97091/vs-gt200ts065s.pdf"),
    "VS-GT250SA60S":     ("SOT-227", 600, 359, 1.16, "https://www.vishay.com/docs/96731/vs-gt250sa60s.pdf"),
    "VS-GT300TD60S":     ("Dual INT-A-PAK low profile", 600, 466, 1.15, "https://www.vishay.com/docs/96723/vs-gt300td60s.pdf"),
    "VS-GT300YH120N":    ("Dual INT-A-PAK", 1200, 400, 1.93, "https://www.vishay.com/docs/94681/vs-gt300yh120n.pdf"),
    "VS-GT400LH060N":    ("Dual INT-A-PAK", 600, 492, 1.67, "https://www.vishay.com/docs/96988/vs-gt400lh060n.pdf"),
    "VS-GT400TD60S":     ("Dual INT-A-PAK low profile", 600, 711, 1.14, "https://www.vishay.com/docs/96724/vs-gt400td60s.pdf"),
    "VS-GT50LA65UF":     ("SOT-227", 650, 59, 1.70, "https://www.vishay.com/docs/96784/vs-gt50la65uf.pdf"),
    "VS-GT51YF120NT":    ("ECONO 2", 1200, 64, 2.34, "https://www.vishay.com/docs/97300/vs-gt51yf120nt.pdf"),
    "VS-GT55LA120UX":    ("SOT-227", 1200, 68, 2.39, "https://www.vishay.com/docs/96778/vs-gt55la120ux.pdf"),
    "VS-GT55NA120UX":    ("SOT-227", 1200, 68, 2.39, "https://www.vishay.com/docs/96874/vs-gt55na120ux.pdf"),
    "VS-GT600TH060S":    ("Dual INT-A-PAK", 600, 755, 1.29, "https://www.vishay.com/docs/97012/vs-gt600th60s.pdf"),
    "VS-GT75LA60UF":     ("SOT-227", 600, 81, 1.79, "https://www.vishay.com/docs/96736/vs-gt75la60uf.pdf"),
    "VS-GT75NA60UF":     ("SOT-227", 600, 81, 1.79, "https://www.vishay.com/docs/96737/vs-gt75na60uf.pdf"),
    "VS-GT75YF120UT":    ("ECONO 2", 1200, 118, 2.20, "https://www.vishay.com/docs/96842/vs-gt75yf120ut.pdf"),
    "VS-GT76YF120NT":    ("ECONO 2", 1200, 118, 2.20, "https://www.vishay.com/docs/97301/vs-gt76yf120nt.pdf"),
    "VS-GT80DA120U":     ("SOT-227", 1200, 139, 2.0, "https://www.vishay.com/docs/96379/vs-gt80da120u.pdf"),
    "VS-GT80DA60U":      ("SOT-227", 600, 123, 1.83, "https://www.vishay.com/docs/95884/vs-gt80da60u.pdf"),
    "VS-GT90DA120U":     ("SOT-227", 1200, 169, 2.17, "https://www.vishay.com/docs/96747/vs-gt90da120u.pdf"),
    "VS-GT90DA60U":      ("SOT-227", 600, 146, 1.64, "https://www.vishay.com/docs/96805/vs-gt90da60u.pdf"),
    "VS-GT90SA120U":     ("SOT-227", 1200, 169, 2.17, "https://www.vishay.com/docs/96863/vs-gt90sa120u.pdf"),
}


def main() -> int:
    fixed = 0
    out: list[str] = []
    seen: set[str] = set()
    for line in DATA.open():
        raw = line.rstrip("\n")
        if not raw.strip():
            continue
        row = json.loads(raw)
        igbt = row.get("semiconductor", {}).get("igbt", {})
        mi = igbt.get("manufacturerInfo", {})
        dsi = mi.get("datasheetInfo", {})
        part = dsi.get("part", {})
        mpn = part.get("partNumber")
        if mi.get("name") != "Vishay" or mpn not in FIXES:
            out.append(raw)  # byte-identical pass-through
            continue
        # Only repair rows exhibiting the diagnosed corruption (numeric case).
        if not isinstance(part.get("case"), (int, float)):
            raise SystemExit(f"{mpn}: expected numeric corrupt 'case', found {part.get('case')!r} — refusing to touch")
        case, vces, ic, vce_sat, _url = FIXES[mpn]
        part["case"] = case
        elec = dsi["electrical"]
        elec["collectorEmitterVoltage"] = vces
        elec["continuousCollectorCurrent"] = ic
        elec["collectorEmitterSaturation"] = vce_sat
        seen.add(mpn)
        fixed += 1
        out.append(json.dumps(row, ensure_ascii=False))
    missing = set(FIXES) - seen
    if missing:
        print(f"WARNING: {len(missing)} fix entries matched no row: {sorted(missing)}")
    tmp = DATA.with_suffix(".ndjson.migrating")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    tmp.replace(DATA)
    print(f"fixed {fixed} Vishay IGBT module rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
