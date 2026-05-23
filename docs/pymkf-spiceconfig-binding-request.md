# PyMKF binding gap: `SpiceSimulationConfig` not reachable from Python

**Filed by:** Heaviside (downstream consumer)
**Date:** 2026-05-23
**Severity:** Medium — workaround exists (text post-processing) but is brittle

## Summary

MKF's `SpiceSimulationConfig` C++ struct (`MKF/src/converter_models/Topology.h:156`) controls every numerical knob the ngspice deck PyMKF emits: snubber R/C, output diode model, output capacitance, switch model, solver tolerances, samples per period. The C++ API to override per-topology defaults is `Topology::set_spice_config(SpiceSimulationConfig)` at `Topology.h:254`.

**The pybind11 binding does not expose this API.** Downstream Python callers cannot override any of the per-topology spice-config fields. The Python-reachable surface (`PyOpenMagnetics.set_settings`) covers GLOBAL `circuitSimulator*` settings (saturation/mutualResistance/coreLossTopology) but not the per-topology struct.

## Why it matters

The C++ defaults bake in some odd choices that disproportionately affect downstream sim accuracy:

| Field | Default | Real-world implication |
|---|---|---|
| `snubR` | 100 Ω | Burns ≈ 25 W on a 60 W buck — dominates measured efficiency |
| `snubC` | 100 pF | Fine |
| `diodeIS / diodeRS` | 1e-14 A / 1 µΩ | RS so low the deck's diode is effectively a short circuit at conduction |
| `outputCapacitance` | 100 µF | Reasonable; sometimes too large/small for the topology under test |
| `samplesPerPeriod` | 200 | Reasonable |
| `relTol/absTol/itl1/itl4/method/trTol` | GEAR/1e-3/1e-9/1e-6/1000/1000/7.0 | Reasonable |

Heaviside's analyst-derived efficiency is independent of these (it uses picked-component parameters from TAS), but the SIM's measured efficiency is biased low because of the snubber + diode defaults. Real designs use much higher snubber R (10 kΩ if at all) and realistic diode models.

## Downstream workaround (Heaviside)

`heaviside/sim/runner.py:_rewrite_lossy_testbench` post-processes the netlist text PyMKF emits:

```python
_RSNUB_NEW_OHM: float = 10_000.0
_CSNUB_NEW_F: float = 100e-12
_DIDEAL_REWRITE: str = "D(Is=1e-12 N=1.05 RS=0.05)"

# regex-rewrites Rsnub_* / Csnub_* / .model DIDEAL on the deck text
```

This is brittle — sensitive to MKF's printf formatting + line shape — and would not be needed if `SpiceSimulationConfig` were reachable from Python.

## Proposed binding

Add a `spice_simulation_config` optional JSON parameter to
`generate_ngspice_circuit` (PyMKF/src/converter.cpp). Skeleton (matches the existing `apply_bridge_simulation_mode` pattern at line 1197):

```cpp
// Apply a partial SpiceSimulationConfig override from a JSON dict.
// Returns true on success. Missing keys keep the per-topology default.
template <typename TopologyT>
bool apply_spice_simulation_config(TopologyT& topology, const json& cfgJson) {
    if (cfgJson.is_null() || (cfgJson.is_object() && cfgJson.empty())) {
        return true;
    }
    if (!cfgJson.is_object()) return false;

    OpenMagnetics::SpiceSimulationConfig cfg = topology.spice_config();
    auto take_d = [&](const char* k, double& dst) {
        if (cfgJson.contains(k) && cfgJson[k].is_number()) dst = cfgJson[k].get<double>();
    };
    auto take_i = [&](const char* k, int& dst) {
        if (cfgJson.contains(k) && cfgJson[k].is_number_integer()) dst = cfgJson[k].get<int>();
    };
    auto take_s = [&](const char* k, std::string& dst) {
        if (cfgJson.contains(k) && cfgJson[k].is_string()) dst = cfgJson[k].get<std::string>();
    };
    take_d("pwmHigh",         cfg.pwmHigh);
    take_d("pwmRise",         cfg.pwmRise);
    take_d("pwmFall",         cfg.pwmFall);
    take_d("swModelVT",       cfg.swModelVT);
    take_d("swModelVH",       cfg.swModelVH);
    take_d("swModelRON",      cfg.swModelRON);
    take_d("swModelROFF",     cfg.swModelROFF);
    take_d("snubR",           cfg.snubR);
    take_d("snubC",           cfg.snubC);
    take_d("snubRReal",       cfg.snubRReal);
    take_d("diodeIS",         cfg.diodeIS);
    take_d("diodeRS",         cfg.diodeRS);
    take_d("outputCapacitance", cfg.outputCapacitance);
    take_d("outputCapInitialChargeFraction", cfg.outputCapInitialChargeFraction);
    take_i("samplesPerPeriod", cfg.samplesPerPeriod);
    take_d("relTol",          cfg.relTol);
    take_d("absTol",           cfg.absTol);
    take_d("vnTol",            cfg.vnTol);
    take_i("itl1",             cfg.itl1);
    take_i("itl4",             cfg.itl4);
    take_s("method",           cfg.method);
    take_d("trTol",            cfg.trTol);

    topology.set_spice_config(std::move(cfg));
    return true;
}
```

