# Heaviside Corpus Run

Topologies attempted: **21**
- PASS: **1**
- FAIL (realism rejected at least one check): **4**
- INCOMPLETE (every applicable check UNAVAILABLE): **0**
- CRASH (pipeline died before verdict): **16**
- NO_SPEC (no regression fixture): **0**

## Per-topology

| Topology | Verdict | pass | fail | unavail | n/a | Failing checks / error |
|---|---|---:|---:|---:|---:|---|
| `active_clamp_forward` | CRASH | 0 | 0 | 0 | 0 | error: decompose failed: PyOpenMagnetics rejected 'active_clamp_forward': generate_ngspice_circuit: No SpiceSimulationConfig registered for topology ACTIVE_CLAMP_FORWARD_CONVERTER. Add an entry in Topology.cpp::spice_simulation_defaults(). |
| `asymmetric_half_bridge` | CRASH | 0 | 0 | 0 | 0 | error: decompose failed: PyOpenMagnetics rejected 'asymmetric_half_bridge': generate_ngspice_circuit: No SpiceSimulationConfig registered for topology ASYMMETRIC_HALF_BRIDGE_CONVERTER. Add an entry in Topology.cpp::spice_simulation_defaults(). |
| `boost` | CRASH | 0 | 0 | 0 | 0 | timeout after 300s |
| `cllc` | CRASH | 0 | 0 | 0 | 0 | error: decompose failed: PyOpenMagnetics rejected 'cllc': generate_ngspice_circuit: No SpiceSimulationConfig registered for topology CLLC_RESONANT_CONVERTER. Add an entry in Topology.cpp::spice_simulation_defaults(). |
| `clllc` | CRASH | 0 | 0 | 0 | 0 | error: spec is missing or invalid for topology 'clllc': |
| `dual_active_bridge` | CRASH | 0 | 0 | 0 | 0 | error: spec is missing or invalid for topology 'dual_active_bridge': |
| `flyback` | CRASH | 0 | 0 | 0 | 0 | error: spec is missing or invalid for topology 'flyback': |
| `isolated_buck` | CRASH | 0 | 0 | 0 | 0 | Fatal Python error: Segmentation fault |
| `isolated_buck_boost` | CRASH | 0 | 0 | 0 | 0 | error: decompose failed: PyOpenMagnetics rejected 'isolated_buck_boost': generate_ngspice_circuit: No SpiceSimulationConfig registered for topology ISOLATED_BUCK_BOOST_CONVERTER. Add an entry in Topology.cpp::spice_simulation_defaults(). |
| `llc` | CRASH | 0 | 0 | 0 | 0 | Fatal Python error: Segmentation fault |
| `phase_shifted_full_bridge` | CRASH | 0 | 0 | 0 | 0 | error: bridge design failed: PyOpenMagnetics rejected topology 'phase_shifted_full_bridge': Exception: [json.exception.out_of_range.403] key 'desiredTurnsRatios' not found |
| `push_pull` | CRASH | 0 | 0 | 0 | 0 | Fatal Python error: Segmentation fault |
| `single_switch_forward` | CRASH | 0 | 0 | 0 | 0 | error: decompose failed: PyOpenMagnetics rejected 'single_switch_forward': generate_ngspice_circuit: No SpiceSimulationConfig registered for topology SINGLE_SWITCH_FORWARD_CONVERTER. Add an entry in Topology.cpp::spice_simulation_defaults(). |
| `two_switch_forward` | CRASH | 0 | 0 | 0 | 0 | error: decompose failed: PyOpenMagnetics rejected 'two_switch_forward': generate_ngspice_circuit: No SpiceSimulationConfig registered for topology TWO_SWITCH_FORWARD_CONVERTER. Add an entry in Topology.cpp::spice_simulation_defaults(). |
| `vienna` | CRASH | 0 | 0 | 0 | 0 | error: bridge design failed: get_extra_components_inputs('vienna') failed: get_extra_components_inputs: get_extra_components_inputs: topology 'vienna' has no extra-components dispatch (or hasn't been wired in PyMKF). |
| `weinberg` | CRASH | 0 | 0 | 0 | 0 | error: realism enrichment failed: weinberg T1 MAS: no winding named 'pri_a' (have: ['Primary', 'Secondary']) |
| `buck` | FAIL | 9 | 1 | 0 | 0 | inductor_isat_margin |
| `cuk` | FAIL | 9 | 1 | 0 | 0 | inductor_isat_margin |
| `sepic` | FAIL | 3 | 2 | 5 | 0 | efficiency_sanity, inductor_isat_margin |
| `zeta` | FAIL | 3 | 2 | 5 | 0 | efficiency_sanity, inductor_isat_margin |
| `four_switch_buck_boost` | PASS | 4 | 0 | 4 | 2 |  |

