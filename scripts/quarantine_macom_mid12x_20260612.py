#!/usr/bin/env python3
"""Quarantine the fabricated MACOM MID12X-SN diode block.

Audit findings (2026-06-12)
---------------------------
The 30 rows with partNumber MID12X-S0 through MID12X-S29 (manufacturer
"MACOM") are confirmed non-existent parts:

1. MACOM is an RF/microwave company (RF amplifiers, PIN diodes, Schottky
   mixer/detector diodes in SOT-23/SC-70).  MACOM has never produced
   power rectifier diodes in TO-220 packages.  All MACOM diodes use
   the MA-prefix scheme (MA4P*, MA4E*); no MID-prefix product exists.

2. Digi-Key v3 API: zero results for any MID12X-SN MPN.

3. Web search (MACOM.com, Mouser, Octopart, datasheetarchive): zero
   results.  The datasheetUrl for every row is a datasheetpdf.com
   search page that returns 404 when fetched -- a known synthetic-row
   junk-class pattern (already blocked by guard BAD_DATASHEET_URL_PATTERNS).

4. The Vf sequence is machine-generated: MID12X-S0 has Vf=0.50 V,
   each subsequent row adds exactly 0.02 V (S1=0.52, S2=0.54 ...
   S29=1.08 V).  All other electricals are identical across all 30
   rows.  This is a synthetic generation artefact, not real variation
   across part-number suffixes.

5. MID12X-S0 was winning the D1 slot in the buck reference design due
   to its implausibly low Vf=0.50 V @ 6 A -- below the Schottky barrier
   for a 200 V silicon diode.

Sibling scope: all 30 MID12X rows share the same fetch signature
(manufacturer=MACOM, MPN pattern MID12X-S<N>, datasheetpdf.com search
URL).  All 30 are moved.

Action: MOVE to diodes.quarantine_synthetic.ndjson.  Rows are
byte-identical to the originals (no modification before quarantine).
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "TAS" / "data"
DIODES = DATA / "diodes.ndjson"
QUARANTINE = DATA / "diodes.quarantine_synthetic.ndjson"

# Pattern that identifies the fabricated MACOM block:
# - manufacturer exactly "MACOM"
# - MPN matches MID12X-S<number>
# - datasheetUrl is a datasheetpdf.com search page
MID12X_MPN_RE = re.compile(r"^MID12X-S\d+$")


def _is_macom_mid12x(row: dict) -> str | None:
    """Return a quarantine reason string if the row is a MACOM MID12X fake,
    None otherwise."""
    try:
        mi = row["semiconductor"]["diode"]["manufacturerInfo"]
    except (KeyError, TypeError):
        return None
    mfr = mi.get("name", "")
    mpn = mi.get("reference", "") or (
        mi.get("datasheetInfo", {}).get("part", {}).get("partNumber", "")
    )
    url = mi.get("datasheetUrl", "")
    if (
        mfr == "MACOM"
        and MID12X_MPN_RE.match(mpn)
        and "datasheetpdf.com/search" in url
    ):
        return (
            "fabricated MACOM MID12X-SN row: MACOM makes no power rectifier diodes, "
            "zero distributor results, Vf increments exactly 0.02 V per suffix "
            "(machine-generated), datasheetpdf.com search-page URL returns 404"
        )
    return None


def main() -> int:
    keep: list[str] = []
    junk: list[str] = []
    reasons: Counter[str] = Counter()

    for raw in DIODES.open(encoding="utf-8"):
        raw_stripped = raw.rstrip("\n")
        if not raw_stripped.strip():
            continue
        try:
            row = json.loads(raw_stripped)
        except json.JSONDecodeError as exc:
            print(f"WARN: corrupt line skipped ({exc})", file=sys.stderr)
            keep.append(raw_stripped)
            continue
        reason = _is_macom_mid12x(row)
        if reason:
            junk.append(raw_stripped)
            reasons[reason] += 1
        else:
            keep.append(raw_stripped)

    if not junk:
        print("diodes: nothing to quarantine (no MID12X-SN rows found)")
        return 0

    # Append to quarantine file (never overwrite — preserves prior quarantine)
    existing = QUARANTINE.read_text(encoding="utf-8") if QUARANTINE.exists() else ""
    QUARANTINE.write_text(existing + "\n".join(junk) + "\n", encoding="utf-8")

    # Atomic replace of main DB
    tmp = DIODES.with_suffix(".ndjson.quarantining")
    tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
    tmp.replace(DIODES)

    print(
        f"diodes: kept={len(keep)}  quarantined={len(junk)} "
        f"-> diodes.quarantine_synthetic.ndjson"
    )
    for r, c in reasons.most_common():
        print(f"        {c:5d}  {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
