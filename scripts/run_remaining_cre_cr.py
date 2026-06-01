#!/usr/bin/env python3
"""Run CRE→CR on remaining golden designs (4-9)."""
import json, logging, os, sys, time, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MOONSHOT_API_KEY", "sk-viKudfa58QW8GjUm8aYxkfv5hmz0i5Y3HRdMKKpphPUupleQ")
logging.basicConfig(level=logging.INFO)

PROTEUS_DIR = Path("/home/alf/OpenConverters/Proteus/tests/reference_designs")
PROTEUS_CR_DIR = Path("/home/alf/OpenConverters/Proteus/tests/reference_designs/crossref_wurth")

REMAINING = [
    "eval-lt7176-az",
    "eval-lt83401-lt83402-az",
    "lt80602-lt80603-lt80603a",
    "lt80603evkit",
    "lt83401-lt83402",
    "um3491-getting-started-with-steval0606yadj-evaluation-board-based-on-dcp0606qtry-automotive-6-v--6-a-stepdown-converter-stmicroelectronics",
]

from heaviside.pipeline.crossref_pipeline import run_crossref_with_cre

for i, name in enumerate(REMAINING):
    print(f"\n[{i+1}/{len(REMAINING)}] {name}", flush=True)
    pdf = PROTEUS_DIR / f"{name}.pdf"
    bom_path = PROTEUS_CR_DIR / name / "bom_full.json"
    bom = json.loads(bom_path.read_text()) if bom_path.exists() else None
    try:
        t0 = time.time()
        outcome = run_crossref_with_cre(name, "Würth Elektronik", pdf_path=pdf, source_bom_override=bom)
        n = len(outcome.components)
        found = sum(1 for c in outcome.components if c.status.value in ('recommended','exact','partial'))
        elapsed = time.time() - t0
        print(f"  {found}/{n} = {found/n*100:.0f}% | {elapsed:.0f}s", flush=True)
    except Exception:
        traceback.print_exc()
        print(f"  ERROR", flush=True)
