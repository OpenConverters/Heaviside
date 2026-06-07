#!/usr/bin/env python3
"""Inspect the picked core's isat value and provenance after full enrichment."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.basicConfig(level=logging.ERROR)

from heaviside.pipeline.cre import ReferenceSpec
from heaviside.pipeline.full_design import full_design

spec = ReferenceSpec(
    topology="buck",
    vin_min=4.5,
    vin_nom=12,
    vin_max=16,
    vout=3.3,
    iout=3,
    pout=9.9,
    fsw=1100000,
    efficiency_target=0.917,
)
s1, s2, outcomes = full_design(
    spec.to_heaviside_spec(), n_candidates_per_topology=1, parallel=False
)
o = outcomes[0]
tas = o.tas


def walk(obj, depth=0):
    """Find any dict with an 'isat' key."""
    if isinstance(obj, dict):
        if "isat" in obj or "ipeak_worst" in obj or "isat_provenance" in obj:
            print(f"  isat={obj.get('isat')} ipeak_worst={obj.get('ipeak_worst')}")
            prov = obj.get("isat_provenance") or obj.get("provenance")
            print(f"  provenance={prov}")
        for v in obj.values():
            walk(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            walk(v, depth + 1)


print("=== TAS top keys ===")
print(list(tas.keys()) if isinstance(tas, dict) else type(tas))
print("=== isat hunt ===")
walk(tas)
