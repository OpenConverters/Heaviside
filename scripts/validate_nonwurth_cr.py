#!/usr/bin/env python3
"""Validate the manufacturer-agnostic CR with a non-Würth target (Vishay)."""

import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MOONSHOT_API_KEY", os.environ.get("MOONSHOT_API_KEY", ""))
logging.basicConfig(level=logging.WARNING)

from heaviside.agents.llm_call import get_token_usage, reset_token_usage
from heaviside.pipeline.crossref_pipeline import run_crossref_pipeline

TARGET = sys.argv[2] if len(sys.argv) > 2 else "Vishay"
name = sys.argv[1] if len(sys.argv) > 1 else "EVL1653F-TF-00A"
bom_path = (
    Path("/home/alf/OpenConverters/Proteus/tests/reference_designs/crossref_wurth")
    / name
    / "bom_full.json"
)
bom = json.loads(bom_path.read_text())

print(f"=== CR: {name} → {TARGET} ({len(bom)} components) ===", flush=True)
reset_token_usage()
t0 = time.time()
outcome = run_crossref_pipeline(bom, TARGET, circuit_context=f"{name} cross-ref to {TARGET}")
elapsed = time.time() - t0

n = len(outcome.components)
from collections import Counter

status_counts = Counter(c.status.value for c in outcome.components)
g0_fires = [f for f in outcome.guardrail_log if str(f.get("guardrail_id")) == "0"]
otto_ran = bool(outcome.otto_log)
usage = get_token_usage()
cost = (usage["input"] * 0.002 + usage["output"] * 0.01) / 1000

print(f"passed={outcome.passed} | components={n} | {dict(status_counts)}", flush=True)
print(f"G0 (already-target→exact) fires: {len(g0_fires)}", flush=True)
for f in g0_fires[:5]:
    print(f"   {f.get('ref_des')}: {f.get('reason')}", flush=True)
print(
    f"Otto ran (non-Würth target): {otto_ran}; challenges={len(outcome.otto_log.get('challenges', [])) if otto_ran else 0}",
    flush=True,
)
print(f"time={elapsed:.0f}s cost=${cost:.2f} llm_calls={usage['calls']}", flush=True)
# Show a few substitutions to confirm real Vishay parts came back
subs = [c for c in outcome.components if c.status.value in ("recommended", "partial")][:5]
print("Sample substitutions:", flush=True)
for c in subs:
    print(f"   {c.ref_des}: {c.original_mpn} → {c.substitute_mpn}", flush=True)
