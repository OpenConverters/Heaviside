#!/usr/bin/env python3
"""Verdict check across representative specs incl. the 7136u high-power case."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.basicConfig(level=logging.ERROR)

from heaviside.pipeline.cre import ReferenceSpec
from heaviside.pipeline.full_design import full_design

SPECS = [
    (
        "EVL1653F",
        dict(
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
        dict(
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
        dict(
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
    (
        "7136u",
        dict(
            topology="buck",
            vin_min=36,
            vin_nom=48,
            vin_max=60,
            vout=24,
            iout=25,
            pout=600,
            fsw=500000,
            efficiency_target=0.965,
        ),
    ),
    (
        "EVQ3359C",
        dict(
            topology="boost",
            vin_min=9,
            vin_nom=12,
            vin_max=18,
            vout=36,
            iout=0.16,
            pout=5.76,
            fsw=500000,
            efficiency_target=0.96,
        ),
    ),
]
for name, kw in SPECS:
    spec = ReferenceSpec(**kw)
    try:
        s1, s2, outcomes = full_design(
            spec.to_heaviside_spec(), n_candidates_per_topology=1, parallel=False
        )
        topo = kw["topology"]
        o = next(
            (x for x in outcomes if x.pick.topology.name == topo), outcomes[0] if outcomes else None
        )
        if not o:
            print(f"{name}: NO OUTCOME", flush=True)
            continue
        v = o.verdict_dict
        fails = [
            f"{c['name']}={c.get('value')}"
            for c in v.get("checks", [])
            if c.get("status") == "fail"
        ]
        isat = next(
            (c.get("value") for c in v.get("checks", []) if c["name"] == "inductor_isat_margin"),
            None,
        )
        print(f"{name}: {v.get('verdict')} | isat={isat} | fails={fails}", flush=True)
    except Exception as e:
        print(f"{name}: ERROR {str(e)[:70]}", flush=True)
