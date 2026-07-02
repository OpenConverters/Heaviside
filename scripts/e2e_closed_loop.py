"""End-to-end CLOSED-LOOP converter design: swept magnetic → real converter
(TAS BOM) → MKF SPICE sim → realism gate → Ray + Nicola. Instrumented for time,
tokens, and real Moonshot cost.

Usage: TOPOS=buck .venv-web/bin/python scripts/e2e_closed_loop.py
"""

from __future__ import annotations

import json
import os
import time
import urllib.request


def _load_env() -> None:
    with open(os.path.join(os.path.dirname(__file__), "..", ".env")) as _f:
        for line in _f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v.strip().strip('"').strip("'"))


def _balance() -> float | None:
    key = os.environ.get("MOONSHOT_API_KEY", "")
    try:
        req = urllib.request.Request(
            "https://api.moonshot.ai/v1/users/me/balance",
            headers={"Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            return float(json.load(r)["data"]["available_balance"])
    except Exception:
        return None


def main() -> None:
    _load_env()
    from heaviside.agents.llm_call import get_token_usage, reset_token_usage
    from heaviside.pipeline.converter_designer import design_converter

    topology = os.environ.get("TOPOS", "buck")
    spec = {
        "inputVoltage": {"minimum": 9, "nominal": 12, "maximum": 16},
        "operatingPoints": [
            {"outputVoltages": [3.3], "outputCurrents": [3], "ambientTemperature": 25}
        ],
        "currentRippleRatio": 0.3,
    }

    bal0 = _balance()
    reset_token_usage()
    t0 = time.monotonic()

    def cb(msg, pct):
        print(f"  [{time.monotonic() - t0:6.1f}s] {pct:3d}%  {msg}", flush=True)

    print(f"=== CLOSED-LOOP design · {topology} · Kimi ===", flush=True)
    design = design_converter(
        topology,
        spec,
        use_llm=True,
        with_reviewers=True,
        sweep_kwargs={
            "f_lo_hz": 100_000,
            "f_hi_hz": 1_000_000,
            "n_coarse": 6,
            "golden_iters": 4,
            "top_k": 3,
            "max_candidates_per_fsw": 12,
        },
        progress=cb,
    )
    elapsed = time.monotonic() - t0
    tok = get_token_usage()
    bal1 = _balance()

    o = design.outcome
    print("\n=== DESIGN ===")
    print(
        f"topology={design.topology}  fsw*={design.fsw_hz / 1e3:.1f}kHz  verdict={design.verdict}"
    )
    c = design.sweep.best
    print(
        f"magnetic (PINNED): {c.core_shape} / {c.core_material}  L={c.inductance_h * 1e6:.1f}uH  "
        f"isat={c.isat_a:.2f}A vs ipeak={c.ipeak_worst_a:.2f}A"
    )
    print(f"reconcile feasible_all_ops={design.reconcile.feasible_all_ops}")
    gk = getattr(o, "gatekeeper", None)
    if gk:
        print(
            f"gatekeeper approved={gk.approved} objections={list(gk.objections)} "
            f"warnings={len(gk.warnings)}"
        )
    vd = o.verdict_dict or {}
    checks = vd.get("checks", [])
    npass = sum(1 for c in checks if c.get("status") == "pass")
    nfail = sum(1 for c in checks if c.get("status") == "fail")
    fails = [c.get("name") for c in checks if c.get("status") == "fail"]
    print(f"realism checks: {npass} pass / {nfail} fail of {len(checks)}  fails={fails}")
    for c in checks:
        if c.get("status") == "fail":
            print(f"    FAIL {c.get('name')}: value={c.get('value')} margin={c.get('margin')}")
    print("\n=== BOM (real TAS parts) ===")
    for row in design.bom:
        print(
            f"  {row['ref']:6s} {row.get('mpn')!s:24s} {row.get('manufacturer') or ''!s:18s} {row.get('category') or ''}"
        )
    if design.notes:
        print("\nnotes:", "; ".join(design.notes)[:300])
    if o.diagnostics:
        print("diagnostics:", "; ".join(o.diagnostics)[:300])
    print("\n=== TIME / COST ===")
    print(
        f"wall-clock: {elapsed:.1f}s  ·  tokens calls={tok['calls']} in={tok['input']:,} out={tok['output']:,}"
    )
    if bal0 is not None and bal1 is not None:
        print(f"real cost (balance delta): ${bal0 - bal1:.4f}  ({bal0:.4f} -> {bal1:.4f})")


if __name__ == "__main__":
    main()
