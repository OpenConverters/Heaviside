# PyOpenMagnetics — Extras Components Probe

Generated: 2026-05-19

## Summary

- Converters probed: **24**
- OK: **13**
- Failed / unbound: **11**

## Per-topology extras

| Topology | Status | Variant | Rounds | Fields added | Extras |
|----------|--------|---------|--------|--------------|--------|
| `buck` | **OK** | `buck` | 1 | — | — |
| `boost` | **PROBE_FAILED** | `boost` | 1 | — | _get_extra_components_inputs: Waveform data contains NaN_ |
| `cuk` | **OK** | `cuk` | 1 | — | magnetic:outputInductor, capacitor:couplingCapacitor, capacitor:outputCapacitor |
| `sepic` | **OK** | `sepic` | 1 | — | magnetic:outputInductor, capacitor:couplingCapacitor, capacitor:outputCapacitor |
| `zeta` | **OK** | `zeta` | 1 | — | magnetic:outputInductor, capacitor:couplingCapacitor, capacitor:outputCapacitor |
| `four_switch_buck_boost` | **OK** | `four_switch_buck_boost` | 1 | — | magnetic:inductor, capacitor:inputCapacitor, capacitor:outputCapacitor |
| `isolated_buck` | **PROBE_FAILED** | `isolated_buck` | 1 | — | _get_extra_components_inputs: [INVALID_DESIGN_REQUIREMENTS] IsolatedBuck requires_ |
| `isolated_buck_boost` | **PROBE_FAILED** | `isolated_buck_boost` | 1 | — | _get_extra_components_inputs: [INVALID_DESIGN_REQUIREMENTS] IsolatedBuckBoost req_ |
| `flyback` | **PROBE_FAILED** | `flyback` | 1 | — | _get_extra_components_inputs: [INVALID_INPUT] Required dutyCycle 0.450102 exceeds_ |
| `single_switch_forward` | **OK** | `single_switch_forward` | 1 | — | magnetic:outputInductor |
| `two_switch_forward` | **OK** | `two_switch_forward` | 1 | — | magnetic:outputInductor |
| `active_clamp_forward` | **OK** | `active_clamp_forward` | 1 | — | magnetic:outputInductor, capacitor:clampCapacitor |
| `push_pull` | **OK** | `push_pull` | 1 | — | magnetic:outputInductor |
| `asymmetric_half_bridge` | **PROBE_FAILED** | `asymmetric_half_bridge` | 2 | dutyCycle | _get_extra_components_inputs: [json.exception.out_of_range.403] key 'dutyCycle' n_ |
| `phase_shifted_full_bridge` | **PROBE_FAILED** | `phase_shifted_full_bridge` | 2 | phaseShift | _get_extra_components_inputs: [json.exception.out_of_range.403] key 'phaseShift' _ |
| `phase_shifted_half_bridge` | **PROBE_FAILED** | `phase_shifted_half_bridge` | 2 | phaseShift | _get_extra_components_inputs: [json.exception.out_of_range.403] key 'phaseShift' _ |
| `weinberg` | **OK** | `weinberg` | 1 | — | magnetic:inputCoupledInductor, capacitor:outputCapacitor |
| `llc` | **OK** | `llc` | 1 | — | capacitor:resonantCapacitor, magnetic:seriesInductor |
| `cllc` | **PROBE_FAILED** | `cllc` | 1 | — | _get_extra_components_inputs: [json.exception.out_of_range.403] key 'powerFlow' n_ |
| `clllc` | **PROBE_FAILED** | `clllc` | 1 | — | _get_extra_components_inputs: [json.exception.out_of_range.403] key 'highVoltageB_ |
| `series_resonant` | **OK** | `src` | 1 | — | capacitor:resonantCapacitor, magnetic:seriesInductor |
| `dual_active_bridge` | **OK** | `dab` | 1 | — | magnetic:seriesInductor |
| `power_factor_correction` | **PROBE_FAILED** | `power_factor_correction` | 1 | — | _get_extra_components_inputs: get_extra_components_inputs: topology 'power_factor_ |
| `vienna` | **PROBE_FAILED** | `vienna` | 1 | — | _get_extra_components_inputs: get_extra_components_inputs: topology 'vienna' has _ |

## Failures

- `boost` (PROBE_FAILED): get_extra_components_inputs: Waveform data contains NaN
- `isolated_buck` (PROBE_FAILED): get_extra_components_inputs: [INVALID_DESIGN_REQUIREMENTS] IsolatedBuck requires at least 2 output voltages (primary + secondary)
- `isolated_buck_boost` (PROBE_FAILED): get_extra_components_inputs: [INVALID_DESIGN_REQUIREMENTS] IsolatedBuckBoost requires at least 2 output voltages (primary + secondary)
- `flyback` (PROBE_FAILED): get_extra_components_inputs: [INVALID_INPUT] Required dutyCycle 0.450102 exceeds maximumDutyCycle 0.450000 at Vin=36.000000V (mode=CCM). Increase magnetizingInductance, lower switchingFrequency, or relax maximumDutyCycle.
- `asymmetric_half_bridge` (PROBE_FAILED): get_extra_components_inputs: [json.exception.out_of_range.403] key 'dutyCycle' not found
- `phase_shifted_full_bridge` (PROBE_FAILED): get_extra_components_inputs: [json.exception.out_of_range.403] key 'phaseShift' not found
- `phase_shifted_half_bridge` (PROBE_FAILED): get_extra_components_inputs: [json.exception.out_of_range.403] key 'phaseShift' not found
- `cllc` (PROBE_FAILED): get_extra_components_inputs: [json.exception.out_of_range.403] key 'powerFlow' not found
- `clllc` (PROBE_FAILED): get_extra_components_inputs: [json.exception.out_of_range.403] key 'highVoltageBusVoltage' not found
- `power_factor_correction` (PROBE_FAILED): get_extra_components_inputs: get_extra_components_inputs: topology 'power_factor_correction' has no extra-components dispatch (or hasn't been wired in PyMKF).
- `vienna` (PROBE_FAILED): get_extra_components_inputs: get_extra_components_inputs: topology 'vienna' has no extra-components dispatch (or hasn't been wired in PyMKF).
