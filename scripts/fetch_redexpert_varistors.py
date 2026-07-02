#!/usr/bin/env python3
"""
Fetch Würth Elektronik varistor (WE-VD) records from REDEXPERT MCP and
write MAS-validated records to TAS/data/varistors.ndjson.

Usage:
    python scripts/fetch_redexpert_varistors.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import contextlib

from redexpert_client import RedexpertClient

from heaviside.librarian.guards import GuardRejectionError, guard_component
from heaviside.librarian.tas import ValidationError

DEST = REPO / "TAS" / "data" / "varistors.ndjson"
QUARANTINE = REPO / "TAS" / "data" / "varistors.quarantine_redexpert.ndjson"
FAMILY_ID = "27"  # Surge Circuit Protection (WE-VD MOVs)
PAGE_SIZE = 100


def _technology(series: str) -> str:
    """Map WE series name to RAS technology enum."""
    s = (series or "").upper()
    if "ML" in s or "MULTI" in s:
        return "multiLayer"
    return "metalOxide"


def _convert(p: dict) -> dict | None:
    """Convert a REDEXPERT varistor product dict to a RAS varistor record."""
    mpn = p.get("orderCode")
    if not mpn:
        return None

    tol_pct = p.get("voltageVarTol")  # e.g. 10 → ±10 %
    v_nom = p.get("voltageVar")  # varistor voltage at 1 mA, V

    if (
        v_nom is None
        or p.get("voltageClamp") is None
        or p.get("currentMax") is None
        or p.get("energyMax") is None
    ):
        return None  # missing required fields

    varistor_voltage: dict = {"nominal": v_nom}
    if tol_pct is not None:
        varistor_voltage["minimum"] = round(v_nom * (1 - tol_pct / 100), 4)
        varistor_voltage["maximum"] = round(v_nom * (1 + tol_pct / 100), 4)

    electrical: dict = {
        "varistorVoltage": varistor_voltage,
        "clampingVoltage": p["voltageClamp"],
        # Clamping is spec'd at the rated peak surge current for MOVs
        "clampingCurrent": p["currentMax"],
        "peakSurgeCurrent": p["currentMax"],
        "surgeWaveform": "8/20",  # WE-VD standard waveform
        "energyAbsorption": p["energyMax"],
    }
    if p.get("voltageRms") is not None:
        electrical["maxContinuousAcVoltage"] = p["voltageRms"]
    if p.get("voltageDc") is not None:
        electrical["maxContinuousDcVoltage"] = p["voltageDc"]
    if p.get("powerMax") is not None:
        pass  # no powerDissipation field in RAS varistor schema

    # Mechanical — disc varistors are THT (only schema-allowed fields)
    mechanical: dict = {"assemblyType": "tht", "shapeType": "Disc"}
    disc_d = p.get("discDiameterMax")
    if disc_d is not None:
        mechanical["diameter"] = {"nominal": disc_d}
    # lead diameter and pin spacing are not in the RAS mechanical schema

    # Temperature
    t_max = p.get("temperatureOpMax")
    t_min = p.get("temperatureOpMin")
    thermal: dict | None = None
    if t_max is not None or t_min is not None:
        op: dict = {}
        if t_min is not None:
            op["minimum"] = t_min
        if t_max is not None:
            op["maximum"] = t_max
        thermal = {"operatingTemperature": op}

    series = p.get("series", "")
    ds_url = p.get("datasheet")

    part: dict = {
        "partNumber": str(mpn),
        "series": series,
        "technology": _technology(series),
    }
    cert = p.get("ulcCert") or p.get("vdeCert") or p.get("csaCert")
    if cert:
        part["matchcodeDescription"] = (
            f"{p.get('voltageRms', '')}Vrms / {int(p['currentMax'])}A / "
            f"{p.get('discDiameterMax', p.get('size', ''))} disc MOV"
        )

    ds_info: dict = {"part": part, "electrical": electrical, "mechanical": mechanical}
    if thermal:
        ds_info["thermal"] = thermal

    mi: dict = {
        "name": "Würth Elektronik",
        "reference": str(mpn),
        "datasheetInfo": ds_info,
    }
    if series:
        mi["family"] = series
    if ds_url:
        mi["datasheetUrl"] = ds_url

    return {"varistor": {"manufacturerInfo": mi}}


def _mpn(rec: dict) -> str | None:
    return rec.get("varistor", {}).get("manufacturerInfo", {}).get("reference")


def main(dry_run: bool = False) -> None:
    client = RedexpertClient()

    # Load existing MPNs to deduplicate
    existing: set[str] = set()
    if DEST.exists():
        with open(DEST) as f:
            for line in f:
                line = line.strip()
                if line:
                    with contextlib.suppress(json.JSONDecodeError):
                        existing.add(str(_mpn(json.loads(line)) or ""))
    print(f"Existing varistor MPNs: {len(existing)}")

    # Fetch all pages from REDEXPERT
    print(f"Fetching REDEXPERT family {FAMILY_ID} …")
    all_products: list[dict] = []
    offset = 0
    while True:
        result = client.call_tool(
            "get_products",
            {
                "module": FAMILY_ID,
                "sortBy": "None",
            },
        )
        products = result.get("results", [])
        total = result.get("count", 0)
        all_products.extend(products)
        offset += len(products)
        print(f"  fetched {offset}/{total}")
        if offset >= total or not products:
            break

    print(f"Total REDEXPERT varistor products: {len(all_products)}")

    new_records: list[str] = []
    quarantine: list[dict] = []

    for p in all_products:
        mpn = str(p.get("orderCode") or "")
        if mpn in existing:
            continue
        rec = _convert(p)
        if rec is None:
            quarantine.append({**p, "_reason": "missing required fields"})
            continue
        try:
            guard_component("varistors", rec)
            new_records.append(json.dumps(rec))
            existing.add(mpn)
        except (ValidationError, GuardRejectionError) as e:
            quarantine.append({**p, "_reason": str(e)})

    print(f"Valid new records: {len(new_records)}, quarantined: {len(quarantine)}")

    if dry_run:
        print("DRY RUN — nothing written.")
        if new_records:
            print("Sample:", new_records[0][:300])
        return

    if new_records:
        with open(DEST, "a") as f:
            for line in new_records:
                f.write(line + "\n")
        print(f"Appended {len(new_records)} records → {DEST}")

    if quarantine:
        with open(QUARANTINE, "a") as f:
            for item in quarantine:
                f.write(json.dumps(item) + "\n")
        print(f"Quarantined {len(quarantine)} → {QUARANTINE}")
    else:
        print("No quarantine.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
