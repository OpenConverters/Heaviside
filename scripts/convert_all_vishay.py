#!/usr/bin/env python3
"""Process all Vishay HTML files and convert to TAS format."""

import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path("/home/alf/OpenConverters/Heaviside")
sys.path.insert(0, str(REPO_ROOT))

INPUT_DIR = Path("/home/alf/Downloads/Vishay")
OUTPUT_DIR = REPO_ROOT / "TAS" / "data"

# Technology mappings
TECH_MAP_RESISTOR = {
    "MELF": "melf",
    "Metal film": "metalFilm",
    "Wirewound": "wirewound",
    "Wirewound ": "wirewound",
    "Power Metal Strip<sup>\u00ae</sup>": "currentSenseShunt",
    "Power Metal Plate<sup>\u2122</sup> current sense": "currentSenseShunt",
    "Copper strip": "currentSenseShunt",
    "Carbon film": "carbonFilm",
    "Thick film": "thickFilm",
    "Thin film": "thinFilm",
    "Metal foil": "metalFoil",
    "Metal oxide": "metalOxide",
    "Metal glaze": "thickFilm",
}


def extract_json_from_html(html_path: Path) -> list[dict]:
    """Extract Next.js page props JSON from HTML file."""
    with open(html_path, encoding="utf-8") as f:
        html = f.read()
    scripts = re.findall(r"<script[^\u003e]*\u003e(.*?)\u003c/script\u003e", html, re.DOTALL)
    if not scripts:
        return []
    data = json.loads(max(scripts, key=len))
    return data.get("props", {}).get("pageProps", {}).get("paramResults", [])


def parse_resistance(min_val: float, max_val: float) -> dict[str, float]:
    if min_val == max_val:
        return {"nominal": min_val}
    nominal = (min_val * max_val) ** 0.5
    return {"nominal": nominal, "minimum": min_val, "maximum": max_val}


def parse_tolerance(tol_str: str) -> float | None:
    if not tol_str or tol_str.lower() in ("n/a", "-"):
        return None
    try:
        return float(tol_str) / 100.0
    except ValueError:
        return None


def parse_temperature(temp_str: str) -> dict[str, float] | None:
    if not temp_str:
        return None
    match = re.findall(r"([+-]?\d+)", temp_str)
    if len(match) >= 2:
        return {"minimum": float(match[0]), "maximum": float(match[1])}
    elif len(match) == 1:
        return {"maximum": float(match[0])}
    return None


def parse_tcr(tcr_str: str) -> float | None:
    if not tcr_str or tcr_str.lower() in ("n/a", "-"):
        return None
    match = re.search(r"(\d+)", tcr_str)
    if match:
        return float(match.group(1))
    return None


def parse_voltage(volt_str: str, volt_val: float) -> float | None:
    if volt_val <= 0 or volt_str in ("n/a", "-", ""):
        return None
    return volt_val


def parse_case_size(size_str: str) -> tuple[float | None, float | None, float | None]:
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


