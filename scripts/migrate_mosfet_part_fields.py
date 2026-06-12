#!/usr/bin/env python3
"""Drop free-text catalog fields from TAS mosfet part sections.

User decision (2026-06-12): ``part.package`` and ``part.qualification``
stay (now legal — added to SAS utils part schema); ``specialFeatures``
(marketing blurbs) and ``generation`` are dropped, data loss approved.
Untouched rows stay byte-identical.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "TAS" / "data" / "mosfets.ndjson"
DROP = ("specialFeatures", "generation")


def main() -> int:
    stats = {"rows": 0, "specialFeatures": 0, "generation": 0}
    out: list[str] = []
    for line in DATA.open():
        raw = line.rstrip("\n")
        if not raw.strip():
            continue
        stats["rows"] += 1
        row = json.loads(raw)
        body = row.get("semiconductor", row).get("mosfet", row.get("mosfet", row))
        part = body.get("manufacturerInfo", {}).get("datasheetInfo", {}).get("part", {})
        changed = False
        for field in DROP:
            if field in part:
                del part[field]
                stats[field] += 1
                changed = True
        out.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")) if changed else raw)
    tmp = DATA.with_suffix(".ndjson.migrating")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    tmp.replace(DATA)
    print(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
