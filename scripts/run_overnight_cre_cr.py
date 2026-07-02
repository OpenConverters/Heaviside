#!/usr/bin/env python3
"""Overnight CRE→CR driver: run the remaining golden designs, track per-design
cost + cumulative total, and persist results incrementally to JSON so the run
survives interruptions. Usage: python scripts/run_overnight_cre_cr.py
"""

import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.basicConfig(level=logging.WARNING)
if not os.environ.get("MOONSHOT_API_KEY"):
    raise SystemExit(
        "MOONSHOT_API_KEY not set — source ./.env first (set -a && . ./.env && set +a)"
    )

PROTEUS_DIR = Path("/home/alf/OpenConverters/Proteus/tests/reference_designs")
PROTEUS_CR_DIR = PROTEUS_DIR / "crossref_wurth"
_CR_DIR_MAP = {
    "infineon-eval-7136u-gan": "infineon-eval-7136u-100v-ganc-half-bridge-evaluation-board-with-100v-coolgan-power-transistor-and-eicedriver-1edn7136u-gatedriver-userguide-usermanual-en",
}
RESULTS_PATH = Path("/tmp/cre_cr_overnight_results.json")

# lt80602-lt80603-lt80603a already run separately ($1.11, 8/11) — skip it.
DESIGNS = [
    "EVL1653F-TF-00A",
    "EVQ3359C-LE-00A",
    "eval-lt7153sp-az",
    "eval-lt7176-az",
    "eval-lt83401-lt83402-az",
    "lt80603evkit",
    "lt83401-lt83402",
    "um3491-getting-started-with-steval0606yadj-evaluation-board-based-on-dcp0606qtry-automotive-6-v--6-a-stepdown-converter-stmicroelectronics",
    "infineon-eval-7136u-gan",
]


def main() -> int:
    from heaviside.agents.llm_call import get_token_usage, reset_token_usage
    from heaviside.pipeline.crossref_pipeline import run_crossref_with_cre

    results: list[dict] = [
        {
            "name": "lt80602-lt80603-lt80603a",
            "found": 8,
            "total": 11,
            "pct": 73,
            "elapsed_s": 1014,
            "cost_usd": 1.11,
            "calls": 10,
            "note": "run separately",
        }
    ]
    cum_cost = 1.11
    for i, name in enumerate(DESIGNS, start=2):
        print(f"\n{'=' * 70}\n[{i}/{len(DESIGNS) + 1}] {name}\n{'=' * 70}", flush=True)
        reset_token_usage()
        cr_dir = _CR_DIR_MAP.get(name, name)
        bom_path = PROTEUS_CR_DIR / cr_dir / "bom_full.json"
        bom = json.loads(bom_path.read_text()) if bom_path.exists() else None
        t0 = time.time()
        try:
            outcome = run_crossref_with_cre(
                name,
                "Würth Elektronik",
                pdf_path=PROTEUS_DIR / f"{name}.pdf",
                source_bom_override=bom,
            )
            elapsed = time.time() - t0
            n = len(outcome.components)
            found = sum(
                1
                for c in outcome.components
                if c.status.value in ("recommended", "exact", "partial")
            )
            usage = get_token_usage()
            cost = (usage["input"] * 0.002 + usage["output"] * 0.01) / 1000
            cum_cost += cost
            r = {
                "name": name,
                "found": found,
                "total": n,
                "pct": round(found / n * 100) if n else 0,
                "elapsed_s": round(elapsed),
                "cost_usd": round(cost, 2),
                "calls": usage["calls"],
            }
            print(
                f"  → {found}/{n} = {r['pct']}% | {elapsed:.0f}s | ${cost:.2f} "
                f"| {usage['calls']} calls | cumulative ${cum_cost:.2f}",
                flush=True,
            )
        except Exception as exc:
            elapsed = time.time() - t0
            usage = get_token_usage()
            cost = (usage["input"] * 0.002 + usage["output"] * 0.01) / 1000
            cum_cost += cost
            r = {
                "name": name,
                "error": str(exc)[:300],
                "elapsed_s": round(elapsed),
                "cost_usd": round(cost, 2),
                "calls": usage.get("calls", 0),
            }
            print(
                f"  → ERROR: {str(exc)[:200]} | ${cost:.2f} | cumulative ${cum_cost:.2f}",
                flush=True,
            )
            traceback.print_exc()
        results.append(r)
        RESULTS_PATH.write_text(
            json.dumps({"results": results, "cumulative_cost_usd": round(cum_cost, 2)}, indent=2)
        )

    ok = [r for r in results if "error" not in r]
    print(f"\n{'=' * 70}\nOVERNIGHT CRE→CR SUMMARY\n{'=' * 70}")
    for r in results:
        if "error" in r:
            print(f"  {r['name']:55s} ERROR")
        else:
            print(
                f"  {r['name']:55s} {r['found']}/{r['total']} = {r['pct']:3d}%  "
                f"${r['cost_usd']:.2f}  {r['elapsed_s']}s"
            )
    avg = sum(r["pct"] for r in ok) / len(ok) if ok else 0
    print(f"\n  designs: {len(results)}  ok: {len(ok)}  avg coverage: {avg:.0f}%")
    print(f"  TOTAL COST: ${cum_cost:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
