"""Real end-to-end design run with Kimi — the exact pipeline the web /jobs/design
triggers (full_design) — instrumented for wall-clock, per-stage time, token usage,
and REAL cost via the Moonshot balance API.

Usage: .venv-web/bin/python scripts/e2e_design_cost.py
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


def _balance() -> dict:
    key = os.environ.get("MOONSHOT_API_KEY", "")
    for base in ("https://api.moonshot.ai/v1", "https://api.moonshot.cn/v1"):
        try:
            req = urllib.request.Request(
                base + "/users/me/balance", headers={"Authorization": f"Bearer {key}"}
            )
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except Exception as exc:  # try the other region
            last = str(exc)
    return {"error": last}


def main() -> None:
    _load_env()
    assert os.environ.get("MOONSHOT_API_KEY"), "no MOONSHOT_API_KEY in .env"

    from heaviside.agents.llm_call import get_token_usage, reset_token_usage
    from heaviside.pipeline.full_design import full_design

    # The minimal input a user submits in the web Designer: Vin window + one rail.
    spec = {
        "inputVoltage": {"minimum": 9, "nominal": 12, "maximum": 16},
        "operatingPoints": [
            {
                "outputVoltages": [3.3],
                "outputCurrents": [3],
                "switchingFrequency": 500000,
                "ambientTemperature": 25,
            }
        ],
        "currentRippleRatio": 0.3,
    }

    bal0 = _balance()
    reset_token_usage()
    t0 = time.monotonic()
    marks: list[tuple[float, int, str]] = []

    def cb(msg: str, pct: int) -> None:
        marks.append((round(time.monotonic() - t0, 1), pct, msg))
        print(f"  [{marks[-1][0]:6.1f}s] {pct:3d}%  {msg}", flush=True)

    restrict = os.environ.get("TOPOS")
    restrict_list = [t.strip() for t in restrict.split(",")] if restrict else None
    print(
        f"=== running full_design (web /jobs/design pipeline) with Kimi "
        f"[restrict={restrict_list or 'none (full screen)'}] ===",
        flush=True,
    )
    err = None
    try:
        _, stage2, outcomes = full_design(
            spec,
            n_candidates_per_topology=2,
            parallel=True,
            progress_cb=cb,
            restrict_topologies=restrict_list,
        )
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        outcomes = []

    elapsed = time.monotonic() - t0
    tok = get_token_usage()
    bal1 = _balance()

    # per-stage durations from the progress marks
    stages = []
    for i, (ts, pct, msg) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else round(elapsed, 1)
        stages.append((msg, pct, round(end - ts, 1)))

    # cost: real balance delta (preferred), and the naive token formula for contrast
    # kimi-k2.5 list price ~ $0.60/M in, $2.50/M out (the ~3.7x-high formula).
    formula = tok["input"] / 1e6 * 0.60 + tok["output"] / 1e6 * 2.50
    real = None
    try:
        b0 = float(bal0.get("data", {}).get("available_balance", bal0.get("available_balance")))
        b1 = float(bal1.get("data", {}).get("available_balance", bal1.get("available_balance")))
        real = b0 - b1
    except Exception:
        pass

    print("\n=== RESULT ===")
    if err:
        print("pipeline error:", err)
    else:
        best = next(
            (o for o in outcomes if o.verdict_dict and o.verdict_dict.get("verdict") == "pass"),
            outcomes[0] if outcomes else None,
        )
        if best:
            print(
                f"topology={best.pick.topology.name}  verdict={best.verdict_dict.get('verdict') if best.verdict_dict else '?'}"
            )
        print(f"outcomes={len(outcomes)}")
        try:
            for topo, reason in (stage2.failures or ())[:8]:
                print(f"  stage2 FAIL {topo}: {reason[:200]}")
        except Exception:
            pass
    print("\n--- TIME ---")
    print(f"wall-clock: {elapsed:.1f}s")
    for msg, pct, dur in stages:
        print(f"  {pct:3d}% {msg:42s} {dur:6.1f}s")
    print("\n--- TOKENS ---")
    print(f"calls={tok.get('calls')}  input={tok['input']:,}  output={tok['output']:,}")
    print("\n--- COST ---")
    print(f"token-formula (list price, ~3.7x high): ${formula:.4f}")
    if real is not None:
        print(f"REAL (Moonshot balance delta): ${real:.4f}")
    else:
        print(f"balance API: before={bal0}  after={bal1}")


if __name__ == "__main__":
    main()