Then wire into `generate_spice_inductor`, `generate_spice_isolated`,
`generate_spice_isolated_scalar` (the three templates used by the
dispatch table) and add a `spice_config` argument to
`generate_ngspice_circuit`:

```cpp
m.def("generate_ngspice_circuit", &generate_ngspice_circuit,
    "... (existing docs) ...",
    py::arg("topology_name"), py::arg("converter_json"),
    py::arg("turns_ratios"), py::arg("magnetizing_inductance"),
    py::arg("vin_index") = 0, py::arg("op_index") = 0,
    py::arg("bridge_simulation_mode") = std::string(""),
    py::arg("spice_config") = nlohmann::json::object());
```

## Python usage after binding lands

```python
P.generate_ngspice_circuit(
    "buck", spec, [1.0], 1e-3, 0, 0, "switch",
    spice_config={
        "snubR": 10_000.0,
        "snubC": 100e-12,
        "diodeRS": 0.05,
        "samplesPerPeriod": 400,  # tighter for high-fsw designs
    },
)
```

Heaviside would then delete `_rewrite_lossy_testbench` entirely.

## Tests Heaviside would add upstream

1. `spice_config={"snubR": 10000}` produces a deck whose `Rsnub_*` lines have `10000`, not `100`.
2. `spice_config=None` and `spice_config={}` produce decks identical to the no-arg call.
3. `spice_config={"unknown_key": 42}` is silently ignored (no error) so future fields don't break old callers.
4. Per-topology defaults are preserved for fields not overridden.

## Related upstream gap (same area)

`MagneticFilterSaturation::evaluate_magnetic` (`MKF/src/advisers/MagneticFilterPhysical.cpp:130`) rejects a core only when `peak_B > B_sat` — no derating margin. Heaviside (and good engineering practice, Maniktala Ch.5) needs ≥ 1.2× margin. Either:

  * Add a `coreAdviserSaturationMargin` setting (default 1.0 = current behavior), OR
  * Add `SATURATION_MARGIN` to the CoreAdviser scoring filter enum so cores with more headroom rank higher (cheapest-with-headroom).

## Third upstream gap — PyMKF SEGFAULT on flyback with max_results > 1

`design_magnetics_from_converter('flyback', spec, max_results, 'available cores', ...)`
segfaults whenever `max_results >= 2`. The crash is **not** pool-exhaustion;
it reproduces independently of stock-catalogue size.

Repro (Heaviside cp312 PyMKF wheel, `core_mode='available cores'`):

```
flyback spec: Vin nom=48 (36-60), Vout=12, Iout=2, n=5, Lm=200µH
useOnlyCoresInStock=True,  max_results=1 → returns 1 design  (OK)
useOnlyCoresInStock=True,  max_results=3 → SIGSEGV
useOnlyCoresInStock=False, max_results=1 → returns 1 design  (OK)
useOnlyCoresInStock=False, max_results=3 → SIGSEGV
```

Buck / boost / cuk are unaffected at max_results=10 with the same wheel.

Python's `faulthandler` traceback resolves to `<lambda>` at
`heaviside/bridge.py:220` → `pyom.design_magnetics_from_converter(...)`. The
segfault is inside the C++ call; never returns to Python.

Hypothesis: the flyback-specific accumulator path (likely the secondary-side
core/coil expansion loop) writes past a fixed-size buffer when more than one
candidate is requested. Suspected site: per-candidate result accumulator in
`CoreAdviser::design_magnetics` flyback dispatch (or the PyMKF wrapper's
loop assembling the response array for two-winding topologies).

