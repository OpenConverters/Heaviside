#!/usr/bin/env python3
"""Training round: CRE specs → converter designer → compare vs reference.

For each golden reference design:
  1. CRE: extract specs + claims from PDF
  2. full_design(): design a competing converter
  3. Compare: topology match, efficiency gap, verdict
  4. Teacher: store comparison lessons
  5. Report: time, cost, results

Usage:
    python scripts/run_training_round.py [design-name]
    python scripts/run_training_round.py              # all 10
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
import traceback
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault(
    "MOONSHOT_API_KEY",
    "sk-viKudfa58QW8GjUm8aYxkfv5hmz0i5Y3HRdMKKpphPUupleQ",
)
logging.basicConfig(level=logging.WARNING)

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


def _extract_spec(name: str) -> dict[str, Any] | None:
    """Run CRE stages 0→2.7 to extract spec + claims from PDF."""
    from heaviside.pipeline.cre import CREState
    from heaviside.pipeline.cre_pipeline import (
        _stage0_extract_pdf,
        _stage1_competitor,
        _stage2_reverse_engineer,
        _stage2_5_verify_mpns,
        _stage2_65_extract_rdson,
        _stage2_7_extract_claims,
    )

    pdf = PROTEUS_DIR / f"{name}.pdf"
    if not pdf.exists():
        return None

    state = CREState(reference=name, pdf_path=pdf)
    state = _stage0_extract_pdf(state)
    state = _stage1_competitor(state)
    state = _stage2_reverse_engineer(state)
    state = _stage2_5_verify_mpns(state)
    state = _stage2_65_extract_rdson(state)
    state = _stage2_7_extract_claims(state)

    if not state.ref_spec:
        return None

    return {
        "ref_spec": state.ref_spec,
        "ref_claims": state.ref_claims,
        "ref_bom": state.ref_bom,
        "diagnostics": state.diagnostics,
    }


def _run_designer(spec: Mapping[str, Any], ref_topology: str) -> dict[str, Any]:
    """Run full_design() on the extracted spec."""
    from heaviside.pipeline.full_design import FullDesignError, full_design
    from heaviside.pipeline.topology_screen import feasible_topology_names

    def selector_fn(s: Mapping[str, Any]) -> tuple[list[str], str]:
        static = feasible_topology_names(s)
        topo = ref_topology.lower().replace(" ", "_").replace("-", "_")
        # Normalize common aliases
        from heaviside.pipeline.cre_testbench import _normalize_topology
        norm = _normalize_topology(topo)
        if norm and norm in static:
            return [norm] + [t for t in static if t != norm], f"training: prefer {norm}"
        return static, "training: static screen"

    try:
        stage1, stage2, outcomes = full_design(
            spec,
            n_candidates_per_topology=3,
            parallel=False,
            selector_fn=selector_fn,
        )
        return {
            "stage1": stage1,
            "stage2": stage2,
            "outcomes": list(outcomes),
            "best": outcomes[0] if outcomes else None,
        }
    except FullDesignError as exc:
        return {"error": str(exc), "outcomes": [], "best": None}
    except Exception as exc:
        return {"error": str(exc), "outcomes": [], "best": None}


def _compare(ref_spec, ref_claims, designer_result: dict) -> dict[str, Any]:
    """Compare designer output against reference design."""
    best = designer_result.get("best")
    if not best:
        return {
            "topo_match": False,
            "designer_topo": "—",
            "designer_eta": None,
            "ref_eta": None,
            "eta_gap_pp": None,
            "verdict": designer_result.get("error", "no_design"),
            "ray_approved": False,
            "nicola_approved": False,
        }

    # Topology match
    designer_topo = best.pick.topology.name if best.pick else "?"
    ref_topo_norm = ref_spec.topology.lower().replace(" ", "_").replace("-", "_")
    topo_match = ref_topo_norm in designer_topo.lower() or designer_topo.lower() in ref_topo_norm

    # Efficiency comparison
    designer_eta = None
    if best.verdict_dict:
        checks = best.verdict_dict.get("checks", [])
        if isinstance(checks, list):
            for chk in checks:
                if isinstance(chk, dict) and "efficiency" in chk.get("name", ""):
                    designer_eta = chk.get("value")
                    break
        elif isinstance(checks, dict):
            eff_check = checks.get("efficiency", checks.get("efficiency_sanity", {}))
            designer_eta = eff_check.get("value") if isinstance(eff_check, dict) else None

    ref_eta = None
    if ref_claims and ref_claims.efficiency:
        ref_eta = max(ref_claims.efficiency.values())
    elif ref_spec.efficiency_target:
        ref_eta = ref_spec.efficiency_target

    eta_gap = None
    if designer_eta is not None and ref_eta is not None and ref_eta > 0:
        eta_gap = (designer_eta - ref_eta) * 100

    # Verdict
    verdict = "no_verdict"
    if best.verdict_dict:
        verdict = best.verdict_dict.get("verdict", "no_verdict")

    # Ray/Nicola
    ray_ok = False
    nicola_ok = False
    if best.gatekeeper:
        ray_ok = best.gatekeeper.approved
    if best.diagnostics:
        nicola_ok = any("nicola" in d.lower() for d in best.diagnostics)

    return {
        "topo_match": topo_match,
        "designer_topo": designer_topo,
        "designer_eta": designer_eta,
        "ref_eta": ref_eta,
        "eta_gap_pp": eta_gap,
        "verdict": verdict,
        "ray_approved": ray_ok,
        "nicola_approved": nicola_ok,
    }


def _store_training_lessons(
    name: str, ref_spec, comparison: dict, designer_result: dict,
) -> int:
    """Store comparison-specific training lessons."""
    from heaviside.pipeline.teacher import Lesson, store_lessons, review_design_run

    now = datetime.now(timezone.utc).isoformat()
    fp = hashlib.sha256(name.encode()).hexdigest()[:12]
    lessons: list[Lesson] = []

    # Lesson from topology match/mismatch
    lessons.append(Lesson(
        id=hashlib.sha256(f"train-topo:{name}".encode()).hexdigest()[:16],
        timestamp=now,
        topology=ref_spec.topology,
        category="training_topology_match",
        severity="info" if comparison["topo_match"] else "warning",
        detail=(
            f"Designer {'matched' if comparison['topo_match'] else 'did NOT match'} "
            f"reference topology. Designer: {comparison['designer_topo']}, "
            f"Reference: {ref_spec.topology}"
        ),
        spec_fingerprint=fp,
        suggestion=None if comparison["topo_match"] else
            f"Consider preferring {ref_spec.topology} for similar specs",
    ))

    # Lesson from efficiency gap
    if comparison["eta_gap_pp"] is not None:
        severity = "info" if abs(comparison["eta_gap_pp"]) < 5 else "warning"
        lessons.append(Lesson(
            id=hashlib.sha256(f"train-eta:{name}".encode()).hexdigest()[:16],
            timestamp=now,
            topology=ref_spec.topology,
            category="training_efficiency_gap",
            severity=severity,
            detail=(
                f"Designer η={comparison['designer_eta']:.1%} vs "
                f"reference η={comparison['ref_eta']:.1%} "
                f"(Δ={comparison['eta_gap_pp']:+.1f}pp)"
            ),
            spec_fingerprint=fp,
            suggestion=None,
        ))

    # Lesson from verdict + failed checks
    verdict_detail = f"Verdict: {comparison['verdict']} for {name}"
    best = designer_result.get("best")
    if best and best.verdict_dict:
        checks = best.verdict_dict.get("checks", [])
        if isinstance(checks, list):
            failed = [c.get("name", "?") for c in checks
                      if isinstance(c, dict) and c.get("status") == "fail"]
            if failed:
                verdict_detail += f". Failed checks: {', '.join(failed)}"

    lessons.append(Lesson(
        id=hashlib.sha256(f"train-verdict:{name}".encode()).hexdigest()[:16],
        timestamp=now,
        topology=ref_spec.topology,
        category="training_verdict",
        severity="info" if comparison["verdict"] == "pass" else "error",
        detail=verdict_detail,
        spec_fingerprint=fp,
        suggestion=None,
    ))

    # Also extract standard design lessons from outcomes
    if designer_result.get("outcomes"):
        spec_dict = ref_spec.to_heaviside_spec()
        design_lessons = review_design_run(designer_result["outcomes"], spec_dict)
        lessons.extend(design_lessons)

    return store_lessons(lessons)


def run_one(name: str) -> dict[str, Any]:
    """Run the full training loop for one design."""
    from heaviside.agents.llm_call import reset_token_usage, get_token_usage

    reset_token_usage()
    t0 = time.time()

    # Step 1: CRE extraction
    cre = _extract_spec(name)
    if not cre or not cre["ref_spec"]:
        return {"name": name, "error": "CRE extraction failed"}

    ref_spec = cre["ref_spec"]
    ref_claims = cre["ref_claims"]

    # Step 2: Run converter designer
    spec_dict = ref_spec.to_heaviside_spec()
    designer_result = _run_designer(spec_dict, ref_spec.topology)

    # Step 3: Compare
    comparison = _compare(ref_spec, ref_claims, designer_result)

    # Step 4: Store lessons
    n_lessons = _store_training_lessons(name, ref_spec, comparison, designer_result)

    elapsed = time.time() - t0
    usage = get_token_usage()
    cost = (usage["input"] * 0.002 + usage["output"] * 0.01) / 1000

    return {
        "name": name,
        "ref_topology": ref_spec.topology,
        "ref_eta": comparison["ref_eta"],
        "designer_topo": comparison["designer_topo"],
        "topo_match": comparison["topo_match"],
        "designer_eta": comparison["designer_eta"],
        "eta_gap_pp": comparison["eta_gap_pp"],
        "verdict": comparison["verdict"],
        "ray_approved": comparison["ray_approved"],
        "n_lessons": n_lessons,
        "elapsed_s": round(elapsed, 1),
        "est_cost_usd": round(cost, 2),
        "llm_calls": usage["calls"],
    }


def main():
    designs = sys.argv[1:] if len(sys.argv) > 1 else GOLDEN_DESIGNS
    print(f"Training round: {len(designs)} designs", flush=True)
    results = []

    for i, name in enumerate(designs):
        print(f"\n[{i+1}/{len(designs)}] {name}", flush=True)
        try:
            r = run_one(name)
            results.append(r)
            if "error" in r:
                print(f"  ERROR: {r['error']}", flush=True)
            else:
                topo_mark = "✓" if r["topo_match"] else "✗"
                eta_str = f"{r['designer_eta']:.1%}" if r["designer_eta"] else "—"
                ref_str = f"{r['ref_eta']:.1%}" if r["ref_eta"] else "—"
                gap_str = f"{r['eta_gap_pp']:+.1f}pp" if r["eta_gap_pp"] is not None else "—"
                print(
                    f"  {r['ref_topology']:<20s} {topo_mark} {r['designer_topo']:<15s} "
                    f"η={eta_str} ref={ref_str} Δ={gap_str} "
                    f"verdict={r['verdict']} lessons={r['n_lessons']} "
                    f"| {r['elapsed_s']}s ${r['est_cost_usd']:.2f}",
                    flush=True,
                )
        except Exception as exc:
            traceback.print_exc()
            results.append({"name": name, "error": str(exc)})

    # Summary
    print(f"\n{'='*90}")
    print("TRAINING ROUND SUMMARY")
    print(f"{'='*90}")
    print(f"{'Design':<35s} {'Ref Topo':<15s} {'Match':>5} {'η_des':>7} {'η_ref':>7} {'Δpp':>6} {'Verdict':>8} {'Time':>6} {'Cost':>6}")
    print("-" * 100)
    total_time = 0
    total_cost = 0
    total_lessons = 0
    n_pass = 0
    n_topo_match = 0
    for r in results:
        if "error" in r and "ref_topology" not in r:
            print(f"{r['name'][:34]:<35s} ERROR: {r['error'][:50]}")
            continue
        t = r.get("elapsed_s", 0)
        c = r.get("est_cost_usd", 0)
        total_time += t
        total_cost += c
        total_lessons += r.get("n_lessons", 0)
        if r.get("verdict") == "pass":
            n_pass += 1
        if r.get("topo_match"):
            n_topo_match += 1

        topo_mark = "✓" if r.get("topo_match") else "✗"
        eta_str = f"{r['designer_eta']:.1%}" if r.get("designer_eta") else "—"
        ref_str = f"{r['ref_eta']:.1%}" if r.get("ref_eta") else "—"
        gap_str = f"{r['eta_gap_pp']:+.1f}" if r.get("eta_gap_pp") is not None else "—"
        print(
            f"{r['name'][:34]:<35s} {r.get('ref_topology','?')[:14]:<15s} "
            f"{topo_mark:>5} {eta_str:>7} {ref_str:>7} {gap_str:>6} "
            f"{r.get('verdict','?'):>8} {t:5.0f}s ${c:.2f}"
        )
    print("-" * 100)
    n_valid = len([r for r in results if "ref_topology" in r])
    print(
        f"TOTAL: {n_pass}/{n_valid} pass, {n_topo_match}/{n_valid} topology match, "
        f"{total_lessons} lessons | {total_time:.0f}s ${total_cost:.2f}"
    )


if __name__ == "__main__":
    main()
