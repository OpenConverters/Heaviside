#!/usr/bin/env python3
"""Run the unified CRE→CR pipeline on all golden reference designs.

Compares results against Proteus golden reports.

Usage:
    python scripts/run_all_cre_cr.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not os.environ.get("MOONSHOT_API_KEY"):
    os.environ["MOONSHOT_API_KEY"] = os.environ.get("MOONSHOT_API_KEY", "")

logging.basicConfig(level=logging.INFO)

PROTEUS_DIR = Path("/home/alf/OpenConverters/Proteus/tests/reference_designs")

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
    "infineon-eval-7136u-gan",
]

PROTEUS_CR_DIR = Path("/home/alf/OpenConverters/Proteus/tests/reference_designs/crossref_wurth")

# Map design names to their Proteus CR directory names (some differ)
_CR_DIR_MAP = {
    "infineon-eval-7136u-gan": "infineon-eval-7136u-100v-ganc-half-bridge-evaluation-board-with-100v-coolgan-power-transistor-and-eicedriver-1edn7136u-gatedriver-userguide-usermanual-en",
}


PROTEUS_CR_DIR = Path("/home/alf/OpenConverters/Proteus/tests/reference_designs/crossref_wurth")


def run_one(name: str) -> dict:
    from heaviside.pipeline.crossref_pipeline import run_crossref_with_cre

    pdf = PROTEUS_DIR / f"{name}.pdf"
    if not pdf.exists():
        return {"name": name, "error": f"PDF not found: {pdf}"}

    # Use Proteus BOM when available (more complete than LLM extraction)
    cr_dir_name = _CR_DIR_MAP.get(name, name)
    proteus_bom_path = PROTEUS_CR_DIR / cr_dir_name / "bom_full.json"
    source_bom = None
    if proteus_bom_path.exists():
        source_bom = json.loads(proteus_bom_path.read_text())

    from heaviside.agents.llm_call import reset_token_usage

    reset_token_usage()

    t0 = time.time()
    outcome = run_crossref_with_cre(
        name,
        "Würth Elektronik",
        pdf_path=pdf,
        source_bom_override=source_bom,
    )
    elapsed = time.time() - t0

    n_total = len(outcome.components)
    n_recommended = sum(1 for c in outcome.components if c.status.value == "recommended")
    n_exact = sum(1 for c in outcome.components if c.status.value == "exact")
    n_partial = sum(1 for c in outcome.components if c.status.value == "partial")
    n_nosub = sum(1 for c in outcome.components if c.status.value == "no_substitute")
    n_keep = sum(1 for c in outcome.components if c.status.value == "keep_original")
    coverage = (n_recommended + n_exact + n_partial) / n_total if n_total else 0

    # Get actual token usage
    from heaviside.agents.llm_call import get_token_usage

    usage = get_token_usage()
    # kimi-k2.5 pricing: $0.002/1K input, $0.01/1K output
    est_cost = (usage["input"] * 0.002 + usage["output"] * 0.01) / 1000

    return {
        "name": name,
        "passed": outcome.passed,
        "elapsed_s": round(elapsed, 1),
        "total": n_total,
        "recommended": n_recommended,
        "exact": n_exact,
        "partial": n_partial,
        "no_substitute": n_nosub,
        "keep_original": n_keep,
        "coverage": round(coverage, 2),
        "guardrails": len(outcome.guardrail_log),
        "est_cost_usd": round(est_cost, 2),
    }


def main():
    print(f"Running CRE→CR on {len(GOLDEN_DESIGNS)} golden designs")
    results = []

    for i, name in enumerate(GOLDEN_DESIGNS):
        print(f"\n{'=' * 70}")
        print(f"[{i + 1}/{len(GOLDEN_DESIGNS)}] {name}")
        print(f"{'=' * 70}")
        try:
            r = run_one(name)
            results.append(r)
            print(
                f"  → {r['total']} components: {r['recommended']}R {r['exact']}E "
                f"{r['partial']}P {r['no_substitute']}N {r['keep_original']}K "
                f"| coverage={r['coverage']:.0%} | {r['guardrails']} guardrails "
                f"| {r['elapsed_s']}s | ~${r.get('est_cost_usd', 0):.2f}",
                flush=True,
            )
        except Exception as exc:
            traceback.print_exc()
            results.append({"name": name, "error": str(exc)})

    # Summary
    print(f"\n{'=' * 70}")
    print("CRE→CR SUMMARY")
    print(f"{'=' * 70}")
    print(
        f"{'Design':<40s} {'Total':>5} {'R':>3} {'E':>3} {'P':>3} {'N':>3} {'K':>3} {'Cov':>5} {'GR':>3} {'Time':>6} {'Cost':>6}"
    )
    print("-" * 90)
    total_time = 0
    total_cost = 0
    for r in results:
        if "error" in r:
            print(f"{r['name'][:39]:<40s} ERROR: {r['error'][:45]}")
            continue
        t = r.get("elapsed_s", 0)
        c = r.get("est_cost_usd", 0)
        total_time += t
        total_cost += c
        print(
            f"{r['name'][:39]:<40s} {r['total']:5d} {r['recommended']:3d} "
            f"{r['exact']:3d} {r['partial']:3d} {r['no_substitute']:3d} "
            f"{r['keep_original']:3d} {r['coverage']:4.0%} {r['guardrails']:3d} "
            f"{t:5.0f}s ${c:.2f}"
        )
    print("-" * 90)
    print(
        f"{'TOTAL':<40s} {'':>5} {'':>3} {'':>3} {'':>3} {'':>3} {'':>3} {'':>5} {'':>3} "
        f"{total_time:5.0f}s ${total_cost:.2f}"
    )


if __name__ == "__main__":
    main()
