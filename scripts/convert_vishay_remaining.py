#!/usr/bin/env python3
"""Process all remaining Vishay HTML files (inductors, MOSFETs, diodes, modules)."""

import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path("/home/alf/OpenConverters/Heaviside")
sys.path.insert(0, str(REPO_ROOT))

INPUT_DIR = Path("/home/alf/Downloads/Vishay")
OUTPUT_DIR = REPO_ROOT / "TAS" / "data"


def extract_json_from_html(html_path: Path) -> list[dict]:
    """Extract Next.js page props JSON from HTML file."""
    with open(html_path, encoding="utf-8") as f:
        html = f.read()
    scripts = re.findall(r"<script[^\u003e]*\u003e(.*?)\u003c/script\u003e", html, re.DOTALL)
    if not scripts:
        return []
    data = json.loads(max(scripts, key=len))
    return data.get("props", {}).get("pageProps", {}).get("paramResults", [])


def write_ndjson(documents: list[dict[str, Any]], output_path: Path) -> None:
    with open(output_path, "a", encoding="utf-8") as f:
        for doc in documents:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")


def parse_temperature(temp_str: str) -> dict[str, float] | None:
    if not temp_str:
        return None
    match = re.findall(r"([+-]?\d+)", temp_str)
    if len(match) >= 2:
        return {"minimum": float(match[0]), "maximum": float(match[1])}
    return None


def parse_case_size(size_str: str) -> tuple:
    if not size_str or size_str.lower() == "n/a":
        return None, None, None

    smd_map = {
        "01005": (0.4e-3, 0.2e-3, None),
        "0201": (0.6e-3, 0.3e-3, None),
        "0402": (1.0e-3, 0.5e-3, None),
        "0603": (1.6e-3, 0.8e-3, None),
        "0805": (2.0e-3, 1.25e-3, None),
        "1206": (3.2e-3, 1.6e-3, None),
        "1210": (3.2e-3, 2.5e-3, None),
        "1218": (3.2e-3, 4.6e-3, None),
        "1812": (4.5e-3, 3.2e-3, None),
        "2010": (5.0e-3, 2.5e-3, None),
        "2512": (6.35e-3, 3.18e-3, None),
        "3637": (3.81e-3, 3.43e-3, None),
        "3920": (10.0e-3, 5.0e-3, None),
        "5930": (15.0e-3, 7.6e-3, None),
        "1020": (2.5e-3, 5.0e-3, None),
    }

    size_clean = size_str.strip()
    if size_clean in smd_map:
        return smd_map[size_clean]

    match = re.match(r"(\d+(?:\.\d+)?)[xX](\d+(?:\.\d+)?)", size_clean)
    if match:
        return float(match.group(1)) * 1e-3, float(match.group(2)) * 1e-3, None

    return None, None, None


def make_inductor_document(product: dict) -> dict[str, Any] | None:
    """Convert Vishay inductor to MAS magnetic document."""
    order_code = product.get("P1001", "")
    if not order_code or order_code.lower() == "n/a":
        return None

    series = product.get("P1001", "")

    # Inductance
    inductance_uh = product.get("T1023")
    if inductance_uh is None:
        return None
    try:
        inductance_h = float(inductance_uh) * 1e-6
    except (ValueError, TypeError):
        return None

    # DCR
    dcr = product.get("P8838")
    if dcr is not None:
        try:
            dcr = float(dcr)
        except (ValueError, TypeError):
            dcr = None

    # Rated current
    rated_current = product.get("T1141")
    if rated_current is not None:
        try:
            rated_current = float(rated_current) / 1000  # mA to A
        except (ValueError, TypeError):
            rated_current = None

    # Saturation current
    sat_current = product.get("T1144")
    if sat_current is not None:
        try:
            sat_current = float(sat_current) / 1000
        except (ValueError, TypeError):
            sat_current = None

    # Size
    size_str = product.get("P4937", "")
    length, width, height = parse_case_size(size_str)

    # Temperature
    temp = parse_temperature(product.get("P8363", ""))

    doc = {
        "magnetic": {
            "manufacturerInfo": {
                "name": "Vishay",
                "reference": order_code,
                "status": "production",
                "family": series,
                "datasheetUrl": f"https://www.vishay.com/en/search/?type=inv&query={order_code}",
                "datasheetInfo": {
                    "part": {
                        "partNumber": order_code,
                        "family": series,
                        "description": product.get("MODEL", ""),
                    },
                    "electrical": {
                        "inductance": {"nominal": inductance_h},
                    },
                },
            },
            "core": {
                "functionalDescription": {
                    "type": "twoPieceSet",
                    "material": "Ferrite",
                    "shape": "SMD",
                    "gapping": [],
                }
            },
            "coil": {
                "bobbin": "Dummy",
                "functionalDescription": [
                    {
                        "name": "winding1",
                        "numberTurns": 1,
                        "numberParallels": 1,
                        "isolationSide": "primary",
                        "wire": "round",
                        "connections": [
                            {"type": "pin", "pinName": "1", "direction": "input"},
                            {"type": "pin", "pinName": "2", "direction": "output"},
                        ],
                    }
                ],
            },
        }
    }

    # Add optional electrical fields
    if dcr is not None:
        doc["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"]["dcResistance"] = {
            "maximum": dcr
        }
    if rated_current is not None:
        doc["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"]["ratedCurrent"] = (
            rated_current
        )
    if sat_current is not None:
        doc["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"][
            "saturationCurrentPeak"
        ] = sat_current

    # Thermal
    if temp:
        doc["magnetic"]["manufacturerInfo"]["datasheetInfo"]["thermal"] = {
            "operatingTemperature": temp
        }

    # Mechanical
    mechanical = {}
    if length:
        mechanical["length"] = {"nominal": length}
    if width:
        mechanical["width"] = {"nominal": width}
    if height:
        mechanical["height"] = {"nominal": height}

    mounting = product.get("P1007", "") or ""
    assembly = "smt" if "Surface" in mounting else "tht"
    mechanical["assemblyType"] = assembly

    if mechanical:
        doc["magnetic"]["manufacturerInfo"]["datasheetInfo"]["mechanical"] = mechanical

    return doc


