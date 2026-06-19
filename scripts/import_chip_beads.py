#!/usr/bin/env python3
"""
Import Würth chip bead records from /tmp/heaviside_chip_beads.ndjson into
TAS/data/magnetics.ndjson, validating each record against the MAS schema
and deduplicating by orderCode.

Usage:
    python scripts/import_chip_beads.py [--dry-run]

Prerequisites:
    Run scripts/export_chip_beads_to_heaviside.py in eb-modelling-heimdall first.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from heaviside.librarian.guards import GuardRejectionError, guard_component
from heaviside.librarian.tas import ValidationError

SOURCE     = Path("/tmp/heaviside_chip_beads.ndjson")
DEST       = REPO / "TAS" / "data" / "magnetics.ndjson"
QUARANTINE = REPO / "TAS" / "data" / "magnetics.quarantine_chip_beads.ndjson"

# Catalog inductors/beads have no real core/coil decomposition.
# Use the same convention as other WE catalog magnetics already in the DB.
_DUMMY_CORE = {
    "functionalDescription": {
        "type": "twoPieceSet",
        "material": "Dummy",
        "shape": "Dummy",
        "gapping": [],
    }
}
_DUMMY_COIL = {
    "bobbin": "Dummy",
    "functionalDescription": [
        {
            "name": "Dummy",
            "numberTurns": 1,
            "numberParallels": 1,
            "isolationSide": "primary",
            "wire": "Dummy",
        }
    ],
}


def _clean_record(rec: dict) -> dict:
    """Normalise a Heimdall-produced chip bead record for Heaviside's MAS validator.

    Fixes applied:
    - core/coil None → Dummy placeholder (schema requires object when key present)
    - manufacturerInfo.orderCode → .reference  (PEAS spine uses 'reference')
    - Drop None-valued optional fields: datasheetUrl, pinLength, distributorsInfo
    - Drop None optional scalars from electrical items (selfResonantFrequency, etc.)
    - Drop model if None
    """
    import copy
    rec = copy.deepcopy(rec)
    mag = rec.get("magnetic", {})

    # core/coil: None → Dummy placeholder
    if mag.get("core") is None:
        mag["core"] = _DUMMY_CORE
    if mag.get("coil") is None:
        mag["coil"] = _DUMMY_COIL

    # distributorsInfo: None or empty internal-only entry → drop
    if not mag.get("distributorsInfo"):
        mag.pop("distributorsInfo", None)

    mi = mag.get("manufacturerInfo", {})

    # orderCode → reference  (PEAS manufacturerInfo field name)
    if "orderCode" in mi:
        mi.setdefault("reference", mi.pop("orderCode"))

    # Drop None-valued optional string fields in manufacturerInfo
    for key in ("datasheetUrl", "family", "status", "description", "series"):
        if mi.get(key) is None:
            mi.pop(key, None)

    di = mi.get("datasheetInfo", {})

    # mechanical: drop None-valued optional fields
    mech = di.get("mechanical", {})
    for key in list(mech.keys()):
        v = mech[key]
        if v is None:
            del mech[key]
        elif isinstance(v, dict) and all(val is None for val in v.values()):
            del mech[key]

    # part: drop None-valued optional fields
    part = di.get("part", {})
    for key in list(part.keys()):
        if part[key] is None:
            del part[key]

    # electrical items: drop None optional scalars; filter schema-invalid points
    for elec_item in di.get("electrical", []):
        for key in list(elec_item.keys()):
            v = elec_item[key]
            if v is None:
                del elec_item[key]
            elif isinstance(v, dict) and all(val is None for val in v.values()):
                del elec_item[key]
        # impedancePoints: drop points where magnitude is non-numeric (Excel stub)
        if "impedancePoints" in elec_item:
            elec_item["impedancePoints"] = [
                p for p in elec_item["impedancePoints"]
                if isinstance((p.get("impedance") or {}).get("magnitude"), (int, float))
            ]
            if not elec_item["impedancePoints"]:
                del elec_item["impedancePoints"]
        # resistancePoints: schema requires resistance >= 0; negative values are
        # measurement artifacts near/above SRF — drop those points.
        if "resistancePoints" in elec_item:
            elec_item["resistancePoints"] = [
                p for p in elec_item["resistancePoints"]
                if p.get("resistance", 0) >= 0
            ]
            if not elec_item["resistancePoints"]:
                del elec_item["resistancePoints"]
        # reactancePoints: schema requires reactance >= 0; capacitive tail above SRF
        # is physically meaningful but not representable — drop those points.
        if "reactancePoints" in elec_item:
            elec_item["reactancePoints"] = [
                p for p in elec_item["reactancePoints"]
                if p.get("reactance", 0) >= 0
            ]
            if not elec_item["reactancePoints"]:
                del elec_item["reactancePoints"]

    # model: drop entirely if None (optional field)
    if di.get("model") is None:
        di.pop("model", None)

    rec["magnetic"] = mag
    return rec


def _mpn(rec: dict) -> str | None:
    mi = rec.get("magnetic", {}).get("manufacturerInfo", {})
    # Raw export from Heimdall uses "orderCode"; already-imported records use "reference"
    return mi.get("orderCode") or mi.get("reference")


def main(dry_run: bool = False) -> None:
    if not SOURCE.exists():
        raise SystemExit(
            f"Source not found: {SOURCE}\n"
            "Run scripts/export_chip_beads_to_heaviside.py in eb-modelling-heimdall first."
        )

    # Load existing magnetics orderCodes to deduplicate
    print("Loading existing magnetics orderCodes …")
    existing_mpns: set[str] = set()
    with open(DEST) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                mpn = _mpn(json.loads(line))
                if mpn:
                    existing_mpns.add(str(mpn))
            except json.JSONDecodeError:
                pass
    print(f"  Existing MPNs: {len(existing_mpns)}")

    # Validate and collect new records
    new_records: list[str] = []
    quarantine_records: list[dict] = []
    skipped_dupe = 0

    with open(SOURCE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            mpn = _mpn(rec)
            if not mpn:
                quarantine_records.append({**rec, "_reason": "missing orderCode"})
                continue
            if str(mpn) in existing_mpns:
                skipped_dupe += 1
                continue
            try:
                rec = _clean_record(rec)
                guard_component("magnetics", rec)
                new_records.append(json.dumps(rec))
                existing_mpns.add(str(mpn))
            except (ValidationError, GuardRejectionError) as e:
                quarantine_records.append({**rec, "_reason": str(e)})

    print(
        f"  New valid records: {len(new_records)}, "
        f"duplicates skipped: {skipped_dupe}, "
        f"quarantined: {len(quarantine_records)}"
    )

    if dry_run:
        print("DRY RUN — nothing written.")
        if new_records:
            print("Sample (first record):")
            print(new_records[0][:300])
        return

    if new_records:
        with open(DEST, "a") as f:
            for line in new_records:
                f.write(line + "\n")
        print(f"Appended {len(new_records)} chip bead records to {DEST}")

    if quarantine_records:
        with open(QUARANTINE, "a") as f:
            for rec in quarantine_records:
                f.write(json.dumps(rec) + "\n")
        print(f"Quarantined {len(quarantine_records)} records → {QUARANTINE}")
    else:
        print("No quarantine records.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
