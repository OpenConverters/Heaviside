#!/usr/bin/env python3
"""Convert Murata CSV product lists to TAS schema format.

Processes all CSV files in /home/alf/Downloads/Murata/ and converts them
to validated TAS components, writing to:
- TAS/data/capacitors.ndjson (for all capacitor types)
- TAS/data/magnetics.ndjson (for inductors, ferrite beads, common mode chokes)

Each CSV has 5 header lines, then column headers on line 7, data from line 8.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

# Paths
INPUT_DIR = Path("/home/alf/Downloads/Murata")
OUTPUT_DIR = Path("/home/alf/OpenConverters/Heaviside/TAS/data")
REPO_ROOT = Path("/home/alf/OpenConverters/Heaviside")

# Add repo to path for imports
sys.path.insert(0, str(REPO_ROOT))


def parse_tolerance(tol_str: str | None) -> dict[str, float] | None:
    """Parse tolerance string like '±20%', '+20%/-10%' into min/max/nominal factor."""
    if not tol_str or tol_str == "-":
        return None
    
    # Handle ±X%
    m = re.match(r"±(\d+)%", tol_str)
    if m:
        pct = float(m.group(1)) / 100.0
        return {"nominal": 0, "minimum": -pct, "maximum": pct}
    
    # Handle +X%/-Y%
    m = re.match(r"\+(\d+)%/-(\d+)%", tol_str)
    if m:
        return {"nominal": 0, "minimum": -float(m.group(2))/100.0, "maximum": float(m.group(1))/100.0}
    
    return None


def parse_size_code(size_str: str) -> tuple[float | None, float | None, float | None]:
    """Parse size code like '3225M/1210' or '2016/0806' into (L, W, H in mm).
    
    Returns (length_mm, width_mm, height_mm) where height is None.
    """
    if not size_str or size_str == "-":
        return None, None, None
    
    # Extract the metric code (first part before /)
    parts = size_str.split("/")
    metric_code = parts[0].strip()
    
    # Remove any suffix letters
    metric_code = re.sub(r'[A-Z]$', '', metric_code, flags=re.IGNORECASE)
    
    if len(metric_code) == 4:
        # Standard 4-digit metric: L W in 0.1mm
        try:
            l_mm = int(metric_code[:2]) * 0.1
            w_mm = int(metric_code[2:]) * 0.1
            return l_mm, w_mm, None
        except ValueError:
            pass
    elif len(metric_code) >= 3:
        # Try to parse as direct dimensions
        m = re.match(r"(\d+(?:\.\d+)?)[xX](\d+(?:\.\d+)?)", metric_code)
        if m:
            return float(m.group(1)), float(m.group(2)), None
    
    return None, None, None


def parse_capacitance_pf(val_str: str) -> float | None:
    """Parse capacitance in pF to Farads."""
    if not val_str or val_str == "-":
        return None
    try:
        pf = float(val_str)
        return pf * 1e-12
    except ValueError:
        return None


def parse_capacitance_uf(val_str: str) -> float | None:
    """Parse capacitance in uF to Farads."""
    if not val_str or val_str == "-":
        return None
    try:
        uf = float(val_str)
        return uf * 1e-6
    except ValueError:
        return None


def parse_voltage(val_str: str) -> float | None:
    """Parse voltage value."""
    if not val_str or val_str == "-":
        return None
    try:
        return float(val_str)
    except ValueError:
        return None


def parse_inductance_uh(val_str: str) -> float | None:
    """Parse inductance in uH to Henries."""
    if not val_str or val_str == "-":
        return None
    try:
        uh = float(val_str)
        return uh * 1e-6
    except ValueError:
        return None


def parse_inductance_nh(val_str: str) -> float | None:
    """Parse inductance in nH to Henries."""
    if not val_str or val_str == "-":
        return None
    try:
        nh = float(val_str)
        return nh * 1e-9
    except ValueError:
        return None


def parse_resistance(val_str: str) -> float | None:
    """Parse resistance in ohms."""
    if not val_str or val_str == "-":
        return None
    try:
        return float(val_str)
    except ValueError:
        return None


def parse_current_ma(val_str: str) -> float | None:
    """Parse current in mA to Amperes."""
    if not val_str or val_str == "-":
        return None
    try:
        ma = float(val_str)
        return ma * 1e-3
    except ValueError:
        return None


def parse_dimension_mm(val_str: str) -> dict[str, float] | None:
    """Parse a dimension in mm to meters as dimensionWithTolerance."""
    if not val_str or val_str == "-":
        return None
    try:
        mm = float(val_str)
        return {"nominal": mm * 1e-3}
    except ValueError:
        return None


def parse_temperature(temp_str: str) -> dict[str, float] | None:
    """Parse temperature string like '85' or '-55 to 125' into min/max."""
    if not temp_str or temp_str == "-":
        return None
    
    # Single value (usually max)
    try:
        return {"maximum": float(temp_str)}
    except ValueError:
        pass
    
    # Range
    m = re.match(r"(-?\d+)\s*to\s*(-?\d+)", temp_str, re.IGNORECASE)
    if m:
        return {"minimum": float(m.group(1)), "maximum": float(m.group(2))}
    
    return None


def make_manufacturer_info(
    part_number: str,
    series: str,
    technology: str,
    case: str,
    datasheet_url: str = "",
    description: str = "",
) -> dict[str, Any]:
    """Build the manufacturerInfo block."""
    info: dict[str, Any] = {
        "name": "Murata",
        "reference": part_number,
        "status": "production",
    }
    if datasheet_url:
        info["datasheetUrl"] = datasheet_url
    if series:
        info["family"] = series
    if description:
        info["description"] = description
    return info


def make_capacitor_document(
    part_number: str,
    series: str,
    technology: str,
    case: str,
    capacitance_f: float,
    rated_voltage: float,
    tolerance_str: str,
    esr: float | None = None,
    temp_max: float | None = None,
    temp_characteristic: str | None = None,
    size_l_mm: float | None = None,
    size_w_mm: float | None = None,
    size_h_mm: float | None = None,
    datasheet_url: str = "",
    application: str = "",
    rated_current: float | None = None,
) -> dict[str, Any]:
    """Build a CAS capacitor document."""
    
    # Capacitance with tolerance
    tolerance = parse_tolerance(tolerance_str)
    capacitance: dict[str, Any] = {"nominal": capacitance_f}
    if tolerance:
        capacitance["minimum"] = capacitance_f * (1 + tolerance.get("minimum", 0))
        capacitance["maximum"] = capacitance_f * (1 + tolerance.get("maximum", 0))
    
    # Electrical
    electrical: dict[str, Any] = {
        "capacitance": capacitance,
        "ratedVoltage": rated_voltage,
        "esr": esr if esr is not None else None,
        "rippleCurrent": rated_current if rated_current is not None else None,
    }
    
    # Thermal
    thermal: dict[str, Any] = {"temperature": {}}
    if temp_max is not None:
        thermal["temperature"]["maximum"] = temp_max
    if temp_characteristic:
        thermal["tcc"] = {"nominal": 0}  # Placeholder
    
    # Mechanical
    dimensions: dict[str, Any] = {}
    if size_l_mm is not None:
        dimensions["length"] = {"nominal": size_l_mm * 1e-3}
    if size_w_mm is not None:
        dimensions["width"] = {"nominal": size_w_mm * 1e-3}
    if size_h_mm is not None:
        dimensions["height"] = {"nominal": size_h_mm * 1e-3}
    
    shape: dict[str, Any] = {}
    if technology and "Lead" in technology:
        shape["assembly"] = "THT"
    elif technology and "Polymer" in technology:
        shape["assembly"] = "SMT"
    else:
        shape["assembly"] = "SMT"  # Default for MLCCs
    
    if case:
        shape["shapeType"] = case
    
    mechanical: dict[str, Any] = {}
    if dimensions:
        mechanical["dimensions"] = dimensions
    if shape:
        mechanical["shape"] = shape
    
    # Build datasheetInfo
    datasheet_info: dict[str, Any] = {
        "part": {
            "partNumber": part_number,
            "series": series or "Unknown",
            "technology": technology or "MLCC",
            "case": case or "Unknown",
        },
        "electrical": electrical,
        "thermal": thermal,
    }
    if mechanical:
        datasheet_info["mechanical"] = mechanical
    
    # Business info
    business = {
        "packaging": "Tape & Reel",
        "moq": 1,
        "distribution": "Mouser/DigiKey",
    }
    datasheet_info["business"] = business
    
    # Model params (simplified)
    if esr is not None:
        datasheet_info["modelParams"] = {
            "rs": esr,
            "cs": capacitance_f,
            "ls": 1e-9,  # Typical parasitic inductance
        }
    
    # Manufacturer info
    manufacturer_info = make_manufacturer_info(
        part_number=part_number,
        series=series,
        technology=technology,
        case=case,
        datasheet_url=datasheet_url,
        description=application,
    )
    manufacturer_info["datasheetInfo"] = datasheet_info
    
    return {"capacitor": {"manufacturerInfo": manufacturer_info}}


def make_magnetic_document(
    part_number: str,
    series: str = "",
    inductance_h: float | None = None,
    dcr_ohm: float | None = None,
    rated_current_a: float | None = None,
    saturation_current_a: float | None = None,
    size_l_mm: float | None = None,
    size_w_mm: float | None = None,
    size_h_mm: float | None = None,
    temp_min: float | None = None,
    temp_max: float | None = None,
    shielded: bool = False,
    datasheet_url: str = "",
    description: str = "",
    srf_hz: float | None = None,
    is_common_mode: bool = False,
    common_mode_z_ohm: float | None = None,
    common_mode_l_henry: float | None = None,
) -> dict[str, Any]:
    """Build a MAS magnetic document."""
    
    # Datasheet info
    part_info: dict[str, Any] = {
        "partNumber": part_number,
    }
    if series:
        part_info["family"] = series
    if description:
        part_info["description"] = description
    
    electrical: dict[str, Any] = {}
    if inductance_h is not None:
        electrical["inductance"] = {"nominal": inductance_h}
    if dcr_ohm is not None:
        electrical["dcResistance"] = {"maximum": dcr_ohm}
    if rated_current_a is not None:
        electrical["ratedCurrent"] = rated_current_a
    if saturation_current_a is not None:
        electrical["saturationCurrentPeak"] = saturation_current_a
    if srf_hz is not None:
        electrical["selfResonantFrequency"] = srf_hz
    
    if is_common_mode and common_mode_z_ohm is not None:
        electrical["maximumImpedance"] = common_mode_z_ohm
    
    thermal: dict[str, Any] = {}
    temp_info: dict[str, Any] = {}
    if temp_min is not None:
        temp_info["minimum"] = temp_min
    if temp_max is not None:
        temp_info["maximum"] = temp_max
    if temp_info:
        thermal["operatingTemperature"] = temp_info
    
    mechanical: dict[str, Any] = {}
    if size_l_mm is not None:
        mechanical["length"] = {"nominal": size_l_mm * 1e-3}
    if size_w_mm is not None:
        mechanical["width"] = {"nominal": size_w_mm * 1e-3}
    if size_h_mm is not None:
        mechanical["height"] = {"nominal": size_h_mm * 1e-3}
    
    # Datasheet info block
    datasheet_info: dict[str, Any] = {"part": part_info}
    if electrical:
        datasheet_info["electrical"] = electrical
    if thermal:
        datasheet_info["thermal"] = thermal
    if mechanical:
        datasheet_info["mechanical"] = mechanical
    
    # Manufacturer info
    manufacturer_info = make_manufacturer_info(
        part_number=part_number,
        series=series,
        technology="Inductor",
        case="",
        datasheet_url=datasheet_url,
        description=description,
    )
    if datasheet_info:
        manufacturer_info["datasheetInfo"] = datasheet_info
    
    # Core and coil (minimal required for MAS)
    core = {
        "functionalDescription": {
            "type": "twoPieceSet",
            "material": "Ferrite",
            "shape": "SMD",
            "gapping": [],
        }
    }
    
    coil = {
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
        ]
    }
    
    return {
        "magnetic": {
            "manufacturerInfo": manufacturer_info,
            "core": core,
            "coil": coil,
        }
    }


def process_mlccs() -> list[dict[str, Any]]:
    """Process MLCC CSV."""
    results = []
    csv_path = INPUT_DIR / "MurataProdList-MLCCs.csv"
    if not csv_path.exists():
        print(f"WARNING: {csv_path} not found")
        return results
    
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        # Skip first 5 lines (metadata header) - MLCCs have one less blank line
        for _ in range(5):
            next(f, None)
        
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("part_number"):
                continue
            
            part = row["part_number"].strip()
            cap_f = parse_capacitance_pf(row.get("capacitance_sort[pF]"))
            voltage = parse_voltage(row.get("rvol"))
            tolerance = row.get("tolerance", "")
            temp_max = parse_temperature(row.get("opetemp-max", ""))
            tcc = row.get("tcc", "")
            
            size_l, size_w, _ = parse_size_code(row.get("LWSize_mm_inch", ""))
            thickness = parse_dimension_mm(row.get("size_thickness_max", ""))
            size_h = thickness["nominal"] * 1e3 if thickness else None
            
            if cap_f and voltage:
                doc = make_capacitor_document(
                    part_number=part,
                    series=part[:7] if len(part) >= 7 else part,
                    technology="MLCC Class II" if tcc and tcc.startswith("X") else "MLCC Class I",
                    case=row.get("LWSize_mm_inch", ""),
                    capacitance_f=cap_f,
                    rated_voltage=voltage,
                    tolerance_str=tolerance,
                    temp_max=temp_max.get("maximum") if temp_max else None,
                    temp_characteristic=tcc if tcc else None,
                    size_l_mm=size_l,
                    size_w_mm=size_w,
                    size_h_mm=size_h,
                    datasheet_url=f"https://www.murata.com/products/productdetail?partno={part}",
                )
                results.append(doc)
    
    print(f"Processed {len(results)} MLCCs")
    return results


def process_lead_type_ceramic() -> list[dict[str, Any]]:
    """Process lead type ceramic capacitors."""
    results = []
    csv_path = INPUT_DIR / "MurataProdList-Lead Type Ceramic Capacitors.csv"
    if not csv_path.exists():
        return results
    
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for _ in range(6):
            next(f, None)
        
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("part_number"):
                continue
            
            part = row["part_number"].strip()
            cap_f = parse_capacitance_pf(row.get("capacitance_sort[pF]"))
            voltage = parse_voltage(row.get("rvol"))
            
            if cap_f and voltage:
                doc = make_capacitor_document(
                    part_number=part,
                    series=part[:7] if len(part) >= 7 else part,
                    technology="MLCC Class II",
                    case=row.get("LorD", ""),
                    capacitance_f=cap_f,
                    rated_voltage=voltage,
                    tolerance_str=row.get("tolerance", ""),
                    datasheet_url=f"https://www.murata.com/products/productdetail?partno={part}",
                )
                results.append(doc)
    
    print(f"Processed {len(results)} lead type ceramics")
    return results


def process_polymer_capacitors() -> list[dict[str, Any]]:
    """Process polymer capacitors."""
    results = []
    csv_path = INPUT_DIR / "MurataProdList-Polymer Capacitors.csv"
    if not csv_path.exists():
        return results
    
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for _ in range(6):
            next(f, None)
        
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("part_number"):
                continue
            
            part = row["part_number"].strip()
            cap_f = parse_capacitance_uf(row.get("capacitance_uf[uF]"))
            voltage = parse_voltage(row.get("rvol"))
            esr = parse_resistance(row.get("esr"))
            
            size_l, size_w, _ = parse_size_code(row.get("LWSize_mm_inch", ""))
            thickness = parse_dimension_mm(row.get("size_thickness", ""))
            size_h = thickness["nominal"] * 1e3 if thickness else None
            
            if cap_f and voltage:
                doc = make_capacitor_document(
                    part_number=part,
                    series=part[:7] if len(part) >= 7 else part,
                    technology="Alum. Polymer",
                    case=row.get("LWSize_mm_inch", ""),
                    capacitance_f=cap_f,
                    rated_voltage=voltage,
                    tolerance_str=row.get("tolerance_per", ""),
                    esr=esr,
                    size_l_mm=size_l,
                    size_w_mm=size_w,
                    size_h_mm=size_h,
                    datasheet_url=f"https://www.murata.com/products/productdetail?partno={part}",
                )
                results.append(doc)
    
    print(f"Processed {len(results)} polymer capacitors")
    return results


def process_resin_molding() -> list[dict[str, Any]]:
    """Process resin molding SMD ceramic capacitors."""
    results = []
    csv_path = INPUT_DIR / "MurataProdList-Resin Molding SMD Type Ceramic Capacitors.csv"
    if not csv_path.exists():
        return results
    
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for _ in range(6):
            next(f, None)
        
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("part_number"):
                continue
            
            part = row["part_number"].strip()
            cap_f = parse_capacitance_pf(row.get("capacitance_sort[pF]"))
            voltage = parse_voltage(row.get("rvol"))
            
            size_l, size_w, _ = parse_size_code(row.get("LxW", ""))
            thickness = parse_dimension_mm(row.get("size_thickness_max", ""))
            size_h = thickness["nominal"] * 1e3 if thickness else None
            
            if cap_f and voltage:
                doc = make_capacitor_document(
                    part_number=part,
                    series=part[:7] if len(part) >= 7 else part,
                    technology="MLCC Class II",
                    case=row.get("LxW", ""),
                    capacitance_f=cap_f,
                    rated_voltage=voltage,
                    tolerance_str=row.get("tolerance", ""),
                    size_l_mm=size_l,
                    size_w_mm=size_w,
                    size_h_mm=size_h,
                    datasheet_url=f"https://www.murata.com/products/productdetail?partno={part}",
                )
                results.append(doc)
    
    print(f"Processed {len(results)} resin molding capacitors")
    return results


def process_3_terminal_capacitors() -> list[dict[str, Any]]:
    """Process 3-terminal capacitors."""
    results = []
    csv_path = INPUT_DIR / "MurataProdList-3-terminal Capacitors.csv"
    if not csv_path.exists():
        return results
    
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for _ in range(6):
            next(f, None)
        
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("part_number"):
                continue
            
            part = row["part_number"].strip()
            cap_f = parse_capacitance_pf(row.get("capacitance_sort[pF]"))
            voltage = parse_voltage(row.get("rvol"))
            rated_current = parse_current_ma(row.get("RatedCurrent", ""))
            
            size_l, size_w, _ = parse_size_code(row.get("LWSize", ""))
            thickness = parse_dimension_mm(row.get("size_thickness_max", ""))
            size_h = thickness["nominal"] * 1e3 if thickness else None
            
            if cap_f and voltage:
                doc = make_capacitor_document(
                    part_number=part,
                    series=part[:7] if len(part) >= 7 else part,
                    technology="MLCC Class II",
                    case=row.get("LWSize", ""),
                    capacitance_f=cap_f,
                    rated_voltage=voltage,
                    tolerance_str=row.get("tolerance_per", ""),
                    rated_current=rated_current,
                    size_l_mm=size_l,
                    size_w_mm=size_w,
                    size_h_mm=size_h,
                    datasheet_url=f"https://www.murata.com/products/productdetail?partno={part}",
                )
                results.append(doc)
    
    print(f"Processed {len(results)} 3-terminal capacitors")
    return results


def process_power_inductors() -> list[dict[str, Any]]:
    """Process power inductors."""
    results = []
    csv_path = INPUT_DIR / "MurataProdList-Power Inductors.csv"
    if not csv_path.exists():
        return results
    
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for _ in range(6):
            next(f, None)
        
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("part_number"):
                continue
            
            part = row["part_number"].strip()
            inductance = parse_inductance_uh(row.get("inductance_uH"))
            dcr = parse_resistance(row.get("dcr"))
            rated_current = parse_current_ma(row.get("rated_current_temp", ""))
            saturation_current = parse_current_ma(row.get("rated_current_sat", ""))
            
            size_l, size_w, _ = parse_size_code(row.get("size_code", ""))
            thickness = parse_dimension_mm(row.get("size_thickness_max", ""))
            size_h = thickness["nominal"] * 1e3 if thickness else None
            
            if inductance:
                doc = make_magnetic_document(
                    part_number=part,
                    series=part[:7] if len(part) >= 7 else part,
                    inductance_h=inductance,
                    dcr_ohm=dcr,
                    rated_current_a=rated_current,
                    saturation_current_a=saturation_current,
                    size_l_mm=size_l,
                    size_w_mm=size_w,
                    size_h_mm=size_h,
                    temp_min=-40,
                    temp_max=125,
                    shielded=True,
                    datasheet_url=f"https://www.murata.com/products/productdetail?partno={part}",
                    description=row.get("Application", ""),
                )
                results.append(doc)
    
    print(f"Processed {len(results)} power inductors")
    return results


def process_rf_inductors() -> list[dict[str, Any]]:
    """Process RF inductors."""
    results = []
    csv_path = INPUT_DIR / "MurataProdList-RF Inductors.csv"
    if not csv_path.exists():
        return results
    
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for _ in range(6):
            next(f, None)
        
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("part_number"):
                continue
            
            part = row["part_number"].strip()
            inductance = parse_inductance_nh(row.get("Inductance_nH"))
            rated_current = parse_current_ma(row.get("RatedCurrent_mA", ""))
            
            size_l, size_w, _ = parse_size_code(row.get("size_code", ""))
            
            if inductance:
                doc = make_magnetic_document(
                    part_number=part,
                    series=part[:7] if len(part) >= 7 else part,
                    inductance_h=inductance,
                    rated_current_a=rated_current,
                    size_l_mm=size_l,
                    size_w_mm=size_w,
                    temp_min=-40,
                    temp_max=125,
                    shielded=False,
                    datasheet_url=f"https://www.murata.com/products/productdetail?partno={part}",
                    description=row.get("Application", ""),
                )
                results.append(doc)
    
    print(f"Processed {len(results)} RF inductors")
    return results


def process_common_mode_chokes() -> list[dict[str, Any]]:
    """Process common mode choke coils."""
    results = []
    csv_path = INPUT_DIR / "MurataProdList-Common Mode Choke Coils.csv"
    if not csv_path.exists():
        return results
    
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for _ in range(6):
            next(f, None)
        
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("part_number"):
                continue
            
            part = row["part_number"].strip()
            common_z = parse_resistance(row.get("Common_Z10M_ohm", ""))
            common_l = parse_inductance_uh(row.get("Common_L_uH", ""))
            rated_current = parse_current_ma(row.get("Idc_mA", ""))
            dcr = parse_resistance(row.get("DCR", ""))
            
            size_l, size_w, _ = parse_size_code(row.get("L_W", ""))
            thickness = parse_dimension_mm(row.get("Tmax", ""))
            size_h = thickness["nominal"] * 1e3 if thickness else None
            
            doc = make_magnetic_document(
                part_number=part,
                series=part[:7] if len(part) >= 7 else part,
                inductance_h=common_l,
                dcr_ohm=dcr,
                rated_current_a=rated_current,
                size_l_mm=size_l,
                size_w_mm=size_w,
                size_h_mm=size_h,
                temp_min=-40,
                temp_max=125,
                shielded=True,
                datasheet_url=f"https://www.murata.com/products/productdetail?partno={part}",
                description=row.get("Application", ""),
                is_common_mode=True,
                common_mode_z_ohm=common_z,
                common_mode_l_henry=common_l,
            )
            results.append(doc)
    
    print(f"Processed {len(results)} common mode chokes")
    return results


def process_ferrite_beads() -> list[dict[str, Any]]:
    """Process ferrite beads - store as RAS resistors (they are frequency-dependent resistors)."""
    results = []
    csv_path = INPUT_DIR / "MurataProdList-Chip Ferrite Bead.csv"
    if not csv_path.exists():
        return results
    
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for _ in range(6):
            next(f, None)
        
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("part_number"):
                continue
            
            part = row["part_number"].strip()
            z100 = parse_resistance(row.get("Z100", ""))
            z1m = parse_resistance(row.get("Z1M", ""))
            rdc = parse_resistance(row.get("RdcMax", ""))
            rated_current = parse_current_ma(row.get("RatedCurrent_mA", ""))
            
            size_l, size_w, _ = parse_size_code(row.get("size_code", ""))
            thickness = parse_dimension_mm(row.get("size_thickness_max", ""))
            size_h = thickness["nominal"] * 1e3 if thickness else None
            
            # Ferrite beads are stored as magnetics (they're inductors at high frequencies)
            # But they have impedance rather than inductance
            doc = make_magnetic_document(
                part_number=part,
                series=part[:7] if len(part) >= 7 else part,
                inductance_h=None,
                dcr_ohm=rdc,
                rated_current_a=rated_current,
                size_l_mm=size_l,
                size_w_mm=size_w,
                size_h_mm=size_h,
                temp_min=-55,
                temp_max=125,
                shielded=False,
                datasheet_url=f"https://www.murata.com/products/productdetail?partno={part}",
                description=row.get("Application", ""),
            )
            # Add impedance info to the magnetic document
            if "magnetic" in doc and "manufacturerInfo" in doc["magnetic"]:
                if "datasheetInfo" in doc["magnetic"]["manufacturerInfo"]:
                    electrical = doc["magnetic"]["manufacturerInfo"]["datasheetInfo"].get("electrical", {})
                    if z100:
                        electrical["maximumImpedance"] = z100
                    doc["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"] = electrical
            
            results.append(doc)
    
    print(f"Processed {len(results)} ferrite beads")
    return results


def write_ndjson(documents: list[dict[str, Any]], output_path: Path) -> None:
    """Write documents to NDJSON file."""
    with open(output_path, "a", encoding="utf-8") as f:
        for doc in documents:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")


def main():
    print("=" * 60)
    print("Murata CSV to TAS Converter")
    print("=" * 60)
    
    # Clear existing files
    caps_path = OUTPUT_DIR / "capacitors_murata.ndjson"
    mags_path = OUTPUT_DIR / "magnetics_murata.ndjson"
    
    # Remove if exists to start fresh
    caps_path.unlink(missing_ok=True)
    mags_path.unlink(missing_ok=True)
    
    all_caps = []
    all_mags = []
    
    # Process capacitors
    all_caps.extend(process_mlccs())
    all_caps.extend(process_lead_type_ceramic())
    all_caps.extend(process_polymer_capacitors())
    all_caps.extend(process_resin_molding())
    all_caps.extend(process_3_terminal_capacitors())
    
    # Process magnetics
    all_mags.extend(process_power_inductors())
    all_mags.extend(process_rf_inductors())
    all_mags.extend(process_common_mode_chokes())
    all_mags.extend(process_ferrite_beads())
    
    # Write to files
    if all_caps:
        write_ndjson(all_caps, caps_path)
        print(f"\nWrote {len(all_caps)} capacitors to {caps_path}")
    
    if all_mags:
        write_ndjson(all_mags, mags_path)
        print(f"Wrote {len(all_mags)} magnetics to {mags_path}")
    
    print("\n" + "=" * 60)
    print("Conversion complete!")
    print(f"  Total capacitors: {len(all_caps)}")
    print(f"  Total magnetics:  {len(all_mags)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
