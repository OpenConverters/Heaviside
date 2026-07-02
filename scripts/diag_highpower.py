#!/usr/bin/env python3
"""Diagnose the 600W/25A high-power thermal + saturation failures."""

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.basicConfig(level=logging.ERROR)

from heaviside.pipeline.cre import ReferenceSpec

from heaviside.pipeline.full_design import full_design
from heaviside.pipeline.realism import _categorise, _iter_components

spec = ReferenceSpec(
    topology="buck",
    vin_min=36.0,
    vin_nom=48.0,
    vin_max=60.0,
    vout=24.0,
    iout=25.0,
    pout=600.0,
    fsw=500000,
    efficiency_target=0.965,
)
s1, s2, outcomes = full_design(
    spec.to_heaviside_spec(), n_candidates_per_topology=1, parallel=False
)
# pick the buck outcome
buck = next((o for o in outcomes if o.pick.topology.name == "buck"), outcomes[0])
print(f"=== {buck.pick.topology.name} ===", flush=True)
tas = buck.tas

# loss budget + efficiency
print("loss_budget:", json.dumps(tas.get("loss_budget", {}), indent=2)[:500], flush=True)
sim = tas.get("simulation_results", {})
for _k, v in sim.items() if isinstance(sim, dict) else []:
    if isinstance(v, dict) and "efficiency_analyst" in v:
        print(
            f"  eta_analyst={v.get('efficiency_analyst')} pin={v.get('pin_analyst')} ploss={v.get('total_loss_analyst')}",
            flush=True,
        )

# per-component thermal + ratings
for _stage, comp in _iter_components(tas):
    cat = _categorise(comp)
    if cat not in ("mosfet", "diode", "magnetic"):
        continue
    name = comp.get("name")
    prov = comp.get("selection_provenance") or {}
    print(f"  {name} ({cat}) mpn={prov.get('mpn', '')}", flush=True)
    for f in (
        "rds_on",
        "vds_rated",
        "vds_stress",
        "id_rated",
        "id_stress",
        "rth_ja",
        "tj",
        "tj_max",
        "isat",
        "ipeak_worst",
        "qg_total",
    ):
        if f in comp:
            print(f"      {f}={comp[f]}", flush=True)
