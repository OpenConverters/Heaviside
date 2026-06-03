# PyOpenMagnetics API Reference

> **This file is extracted from AGENTS.md, the SINGLE SOURCE OF TRUTH for any AI agent using PyOpenMagnetics.**
> Every instruction was derived from reading the C++ source code and validated by running the library.
> When this document contradicts the `.pyi` stubs or bundled examples, **trust this document**.

---

## Critical Rules

**Before writing ANY code, check these three things:**

1. **NEVER use `json.dumps()`** -- Pass Python dicts directly to all functions.
   `json.dumps()` converts your dict to a string, causing schema errors.

2. **NEVER invent your own JSON structure** -- Copy the EXACT schemas from the Converter Models section.
   Fields like `"inputs"`, `"outputs"`, `"voltage"` are WRONG. Use `"inputVoltage"`, `"operatingPoints"`, etc.

3. **NEVER abandon Method A after an error** -- If you get `"Input JSON does not conform to schema!"`,
   the fix is to correct your JSON, NOT to fall back to manual calculations.

### The #1 Rule -- DO NOT USE MANUAL CALCULATIONS

When a user asks you to design a magnetic component for a power converter:

1. **ALWAYS** use `calculate_advised_magnetics_fast()` (Method C -- fast path, no winding solver):
   - Call `process_converter(topology, advanced_spec, False)` → `process_inputs()` → `calculate_advised_magnetics_fast()`
   - This is the **working path in PyOM 1.3.6** -- returns core + turns + DC/core losses
   - See `sweep_frequency.py:design_at_frequency_fast()` for the complete reference implementation
2. **NEVER** use `design_magnetics_from_converter()` or `calculate_advised_magnetics()` --
   both hang indefinitely in PyOM 1.3.6 (winding solver bug)
3. **NEVER** calculate turns ratios, inductance, core sizes, or wire gauges manually
4. **NEVER** fall back to textbook formulas -- fix the API call instead

The MKF engine handles complex magnetic field distributions, temperature-dependent
material properties, geometrical fringing effects, and real commercial core database
constraints that manual formulas cannot replicate.

### Timeout Is NOT a Timeout -- The Winding Solver Hangs

**CRITICAL (confirmed PyOM 1.3.6):** `design_magnetics_from_converter` and
`calculate_advised_magnetics` HANG indefinitely after the core selection phase
(visible as `[CoreAdviser] After Losses: N` being the last log line). This is
a **winding solver bug** in the C++ layer, not a slow computation.

**Symptoms:**
- CoreAdviser logs up to `After Losses: N` then no further output
- Process never returns, even after hours
- Affects all topologies (flyback, buck, boost, forward)
- `coilTryRewind`, `coilDelimitAndCompact` settings do not help
- Adding `coilWindEvenIfNotFit=True` does not help

**Fix: Do NOT use `design_magnetics_from_converter` or `calculate_advised_magnetics`.**
Use `calculate_advised_magnetics_fast` (Method C -- the working path in PyOM 1.3.6).
See `sweep_frequency.py:design_at_frequency_fast()` for the complete reference implementation.

```python
# Method C: process_converter → process_inputs → calculate_advised_magnetics_fast
# This is the ONLY non-hanging path in PyOM 1.3.6
processed = PyOM.process_converter("flyback", advanced_spec, False)
pi = PyOM.process_inputs({
    "designRequirements": processed["designRequirements"],
    "operatingPoints":    processed["operatingPoints"]
})
results = PyOM.calculate_advised_magnetics_fast(pi, 1, "standard cores")
# Returns: {"data": [{"mas": {...}, "scoring": float_losses_W, "scoringPerFilter": None}]}
# scoring = total losses in Watts (lower = better)
```

---

## Import Procedure

The package has **no `__init__.py`**. A bare `import PyOpenMagnetics` gives an
**empty namespace with 0 functions**. You MUST use `importlib`:

```python
import importlib.util, os, glob

pkg_dir = os.path.join(
    os.path.dirname(__import__('PyOpenMagnetics').__path__[0]),
    'PyOpenMagnetics'
)
so_files = glob.glob(os.path.join(pkg_dir, 'PyOpenMagnetics.cpython-*'))
assert so_files, f"No .so/.pyd found in {pkg_dir}"

spec = importlib.util.spec_from_file_location('PyOpenMagnetics', so_files[0])
PyOM = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PyOM)

# MANDATORY -- must call before any other function
PyOM.load_databases({})
```

**Verify:**
```python
assert len(dir(PyOM)) > 100, "Module not loaded properly"
assert hasattr(PyOM, 'design_magnetics_from_converter')
print(f"Materials: {len(PyOM.get_core_material_names())}")   # ~409
print(f"Shapes: {len(PyOM.get_core_shape_names(True))}")     # ~1301
```

### Installation

```bash
pip install PyOpenMagnetics
```

If the `.whl` is provided as base64-encoded parts:
```python
import base64
with open("pyopenmagnetics-1.3.0.whl.base64.partaa", "rb") as f:
    part_a = f.read()
with open("pyopenmagnetics-1.3.0.whl.base64.partab", "rb") as f:
    part_b = f.read()
whl_data = base64.b64decode(part_a + part_b)
with open("pyopenmagnetics.whl", "wb") as f:
    f.write(whl_data)
# Then: pip install pyopenmagnetics.whl
```

---

## Method A: design_magnetics_from_converter()

This goes directly from converter specifications to complete, ranked magnetic
component designs in a single call. This is the **preferred** method.

### ngspice Is Bundled

ngspice is shipped inside the wheel at `.ngspice/lib/libngspice.so`. No
separate install needed. The C++ code **ignores** the `use_ngspice` parameter:
`(void)useNgspice;` -- it always uses ngspice internally.

In sandboxed environments where `dlopen` is restricted, ngspice may fail to
initialize -- use Method B with `use_ngspice=False` in that case.

### Signature (use POSITIONAL args -- the .pyi has WRONG keyword names!)

```python
result = PyOM.design_magnetics_from_converter(
    topology_name,    # str -- REQUIRED
    converter_json,   # dict -- REQUIRED (BASE class schema, see Converter Models)
    max_results,      # int -- 1-10
    core_mode_json,   # str -- "available cores" or "standard cores"
    use_ngspice,      # bool -- IGNORED (always True internally)
    weights_json      # dict or None
)
```

### The .pyi Stub Has WRONG Keyword Names

`.pyi` says: `topology=`, `converter=`, `core_mode=`, `weights=`.
Actual pybind11: `topology_name=`, `converter_json=`, `core_mode_json=`, `weights_json=`.
**Safest: use positional arguments.**

### Pass Python Dicts -- NEVER json.dumps()!

The C++ pybind11 binding accepts **Python dicts directly**. `json.dumps()` turns
your dict into a **string** which causes `"Input JSON does not conform to schema!"`.

```python
# WRONG -- json.dumps() causes schema error
result = PyOM.design_magnetics_from_converter("flyback", json.dumps(converter), ...)

# CORRECT -- pass Python dict and plain string
result = PyOM.design_magnetics_from_converter("flyback", converter, 3, "available cores", True, None)
```

