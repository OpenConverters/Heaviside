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

# Ensure heaviside is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO)

PROTEUS_DIR = Path("/home/alf/OpenConverters/Proteus/tests/reference_designs")
OUTPUT_BASE = Path("/home/alf/OpenConverters/Heaviside/tests/cre_output")

# Golden designs (the 9 from the previous CR pipeline)
GOLDEN_DESIGNS = [
    "EVL1653F-TF-00A",
    "EVQ3359C-LE-00A",
    "eval-lt7153sp-az",
    "eval-lt7176-az",
    "eval-lt83401-lt83402-az",
    "lt80602-lt80603-lt80603a",
    "lt80603evkit",
    "lt83401-lt83402",
    "um3491-getting-started-with-steval0606yadj-evaluation-board-based-on-dcp0606qtry-automotive-6-v--6-a-stepdown-converter-stmicroelectronics",
]

DESIGNS = GOLDEN_DESIGNS if "--golden" in sys.argv else sorted(
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
            if len(comps) >= 2:
                ideal, bom_c = comps[0], comps[-1]
                print(f"  → {topo} | BOM={bom} | ideal η={ideal['sim_eff']:.1%} "
                      f"bom η={bom_c['sim_eff']:.1%} "
                      f"claimed={bom_c['claimed_eff']:.1%} Δ={bom_c['delta_pp']:.1f}pp "
                      f"| {passed} | {elapsed}s")
            elif comps:
                last = comps[-1]
                print(f"  → {topo} | BOM={bom} | η_sim={last['sim_eff']:.1%} "
                      f"η_claimed={last['claimed_eff']:.1%} Δ={last['delta_pp']:.1f}pp "
                      f"| {passed} | {elapsed}s")
            else:
                diags = r.get("diagnostics", [])
                diag_short = "; ".join(d[:60] for d in diags[:2]) if diags else "no sim"
                print(f"  → {topo} | BOM={bom} | {diag_short} | {passed} | {elapsed}s")
        except Exception as exc:
            traceback.print_exc()
            results.append({"name": name, "error": str(exc), "passed": False})

    # Summary
    print(f"\n{'='*70}")
    print("CRE VIRTUAL TEST BENCH SUMMARY")
    print(f"{'='*70}")
    print(f"{'Design':<40s} {'Topo':<10s} {'BOM':>4} {'η_ideal':>7} {'η_bom':>6} {'η_ref':>6} {'Δpp':>5} {'Vout':>6} {'':>5}")
    print("-" * 100)
    for r in results:
        if "error" in r:
            print(f"{r['name'][:39]:<40s} ERROR: {r['error'][:55]}")
            continue
        topo = r.get("topology", "?")[:9]
        bom = r.get("bom_count", 0)
        comps = r.get("comparisons", [])
        passed = "PASS" if r.get("passed") else "FAIL"
        if len(comps) >= 2:
            ideal, last = comps[0], comps[-1]
            print(f"{r['name'][:39]:<40s} {topo:<10s} {bom:4d} "
                  f"{ideal['sim_eff']:6.1%} {last['sim_eff']:5.1%} "
                  f"{last['claimed_eff']:5.1%} "
                  f"{last['delta_pp']:4.1f}p {last['sim_vout']:5.1f}V {passed}")
        elif comps:
            last = comps[-1]
            print(f"{r['name'][:39]:<40s} {topo:<10s} {bom:4d} "
                  f"{'—':>7s} {last['sim_eff']:5.1%} {last['claimed_eff']:5.1%} "
                  f"{last['delta_pp']:4.1f}p {last['sim_vout']:5.1f}V {passed}")
        else:
            diag = "; ".join(d[:60] for d in r.get("diagnostics", [])[:2])
            print(f"{r['name'][:39]:<40s} {topo:<10s} {bom:4d} "
                  f"{'—':>7s} {'—':>6s} {'—':>6s} {'—':>5s} {'—':>6s} {passed}")
            if diag:
                print(f"  diag: {diag[:120]}")


if __name__ == "__main__":
    main()