def make_mosfet_document(product: dict) -> dict[str, Any] | None:
    """Convert Vishay MOSFET to SAS mosfet document."""
    part_number = product.get("P1001", "")
    if not part_number or part_number.lower() == "n/a":
        return None

    series = part_number
    package = product.get("PACKAGES", "")

    # Try to parse key parameters
    vds = product.get("P7002")
    if vds is not None:
        try:
            vds = float(vds)
        except (ValueError, TypeError):
            vds = None

    ids = product.get("P7012")
    if ids is not None:
        try:
            ids = float(ids)
        except (ValueError, TypeError):
            ids = None

    rds_on = product.get("P7013")
    if rds_on is not None:
        try:
            rds_on = float(rds_on)
        except (ValueError, TypeError):
            rds_on = None

    vgs_max = product.get("P7008")
    if vgs_max is not None:
        try:
            vgs_max = float(vgs_max)
        except (ValueError, TypeError):
            vgs_max = None

    pd = product.get("P7016")
    if pd is not None:
        try:
            pd = float(pd)
        except (ValueError, TypeError):
            pd = None

    doc = {
        "mosfet": {
            "manufacturerInfo": {
                "name": "Vishay",
                "reference": part_number,
                "status": "production",
                "family": series,
                "datasheetUrl": f"https://www.vishay.com/en/search/?type=inv&query={part_number}",
                "datasheetInfo": {
                    "part": {
                        "partNumber": part_number,
                        "series": series,
                        "technology": "Si",
                        "subType": "nChannel" if product.get("P7001") == "N" else "pChannel",
                        "case": package,
                    },
                    "electrical": {},
                },
            }
        }
    }

    electrical = doc["mosfet"]["manufacturerInfo"]["datasheetInfo"]["electrical"]

    if vds is not None:
        electrical["drainSourceVoltage"] = vds
    if ids is not None:
        electrical["continuousDrainCurrent"] = ids
    if rds_on is not None:
        electrical["onResistance"] = rds_on
    if vgs_max is not None:
        electrical["gateSourceVoltageMax"] = vgs_max
    if pd is not None:
        electrical["powerDissipation"] = pd

    if not electrical:
        return None

    return doc


def make_diode_document(product: dict) -> dict[str, Any] | None:
    """Convert Vishay diode module to SAS semiconductor document."""
    part_number = product.get("P1001", "")
    if not part_number or part_number.lower() == "n/a":
        return None

    series = part_number

    # Try to get key specs
    vrrm = product.get("P6005")
    if vrrm is not None:
        try:
            vrrm = float(vrrm)
        except (ValueError, TypeError):
            vrrm = None

    if_avg = product.get("P6384")
    if if_avg is not None:
        try:
            if_avg = float(if_avg)
        except (ValueError, TypeError):
            if_avg = None

    package = product.get("P8501", "") or product.get("P6000", "")

    doc = {
        "semiconductor": {
            "diode": {
                "manufacturerInfo": {
                    "name": "Vishay",
                    "reference": part_number,
                    "status": "production",
                    "family": series,
                    "datasheetUrl": f"https://www.vishay.com/en/search/?type=inv&query={part_number}",
                    "datasheetInfo": {
                        "part": {
                            "partNumber": part_number,
                            "series": series,
                            "technology": "Si",
                            "subType": "rectifier",
                            "case": package,
                        },
                        "electrical": {},
                    },
                }
            }
        }
    }

    electrical = doc["semiconductor"]["diode"]["manufacturerInfo"]["datasheetInfo"]["electrical"]

    if vrrm is not None:
        electrical["reverseVoltage"] = vrrm
    if if_avg is not None:
        electrical["forwardCurrent"] = if_avg

    if not electrical:
        return None

    return doc


