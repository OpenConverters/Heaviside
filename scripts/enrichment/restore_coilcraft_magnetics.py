#!/usr/bin/env python
"""Restore the 4 quarantined Coilcraft power-inductor stubs with full rows.

The stubs in TAS/data/magnetics.quarantine_stubs.ndjson had only
manufacturerInfo (no core/coil) and partially WRONG electrical values
(e.g. SLC7649S-700KLC stored 700 nH where the datasheet says 70 nH).
These rows are rebuilt from the Coilcraft series datasheets, mirroring
the structure of the existing commercial XGL/XEL rows in
magnetics.ndjson (Dummy core/coil convention + datasheetInfo), with a
part.partNumber so the insert guard's real-MPN requirement holds.

Datasheets (fetched + transcribed 2026-06-12):

* XGL6060 Doc 1621 rev 02/19/26
  https://www.coilcraft.com/getmedia/329fe97c-7311-4726-9bf3-37718f42b168/xgl6060.pdf
  XGL6060-822ME_: 8.2 uH +/-20 %, DCR 15.2/16.8 mOhm typ/max, SRF 14 MHz,
  Isat 4.1/6.2/8.1 A (10/20/30 % drop), Irms 8.0/11.0 A (20/40 C rise);
  body 6.51 x 6.71 mm, height 6.1 mm max; composite core, shielded.
* XGL5050 Doc 1577 rev 02/19/26
  https://www.coilcraft.com/getmedia/348b2df6-54b3-4579-8737-e8367b6fa367/xgl5050.pdf
  XGL5050-153ME_: 15.0 uH +/-20 %, DCR 49.8/54.9 mOhm, SRF 13 MHz,
  Isat 2.0/2.9/3.9 A, Irms 3.4/4.6 A; body 5.28 x 5.48 mm, height 5.10 max.
* SLC7649 Doc 481 rev 03/11/26
  https://www.coilcraft.com/getmedia/0d701ca2-6dd3-4654-9f29-37f6d8a82fc7/slc7649.pdf
  SLC7649S-700KL_: 70 nH +/-10 %, DCR 0.17 mOhm +/-5 %, SRF 750 MHz typ,
  Isat 65 A (20 % typ drop), Irms 56/74 A (20/40 C rise);
  body 6.75 x 7.3 mm, height 4.6 mm; ferrite core, shielded.
* XEL4030 Doc 1321 rev 02/19/26
  https://www.coilcraft.com/pdfs/xel4030.pdf
  XEL4030-472ME_: 4.7 uH +/-20 %, DCR 40.0/44.1 mOhm typ/max, SRF 30 MHz,
  Isat 4.6 A (30 % typ drop), Irms 3.9/5.1 A (20/40 C rise);
  body 4.0 x 4.0 mm, height 3.10 mm max; composite core, shielded.

Conventions (consistent across the four rows, documented here because
the legacy family rows are ambiguous):
* ratedCurrent      = Irms at 40 C rise (Coilcraft's headline rating:
                      "Ambient temperature ... with (40 C rise) Irms").
* saturationCurrentPeak = Isat at 30 % inductance drop for XGL
                      (the deepest column), the single published Isat
                      for XEL4030 (30 % typ) and SLC7649 (20 % typ).
* dcResistance      = maximum where the datasheet has a max column;
                      SLC7649 publishes nominal +/-5 %, stored as
                      nominal + computed maximum.
* inductance        = nominal only (family-row convention).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from heaviside.librarian.guards import guard_component

REPO = Path(__file__).resolve().parents[2]
DB_PATH = REPO / "TAS" / "data" / "magnetics.ndjson"
QUARANTINE_PATH = REPO / "TAS" / "data" / "magnetics.quarantine_stubs.ndjson"
PROVENANCE_PATH = REPO / "scripts" / "enrichment" / "diodes_refetch_provenance.ndjson"

XGL6060_URL = "https://www.coilcraft.com/getmedia/329fe97c-7311-4726-9bf3-37718f42b168/xgl6060.pdf"
XGL5050_URL = "https://www.coilcraft.com/getmedia/348b2df6-54b3-4579-8737-e8367b6fa367/xgl5050.pdf"
SLC7649_URL = "https://www.coilcraft.com/getmedia/0d701ca2-6dd3-4654-9f29-37f6d8a82fc7/slc7649.pdf"
XEL4030_URL = "https://www.coilcraft.com/pdfs/xel4030.pdf"

DUMMY_CORE = {
    "functionalDescription": {
        "type": "twoPieceSet",
        "material": "Dummy",
        "shape": "Dummy",
        "gapping": [],
    }
}
DUMMY_COIL = {
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


def _row(
    *,
    mpn: str,
    description: str,
    material: str,
    datasheet_url: str,
    electrical: dict[str, Any],
    mechanical: dict[str, Any],
) -> dict[str, Any]:
    return {
        "magnetic": {
            "manufacturerInfo": {
                "name": "Coilcraft",
                "reference": mpn,
                "status": "production",
                "datasheetUrl": datasheet_url,
                "datasheetInfo": {
                    "part": {
                        "partNumber": mpn,
                        "description": description,
                        "material": material,
                        "shielded": True,
                    },
                    "electrical": electrical,
                    "mechanical": mechanical,
                },
            },
            "core": DUMMY_CORE,
            "coil": DUMMY_COIL,
        }
    }


ROWS: list[tuple[dict[str, Any], dict[str, Any]]] = [
    (
        _row(
            mpn="XGL6060-822MEC",
            description="Coilcraft XGL6060 8.2uH Isat=8.1A Irms=11.0A",
            material="Composite",
            datasheet_url=XGL6060_URL,
            electrical={
                "inductance": {"nominal": 8.2e-06},
                "dcResistance": {"maximum": 0.0168},
                "ratedCurrent": 11.0,
                "saturationCurrentPeak": 8.1,
                "selfResonantFrequency": 14e6,
            },
            mechanical={
                "length": {"nominal": 0.00651},
                "width": {"nominal": 0.00671},
                "height": {"maximum": 0.0061},
            },
        ),
        {
            "mpn": "XGL6060-822MEC",
            "class": "coilcraft_magnetics_restore",
            "source": "manufacturer-datasheet",
            "sourceUrl": XGL6060_URL,
            "table": "Doc 1621-2 electrical table, XGL6060-822ME_",
        },
    ),
    (
        _row(
            mpn="XGL5050-153MEC",
            description="Coilcraft XGL5050 15.0uH Isat=3.9A Irms=4.6A",
            material="Composite",
            datasheet_url=XGL5050_URL,
            electrical={
                "inductance": {"nominal": 1.5e-05},
                "dcResistance": {"maximum": 0.0549},
                "ratedCurrent": 4.6,
                "saturationCurrentPeak": 3.9,
                "selfResonantFrequency": 13e6,
            },
            mechanical={
                "length": {"nominal": 0.00528},
                "width": {"nominal": 0.00548},
                "height": {"maximum": 0.0051},
            },
        ),
        {
            "mpn": "XGL5050-153MEC",
            "class": "coilcraft_magnetics_restore",
            "source": "manufacturer-datasheet",
            "sourceUrl": XGL5050_URL,
            "table": "Doc 1577-2 electrical table, XGL5050-153ME_",
        },
    ),
    (
        _row(
            mpn="SLC7649S-700KLC",
            description="Coilcraft SLC7649S 70nH Isat=65A Irms=74A",
            material="Ferrite",
            datasheet_url=SLC7649_URL,
            electrical={
                # Quarantined stub said 700 nH — the datasheet table says
                # 70 nH (the stub also carried Irms 39 A, matching nothing
                # in Doc 481 rev 03/11/26).
                "inductance": {"nominal": 7.0e-08},
                "dcResistance": {"nominal": 0.00017, "maximum": 0.0001785},
                "ratedCurrent": 74,
                "saturationCurrentPeak": 65,
                "selfResonantFrequency": 750e6,
            },
            mechanical={
                "length": {"nominal": 0.00675},
                "width": {"nominal": 0.0073},
                "height": {"nominal": 0.0046},
            },
        ),
        {
            "mpn": "SLC7649S-700KLC",
            "class": "coilcraft_magnetics_restore",
            "source": "manufacturer-datasheet",
            "sourceUrl": SLC7649_URL,
            "table": "Doc 481-1 electrical table, SLC7649S-700KL_",
        },
    ),
    (
        _row(
            mpn="XEL4030-472MEB",
            description="Coilcraft XEL4030 4.7uH Isat=4.6A Irms=5.1A",
            material="Composite",
            datasheet_url=XEL4030_URL,
            electrical={
                "inductance": {"nominal": 4.7e-06},
                "dcResistance": {"maximum": 0.0441},
                "ratedCurrent": 5.1,
                "saturationCurrentPeak": 4.6,
                "selfResonantFrequency": 30e6,
            },
            mechanical={
                "length": {"nominal": 0.004},
                "width": {"nominal": 0.004},
                "height": {"maximum": 0.0031},
            },
        ),
        {
            "mpn": "XEL4030-472MEB",
            "class": "coilcraft_magnetics_restore",
            "source": "manufacturer-datasheet",
            "sourceUrl": XEL4030_URL,
            "table": "Doc 1321-1 electrical table, XEL4030-472ME_",
        },
    ),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    refs = {prov["mpn"] for _, prov in ROWS}

    # Validate everything BEFORE touching any file.
    for row, _prov in ROWS:
        guard_component("magnetics", row)
    print(f"all {len(ROWS)} rows pass guard_component('magnetics', ...)")

    # The restored references must not already exist in the main DB.
    with DB_PATH.open() as fh:
        for n, line in enumerate(fh, 1):
            for ref in refs:
                if f'"{ref}"' in line:
                    raise SystemExit(f"{ref} already present in magnetics.ndjson L{n}")

    quarantine_lines = [ln for ln in QUARANTINE_PATH.read_text().splitlines() if ln.strip()]
    remaining = []
    removed = []
    for ln in quarantine_lines:
        ref = json.loads(ln)["magnetic"]["manufacturerInfo"]["reference"]
        (removed if ref in refs else remaining).append(ref)
    missing = refs - set(removed)
    if missing:
        raise SystemExit(f"expected stubs not found in quarantine file: {missing}")

    if args.dry_run:
        print("dry run — nothing written")
        return 0

    with DB_PATH.open("a") as fh:
        for row, _ in ROWS:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    # Drop the restored stubs from the quarantine file.
    kept = [
        ln
        for ln in quarantine_lines
        if json.loads(ln)["magnetic"]["manufacturerInfo"]["reference"] not in refs
    ]
    QUARANTINE_PATH.write_text("".join(ln + "\n" for ln in kept))
    with PROVENANCE_PATH.open("a") as fh:
        for _, prov in ROWS:
            fh.write(json.dumps(prov, ensure_ascii=False) + "\n")
    print(f"restored {len(ROWS)} rows to {DB_PATH}; quarantine now has {len(kept)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
