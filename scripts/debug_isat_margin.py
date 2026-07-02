"""Pin down the sweep-vs-realism-gate isat-margin divergence for one buck design.
Designs the magnetic at a fixed fsw (no full sweep, no LLM), then runs realize
and prints what the SWEEP sees vs what the GATE sees, side by side.
"""

from __future__ import annotations


def main() -> None:
    from heaviside import bridge
    from heaviside.pipeline.full_design import TopologyPick, stage3_realize
    from heaviside.stages import converter_spec_build
    from heaviside.topologies import get

    spec = {
        "inputVoltage": {"minimum": 9, "nominal": 12, "maximum": 16},
        "operatingPoints": [
            {"outputVoltages": [3.3], "outputCurrents": [3], "ambientTemperature": 25}
        ],
        "currentRippleRatio": 0.3,
    }
    base = converter_spec_build.build(dict(spec), "buck")
    entry = get("buck")
    fsw = 132_900.0

    cands = bridge.design_magnetics_at_fsw("buck", base, fsw, max_results=12)
    chosen = cands[0]
    spec_at = dict(base)
    spec_at["operatingPoints"] = [{**o, "switchingFrequency": fsw} for o in base["operatingPoints"]]

    # --- SWEEP side ---
    ipeak_s, L_s = bridge._isat_margin_inputs(entry, spec_at, chosen)
    isat_s_100 = bridge._isat_from_mas(chosen.magnetic, L_s, temperature_c=100.0)
    isat_s_25 = bridge._isat_from_mas(chosen.magnetic, L_s, temperature_c=25.0)
    print("=== SWEEP side ===")
    print(f"  core={chosen.core_shape_name}/{chosen.core_material_name}")
    print(f"  L_harvested={L_s * 1e6:.2f}uH  ipeak={ipeak_s:.3f}A")
    print(
        f"  isat@100C={isat_s_100:.3f}A (margin {isat_s_100 / ipeak_s:.2f})  isat@25C={isat_s_25:.3f}A (margin {isat_s_25 / ipeak_s:.2f})"
    )

    # --- GATE side: realize + read the enriched magnetic ---
    md = bridge.MagneticDesign(scoring=float(chosen.scoring), mas=chosen.mas, elapsed_s=0.0)
    pick = TopologyPick(
        topology=entry, main_magnetic=md, candidates=(md,), pick_reason="", pick_criteria="debug"
    )
    outcome = stage3_realize(pick, spec_at, pinned_main=md)
    tas = outcome.tas or {}
    print("\n=== GATE side (enriched TAS) ===")
    print(f"  duty={tas.get('duty')} duty_min={tas.get('duty_min')} duty_max={tas.get('duty_max')}")
    found = False
    for stage in tas.get("topology", {}).get("stages", []):
        for c in stage.get("circuit", {}).get("components", []):
            if isinstance(c, dict) and ("isat" in c or "ipeak_worst" in c):
                found = True
                # the L the gate used: from desiredInductance harvested, or the comp MAS
                print(
                    f"  comp={c.get('name')}: isat={c.get('isat')}A  ipeak_worst={c.get('ipeak_worst')}A "
                    f"margin={(c.get('isat') / c.get('ipeak_worst')) if c.get('isat') and c.get('ipeak_worst') else '?'}"
                )
                prov = c.get("isat_provenance") or {}
                print(
                    f"    isat_provenance: T={prov.get('temperature_c')}C method={prov.get('method')}"
                )
    if not found:
        print("  (no magnetic component with isat/ipeak found)")
    # what L did the gate use?
    print(
        f"  spec desiredInductance passed to gate: see decompose; harvested L_s={L_s * 1e6:.2f}uH"
    )
    # realism verdict
    vd = outcome.verdict_dict or {}
    for c in vd.get("checks", []):
        if c.get("name") == "inductor_isat_margin":
            print(
                f"  realism inductor_isat_margin: status={c.get('status')} value={c.get('value')} margin={c.get('margin')}"
            )


if __name__ == "__main__":
    main()
