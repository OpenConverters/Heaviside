#!/usr/bin/env python3
"""Run the CRE virtual test bench on all 10 reference design PDFs.

Usage:
  MOONSHOT_API_KEY=sk-... python scripts/run_all_cre.py
"""

from __future__ import annotations

import json
import logging
import sys
import time
import traceback
from pathlib import Path

logging.basicConfig(level=logging.INFO)

PROTEUS_DIR = Path("/home/alf/OpenConverters/Proteus/tests/reference_designs")
OUTPUT_BASE = Path("/home/alf/OpenConverters/Heaviside/tests/cre_output")

DESIGNS = sorted(
    p.stem for p in PROTEUS_DIR.glob("*.pdf")
)


def run_one(name: str) -> dict:
    from heaviside.pipeline.cre_pipeline import run_cre_pipeline

    pdf = PROTEUS_DIR / f"{name}.pdf"
    if not pdf.exists():
        return {"name": name, "error": f"PDF not found: {pdf}"}

    t0 = time.time()
    outcome = run_cre_pipeline(name, pdf_path=pdf, verbose=True)
    elapsed = time.time() - t0

    out_dir = OUTPUT_BASE / name
    out_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "name": name,
        "passed": outcome.passed,
        "elapsed_s": round(elapsed, 1),
        "topology": outcome.ref_spec.topology if outcome.ref_spec else "?",
        "vin_max": outcome.ref_spec.vin_max if outcome.ref_spec else 0,
        "vout": outcome.ref_spec.vout if outcome.ref_spec else 0,
        "pout": outcome.ref_spec.pout if outcome.ref_spec else 0,
        "bom_count": len(outcome.ref_bom),
        "comparisons": [
            {
                "sim_eff": c.sim_efficiency,
                "claimed_eff": c.claimed_efficiency,
                "delta_pp": c.efficiency_delta_pp,
                "sim_vout": c.sim_vout,
                "claimed_vout": c.claimed_vout,
                "vout_err": c.vout_error_pct,
                "passed": c.passed,
                "diagnosis": c.diagnosis[:200] if c.diagnosis else "",
            }
            for c in outcome.comparisons
        ],
        "diagnostics": list(outcome.diagnostics),
    }

    (out_dir / "outcome.json").write_text(json.dumps(result, indent=2) + "\n")

    return result


def main():
    print(f"Found {len(DESIGNS)} reference design PDFs")
    results = []

    for i, name in enumerate(DESIGNS):
        print(f"\n{'='*70}")
        print(f"[{i+1}/{len(DESIGNS)}] {name}")
        print(f"{'='*70}")
        try:
            r = run_one(name)
            results.append(r)
            topo = r.get("topology", "?")
            bom = r.get("bom_count", 0)
            passed = "PASS" if r.get("passed") else "FAIL"
            elapsed = r.get("elapsed_s", "?")
            comps = r.get("comparisons", [])
            if comps:
                last = comps[-1]
                print(f"  → {topo} | BOM={bom} | η_sim={last['sim_eff']:.1%} "
                      f"η_claimed={last['claimed_eff']:.1%} Δ={last['delta_pp']:.1f}pp "
                      f"| {passed} | {elapsed}s")
            else:
                print(f"  → {topo} | BOM={bom} | no simulation | {passed} | {elapsed}s")
        except Exception as exc:
            traceback.print_exc()
            results.append({"name": name, "error": str(exc), "passed": False})

    # Summary
    print(f"\n{'='*70}")
    print("CRE VIRTUAL TEST BENCH SUMMARY")
    print(f"{'='*70}")
    print(f"{'Design':<45s} {'Topo':<10s} {'BOM':>4} {'η_sim':>6} {'η_ref':>6} {'Δpp':>5} {'Vout':>6} {'':>5}")
    print("-" * 90)
    for r in results:
        if "error" in r:
            print(f"{r['name'][:44]:<45s} ERROR: {r['error'][:40]}")
            continue
        topo = r.get("topology", "?")[:9]
        bom = r.get("bom_count", 0)
        comps = r.get("comparisons", [])
        passed = "PASS" if r.get("passed") else "FAIL"
        if comps:
            last = comps[-1]
            print(f"{r['name'][:44]:<45s} {topo:<10s} {bom:4d} "
                  f"{last['sim_eff']:5.1%} {last['claimed_eff']:5.1%} "
                  f"{last['delta_pp']:4.1f}p {last['sim_vout']:5.1f}V {passed}")
        else:
            print(f"{r['name'][:44]:<45s} {topo:<10s} {bom:4d} "
                  f"{'—':>6s} {'—':>6s} {'—':>5s} {'—':>6s} {passed}")


if __name__ == "__main__":
    main()
