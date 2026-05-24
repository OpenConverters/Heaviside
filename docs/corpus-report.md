# Heaviside Corpus Run

Topologies attempted: **21**
- PASS: **6**
- FAIL (realism rejected at least one check): **5**
- INCOMPLETE (every applicable check UNAVAILABLE): **1**
- CRASH (pipeline died before verdict): **9**
- NO_SPEC (no regression fixture): **0**

## Per-topology

| Topology | Verdict | pass | fail | unavail | n/a | Failing checks / error |
|---|---|---:|---:|---:|---:|---|
| `clllc` | CRASH | 0 | 0 | 0 | 0 | Fatal Python error: Segmentation fault |
| `dual_active_bridge` | CRASH | 0 | 0 | 0 | 0 | error: decompose failed: PyOpenMagnetics rejected 'dual_active_bridge': generate_ngspice_circuit: unknown topology 'dual_active_bridge' |
| `flyback` | CRASH | 0 | 0 | 0 | 0 | Fatal Python error: Segmentation fault |
| `isolated_buck` | CRASH | 0 | 0 | 0 | 0 | Fatal Python error: Segmentation fault |
| `llc` | CRASH | 0 | 0 | 0 | 0 | error: bridge design failed: PyOpenMagnetics rejected topology 'llc': Exception: bad optional access |
| `phase_shifted_full_bridge` | CRASH | 0 | 0 | 0 | 0 | error: decompose failed: PyOpenMagnetics rejected 'phase_shifted_full_bridge': generate_ngspice_circuit: No SpiceSimulationConfig registered for topology PHASE_SHIFTED_FULL_BRIDGE_CONVERTER. Add an entry in Topology.cpp::spice_simulation_defaults(). |
| `push_pull` | CRASH | 0 | 0 | 0 | 0 | Fatal Python error: Segmentation fault |
| `vienna` | CRASH | 0 | 0 | 0 | 0 | error: bridge design failed: get_extra_components_inputs('vienna') failed: get_extra_components_inputs: get_extra_components_inputs: topology 'vienna' has no extra-components dispatch (or hasn't been wired in PyMKF). |
| `weinberg` | CRASH | 0 | 0 | 0 | 0 | Fatal Python error: Segmentation fault |
| `active_clamp_forward` | FAIL | 2 | 2 | 5 | 1 | efficiency_sanity, inductor_isat_margin |
| `asymmetric_half_bridge` | FAIL | 1 | 1 | 8 | 0 | inductor_isat_margin |
| `isolated_buck_boost` | FAIL | 4 | 1 | 5 | 0 | efficiency_sanity |
| `single_switch_forward` | FAIL | 1 | 1 | 8 | 0 | inductor_isat_margin |
| `two_switch_forward` | FAIL | 3 | 2 | 5 | 0 | efficiency_sanity, inductor_isat_margin |
| `cllc` | INCOMPLETE | 0 | 0 | 9 | 1 |  |
| `boost` | PASS | 10 | 0 | 0 | 0 |  |
| `buck` | PASS | 10 | 0 | 0 | 0 |  |
| `cuk` | PASS | 10 | 0 | 0 | 0 |  |
| `four_switch_buck_boost` | PASS | 4 | 0 | 4 | 2 |  |
| `sepic` | PASS | 5 | 0 | 5 | 0 |  |
| `zeta` | PASS | 5 | 0 | 5 | 0 |  |