### NEVER Abandon Method A After a Schema Error!

If `design_magnetics_from_converter` throws `"Input JSON does not conform to schema!"`,
the fix is to correct the JSON -- **NOT** to fall back to `calculate_advised_cores` or
manual math. Common fixes:
1. Remove `json.dumps()` from all arguments
2. Use `"available cores"` (lowercase + space), not `"STANDARD_CORES"`
3. Use positional arguments, not keyword arguments
4. For offline flyback: use the Advanced schema with `desiredInductance` (Method B only)

---

## Method B: process_converter() + calculate_advised_magnetics()

Use when Method A returns an actual error (not a timeout). Requires you to pre-calculate
inductance and turns ratio values.

### Step 1: process_converter()

```python
processed = PyOM.process_converter("flyback", flyback_advanced, use_ngspice=False)
assert "error" not in processed
```

Uses the **Advanced** class schema (WITH `desiredInductance`, `desiredTurnsRatios`).

`process_converter` returns two keys:
- `designRequirements` -- with `magnetizingInductance`, `turnsRatios`, `name`, `topology`
- `operatingPoints` -- with full excitation waveforms per winding

### Step 2: calculate_advised_magnetics()

**WARNING (PyOM 1.3.6):** `calculate_advised_magnetics` hangs in the winding
solver phase (same bug as Method A). Use `calculate_advised_cores` instead
(see "Winding Solver Hang" section above).

If using in a future fixed version:

```python
mas_inputs = {
    "designRequirements": processed["designRequirements"],
    "operatingPoints":    processed["operatingPoints"]
}
# call process_inputs BEFORE calculate_advised_magnetics
processed_inputs = PyOM.process_inputs(mas_inputs)

# calculate_advised_magnetics takes EXACTLY 3 args -- NO weights parameter!
# core_mode must be UPPERCASE with underscore: "STANDARD_CORES" or "AVAILABLE_CORES"
designs = PyOM.calculate_advised_magnetics(
    processed_inputs, 1, "STANDARD_CORES"  # NO 4th arg -- weights not supported
)
# Result is {"data": [{"mas": {...}, "scoring": float, "scoringPerFilter": {...}}, ...]}
# NOT a list of [mas, score] tuples!
d = designs["data"][0]
mas_obj = d["mas"]
score   = d["scoring"]
```

### Per-topology thin wrappers for process_converter

`process_flyback()`, `process_buck()`, `process_boost()`,
`process_single_switch_forward()`, `process_two_switch_forward()`,
`process_active_clamp_forward()`, `process_push_pull()`,
`process_isolated_buck()`, `process_isolated_buck_boost()`,
`process_current_transformer(json, turns_ratio, secondary_resistance=0.0)`

---

## Method C: process_converter() + calculate_advised_magnetics_fast() [RECOMMENDED]

**This is the only non-hanging path in PyOM 1.3.6.** Uses the Advanced schema
(same as Method B) but calls `calculate_advised_magnetics_fast` which bypasses
the CoilAdviser winding solver entirely. Returns core + turns + DC/core losses.

### When to use

- All practical magnetics design in PyOM 1.3.6 (Methods A and B hang)
- Frequency sweeps and design space exploration (5-10x faster than winding solver)
- Any topology: flyback, buck, boost, forward, push-pull, isolated variants

### Signature

```python
results = PyOM.calculate_advised_magnetics_fast(
    processed_inputs,   # dict from process_inputs()
    max_results,        # int, 1-10
    core_mode           # str: "standard cores" or "available cores"
)
```

### Result structure

```python
# SUCCESS: {"data": [{"mas": {...}, "scoring": float, "scoringPerFilter": None}, ...]}
# ERROR:   {"data": "Exception: ..."} (data is a STRING, not a list)

results = PyOM.calculate_advised_magnetics_fast(pi, 1, "standard cores")
d = results["data"]
if isinstance(d, str):
    print(f"Error: {d}")
else:
    item = d[0]
    mas = item["mas"]
    total_losses_W = item["scoring"]          # WATTS (lower = better)
    # scoringPerFilter is None for this function

    # Core info
    core_fd = mas["magnetic"]["core"]["functionalDescription"]
    shape_name = core_fd["shape"]["name"] if isinstance(core_fd["shape"], dict) else core_fd["shape"]
    mat_name   = core_fd["material"]["name"] if isinstance(core_fd["material"], dict) else core_fd["material"]

    # Winding turns
    coil_fd = mas["magnetic"]["coil"]["functionalDescription"]
    for winding in coil_fd:
        print(f"  {winding['name']}: {winding['numberTurns']} turns")

    # Losses (first operating point)
    outputs = mas["outputs"][0] if isinstance(mas.get("outputs"), list) else mas.get("outputs", {})
    core_losses   = outputs.get("coreLosses", {}).get("coreLosses", 0)
    winding_losses = outputs.get("windingLosses", {}).get("windingLosses", 0)
```

### Complete flyback example

```python
import PyOpenMagnetics.PyOpenMagnetics as PyOM

PyOM.load_databases({})
PyOM.set_settings({"useToroidalCores": False})  # Partial dict works fine in 1.3.6

# Advanced schema: pre-calculate Lm and turns ratio analytically
# For 500V→20V, 5A, DCM at low line (D_min=0.559 > D_max=0.45):
#   n_sp = Ns/Np ≈ 0.036  (how the agent spec stores it)
#   n_ps = Np/Ns = 1/0.036 = 27.78  ← what desiredTurnsRatios takes (MKF convention)
#   Vor = (Vout + Vf) * (Np/Ns) = 20.5 * 27.78 ≈ 570V
#   D_nom = Vor/(Vin_nom+Vor) = 570/1070 ≈ 0.533
#   DCM Lm = Vin_min²·D_lim²/(2·Pout·fsw·(1+Ns/Np)) ≈ 3572 µH
flyback_adv = {
    "inputVoltage": {"minimum": 450.0, "nominal": 500.0, "maximum": 550.0},
    "desiredInductance": 3572e-6,
    "desiredTurnsRatios": [27.78],       # ALWAYS Np/Ns — confirmed from MKF source
    "desiredDutyCycle": [[0.533, 0.559, 0.509]],  # [D_nom, D_min, D_max] — matches collect_input_voltages order
    "maximumDutyCycle": 0.55,
    "efficiency": 0.90,
    "diodeVoltageDrop": 0.5,
    "currentRippleRatio": 0.4,
    "operatingPoints": [{
        "outputVoltages": [20.0],
        "outputCurrents": [5.0],
        "switchingFrequency": 50000,
        "ambientTemperature": 25
    }]
}

processed = PyOM.process_converter("flyback", flyback_adv, False)
assert "error" not in processed, processed.get("error")

pi = PyOM.process_inputs({
    "designRequirements": processed["designRequirements"],
    "operatingPoints":    processed["operatingPoints"]
})

results = PyOM.calculate_advised_magnetics_fast(pi, 3, "standard cores")
d = results["data"]
if isinstance(d, str):
    print(f"Error: {d}")
else:
    for item in d[:3]:
        mas = item["mas"]
        core_fd = mas["magnetic"]["core"]["functionalDescription"]
        shape = core_fd["shape"]["name"] if isinstance(core_fd["shape"], dict) else core_fd["shape"]
        mat   = core_fd["material"]["name"] if isinstance(core_fd["material"], dict) else core_fd["material"]
        print(f"Loss {item['scoring']:.3f}W: {shape} / {mat}")
        for w in mas["magnetic"]["coil"]["functionalDescription"]:
            print(f"  {w['name']}: {w['numberTurns']} turns")
```

