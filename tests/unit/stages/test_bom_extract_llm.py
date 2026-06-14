"""Real-LLM tests for the bom_extract LLM adapter (text/PDF -> BomComponent).

No mocks (project policy): these call the real reverse-engineer extractor.

- The in-suite test feeds a short BOM excerpt as ``pdf_text`` so the LLM
  call stays to a single small generation (still minutes-scale — kimi is a
  reasoning model — but bounded and cheap). It asserts the result is the
  canonical, PEAS-aligned shape.
- The full-PDF "beats Proteus" check is opt-in (``HEAVISIDE_RUN_LLM_PDF=1``):
  one reasoning call over ~100k chars of datasheet runs for many minutes,
  too slow for the default suite. It validates extraction breadth against
  Proteus's per-design report.

Both skip cleanly without ``MOONSHOT_API_KEY``.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from heaviside.stages.bom_extract import PEAS_CATEGORIES, extract_bom_from_pdf

_HAS_KEY = bool(os.environ.get("MOONSHOT_API_KEY"))

# A short, unambiguous BOM excerpt — enough to exercise the LLM extractor +
# the deterministic normalize/expand/categorize engine without a 100k-char run.
_EXCERPT = """\
EVAL-BUCK33  3.3V/2A Synchronous Buck Reference Design

Bill of Materials:
Ref  Qty  Part Number       Manufacturer      Value      Description
U1   1    LT8640S           Analog Devices               42V 5A sync buck regulator
L1   1    744325240         Wurth Elektronik  2.4uH      shielded power inductor
Cin  2    GRM31CR61H106KA   Murata            10uF/50V   X5R MLCC input cap
Cout 2    GRM32ER61A476KE   Murata            47uF/10V   X5R MLCC output cap
Cff  1    GRM1555C1H101J    Murata            100pF/50V  C0G feedforward cap
R1   1    CRCW06031003F     Vishay            100k       1% feedback resistor
R2   1    CRCW06032002F     Vishay            20k        1% feedback resistor
D1   1    PMEG6010CEH       Nexperia                     60V schottky catch diode
"""


@pytest.mark.skipif(not _HAS_KEY, reason="MOONSHOT_API_KEY not set")
def test_excerpt_extraction_is_peas_aligned():
    bom = extract_bom_from_pdf(pdf_text=_EXCERPT, reference="EVAL-BUCK33")

    # 8 distinct ref-des in the excerpt; the LLM may collapse the qty-2 lines
    # or expand them, so accept >=7 (one part missed) rather than pin a count.
    assert len(bom) >= 7, f"only {len(bom)} components extracted from the excerpt"
    for c in bom:
        assert c.ref_des, "component without a ref_des"
        assert c.category in PEAS_CATEGORIES or c.category == "", c.category
    cats = {c.category for c in bom}
    # a buck reference must classify the switch/controller, the magnetic, caps
    assert "magnetic" in cats, f"inductor not classified as magnetic: {cats}"
    assert "capacitor" in cats, f"caps not classified: {cats}"
    assert cats & {"semiconductor", "controller"}, f"no active device classified: {cats}"


_PDF = Path("/home/alf/OpenConverters/Proteus/tests/reference_designs/EVL1653F-TF-00A.pdf")
_PROTEUS_REVIEWED = 13  # EVL1653F per-design CR report "Components reviewed"


@pytest.mark.skipif(not _HAS_KEY, reason="MOONSHOT_API_KEY not set")
@pytest.mark.skipif(
    os.environ.get("HEAVISIDE_RUN_LLM_PDF") != "1",
    reason="heavy full-PDF extraction; set HEAVISIDE_RUN_LLM_PDF=1 to run",
)
@pytest.mark.skipif(not _PDF.exists(), reason=f"source PDF missing: {_PDF}")
def test_full_pdf_extraction_beats_proteus():
    bom = extract_bom_from_pdf(_PDF, reference="EVL1653F-TF-00A")
    assert len(bom) >= _PROTEUS_REVIEWED, (
        f"extracted {len(bom)} components, below Proteus's {_PROTEUS_REVIEWED} reviewed"
    )
    for c in bom:
        assert c.ref_des
        assert c.category in PEAS_CATEGORIES or c.category == ""
    assert {c.category for c in bom} & set(PEAS_CATEGORIES)
