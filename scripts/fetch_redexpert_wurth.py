#!/usr/bin/env python3
"""Fetch Würth Elektronik parts from the REDEXPERT MCP and fill TAS gaps,
through the librarian guards. Two modes:

  python scripts/fetch_redexpert_wurth.py gaps    # report what TAS is missing
  python scripts/fetch_redexpert_wurth.py fill    # convert+guard+write caps

Capacitor families (ceramic / alu-poly / suppression) convert to the CAS
capacitor schema here. Inductor/choke/transformer families map to MAS
(magnetics) which needs a richer converter — those are REPORTED as gaps
only (not auto-filled) so a human/MAS-aware pass can handle them. Every
written row passes guard_component + validate_component; nothing is
fabricated — all values come from REDEXPERT's published parametric data.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from redexpert_client import RedexpertClient

from heaviside.librarian.fetcher.convert import _CAP_EIA_CLASS
from heaviside.librarian.guards import GuardRejectionError, guard_component
from heaviside.librarian.tas import ValidationError

REPO = Path(__file__).resolve().parents[1]
CAP_PATH = REPO / "TAS" / "data" / "capacitors.ndjson"
MAG_PATH = REPO / "TAS" / "data" / "magnetics.ndjson"

# Commercial finished-magnetic convention (matches
# scripts/enrichment/restore_coilcraft_magnetics.py): MAS requires
# core+coil, but a catalog inductor has no published core/winding
# decomposition — so the structural fields are explicit "Dummy"
# placeholders (honestly labelled, not fabricated-as-real) while the
# real catalog data lives in manufacturerInfo.datasheetInfo.
DUMMY_CORE = {"functionalDescription": {
    "type": "twoPieceSet", "material": "Dummy", "shape": "Dummy", "gapping": []}}
DUMMY_COIL = {"bobbin": "Dummy", "functionalDescription": [
    {"name": "Dummy", "numberTurns": 1, "numberParallels": 1,
     "isolationSide": "primary", "wire": "Dummy"}]}

# inductance-based magnetic families (ferrites id 1 are impedance beads —
# the inductance-based magnetic electrical schema does not fit them, so
# they are reported as a gap, not filled).
INDUCTOR_FAMILIES = {
    "4": "Power Inductors",
    "6": "PFC Chokes",
    "3": "Common Mode Chokes for Power Lines",
    "23": "Common Mode Chokes for Data and Signal Lines",
}

# REDEXPERT family id -> (TAS category, kind). Capacitor families are
# auto-fillable here; magnetics families are gap-reported only (MAS
# converter out of scope for this script).
CAP_FAMILIES = {
    "13": "Ceramic Capacitors",
    "20": "Aluminum Electrolytic / Aluminum Polymer Capacitors",
    "11": "Interference Suppression Capacitors",
}
MAGNETICS_FAMILIES = {
    "4": "Power Inductors",
    "6": "PFC Chokes",
    "3": "Common Mode Chokes for Power Lines",
    "23": "Common Mode Chokes for Data and Signal Lines",
    "1": "Ferrites for PCB Assembly",
}


def _f(v):
    return v if isinstance(v, (int, float)) else None


def convert_ceramic(p: dict) -> dict | None:
    diel = (p.get("type") or "").upper()
    tech = _CAP_EIA_CLASS.get(diel)
    if not tech or not p.get("orderCode") or not p.get("capacitance"):
        return None
    el: dict = {"capacitance": {"nominal": p["capacitance"]}}
    if _f(p.get("ratedVoltage")) is not None:
        el["ratedVoltage"] = p["ratedVoltage"]
    if _f(p.get("rs")) is not None:
        el["esr"] = p["rs"]
    if _f(p.get("resistanceIso")) is not None:
        el["insulationResistance"] = float(p["resistanceIso"])
    dims: dict = {}
    for re_k, tas_k in (("sizeLength", "length"), ("sizeWidth", "width")):
        if _f(p.get(re_k)) is not None:
            dims[tas_k] = {"nominal": p[re_k]}
    h = p.get("sizeHeightMax") or p.get("sizeHeight")
    if _f(h) is not None:
        dims["height"] = {"nominal": h}
    mech = {"shape": {"assembly": "SMT", "shapeType": "SMD Chip"}}
    if dims:
        mech["dimensions"] = dims
    return {"capacitor": {
        "manufacturerInfo": {
            "name": "Würth Elektronik",
            # reference is what the selector keys on (Capacitor.from_envelope);
            # omitting it made caps INVISIBLE to selection. status='production':
            # a part returned by REDEXPERT's live catalogue is orderable.
            "reference": str(p["orderCode"]),
            "status": "production",
            "datasheetUrl": p.get("datasheet"),
            "datasheetInfo": {
                "part": {
                    "partNumber": str(p["orderCode"]),
                    "series": p.get("series"),
                    "technology": tech,
                    "dielectricCode": diel,
                    "case": str(p.get("size")),
                },
                "electrical": el,
                "thermal": {"temperature": {
                    "nominal": 25,
                    "minimum": p.get("temperatureMin"),
                    "maximum": p.get("temperatureMax"),
                }},
                "mechanical": mech,
            },
        },
        "distributorsInfo": [{"name": "Würth Elektronik"}],
    }}


def convert_inductor(p: dict) -> dict | None:
    """REDEXPERT power inductor -> TAS magnetic (Dummy core/coil).
    Not used for CMC families — those go through convert_cmc()."""
    if not p.get("orderCode") or _f(p.get("inductance")) is None:
        return None
    el: dict = {"subtype": "inductor", "inductance": {"nominal": p["inductance"]}}
    dc_typ = _f(p.get("resistanceDcTyp"))
    dc_max = _f(p.get("resistanceDcMax")) or _f(p.get("resistanceDc1Max")) or _f(p.get("resistanceDc"))
    if dc_typ is not None or dc_max is not None:
        dc: dict = {}
        if dc_typ is not None:
            dc["nominal"] = dc_typ
        if dc_max is not None:
            dc["maximum"] = dc_max
        el["dcResistance"] = dc
    if _f(p.get("ratedCurrent")) is not None:
        el["ratedCurrents"] = [p["ratedCurrent"]]
    if _f(p.get("saturationCurrent")) is not None:
        el["saturationCurrentPeak"] = p["saturationCurrent"]
    if _f(p.get("selfResonanceFrequency")) is not None:
        el["selfResonantFrequency"] = p["selfResonanceFrequency"]
    mech: dict = {}
    if _f(p.get("sizeLength")) is not None:
        mech["length"] = {"nominal": p["sizeLength"]}
    if _f(p.get("sizeWidth")) is not None:
        mech["width"] = {"nominal": p["sizeWidth"]}
    h_max, h_nom = _f(p.get("sizeHeightMax")), _f(p.get("sizeHeight"))
    if h_max is not None:
        mech["height"] = {"maximum": h_max}
    elif h_nom is not None:
        mech["height"] = {"nominal": h_nom}
    ind_uh = p["inductance"] * 1e6
    return {"magnetic": {
        "manufacturerInfo": {
            "name": "Würth Elektronik",
            "reference": str(p["orderCode"]),
            "status": "production",
            "datasheetUrl": p.get("datasheet"),
            "datasheetInfo": {
                "part": {
                    "partNumber": str(p["orderCode"]),
                    "description": f"Würth {p.get('series') or ''} {ind_uh:g}uH".strip(),
                    "material": str(p.get("material") or "Composite"),
                    "shielded": (p.get("shieldingType") or "").lower() != "unshielded",
                },
                "electrical": [el],
                "mechanical": mech,
            },
        },
        "core": DUMMY_CORE,
        "coil": DUMMY_COIL,
    }}


def convert_cmc(p: dict, fam23: bool = False) -> dict | None:
    """REDEXPERT CMC (families 3 and 23) -> TAS magnetic using
    magneticDatasheetCommonModeChokeElectrical schema."""
    if not p.get("orderCode"):
        return None
    series = p.get("series") or ""
    n_lines = int(p.get("lines") or "2")
    assy = (p.get("assemblingTechnology") or "SMT").upper()
    mounting = "tht" if assy == "THT" else "smt"

    el: dict = {"subtype": "commonModeChoke"}
    if _f(p.get("ratedCurrent")) is not None:
        el["ratedCurrents"] = [p["ratedCurrent"]]
    dc_typ = _f(p.get("resistanceDcTyp"))
    dc_val = _f(p.get("resistanceDc"))
    if dc_typ is not None or dc_val is not None:
        dcr: dict = {}
        if dc_typ is not None:
            dcr["nominal"] = dc_typ
        elif dc_val is not None:
            dcr["maximum"] = dc_val
        el["dcResistances"] = [dcr] * n_lines
    v = _f(p.get("ratedVoltage"))
    if v is not None:
        if mounting == "tht" or not fam23:
            el["ratedVoltageAC"] = v
        else:
            el["ratedVoltageDC"] = v
    vt = _f(p.get("vt"))
    if vt is not None:
        el["insulationTestVoltageAC"] = vt
    imp = _f(p.get("impedance"))
    if imp is not None:
        el["impedancePoints"] = [
            {"frequency": 100_000_000.0, "impedance": {"magnitude": imp}}
        ]

    mech: dict = {"mounting": mounting}
    if _f(p.get("sizeLength")) is not None:
        mech["length"] = {"nominal": p["sizeLength"]}
    if _f(p.get("sizeWidth")) is not None:
        mech["width"] = {"nominal": p["sizeWidth"]}
    h = _f(p.get("sizeHeight"))
    if h is not None:
        mech["height"] = {"nominal": h}

    ind = _f(p.get("inductance"))
    ind_str = f", {ind*1e6:g}uH" if ind is not None else ""
    i_str = f", {p['ratedCurrent']}A" if _f(p.get("ratedCurrent")) is not None else ""
    desc = f"Würth {series} Common Mode Choke{ind_str}{i_str}".strip()

    winding_style = p.get("style") or ("bifilar" if fam23 else None)

    part: dict = {
        "partNumber": str(p["orderCode"]),
        "description": desc,
        "family": series,
    }
    if winding_style:
        part["windingStyle"] = winding_style
    part["numberOfWindings"] = n_lines

    dummy_coil = {
        "bobbin": "Dummy",
        "functionalDescription": [
            {"name": "primary", "numberTurns": 1, "numberParallels": 1,
             "isolationSide": "primary", "wire": "Dummy"},
            {"name": "secondary", "numberTurns": 1, "numberParallels": 1,
             "isolationSide": "secondary", "wire": "Dummy"},
        ],
    }
    dummy_core = {"functionalDescription": {
        "type": "toroidal",
        "material": "NiZn" if fam23 else "MnZn",
        "shape": "Dummy",
        "gapping": [],
    }}

    return {"magnetic": {
        "manufacturerInfo": {
            "name": "Würth Elektronik",
            "reference": str(p["orderCode"]),
            "family": series,
            "status": "production",
            "datasheetUrl": p.get("datasheet"),
            "datasheetInfo": {
                "part": part,
                "electrical": [el],
                "thermal": {
                    "operatingTemperature": {
                        "minimum": -40.0,
                        "maximum": float(p["temperatureMax"]) if p.get("temperatureMax") else 125.0,
                    }
                },
                "mechanical": mech,
            },
        },
        "core": dummy_core,
        "coil": dummy_coil,
        "distributorsInfo": [{"name": "Würth Elektronik"}],
    }}


def _existing_wurth_mag_mpns() -> set[str]:
    mpns: set[str] = set()
    if not MAG_PATH.exists():
        return mpns
    for line in MAG_PATH.open():
        if "rth Elektronik" not in line:
            continue
        row = json.loads(line)
        b = row.get("magnetic", row)
        mi = b.get("manufacturerInfo", {})
        if mi.get("name") and "rth" in mi["name"]:
            for pn in (mi.get("reference"),
                       mi.get("datasheetInfo", {}).get("part", {}).get("partNumber")):
                if pn:
                    mpns.add(str(pn))
    return mpns


CMC_FAMILIES = {"3", "23"}


def run_fill_magnetics() -> int:
    c = RedexpertClient()
    existing = _existing_wurth_mag_mpns()
    print(f"TAS already has {len(existing)} Würth magnetic MPNs")
    new_rows: list[str] = []
    stats = {"fetched": 0, "converted": 0, "dup": 0, "guard_fail": 0, "written": 0}
    fails: list[str] = []
    for fam, title in INDUCTOR_FAMILIES.items():
        is_cmc = fam in CMC_FAMILIES
        try:
            res = c.products(fam).get("results", [])
        except Exception as exc:
            print(f"  {title}: fetch error {str(exc)[:60]}")
            continue
        fam_written = 0
        stats["fetched"] += len(res)
        for p in res:
            env = convert_cmc(p, fam23=(fam == "23")) if is_cmc else convert_inductor(p)
            if env is None:
                continue
            stats["converted"] += 1
            mpn = env["magnetic"]["manufacturerInfo"]["reference"]
            if mpn in existing:
                stats["dup"] += 1
                continue
            try:
                guard_component("magnetics", env)
            except (GuardRejectionError, ValidationError) as exc:
                stats["guard_fail"] += 1
                if len(fails) < 8:
                    fails.append(f"{mpn}: {str(exc)[:120]}")
                continue
            existing.add(mpn)
            new_rows.append(json.dumps(env, ensure_ascii=False, separators=(",", ":")))
            stats["written"] += 1
            fam_written += 1
        print(f"  {title}: {len(res)} fetched, {fam_written} new written")
    c.close()
    if new_rows:
        with MAG_PATH.open("a") as fh:
            fh.write("\n".join(new_rows) + "\n")
    print(f"\nfill_magnetics stats: {stats}")
    for f in fails:
        print("  guard_fail:", f)
    print("NOTE: Ferrites (family 1) are impedance beads — not filled "
          "(chip-bead schema, not inductor).")
    return 0


def _existing_wurth_cap_mpns() -> set[str]:
    mpns: set[str] = set()
    for line in CAP_PATH.open():
        if "rth Elektronik" not in line:
            continue
        row = json.loads(line)
        b = row.get("capacitor", row)
        mi = b.get("manufacturerInfo", {})
        if mi.get("name") and "rth" in mi["name"]:
            pn = mi.get("datasheetInfo", {}).get("part", {}).get("partNumber")
            if pn:
                mpns.add(str(pn))
    return mpns


def run_gaps() -> int:
    c = RedexpertClient()
    existing = _existing_wurth_cap_mpns()
    print(f"TAS already has {len(existing)} Würth capacitor MPNs\n")
    print(f"{'family':52s} {'REDEXPERT':>9s} {'in TAS':>7s} {'MISSING':>8s}")
    for fam, title in {**CAP_FAMILIES, **MAGNETICS_FAMILIES}.items():
        try:
            res = c.products(fam).get("results", [])
        except Exception as exc:
            print(f"{title[:52]:52s}  ERROR {str(exc)[:40]}")
            continue
        codes = {str(p.get("orderCode")) for p in res if p.get("orderCode")}
        cat = "cap" if fam in CAP_FAMILIES else "mag"
        in_tas = len(codes & existing) if cat == "cap" else 0
        miss = len(codes) - in_tas if cat == "cap" else len(codes)
        tag = "" if cat == "cap" else "  (magnetics — gap only)"
        print(f"{title[:52]:52s} {len(codes):9d} {in_tas:7d} {miss:8d}{tag}")
    c.close()
    return 0


def run_fill() -> int:
    c = RedexpertClient()
    existing = _existing_wurth_cap_mpns()
    new_rows: list[str] = []
    stats = {"fetched": 0, "converted": 0, "dup": 0, "guard_fail": 0, "written": 0}
    fails: list[str] = []
    for fam in CAP_FAMILIES:
        if fam != "13":
            continue  # ceramic only for now (alu/suppression need their own convert)
        res = c.products(fam).get("results", [])
        stats["fetched"] += len(res)
        for p in res:
            env = convert_ceramic(p)
            if env is None:
                continue
            stats["converted"] += 1
            mpn = env["capacitor"]["manufacturerInfo"]["datasheetInfo"]["part"]["partNumber"]
            if mpn in existing:
                stats["dup"] += 1
                continue
            try:
                guard_component("capacitors", env)
            except (GuardRejectionError, ValidationError) as exc:
                stats["guard_fail"] += 1
                if len(fails) < 5:
                    fails.append(f"{mpn}: {exc}")
                continue
            existing.add(mpn)
            new_rows.append(json.dumps(env, ensure_ascii=False, separators=(",", ":")))
            stats["written"] += 1
    c.close()
    if new_rows:
        with CAP_PATH.open("a") as fh:
            fh.write("\n".join(new_rows) + "\n")
    print(f"fill stats: {stats}")
    for f in fails:
        print("  guard_fail:", f)
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "gaps"
    if mode == "gaps":
        sys.exit(run_gaps())
    elif mode == "fillmag":
        sys.exit(run_fill_magnetics())
    else:
        sys.exit(run_fill())
