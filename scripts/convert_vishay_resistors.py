#!/usr/bin/env python3
"""Extract Vishay resistor data from saved HTML and convert to TAS format."""

import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path("/home/alf/OpenConverters/Heaviside")
sys.path.insert(0, str(REPO_ROOT))

INPUT_DIR = Path("/home/alf/Downloads/Vishay")
OUTPUT_DIR = REPO_ROOT / "TAS" / "data"

# Technology mapping from Vishay to RAS enum
TECH_MAP = {
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
    "Metal glaze": "thickFilm",  # closest match
}


def extract_json_from_html(html_path: Path) -> dict:
    """Extract Next.js page props JSON from HTML file."""
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    scripts = re.findall(r"<script[^\u003e]*\u003e(.*?)\u003c/script\u003e", html, re.DOTALL)
    data = json.loads(max(scripts, key=len))
    return data["props"]["pageProps"]


def parse_resistance(min_val: float, max_val: float) -> dict[str, float]:
    """Calculate nominal resistance from min/max range."""
    if min_val == max_val:
        return {"nominal": min_val}
    # Use geometric mean for resistance ranges
    nominal = (min_val * max_val) ** 0.5
    return {"nominal": nominal, "minimum": min_val, "maximum": max_val}


def parse_tolerance(tol_str: str) -> float | None:
    """Parse tolerance string to fraction."""
    if not tol_str or tol_str.lower() in ("n/a", "-"):
        return None
    try:
        return float(tol_str) / 100.0
    except ValueError:
        return None


def parse_temperature(temp_str: str) -> dict[str, float] | None:
    """Parse temperature range string like '-55 to +125'."""
    if not temp_str:
        return None
    match = re.findall(r"([+-]?\d+)", temp_str)
    if len(match) >= 2:
        return {"minimum": float(match[0]), "maximum": float(match[1])}
    elif len(match) == 1:
        return {"maximum": float(match[0])}
    return None


def parse_tcr(tcr_str: str) -> float | None:
    """Parse temperature coefficient string."""
    if not tcr_str or tcr_str.lower() in ("n/a", "-"):
        return None
    # Extract first number from ranges like "100 to 180"
    match = re.search(r"(\d+)", tcr_str)
    if match:
        return float(match.group(1))
    return None


def parse_voltage(volt_str: str, volt_val: float) -> float | None:
    """Parse voltage, handling special cases."""
    if volt_val <= 0 or volt_str in ("n/a", "-", ""):
        return None
    return volt_val


def parse_case_size(size_str: str) -> tuple[float | None, float | None, float | None]:
    """Parse case size string to dimensions in meters."""
    if not size_str or size_str.lower() == "n/a":
        return None, None, None
    
    # Common SMD sizes
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
        "3637": (3.81e-3, 3.43e-3, None),  # WSL3637
        "3920": (10.0e-3, 5.0e-3, None),
        "5930": (15.0e-3, 7.6e-3, None),
        "1020": (2.5e-3, 5.0e-3, None),
    }
    
    size_clean = size_str.strip()
    if size_clean in smd_map:
        return smd_map[size_clean]
    
    # Try to parse as LxW format
    match = re.match(r"(\d+(?:\.\d+)?)[xX](\d+(?:\.\d+)?)", size_clean)
    if match:
        return float(match.group(1)) * 1e-3, float(match.group(2)) * 1e-3, None
    
    return None, None, None


def make_resistor_document(product: dict) -> dict[str, Any] | None:
    """Convert Vishay resistor product to RAS document."""
    order_code = product.get("P1001", "")
    if not order_code or order_code.lower() == "n/a":
        return None
    
    series = product.get("P1001", "")
    tech_raw = product.get("technology", "")
    tech = TECH_MAP.get(tech_raw, "thickFilm")  # default fallback
    
    # Electrical
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
        product.get("wt_max_voltage_displ", ""),
        product.get("wt_max_voltage_value", 0)
    )
    
    tcr = parse_tcr(product.get("TCR_DISPL", ""))
    
    # Thermal
    temp = parse_temperature(product.get("temp", ""))
    
    # Mechanical
    size_str = product.get("size_device_style", "")
    length, width, height = parse_case_size(size_str)
    
    mounting = product.get("mounting_tech", "")
    assembly = "smt" if "Surface-mount" in mounting else "tht" if "Through-hole" in mounting else "smt"
    
    # Build document
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
    
    # Add optional electrical fields
    if voltage:
        doc["resistor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]["maxVoltage"] = voltage
    if tcr:
        doc["resistor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]["temperatureCoefficient"] = tcr
    
    # Add thermal
    if temp:
        doc["resistor"]["manufacturerInfo"]["datasheetInfo"]["thermal"] = {
            "operatingTemperature": temp
        }
    
    # Add mechanical
    mechanical = {"shapeType": "SMD Chip" if assembly == "smt" else "THT"}
    if length:
        mechanical["length"] = {"nominal": length}
    if width:
        mechanical["width"] = {"nominal": width}
    if height:
        mechanical["height"] = {"nominal": height}
    mechanical["assemblyType"] = assembly
    
    doc["resistor"]["manufacturerInfo"]["datasheetInfo"]["mechanical"] = mechanical
    
    # Add business
    doc["resistor"]["manufacturerInfo"]["datasheetInfo"]["business"] = {
        "packaging": "Tape & Reel" if assembly == "smt" else "Bulk",
        "moq": 1,
        "distribution": "Mouser/DigiKey",
    }
    
    return doc


def process_resistors() -> list[dict[str, Any]]:
    """Process Vishay resistors."""
    html_path = INPUT_DIR / "resistors-fixed.html"
    if not html_path.exists():
        print(f"WARNING: {html_path} not found, trying downloaded file")
        html_path = Path("/tmp/vishay_resistors.html")
    
    pageProps = extract_json_from_html(html_path)
    products = pageProps.get("paramResults", [])
    
    results = []
    skipped = 0
    for product in products:
        doc = make_resistor_document(product)
        if doc:
            results.append(doc)
        else:
            skipped += 1
    
    print(f"Processed {len(results)} resistors from {len(products)} raw entries ({skipped} skipped)")
    return results


def write_ndjson(documents: list[dict[str, Any]], output_path: Path) -> None:
    """Write documents to NDJSON file."""
    with open(output_path, "a", encoding="utf-8") as f:
        for doc in documents:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")


def main():
    print("=" * 60)
    print("Vishay Resistors to TAS Converter")
    print("=" * 60)
    
    all_resistors = process_resistors()
    
    # Write resistors
    if all_resistors:
        resistors_path = OUTPUT_DIR / "resistors.ndjson"
        write_ndjson(all_resistors, resistors_path)
        print(f"\nWrote {len(all_resistors)} resistors to {resistors_path}")
    
    print("\n" + "=" * 60)
    print("Conversion complete!")
    print(f"  Total resistors: {len(all_resistors)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
