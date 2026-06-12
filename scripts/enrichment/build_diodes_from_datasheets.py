#!/usr/bin/env python
"""Datasheet-sourced diode rows for the classes Digi-Key cannot supply.

Digi-Key's parametric feed has no I_F(AV) for TVS/Zener parts (so the
canonical converter rightly refuses them) and its v3 descriptions do not
carry the manufacturer's "ultrafast" designation for most series.  These
rows are therefore transcribed from the manufacturer SERIES datasheets
listed below — every value comes from a ratings table in the linked PDF,
fetched and read on 2026-06-12.  Nothing is estimated.

Sources (one entry per series):

* Nexperia BZX84 series Rev. 7 (2023-01-01), zeners, SOT-23:
  https://assets.nexperia.com/documents/data-sheet/BZX84_SER.pdf
  Tables 5/7/8/9: IF max 200 mA, VF <= 0.9 V @ 10 mA, Ptot 250 mW,
  Tj max 150 C, Rth(j-a) 500 K/W; per-type Vz min/max @ Izt, IR @ VR,
  Cd max @ 0 V.
* Vishay SMAJ5.0A thru SMAJ188CA Rev. 09-Jan-2024, doc 88390, TVS, SMA:
  https://www.vishay.com/docs/88390/smaj50a.pdf
  PPPM 400 W (10/1000 us), IFSM 40 A (8.3 ms, unidirectional),
  VF 3.5 V @ IF = 25 A (note 6, unidirectional), Tj max 150 C; per-part
  VBR min/max @ IT, VWM, ID @ VWM, IPPM, VC max.
* Vishay ES2A/ES2B/ES2C/ES2D Rev. 01-Apr-2020, doc 88587,
  "Surface-Mount Ultrafast Plastic Rectifier", SMB (DO-214AA):
  https://www.vishay.com/docs/88587/es2.pdf
  IF(AV) 2.0 A, IFSM 50 A, VF 0.90 V @ 2.0 A, trr 20 ns max,
  Qrr 10 nC @ 25 C, Cj 18 pF @ 4 V, IR 10 uA, Tj max 150 C, RthJA 75.
* Vishay BYW29/BYWF29/BYWB29 Rev. 25-Oct-2023, doc 88560,
  "Ultrafast Rectifier", TO-220AC / ITO-220AC / D2PAK:
  https://www.vishay.com/docs/88560/byw29200.pdf
  IF(AV) 8.0 A, IFSM 100 A, VF 1.3 V @ 20 A (25 C), trr 25 ns,
  IR 10 uA, Cj 45 pF @ 4 V, Tj max 150 C, RthJC 2.5 (BYW/BYWB),
  5.5 (BYWF).
* Diodes Incorporated US1A-US1M DS16008 Rev. 11-2 (Dec 2014),
  "1.0A SURFACE MOUNT ULTRA-FAST RECTIFIER", SMA:
  https://www.diodes.com/assets/Datasheets/ds16008.pdf
  Io 1.0 A, IFSM 30 A, VFM 1.0/1.3/1.7 V @ 1.0 A, IRM 5 uA,
  trr 50/75 ns, CT 20/10 pF @ 4 V, Tj max 150 C, RthJT 30.
* MCC UF4001 thru UF4007 Rev. D (2016-11-29),
  "1 Amp Ultra Fast Recovery Rectifier", DO-41 (Digi-Key-hosted copy):
  https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/2547/UF4001-UF4007%28DO-41%29-D.pdf
  IF(AV) 1 A, IFSM 30 A, VF 1.0/1.3/1.7 V @ 1.0 A, IR 5 uA,
  trr 50/75 ns, Cj 20/10 pF @ 4 V, Top max 125 C, RthJA 60.

NOT included (and why):
* Diodes Inc ES1A-ES1G (ds14001): datasheet title says "SUPER-FAST
  rectifier", not ultrafast — labelling it subType=ultrafast would be
  invention.
* MCC MUR405-MUR4100: the MCC datasheet is stamped "Obsolete".

Every row passes guard_component('diodes', row) BEFORE anything is
written; a failure aborts loudly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from heaviside.librarian.guards import guard_component

REPO = Path(__file__).resolve().parents[2]
DB_PATH = REPO / "TAS" / "data" / "diodes.ndjson"
PROVENANCE_PATH = REPO / "scripts" / "enrichment" / "diodes_refetch_provenance.ndjson"

BZX84_URL = "https://assets.nexperia.com/documents/data-sheet/BZX84_SER.pdf"
SMAJ_URL = "https://www.vishay.com/docs/88390/smaj50a.pdf"
ES2_URL = "https://www.vishay.com/docs/88587/es2.pdf"
BYW29_URL = "https://www.vishay.com/docs/88560/byw29200.pdf"
US1_URL = "https://www.diodes.com/assets/Datasheets/ds16008.pdf"
UF4_URL = (
    "https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/2547/"
    "UF4001-UF4007%28DO-41%29-D.pdf"
)


def _diode(
    *,
    manufacturer: str,
    mpn: str,
    datasheet_url: str,
    part: dict[str, Any],
    electrical: dict[str, Any],
    thermal: dict[str, Any],
    mechanical: dict[str, Any],
) -> dict[str, Any]:
    return {
        "semiconductor": {
            "diode": {
                "manufacturerInfo": {
                    "name": manufacturer,
                    "reference": mpn,
                    "status": "production",
                    "datasheetUrl": datasheet_url,
                    "datasheetInfo": {
                        "part": {"partNumber": mpn, "technology": "Si", **part},
                        "electrical": electrical,
                        "thermal": thermal,
                        "mechanical": mechanical,
                    },
                }
            }
        }
    }


ROWS: list[tuple[dict[str, Any], dict[str, Any]]] = []  # (row, provenance)


def add(row: dict[str, Any], cls: str, url: str, table: str) -> None:
    mpn = row["semiconductor"]["diode"]["manufacturerInfo"]["reference"]
    ROWS.append(
        (
            row,
            {
                "mpn": mpn,
                "class": cls,
                "source": "manufacturer-datasheet",
                "sourceUrl": url,
                "table": table,
            },
        )
    )


# ---------------------------------------------------------------------------
# Zeners — Nexperia BZX84 series (Tables 5, 7, 8, 9)
# type -> (per-selection {A/B/C: (Vz_min, Vz_max)}, Vz_nom, IR_max_uA, Cd_max_pF)
# ---------------------------------------------------------------------------

BZX84: dict[str, tuple[dict[str, tuple[float, float]], float, float, float]] = {
    "3V9": ({"A": (3.86, 3.94), "B": (3.82, 3.98), "C": (3.70, 4.10)}, 3.9, 3.0, 450),
    "4V3": ({"A": (4.25, 4.35), "B": (4.21, 4.39), "C": (4.00, 4.60)}, 4.3, 3.0, 450),
    "4V7": ({"A": (4.65, 4.75), "B": (4.61, 4.79), "C": (4.40, 5.00)}, 4.7, 3.0, 300),
    "5V1": ({"A": (5.04, 5.16), "B": (5.00, 5.20), "C": (4.80, 5.40)}, 5.1, 2.0, 300),
    "5V6": ({"A": (5.54, 5.66), "B": (5.49, 5.71), "C": (5.20, 6.00)}, 5.6, 1.0, 300),
    "6V2": ({"A": (6.13, 6.27), "B": (6.08, 6.32), "C": (5.80, 6.60)}, 6.2, 3.0, 200),
    "6V8": ({"A": (6.73, 6.87), "B": (6.66, 6.94), "C": (6.40, 7.20)}, 6.8, 2.0, 200),
    "9V1": ({"A": (9.00, 9.20), "B": (8.92, 9.28), "C": (8.50, 9.60)}, 9.1, 0.5, 150),
    "10": ({"A": (9.90, 10.10), "B": (9.80, 10.20), "C": (9.40, 10.60)}, 10, 0.2, 90),
    "11": ({"A": (10.89, 11.11), "B": (10.80, 11.20), "C": (10.40, 11.60)}, 11, 0.1, 85),
    "12": ({"A": (11.88, 12.12), "B": (11.80, 12.20), "C": (11.40, 12.70)}, 12, 0.1, 85),
    "13": ({"A": (12.87, 13.13), "B": (12.70, 13.30), "C": (12.40, 14.10)}, 13, 0.1, 80),
    "15": ({"A": (14.85, 15.15), "B": (14.70, 15.30), "C": (13.80, 15.60)}, 15, 0.05, 75),
}

for typ, (sels, vz_nom, ir_ua, cd_pf) in BZX84.items():
    for sel, (vz_min, vz_max) in sels.items():
        mpn = f"BZX84-{sel}{typ}"
        add(
            _diode(
                manufacturer="Nexperia",
                mpn=mpn,
                datasheet_url=BZX84_URL,
                part={"series": "BZX84", "subType": "zener", "case": "SOT-23"},
                electrical={
                    "reverseVoltage": vz_nom,
                    "forwardCurrent": 0.2,  # IF max, Table 5
                    "forwardVoltage": 0.9,  # VF max, Table 7
                    "forwardVoltageAt": 0.01,  # IF = 10 mA
                    "breakdownVoltage": {
                        "minimum": vz_min,
                        "nominal": vz_nom,
                        "maximum": vz_max,
                    },
                    "reverseLeakageCurrent": ir_ua * 1e-6,
                    "junctionCapacitance": cd_pf * 1e-12,
                    "junctionCapacitanceVr": 0,  # f = 1 MHz; VR = 0 V
                    "powerDissipation": 0.25,
                },
                thermal={
                    "junctionTemperatureMax": 150,
                    "thermalResistanceJunctionAmbient": 500,
                },
                mechanical={"assemblyType": "smt", "case": "SOT-23"},
            ),
            "zener_5_12V",
            BZX84_URL,
            f"Tables 8/9, type {typ} sel {sel}",
        )

# ---------------------------------------------------------------------------
# TVS — Vishay SMAJ unidirectional (Electrical characteristics table, p.2)
# mpn-suffix -> (VBR_min, VBR_max, IT_mA, VWM, ID_uA, IPPM_A, VC_V)
# ---------------------------------------------------------------------------

SMAJ: dict[str, tuple[float, float, float, float, float, float, float]] = {
    "5.0A": (6.40, 7.07, 10, 5.0, 800, 43.5, 9.2),
    "6.0A": (6.67, 7.37, 10, 6.0, 800, 38.8, 10.3),
    "6.5A": (7.22, 7.98, 10, 6.5, 500, 35.7, 11.2),
    "7.0A": (7.78, 8.60, 10, 7.0, 200, 33.3, 12.0),
    "7.5A": (8.33, 9.21, 1.0, 7.5, 100, 31.0, 12.9),
    "8.0A": (8.89, 9.83, 1.0, 8.0, 50, 29.4, 13.6),
    "8.5A": (9.44, 10.4, 1.0, 8.5, 10, 27.8, 14.4),
    "9.0A": (10.0, 11.1, 1.0, 9.0, 5.0, 26.0, 15.4),
    "10A": (11.1, 12.3, 1.0, 10, 1.0, 23.5, 17.0),
    "11A": (12.2, 13.5, 1.0, 11, 1.0, 22.0, 18.2),
    "12A": (13.3, 14.7, 1.0, 12, 1.0, 20.1, 19.9),
    "13A": (14.4, 15.9, 1.0, 13, 1.0, 18.6, 21.5),
    "14A": (15.6, 17.2, 1.0, 14, 1.0, 17.2, 23.2),
    "15A": (16.7, 18.5, 1.0, 15, 1.0, 16.4, 24.4),
    "16A": (17.8, 19.7, 1.0, 16, 1.0, 15.4, 26.0),
    "17A": (18.9, 20.9, 1.0, 17, 1.0, 14.5, 27.6),
    "18A": (20.0, 22.1, 1.0, 18, 1.0, 13.7, 29.2),
    "20A": (22.2, 24.5, 1.0, 20, 1.0, 12.3, 32.4),
    "22A": (24.4, 26.9, 1.0, 22, 1.0, 11.3, 35.5),
    "24A": (26.7, 29.5, 1.0, 24, 1.0, 10.3, 38.9),
    "26A": (28.9, 31.9, 1.0, 26, 1.0, 9.5, 42.1),
    "28A": (31.1, 34.4, 1.0, 28, 1.0, 8.8, 45.4),
    "30A": (33.3, 36.8, 1.0, 30, 1.0, 8.3, 48.4),
    "33A": (36.7, 40.6, 1.0, 33, 1.0, 7.5, 53.3),
    "36A": (40.0, 44.2, 1.0, 36, 1.0, 6.9, 58.1),
    "40A": (44.4, 49.1, 1.0, 40, 1.0, 6.2, 64.5),
}

for suffix, (vbr_min, vbr_max, _it_ma, vwm, id_ua, ippm, vc) in SMAJ.items():
    mpn = f"SMAJ{suffix}"
    add(
        _diode(
            manufacturer="Vishay",
            mpn=mpn,
            datasheet_url=SMAJ_URL,
            part={"series": "SMAJ", "subType": "tvs", "case": "DO-214AC"},
            electrical={
                # House convention for TVS (see existing Littelfuse SMBJ
                # rows): reverseVoltage = VBR max, forwardCurrent = IPPM.
                "reverseVoltage": vbr_max,
                "forwardCurrent": ippm,
                "surgeCurrent": 40,  # IFSM 8.3 ms, unidirectional
                "forwardVoltage": 3.5,  # note 6: VF = 3.5 V at IF = 25 A
                "forwardVoltageAt": 25,
                "standoffVoltage": vwm,
                "breakdownVoltage": {"minimum": vbr_min, "maximum": vbr_max},
                "clampingVoltage": vc,
                "peakPulseCurrent": ippm,
                "reverseLeakageCurrent": id_ua * 1e-6,
                "powerDissipation": 400,  # PPPM 10/1000 us
            },
            thermal={"junctionTemperatureMax": 150},
            mechanical={"assemblyType": "smt", "case": "DO-214AC"},
        ),
        "tvs_5_24V",
        SMAJ_URL,
        f"Electrical characteristics p.2, SMAJ{suffix}",
    )

# ---------------------------------------------------------------------------
# Ultrafast — Vishay ES2A/B/C/D
# ---------------------------------------------------------------------------

for mpn, vr in (("ES2A", 50), ("ES2B", 100), ("ES2C", 150), ("ES2D", 200)):
    add(
        _diode(
            manufacturer="Vishay",
            mpn=mpn,
            datasheet_url=ES2_URL,
            part={"subType": "ultrafast", "case": "DO-214AA"},
            electrical={
                "reverseVoltage": vr,
                "forwardCurrent": 2.0,
                "surgeCurrent": 50,
                "forwardVoltage": 0.90,
                "forwardVoltageAt": 2.0,
                "reverseLeakageCurrent": 10e-6,
                "reverseRecoveryTime": 20e-9,
                "reverseRecoveryCharge": 10e-9,
                "junctionCapacitance": 18e-12,
                "junctionCapacitanceVr": 4.0,
            },
            thermal={
                "junctionTemperatureMax": 150,
                "thermalResistanceJunctionAmbient": 75,
            },
            mechanical={"assemblyType": "smt", "case": "DO-214AA"},
        ),
        "ultrafast_200V",
        ES2_URL,
        f"Maximum ratings / electrical characteristics, {mpn}",
    )

# ---------------------------------------------------------------------------
# Ultrafast — Vishay BYW29 / BYWF29 / BYWB29
# ---------------------------------------------------------------------------

_BYW = [(f"BYW29-{v}", v, "TO-220AC", "tht", 2.5) for v in (50, 100, 150, 200)]
_BYW += [(f"BYWF29-{v}", v, "ITO-220AC", "tht", 5.5) for v in (50, 100, 150, 200)]
_BYW += [("BYWB29-200", 200, "TO-263AB", "smt", 2.5)]

for mpn, vr, case, assembly, rthjc in _BYW:
    add(
        _diode(
            manufacturer="Vishay",
            mpn=mpn,
            datasheet_url=BYW29_URL,
            part={"subType": "ultrafast", "case": case},
            electrical={
                "reverseVoltage": vr,
                "forwardCurrent": 8.0,
                "surgeCurrent": 100,
                "forwardVoltage": 1.3,
                "forwardVoltageAt": 20,
                "reverseLeakageCurrent": 10e-6,
                "reverseRecoveryTime": 25e-9,
                "junctionCapacitance": 45e-12,
                "junctionCapacitanceVr": 4.0,
            },
            thermal={
                "junctionTemperatureMax": 150,
                "thermalResistanceJunctionCase": rthjc,
            },
            mechanical={"assemblyType": assembly, "case": case},
        ),
        "ultrafast_200V",
        BYW29_URL,
        f"Maximum ratings / electrical characteristics, {mpn}",
    )

# ---------------------------------------------------------------------------
# Ultrafast — Diodes Incorporated US1A-US1M (orderable as US1x-13-F)
# device -> (VRRM, VFM, trr_ns, CT_pF)
# ---------------------------------------------------------------------------

US1: dict[str, tuple[float, float, float, float]] = {
    "US1A": (50, 1.0, 50, 20),
    "US1B": (100, 1.0, 50, 20),
    "US1D": (200, 1.0, 50, 20),
    "US1G": (400, 1.3, 50, 20),
    "US1J": (600, 1.7, 75, 10),
    "US1K": (800, 1.7, 75, 10),
    "US1M": (1000, 1.7, 75, 10),
}

for dev, (vr, vf, trr_ns, ct_pf) in US1.items():
    mpn = f"{dev}-13-F"
    add(
        _diode(
            manufacturer="Diodes Incorporated",
            mpn=mpn,
            datasheet_url=US1_URL,
            part={"subType": "ultrafast", "case": "SMA"},
            electrical={
                "reverseVoltage": vr,
                "forwardCurrent": 1.0,
                "surgeCurrent": 30,
                "forwardVoltage": vf,
                "forwardVoltageAt": 1.0,
                "reverseLeakageCurrent": 5e-6,
                "reverseRecoveryTime": trr_ns * 1e-9,
                "junctionCapacitance": ct_pf * 1e-12,
                "junctionCapacitanceVr": 4.0,
            },
            thermal={"junctionTemperatureMax": 150},
            mechanical={"assemblyType": "smt", "case": "SMA"},
        ),
        "ultrafast_200V",
        US1_URL,
        f"Maximum ratings / electrical characteristics, {dev}",
    )

# ---------------------------------------------------------------------------
# Ultrafast — MCC UF4001-UF4007
# mpn -> (VRRM, VF, trr_ns, CJ_pF)
# ---------------------------------------------------------------------------

UF4: dict[str, tuple[float, float, float, float]] = {
    "UF4001": (50, 1.0, 50, 20),
    "UF4002": (100, 1.0, 50, 20),
    "UF4003": (200, 1.0, 50, 20),
    "UF4004": (400, 1.3, 50, 20),
    "UF4005": (600, 1.7, 75, 10),
    "UF4006": (800, 1.7, 75, 10),
    "UF4007": (1000, 1.7, 75, 10),
}

for mpn, (vr, vf, trr_ns, cj_pf) in UF4.items():
    add(
        _diode(
            manufacturer="Micro Commercial Components",
            mpn=mpn,
            datasheet_url=UF4_URL,
            part={"subType": "ultrafast", "case": "DO-41"},
            electrical={
                "reverseVoltage": vr,
                "forwardCurrent": 1.0,
                "surgeCurrent": 30,
                "forwardVoltage": vf,
                "forwardVoltageAt": 1.0,
                "reverseLeakageCurrent": 5e-6,
                "reverseRecoveryTime": trr_ns * 1e-9,
                "junctionCapacitance": cj_pf * 1e-12,
                "junctionCapacitanceVr": 4.0,
            },
            thermal={
                "junctionTemperatureMax": 125,
                "thermalResistanceJunctionAmbient": 60,
            },
            mechanical={"assemblyType": "tht", "case": "DO-41"},
        ),
        "ultrafast_200V",
        UF4_URL,
        f"Maximum ratings / electrical characteristics, {mpn}",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    existing: set[str] = set()
    with DB_PATH.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            existing.add(
                rec["semiconductor"]["diode"]["manufacturerInfo"]["datasheetInfo"][
                    "part"
                ]["partNumber"]
            )

    to_write: list[tuple[dict[str, Any], dict[str, Any]]] = []
    dupes: list[str] = []
    seen: set[str] = set()
    for row, prov in ROWS:
        mpn = prov["mpn"]
        if mpn in seen:
            raise SystemExit(f"internal duplicate in builder: {mpn}")
        seen.add(mpn)
        if mpn in existing:
            dupes.append(mpn)
            continue
        # HARD GATE — abort on any failure, never skip.
        guard_component("diodes", row)
        to_write.append((row, prov))

    by_class: dict[str, int] = {}
    for _, prov in to_write:
        by_class[prov["class"]] = by_class.get(prov["class"], 0) + 1
    print("validated rows by class:", json.dumps(by_class, sort_keys=True))
    if dupes:
        print(f"already in DB (skipped as duplicates): {dupes}")

    if args.dry_run:
        print(f"dry run — {len(to_write)} rows NOT written")
        return 0
    if not to_write:
        raise SystemExit("nothing to write")

    with DB_PATH.open("a") as fh:
        for row, _ in to_write:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    with PROVENANCE_PATH.open("a") as fh:
        for _, prov in to_write:
            fh.write(json.dumps(prov, ensure_ascii=False) + "\n")
    print(f"appended {len(to_write)} rows to {DB_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
