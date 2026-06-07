#!/usr/bin/env python3
"""Run the CR pipeline on all 10 Proteus golden designs and compare results.

Usage:
  MOONSHOT_API_KEY=sk-... python scripts/run_all_crossref.py
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO)

PROTEUS_DIR = Path("/home/alf/OpenConverters/Proteus/tests/reference_designs/crossref_wurth")
OUTPUT_BASE = Path("/home/alf/OpenConverters/Heaviside/tests/crossref_output")

DESIGNS = [d.name for d in sorted(PROTEUS_DIR.iterdir()) if d.is_dir()]

# Context hints per design (helps the LLM)
CONTEXTS = {
    "EVL1653F-TF-00A": "Eval board for LT1653F boost converter",
    "EVQ3359C-LE-00A": "MPS EVQ3359C-LE-00A dual-phase synchronous buck, 60V input, ~5A/phase",
    "eval-lt7153sp-az": "Analog Devices LT7153SP eval board, step-down regulator",
    "eval-lt7176-az": "Analog Devices LT7176 eval board, step-down regulator",
    "eval-lt83401-lt83402-az": "Analog Devices LT83401/LT83402 eval board, LED driver",
    "infineon-eval-7136u-100v-ganc-half-bridge-evaluation-board-with-100v-coolgan-power-transistor-and-eicedriver-1edn7136u-gatedriver-userguide-usermanual-en": "Infineon 100V GaN half-bridge eval board",
    "lt80602-lt80603-lt80603a": "Analog Devices LT80602/LT80603 eval board",
    "lt80603evkit": "Analog Devices LT80603 eval kit",
    "lt83401-lt83402": "Analog Devices LT83401/LT83402 LED driver",
    "um3491-getting-started-with-steval0606yadj-evaluation-board-based-on-dcp0606qtry-automotive-6-v--6-a-stepdown-converter-stmicroelectronics": "ST STEVAL-0606YADJ 6V/6A automotive step-down converter",
}


def run_one(name: str) -> dict:
    from heaviside.pipeline.crossref_pipeline import run_crossref_pipeline
    from heaviside.report.crossref_html import render_crossref_html

    bom_path = PROTEUS_DIR / name / "bom_full.json"
    bom = json.loads(bom_path.read_text())

    t0 = time.time()
    outcome = run_crossref_pipeline(
        bom,
        "Wurth Elektronik",
        circuit_context=CONTEXTS.get(name, name),
        verbose=True,
    )
    elapsed = time.time() - t0

    # Save outcome
    out_dir = OUTPUT_BASE / name
    out_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "target_manufacturer": outcome.target_manufacturer,
        "passed": outcome.passed,
        "elapsed_s": round(elapsed, 1),
        "diagnostics": list(outcome.diagnostics),
        "guardrail_log": list(outcome.guardrail_log),
        "otto_log": outcome.otto_log,
        "review_verdicts": list(outcome.review_verdicts),
        "reviewer_log": outcome.reviewer_log,
        "components": [],
    }
    for c in outcome.components:
        result["components"].append(
            {
                "ref_des": c.ref_des,
                "component_type": c.component_type,
                "original_mpn": c.original_mpn,
                "substitute_mpn": c.substitute_mpn,
                "status": c.status.value,
                "notes": c.notes,
            }
        )

    (out_dir / "outcome.json").write_text(json.dumps(result, indent=2) + "\n")

    # Generate HTML report
    html = render_crossref_html(
        result,
        title=name,
        circuit_context=CONTEXTS.get(name, ""),
    )
    (out_dir / "report.html").write_text(html)

    # Summary stats
    from collections import Counter

    counts = Counter(c.status.value for c in outcome.components)
    total = len(outcome.components)
    replaced = counts.get("recommended", 0) + counts.get("partial", 0) + counts.get("exact", 0)

    return {
        "name": name,
        "total": total,
        "replaced": replaced,
        "coverage": f"{100 * replaced / total:.0f}%" if total else "0%",
        "passed": outcome.passed,
        "elapsed": f"{elapsed:.0f}s",
        "review": outcome.review_verdicts[-1].get("verdict", "?")
        if outcome.review_verdicts
        else "?",
    }


def main():
    skip = set()
    if "--skip-done" in sys.argv:
        for name in DESIGNS:
            if (OUTPUT_BASE / name / "outcome.json").exists():
                skip.add(name)
                print(f"SKIP {name} (already done)")

    results = []
    for i, name in enumerate(DESIGNS):
        if name in skip:
            # Load existing result
            existing = json.loads((OUTPUT_BASE / name / "outcome.json").read_text())
            from collections import Counter

            counts = Counter(c["status"] for c in existing["components"])
            total = len(existing["components"])
            replaced = (
                counts.get("recommended", 0) + counts.get("partial", 0) + counts.get("exact", 0)
            )
            results.append(
                {
                    "name": name,
                    "total": total,
                    "replaced": replaced,
                    "coverage": f"{100 * replaced / total:.0f}%" if total else "0%",
                    "passed": existing["passed"],
                    "elapsed": f"{existing.get('elapsed_s', '?')}s",
                    "review": "cached",
                }
            )
            continue

        print(f"\n{'=' * 70}")
        print(f"[{i + 1}/{len(DESIGNS)}] {name}")
        print(f"{'=' * 70}")
        try:
            r = run_one(name)
            results.append(r)
            print(f"  → {r['coverage']} coverage, {r['review']}, {r['elapsed']}")
        except Exception as exc:
            print(f"  FAILED: {exc}")
            results.append(
                {
                    "name": name,
                    "total": 0,
                    "replaced": 0,
                    "coverage": "ERR",
                    "passed": False,
                    "elapsed": "?",
                    "review": f"ERROR: {exc}",
                }
            )

    # Summary table
    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    print(f"{'Design':<40} {'BOM':>4} {'Würth':>5} {'Cov':>5} {'Review':>10} {'Time':>6}")
    print("-" * 75)
    for r in results:
        print(
            f"{r['name'][:39]:<40} {r['total']:>4} {r['replaced']:>5} "
            f"{r['coverage']:>5} {str(r['review'])[:10]:>10} {r['elapsed']:>6}"
        )

    total_comps = sum(r["total"] for r in results)
    total_replaced = sum(r["replaced"] for r in results)
    total_passed = sum(1 for r in results if r["passed"])
    print("-" * 75)
    print(
        f"{'TOTAL':<40} {total_comps:>4} {total_replaced:>5} "
        f"{f'{100 * total_replaced / total_comps:.0f}%' if total_comps else '?':>5} "
        f"{f'{total_passed}/{len(results)}':>10}"
    )


if __name__ == "__main__":
    main()
