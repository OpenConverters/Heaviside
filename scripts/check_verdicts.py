#!/usr/bin/env python3
"""Check which realism checks fail for each design."""

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MOONSHOT_API_KEY", os.environ.get("MOONSHOT_API_KEY", ""))
logging.basicConfig(level=logging.WARNING)

from heaviside.pipeline.cre import ReferenceSpec
from heaviside.pipeline.full_design import full_design

specs = [
    (
        "EVL1653F",
        ReferenceSpec(
            topology="buck",
            vin_min=4.5,
            vin_nom=12,
            vin_max=16,
            vout=3.3,
            iout=3,
            pout=9.9,
            fsw=1100000,
            efficiency_target=0.917,
        ),
    ),
    (
        "lt80602",
        ReferenceSpec(
            topology="buck",
            vin_min=4.5,
            vin_nom=24,
            vin_max=65,
            vout=5,
            iout=3.5,
            pout=17.5,
            fsw=400000,
            efficiency_target=0.926,
        ),
    ),
    (
        "um3491",
        ReferenceSpec(
            topology="buck",
            vin_min=6,
            vin_nom=12,
            vin_max=36,
            vout=3.3,
            iout=6,
            pout=19.8,
            fsw=500000,
            efficiency_target=0.9,
        ),
    ),
]

for name, spec in specs:
    print(f"\n=== {name} ===", flush=True)
    d = spec.to_heaviside_spec()
    try:
        s1, s2, outcomes = full_design(d, n_candidates_per_topology=1, parallel=False)
        if not outcomes:
            print("  NO OUTCOMES")
            continue
        o = outcomes[0]
        v = o.verdict_dict
        print(f"  Topology: {o.pick.topology.name}")
        print(f"  Verdict: {v['verdict']} ({v['summary']})")
        for chk in v.get("checks", []):
            s = chk.get("status", "?")
            n = chk.get("name", "?")
            val = chk.get("value", "")
            thr = chk.get("threshold", "")
            mark = "PASS" if s == "pass" else "FAIL" if s == "fail" else "N/A"
            extra = f" (val={val}, thresh={thr})" if s == "fail" else ""
            print(f"  {mark} {n}{extra}")
    except Exception as e:
        print(f"  ERROR: {e}")
