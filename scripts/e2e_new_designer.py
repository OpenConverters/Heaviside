"""End-to-end run of the NEW fsw-from-magnetic designer pipeline — the B-stages
chained together (the integrated flow B9 will eventually wire into /design).

  B2 topology_constraints.propose  (LLM: maxDutyCycle / maxVds)
  B0 converter_spec_build.build     (BASE schema)
  B4 frequency_sweep.sweep          (fsw* from total-loss argmin, MKF per fsw)
  B5 magnetic_picker.pick_*_llm     (LLM suitability pick over the loss front)
  B7 op_reconcile.reconcile         (cross-OP saturation/thermal)
  B9 design_artifact.*              (loss curve + provenance)

Instrumented for wall-clock, per-stage time, tokens, and real Moonshot cost.
Usage: TOPOS=buck .venv-web/bin/python scripts/e2e_new_designer.py
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
    from heaviside.agents import magnetic_picker
    from heaviside.agents.llm_call import get_token_usage, reset_token_usage
    from heaviside.stages import (
        converter_spec_build,
        design_artifact,
        frequency_sweep,
        op_reconcile,
        topology_constraints,
    )

    topology = os.environ.get("TOPOS", "buck")
    spec_raw = {
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
    times: dict[str, float] = {}

    def stamp(name: str, t_start: float) -> None:
        times[name] = round(time.monotonic() - t_start, 1)
        print(f"  [{time.monotonic() - t0:6.1f}s] {name}: {times[name]}s", flush=True)

    print(f"=== NEW designer pipeline · topology={topology} · Kimi ===", flush=True)

    # B2 — propose constraints (LLM)
    s = time.monotonic()
    constraints = topology_constraints.propose(spec_raw, topology, use_llm=True, check_tas=True)
    stamp(
        f"B2 constraints (D={constraints.maximum_duty_cycle} Vds={constraints.maximum_drain_source_voltage} src={constraints.source})",
        s,
    )

    # B0 — build BASE spec
    s = time.monotonic()
    spec = converter_spec_build.build(dict(spec_raw), topology, constraints=constraints)
    stamp("B0 converter_spec_build", s)

    # B4 — frequency sweep
    s = time.monotonic()
    result = frequency_sweep.sweep(
        topology,
        spec,
        f_lo_hz=100_000,
        f_hi_hz=1_000_000,
        n_coarse=6,
        golden_iters=4,
        top_k=3,
        max_candidates_per_fsw=12,
    )
    stamp(
        f"B4 frequency_sweep (fsw*={result.fsw_star_hz / 1e3:.0f}kHz front={len(result.front)})", s
    )

    # B5 — suitability pick (LLM)
    s = time.monotonic()
    pick = magnetic_picker.pick_magnetic_from_sweep_llm(result, spec)
    chosen = result.front[pick["index"]]
    stamp(f"B5 suitability pick (idx={pick['index']} src={pick['source']})", s)

    # B7 — op_reconcile
    s = time.monotonic()
    try:
        recon = op_reconcile.reconcile(topology, spec, chosen.mas, min_isat_ratio=1.2)
        recon_ok = recon.feasible_all_ops
    except op_reconcile.InfeasibleAtOP as exc:
        recon = exc.report
        recon_ok = False
    stamp(f"B7 op_reconcile (feasible_all_ops={recon_ok} binding_op={recon.binding_op_index})", s)

    # B9 — artifacts
    s = time.monotonic()
    artifact = design_artifact.loss_curve_artifact(result)
    prov = design_artifact.design_provenance(result, topology=topology, spec=spec)
    stamp("B9 artifacts (loss curve + provenance)", s)

    elapsed = time.monotonic() - t0
    tok = get_token_usage()
    bal1 = _balance()

    print("\n=== DESIGN ===")
    print(f"topology={topology}  fsw*={result.fsw_star_hz / 1e3:.1f}kHz")
    print(
        f"magnetic: {chosen.core_shape} / {chosen.core_material}  L={chosen.inductance_h * 1e6:.1f}uH"
    )
    print(
        f"loss: total={chosen.total_loss_w:.3f}W (mag={chosen.magnetic_loss_w:.3f} sw={chosen.switching_loss_w:.3f})"
    )
    print(
        f"saturation: isat={chosen.isat_a:.2f}A vs ipeak={chosen.ipeak_worst_a:.2f}A (margin {chosen.isat_a / chosen.ipeak_worst_a:.2f}x)"
    )
    print(f"envelope FET: {result.envelope_fet.mpn}")
    print(f"reconcile feasible_all_ops={recon_ok}")
    print(f"provenance keys: {list(prov)}")
    print(f"loss-curve points: {len(artifact['loss_curve'])}")
    print("\n=== TIME / COST ===")
    print(f"wall-clock: {elapsed:.1f}s")
    print(f"tokens: calls={tok['calls']} in={tok['input']:,} out={tok['output']:,}")
    if bal0 is not None and bal1 is not None:
        print(f"real cost (balance delta): ${bal0 - bal1:.4f}  (balance {bal0:.4f} -> {bal1:.4f})")


if __name__ == "__main__":
    main()
