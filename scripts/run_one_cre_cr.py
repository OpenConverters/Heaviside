#!/usr/bin/env python3
"""Run CRE→CR on a single design. Usage: python scripts/run_one_cre_cr.py <design-name>"""

import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MOONSHOT_API_KEY", "sk-viKudfa58QW8GjUm8aYxkfv5hmz0i5Y3HRdMKKpphPUupleQ")
logging.basicConfig(level=logging.WARNING)

PROTEUS_DIR = Path("/home/alf/OpenConverters/Proteus/tests/reference_designs")
PROTEUS_CR_DIR = Path("/home/alf/OpenConverters/Proteus/tests/reference_designs/crossref_wurth")
_CR_DIR_MAP = {
    "infineon-eval-7136u-gan": "infineon-eval-7136u-100v-ganc-half-bridge-evaluation-board-with-100v-coolgan-power-transistor-and-eicedriver-1edn7136u-gatedriver-userguide-usermanual-en",
}

name = sys.argv[1]
from heaviside.agents.llm_call import get_token_usage, reset_token_usage
from heaviside.pipeline.crossref_pipeline import run_crossref_with_cre

reset_token_usage()
cr_dir = _CR_DIR_MAP.get(name, name)
bom_path = PROTEUS_CR_DIR / cr_dir / "bom_full.json"
bom = json.loads(bom_path.read_text()) if bom_path.exists() else None

t0 = time.time()
outcome = run_crossref_with_cre(
    name, "Würth Elektronik", pdf_path=PROTEUS_DIR / f"{name}.pdf", source_bom_override=bom
)
elapsed = time.time() - t0

n = len(outcome.components)
found = sum(1 for c in outcome.components if c.status.value in ("recommended", "exact", "partial"))
usage = get_token_usage()
cost = (usage["input"] * 0.002 + usage["output"] * 0.01) / 1000
print(
    f"{name}: {found}/{n} = {found / n * 100:.0f}% | {elapsed:.0f}s | ${cost:.2f} | {usage['calls']} calls"
)
