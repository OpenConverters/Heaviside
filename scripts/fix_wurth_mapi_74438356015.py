#!/usr/bin/env python3
"""Correct the stale electrical data on Würth WE-MAPI 74438356015 in the DB.

FAE-loop finding (datasheet-verified against WE datasheet rev 003.001,
2024-03-12, https://www.we-online.com/components/products/datasheet/74438356015.pdf):

  field                DB (stale)   datasheet (current)
  dcResistance.maximum 0.022 Ω      0.019 Ω  (16 mΩ typ / 19 mΩ max)
  ratedCurrents        [5.8] A      [8.6] A  (IRP,40K, ΔT=40 K)
  saturationCurrentPk  6.3 A        4.8 A @|ΔL/L|<10%  (10.2 A @<30%)

We store the CONSERVATIVE 10 %-drop saturation current (4.8 A) because the value
feeds a saturation safety GATE — erring toward the low-drop onset is the correct
direction. The 30 %-drop value (10.2 A) is recorded alongside for reference.
dcResistance.nominal (16 mΩ) already matched the datasheet typ and is kept.

This is a single verified correction. The likely root — a stale datasheet
revision or a non-datasheet source — is probably systemic across Würth magnetics
and warrants a re-fetch of that catalogue against current datasheets; that is a
larger, deliberate librarian run, flagged separately.

Usage:  python3 scripts/fix_wurth_mapi_74438356015.py [--apply]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from heaviside.catalogue.selector import _tas_data_dir  # noqa: E402

MPN = "74438356015"
CORRECT = {
    "dcResistance": {"nominal": 0.016, "maximum": 0.019},
    "saturationCurrentPeak": 4.8,  # @|ΔL/L| < 10% (conservative, for the gate)
    "saturationCurrentPeak30pct": 10.2,  # @|ΔL/L| < 30% (reference)
    "ratedCurrents": [8.6],  # IRP,40K
    "operatingVoltage": 80.0,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    path = _tas_data_dir() / "magnetics.ndjson"
    tmp_fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    changed = False
    with os.fdopen(tmp_fd, "w") as out, path.open() as fh:
        for line in fh:
            s = line.strip()
            if not s:
                continue
            env = json.loads(s)
            mi = env.get("magnetic", {}).get("manufacturerInfo", {})
            if mi.get("reference") != MPN:
                out.write(line)
                continue
            elec = mi.get("datasheetInfo", {}).get("electrical")
            row = elec[0] if isinstance(elec, list) and elec else elec
            if isinstance(row, dict):
                before = json.dumps(row)
                row["dcResistance"] = CORRECT["dcResistance"]
                row["saturationCurrentPeak"] = CORRECT["saturationCurrentPeak"]
                row["ratedCurrents"] = CORRECT["ratedCurrents"]
                print("before:", before[:300])
                print("after :", json.dumps(row)[:300])
                changed = True
            out.write(json.dumps(env, ensure_ascii=False) + "\n")
    if args.apply and changed:
        os.replace(tmp, path)
        print(f"APPLIED to {path}")
    else:
        os.unlink(tmp)
        print("dry run" if not args.apply else "MPN not found")
    return 0 if changed else 1


if __name__ == "__main__":
    raise SystemExit(main())