Mitigation downstream: Heaviside hard-caps `pool=1` for flyback in
`design_converter_components` (see `_CRASHY_POOL_CAP_1`). This disables the
isat post-filter for flyback (only one candidate to inspect), but keeps the
process alive. Other topologies retain the full pool.

Fix upstream: (a) bounds-check the flyback accumulator loop, AND/OR
(b) cap `max_results` to `min(requested, actual_available)` at the top of
`design_magnetics_from_converter` so undersized accessible pools degrade
gracefully instead of crashing.

## Fourth upstream gap — `Magnetic::calculate_saturation_current` ignores
## the temperature arg for B_sat lookup

`MKF/src/constructive_models/Magnetic.cpp:166-176`:

```cpp
double Magnetic::calculate_saturation_current(double temperature) {
    auto magneticFluxDensitySaturation = get_mutable_core().get_magnetic_flux_density_saturation();   // ← temperature not forwarded
    auto initialPermeability = get_mutable_core().get_initial_permeability(temperature);              // uses temperature
    ...
}
```

`B_sat` is temperature-dependent (typical ferrite: 0.495 T @ 25°C vs
0.390 T @ 100°C — 21% delta), but only permeability is looked up at
`temperature`. Fix: pass `temperature` to
`get_magnetic_flux_density_saturation()` too. One-line patch.

## Fifth upstream gap — `calculate_saturation_current` formula uses bare-core reluctance

Same function as #4, line 174:

```cpp
double saturationCurrent = magneticFluxDensitySaturation * effectiveArea * reluctance / numberTurns;
```

`reluctance` here is the **core's bare reluctance** (no gap). The
formula equates to `isat = B_sat · N · A_e / L_bare`, where
`L_bare = N²/ℜ_core`. When the designed inductor has a gap (or the
target L differs from L_bare for any reason), the returned isat
applies to the wrong inductance.

Observed: a flyback toroid picked with `nominal L = 562 µH` but
`L_bare = 1170 µH` (toroid, ungapped). PyOM returned `isat = 0.898 A`
for the bare 1170 µH operating point; the analytical figure at the
designed 562 µH is 1.47 A — a 1.6× spread that flips the realism
verdict.

Fix: either (a) add an overload `calculate_saturation_current(L_target,
temperature)` that uses `isat = B_sat · N · A_e / L_target`, or
(b) compute `reluctance` from the **gapped** core + coil pair so the
formula evaluates against the actually-designed inductance.

## Sixth upstream gap — CoreAdviser shortlists ungappable cores whose bare L is far off target

Toroids cannot be gapped. For a converter that needs `L = 562 µH`,
the CoreAdviser should refuse to shortlist a toroid whose bare L is
1170 µH because no gap strategy will close the gap. Today it ships
the candidate with `gapping: []` and the achieved inductance is
~2× the target — silent miss.

Fix locations to investigate: `CoreAdviser::design_magnetics` gap
optimisation step, and the per-candidate L matching filter
(`MagneticFilterLosses::evaluate_magnetic` calls
`check_requirement(magnetizing_inductance, candidate_L)` —
that's where a non-gappable shape with L outside the tolerance
window should be rejected).

## Seventh upstream gap — `design_magnetics_from_converter` dispatches to basic ctors that ignore `desiredInductance`

`PyMKF/src/converter.cpp:820` constructs `OpenMagnetics::Flyback(json)`
(basic). The basic `Flyback::process_design_requirements()` computes
its own `globalNeededInductance` from V·s + ripple ratio + duty and
**ignores** whatever `desiredInductance` field the JSON contains.
Buck (line 827) and Boost (833) do the same.

Meanwhile `process_flyback_internal` (line 149, used by
`process_converter` — the topology-only path) constructs
`AdvancedFlyback`, which DOES honour `desiredInductance`.

The inconsistency causes downstream surprises: a user spec that sets
`desiredInductance` works through one API surface and is silently
discarded through the other. Heaviside now adopts the L MKF actually
used (harvested from the returned MAS at
`designRequirements.magnetizingInductance`), so this no longer
silently breaks Heaviside — but the inconsistency is still worth
documenting / fixing upstream.

Fix options: (a) dispatch to `Advanced*` ctors in
`design_magnetics_from_converter`, OR (b) document clearly that
`desiredInductance` is advisory-only in this API surface, OR (c) add
an explicit `lockToUserInductance` flag.
