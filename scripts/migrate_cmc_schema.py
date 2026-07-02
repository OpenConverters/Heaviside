#!/usr/bin/env python3
"""Migrate Würth CMC entries in TAS/data/magnetics.ndjson from the old
`subtype: "inductor"` electrical schema to the new MAS
`magneticDatasheetCommonModeChokeElectrical` format (subtype: "commonModeChoke").

REDEXPERT families 3 (power) and 23 (signal) are fetched to enrich each
matching entry with:
  - ratedVoltageAC / ratedVoltageDC
  - insulationTestVoltageAC (from REDEXPERT vt field, family 3)
  - impedancePoints at 100 MHz (from REDEXPERT impedance field, family 23)
  - dcResistances expanded from single dcResistance to per-winding array

Core / coil / distributors data is preserved unchanged.
Entries that are NOT common-mode chokes (WE-TORPFC, WE-HEPA, PFC inductors)
keep their existing inductor schema.

Usage:
  python scripts/migrate_cmc_schema.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from redexpert_client import RedexpertClient

REPO = Path(__file__).resolve().parents[1]
MAG_PATH = REPO / "TAS" / "data" / "magnetics.ndjson"


def _f(v) -> float | None:
    return v if isinstance(v, (int, float)) else None


def _is_cmc(magnetic: dict) -> bool:
    """True if this Würth magnetic entry is a common-mode choke."""
    mi = magnetic.get("manufacturerInfo", {})
    if "rth" not in mi.get("name", ""):
        return False
    di = mi.get("datasheetInfo", {})
    desc = di.get("part", {}).get("description", "")
    return "Common Mode" in desc


def _migrate_electrical(
    old_el: dict,
    re_data: dict | None,
    num_windings: int,
    fam23: bool,
) -> dict:
    """Convert one inductor electrical entry to commonModeChoke format."""
    new: dict = {"subtype": "commonModeChoke"}

    # ratedCurrents: keep existing or promote saturationCurrentPeak
    rc = old_el.get("ratedCurrents")
    if rc:
        new["ratedCurrents"] = rc
    elif old_el.get("saturationCurrentPeak") is not None:
        new["ratedCurrents"] = [old_el["saturationCurrentPeak"]]

    # Number of lines: REDEXPERT gives the actual value; fall back to coil count
    n_lines = int(re_data["lines"]) if re_data and re_data.get("lines") else max(2, num_windings)

    # dcResistance (single, old) → dcResistances (per line, new)
    old_dcr = old_el.get("dcResistance", {})
    if old_dcr:
        entry: dict = {}
        if old_dcr.get("nominal") is not None:
            entry["nominal"] = old_dcr["nominal"]
        if old_dcr.get("maximum") is not None:
            entry["maximum"] = old_dcr["maximum"]
        if entry:
            # One entry per line (symmetric winding → all lines identical)
            new["dcResistances"] = [entry] * n_lines

    # Enrich from REDEXPERT
    if re_data:
        v = _f(re_data.get("ratedVoltage"))
        if v is not None:
            assy = re_data.get("assemblingTechnology", "").upper()
            if assy == "THT" or not fam23:
                new["ratedVoltageAC"] = v
            else:
                new["ratedVoltageDC"] = v

        vt = _f(re_data.get("vt"))
        if vt is not None:
            new["insulationTestVoltageAC"] = vt

        imp = _f(re_data.get("impedance"))
        if imp is not None:
            # Signal CMCs (WE-CNSW, WE-SL, …) are impedance-spec'd at 100 MHz
            new["impedancePoints"] = [{"frequency": 100_000_000.0, "impedance": {"magnitude": imp}}]

    return new


def migrate(dry_run: bool) -> None:
    print("Fetching REDEXPERT CMC families 3 and 23 …")
    c = RedexpertClient()
    re3 = {str(p["orderCode"]): p for p in c.products("3").get("results", []) if p.get("orderCode")}
    re23 = {
        str(p["orderCode"]): p for p in c.products("23").get("results", []) if p.get("orderCode")
    }
    c.close()
    print(f"  Family 3 (power): {len(re3)} parts")
    print(f"  Family 23 (signal): {len(re23)} parts")

    lines_in = MAG_PATH.read_text().splitlines()
    lines_out: list[str] = []
    stats = {"total": 0, "migrated": 0, "enriched": 0, "skipped": 0}

    for raw in lines_in:
        raw = raw.strip()
        if not raw:
            lines_out.append(raw)
            continue

        row = json.loads(raw)
        mag = row.get("magnetic", row)
        stats["total"] += 1

        if not _is_cmc(mag):
            lines_out.append(raw)
            stats["skipped"] += 1
            continue

        mi = mag.get("manufacturerInfo", {})
        di = mi.get("datasheetInfo", {})
        electrical = di.get("electrical", [])

        # Only migrate entries still in old inductor format
        old_subtypes = [el.get("subtype") for el in electrical]
        if "commonModeChoke" in old_subtypes:
            lines_out.append(raw)
            stats["skipped"] += 1
            continue
        if "inductor" not in old_subtypes:
            lines_out.append(raw)
            stats["skipped"] += 1
            continue

        mpn = mi.get("reference") or di.get("part", {}).get("partNumber", "")
        re_data = re3.get(mpn) or re23.get(mpn)
        fam23 = mpn in re23

        # Count number of windings from coil description
        coil_fd = mag.get("coil", {}).get("functionalDescription", [])
        num_windings = max(2, len(coil_fd))

        new_electrical = []
        for el in electrical:
            if el.get("subtype") == "inductor":
                new_electrical.append(_migrate_electrical(el, re_data, num_windings, fam23))
            else:
                new_electrical.append(el)

        di["electrical"] = new_electrical
        stats["migrated"] += 1
        if re_data:
            stats["enriched"] += 1

        lines_out.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))

    print(f"\nMigration stats: {stats}")
    if not dry_run:
        MAG_PATH.write_text("\n".join(lines_out) + "\n")
        print(f"Wrote {len(lines_out)} lines to {MAG_PATH}")
    else:
        print("(dry-run — no changes written)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    migrate(args.dry_run)
