#!/usr/bin/env python3
"""Quarantine Vishay catalog-matrix stub rows from TAS/data/resistors.ndjson.

Context
-------
117,472 resistor rows, of which 86,751 are Vishay rows where
``part.partNumber == part.series``.  These are NOT orderable parts — they are
catalog-matrix stubs: only ~366 distinct partNumber values across all 86,751
rows, and those values are bare SERIES NAMES like "PTN" (5,616 rows), "P-NS"
(5,304), "DLA Moisture Resistant Chip Resistor" (3,120), etc.  They pollute
the catalog and win Pareto picks (e.g. "HTS" wins a buck feedback-divider
slot).

Quarantine criterion
--------------------
Resistor rows where ALL of:
  * manufacturerInfo.name == "Vishay"
  * part.partNumber == part.series   (non-empty)

Non-Vishay partNumber==series rows (~93, Yageo/TE/Stackpole) are KEPT —
those are real orderable MPNs with a sloppy duplicated series field.

Action
------
MOVE matching rows to TAS/data/resistors.quarantine_stubs.ndjson (appending if
the file already exists, following the convention in quarantine_tas_20260612.py).
Kept rows stay byte-identical.  Atomic replace via a .quarantining temp file.

Usage
-----
    PYTHONPATH=. .venv-web/bin/python scripts/quarantine_resistor_stubs_20260613.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "TAS" / "data"
SRC = DATA / "resistors.ndjson"
QPATH = DATA / "resistors.quarantine_stubs.ndjson"


def _is_stub(row: dict) -> str | None:
    """Return a reason string if this row should be quarantined, else None."""
    body = row.get("resistor", row)
    mi = body.get("manufacturerInfo", {})
    if mi.get("name") != "Vishay":
        return None
    part = mi.get("datasheetInfo", {}).get("part", {})
    mpn = part.get("partNumber") or ""
    series = part.get("series") or ""
    if mpn and mpn == series:
        return f"partNumber == series (catalog-matrix stub: {mpn!r})"
    return None


def main() -> int:
    keep: list[str] = []
    junk: list[str] = []
    reasons: Counter[str] = Counter()

    for line in SRC.open(encoding="utf-8"):
        raw = line.rstrip("\n")
        if not raw.strip():
            continue
        reason = _is_stub(json.loads(raw))
        if reason:
            junk.append(raw)
            # Bucket by series name (the value of partNumber), not the full reason string
            series_val = json.loads(raw)
            body = series_val.get("resistor", series_val)
            pn = (
                body.get("manufacturerInfo", {})
                .get("datasheetInfo", {})
                .get("part", {})
                .get("partNumber", "?")
            )
            reasons[pn] += 1
        else:
            keep.append(raw)

    if not junk:
        print("resistors  nothing to quarantine")
        return 0

    # Append to quarantine (idempotency: if script is re-run on already-pruned
    # file, _is_stub will find nothing and junk will be empty — no harm done).
    existing = QPATH.read_text(encoding="utf-8") if QPATH.exists() else ""
    QPATH.write_text(existing + "\n".join(junk) + "\n", encoding="utf-8")

    # Atomic replace of source
    tmp = SRC.with_suffix(".ndjson.quarantining")
    tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
    tmp.replace(SRC)

    print(f"resistors  kept={len(keep)}  quarantined={len(junk)} -> {QPATH.name}")
    print(f"           distinct series quarantined: {len(reasons)}")
    print("\nTop 25 quarantined series by row count:")
    for series, cnt in reasons.most_common(25):
        print(f"  {cnt:6d}  {series!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