### Key differences from calculate_advised_cores

| Feature | `calculate_advised_cores` | `calculate_advised_magnetics_fast` |
|---|---|---|
| Returns turns? | No (core only) | **Yes** |
| Returns losses? | No | **Yes** (DC+core) |
| `scoring` meaning | Dimensionless (higher=better) | **Total losses W** (lower=better) |
| `scoringPerFilter` | Has breakdown dict | Always `None` |
| Requires weights arg? | **Yes** (2nd arg) | No |
| Args | `(inputs, weights, n, mode)` | `(inputs, n, mode)` |



## Core Database Access

**Naming convention**: single-item lookup functions use the `find_*_by_name()` prefix,
NOT `get_*_by_name()`. `get_core_shape_by_name()` does **not exist** and will raise `AttributeError`.

### Core Shapes
```python
names    = PyOM.get_core_shape_names(True)            # True=include toroidal
families = PyOM.get_core_shape_families()              # NO argument (bool arg causes TypeError)
shape    = PyOM.find_core_shape_by_name("E 25/13/7")
```

### Core Materials
```python
mat_names = PyOM.get_core_material_names()
material  = PyOM.find_core_material_by_name("3C95")
```

### Wires
```python
wire_names = PyOM.get_wire_names()
wire       = PyOM.find_wire_by_name("Round 0.5 - Grade 1")
```

### Bobbins
```python
bobbin = PyOM.find_bobbin_by_name("E 25/13/7")
```

### WRONG names -- do NOT use these
```python
PyOM.get_core_shape_by_name(...)      # AttributeError -- does not exist
PyOM.get_core_material_by_name(...)   # AttributeError -- does not exist
PyOM.get_wire_by_name(...)            # AttributeError -- does not exist
PyOM.get_bobbin_by_name(...)          # AttributeError -- does not exist
PyOM.get_core_shape_families(True)    # TypeError -- takes no arguments
```

---

## Converter Models

### The THREE Things That MUST Be Correct

#### 1. core_mode_json String Is ALWAYS Lowercase With Space

**ALL** functions use **lowercase with space** — this is confirmed from the C++ source
(`CoreAdviser.h` `from_json`):

```cpp
if (j == "available cores") x = CoreAdviserModes::AVAILABLE_CORES;
else if (j == "standard cores") x = CoreAdviserModes::STANDARD_CORES;
else throw std::runtime_error("Input JSON does not conform to schema!");
```

```
"available cores"    -- all WE commercial cores (~1301 shapes, 60-120+ sec)
"standard cores"     -- generic shapes (faster, fewer options)
```

| Function | WRONG | CORRECT |
|---|---|---|
| `design_magnetics_from_converter` | `"STANDARD_CORES"` | `"standard cores"` |
| `calculate_advised_cores` | `"STANDARD_CORES"` | `"standard cores"` |
| `calculate_advised_magnetics` | `"STANDARD_CORES"` | `"standard cores"` |

**All three functions use the same lowercase+space format.**
`"STANDARD_CORES"` and `"AVAILABLE_CORES"` are wrong for ALL functions and will
return `"Input JSON does not conform to schema!"`.

#### 2. Flyback operatingPoints[].mode -- Optional When currentRippleRatio Is Set

The `mode` field is **optional** if `currentRippleRatio` is provided. The C++ code
will auto-infer the mode: CCM if ripple < 1.0, DCM if ripple >= 1.0.

If you omit both `mode` AND `currentRippleRatio`, you'll get an error:
`"Either current ripple ratio or mode is needed for the Flyback OperatingPoint Mode"`

Valid mode values (MAS 1.0 camelCase):
- `"continuousConductionMode"` (CCM -- most common for >20W)
- `"discontinuousConductionMode"` (DCM)
- `"boundaryModeOperation"` (BCM)
- `"quasiResonantMode"` (QR)

Other topologies (Buck, Boost, Forward, LLC) do **NOT** need a mode field.

#### 3. Use BASE Class Schema for Method A (NOT AdvancedFlyback)

`design_magnetics_from_converter()` uses `OpenMagnetics::Flyback` (base).
`process_converter()` uses `OpenMagnetics::AdvancedFlyback`.

| Field | Method A (Base) | Method B (Advanced) |
|---|---|---|
| `desiredInductance` | Causes schema error | Required |
| `desiredTurnsRatios` | Causes schema error | Required |
| `desiredDutyCycle` | Causes schema error | Optional |

#### 4. `desiredTurnsRatios` is ALWAYS Np/Ns (primary-to-secondary)

**Confirmed from MKF C++ source (`Flyback.cpp` line 521):**
```cpp
turnsRatios.push_back(primaryTurns / secondaryTurns);  // Np/Ns
```
And the duty cycle formula (line 22):
```cpp
D = (turnsRatio * outputVoltage) / (inputVoltage + turnsRatio * outputVoltage);
// = (n_ps * Vout) / (Vin + n_ps * Vout)   where n_ps = Np/Ns
```

Agent specs typically store `turnsRatios` as **Ns/Np** (e.g., `0.036`).
**Always invert before passing to `desiredTurnsRatios`:**
```python
n_sp = specs["turnsRatios"][0]["nominal"]   # e.g. 0.036 (Ns/Np from agent spec)
adv["desiredTurnsRatios"] = [1.0 / n_sp]   # e.g. 27.78 (Np/Ns for PyOM)
```

#### 5. `desiredDutyCycle` order matches `collect_input_voltages`: [nominal, minimum, maximum]

**Confirmed from MKF C++ source (`Topology.h` `collect_input_voltages`):**
```cpp
if (inputVoltage.get_nominal()) voltages.push_back(nominal);   // index 0
if (inputVoltage.get_minimum()) voltages.push_back(minimum);   // index 1
if (inputVoltage.get_maximum()) voltages.push_back(maximum);   // index 2
```
And `AdvancedFlyback::process()` uses:
```cpp
customDutyCycle = get_desired_duty_cycle()[opIndex][inputVoltageIndex];
```

