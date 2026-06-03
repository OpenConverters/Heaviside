# OpenMagnetics Converter & Adviser Models

Reference for the OpenMagnetics MKF C++ library API. All classes live in `namespace OpenMagnetics`. JSON serialization uses nlohmann/json (`from_json`/`to_json`).

---

## Supported Topologies

All topologies inherit from the `Topology` base class and implement:

```cpp
DesignRequirements process_design_requirements();
std::vector<OperatingPoint> process_operating_points(const std::vector<double>& turnsRatios, double magnetizingInductance);
bool run_checks(bool assert = false);
```

The `process()` method on the base `Topology` calls `process_design_requirements()` + `process_operating_points()` and returns an `Inputs` object ready for MagneticAdviser.

### Non-Isolated (Inductor-Based)

| Topology | Class | Magnetic Component | MAS Base |
|---|---|---|---|
| Buck | `Buck` | Inductor | `MAS::Buck` |
| Boost | `Boost` | Inductor | `MAS::Boost` |
| PFC Boost | `PowerFactorCorrection` | Inductor | (standalone) |

### Isolated (Transformer-Based)

| Topology | Class | Magnetic Component | MAS Base |
|---|---|---|---|
| Flyback | `Flyback` | Coupled inductor / transformer | `MAS::Flyback` |
| Single-Switch Forward | `SingleSwitchForward` | Transformer (+ demagnetization winding) | `MAS::Forward` |
| Two-Switch Forward | `TwoSwitchForward` | Transformer | `MAS::Forward` |
| Active Clamp Forward | `ActiveClampForward` | Transformer | `MAS::Forward` |
| Push-Pull | `PushPull` | Center-tapped transformer | `MAS::PushPull` |
| Isolated Buck (Flybuck) | `IsolatedBuck` | Coupled inductor | `MAS::IsolatedBuck` |
| Isolated Buck-Boost | `IsolatedBuckBoost` | Coupled inductor | `MAS::IsolatedBuckBoost` |
| Phase-Shifted Full Bridge | `PhaseShiftedFullBridge` | Transformer | `MAS::PhaseShiftFullBridge` |
| Phase-Shifted Half Bridge | `PhaseShiftedHalfBridge` | Transformer | `MAS::PhaseShiftFullBridge` |

### Resonant

| Topology | Class | Magnetic Component | MAS Base |
|---|---|---|---|
| LLC (Half/Full Bridge) | `Llc` | Transformer (+ optional integrated Lr) | `MAS::LlcResonant` |
| CLLC Bidirectional | `CllcConverter` | Transformer | `MAS::CllcResonant` |

### Bidirectional

| Topology | Class | Magnetic Component | MAS Base |
|---|---|---|---|
| Dual Active Bridge (DAB) | `Dab` | Transformer (+ series inductor) | `MAS::DualActiveBridge` |
| CLLC Bidirectional | `CllcConverter` | Transformer | `MAS::CllcResonant` |

### EMI / Sensing

| Topology | Class | Magnetic Component | MAS Base |
|---|---|---|---|
| Common Mode Choke | `CommonModeChoke` | Coupled inductor (bifilar/trifilar) | (standalone) |
| Differential Mode Choke | `DifferentialModeChoke` | Inductor | (standalone) |
| Current Transformer | `CurrentTransformer` | Transformer | `MAS::CurrentTransformer` |

---

## Topology Input Schemas

### Buck / Boost (Non-Isolated Inductors)

Standard mode (auto-calculates inductance from ripple ratio):

```json
{
  "inputVoltage": {"nominal": 12.0, "minimum": 10.0, "maximum": 14.0},
  "diodeVoltageDrop": 0.5,
  "currentRippleRatio": 0.4,
  "efficiency": 0.9,
  "maximumSwitchCurrent": null,
  "operatingPoints": [
    {
      "outputVoltage": 5.0,
      "outputCurrent": 2.0,
      "switchingFrequency": 200000,
      "ambientTemperature": 25.0
    }
  ]
}
```

Advanced mode (user specifies inductance directly):

```json
{
  "inputVoltage": {"nominal": 12.0, "minimum": 10.0, "maximum": 14.0},
  "diodeVoltageDrop": 0.5,
  "currentRippleRatio": 0.4,
  "efficiency": 0.9,
  "operatingPoints": [...],
  "desiredInductance": 47e-6
}
```

