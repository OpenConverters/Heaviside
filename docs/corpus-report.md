# Heaviside Corpus Run

Topologies attempted: **21**
- PASS: **4**
- FAIL (realism rejected at least one check): **2**
- INCOMPLETE (every applicable check UNAVAILABLE): **1**
- CRASH (pipeline died before verdict): **14**
- NO_SPEC (no regression fixture): **0**

## Per-topology

| Topology | Verdict | pass | fail | unavail | n/a | Failing checks / error |
|---|---|---:|---:|---:|---:|---|
| `active_clamp_forward` | CRASH | 0 | 0 | 0 | 0 | exit=1 |
| `asymmetric_half_bridge` | CRASH | 0 | 0 | 0 | 0 | error: decompose failed: PyOpenMagnetics rejected 'asymmetric_half_bridge': generate_ngspice_circuit: AsymmetricHalfBridge::generate_ngspice_circuit: turnsRatios empty |
| `clllc` | CRASH | 0 | 0 | 0 | 0 | error: spec is missing or invalid for topology 'clllc': |
| `dual_active_bridge` | CRASH | 0 | 0 | 0 | 0 | error: spec is missing or invalid for topology 'dual_active_bridge': |
| `flyback` | CRASH | 0 | 0 | 0 | 0 | error: spec is missing or invalid for topology 'flyback': |
| `isolated_buck` | CRASH | 0 | 0 | 0 | 0 | Fatal Python error: Segmentation fault |
| `isolated_buck_boost` | CRASH | 0 | 0 | 0 | 0 | exit=1 |
| `llc` | CRASH | 0 | 0 | 0 | 0 | Fatal Python error: Segmentation fault |
| `phase_shifted_full_bridge` | CRASH | 0 | 0 | 0 | 0 | error: bridge design failed: PyOpenMagnetics rejected topology 'phase_shifted_full_bridge': Exception: [json.exception.out_of_range.403] key 'desiredTurnsRatios' not found |
| `push_pull` | CRASH | 0 | 0 | 0 | 0 | error: bridge design failed: get_extra_components_inputs('push_pull') failed: get_extra_components_inputs: [INVALID_DESIGN_REQUIREMENTS] T1 cannot be larger than period/2, wrong topology configuration |
| `single_switch_forward` | CRASH | 0 | 0 | 0 | 0 | error: decompose failed: PyOpenMagnetics rejected 'single_switch_forward': generate_ngspice_circuit: [INVALID_DESIGN_REQUIREMENTS] SingleSwitchForward: turnsRatios must not be empty |
| `two_switch_forward` | CRASH | 0 | 0 | 0 | 0 | Fatal Python error: Segmentation fault |
| `vienna` | CRASH | 0 | 0 | 0 | 0 | error: bridge design failed: get_extra_components_inputs('vienna') failed: get_extra_components_inputs: get_extra_components_inputs: topology 'vienna' has no extra-components dispatch (or hasn't been wired in PyMKF). |
| `weinberg` | CRASH | 0 | 0 | 0 | 0 | error: realism enrichment failed: weinberg T1 MAS: no winding named 'pri_a' (have: ['Primary', 'Secondary']) |
| `boost` | FAIL | 8 | 1 | 0 | 1 | inductor_isat_margin |
| `buck` | FAIL | 9 | 1 | 0 | 0 | inductor_isat_margin |
| `cllc` | INCOMPLETE | 0 | 0 | 9 | 1 |  |
| `cuk` | PASS | 10 | 0 | 0 | 0 |  |
| `four_switch_buck_boost` | PASS | 4 | 0 | 4 | 2 |  |
| `sepic` | PASS | 5 | 0 | 5 | 0 |  |
| `zeta` | PASS | 5 | 0 | 5 | 0 |  |