```python
# desiredDutyCycle = [[D_nom, D_min, D_max]]  (one sub-list per operating point)
# Only include entries for voltages that are present in inputVoltage:
iv = specs["inputVoltage"]
dc_list = []
if iv.get("nominal") is not None: dc_list.append(round(D_nom, 3))
if iv.get("minimum") is not None: dc_list.append(round(D_min, 3))
if iv.get("maximum") is not None: dc_list.append(round(D_max, 3))
adv["desiredDutyCycle"] = [dc_list]   # outer list = one entry per operatingPoint

# For a flyback with Np/Ns=27.78, Vout=20V, Vf=0.5V:
# Vor = 20.5 * 27.78 = 569.5V
# D = Vor/(Vin+Vor):  D_nom=0.533 (Vin=500), D_min=0.559 (Vin=450), D_max=0.509 (Vin=550)
adv["desiredDutyCycle"] = [[0.533, 0.559, 0.509]]
```

**WRONG (old, incorrect ordering):**
```python
adv["desiredDutyCycle"] = [[D_vin_min, D_vin_max]]   # WRONG — missing nominal, wrong order
```
| `currentRippleRatio` | REQUIRED (Flyback/Boost/Forward) | Optional |
| `mode` in operatingPoints | Optional (auto-inferred if currentRippleRatio set) | Not needed |

### Singular vs Plural Field Names (Critical!)

| Topology | outputVoltage(s) | outputCurrent(s) |
|---|---|---|
| Flyback | `outputVoltages` (PLURAL, list) | `outputCurrents` (PLURAL, list) |
| Forward | `outputVoltages` (PLURAL, list) | `outputCurrents` (PLURAL, list) |
| PushPull | `outputVoltages` (PLURAL, list) | `outputCurrents` (PLURAL, list) |
| LLC | `outputVoltages` (PLURAL, list) | `outputCurrents` (PLURAL, list) |
| Buck | `outputVoltage` (SINGULAR, float) | `outputCurrent` (SINGULAR, float) |
| Boost | `outputVoltage` (SINGULAR, float) | `outputCurrent` (SINGULAR, float) |
| DualActiveBridge | `outputVoltage` (SINGULAR) | `outputCurrent` (SINGULAR) |

### Flyback -- Method A (BASE class)

```python
flyback_base = {
    "currentRippleRatio": 0.4,          # REQUIRED (j.at)
    "diodeVoltageDrop": 0.5,            # REQUIRED (j.at)
    "efficiency": 0.88,                 # REQUIRED (j.at)
    "inputVoltage": {                   # REQUIRED (j.at)
        "minimum": 185.0,
        "nominal": 220.0,              # recommended
        "maximum": 265.0
    },
    "operatingPoints": [{               # REQUIRED (j.at)
        "ambientTemperature": 25.0,     # REQUIRED (j.at)
        "outputVoltages": [12.0],       # REQUIRED (j.at) -- PLURAL
        "outputCurrents": [2.0],        # REQUIRED (j.at) -- PLURAL
        "switchingFrequency": 100000.0, # optional (get_stack_optional)
        "mode": "continuousConductionMode"  # Optional if currentRippleRatio is set
    }],
    "maximumDutyCycle": 0.45,           # optional
    "maximumDrainSourceVoltage": 800.0  # optional
}
```

### Flyback -- Method B (AdvancedFlyback)

```python
flyback_advanced = {
    "inputVoltage": {"minimum": 185, "maximum": 265},
    "desiredInductance": 800e-6,         # AdvancedFlyback only
    "desiredTurnsRatios": [13.5],        # ALWAYS Np/Ns (confirmed MKF source: primaryTurns/secondaryTurns)
    "desiredDutyCycle": [[0.45, 0.45]], # AdvancedFlyback only
    "maximumDutyCycle": 0.45,
    "efficiency": 0.88,
    "diodeVoltageDrop": 0.5,
    "currentRippleRatio": 0.4,
    "operatingPoints": [{
        "outputVoltages": [12.0],
        "outputCurrents": [2.0],
        "switchingFrequency": 100000,
        "ambientTemperature": 25
    }]
}
```

### Buck -- Method A (BASE class)

```python
buck_base = {
    "diodeVoltageDrop": 0.5,            # REQUIRED (j.at)
    "inputVoltage": {"minimum": 10.0, "maximum": 14.0},  # REQUIRED
    "operatingPoints": [{
        "ambientTemperature": 25.0,     # REQUIRED
        "outputVoltage": 3.3,           # REQUIRED -- SINGULAR!
        "outputCurrent": 5.0,           # REQUIRED -- SINGULAR!
        "switchingFrequency": 500000.0  # REQUIRED (j.at)
    }]
}
```

### Boost -- Method A (BASE class)

```python
boost_base = {
    "currentRippleRatio": 0.3,          # REQUIRED (j.at)
    "diodeVoltageDrop": 0.7,            # REQUIRED (j.at)
    "efficiency": 0.92,                 # REQUIRED (j.at)
    "inputVoltage": {"minimum": 5.0, "maximum": 5.0},  # REQUIRED
    "operatingPoints": [{
        "ambientTemperature": 25.0,     # REQUIRED
        "outputVoltage": 12.0,          # REQUIRED -- SINGULAR!
        "outputCurrent": 1.0,           # REQUIRED -- SINGULAR!
        "switchingFrequency": 100000.0  # REQUIRED (j.at)
    }]
}
```

### Forward (Single-Switch, Two-Switch, Active-Clamp) -- Method A

```python
forward_base = {
    "currentRippleRatio": 0.4,          # REQUIRED (j.at)
    "diodeVoltageDrop": 0.5,            # REQUIRED (j.at)
    "inputVoltage": {"minimum": 36.0, "maximum": 72.0},
    "operatingPoints": [{
        "ambientTemperature": 25.0,     # REQUIRED (j.at)
        "outputVoltages": [5.0],        # REQUIRED (j.at) -- PLURAL
        "outputCurrents": [10.0],       # REQUIRED (j.at) -- PLURAL
        "switchingFrequency": 200000.0  # REQUIRED (j.at) for Forward!
    }]
    # optional: "efficiency", "dutyCycle", "maximumSwitchCurrent"
}
```
Use topology strings: `"single_switch_forward"`, `"two_switch_forward"`, `"active_clamp_forward"`.

### LLC -- Method A (BASE class)

```python
llc_base = {
    "inputVoltage": {"minimum": 380.0, "maximum": 420.0},  # REQUIRED (j.at)
    "minSwitchingFrequency": 100000.0,  # REQUIRED (j.at)
    "maxSwitchingFrequency": 300000.0,  # REQUIRED (j.at)
    "operatingPoints": [{               # REQUIRED (j.at)
        "ambientTemperature": 25.0,
        "outputVoltages": [12.0],       # REQUIRED -- PLURAL
        "outputCurrents": [5.0],        # REQUIRED -- PLURAL
        "switchingFrequency": 200000.0  # Operating frequency
    }],
    # optional fields:
    "efficiency": 0.95,
    "qualityFactor": 0.5,
    "resonantFrequency": 150000.0
}
```

### PushPull -- Same Schema as Forward

### How to Estimate Flyback Parameters

