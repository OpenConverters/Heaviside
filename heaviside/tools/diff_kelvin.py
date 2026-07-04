"""Differential fuzzer: HS selector (streaming) vs Kelvin (index) over the LIVE TAS DB.

Runs both implementations on the same designRequirements and asserts identical decisions
(chosen MPN + rejection histogram). Unlike the committed golden (fixture-scoped, exact), this
exercises the full 765k-row catalogs and the index path, and re-runs against whatever the
nightly has appended — so drift shows up here. Any divergence is a bug (no known-noise list).

  PyKelvin on PYTHONPATH (Kelvin/build); then:
  python -m heaviside.tools.diff_kelvin --data /home/alf/PSMA/TAS/data --cache ~/.kelvin/index \
         --seed 0 --cases 200
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from heaviside.catalogue import kirchhoff_fill as kf
from heaviside.catalogue.selector import (
    MosfetTiebreaker,
    SelectionError,
    select_capacitor,
    select_diode,
    select_mosfet,
    select_resistor,
)


def _hs_mosfet(req, op_fsw, data_dir):
    mc = kf._mosfet_constraints(req, op_fsw=op_fsw)
    tb = MosfetTiebreaker.LOWEST_TOTAL_LOSS if mc.op_fsw is not None else MosfetTiebreaker.LOWEST_RDS_ON
    return select_mosfet(mc, tiebreaker=tb, tas_data_dir=data_dir)


def _hs_diode(req, data_dir):
    from heaviside.catalogue.selector import DiodeTiebreaker
    return select_diode(kf._diode_constraints(req), tiebreaker=DiodeTiebreaker.LOWEST_VF,
                        tas_data_dir=data_dir)


def _hs_capacitor(req, data_dir):
    from heaviside.catalogue.selector import CapacitorTiebreaker
    return select_capacitor(kf._capacitor_constraints(req), tiebreaker=CapacitorTiebreaker.LOWEST_ESR,
                           tas_data_dir=data_dir)


def _hs_resistor(req, data_dir):
    from heaviside.catalogue.selector import ResistorConstraints
    target = float(req["resistance"]["nominal"])
    return select_resistor(ResistorConstraints(target_ohms=target,
                                               max_tolerance=float(req.get("tolerance", 0.05)),
                                               max_value_deviation=0.2), tas_data_dir=data_dir)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--cases", type=int, default=200)
    args = ap.parse_args()

    import PyKelvin  # noqa: E402
    eng = PyKelvin.Engine(args.data, args.cache, True)
    rng = random.Random(args.seed)
    mism = 0
    checked = 0

    def kelvin(category, req, options):
        try:
            r = eng.select(category, req, options)
            cands = r["candidates"]
            return ("ok", cands[0]["mpn"] if cands else None, r.get("rejections", {}))
        except PyKelvin.NoCandidates as e:
            payload = json.loads(str(e).split("\n")[0]) if str(e).startswith("{") else {}
            return ("err", None, payload.get("rejections", {}))

    def hs(fn, *a):
        try:
            return ("ok", fn(*a).chosen.mpn, {})
        except SelectionError as e:
            return ("err", None, dict(e.rejection_counts))

    for _ in range(args.cases):
        which = rng.choice(["mosfet", "diode", "capacitor", "resistor"])
        if which == "mosfet":
            req = {"ratedDrainSourceVoltage": rng.choice([30, 60, 100, 250, 400, 650, 900]),
                   "ratedContinuousDrainCurrent": rng.choice([1, 5, 10, 20, 50]),
                   "maximumOnResistance": rng.choice([0.005, 0.02, 0.1, 0.5])}
            fsw = rng.choice([None, 100000.0])
            k = kelvin("mosfet", req, {"opFsw": fsw} if fsw else {})
            h = hs(_hs_mosfet, req, fsw, Path(args.data))
        elif which == "diode":
            req = {"ratedReverseVoltage": rng.choice([40, 60, 100, 200, 600]),
                   "ratedForwardCurrent": rng.choice([1, 5, 10, 30])}
            k = kelvin("diode", req, {})
            h = hs(_hs_diode, req, Path(args.data))
        elif which == "capacitor":
            req = {"capacitance": {"nominal": rng.choice([1e-9, 1e-7, 1e-6, 1e-5, 1e-4])},
                   "ratedVoltage": rng.choice([16, 25, 50, 100, 450])}
            k = kelvin("capacitor", req, {})
            h = hs(_hs_capacitor, req, Path(args.data))
        else:
            req = {"resistance": {"nominal": rng.choice([1.0, 100.0, 1e3, 1e4, 1e5])},
                   "tolerance": 0.05}
            k = kelvin("resistor", req, {})
            h = hs(_hs_resistor, req, Path(args.data))

        checked += 1
        # Compare chosen MPN (candidates[0]) and, on the error path, the rejection histograms.
        if k[0] != h[0] or k[1] != h[1]:
            mism += 1
            print(f"MISMATCH [{which}] req={req}\n  HS={h}\n  Kelvin={k}")
        elif h[0] == "err" and k[2] != h[2]:
            mism += 1
            print(f"HISTOGRAM MISMATCH [{which}] req={req}\n  HS={h[2]}\n  Kelvin={k[2]}")

    print(f"\nchecked {checked} cases, {mism} mismatch(es)")
    return 1 if mism else 0


if __name__ == "__main__":
    sys.exit(main())
