#!/usr/bin/env python3
import sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.basicConfig(level=logging.ERROR)

from heaviside.pipeline.cre_testbench import _normalize_topology
print("half-bridge ->", _normalize_topology("half-bridge"), flush=True)

from heaviside.pipeline.cre import ReferenceSpec
from heaviside.pipeline.full_design import full_design

# 7136u: non-isolated GaN half-bridge as sync buck, 48V->24V, 25A, 500kHz
spec = ReferenceSpec(topology="buck", vin_min=36.0, vin_nom=48.0, vin_max=60.0,
                     vout=24.0, iout=25.0, pout=600.0, fsw=500000, efficiency_target=0.965)
s1, s2, outcomes = full_design(spec.to_heaviside_spec(), n_candidates_per_topology=1, parallel=False)
print(f"Outcomes: {len(outcomes)}", flush=True)
for o in outcomes[:1]:
    v = o.verdict_dict
    print(f"{o.pick.topology.name}: {v.get('verdict')} ({v.get('summary')})", flush=True)
    for chk in v.get("checks", []):
        if chk.get("status") == "fail":
            print(f"  FAIL {chk['name']} val={chk.get('value')}", flush=True)