If user says "220V to 12V, 2A" without detail:
```
Vin_dc_min ~ 185 x sqrt(2) x 0.9 ~ 235V
Dmax = 0.45, eta = 0.88
n = (12+0.5)*0.55 / (235*0.45*0.88) ~ 0.074
turnsRatio = 1/n ~ 13.5   # desiredTurnsRatios = [Np/Ns] — ALWAYS Np/Ns, NOT Ns/Np
Lm ~ 800 uH                --> for desiredInductance (Method B only)
```

---

## Supported Topologies

| Topology string | Method A class | Singular/Plural | Notes |
|---|---|---|---|
| `"flyback"` | `Flyback` | PLURAL + **mode required** | Most common |
| `"buck"` | `Buck` | **SINGULAR** | |
| `"boost"` | `Boost` | **SINGULAR** | |
| `"single_switch_forward"` | `SingleSwitchForward` | PLURAL | switchingFreq REQUIRED |
| `"two_switch_forward"` | `TwoSwitchForward` | PLURAL | switchingFreq REQUIRED |
| `"active_clamp_forward"` | `ActiveClampForward` | PLURAL | switchingFreq REQUIRED |
| `"push_pull"` | `PushPull` | PLURAL | Same as Forward |
| `"llc"` | `Llc` | PLURAL | |
| `"isolated_buck"` | `IsolatedBuck` | PLURAL | |
| `"isolated_buck_boost"` | `IsolatedBuckBoost` | PLURAL | |
| Others (`"cllc"`, `"dab"`, `"psfb"`, `"pshb"`) | internal fallback | varies | |

---

## Operating Point Definition

Operating points are defined within the converter JSON under `operatingPoints` (a list).
Each operating point specifies:

- `ambientTemperature` -- REQUIRED for all topologies (float, degrees C)
- `outputVoltage`/`outputVoltages` -- REQUIRED (singular for Buck/Boost, plural list for others)
- `outputCurrent`/`outputCurrents` -- REQUIRED (singular for Buck/Boost, plural list for others)
- `switchingFrequency` -- REQUIRED for Buck, Boost, Forward; optional for Flyback
- `mode` -- Optional for Flyback if `currentRippleRatio` is set (auto-inferred: CCM if ripple < 1.0, DCM if >= 1.0)

---

## Settings

Control global behavior with the settings API:

```python
# Get current settings
settings = PyOM.get_settings()

# Modify settings
PyOM.set_settings({
    "useOnlyCoresInStock": True,         # only use cores marked in stock
    "painterNumberPointsX": 100,         # plot X resolution
    "painterNumberPointsY": 100,         # plot Y resolution
    "coilAllowMarginTape": True,         # allow margin tape in coil designs
    "coilAllowInsulatedWire": True,      # allow insulated wire
    "magneticFieldMirroringDimension": 0 # simulation accuracy (0=fast, higher=more accurate)
})

# Reset to defaults
PyOM.reset_settings()
```

---

## Weights (Priorities)

**`design_magnetics_from_converter()`** accepts an optional weights dict as the 6th positional arg.
The keys must be **lowercase**: `"efficiency"`, `"cost"`, `"dimensions"`.

**`calculate_advised_magnetics()`** does NOT accept a weights argument (3 args only).

**`calculate_advised_cores()`** accepts weights as the 2nd arg. Both UPPERCASE and lowercase
keys work (verified empirically). UPPERCASE is conventional since scoring breakdown uses
UPPERCASE keys:

```python
# For design_magnetics_from_converter (6th arg, lowercase keys)
weights_method_a = {
    "efficiency": 2.0,    # prioritize low losses
    "cost": 1.0,
    "dimensions": 0.5
}

# For calculate_advised_cores (2nd arg, both work -- use UPPERCASE by convention)
weights_cores = {
    "COST": 1.0,
    "EFFICIENCY": 2.0,
    "DIMENSIONS": 0.5
}
```

| Function | Weights arg position | Key format | Default |
|---|---|---|---|
| `design_magnetics_from_converter` | 6th (last) | **lowercase** | `None` (equal) |
| `calculate_advised_magnetics` | **NOT supported** (3 args only) | N/A | equal |
| `calculate_advised_cores` | 2nd | UPPERCASE or lowercase | required |

---

## Result Structure

### `calculate_advised_cores()` result

```python
result = PyOM.calculate_advised_cores(inputs, weights, 3, "standard cores")
# Returns: {"data": [{"mas": {...}, "scoring": float, "scoringPerFilter": {...}}, ...]}

d = result["data"]              # list of result dicts (NOT a list of chars!)
# On error: result["data"] is a string (NOT a list), e.g. "Exception: Input JSON does not conform to schema!"
if isinstance(d, str):
    print(f"Error: {d}")
else:
    first = d[0]                   # First (best) core
    mas_obj = first["mas"]         # MAS dict (core filled, coil may be empty)
    score   = first["scoring"]     # float -- higher is better
    score_breakdown = first["scoringPerFilter"]  # {"EFFICIENCY": 0.9, ...}

    core_fd = mas_obj["magnetic"]["core"]["functionalDescription"]
    shape    = core_fd["shape"]        # dict with "name", family, dimensions
    material = core_fd["material"]     # dict with "name", manufacturer, Bsat, etc.
    gapping  = core_fd["gapping"]      # list of gap dicts
```

### `calculate_advised_magnetics()` result (if/when working)

```python
result = PyOM.calculate_advised_magnetics(inputs, 3, "standard cores")
# Returns: {"data": [{"mas": {...}, "scoring": float, "scoringPerFilter": {...}}, ...]}
# Same structure as calculate_advised_cores -- NOT a list of [mas, score] tuples!
# On error: result["data"] is a string, e.g. "Exception: ..."

d = result["data"][0]
mas_obj = d["mas"]                 # Full MAS: core + coil + inputs + outputs
score   = d["scoring"]

# Core info
core_fd = mas_obj["magnetic"]["core"]["functionalDescription"]
shape    = core_fd["shape"]        # may be string name OR full dict
material = core_fd["material"]     # may be string name OR full dict
gapping  = core_fd["gapping"]

# Coil info
coil_fd = mas_obj["magnetic"]["coil"]["functionalDescription"]
for winding in coil_fd:
    name      = winding["name"]
    turns     = winding["numberTurns"]
    parallels = winding.get("numberParallels", 1)
    wire      = winding.get("wire", {})

# Design requirements (from inputs)
dr = mas_obj["inputs"]["designRequirements"]
mag_ind     = dr["magnetizingInductance"]   # {nominal, minimum, maximum}
turns_ratios = dr["turnsRatios"]            # list of {nominal}
```

### Error detection

```python
# Both functions return {"data": [...]} on success (data is a list of dicts)
# On error, data is a STRING (not a list):
result = PyOM.calculate_advised_cores(processed, weights, 3, "standard cores")
d = result["data"]
if isinstance(d, str):
    print(f"Error: {d}")   # e.g. "Exception: Input JSON does not conform to schema!"
else:
    for item in d:
        # process results...
        pass
```

