#!/usr/bin/env python3
"""Run CRE→CR designs 2-4 (EVQ3359C, eval-lt7153sp, eval-lt7176)."""

import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MOONSHOT_API_KEY", os.environ.get("MOONSHOT_API_KEY", ""))
logging.basicConfig(level=logging.WARNING)

PROTEUS_DIR = Path("/home/alf/OpenConverters/Proteus/tests/reference_designs")
PROTEUS_CR_DIR = Path("/home/alf/OpenConverters/Proteus/tests/reference_designs/crossref_wurth")

DESIGNS = ["EVQ3359C-LE-00A", "eval-lt7153sp-az", "eval-lt7176-az"]

from heaviside.agents.llm_call import get_token_usage, reset_token_usage
from heaviside.pipeline.crossref_pipeline import run_crossref_with_cre

for i, name in enumerate(DESIGNS):
    print(f"[{i + 1}/{len(DESIGNS)}] {name}", flush=True)
    reset_token_usage()
    bom_path = PROTEUS_CR_DIR / name / "bom_full.json"
    bom = json.loads(bom_path.read_text()) if bom_path.exists() else None
    try:
        t0 = time.time()
        outcome = run_crossref_with_cre(
            name, "Würth Elektronik", pdf_path=PROTEUS_DIR / f"{name}.pdf", source_bom_override=bom
        )
        elapsed = time.time() - t0
        n = len(outcome.components)
        found = sum(
            1 for c in outcome.components if c.status.value in ("recommended", "exact", "partial")
        )
        usage = get_token_usage()
        cost = (usage["input"] * 0.002 + usage["output"] * 0.01) / 1000
        print(
            f"  {found}/{n} = {found / n * 100:.0f}% | {elapsed:.0f}s | ${cost:.2f} | {usage['calls']} calls",
            flush=True,
        )
    except Exception:
        traceback.print_exc()
        print("  ERROR", flush=True)