def make_igbt_document(product: dict) -> dict[str, Any] | None:
    """Convert Vishay IGBT module to SAS semiconductor document."""
    part_number = product.get("P1001", "")
    if not part_number or part_number.lower() == "n/a":
        return None

    series = part_number

    vces = product.get("P6358")
    if vces is not None:
        try:
            vces = float(vces)
        except (ValueError, TypeError):
            vces = None

    ic = product.get("P6384")
    if ic is not None:
        try:
            ic = float(ic)
        except (ValueError, TypeError):
            ic = None

    doc = {
        "semiconductor": {
            "igbt": {
                "manufacturerInfo": {
                    "name": "Vishay",
                    "reference": part_number,
                    "status": "production",
                    "family": series,
                    "datasheetUrl": f"https://www.vishay.com/en/search/?type=inv&query={part_number}",
                    "datasheetInfo": {
                        "part": {
                            "partNumber": part_number,
                            "series": series,
                            "technology": "Si",
                            "case": product.get("P6311", ""),
                        },
                        "electrical": {},
                    },
                }
            }
        }
    }

    electrical = doc["semiconductor"]["igbt"]["manufacturerInfo"]["datasheetInfo"]["electrical"]

    if vces is not None:
        electrical["collectorEmitterVoltage"] = vces
    if ic is not None:
        electrical["continuousCollectorCurrent"] = ic

    if not electrical:
        return None

    return doc


def process_file(filename: str, output_file: str, converter_func) -> int:
    html_path = INPUT_DIR / filename
    if not html_path.exists():
        print(f"  SKIPPED: {filename} not found")
        return 0

    products = extract_json_from_html(html_path)
    if not products:
        print(f"  SKIPPED: {filename} has no products")
        return 0

    results = []
    for product in products:
        doc = converter_func(product)
        if doc:
            results.append(doc)

    if results:
        output_path = OUTPUT_DIR / output_file
        write_ndjson(results, output_path)
        print(f"  PROCESSED: {len(results)} items from {filename}")
        return len(results)

    print(f"  WARNING: 0 valid items from {filename}")
    return 0


def main():
    print("=" * 70)
    print("Vishay Remaining Products to TAS Converter")
    print("=" * 70)

    total_inductors = 0
    total_mosfets = 0
    total_diodes = 0
    total_igbts = 0

    # Process Inductors
    print("\n--- INDUCTORS ---")
    total_inductors += process_file("Inductors.html", "magnetics.ndjson", make_inductor_document)
    total_inductors += process_file(
        "Inductors - Transformers.html", "magnetics.ndjson", make_inductor_document
    )

    # Process MOSFETs
    print("\n--- MOSFETs ---")
    total_mosfets += process_file(
        "MOSFETs - Automotive.html", "mosfets.ndjson", make_mosfet_document
    )
    total_mosfets += process_file("mosfets.html", "mosfets.ndjson", make_mosfet_document)

    # Process Diode Modules
    print("\n--- DIODE MODULES ---")
    total_diodes += process_file(
        "Modules - Modules, Diode - FRED Pt®.html", "diodes.ndjson", make_diode_document
    )
    total_diodes += process_file(
        "Modules - Modules, Diode - HEXFRED®.html", "diodes.ndjson", make_diode_document
    )
    total_diodes += process_file(
        "Modules - Modules, Diode - High Performance Schottky.html",
        "diodes.ndjson",
        make_diode_document,
    )
    total_diodes += process_file(
        "Modules - Modules, Diode - High Voltage.html", "diodes.ndjson", make_diode_document
    )
    total_diodes += process_file(
        "Modules, Diode - Silicon Carbide (SiC).html", "diodes.ndjson", make_diode_document
    )

    # Process IGBT Modules
    print("\n--- IGBT MODULES ---")
    total_igbts += process_file("Modules - Modules, IGBT.html", "igbts.ndjson", make_igbt_document)

    # Process MOSFET Modules
    print("\n--- MOSFET MODULES ---")
    total_mosfets += process_file(
        "Modules - Modules, MOSFET.html", "mosfets.ndjson", make_mosfet_document
    )

    # Summary
    print("\n" + "=" * 70)
    print("CONVERSION COMPLETE")
    print("=" * 70)
    print(f"  Inductors:   {total_inductors}")
    print(f"  MOSFETs:     {total_mosfets}")
    print(f"  Diodes:      {total_diodes}")
    print(f"  IGBTs:       {total_igbts}")
    print("=" * 70)


if __name__ == "__main__":
    main()