# Operating points used (with full waveforms)
ops = mas_obj["inputs"]["operatingPoints"]
```

---

## Post-Design Analysis

### Inductance Calculation
```python
inductance = PyOM.calculate_inductance(magnetic)
# Returns dict with:
#   magnetizingInductance -> {magnetizingInductance, coreReluctance, gappingReluctance, ...}
```

### Core Losses
```python
models = {"coreLosses": "IGSE"}  # or "STEINMETZ", "ROSHEN", etc.
losses = PyOM.calculate_core_losses(core, coil, operating_point, models)
```

### Winding Losses
```python
winding_losses = PyOM.calculate_winding_losses(magnetic, operating_point, temperature)
```

### Full Simulation
```python
sim_result = PyOM.simulate(mas)
```

### Wire Utilities
```python
# Skin depth at frequency
delta = PyOM.calculate_effective_skin_depth("copper", 100000, 25)

# DC resistance per meter for a given wire diameter
rdc = PyOM.calculate_dc_resistance_per_meter("copper", 0.5e-3, 25)
```

### SPICE Export
```python
subcircuit = PyOM.export_magnetic_as_subcircuit(magnetic)
# Returns SPICE subcircuit string
```

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `"Input JSON does not conform to schema!"` (instant) | Used `json.dumps()` on converter or core_mode | Pass Python dict directly -- NOT `json.dumps(dict)` |
| `"Input JSON does not conform to schema!"` (instant) | Wrong `core_mode_json` | Use `"standard cores"` or `"available cores"` (lowercase + space) for ALL functions |
| `"Input JSON does not conform to schema!"` (instant) | Used `"STANDARD_CORES"` with `calculate_advised_cores` | Use `"standard cores"` (lowercase) -- ALL functions use the same format |
| `"Input JSON does not conform to schema!"` (instant) | Missing both `mode` and `currentRippleRatio` | Add either `mode` OR `currentRippleRatio` (mode auto-inferred if ripple set) |
| `"Input JSON does not conform to schema!"` (instant) | Used `desiredInductance` with Method A | Remove it -- base class doesn't accept it |
| `"ngspice is not available"` | Sandbox restriction | Use Method B with `use_ngspice=False` |
| `"key 'currentRippleRatio' not found"` | Missing required field | Add to JSON |
| `"key 'outputVoltage' not found"` | Used plural for Buck/Boost | Use singular: `outputVoltage`, `outputCurrent` |
| `"key 'outputVoltages' not found"` | Used singular for Flyback/Forward | Use plural: `outputVoltages`, `outputCurrents` |
| `TypeError: incompatible function arguments` | Wrong keyword names | Use positional args |
| Empty namespace (0 functions) | Bare `import PyOpenMagnetics` | Use `importlib` (Section: Import Procedure) |
| `get_core_shape_names()` TypeError | Missing bool arg | Use `get_core_shape_names(True)` |

### Common Wrong Patterns vs Correct Patterns

**WRONG: Using json.dumps()**
```python
# This WILL FAIL with "Input JSON does not conform to schema!"
result = PyOM.design_magnetics_from_converter(
    "flyback",
    json.dumps(converter_spec),  # WRONG - json.dumps() converts dict to string!
    3
)
```

**CORRECT: Pass Python dict directly**
```python
result = PyOM.design_magnetics_from_converter(
    "flyback",
    converter_spec,  # CORRECT - Python dict, not string
    3,
    "standard cores",
    True,
    None
)
```

---

**WRONG: Inventing your own JSON structure**
```python
# This WILL FAIL - this is NOT the correct schema!
converter_spec = {
    "inputs": [{
        "name": "Primary",
        "voltage": {"nominal": 310.0}
    }],
    "outputs": [{
        "name": "Secondary",
        "voltage": 12.0,
        "current": 2.0
    }],
    "switchingFrequency": 100000.0
}
```

**CORRECT: Use the exact schema from the Converter Models section**
```python
flyback = {
    "currentRippleRatio": 0.4,
    "diodeVoltageDrop": 0.5,
    "efficiency": 0.88,
    "inputVoltage": {
        "minimum": 185.0,
        "nominal": 220.0,
        "maximum": 265.0
    },
    "operatingPoints": [{
        "ambientTemperature": 25.0,
        "outputVoltages": [12.0],
        "outputCurrents": [2.0],
        "switchingFrequency": 100000.0
    }],
    "maximumDutyCycle": 0.45
}
```

---

**WRONG: Using keyword arguments from .pyi stubs**
```python
result = PyOM.design_magnetics_from_converter(
    topology="flyback",      # Wrong keyword
    converter=flyback,       # Wrong keyword
    max_results=3
)
```

**CORRECT: Use positional arguments**
```python
result = PyOM.design_magnetics_from_converter(
    "flyback",           # topology_name (positional)
    flyback,             # converter_json (positional)
    3,                   # max_results (positional)
    "standard cores",    # core_mode_json (positional)
    True,                # use_ngspice (positional, ignored internally)
    None                 # weights_json (positional)
)
```

---

**WRONG: Abandoning Method A after schema error**
```python
try:
    result = PyOM.design_magnetics_from_converter(...)
except:
    print("Method A failed, doing manual calculations instead...")
    L = V * D / (f * delta_I)  # NO!
```

**CORRECT: Fix the JSON and retry**
```python
# If you get a schema error, FIX THE JSON, don't abandon Method A
# Check:
# 1. Did you use json.dumps()? Remove it.
# 2. Did you use the correct field names? Check Converter Models section.
# 3. Did you use "available cores" (lowercase with space)?
# 4. Did you include all REQUIRED fields?
```

---

## Complete Examples

### Working Method: calculate_advised_cores() (no winding solver, reliable in 1.3.6)

```python
import importlib.util, os, glob

# Load module
pkg_dir = os.path.join(
    os.path.dirname(__import__('PyOpenMagnetics').__path__[0]),
    'PyOpenMagnetics'
)
so_files = glob.glob(os.path.join(pkg_dir, 'PyOpenMagnetics.cpython-*'))
spec = importlib.util.spec_from_file_location('PyOpenMagnetics', so_files[0])
PyOM = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PyOM)
PyOM.load_databases({})

# Build MAS inputs manually (from calculated Lm and excitation waveforms)
# For flyback 500V→20V, 5A, fs=100kHz, D=0.5:
#   n = Ns/Np = (Vout + Vf) * (1-D) / (Vin_min * D) = 20.5*0.5 / (450*0.5) = 0.0456
#   Lm = Vin_min * D^2 / (2 * fs * Ip_ripple) -- size for r=0.4 ripple
#   Ip_avg = Pout / (Vin_nom * eta) = 100 / (500*0.93) = 0.215 A
#   ΔIp = r * Ip_avg = 0.4 * 0.215 = 0.086 A (ripple)
#   Lm = 500 * 0.5^2 / (2 * 100000 * 0.086) ≈ 7.3 mH