### Flyback (Isolated)

Standard mode:

```json
{
  "inputVoltage": {"nominal": 310.0, "minimum": 250.0, "maximum": 375.0},
  "diodeVoltageDrop": 0.5,
  "maximumDrainSourceVoltage": 600,
  "maximumDutyCycle": 0.5,
  "currentRippleRatio": 0.4,
  "efficiency": 0.85,
  "operatingPoints": [
    {
      "outputVoltages": [12.0, 5.0],
      "outputCurrents": [1.5, 0.5],
      "switchingFrequency": 100000,
      "ambientTemperature": 25.0,
      "mode": "Continuous Conduction Mode"
    }
  ]
}
```

Advanced mode:

```json
{
  "inputVoltage": {"nominal": 310.0, "minimum": 250.0, "maximum": 375.0},
  "diodeVoltageDrop": 0.5,
  "efficiency": 0.85,
  "currentRippleRatio": null,
  "desiredInductance": 500e-6,
  "desiredTurnsRatios": [0.1],
  "desiredDutyCycle": [[0.45, 0.35]],
  "desiredDeadTime": [1e-6],
  "operatingPoints": [...]
}
```

### Forward Converters (Single-Switch, Two-Switch, Active Clamp)

All three share the `MAS::Forward` base and use `ForwardOperatingPoint`:

```json
{
  "inputVoltage": {"nominal": 48.0, "minimum": 36.0, "maximum": 72.0},
  "diodeVoltageDrop": 0.5,
  "currentRippleRatio": 0.4,
  "efficiency": 0.9,
  "outputInductance": null,
  "operatingPoints": [
    {
      "outputVoltages": [5.0],
      "outputCurrents": [10.0],
      "switchingFrequency": 200000,
      "ambientTemperature": 25.0
    }
  ]
}
```

### LLC Resonant

Standard mode:

```json
{
  "inputVoltage": {"nominal": 400.0, "minimum": 360.0, "maximum": 420.0},
  "bridgeType": "half-bridge",
  "efficiency": 0.96,
  "resonantFrequency": 100000,
  "minSwitchingFrequency": 80000,
  "maxSwitchingFrequency": 140000,
  "qualityFactor": 0.3,
  "integratedResonantInductor": false,
  "operatingPoints": [
    {
      "outputVoltages": [12.0],
      "outputCurrents": [5.0],
      "ambientTemperature": 25.0
    }
  ]
}
```

Advanced mode adds:

```json
{
  "desiredTurnsRatios": [16.0],
  "desiredMagnetizingInductance": 500e-6,
  "desiredResonantInductance": 50e-6,
  "desiredResonantCapacitance": 47e-9
}
```

Computed internal values: `computedResonantInductance` (Ls), `computedResonantCapacitance` (C), `computedInductanceRatio` (Ln = Lm/Ls, default 5), `computedDeadTime` (default 1 us).

### DAB (Dual Active Bridge)

```json
{
  "inputVoltage": {"nominal": 400.0, "minimum": 360.0, "maximum": 420.0},
  "efficiency": 0.96,
  "seriesInductance": null,
  "useLeakageInductance": true,
  "operatingPoints": [
    {
      "outputVoltages": [48.0],
      "outputCurrents": [10.0],
      "switchingFrequency": 100000,
      "ambientTemperature": 25.0
    }
  ]
}
```

Advanced mode adds: `desiredTurnsRatios`, `desiredMagnetizingInductance`, `desiredSeriesInductance`.

Key static helper methods:
- `Dab::compute_power(V1, V2, N, phi, Fs, L)` -- power from phase shift
- `Dab::compute_phase_shift(V1, V2, N, Fs, L, P)` -- phase shift for desired power
- `Dab::compute_series_inductance(V1, V2, N, phi, Fs, P)` -- required L
- `Dab::check_zvs_primary(phi, d)` / `check_zvs_secondary(phi, d)` -- ZVS checks

### CLLC Bidirectional Resonant

```json
{
  "inputVoltage": {"nominal": 400.0, "minimum": 360.0, "maximum": 420.0},
  "bidirectional": true,
  "symmetricDesign": true,
  "efficiency": 0.96,
  "qualityFactor": 0.3,
  "minSwitchingFrequency": 80000,
  "maxSwitchingFrequency": 140000,
  "operatingPoints": [
    {
      "outputVoltages": [48.0],
      "outputCurrents": [10.0],
      "switchingFrequency": 100000,
      "ambientTemperature": 25.0
    }
  ]
}
```

