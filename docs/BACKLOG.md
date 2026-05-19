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
   `asymmetric_half_bridge`, `weinberg`, `dual_active_bridge`, `llc`,
   `clllc` (just landed — first multi-cap binding consumer, exercises
   `capacitor_binding={C_r1: Cr1_HV_resonantCapacitor, C_r2:
   Cr2_LV_resonantCapacitor}` and `magnetic_binding` with two extras-
   magnetic roles `Lr1_HV_seriesInductor` + `Lr2_LV_seriesInductor`).

   Remaining un-stenciled converter topologies (per registry, 24 total):
   `cllc` (BLOCKED — MKF unknown topology), `series_resonant`
   (BLOCKED — MKF emits behavioural bridge only), `vienna` (PARTIALLY
   BLOCKED — `process_vienna` errors with `cannot use at() with string`,
   though `generate_ngspice_circuit` knows the topology), and
   `power_factor_correction` (BLOCKED — `generate_ngspice_circuit`
   doesn't know `pfc` / `powerFactorCorrection` / `power_factor_correction`).
   See Upstream bugs below. Effectively the un-blocked stencil work is
   complete until upstream MKF lands fixes.

   Each remaining stencil (when unblocked) needs:
   - a stencil function in `heaviside/decomposer/stencils.py`
   - a golden `.spice` + `.tas.json` under `tests/regression/decomposer/golden/`
     (bridge topologies → TAS-only goldens; see `weinberg` / `dab` /
     `clllc` for the pattern)
   - a `magnetic_binding` entry in the registry (extras roles in
     `docs/extras-probe.json`)
   - a `capacitor_binding` entry for any resonant cap exposed via
     extras-cap (CLLLC was the first multi-cap consumer; SRC / CLLC
     will follow once they decode)
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
   plumbing done and now exercised end-to-end by both LLC (single cap)
   and CLLLC (dual cap — first real multi-cap consumer).
   `TopologyEntry.capacitor_binding: dict[str, str]` maps TAS cap
   component name → PyOM extras-cap role name;
   `attach_components_to_tas` walks `_tas_capacitor_components`, stamps
   `cas_inputs` onto each bound cap, and raises loudly on stencil/registry
   drift. Buck-class topologies (empty `capacitor_binding`) are untouched —
   their output caps stay placeholders for the librarian agent.
   **Remaining**: SRC and CLLC stencils still blocked upstream (see
   Upstream bugs), but the plumbing is ready for them the moment they
   land.

## Upstream bugs (track, can't fix from Heaviside)

**See `docs/mkf-handoff.md`** for a full per-bug writeup (symptom +
repro + root cause + concrete fix sketch + file:line pointers in
`PyOpenMagnetics/src/converter.cpp` and the per-topology MKF sources)
written for the upstream MKF agent. The summaries below are kept here
so the gap stays visible from inside Heaviside's planning loop.

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

- **MKF `generate_ngspice_circuit` does not know
  `power_factor_correction`** — returns `"unknown topology"` for `pfc`,
  `powerFactorCorrection`, and `power_factor_correction`. PFC is
  registered as a converter in PyOM (and `process_inputs` accepts it),
  but the ngspice generator has no dispatch path. Blocks the PFC
  stencil entirely; same shape as the CLLC bug above.

- **MKF `process_vienna` raises `cannot use at() with string`** —
  `generate_ngspice_circuit("vienna", ...)` knows the topology but the
  Vienna processor errors out before producing a deck, regardless of
  spec shape (`lineToLineVoltage` + `outputDcVoltage` set per
  `dump_all_decks.py` recipe). Partially blocks Vienna: PyOM accepts the
  variant string but never returns a netlist. Distinct from the
  PFC/CLLC "unknown topology" path.

## Next (after Now is empty)

- **Agent layer kickoff.** Strands + Kimi default. Start with a single
  `converter-designer` agent that wraps the CLI from item 5 above and one
  reviewer agent (`ray` or `nicola`). Defer the full Proteus-style fleet.
- **MCP server surface.** Same pipeline as the CLI, exposed as MCP tools.
- **FastAPI surface.** Same pipeline, JSON over HTTP.
- **Realism gate** ✅ v0.1 done + buck enrichment landed.
  `heaviside/pipeline/realism.py` ports all 10 Proteus physics primitives
  (power balance, voltage derating ×3, Isat margin, duty cycle bounds,
  no-negative-losses, thermal, efficiency, Vout regulation) with strict
  "throw on bad input" semantics per CLAUDE.md.  Orchestrator
  `evaluate_tas(tas, *, topology, spec)` classifies every check as
  PASS / FAIL / NOT_APPLICABLE / UNAVAILABLE — nothing silently skipped.
  Verdict: PASS / FAIL / INCOMPLETE.  CLI `heaviside design ... --realism`
  is opt-in and exits 6 on FAIL or INCOMPLETE (fail-closed, no `--force`).
  `heaviside/pipeline/extract.py` adds a topology-aware enrichment step
  that stamps derived stresses onto the TAS before the gate runs.  Buck
  extractor computes `D = Vout/Vin`, `Ipeak_worst = Iout + Vout·(1−D_min)
  /(0.8·L·fsw)/2` (Vin_max + −20% inductance tolerance per PROTEUS.md
  rules), and `Isat = B_sat·N·A_e/L` (B_sat = conservative minimum
  across the MAS saturation curve's temperature samples).  Every
  computed value carries a `*_provenance` dict tracing each input.
  **Real buck now reaches PASS** (duty 0.333, margin 0.28; Isat ratio
  2.07, margin 0.87 — Vin 36-60V, Vout 12V, Iout 5A, 200 kHz, 22 µH).
  Other topologies pass through enrichment unchanged → still honestly
  INCOMPLETE until the librarian / sim / analyst agents enrich them or
  a per-topology extractor is added.  108 unit tests in
  `tests/unit/test_realism.py` + `tests/unit/test_extract.py` + 2 CLI
  tests.  **Boost**, **flyback**, **cuk**, **SEPIC**, **zeta**,
  **single_switch_forward**, and **two_switch_forward** extractors
  landed (`tests/unit/test_extract_boost_flyback.py`,
  `tests/unit/test_extract_cuk_sepic_zeta.py`,
  `tests/unit/test_extract_forward.py`, 79 new tests): boost uses
  `D = 1 − Vin/Vout` with ripple maximised over Vin (closed-form
  interior peak at `Vout/2`), I_L_avg at Vin_min, same `B_sat·N·A_e/L`
  Isat math; flyback uses `D = Vout·n/(Vin + Vout·n)` with `n = N_p/N_s`
  read from MAS, primary-referred `Ipeak = I_in/D + Δi/2` at Vin_min,
  Isat on the magnetising inductance.  Cuk / SEPIC / zeta share a
  single extractor: `D = Vout/(Vin+Vout)`, both inductors see
  `ΔI_L = Vin·D/(L·fsw)` (volt-second balance, monotone increasing
  in Vin), L1 carries `I_in = Pout/(η·Vin)` worst at Vin_min, L2
  carries Iout independent of Vin, each inductor stamped with its
  own Isat from its own MAS; `spec.desiredOutputInductance` consulted
  for L2 (provenance records source).  Single/two-switch forward
  share a stage-role-aware extractor: turns ratio
  `n = N_pri/N_sec0` read from T1 by winding name (handles SSF's
  3-winding vs 2SF's 2-winding shape uniformly), buck-shaped output
  choke `ΔI_L = Vout·(1−D)/(L_out·fsw)` worst at D_min (Vin_max),
  Isat stamped on L_out0 only — T1 is intentionally skipped because
  the demag winding clamps its core every cycle.  `D_max ≥ 0.5`
  throws (reset-window violation).  Active-clamp forward reuses the
  same shared extractor (`_enrich_forward_family(..., enforce_half_duty=False)`)
  since the output-side analytics are identical — the clamp cap +
  auxiliary FET absorb the reset volt-seconds so D may exceed 0.5;
  the realism gate's generic 0.05 < D < 0.95 CCM bound still
  applies and fail-closes any over-ceiling design.  Isolated buck
  (flybuck) extractor stamps T1 itself as the binding magnetic
  (unlike the forward family where T1 is intentionally skipped): the
  primary winding *is* the buck inductor, so D = Vout_pri/Vin,
  ripple worst at Vin_max with L*0.8, Isat = B_sat·N_pri·A_e/L_pri.
  v0.1 explicitly does NOT model reflected secondary load — the
  `secondary_reflected_current_modelled: false` provenance flag
  pins that for regression and a future extension must update the
  flag in the same commit.  Multi-output flybacks throw (not yet
  supported).  **Remaining**: isolated_buck_boost (inverting
  primary — worst-case ripple at Vin_max but avg current at
  Vin_min, same shape as the boost extractor);
  librarian agent populates `vds_rated` / `vrrm_rated` / `v_rated` on TAS
  components for the voltage derating checks; analyst agent computes Tj
  for thermal_limit; sim agent populates `simulation_results` /
  `loss_budget` for the remaining 4 checks.
- **Analytical regression suite** ✅ landed
  (`tests/regression/converters/test_converter_corpus.py`, 51 new
  tests).  CI gate per AGENTS.md rule 7.  Loads every entry in
  `TAS/data/converters.ndjson` (48 today: 34 buck-shaped, 10
  single-switch forward, 3 flyback, 1 intentionally-empty placeholder),
  classifies by component fingerprint, runs `evaluate_tas` and snapshots
  `{verdict, summary, per-check status}` into a committed
  `golden_baseline.json`.  Today's honest verdict is INCOMPLETE on every
  populated entry (no real component data attached → every check
  UNAVAILABLE).  When the librarian agent populates real components, the
  guard test `test_entry_is_evaluable_or_explicitly_empty` will trip and
  the golden must be regenerated via
  `python -m tests.regression.converters.regen_golden` in the same
  reviewed commit.  Unknown component fingerprints fail loudly (no
  silent skip); corpus size pinned to 48 in
  `test_corpus_size_matches_agents_rule_7`.
- **Component-librarian agent port from Proteus.** First real consumer of
  the `kind="capacitor"` `CAS::Inputs` from `extra_components`.
- **Integration-test caching strategy.** End-to-end PyOM runs are 1–2 min
  each; suite will balloon as topologies are added. Cache by
  `(topology, spec_hash, pyom_sha)` → MAS JSON on disk.

## Later (deferred, but tracked so we don't forget)

- **Remaining stencils** (all upstream-blocked, see Upstream bugs):
  `cllc`, `vienna`, `power_factor_correction`, `series_resonant`.
  Each will need the same 4-part bring-up (stencil, golden, binding,
  drift-test row) the moment MKF unblocks it.
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
  component (item 3, done). First multi-cap consumer (CLLLC) has
  landed; CLLC will follow once upstream unblocks the topology.
- `bridge.design_converter_components` REAL mode requires the Magnetic
  JSON (`design.magnetic`), not the MAS envelope (`design.mas`). This
  is locked into the bridge but easy to regress — see the test in
  `tests/unit/test_bridge.py` that pins it.
