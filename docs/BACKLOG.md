# Heaviside Backlog

Living, ordered list of next work. **Top of each section is highest priority.**
Items are crossed off in commits, not here — when something is done it just gets
removed. New items get inserted in priority order.

See `AGENTS.md` "Autonomy" section: an agent should pick the top unblocked item
from `Now`, do it, commit, and move on without checking in.

## Now (do next, in order)

1. **Stencils for the next batch of topologies.**
   Done so far (with stencil + golden + registry + drift-test row):
   `push_pull`, `phase_shifted_full_bridge`, `phase_shifted_half_bridge`,
   `asymmetric_half_bridge`, `weinberg`, `dual_active_bridge`, `llc`
   (LLC has been stenciled since the original push; just now wired
   `capacitor_binding={"C_r": "resonantCapacitor"}` — first real consumer
   of item 3's cap-attach plumbing). SRC blocked on MKF behavioural-
   bridge limitation; CLLC blocked because MKF `generate_ngspice_circuit`
   doesn't know the topology (see Upstream bugs for both).

   Remaining un-stenciled converter topologies (per registry, 24 total):
   `cllc` (BLOCKED, see above), `clllc`, `series_resonant` (BLOCKED),
   `power_factor_correction`, `vienna`. **CLLLC** is the natural next
   target — it's the largest of the resonant family (2 bridges × 4
   switches + 2 resonant tanks + main transformer = ~16 real components,
   similar scale to DAB but with the additional `capacitor_binding={
   "Cr1": "Cr1_HV_resonantCapacitor", "Cr2": "Cr2_LV_resonantCapacitor"}`
   plus matching `magnetic_binding` for Lr1/Lr2 — see
   `docs/extras-probe.json`). PFC / vienna are non-isolated and don't
   exercise cap-binding.
   Each remaining stencil needs:
   - a stencil function in `heaviside/decomposer/stencils.py`
   - a golden `.spice` + `.tas.json` under `tests/regression/decomposer/golden/`
     (bridge topologies → TAS-only goldens; see `weinberg` / `dab` for the
     pattern)
   - a `magnetic_binding` entry in the registry (extras roles in
     `docs/extras-probe.json`)
   - a `capacitor_binding` entry for CLLLC
   - a row in the drift test prefix map

2. **End-to-end `heaviside design` CLI command.** ✅ Done. `heaviside design
   TOPOLOGY --spec FILE [--turns ...] [--lm ...] [--bridge-mode auto|switch|
   pulse] [--no-attach] [--out FILE] [--compact]`. Auto-detects switch-mode
   for bridge families, lazy-imports PyOM so `version`/`topologies` stay
   instant, accepts PyOM aliases (`dab` → `dual_active_bridge`), distinct
   exit codes (2 spec/arg, 3 decompose, 4 attach, 5 other PyOM). Smoke-tested
   end-to-end on buck (48→12 V / 5 A); 12 unit tests in
   `tests/unit/test_cli.py`. Note: `attach_components_to_tas` is magnetics-
   only by design; FET / diode / cap attach is a future bridge feature.

3. **Resonant cap binding for LLC / SRC / CLLC / CLLLC.** ✅ Bridge
   plumbing done. `TopologyEntry.capacitor_binding: dict[str, str]` maps
   TAS cap component name → PyOM extras-cap role name;
   `attach_components_to_tas` walks `_tas_capacitor_components`, stamps
   `cas_inputs` onto each bound cap, and raises loudly on stencil/registry
   drift. Buck-class topologies (empty `capacitor_binding`) are untouched —
   their output caps stay placeholders for the librarian agent. 5 unit
   tests in `tests/unit/test_bridge.py` against a synthetic CLLC-shaped
   entry. **Remaining**: the LLC / CLLC / CLLLC *stencils* themselves
   (none exist yet — blocked on the same MKF behavioural-bridge issue as
   SRC for LLC, see Upstream bugs) plus filling in `capacitor_binding` on
   the registry once those stencils land. Bridge no longer blocks
   librarian agent kickoff.

## Upstream bugs (track, can't fix from Heaviside)

- **PyOpenMagnetics segfault in `design_magnetics_from_converter('llc', ...)`**
  — reproduces standalone at 48V and 400V, all spec variants tried. The LLC
  integration test in `tests/integration/test_bridge_integration.py` uses a
  subprocess canary and skips with a visible reason; canary will auto-enable
  the test the moment upstream is fixed.

- **MKF SRC (`series_resonant`) does not honour `bridge_simulation_mode="switch"`**
  — always emits a single behavioural `Vbridge` PULSE source instead of real
  SHI/SLO (or SA-SD) switches, unlike LLC / DAB / PSFB / AHB. Blocks the
  SRC stencil because there are no MOSFET refdeses in the deck to anchor
  Q1/Q2 against. Options: (a) fix upstream MKF to emit switches like LLC
  does, (b) write a deck-augmenting stencil that synthesises Q1-Q4 in TAS
  not anchored to spice refdeses — this would be a new pattern affecting
  any future behavioural-bridge topology. Deferred pending decision.

- **MKF `generate_ngspice_circuit` does not know `cllc`** — returns
  `"unknown topology 'cllc'"` for all variants tried (`cllc`, `CLLC`,
  `cllcConverter`, `CLLCConverter`). CLLC is registered in
  `get_extra_components_inputs` (extras: `Cr1_resonantCapacitor_primary`
  + `Cr2_resonantCapacitor_secondary`) but the netlist generator has no
  dispatch path. Blocks the CLLC stencil entirely — there is no deck to
  decompose. CLLLC is fine and emits real switches; LLC also works for
  decompose-only (its `design_magnetics_from_converter` segfault is
  Phase B only).

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
- Capacitor extras (`ExtraCapacitorSpec`) now have a TAS-side attach
  path via `TopologyEntry.capacitor_binding` + `cas_inputs` on the TAS
  component (item 3, done). Still need a stencil to consume it (CLLC).
- `bridge.design_converter_components` REAL mode requires the Magnetic
  JSON (`design.magnetic`), not the MAS envelope (`design.mas`). This
  is locked into the bridge but easy to regress — see the test in
  `tests/unit/test_bridge.py` that pins it.