Advanced mode adds: `desiredTurnsRatios`, `desiredMagnetizingInductance`, `desiredResonantInductancePrimary`, `desiredResonantCapacitancePrimary`, `desiredResonantInductanceSecondary`, `desiredResonantCapacitanceSecondary`.

Returns `CllcResonantParameters` struct with: `turnsRatio`, `resonantFrequency`, `primaryResonantInductance` (L1), `primaryResonantCapacitance` (C1), `magnetizingInductance` (Lm), `secondaryResonantInductance` (L2), `secondaryResonantCapacitance` (C2), `qualityFactor`, `inductanceRatio` (k = Lm/L1), `equivalentAcResistance`, `resonantInductorRatio` (a), `resonantCapacitorRatio` (b).

### PFC (Power Factor Correction)

```json
{
  "inputVoltage": {"nominal": 230.0, "minimum": 85.0, "maximum": 265.0},
  "outputVoltage": 400.0,
  "outputPower": 300.0,
  "lineFrequency": 50.0,
  "switchingFrequency": 65000,
  "currentRippleRatio": 0.3,
  "efficiency": 0.95,
  "mode": "Continuous Conduction Mode",
  "diodeVoltageDrop": 0.6,
  "ambientTemperature": 25.0
}
```

Mode options: `"Continuous Conduction Mode"`, `"Discontinuous Conduction Mode"`, `"Critical Conduction Mode"`.

Helper methods: `calculate_inductance_ccm()`, `calculate_inductance_dcm()`, `calculate_inductance_crcm()`, `determine_actual_mode(inductance)`.

### Current Transformer

```json
{
  "primaryCurrent": {"rms": 10.0, "peak": 14.14},
  "frequency": 50.0,
  "burdenResistance": 10.0,
  "accuracy": 0.5
}
```

Process method: `process(turnsRatio, secondaryDcResistance)` or `process(magnetic)`.

---

## CoreAdviser

Recommends optimal magnetic cores from a database using multi-criteria scoring.

### Key API

```cpp
CoreAdviser adviser;                           // Default models
CoreAdviser adviser(models);                   // Custom reluctance/loss models

// Main entry points
auto results = adviser.get_advised_core(inputs, maxResults);
auto results = adviser.get_advised_core(inputs, weights, maxResults);
auto results = adviser.get_advised_core(inputs, &cores, maxResults);   // Custom core list
auto results = adviser.get_advised_core(inputs, &shapes, maxResults);  // Custom shapes
```

### Filter Weights

```cpp
std::map<CoreAdviserFilters, double> weights = {
    {CoreAdviserFilters::COST,       0.3},   // Lower cost = better
    {CoreAdviserFilters::EFFICIENCY, 0.5},   // Lower losses = better
    {CoreAdviserFilters::DIMENSIONS, 0.2}    // Smaller size = better
};
```

### Operating Modes

| Mode | Description |
|---|---|
| `AVAILABLE_CORES` | Uses manufacturer stock database with existing gapping |
| `STANDARD_CORES` | Uses standard core shapes, calculates optimal gap |
| `CUSTOM_CORES` | User-provided custom core shapes (not yet implemented) |

### Filter Pipeline (Power Application)

1. **AreaProduct** (pass/fail) -- eliminates cores too small for energy storage
2. **EnergyStored** (pass/fail) -- checks L*I^2/2 vs. saturation
3. **Cost** (scored, log normalization, inverted)
4. **Dimensions** (scored, linear normalization, inverted)
5. **Losses** (scored, log normalization, inverted)

### Filter Pipeline (Suppression Application)

1. **MinimumImpedance** (pass/fail at operating frequency)
2. **Cost** (scored)
3. **Dimensions** (scored)
4. **MagneticInductance** (scored)
5. **Losses** (scored)

### Gap Optimization (STANDARD_CORES Mode)

```cpp
GappingConstraints constraints = adviser.calculate_gapping_constraints(inputs, core);
// constraints.minGap  -- minimum for energy storage
// constraints.maxGap  -- limited by 30% column width or 25% fringing factor
// constraints.optimalGap -- minimizes core losses (golden-section search)
```

### Return Type

