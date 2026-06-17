"""BOM-extraction regression vs Proteus (LLM, opt-in).

Before the CR-coverage comparison can be trusted, our PDF->BOM extraction
must see AT LEAST as many components as Proteus did — otherwise a high
coverage % is just a smaller denominator. For each of the 10 reference
designs this runs our real extraction (RE stage0->stage2, LLM) on the
source PDF and asserts our component count >= Proteus's "Components
reviewed".

Proteus baseline is HARDCODED from Proteus's per-design Cross-Reference
Report PDFs (the "Components reviewed" line); we do not run Proteus.

Skipped unless HEAVISIDE_RUN_LLM_CR=1 (extraction is ~1-2 kimi-k2.5 calls
per design; measure true cost via the Moonshot balance API).

    HEAVISIDE_RUN_LLM_CR=1 .venv-web/bin/python -m pytest \
        tests/regression/test_bom_extraction_vs_proteus.py -p no:cacheprovider -q -s
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

PROTEUS_DIR = Path("/home/alf/OpenConverters/Proteus/tests/reference_designs")

# Proteus "Components reviewed" per design — from its per-design
# Cross-Reference Report PDFs. This is the extraction count to match/beat.
PROTEUS_REVIEWED: dict[str, int] = {
    "EVL1653F-TF-00A": 13,
    "EVQ3359C-LE-00A": 45,
    "eval-lt7153sp-az": 18,
    "eval-lt7176-az": 90,
    "eval-lt83401-lt83402-az": 35,
    "lt80602-lt80603-lt80603a": 12,
    "lt83401-lt83402": 35,
    "lt80603evkit": 26,
    "infineon-eval-7136u-gan": 52,
    "um3491-getting-started-with-steval0606yadj-evaluation-board-based-on-dcp0606qtry-automotive-6-v--6-a-stepdown-converter-stmicroelectronics": 16,
}

_RUN = os.environ.get("HEAVISIDE_RUN_LLM_CR") == "1"
pytestmark = pytest.mark.skipif(
    not _RUN, reason="LLM extraction run is opt-in; set HEAVISIDE_RUN_LLM_CR=1"
)


@pytest.mark.parametrize("design", list(PROTEUS_REVIEWED), ids=list(PROTEUS_REVIEWED))
def test_extraction_matches_or_beats_proteus(design: str) -> None:
    # Exercises the bom_extract STAGE (extract_bom_from_pdf): stage0 text +
    # stage2 reverse-engineer (competitor analysis dropped — one LLM call),
    # then the deterministic normalize/expand/categorize engine.
    from heaviside.stages.bom_extract import extract_bom_from_pdf

    pdf = PROTEUS_DIR / f"{design}.pdf"
    if not pdf.exists():
        pytest.skip(f"source PDF missing: {pdf}")

    bom = extract_bom_from_pdf(pdf, reference=design)
    ours = len(bom)
    proteus = PROTEUS_REVIEWED[design]

    rec = {"design": design, "ours": ours, "proteus": proteus}
    with Path("/tmp/bom_extraction_vs_proteus.ndjson").open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    print(f"\n{design}: ours {ours} components | proteus reviewed {proteus}"
          f"  {'OK' if ours >= proteus else 'SHORT'}", flush=True)

    assert ours >= proteus, (
        f"{design}: our extraction found {ours} components, BELOW Proteus's "
        f"{proteus} reviewed — CR coverage on a smaller BOM can't be trusted"
    )
