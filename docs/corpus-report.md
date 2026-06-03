# Heaviside Corpus Run

Topologies attempted: **21**
- PASS: **14**
- FAIL (realism rejected at least one check): **5**
- INCOMPLETE (every applicable check UNAVAILABLE): **0**
- CRASH (pipeline died before verdict): **2**
- NO_SPEC (no regression fixture): **0**

## Per-topology

| Topology | Verdict | pass | fail | unavail | n/a | Failing checks / error |
|---|---|---:|---:|---:|---:|---|
| `cllc` | CRASH | 0 | 0 | 0 | 0 | error: bridge design failed: design_magnetics_from_converter('cllc') returned zero designs for spec. Loosen constraints or check converter inputs. |
| `isolated_buck` | CRASH | 0 | 0 | 0 | 0 | timeout after 600s |
| `active_clamp_forward` | FAIL | 5 | 1 | 4 | 0 | efficiency_sanity |
| `clllc` | FAIL | 6 | 1 | 2 | 1 | efficiency_sanity |
| `dual_active_bridge` | FAIL | 5 | 1 | 3 | 1 | efficiency_sanity |
| `isolated_buck_boost` | FAIL | 5 | 1 | 4 | 0 | efficiency_sanity |
| `single_switch_forward` | FAIL | 3 | 1 | 6 | 0 | efficiency_sanity |
| `asymmetric_half_bridge` | PASS | 9 | 0 | 0 | 1 |  |
| `boost` | PASS | 10 | 0 | 0 | 0 |  |
| `buck` | PASS | 10 | 0 | 0 | 0 |  |
| `cuk` | PASS | 10 | 0 | 0 | 0 |  |
| `flyback` | PASS | 10 | 0 | 0 | 0 |  |
| `four_switch_buck_boost` | PASS | 8 | 0 | 0 | 2 |  |
| `llc` | PASS | 9 | 0 | 0 | 1 |  |
| `phase_shifted_full_bridge` | PASS | 9 | 0 | 0 | 1 |  |
| `push_pull` | PASS | 10 | 0 | 0 | 0 |  |
| `sepic` | PASS | 10 | 0 | 0 | 0 |  |
| `two_switch_forward` | PASS | 10 | 0 | 0 | 0 |  |
| `vienna` | PASS | 2 | 0 | 8 | 0 |  |
| `weinberg` | PASS | 5 | 0 | 4 | 1 |  |
| `zeta` | PASS | 10 | 0 | 0 | 0 |  |