Returns `std::vector<std::pair<Mas, double>>` -- vector of (complete magnetic assembly, score) pairs sorted by descending score. The `Mas` object contains the `Magnetic` component plus `Inputs` and `Outputs`.

---

## MagneticAdviser

Top-level orchestrator that combines CoreAdviser + CoilAdviser + simulation into a complete design flow.

### Key API

```cpp
MagneticAdviser adviser;
adviser.set_core_mode(CoreAdviser::CoreAdviserModes::STANDARD_CORES);  // default

// From Inputs (design requirements + operating points)
auto results = adviser.get_advised_magnetic(inputs, maxResults);
auto results = adviser.get_advised_magnetic(inputs, weights, maxResults);

// Directly from a converter topology (recommended path)
AdvancedFlyback flyback(jsonData);
auto results = adviser.get_advised_magnetic_from_converter(flyback, maxResults);
auto results = adviser.get_advised_magnetic_from_converter(flyback, weights, maxResults);
```

### Default Scoring Filters

| Filter | Invert | Log | Default Weight |
|---|---|---|---|
| `COST` | yes | yes | 1.0 |
| `LOSSES` | yes | yes | 1.0 |
| `DIMENSIONS` | yes | no (linear) | 1.0 |

### Weight Guidelines

| Application | COST | LOSSES | DIMENSIONS |
|---|---|---|---|
| Consumer electronics | 2.0 | 0.5 | 1.5 |
| High-efficiency PSU | 0.5 | 2.0 | 1.0 |
| Space-constrained | 0.5 | 1.0 | 2.0 |
| Balanced | 1.0 | 1.0 | 1.0 |

### get_advised_magnetic_from_converter() Flow

This template method works with any converter type and automates the full pipeline:

1. `converter.process_design_requirements()` -- calculates turns ratios, inductance
2. Extracts turnsRatios and magnetizingInductance from DesignRequirements
3. `converter.simulate_and_extract_operating_points(turnsRatios, inductance)` -- runs ngspice
4. Builds `Inputs` from DesignRequirements + simulated OperatingPoints
5. `inputs.process()` -- computes harmonics and derived data
6. `get_advised_magnetic(inputs, maxResults)` -- runs CoreAdviser + CoilAdviser

### Return Type

Returns `std::vector<std::pair<Mas, double>>` -- scored and ranked complete magnetic designs.

---

## CoilAdviser

Designs complete coil configurations: winding pattern, wire selection, insulation.

### Key API

```cpp
CoilAdviser coilAdviser;
coilAdviser.set_maximum_effective_current_density(5e6);  // 5 A/mm^2
coilAdviser.set_allow_margin_tape(true);
coilAdviser.set_allow_insulated_wire(true);
coilAdviser.set_maximum_number_parallels(4);

auto results = coilAdviser.get_advised_coil(mas, maxResults);
auto results = coilAdviser.get_advised_coil(&wires, mas, maxResults);  // custom wire list
```

### Default Scoring Filters

| Filter | Purpose |
|---|---|
| `EFFECTIVE_RESISTANCE` | AC winding resistance (lower = better) |
| `EFFECTIVE_CURRENT_DENSITY` | Current density (strictly required, pass/fail) |
| `MAGNETOMOTIVE_FORCE` | MMF distribution quality (lower = better) |

### Design Process

1. Calculate winding window proportions based on average power per winding
2. Generate candidate winding patterns (e.g., `{0,1}`, `{1,0}` for 2-winding)
3. For each pattern, determine insulation requirements (IEC 60664-1 / IEC 61558)
4. Select optimal wires via WireAdviser
5. Score complete coil configurations and return ranked results

### Winding Pattern Control

- Non-interleaved: `pattern={0,1}`, `repetitions=1`
- Interleaved: `pattern={0,1}`, `repetitions=2` (gives P-S-P-S)

---

## WireAdviser

Selects optimal wire type and parallel configuration per winding.

### Key API

```cpp
WireAdviser wireAdviser;
wireAdviser.set_maximum_effective_current_density(5e6);
wireAdviser.set_maximum_number_parallels(4);

auto results = wireAdviser.get_advised_wire(winding, section, current, temperature, numSections, maxResults);
auto results = wireAdviser.get_advised_wire(&wires, winding, section, current, temperature, numSections, maxResults);

// For planar (PCB) windings
auto results = wireAdviser.get_advised_planar_wire(winding, section, current, temperature, numSections, maxResults);
```

### Wire Types Supported