mas_inputs = {
    "designRequirements": {
        "magnetizingInductance": {"nominal": 7.3e-3},
        "turnsRatios": [{"nominal": 0.0456}],  # Ns/Np = 1/21.9  (agent-side convention)
    # NOTE: when passing to desiredTurnsRatios, invert: 21.9 (Np/Ns = MKF convention)
        "topology": "flybackConverter",        # MAS 1.0 camelCase enum (see Note #10)
        "operatingTemperature": {"maximum": 100}
    },
    "operatingPoints": [{
        "conditions": {"ambientTemperature": 25},
        "excitationsPerWinding": [
            {
                "frequency": 100000,
                "current": {"processed": {
                    "label": "flybackPrimary",
                    "peakToPeak": 0.086,   # ΔIp = r * Ip_avg
                    "offset": 0.215,       # Ip_avg
                    "dutyCycle": 0.5
                }},
                "voltage": {"processed": {
                    "label": "rectangular",
                    "peakToPeak": 500.0,   # Vin_nom
                    "offset": 0.0,
                    "dutyCycle": 0.5
                }}
            },
            {
                "frequency": 100000,
                "current": {"processed": {
                    "label": "flybackSecondary",
                    "peakToPeak": 1.88,    # ΔIs = ΔIp / n
                    "offset": 4.7,         # Is_avg = Iout / (1-D) + ΔIs/2
                    "dutyCycle": 0.5
                }},
                "voltage": {"processed": {
                    "label": "rectangular",
                    "peakToPeak": 20.0,
                    "offset": 0.0,
                    "dutyCycle": 0.5
                }}
            }
        ]
    }]
}

# process_inputs BEFORE calculate_advised_cores
processed_inputs = PyOM.process_inputs(mas_inputs)

# calculate_advised_cores: weights use UPPERCASE keys (lowercase also accepted)
# core_mode: "standard cores" (lowercase + space) -- same as design_magnetics_from_converter!
# "STANDARD_CORES" is WRONG and causes schema error
weights = {"COST": 1.0, "EFFICIENCY": 2.0, "DIMENSIONS": 0.5}
result = PyOM.calculate_advised_cores(processed_inputs, weights, 3, "standard cores")
# NOTE: "standard cores" (lowercase + space) -- NEVER "STANDARD_CORES"

# Parse result: {"data": [...]} on success, {"data": "Exception: ..."} on error
d = result["data"]
if isinstance(d, str):
    print(f"Error: {d}")
else:
    for item in d[:3]:
        core_fd = item["mas"]["magnetic"]["core"]["functionalDescription"]
        shape_name = core_fd["shape"]["name"] if isinstance(core_fd["shape"], dict) else core_fd["shape"]
        mat_name   = core_fd["material"]["name"] if isinstance(core_fd["material"], dict) else core_fd["material"]
        print(f"Score {item['scoring']:.3f}: {shape_name} / {mat_name}")
```

### Method A: design_magnetics_from_converter() (hangs in PyOM 1.3.6, document for future use)

```python
# ⚠️ PyOM 1.3.6: HANGS after [CoreAdviser] After Losses -- do not use
# Document for reference when the winding solver bug is fixed

flyback = {
    "currentRippleRatio": 0.4,
    "diodeVoltageDrop": 0.5,
    "efficiency": 0.88,
    "inputVoltage": {"minimum": 185.0, "maximum": 265.0},  # omit nominal if strictly between min/max
    "maximumDutyCycle": 0.45,
    "operatingPoints": [{
        "ambientTemperature": 25.0,
        "outputVoltages": [12.0],    # PLURAL for flyback
        "outputCurrents": [2.0],     # PLURAL for flyback
        "switchingFrequency": 100000.0
    }]
}

result = PyOM.design_magnetics_from_converter(
    "flyback",          # topology_name (positional, lowercase)
    flyback,            # converter_json (positional, Python dict -- NOT json.dumps())
    1,                  # max_results
    "available cores",  # core_mode_json (lowercase + space for this function)
    True,               # use_ngspice (ignored internally)
    None                # weights_json (lowercase keys: "efficiency", "cost", "dimensions")
)

if isinstance(result, dict) and "error" in result:
    print(f"Error: {result['error']}")
# If it returns (when bug is fixed), result structure is same as calculate_advised_cores
# {"data": [{"mas": {...}, "scoring": float, "scoringPerFilter": {...}}]}
```

### Method B: process_converter() then calculate_advised_magnetics()

```python
# ⚠️ PyOM 1.3.6: calculate_advised_magnetics ALSO hangs (winding solver)
# Document for future reference

flyback_adv = {
    "inputVoltage": {"minimum": 185.0, "maximum": 265.0},
    "desiredInductance": 800e-6,        # AdvancedFlyback only
    "desiredTurnsRatios": [13.5],       # ALWAYS Np/Ns (confirmed MKF source: primaryTurns/secondaryTurns)
    "desiredDutyCycle": [[0.45, 0.45]], # [D_nom, D_min, D_max] — matches collect_input_voltages order
    "maximumDutyCycle": 0.45,
    "efficiency": 0.88,
    "diodeVoltageDrop": 0.5,
    "currentRippleRatio": 0.4,
    "operatingPoints": [{
        "outputVoltages": [12.0],
        "outputCurrents": [2.0],
        "switchingFrequency": 100000,
        "ambientTemperature": 25
    }]
}
processed = PyOM.process_converter("flyback", flyback_adv, False)  # use_ngspice=False

# process_inputs is REQUIRED before calculate_advised_magnetics
processed_inputs = PyOM.process_inputs({
    "designRequirements": processed["designRequirements"],
    "operatingPoints":    processed["operatingPoints"]
})

# calculate_advised_magnetics: 3 args ONLY -- NO weights parameter
# core_mode uses LOWERCASE WITH SPACE (same as all other functions!)
designs = PyOM.calculate_advised_magnetics(processed_inputs, 1, "standard cores")

