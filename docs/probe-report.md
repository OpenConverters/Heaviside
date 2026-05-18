# PyOpenMagnetics Topology Probe Report

Generated: 2026-05-18

## Summary

- Converters in registry: **24**
- Bound in PyOpenMagnetics: **7**
- Unbound (upstream work needed): **17**
- Magnetic-only (skipped, designed via different API): **3**

## Per-topology results

| Topology | Family | Status | Variant accepted | First error |
|----------|--------|--------|------------------|-------------|
| `buck` | non_isolated | **BOUND_NEEDS_INPUT** | `buck` | Exception: [json.exception.type_error.302] type must be number, but is object |
| `boost` | non_isolated | **BOUND_NEEDS_INPUT** | `boost` | Exception: [json.exception.type_error.302] type must be number, but is object |
| `cuk` | non_isolated | **UNBOUND** | `—` | Exception: Unknown topology: Cuk |
| `sepic` | non_isolated | **UNBOUND** | `—` | Exception: Unknown topology: Sepic |
| `zeta` | non_isolated | **UNBOUND** | `—` | Exception: Unknown topology: Zeta |
| `four_switch_buck_boost` | non_isolated | **UNBOUND** | `—` | Exception: Unknown topology: FourSwitchBuckBoost |
| `isolated_buck` | isolated_single_switch | **UNBOUND** | `—` | Exception: Unknown topology: isolatedBuck |
| `isolated_buck_boost` | isolated_single_switch | **UNBOUND** | `—` | Exception: Unknown topology: isolatedBuckBoost |
| `flyback` | isolated_single_switch | **BOUND_NEEDS_INPUT** | `flyback` | Exception: [json.exception.out_of_range.403] key 'desiredInductance' not found |
| `single_switch_forward` | isolated_single_switch | **UNBOUND** | `—` | Exception: Unknown topology: singleSwitchForward |
| `two_switch_forward` | isolated_two_switch | **UNBOUND** | `—` | Exception: Unknown topology: twoSwitchForward |
| `active_clamp_forward` | isolated_two_switch | **UNBOUND** | `—` | Exception: Unknown topology: activeClampForward |
| `push_pull` | isolated_push_pull | **UNBOUND** | `—` | Exception: Unknown topology: pushPull |
| `asymmetric_half_bridge` | isolated_bridge | **UNBOUND** | `—` | Exception: Unknown topology: AsymmetricHalfBridge |
| `phase_shifted_full_bridge` | isolated_bridge | **UNBOUND** | `—` | Exception: Unknown topology: PhaseShiftedFullBridge |
| `phase_shifted_half_bridge` | isolated_bridge | **UNBOUND** | `—` | Exception: Unknown topology: PhaseShiftedHalfBridge |
| `weinberg` | isolated_bridge | **UNBOUND** | `—` | Exception: Unknown topology: Weinberg |
| `llc` | resonant | **BOUND_NEEDS_INPUT** | `llc` | Exception: [json.exception.out_of_range.403] key 'minSwitchingFrequency' not fou |
| `cllc` | resonant | **BOUND_NEEDS_INPUT** | `cllc` | Exception: [json.exception.out_of_range.403] key 'minSwitchingFrequency' not fou |
| `clllc` | resonant | **UNBOUND** | `—` | Exception: Unknown topology: CLLLC |
| `series_resonant` | resonant | **UNBOUND** | `—` | Exception: Unknown topology: SRC |
| `dual_active_bridge` | resonant | **BOUND_NEEDS_INPUT** | `dab` | Exception: [json.exception.type_error.302] type must be number, but is object |
| `power_factor_correction` | ac_dc | **BOUND_NEEDS_INPUT** | `pfc` | Exception: [INVALID_DESIGN_REQUIREMENTS] PFC: minimum RMS input voltage must be  |
| `vienna` | ac_dc | **UNBOUND** | `—` | Exception: Unknown topology: Vienna |
| `common_mode_choke` | filter_magnetic | **MAGNETIC_SKIPPED** | `—` |  |
| `differential_mode_choke` | filter_magnetic | **MAGNETIC_SKIPPED** | `—` |  |
| `current_transformer` | sense_magnetic | **MAGNETIC_SKIPPED** | `—` |  |

## Action: bindings to add in `vendor/PyOpenMagnetics/`

- `cuk` — tried ['cuk', 'Cuk']; engine response: `Exception: Unknown topology: Cuk`
- `sepic` — tried ['sepic', 'Sepic']; engine response: `Exception: Unknown topology: Sepic`
- `zeta` — tried ['zeta', 'Zeta']; engine response: `Exception: Unknown topology: Zeta`
- `four_switch_buck_boost` — tried ['fourSwitchBuckBoost', 'FourSwitchBuckBoost']; engine response: `Exception: Unknown topology: FourSwitchBuckBoost`
- `isolated_buck` — tried ['isolatedBuck']; engine response: `Exception: Unknown topology: isolatedBuck`
- `isolated_buck_boost` — tried ['isolatedBuckBoost']; engine response: `Exception: Unknown topology: isolatedBuckBoost`
- `single_switch_forward` — tried ['singleSwitchForward']; engine response: `Exception: Unknown topology: singleSwitchForward`
- `two_switch_forward` — tried ['twoSwitchForward']; engine response: `Exception: Unknown topology: twoSwitchForward`
- `active_clamp_forward` — tried ['activeClampForward']; engine response: `Exception: Unknown topology: activeClampForward`
- `push_pull` — tried ['pushPull']; engine response: `Exception: Unknown topology: pushPull`
- `asymmetric_half_bridge` — tried ['asymmetricHalfBridge', 'AsymmetricHalfBridge']; engine response: `Exception: Unknown topology: AsymmetricHalfBridge`
- `phase_shifted_full_bridge` — tried ['phaseShiftedFullBridge', 'PhaseShiftedFullBridge']; engine response: `Exception: Unknown topology: PhaseShiftedFullBridge`
- `phase_shifted_half_bridge` — tried ['phaseShiftedHalfBridge', 'PhaseShiftedHalfBridge']; engine response: `Exception: Unknown topology: PhaseShiftedHalfBridge`
- `weinberg` — tried ['weinberg', 'Weinberg']; engine response: `Exception: Unknown topology: Weinberg`
- `clllc` — tried ['clllc', 'clllcResonant', 'CLLLC']; engine response: `Exception: Unknown topology: CLLLC`
- `series_resonant` — tried ['src', 'seriesResonant', 'SRC']; engine response: `Exception: Unknown topology: SRC`
- `vienna` — tried ['vienna', 'Vienna']; engine response: `Exception: Unknown topology: Vienna`
