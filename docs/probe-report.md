# PyOpenMagnetics Topology Probe Report

Generated: 2026-05-18

## Summary

- Converters in registry: **24**
- Bound in PyOpenMagnetics: **24**
- Unbound (upstream work needed): **0**
- Magnetic-only (skipped, designed via different API): **3**

## Per-topology results

| Topology | Family | Status | Variant accepted | First error |
|----------|--------|--------|------------------|-------------|
| `buck` | non_isolated | **BOUND_NEEDS_INPUT** | `buck` | Exception: [json.exception.type_error.302] type must be number, but is object |
| `boost` | non_isolated | **BOUND_NEEDS_INPUT** | `boost` | Exception: [json.exception.type_error.302] type must be number, but is object |
| `cuk` | non_isolated | **BOUND_NEEDS_INPUT** | `cuk` | Exception: [json.exception.type_error.302] type must be number, but is object |
| `sepic` | non_isolated | **BOUND_NEEDS_INPUT** | `sepic` | Exception: [json.exception.type_error.302] type must be number, but is object |
| `zeta` | non_isolated | **BOUND_NEEDS_INPUT** | `zeta` | Exception: [json.exception.type_error.302] type must be number, but is object |
| `four_switch_buck_boost` | non_isolated | **BOUND_NEEDS_INPUT** | `four_switch_buck_boost` | Exception: [json.exception.type_error.302] type must be number, but is object |
| `isolated_buck` | isolated_single_switch | **BOUND_NEEDS_INPUT** | `isolated_buck` | Exception: [json.exception.type_error.302] type must be number, but is object |
| `isolated_buck_boost` | isolated_single_switch | **BOUND_NEEDS_INPUT** | `isolated_buck_boost` | Exception: [json.exception.type_error.302] type must be number, but is object |
| `flyback` | isolated_single_switch | **BOUND_NEEDS_INPUT** | `flyback` | Exception: [json.exception.out_of_range.403] key 'desiredInductance' not found |
| `single_switch_forward` | isolated_single_switch | **BOUND_NEEDS_INPUT** | `single_switch_forward` | Exception: [json.exception.type_error.302] type must be number, but is object |
| `two_switch_forward` | isolated_two_switch | **BOUND_NEEDS_INPUT** | `two_switch_forward` | Exception: [json.exception.type_error.302] type must be number, but is object |
| `active_clamp_forward` | isolated_two_switch | **BOUND_NEEDS_INPUT** | `active_clamp_forward` | Exception: [json.exception.type_error.302] type must be number, but is object |
| `push_pull` | isolated_push_pull | **BOUND_NEEDS_INPUT** | `push_pull` | Exception: [json.exception.type_error.302] type must be number, but is object |
| `asymmetric_half_bridge` | isolated_bridge | **BOUND_NEEDS_INPUT** | `asymmetric_half_bridge` | Exception: [json.exception.type_error.302] type must be number, but is object |
| `phase_shifted_full_bridge` | isolated_bridge | **BOUND_NEEDS_INPUT** | `phase_shifted_full_bridge` | Exception: [json.exception.type_error.302] type must be number, but is object |
| `phase_shifted_half_bridge` | isolated_bridge | **BOUND_NEEDS_INPUT** | `phase_shifted_half_bridge` | Exception: [json.exception.type_error.302] type must be number, but is object |
| `weinberg` | isolated_bridge | **BOUND_NEEDS_INPUT** | `weinberg` | Exception: [json.exception.type_error.302] type must be number, but is object |
| `llc` | resonant | **BOUND_NEEDS_INPUT** | `llc` | Exception: [json.exception.out_of_range.403] key 'minSwitchingFrequency' not fou |
| `cllc` | resonant | **BOUND_NEEDS_INPUT** | `cllc` | Exception: [json.exception.out_of_range.403] key 'minSwitchingFrequency' not fou |
| `clllc` | resonant | **BOUND_NEEDS_INPUT** | `clllc` | Exception: [json.exception.out_of_range.403] key 'highVoltageBusVoltage' not fou |
| `series_resonant` | resonant | **BOUND_NEEDS_INPUT** | `src` | Exception: [json.exception.out_of_range.403] key 'minSwitchingFrequency' not fou |
| `dual_active_bridge` | resonant | **BOUND_NEEDS_INPUT** | `dab` | Exception: [json.exception.type_error.302] type must be number, but is object |
| `power_factor_correction` | ac_dc | **BOUND_NEEDS_INPUT** | `power_factor_correction` | Exception: PowerFactorCorrection: 'outputVoltage' is required |
| `vienna` | ac_dc | **BOUND_NEEDS_INPUT** | `vienna` | Exception: [json.exception.out_of_range.403] key 'lineToLineVoltage' not found |
| `common_mode_choke` | filter_magnetic | **MAGNETIC_SKIPPED** | `—` |  |
| `differential_mode_choke` | filter_magnetic | **MAGNETIC_SKIPPED** | `—` |  |
| `current_transformer` | sense_magnetic | **MAGNETIC_SKIPPED** | `—` |  |
