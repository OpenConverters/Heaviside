#!/usr/bin/env python3
"""Extract Vishay product data from saved HTML files and convert to TAS format."""

import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path("/home/alf/OpenConverters/Heaviside")
sys.path.insert(0, str(REPO_ROOT))

INPUT_DIR = Path("/home/alf/Downloads/Vishay")
OUTPUT_DIR = REPO_ROOT / "TAS" / "data"


def extract_json_from_html(html_path: Path) -> dict:
    """Extract Next.js page props JSON from HTML file."""
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    
    # Find all script tags
    scripts = re.findall(r"<script[^\u003e]*\u003e(.*?)\u003c/script\u003e", html, re.DOTALL)
    
    # Find the largest script (contains the data)
    largest_script = max(scripts, key=len)
    
    # Parse JSON
    data = json.loads(largest_script)
    return data["props"]["pageProps"]


def parse_capacitance(value: Any) -> float | None:
    """Parse capacitance value to Farads."""
    if value is None:
        return None
    try:
        return float(value) * 1e-6  # µF to F
    except (ValueError, TypeError):
        return None


def parse_voltage(value: Any) -> float | None:
    """Parse voltage value."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def parse_temperature(temp_str: str) -> dict[str, float] | None:
    """Parse temperature string like '+85 °C' to min/max."""
    if not temp_str:
        return None
    # Extract number
    match = re.search(r"([+-]?\d+)", temp_str)
    if match:
        return {"maximum": float(match.group(1))}
    return None


def parse_case_size(case_str: str) -> tuple[float | None, float | None]:
    """Parse case size like 'Ø8.2x11' to diameter and length in meters."""
    if not case_str:
        return None, None
    # Remove HTML entities
    case_str = case_str.replace("&#216;", "Ø").replace("&#176;", "°")
    
    # Match diameter x length pattern
    match = re.search(r"Ø(\d+(?:\.\d+)?)[xX](\d+(?:\.\d+)?)", case_str)
    if match:
        diameter_mm = float(match.group(1))
        length_mm = float(match.group(2))
        return diameter_mm * 1e-3, length_mm * 1e-3
    
    return None, None


def make_capacitor_document(product: dict) -> dict[str, Any]:
    """Convert Vishay capacitor product to CAS document."""
    order_code = product.get("order_code", "")
    series = product.get("P1001", "")
    
    # Parse electrical specs
    capacitance_f = parse_capacitance(product.get("cap"))
    voltage = parse_voltage(product.get("Voltage"))
    
    if not capacitance_f or not voltage:
        return None
    
    # Parse dimensions
    case_str = product.get("case_size", "").replace("&#216;", "Ø").replace("&#176;", "°")
    diameter_m, length_m = parse_case_size(case_str)
    
    # Parse temperature
    temp = parse_temperature(product.get("temp_max", ""))
    
    # Useful life
    useful_life = None
    try:
        useful_life = float(product.get("Useful_Life", 0))
    except (ValueError, TypeError):
        pass
    
    # Build document
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
                        "technology": product.get("technology", "Aluminum Electrolytic"),
                        "case": case_str,
                    },
                    "electrical": {
                        "capacitance": {
                            "nominal": capacitance_f,
                        },
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
                            "assembly": "THT" if product.get("Form") == "Radial" else "SMT",
                            "shapeType": product.get("Form", "Radial"),
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
    
    # Add dimensions if available
    if diameter_m:
        doc["capacitor"]["manufacturerInfo"]["datasheetInfo"]["mechanical"]["dimensions"]["diameter"] = {
            "nominal": diameter_m
        }
    if length_m:
        doc["capacitor"]["manufacturerInfo"]["datasheetInfo"]["mechanical"]["dimensions"]["length"] = {
            "nominal": length_m
        }
    
    # Add useful life if available
    if useful_life:
        doc["capacitor"]["manufacturerInfo"]["datasheetInfo"]["lifetime"] = {
            "lifetimeEndurance": useful_life,
        }
    
    return doc


def process_capacitors() -> list[dict[str, Any]]:
    """Process Vishay aluminum capacitors."""
    html_path = INPUT_DIR / "Capacitors - Aluminum Electrolytic.html"
    if not html_path.exists():
        print(f"WARNING: {html_path} not found")
        return []
    
    pageProps = extract_json_from_html(html_path)
    products = pageProps.get("paramResults", [])
    
    results = []
    for product in products:
        doc = make_capacitor_document(product)
        if doc:
            results.append(doc)
    
    print(f"Processed {len(results)} capacitors from {len(products)} raw entries")
    return results


def process_mosfets() -> list[dict[str, Any]]:
    """Process Vishay MOSFETs - store as SAS semiconductors."""
    html_path = INPUT_DIR / "mosfets.html"
    if not html_path.exists():
        print(f"WARNING: {html_path} not found")
        return []
    
    pageProps = extract_json_from_html(html_path)
    products = pageProps.get("paramResults", [])
    
    print(f"Found {len(products)} MOSFETs (SAS format not yet implemented)")
    return []


def write_ndjson(documents: list[dict[str, Any]], output_path: Path) -> None:
    """Write documents to NDJSON file."""
    with open(output_path, "a", encoding="utf-8") as f:
        for doc in documents:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")


def main():
    print("=" * 60)
    print("Vishay HTML to TAS Converter")
    print("=" * 60)
    
    all_caps = process_capacitors()
    all_mosfets = process_mosfets()
    
    # Write capacitors
    if all_caps:
        caps_path = OUTPUT_DIR / "capacitors.ndjson"
        write_ndjson(all_caps, caps_path)
        print(f"\nWrote {len(all_caps)} capacitors to {caps_path}")
    
    print("\n" + "=" * 60)
    print("Conversion complete!")
    print(f"  Total capacitors: {len(all_caps)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
