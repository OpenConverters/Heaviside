# Heaviside Backlog

Living, ordered list of next work. **Top of each section is highest priority.**
Items are crossed off in commits, not here — when something is done it just gets
removed. New items get inserted in priority order.

See `AGENTS.md` "Autonomy" section: an agent should pick the top unblocked item
from `Now`, do it, commit, and move on without checking in.

## Now (do next, in order)

1. **Fix the 8 spec-blocked topologies in `scripts/probe_extras.py`.** From
   `docs/extras-probe-report.md`:
   - `boost` (NaN result)
   - `flyback` (duty cycle constraint)
   - `isolated_buck`, `isolated_buck_boost` (need ≥2 output voltages)
   - `asymmetric_half_bridge` (needs nested `dutyCycle`)
   - `phase_shifted_full_bridge`, `phase_shifted_half_bridge` (needs nested
     `phaseShift` — top-level rejected)
   - `cllc` (powerFlow)
   - `clllc` (highVoltageBusVoltage)

   Per-topology spec shapes live in MKF `converter_models/Advanced<Topology>.h`
   headers. Goal: every converter in the probe lands in `OK` or
   `EXPECTED_EMPTY`, exit code 0. Unblocks stencils for these topologies.

2. **Stencils for the next batch of topologies** (in priority order):
   `phase_shifted_full_bridge`, `asymmetric_half_bridge`, `push_pull`,
   `weinberg`, `series_resonant`, `dual_active_bridge`. Each needs:
   - a stencil function in `heaviside/decomposer/stencils.py`
   - a golden `.spice` + `.tas.json` under `tests/regression/decomposer/golden/`
   - a `magnetic_binding` entry in the registry
   - a row in the drift test prefix map

3. **End-to-end `heaviside design` CLI command.** First user-visible surface.
   Pipeline: `DesignSpec` (JSON or flags) → `decompose_from_spec` →
   `design_converter_components` → `attach_components_to_tas` → print/save
   the populated TAS. No agent layer yet — just the bridge driven from argv.

## Upstream bugs (track, can't fix from Heaviside)

- **PyOpenMagnetics segfault in `design_magnetics_from_converter('llc', ...)`**
  — reproduces standalone at 48V and 400V, all spec variants tried. The LLC
  integration test in `tests/integration/test_bridge_integration.py` uses a
  subprocess canary and skips with a visible reason; canary will auto-enable
  the test the moment upstream is fixed.

## Next (after Now is empty)

- **Agent layer kickoff.** Strands + Kimi default. Start with a single
  `converter-designer` agent that wraps the CLI from item 5 above and one
  reviewer agent (`ray` or `nicola`). Defer the full Proteus-style fleet.
- **MCP server surface.** Same pipeline as the CLI, exposed as MCP tools.
- **FastAPI surface.** Same pipeline, JSON over HTTP.
- **Realism gate** (`heaviside/pipeline/realism.py`). Fail-closed; see
  AGENTS.md rule 5. Inputs: populated TAS from the bridge. Outputs:
  pass/fail + per-check report. No `--force`.
- **Analytical regression suite** against the 47 designs in
  `TAS/data/converters.ndjson`. CI gate per AGENTS.md rule 7.
- **Component-librarian agent port from Proteus.** First real consumer of
  the `kind="capacitor"` `CAS::Inputs` from `extra_components`.
- **Integration-test caching strategy.** End-to-end PyOM runs are 1–2 min
  each; suite will balloon as topologies are added. Cache by
  `(topology, spec_hash, pyom_sha)` → MAS JSON on disk.

## Later (deferred, but tracked so we don't forget)

- **Remaining 11 stencils** to reach 24/24: `cllc`, `clllc`, `vienna`,
  `power_factor_correction`, plus whatever survives from the Now/Next
  stencil batch. Each needs the same 4-part bring-up (stencil, golden,
  binding, drift-test row).
- **K-statement recovery in `spice_to_tas`.** MVP collapses to one
  multi-winding magnetic with inline `inductances`+`coupling`; would be
  nice to recover separate windings from K-statement coupling matrices.
- **Role inference in `spice_to_tas`.** Currently emits a single
  `switchingCell` stage; topology-aware role inference (input filter /
  switching cell / rectifier / output filter) deferred.
- **Stencil↔registry coverage matrix** in the README / AGENTS.md
  explicitly listing bridge-ready vs stencil-only vs registry-only.

## Known unknowns / honest assessment

- LLC `L_r → seriesInductor` is an assumption from MKF source, not a
  verified runtime fact. Item 1 in `Now` fixes this.
- Capacitor extras (`ExtraCapacitorSpec`) have no TAS-side attachment
  path yet; waiting on the librarian agent.
- `bridge.design_converter_components` REAL mode requires the Magnetic
  JSON (`design.magnetic`), not the MAS envelope (`design.mas`). This
  is locked into the bridge but easy to regress — see the test in
  `tests/unit/test_bridge.py` that pins it.