# Parse result (same structure as calculate_advised_cores)
d = designs["data"][0]
mas_obj = d["mas"]
score   = d["scoring"]
core_fd = mas_obj["magnetic"]["core"]["functionalDescription"]
coil_fd = mas_obj["magnetic"]["coil"]["functionalDescription"]
```

---

## Quick-Reference Checklists

### Working (PyOM 1.3.6): calculate_advised_cores()

- [ ] Used `importlib` to load `.so` (NOT bare `import`)
- [ ] Called `load_databases({})` after loading
- [ ] Called `process_inputs(mas_inputs)` BEFORE passing to `calculate_advised_cores`
- [ ] `mas_inputs` has `designRequirements` + `operatingPoints` (MAS inputs schema)
- [ ] `designRequirements.topology` uses MAS 1.0 camelCase enum: `"flybackConverter"`, `"buckConverter"`, `"boostConverter"`, etc. (see Note #10 -- PyOM now accepts MAS 1.0 camelCase, pre-1.0 Title Case, and legacy short forms transparently)
- [ ] `excitationsPerWinding` has one entry per winding with `frequency`, `current`, `voltage`
- [ ] Weights use UPPERCASE or lowercase keys: `{"COST": 1.0, "EFFICIENCY": 2.0, "DIMENSIONS": 0.5}`
- [ ] `core_mode_json` is **`"standard cores"`** or **`"available cores"`** (lowercase + space!)
- [ ] **NEVER use `"STANDARD_CORES"` or `"AVAILABLE_CORES"` -- these cause schema error**
- [ ] Parse result: check `isinstance(result["data"], str)` for error, otherwise iterate list

### Method A (future): design_magnetics_from_converter()

- [ ] Used `importlib` to load `.so` (NOT bare `import`)
- [ ] Called `load_databases({})` after loading
- [ ] Topology first arg is **lowercase** (`"flyback"`, not `"Flyback"`)
- [ ] `core_mode_json` is `"available cores"` or `"standard cores"` (**lowercase + space**)
- [ ] JSON uses **BASE class** schema (NO `desiredInductance`, NO `desiredTurnsRatios`)
- [ ] For Flyback: `currentRippleRatio` present; `outputVoltages`/`outputCurrents` **PLURAL**
- [ ] For Buck/Boost: `outputVoltage`/`outputCurrent` are **SINGULAR**
- [ ] Passing **Python dicts directly** -- NOT `json.dumps()` (causes schema error)
- [ ] Weights (6th arg) use **lowercase** keys: `"efficiency"`, `"cost"`, `"dimensions"`

### Method B (future): process_converter() + calculate_advised_magnetics()

- [ ] JSON uses **Advanced** schema (WITH `desiredInductance`, `desiredTurnsRatios`)
- [ ] `use_ngspice=False` to skip ngspice
- [ ] Call `process_inputs()` on the result BEFORE passing to `calculate_advised_magnetics`
- [ ] `calculate_advised_magnetics` takes **3 args ONLY** -- no weights parameter!
- [ ] `core_mode_json` uses **lowercase with space**: `"standard cores"` or `"available cores"`

---

## Known Issues and Notes (PyOM 1.3.6)

1. **Winding solver hangs** -- `design_magnetics_from_converter` and
   `calculate_advised_magnetics` both hang indefinitely after
   `[CoreAdviser] After Losses: N`. This is a bug in the C++ winding layout solver.
   **Workaround: use `calculate_advised_cores` to get the core, then calculate turns manually.**

2. **`calculate_advised_magnetics_fast` EXISTS in PyOM 1.3.6** and is the **recommended path**.
   It bypasses the winding solver (CoilAdviser), returns core + turns + DC/core losses.
   - Call: `calculate_advised_magnetics_fast(processed_inputs, max_results, core_mode)`
   - `core_mode`: `"standard cores"` or `"available cores"` (lowercase + space, same as all functions)
   - Result: `{"data": [{"mas": {...}, "scoring": float_losses_W, "scoringPerFilter": None}]}`
   - `scoring` = **total losses in Watts** (lower is better -- NOT a dimensionless score)
   - `scoringPerFilter` is always `None` for this function (unlike `calculate_advised_cores`)
   - Winding info: `mas["magnetic"]["coil"]["functionalDescription"]` → list with `numberTurns`
   - Losses: `mas["outputs"][0]["coreLosses"]["coreLosses"]` and `mas["outputs"][0]["windingLosses"]["windingLosses"]`
   - See `sweep_frequency.py:design_at_frequency_fast()` for the complete reference implementation

2. **`use_ngspice` is ignored by `design_magnetics_from_converter`** -- `(void)useNgspice;`
   in C++ source. ngspice is always used internally.

3. **`.pyi` has wrong keyword names** -- use positional args or correct names:
   `topology_name=`, `converter_json=`, `core_mode_json=`, `weights_json=`.

4. **Two different Flyback classes** -- `Flyback` (base, Method A) vs
   `AdvancedFlyback` (Method B). Different JSON schemas. Never mix them.

5. **`get_core_shape_names()`** requires boolean arg: `True`=include toroidal.

6. **core_mode string format is ALWAYS lowercase + space for ALL functions:**
   - `design_magnetics_from_converter`: `"available cores"` / `"standard cores"`
   - `calculate_advised_cores`: `"available cores"` / `"standard cores"` (SAME FORMAT!)
   - `calculate_advised_magnetics`: `"available cores"` / `"standard cores"` (SAME FORMAT!)
   - **`"AVAILABLE_CORES"` and `"STANDARD_CORES"` are WRONG for ALL functions.** They
     cause `"Input JSON does not conform to schema!"` confirmed from C++ source:
     `CoreAdviser.h` only accepts `"available cores"`, `"standard cores"`, `"custom cores"`.

7. **`calculate_advised_magnetics` takes 3 args, NOT 4** -- no weights parameter.
   Weights go in `calculate_advised_cores` (2nd arg, UPPERCASE keys).

8. **Result structure is `{"data": [...]}` on success, `{"data": "Exception: ..."}` on error**
   -- on success, `data` is a list of `{"mas": {...}, "scoring": float, "scoringPerFilter": {...}}` dicts.
   On error, `data` is a **string** (the error message). Check with `isinstance(result["data"], str)`.
   The old knowledge file said errors are a list of single chars -- this is WRONG.

9. **`process_inputs()` is required before `calculate_advised_*`** -- pass the raw
   `{designRequirements, operatingPoints}` dict through `process_inputs()` first.

10. **`topology` argument: PyOM accepts all three forms transparently.**
    Since MAS 1.0 (RFC 0007), the canonical enum values in `designRequirements.topology`
    are camelCase: `"flybackConverter"`, `"buckConverter"`, `"boostConverter"`, `"forwardConverter"`,
    `"pushPullConverter"`, `"halfBridgeConverter"`, `"fullBridgeConverter"`, `"llcConverter"`,
    `"dualActiveBridgeConverter"`, `"phaseShiftedFullBridgeConverter"`, etc.

    As of PyOpenMagnetics 1.3.10 (PyMKF `converter.cpp` `normalize_topology_name()` +
    `compat::migrate_pre_1_0()`), `process_converter()` and `design_magnetics_from_converter()`
    accept **all three** forms in the `topology_name` argument and migrate any pre-1.0
    enum strings inside the JSON body (e.g. `mode: "Continuous Conduction Mode"` ->
    `mode: "continuousConductionMode"`):

    - MAS 1.0 camelCase: `"flybackConverter"`, `"buckConverter"`, ...
    - Pre-1.0 Title Case: `"Flyback Converter"`, `"Buck Converter"`, ...
    - Short form: `"flyback"`, `"buck"`, `"boost"`, `"forward"`, `"llc"`, ...

    Unknown names still throw `Unknown topology: <name>` -- no silent fallback.
    Returned MAS docs always emit MAS 1.0 camelCase in `designRequirements.topology`.

    The example file `02_flyback_efd25_3c95.json` uses the legacy bare value `"Flyback"`
    -- this is rejected (not a recognized form); use `"flybackConverter"` instead.

11. **Do NOT `json.dumps()` the arguments** -- pass Python dicts directly.

12. **`set_settings()` accepts partial dicts** -- you can pass only the keys you want to change.
    The C++ binding merges the partial dict into the current settings.
    Example: `PyOM.set_settings({"useToroidalCores": False})` works correctly.
    Confirmed in PyOM 1.3.6 with `useToroidalCores`, `useOnlyCoresInStock`, etc.
