# PyOpenMagnetics — Extras Components Probe

Generated: 2026-05-19

## Summary

- Converters probed: **24**
- OK: **24**
- Failed / unbound: **0**

## Per-topology extras

| Topology | Status | Variant | Rounds | Fields added | Extras |
|----------|--------|---------|--------|--------------|--------|
| `buck` | **OK** | `buck` | 1 | — | — |
| `boost` | **OK** | `boost` | 1 | — | — |
| `cuk` | **OK** | `cuk` | 1 | — | magnetic:outputInductor, capacitor:couplingCapacitor, capacitor:outputCapacitor |
| `sepic` | **OK** | `sepic` | 1 | — | magnetic:outputInductor, capacitor:couplingCapacitor, capacitor:outputCapacitor |
| `zeta` | **OK** | `zeta` | 1 | — | magnetic:outputInductor, capacitor:couplingCapacitor, capacitor:outputCapacitor |
| `four_switch_buck_boost` | **OK** | `four_switch_buck_boost` | 1 | — | magnetic:inductor, capacitor:inputCapacitor, capacitor:outputCapacitor |
| `isolated_buck` | **OK** | `isolated_buck` | 1 | — | — |
| `isolated_buck_boost` | **OK** | `isolated_buck_boost` | 1 | — | — |
| `flyback` | **OK** | `flyback` | 1 | — | — |
| `single_switch_forward` | **OK** | `single_switch_forward` | 1 | — | magnetic:outputInductor |
| `two_switch_forward` | **OK** | `two_switch_forward` | 1 | — | magnetic:outputInductor |
| `active_clamp_forward` | **OK** | `active_clamp_forward` | 1 | — | magnetic:outputInductor, capacitor:clampCapacitor |
| `push_pull` | **OK** | `push_pull` | 1 | — | magnetic:outputInductor |
| `asymmetric_half_bridge` | **OK** | `asymmetric_half_bridge` | 1 | — | magnetic:outputInductor |
| `phase_shifted_full_bridge` | **OK** | `phase_shifted_full_bridge` | 1 | — | magnetic:outputInductor, magnetic:seriesInductor |
| `phase_shifted_half_bridge` | **OK** | `phase_shifted_half_bridge` | 1 | — | magnetic:outputInductor, magnetic:seriesInductor |
| `weinberg` | **OK** | `weinberg` | 1 | — | magnetic:inputCoupledInductor, capacitor:outputCapacitor |
| `llc` | **OK** | `llc` | 1 | — | capacitor:resonantCapacitor, magnetic:seriesInductor |
| `cllc` | **OK** | `cllc` | 1 | — | capacitor:Cr1_resonantCapacitor_primary, capacitor:Cr2_resonantCapacitor_secondary |
| `clllc` | **OK** | `clllc` | 1 | — | capacitor:Cr1_HV_resonantCapacitor, capacitor:Cr2_LV_resonantCapacitor, magnetic:Lr1_HV_seriesInductor, magnetic:Lr2_LV_seriesInductor |
| `series_resonant` | **OK** | `src` | 1 | — | capacitor:resonantCapacitor, magnetic:seriesInductor |
| `dual_active_bridge` | **OK** | `dab` | 1 | — | magnetic:seriesInductor |
| `power_factor_correction` | **OK** | `power_factor_correction` | 1 | — | — |
| `vienna` | **OK** | `vienna` | 1 | — | — |