def make_resistor_document(product: dict) -> dict[str, Any] | None:
    order_code = product.get("P1001", "")
    if not order_code or order_code.lower() == "n/a":
        return None

    series = product.get("P1001", "")
    tech_raw = product.get("technology", "")
    tech = TECH_MAP_RESISTOR.get(tech_raw, "thickFilm")

    res_min = product.get("res_min_value", 0)
    res_max = product.get("res_max_value", 0)
    if res_min <= 0 and res_max <= 0:
        return None

    resistance = parse_resistance(res_min, res_max)
    tolerance = parse_tolerance(product.get("tolerance_displ", ""))
    power = product.get("wt_power_rating_value", 0)
    if power <= 0:
        return None

    voltage = parse_voltage(
        product.get("wt_max_voltage_displ", ""), product.get("wt_max_voltage_value", 0)
    )

    tcr = parse_tcr(product.get("TCR_DISPL", ""))
    temp = parse_temperature(product.get("temp", ""))

    size_str = product.get("size_device_style", "")
    length, width, height = parse_case_size(size_str)

    mounting = product.get("mounting_tech", "")
    assembly = (
        "smt" if "Surface-mount" in mounting else "tht" if "Through-hole" in mounting else "smt"
    )

    doc = {
        "resistor": {
            "manufacturerInfo": {
                "name": "Vishay",
                "reference": order_code,
                "status": "production",
                "family": series,
                "datasheetUrl": f"https://www.vishay.com/en/search/?type=inv&query={order_code}",
                "datasheetInfo": {
                    "part": {
                        "partNumber": order_code,
                        "series": series,
                        "technology": tech,
                        "case": size_str,
                    },
                    "electrical": {
                        "resistance": resistance,
                        "tolerance": tolerance if tolerance is not None else 0.05,
                        "powerRating": power,
                        "powerRatingTemperature": 70,
                    },
                },
            }
        }
    }

    if voltage:
        doc["resistor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]["maxVoltage"] = voltage
    if tcr:
        doc["resistor"]["manufacturerInfo"]["datasheetInfo"]["electrical"][
            "temperatureCoefficient"
        ] = tcr

    if temp:
        doc["resistor"]["manufacturerInfo"]["datasheetInfo"]["thermal"] = {
            "operatingTemperature": temp
        }

    mechanical = {"shapeType": "SMD Chip" if assembly == "smt" else "THT"}
    if length:
        mechanical["length"] = {"nominal": length}
    if width:
        mechanical["width"] = {"nominal": width}
    if height:
        mechanical["height"] = {"nominal": height}
    mechanical["assemblyType"] = assembly

    doc["resistor"]["manufacturerInfo"]["datasheetInfo"]["mechanical"] = mechanical
    doc["resistor"]["manufacturerInfo"]["datasheetInfo"]["business"] = {
        "packaging": "Tape & Reel" if assembly == "smt" else "Bulk",
        "moq": 1,
        "distribution": "Mouser/DigiKey",
    }

    return doc


def extract_capacitance(value) -> float | None:
    """Extract capacitance value from various formats."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    # Convert to string and replace HTML entities
    value_str = str(value)
    value_str = value_str.replace("&#181;", "µ").replace("&#956;", "µ")

    # Handle strings with HTML comments like "<!-- 33000--> 33 µF"
    match = re.search(r"(\d+(?:\.\d+)?)\s*[µuµ]F", value_str, re.IGNORECASE)
    if match:
        return float(match.group(1)) * 1e-6
    # Handle pF
    match = re.search(r"(\d+(?:\.\d+)?)\s*pF", value_str, re.IGNORECASE)
    if match:
        return float(match.group(1)) * 1e-12
    # Handle plain number
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def extract_voltage(value) -> float | None:
    """Extract voltage value from various formats."""
    if value is None or str(value).upper() == "NA":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def make_capacitor_document(
    product: dict, tech_override: str | None = None
) -> dict[str, Any] | None:
    """Universal capacitor converter handling multiple Vishay formats."""
    # Try multiple order code fields
    order_code = (
        product.get("order_code", "") or product.get("P1001", "") or product.get("P1009", "")
    )
    if not order_code or order_code.lower() == "n/a":
        return None

    series = product.get("P1001", "") or product.get("P1009", "")

    # Try multiple capacitance fields
    capacitance_f = None
    if product.get("cap"):
        capacitance_f = extract_capacitance(product["cap"])
    elif product.get("cap_pf"):
        capacitance_f = float(product["cap_pf"]) * 1e-12
    elif product.get("T9505"):
        capacitance_f = extract_capacitance(product["T9505"])
    elif product.get("P3842"):
        capacitance_f = extract_capacitance(product["P3842"])

    if not capacitance_f:
        return None

    # Try multiple voltage fields
    voltage = None
    if product.get("Voltage"):
        voltage = extract_voltage(product["Voltage"])
    elif product.get("voltage_v"):
        voltage = extract_voltage(product["voltage_v"])
    elif product.get("rvol"):
        voltage = extract_voltage(product["rvol"])
    elif product.get("T1112"):
        voltage = extract_voltage(product["T1112"])
    elif product.get("P3527"):
        voltage = extract_voltage(product["P3527"])

    if not voltage:
        return None

    # Technology
    tech = tech_override
    if not tech:
        tech = product.get("technology", "")
        if "Aluminum" in tech:
            tech = "Alum. Electrolytic"
        elif "Polymer" in tech:
            tech = "Alum. Polymer"
        elif "Tantalum" in tech:
            tech = "Tantalum"
        elif "Film" in tech:
            tech = "Film Capacitor"
        elif "Ceramic" in tech:
            tech = "MLCC Class II"
        else:
            tech = "Alum. Electrolytic"

    # Case size
    case = (
        product.get("case_size", "")
        or product.get("size_device_style", "")
        or product.get("P5897", "")
        or product.get("P3702", "")
    )
    case = case.replace("&#216;", "Ø").replace("&#176;", "°")

    # Temperature
    temp_str = product.get("temp_max", "") or product.get("temp", "")
    temp = parse_temperature(temp_str)

    doc = {
        "capacitor": {
            "manufacturerInfo": {
                "name": "Vishay",
                "reference": order_code,
                "status": "production",
                "family": series,
                "datasheetUrl": f"https://www.vishay.com/en/search/?type=inv&query={order_code}",
                "datasheetInfo": {
                    "part": {
                        "partNumber": order_code,
                        "series": series,
                        "technology": tech,
                        "case": case,
                    },
                    "electrical": {
                        "capacitance": {"nominal": capacitance_f},
                        "ratedVoltage": voltage,
                        "esr": None,
                        "rippleCurrent": None,
                    },
                    "thermal": {
                        "temperature": temp if temp else {"maximum": 85},
                    },
                    "mechanical": {
                        "dimensions": {},
                        "shape": {
                            "assembly": "THT",
                            "shapeType": "Radial Cylindrical",
                        },
                    },
                    "business": {
                        "packaging": "Bulk",
                        "moq": 1,
                        "distribution": "Mouser/DigiKey",
                    },
                },
            }
        }
    }

    return doc


def write_ndjson(documents: list[dict[str, Any]], output_path: Path) -> None:
    with open(output_path, "a", encoding="utf-8") as f:
        for doc in documents:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")


def process_file(filename: str, output_file: str, converter_func, **kwargs) -> int:
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
        doc = converter_func(product, **kwargs)
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
    print("Vishay Catalog to TAS Converter - Processing All Files")
    print("=" * 70)

    total_caps = 0
    total_resistors = 0
    total_magnetics = 0
    total_mosfets = 0

    # Process Capacitors
    print("\n--- CAPACITORS ---")
    total_caps += process_file(
        "Capacitors - Aluminum Electrolytic.html",
        "capacitors.ndjson",
        make_capacitor_document,
        tech_override="Alum. Electrolytic",
    )
    total_caps += process_file(
        "Capacitors - Energy Storage.html",
        "capacitors.ndjson",
        make_capacitor_document,
        tech_override="Film Capacitor",
    )
    total_caps += process_file(
        "Capacitors - Film.html",
        "capacitors.ndjson",
        make_capacitor_document,
        tech_override="Film Capacitor",
    )
    total_caps += process_file(
        "Capacitors - Polymer.html",
        "capacitors.ndjson",
        make_capacitor_document,
        tech_override="Alum. Polymer",
    )
    total_caps += process_file(
        "Capacitors - Tantalum.html",
        "capacitors.ndjson",
        make_capacitor_document,
        tech_override="Tantalum",
    )
    total_caps += process_file(
        "Capacitors - Thin Film.html",
        "capacitors.ndjson",
        make_capacitor_document,
        tech_override="Film Capacitor",
    )
    total_caps += process_file(
        "Ceramic - RF Power.html",
        "capacitors.ndjson",
        make_capacitor_document,
        tech_override="MLCC Class II",
    )

    # Process Resistors
    print("\n--- RESISTORS ---")
    html_path = Path("/tmp/vishay_resistors.html")
    if html_path.exists():
        products = extract_json_from_html(html_path)
        results = []
        for product in products:
            doc = make_resistor_document(product)
            if doc:
                results.append(doc)
        if results:
            write_ndjson(results, OUTPUT_DIR / "resistors.ndjson")
            print(f"  PROCESSED: {len(results)} resistors from downloaded file")
            total_resistors += len(results)

    total_resistors += process_file(
        "Resistors, Fixed - Automotive.html", "resistors.ndjson", make_resistor_document
    )

    # Process Magnetics (Inductors/Transformers)
    print("\n--- MAGNETICS ---")
    print("  SKIPPED: Inductors/Transformers (MAS schema needed)")

    # Process MOSFETs
    print("\n--- MOSFETs ---")
    print("  SKIPPED: MOSFETs (SAS schema needed)")

    # Summary
    print("\n" + "=" * 70)
    print("CONVERSION COMPLETE")
    print("=" * 70)
    print(f"  Capacitors:  {total_caps}")
    print(f"  Resistors:   {total_resistors}")
    print(f"  Magnetics:   {total_magnetics} (skipped)")
    print(f"  MOSFETs:     {total_mosfets} (skipped)")
    print("=" * 70)


if __name__ == "__main__":
    main()
