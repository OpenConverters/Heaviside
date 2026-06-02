#!/usr/bin/env python3
"""Inspect the designer's TAS components to design a BOM extractor for CR."""
import sys, json, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.basicConfig(level=logging.ERROR)

from heaviside.pipeline.cre import ReferenceSpec
from heaviside.pipeline.full_design import full_design
from heaviside.pipeline.realism import _iter_components, _categorise

spec = ReferenceSpec(topology="buck", vin_min=4.5, vin_nom=12, vin_max=16,
                     vout=3.3, iout=3, pout=9.9, fsw=1100000, efficiency_target=0.917)
s1, s2, outcomes = full_design(spec.to_heaviside_spec(), n_candidates_per_topology=1, parallel=False)
o = outcomes[0]

seen = set()
for stage_name, comp in _iter_components(o.tas):
    cat = _categorise(comp)
    name = comp.get("name") or comp.get("ref_des") or comp.get("reference") or "?"
    prov = comp.get("selection_provenance") or {}
    mpn = prov.get("mpn") or comp.get("mpn") or ""
    mfr = prov.get("manufacturer") or comp.get("manufacturer") or ""
    val = comp.get("value") or ""
    if name in seen:
        continue
    seen.add(name)
    print(f"  ref={name:8s} cat={cat:12s} mpn={mpn[:24]:24s} mfr={mfr[:15]:15s} val={val}")
    # Dump keys of first few for structure
print("\n=== sample component keys ===")
for i, (sn, comp) in enumerate(_iter_components(o.tas)):
    if i >= 3:
        break
    print(f"  {comp.get('name','?')}: {sorted(comp.keys())}")
