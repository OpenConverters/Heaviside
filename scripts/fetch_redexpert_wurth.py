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
    sys.exit(run_gaps() if mode == "gaps" else run_fill())
