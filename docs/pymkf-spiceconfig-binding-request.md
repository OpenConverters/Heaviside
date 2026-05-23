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

## Third upstream gap — PyMKF SEGFAULT on flyback above accessible-pool size

`design_magnetics_from_converter('flyback', spec, max_results, ...)` segfaults
when `max_results` exceeds the actual number of candidates PyMKF can produce.

Repro (Heaviside cp312 build of PyMKF, default stock catalogue):

```
flyback spec: Vin nom=48 (36-60), Vout=12, Iout=2, n=5, Lm=200µH
max_results=10  → returns 10 designs (OK)
max_results=15  → returns 14 designs (OK; pool exhausted at 14)
max_results=20  → SIGSEGV
max_results=50  → SIGSEGV
```

Python's `faulthandler` traceback resolves to `<lambda>` at
`heaviside/bridge.py:220` → `pyom.design_magnetics_from_converter(...)`. The
segfault is inside the C++ call; never returns to Python.

Hypothesis: the C++ design loop reserves a vector of size `max_results` and
overshoots when fewer real candidates exist. Suspected site: the per-
candidate result accumulator in `CoreAdviser::design_magnetics` (or the
PyMKF wrapper's loop assembling the response array).

Mitigation downstream: Heaviside's tier-2 retry now caps the pool at
`2 × len(tier_1_returned)` so we never ask for more than ~2x what PyMKF's
own pool size says exists. See `heaviside/bridge.py:design_converter_components`
for the cap.

Fix upstream: bound `max_results` to `min(requested, actual_available)` at
the top of `design_magnetics_from_converter`, OR add proper bounds-checking
inside the C++ result loop.
