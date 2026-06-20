"""Focused end-to-end check of the CHANGED designer path (design_converter →
stage3_realize → op_reconcile → realism gate).

Runs deterministically (use_llm=False, with_reviewers=False) so it is free and
needs no Moonshot key — the point is to exercise the engine and the new
fail-loud behaviour, not the LLM stages:

  * stage3_realize now RAISES (RealizeError) on any step failure instead of
    returning a degraded outcome;
  * op_reconcile now RAISES (InfeasibleAtOP) on a cross-OP-infeasible design;
  * the realism gate only returns PASS when a real physics check passed.

Exit codes:
  0 — produced a design (prints verdict; PASS or FAIL are both "the pipeline
      ran and the gate spoke", which is success for THIS check)
  3 — raised RealizeError / InfeasibleAtOP (a loud, correct refusal — this is
      the new behaviour working; printed in full so abt #12 etc. are visible)
  4 — PyOM unavailable (vendor build not settled yet — caller should retry)
  1 — any other unexpected error

Usage: TOPOS=buck .venv-web/bin/python scripts/e2e_design_converter_check.py
"""
from __future__ import annotations

import os
import sys
import traceback


def main() -> int:
    topology = os.environ.get("TOPOS", "buck")

    # PyOM gate first: a broken vendor build is a "retry later", not a failure
    # of the pipeline under test.
    try:
        import PyOpenMagnetics  # noqa: F401
    except Exception as exc:  # vendor not built / wrong binding
        print(f"[skip] PyOpenMagnetics unavailable ({exc}) — vendor build not settled", flush=True)
        return 4

    from heaviside.pipeline.converter_designer import design_converter
    from heaviside.pipeline.full_design import RealizeError
    from heaviside.stages.frequency_sweep import FrequencySweepError
    from heaviside.stages.op_reconcile import InfeasibleAtOP

    # All of these are the "refuse loudly instead of producing a bad design"
    # family — any of them is the no-fallback behaviour working, not a crash.
    loud_refusals = (RealizeError, InfeasibleAtOP, FrequencySweepError)

    # A deliberately gentle buck (low current, generous ripple) so a feasible
    # core exists and the run reaches stage3_realize / op_reconcile / the gate.
    # Override with HV_VIN/HV_VOUT/HV_IOUT to probe a harder corner (e.g. the
    # 48→12V/5A spec that surfaces abt #12 at B4).
    vin = float(os.environ.get("HV_VIN", "12"))
    vout = float(os.environ.get("HV_VOUT", "5"))
    iout = float(os.environ.get("HV_IOUT", "1"))
    spec = {
        "inputVoltage": {"minimum": vin * 0.9, "nominal": vin, "maximum": vin * 1.1},
        "operatingPoints": [{
            "outputVoltages": [vout], "outputCurrents": [iout],
            "switchingFrequency": 400000, "ambientTemperature": 25,
        }],
        "currentRippleRatio": 0.4,
    }

    print(f"=== design_converter check · topology={topology} · deterministic ===", flush=True)
    try:
        design = design_converter(topology, spec, use_llm=False, with_reviewers=False)
    except loud_refusals as exc:
        print("\n=== LOUD REFUSAL (new fail-closed behaviour working) ===", flush=True)
        print(f"{type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
        return 3

    verdict = design.outcome.verdict_dict
    print("\n=== DESIGN PRODUCED ===", flush=True)
    print(f"topology={design.topology}  fsw*={design.fsw_hz/1e3:.1f}kHz", flush=True)
    print(f"realism verdict: {verdict['verdict'] if verdict else 'NONE'}", flush=True)
    if verdict:
        for c in verdict.get("checks", []):
            print(f"  - {c['name']}: {c['status']}", flush=True)
    print(f"BOM lines: {len(design.bom)}", flush=True)
    print(f"notes: {list(design.notes)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
