#!/usr/bin/env python3
"""Run the full CR pipeline on EVQ3359C-LE-00A and compare against Proteus golden.

Usage:
  MOONSHOT_API_KEY=sk-... python scripts/run_evq_crossref.py [--deterministic-only]

When --deterministic-only is passed, only stages 1-2 (prefetch + preclassify)
run — useful to verify TAS plumbing without an API key.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────
PROTEUS_DIR = Path("/home/alf/OpenConverters/Proteus/tests/reference_designs/crossref_wurth/EVQ3359C-LE-00A")
GOLDEN_BOM = PROTEUS_DIR / "bom_full.json"
GOLDEN_REPORT = PROTEUS_DIR / "report.md"
OUTPUT_DIR = Path("/home/alf/OpenConverters/Heaviside/tests/crossref_output/EVQ3359C-LE-00A")


def load_golden_bom() -> list[dict]:
    return json.loads(GOLDEN_BOM.read_text())


def parse_golden_report(report_text: str) -> dict[str, dict]:
    """Parse Proteus golden report.md into a ref_des → info map."""
    entries: dict[str, dict] = {}
    for line in report_text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 5:
            continue
        ref_cell = cells[1] if len(cells) > 1 else ""
        if ref_cell in ("Ref", "-----", "---", "Category", ""):
            continue
        # Parse ref_des ranges like "C1–C4" or "R5/R6/R9/R11"
        refs = _expand_refs(ref_cell)
        for ref in refs:
            info = {"raw_line": line, "cells": cells}
            # Detect status
            status_col = next((c for c in reversed(cells) if c), "")
            if "ALREADY WÜRTH" in status_col or "ALREADY" in status_col.upper():
                info["status"] = "keep_original"
            elif "NOT REPLACED" in status_col.upper():
                info["status"] = "no_substitute"
            elif "REPLACED" in status_col.upper():
                info["status"] = "replaced"
            else:
                info["status"] = "unknown"
            # Extract Würth PN if present
            for cell in cells:
                if cell.startswith("**") and cell.endswith("**"):
                    info["wurth_pn"] = cell.strip("*").strip()
            entries[ref] = info
    return entries


def _expand_refs(ref_cell: str) -> list[str]:
    """Expand ref_des ranges: 'C1–C4' → ['C1','C2','C3','C4'], 'R5/R6' → ['R5','R6']."""
    refs: list[str] = []
    for part in ref_cell.replace("–", "-").replace(",", "/").split("/"):
        part = part.strip()
        if "-" in part and not part.startswith("-"):
            # Range like C19-C22
            import re
            m = re.match(r"([A-Z]+)(\d+)\s*-\s*([A-Z]*)(\d+)", part)
            if m:
                prefix = m.group(1)
                start, end = int(m.group(2)), int(m.group(4))
                for i in range(start, end + 1):
                    refs.append(f"{prefix}{i}")
                continue
        refs.append(part)
    return refs


def run_deterministic(bom: list[dict]) -> None:
    """Run only the deterministic stages and report results."""
    from heaviside.pipeline.crossref import CrossRefState
    from heaviside.pipeline.crossref_pipeline import (
        _normalize_bom,
        _stage1_prefetch,
        _stage2_preclassify,
    )

    state = CrossRefState(
        source_bom=_normalize_bom(bom),
        target_manufacturer="Wurth Elektronik",
        circuit_context="MPS EVQ3359C-LE-00A dual-phase synchronous buck, 60V input",
    )

    state = _stage1_prefetch(state)
    state = _stage2_preclassify(state)

    print("=" * 70)
    print("DETERMINISTIC STAGES RESULT")
    print("=" * 70)

    # Prefetch summary
    cats: dict[str, int] = {}
    for ref, cands in state.candidates_by_ref.items():
        comp = next((c for c in state.source_bom if c.get("ref_des") == ref), {})
        cat = comp.get("component_type", "?")
        cats[cat] = cats.get(cat, 0) + len(cands)
    print(f"\nPrefetch candidates by category:")
    for cat, count in sorted(cats.items()):
        print(f"  {cat}: {count} candidates")

    # Preclassify summary
    print(f"\nPre-classified as keep_original: {len(state.preclassified)}")
    for ref, info in sorted(state.preclassified.items()):
        print(f"  {ref}: {info['reason']}")

    # Compare with golden
    golden = parse_golden_report(GOLDEN_REPORT.read_text())
    golden_keeps = {ref for ref, info in golden.items() if info["status"] == "keep_original"}
    heavi_keeps = set(state.preclassified.keys())

    print(f"\nGolden keep_original: {sorted(golden_keeps)}")
    print(f"Heaviside keep_original: {sorted(heavi_keeps)}")
    if golden_keeps == heavi_keeps:
        print("  MATCH ✓")
    else:
        missing = golden_keeps - heavi_keeps
        extra = heavi_keeps - golden_keeps
        if missing:
            print(f"  Missing from Heaviside: {sorted(missing)}")
        if extra:
            print(f"  Extra in Heaviside: {sorted(extra)}")


def run_full(bom: list[dict]) -> None:
    """Run the full CR pipeline including LLM stages."""
    import logging
    logging.basicConfig(level=logging.INFO)

    from heaviside.pipeline.crossref_pipeline import run_crossref_pipeline

    outcome = run_crossref_pipeline(
        bom,
        "Wurth Elektronik",
        circuit_context="MPS EVQ3359C-LE-00A dual-phase synchronous buck, 60V input, up to 5A/phase",
        verbose=True,
    )

    # Save outcome
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    result = {
        "target_manufacturer": outcome.target_manufacturer,
        "passed": outcome.passed,
        "diagnostics": list(outcome.diagnostics),
        "guardrail_log": list(outcome.guardrail_log),
        "otto_log": outcome.otto_log,
        "review_verdicts": list(outcome.review_verdicts),
        "reviewer_log": outcome.reviewer_log,
        "components": [],
    }
    for c in outcome.components:
        result["components"].append({
            "ref_des": c.ref_des,
            "component_type": c.component_type,
            "original_mpn": c.original_mpn,
            "substitute_mpn": c.substitute_mpn,
            "status": c.status.value,
            "notes": c.notes,
            "guardrail_fires": list(c.guardrail_fires),
            "match_score": c.match_score,
        })

    out_file = OUTPUT_DIR / "outcome.json"
    out_file.write_text(json.dumps(result, indent=2) + "\n")
    print(f"\nWrote outcome to {out_file}")

    # Compare against golden
    compare_with_golden(outcome)


def compare_with_golden(outcome) -> None:
    """Compare Heaviside outcome against Proteus golden report."""
    golden = parse_golden_report(GOLDEN_REPORT.read_text())

    print("\n" + "=" * 70)
    print("COMPARISON: Heaviside vs Proteus Golden")
    print("=" * 70)

    # Build Heaviside map
    heavi = {}
    for c in outcome.components:
        heavi[c.ref_des] = {
            "status": c.status.value,
            "substitute_mpn": c.substitute_mpn,
            "notes": c.notes,
        }

    # Category stats
    status_counts = {}
    for c in outcome.components:
        status_counts[c.status.value] = status_counts.get(c.status.value, 0) + 1
    print(f"\nHeaviside status breakdown:")
    for s, n in sorted(status_counts.items()):
        print(f"  {s}: {n}")

    # Golden stats
    golden_counts = {}
    for ref, info in golden.items():
        golden_counts[info["status"]] = golden_counts.get(info["status"], 0) + 1
    print(f"\nProteus golden status breakdown:")
    for s, n in sorted(golden_counts.items()):
        print(f"  {s}: {n}")

    # Per-component comparison
    print(f"\n{'Ref':<10} {'Golden':<16} {'Heaviside':<16} {'Golden PN':<18} {'Heavi PN':<18} {'Match?'}")
    print("-" * 96)

    matches = 0
    mismatches = 0
    for ref in sorted(set(golden.keys()) | set(heavi.keys())):
        g = golden.get(ref, {})
        h = heavi.get(ref, {})
        g_status = g.get("status", "missing")
        h_status = h.get("status", "missing")
        g_pn = g.get("wurth_pn", "—")
        h_pn = h.get("substitute_mpn") or "—"

        # A match means the decision direction agrees
        # (both replaced, both not-replaced, both keep-original)
        g_replaced = g_status == "replaced"
        h_replaced = h_status in ("exact", "recommended", "partial")
        g_kept = g_status == "keep_original"
        h_kept = h_status in ("keep_original", "exact")
        g_none = g_status in ("no_substitute", "unknown")
        h_none = h_status == "no_substitute"

        # Heaviside found a replacement where Proteus didn't = improvement
        h_better = g_none and h_replaced

        agree = (g_replaced and h_replaced) or (g_kept and h_kept) or (g_none and h_none)
        if g_status == "missing" or h_status == "missing":
            marker = "?"
        elif agree:
            marker = "OK"
            matches += 1
        elif h_better:
            marker = "H>P"
            matches += 1
        else:
            marker = "DIFF"
            mismatches += 1

        print(f"{ref:<10} {g_status:<16} {h_status:<16} {g_pn:<18} {h_pn:<18} {marker}")

    total = matches + mismatches
    if total > 0:
        print(f"\nAgreement: {matches}/{total} = {100*matches/total:.0f}%")
    print(f"Mismatches: {mismatches}")


def main() -> None:
    bom = load_golden_bom()
    print(f"Loaded {len(bom)} BOM entries from Proteus golden")

    if "--deterministic-only" in sys.argv:
        run_deterministic(bom)
    else:
        import os
        if not os.environ.get("MOONSHOT_API_KEY"):
            print("\nMOONSHOT_API_KEY not set.")
            print("  Run with --deterministic-only to test prefetch/preclassify")
            print("  Or: MOONSHOT_API_KEY=sk-... python scripts/run_evq_crossref.py")
            sys.exit(1)
        run_full(bom)


if __name__ == "__main__":
    main()
