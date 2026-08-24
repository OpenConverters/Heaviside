#!/usr/bin/env python3
"""Populate `automotive` on chip-bead catalogue records (ABT #884).

A cross-reference can silently downgrade a board's qualification: substituting a
general-grade bead for an AEC-Q200 one looks identical on every electrical
parameter we check, and qualification grade is often the reason a specific part
is on the BOM. The MAS magnetic schema has had the field for this all along —
`datasheetInfo.part.automotive`, "True if the part is qualified for automotive
applications (AEC-Q200 or equivalent)" — and it was null for all 3 058 chip beads
from all ten manufacturers, so the gate had nothing to compare.

This fills it from each manufacturer's OWN published data. Nothing is inferred
from a part number, and a part neither source covers is left without the field
(an honest unknown that the gate reports as unverifiable, rather than a guess):

* **Würth Elektronik** — RedExpert's PCB-Ferrites catalogue (module 1) publishes
  `Is_AECQ_Text` per order code: "1" and "3" are AEC-Q200 grades, "No" is not
  qualified. That is exactly the schema's wording, so grade != "No" -> True.
  (`Is_Automotive_Present` is a narrower marketing flag — it calls 180 Grade-1
  qualified parts non-automotive — so it is deliberately NOT what we read.)
* **Murata** — their records carry Murata's own application category in
  `manufacturerInfo.description`: "General", "Infotainment" or
  "Powertrain/Safety". The latter two are the automotive lineup.

Additive and idempotent: a record that already has the field is never
overwritten, and re-running changes nothing. Run with --dry-run first.

    python scripts/backfill_bead_automotive.py --dry-run
    python scripts/backfill_bead_automotive.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import urllib.request
from pathlib import Path

REDEXPERT_PCB_FERRITES = "https://redexpert.we-online.com/redexpert/product/list/1"
# /redexpert/* browser-sniffs the UA; a bare "Mozilla/5.0" gets 301 -> update-browser.
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)

# Murata's own application categories, from the record's description field.
_MURATA_AUTOMOTIVE = {"powertrain/safety", "infotainment"}
_MURATA_GENERAL = {"general"}


def _catalogue_path() -> Path:
    root = Path(
        os.environ.get(
            "HEAVISIDE_TAS_DATA_DIR",
            str(Path(__file__).resolve().parents[1] / "TAS" / "data"),
        )
    )
    return root / "magnetics.ndjson"


def fetch_wurth_aec() -> dict[str, bool]:
    """order code -> is AEC-Q200 qualified, from Würth's own catalogue."""
    req = urllib.request.Request(
        REDEXPERT_PCB_FERRITES,
        headers={"User-Agent": _UA, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8", "replace")
    # RedExpert responses can carry raw control characters.
    payload = json.loads(re.sub(r"[\x00-\x1f]", "", raw))
    rows = payload["Data"] if isinstance(payload, dict) else payload
    out: dict[str, bool] = {}
    for row in rows:
        code = str(row.get("Order_Code") or "").strip()
        grade = str(row.get("Is_AECQ_Text") or "").strip()
        if code and grade:
            out[code] = grade.lower() != "no"
    if not out:
        raise SystemExit("RedExpert returned no order codes — refusing to run blind")
    return out


def _bead_record(env: dict) -> dict | None:
    """The manufacturerInfo of a chipBead envelope, or None."""
    mi = (env.get("magnetic") or {}).get("manufacturerInfo")
    if not isinstance(mi, dict):
        return None
    electrical = (mi.get("datasheetInfo") or {}).get("electrical")
    if not isinstance(electrical, list) or not electrical:
        return None
    first = electrical[0]
    if not isinstance(first, dict) or first.get("subtype") != "chipBead":
        return None
    return mi


def decide(mi: dict, wurth: dict[str, bool]) -> bool | None:
    """True/False from the manufacturer's own data, or None when unknown."""
    name = str(mi.get("name") or "")
    part = (mi.get("datasheetInfo") or {}).get("part") or {}
    ref = str(mi.get("reference") or part.get("partNumber") or "").strip()
    if name == "Würth Elektronik":
        return wurth.get(ref)
    if name == "Murata":
        described = str(mi.get("description") or part.get("description") or "").strip().lower()
        if described in _MURATA_AUTOMOTIVE:
            return True
        if described in _MURATA_GENERAL:
            return False
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the catalogue")
    ap.add_argument("--dry-run", action="store_true", help="report only (default)")
    args = ap.parse_args()
    apply = args.apply and not args.dry_run

    path = _catalogue_path()
    if not path.is_file():
        raise SystemExit(f"catalogue not found: {path}")

    print("reading Würth AEC-Q200 grades from RedExpert ...")
    wurth = fetch_wurth_aec()
    print(f"  {len(wurth)} order codes, {sum(wurth.values())} AEC-Q200 qualified")

    tmp = path.with_suffix(".ndjson.backfill-tmp")
    stats = {"beads": 0, "set_true": 0, "set_false": 0, "already": 0, "unknown": 0, "lines": 0}
    unknown_by_mfr: dict[str, int] = {}

    with open(path, encoding="utf-8") as src, open(tmp, "w", encoding="utf-8") as dst:
        for line in src:
            stats["lines"] += 1
            stripped = line.strip()
            if not stripped:
                dst.write(line)
                continue
            if '"chipBead"' not in stripped:
                dst.write(line)  # untouched, byte for byte
                continue
            try:
                env = json.loads(stripped)
            except json.JSONDecodeError:
                dst.write(line)
                continue
            mi = _bead_record(env)
            if mi is None:
                dst.write(line)
                continue
            stats["beads"] += 1
            part = (mi.get("datasheetInfo") or {}).get("part")
            if not isinstance(part, dict):
                stats["unknown"] += 1
                dst.write(line)
                continue
            if "automotive" in part:
                stats["already"] += 1
                dst.write(line)
                continue
            verdict = decide(mi, wurth)
            if verdict is None:
                stats["unknown"] += 1
                name = str(mi.get("name") or "?")
                unknown_by_mfr[name] = unknown_by_mfr.get(name, 0) + 1
                dst.write(line)
                continue
            part["automotive"] = verdict
            stats["set_true" if verdict else "set_false"] += 1
            dst.write(json.dumps(env, ensure_ascii=False) + "\n")

    print(f"\ncatalogue lines      : {stats['lines']}")
    print(f"chip-bead records    : {stats['beads']}")
    print(f"  automotive = True  : {stats['set_true']}")
    print(f"  automotive = False : {stats['set_false']}")
    print(f"  already had it     : {stats['already']}")
    print(f"  left unknown       : {stats['unknown']}")
    for name, count in sorted(unknown_by_mfr.items(), key=lambda kv: -kv[1]):
        print(f"      {count:5d}  {name}")

    if not apply:
        tmp.unlink(missing_ok=True)
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0

    backup = path.with_suffix(".ndjson.pre-automotive")
    if not backup.exists():
        shutil.copy2(path, backup)
        print(f"\nbackup: {backup}")
    os.replace(tmp, path)
    print(f"written: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