Round, litz, foil, rectangular, planar (PCB traces). Controlled via global settings:
- `settings.set_wire_adviser_include_round(bool)`
- `settings.set_wire_adviser_include_litz(bool)`
- `settings.set_wire_adviser_include_foil(bool)`
- `settings.set_wire_adviser_include_rectangular(bool)`
- `settings.set_wire_adviser_include_planar(bool)`

### Filter Pipeline

1. **Area (no parallels)** -- eliminates wires too large for section
2. **Solid insulation requirements** -- validates insulation grade/layers
3. **Area (with parallels)** -- validates wire fits with parallel config
4. **Effective resistance** -- scores by AC resistance (skin effect)
5. **Skin losses density** -- scores by skin effect power density
6. **Proximity factor** -- scores by proximity effect susceptibility

---

## How Converter Models Feed Into Magnetic Design

The complete flow from converter specification to optimized magnetic component:

```
[Converter JSON] --> Topology.from_json()
        |
        v
Topology.run_checks()             // Validate inputs
        |
        v
Topology.process_design_requirements()   // Calculate:
        |                                 //   - Turns ratios
        |                                 //   - Magnetizing inductance
        |                                 //   - Winding isolation sides
        v
Topology.process_operating_points()      // Calculate winding waveforms:
        |                                 //   - Voltage waveforms per winding
        |                                 //   - Current waveforms per winding
        |                                 //   - At each (Vin, operating point) combo
        v
Inputs = {DesignRequirements, OperatingPoints}
        |
        v
MagneticAdviser.get_advised_magnetic(inputs)
        |
        +---> CoreAdviser.get_advised_core()      // Select + gap cores
        |         |
        |         +---> Filter: AreaProduct, EnergyStored, Cost, Dimensions, Losses
        |         |
        |         v
        |     Ranked cores (with gaps for STANDARD_CORES mode)
        |
        +---> CoilAdviser.get_advised_coil()      // Design windings
        |         |
        |         +---> WireAdviser.get_advised_wire()  // Select wire per winding
        |         |
        |         v
        |     Complete coil designs
        |
        +---> MagneticSimulator.simulate()        // Full electromagnetic simulation
        |
        v
    Ranked Mas objects (Magnetic + Inputs + Outputs + score)
```

### Shortcut: get_advised_magnetic_from_converter()

For the simplest usage, `MagneticAdviser::get_advised_magnetic_from_converter()` wraps the entire flow. It accepts any converter type and handles simulation automatically:

```cpp
// Standard Flyback -- library calculates optimal inductance and turns ratio
Flyback flyback(flybackJson);
MagneticAdviser adviser;
auto results = adviser.get_advised_magnetic_from_converter(flyback, 5);

// Advanced Flyback -- user specifies inductance and turns ratio
AdvancedFlyback advFlyback(advancedJson);
auto results = adviser.get_advised_magnetic_from_converter(advFlyback, 5);

// Non-isolated Buck -- single winding, no turns ratios
Buck buck(buckJson);
auto results = adviser.get_advised_magnetic_from_converter(buck, 5);
```

### Standard vs. Advanced Mode

Each topology has two modes:
- **Standard** (e.g., `Flyback`, `Buck`): The library calculates the optimal inductance and turns ratios from the converter specification (ripple ratio, efficiency, etc.)
- **Advanced** (e.g., `AdvancedFlyback`, `AdvancedBuck`): The user directly specifies `desiredInductance`, `desiredTurnsRatios`, etc. The library skips the design calculation and uses the user's values

### ngspice Simulation Integration

Every topology provides:
- `generate_ngspice_circuit(...)` -- creates a SPICE netlist
- `simulate_and_extract_operating_points(...)` -- runs ngspice and extracts winding-level `OperatingPoint` objects
- `simulate_and_extract_topology_waveforms(...)` -- runs ngspice and extracts converter-level `ConverterWaveforms` (input/output voltages and currents)

The simulation path produces more accurate waveforms than the analytical path, especially for resonant topologies (LLC, CLLC, DAB) where analytical approximations can diverge from reality.

### ConverterWaveforms vs. OperatingPoint

- **ConverterWaveforms**: Converter-level signals (Vin, Iin, Vout, Iout) -- for validating converter behavior
- **OperatingPoint**: Winding-level signals (voltage and current per winding) -- for magnetic component design (core losses, winding losses, saturation checks)
