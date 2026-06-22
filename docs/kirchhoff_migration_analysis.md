All facts confirmed against source. The `kind="magnetic"` grep returned 0 because the registry uses a different field syntax, but the count (27 entries) and the org mismatch (OpenConverters vs Power-Supply-Manufacturers-Association in `.gitmodules`) are confirmed. I have enough verified grounding. Writing the deliverable now.

# Adopting Kirchhoff for Converter SPICE Generation & Simulation — Migration Analysis

## 1. Executive summary

- **What Kirchhoff is.** A C++17 library (`/home/alf/OpenConverters/Kirchhoff`) that runs a three-step pipeline per topology: `design_<topo>(spec) → <Topo>Design` → `build_<topo>_tas(design) → TAS document (JSON)` → `tas_to_ngspice(tas, fidelity) → runnable ngspice deck`. The third step is a **genuinely generic** walker (`src/TasAssembler.cpp:149`) that emits a deck for *any* TAS document — it is not flyback-specialized, contrary to README P3's "generic walker … pending" (`README.md:93`, which is stale).
- **Real maturity — usable as a deck generator for a pilot, not for cutover.** The prebuilt `build/PyKirchhoff.cpython-312-x86_64-linux-gnu.so` imports cleanly and `test_mkf_equivalence` passes (**58 assertions / 13 cases** at Vout 2% / Iout 2% / eff 3% vs MKF reference JSONs, `tests/test_mkf_equivalence.cpp:56-59`). But **only 2 of 13 design functions are Python-bound** — `src/bindings.cpp` has exactly three `m.def` lines (`design_flyback_tas:37`, `design_boost_tas:43`, `tas_to_ngspice:49`); the other 11 designers are C++-only.
- **The single most important blockers.** (a) 11 of 13 designers unbound to Python; (b) no packaging at all — no `pyproject.toml`/`setup.py`/`.gitmodules`/CI, and CMake hardcodes `PSMA_ROOT=/home/alf/PSMA` (`CMakeLists.txt:11`); (c) Kirchhoff's deck is **structurally incompatible** with Heaviside's MKF-tuned runner (per-stage `.subckt`/`X`, `Vstim_*` gate sources, embedded `.control`/`.meas`/`.endc` vs HS's flat deck with bare `.end` and `hsv_*` injection).
- **Honest readiness verdict: ready for a single-topology pilot, behind a flag, today.** Kirchhoff replaces *deck assembly + sim orchestration* — **not** the magnetics engine and **not** result extraction. Its MKF_MODEL path does not call MKF; it requires `magnetic.modelOutputs.spiceSubcircuit` pre-populated by an external MKF export (`MAS/src/MasConverter.cpp:108-118`), so PyOpenMagnetics stays.
- **Coverage is a reduction.** Kirchhoff covers **13** topologies; Heaviside's registry has **27** entries (`heaviside/topologies/registry.py`, confirmed `grep -c TopologyEntry( == 27`). The high-value gaps — LLC/CLLC/CLLLC/SRC, DAB, PFC, Vienna — have no Kirchhoff path. **MKF must stay for the long tail during and after migration.**
- **The natural seam exists but is unwired.** `heaviside/stages/spice_sim.py` (`simulate_from_spec`) is a clean PEAS-typed Tier-1 stage, but **neither** production chain uses it — `full_design.py:546` and `re_testbench.py:1023` both call `decompose_from_spec` directly. Wiring this seam is prerequisite work shared by *both* backends.
- **Fail-loud caveat in Kirchhoff.** Every design fn has a soft-default efficiency (`dr.value("efficiency", 0.88/0.9)`) — **confirmed in all 13** `src/*.cpp` files. This violates Heaviside's no-defaults rule and must be changed to throw before HS trusts the numbers.
- **Recommended first move.** Wire `stages/spice_sim.py` into the realize chain and CRE testbench with MKF as the only backend (Phase 0). This is independent of Kirchhoff, de-risks everything after, and turns the future flag-flip into the *only* behavioral change.

---

## 2. What Kirchhoff gives us

### The 3-step pipeline
1. **Design** — `design_<topo>(TAS-spec)` returns a typed `<Topo>Design` struct (duty, peak currents, magnetizing inductance, turns ratios). 13 designers exist as C++ headers: `design_flyback/boost/buck/forward/two_switch_forward/sepic/cuk/zeta/push_pull/psfb/ahb/acf/fsbb` (verified via `grep design_ src/*.hpp`).
2. **Assemble** — `build_<topo>_tas(design)` emits a vendor-neutral TAS document: `{inputs, topology.stages[] of CIAS bricks, interStageConnections[], simulation}`.
3. **Emit** — `tas_to_ngspice(tas, fidelity)` walks the TAS, emits one `.subckt` per stage via the CIAS converter, synthesizes a testbench (Vin source, load from operating points, PWM stimulus, `.tran`/`.control`/`.meas`), and returns deck text (`src/TasAssembler.cpp:149`). This is the single generic emitter the equivalence test drives for all 13 topologies (one `tas_to_ngspice` call site, `tests/test_mkf_equivalence.cpp:122`).

### The Fidelity model and how it maps onto HS's ideal-then-real flow
`PEAS::Fidelity {origin, allowStoredModelParams, curveFit}` has three origins, and the assembler **infers fidelity per component** from the part data — it is not passed explicitly per part (`infer_fidelity`, `src/TasAssembler.cpp:111-133`):
- **REQUIREMENTS** (ideal) — a pre-sourcing seed with no part data.
- **DATASHEET** (real parasitics) — a bound part with `manufacturerInfo`/`core`/`coil`. SAS emits real MOSFET `Rds_on`/`Coss`/body-diode and diode `Vf`/`CJO`/`TT`/`BV` directly into the deck (`SAS/src/SasConverter.cpp:63-158`).
- **MKF_MODEL** — a magnetic carrying `modelOutputs.spiceSubcircuit`, hoisted verbatim into the deck.

**This maps cleanly onto HS's ideal-then-real flow**: the *same* TAS becomes progressively more real as parts are bound, and re-emitting the deck yields a more-real deck. It is philosophically cleaner than HS's current approach, which regex-patches parasitics onto an ideal MKF deck *after* generation (`heaviside/sim/parasitics.py`). Under Kirchhoff, parasitics live in the data, not in a post-hoc text edit — so `inject_parasitics.py` becomes largely unnecessary **if** HS stamps selected MPNs as inline PEAS parts before the emit step.

### The 13 MKF-equivalence-verified topologies
The binary passes 58 assertions across 13 cases at 2%/2%/3%. Two nuances correct the briefing:
- **Efficiency is equality-checked for only 5 of 13** (boost, buck, flyback, push-pull, 4SBB). For the other 8 it is **directional only** (`CHECK(r.eff >= mkfEff)` with a ≤1.05 ceiling).
- The 8 directional cases are **not all "isolated/rectifier."** 5 are (forward, TSF, PSFB, AHB, ACF — MKF senses gross switch current or uses lossy real diodes). The other 3 — **SEPIC, Cuk, Zeta** — are non-isolated; they are directional for a *different* reason: MKF's reference decks carry 100 Ω bleeder/snubber resistors (~6 W) that Kirchhoff omits, artificially depressing MKF's efficiency. **Implication:** for those 8, Kirchhoff's absolute efficiency is *not* pinned to a reference — a real regression inside the (MKF, 1.05) band would be invisible, which matters because HS's real-BOM sim cares about absolute efficiency.

### The PyKirchhoff binding
The compiled `.so` is built and importable, exposing exactly `['design_boost_tas', 'design_flyback_tas', 'tas_to_ngspice']`. For any topology beyond boost/flyback, Python must either gain new `m.def` lines (one-liner each, mirroring the existing two) or hand-build a TAS document and call only the generic `tas_to_ngspice`.

---

## 3. What Heaviside does today

**HS does not generate the netlist — MKF does.** `heaviside/decomposer/api.py:181` calls PyOpenMagnetics' `generate_ngspice_circuit(...)` and receives `{"netlist": <deck text>}`. On top of that, HS does three things:

1. **Post-patches the deck text unconditionally** (`_patch_spice_defaults`, `api.py:84`, called at `api.py:194`) — rewrites MKF's pathological defaults: snubber R (100 Ω → 10 kΩ), `DIDEAL` diode IS/RS, and injects switch RON/VH that MKF omits for energy-storage topologies. This is the brittle text workaround for the unbound `SpiceSimulationConfig` C++ struct ("spice_config"), per `docs/pymkf-spiceconfig-binding-request.md`. **These patched values are pinned by golden decks — load-bearing, not cosmetic.**
2. **Parses** the deck with a hand-written ngspice parser (`spice_parser.py`).
3. **Applies a per-topology stencil** (`stencils.py`, ~4380 lines) to reverse-engineer a TAS `topology` block from the parsed deck — filtering testbench scaffolding, renaming refdeses (S1→Q1, Cout→C_out), grouping survivors into stages/interStageConnections.

`decompose_from_spec` (`api.py:225`) returns `(netlist, tas)` where `tas = {"inputs": <from inputs_mapper>, "topology": <stencil output>}`.

**The simulation path** (`heaviside/sim/`) shells out to plain `ngspice -b` (`runner.py:796`) — no PySpice/libngspice. The runner is hard-coupled to MKF's flat-deck conventions: it splices four `.meas tran hsv_* avg …` before a bare `.end`, selects a probe quadruple from a hard-coded `_PROBE_CANDIDATES` table keyed on MKF node/source names (`v(vin_dc)`, `i(Vin_sense)`, `i(Vl_sense)`), regex-parses `hsv_*` from stdout into a `SimResult`, and `simulate_closed_loop` runs a damped duty search by rewriting the `Vpwm … PULSE(...)` field.

**The realize chain** (`full_design.py` `stage3_realize`): `decompose_from_spec` (`:546`) → attach MKF magnetics → assemble real BOM → enrich → `inject_parasitics` (`:577`) → `simulate_closed_loop`/`steady_state` (`:593/595`) → stamp `tas.simulation_results.op0`. The CRE testbench (`re_testbench.py`) runs its own parallel two-phase ideal-then-BOM sim with a separate set of regex deck rewriters, also calling `decompose_from_spec` directly (`:1023`) and `inject_parasitics` (`:1203`).

**The natural seam.** `heaviside/stages/spice_sim.py` (`simulate_from_spec`) is a clean, PEAS-typed Tier-1 stage returning a stable `SpiceResult` — the ideal backend-selection point. **It is wired into neither production chain** (verified: no production importer; only `tests/unit/stages/test_spice_sim.py` imports it). Both chains bypass it.

**What stays in MKF regardless.** The magnetic engine (`design_converter_components`, `bridge.py:1797`; `attach_components_to_tas`, `bridge.py:869`) and ngspice execution + result extraction. Per project policy, all magnetics math stays in MKF, and Kirchhoff's MKF_MODEL path *consumes* an MKF-exported subcircuit rather than computing it.

---

## 4. Gap analysis: what blocks adoption

| # | Gap | Severity | Owner | Notes / plan-claim corrections |
|---|-----|----------|-------|---------------|
| K1 | **Only 2 of 13 design fns bound to Python** (`bindings.cpp` has 3 `m.def`); 11 C++-only | **Blocker** | Kirchhoff | Each fix is a one-liner mirroring flyback/boost. Without it, a Python pilot beyond boost/flyback is impossible unless HS hand-builds TAS docs. |
| K2 | **No packaging** — no `pyproject`/`setup`/`.gitmodules`/CI; CMake hardcodes `PSMA_ROOT=/home/alf/PSMA` (`CMakeLists.txt:11`, also `tests/test_tas_schema.py:17`) | High | Kirchhoff | Pilot mitigation: importlib-load the prebuilt `.so` (the proven `bridge._import_pyom_vendor` pattern). Blocks CI/prod (Scaleway plain-file deploy), not the pilot. |
| K3 | **Soft-default efficiency** (`value("efficiency", 0.88/0.9)`) violates fail-loud — **confirmed in all 13** `src/*.cpp` (briefing named only 3; it's pervasive) | High | Kirchhoff | Must throw, like the sibling `nominal()` helper does. HS's `inputs_mapper` already requires efficiency, so HS always passes it — but the C++ default must go before HS trusts the path. |
| K4 | **DATASHEET fidelity end-to-end-tested only for a capacitor ESR** (`test_real_fidelity.cpp`); real MOSFET/diode parasitics are *emitted* but not equivalence-tested | Medium | Kirchhoff | HS's real-BOM sim exists to capture real Rds_on/Vf losses; add an end-to-end bound-semiconductor efficiency test before trusting real-fidelity decks. |
| K5 | **P6 results extraction absent** — `tas_to_ngspice` returns deck text only; no code writes results into PEAS/MAS outputs | Low | Kirchhoff | Not blocking: HS already owns run+parse (`sim/runner.py`, `stamp_simulation_results`). Kirchhoff replaces deck-gen+parasitics, not execution/extraction. |
| H1 | **Deck not drop-in for HS's runner** (structural mismatch — confirmed) | High | Heaviside | KIR: per-stage `.subckt`/`X`, `Vstim_<stage>_<comp>` gates, output node = interStage group name, no `.save`, embedded `.control`/`.meas`/`.endc`. HS expects flat deck, bare `.end`, `_PROBE_CANDIDATES` on MKF names, `Vpwm` PULSE. Needs a Kirchhoff-aware deck normalizer. |
| H2 | **No backend abstraction; `spice_sim` seam unwired** | High | Heaviside | Build `kirchhoff_adapter.py` + a `backend=` switch in `simulate_from_spec`, THEN route `stage3_realize:546` and `re_testbench:1023` through it. The realize chain interleaves attach/BOM/enrich between decompose and simulate — `simulate_from_spec` does not model that today; that gap must be closed. |
| H3 | **No cross-backend equivalence harness in-suite** | High | Heaviside | Kirchhoff's own test is C++ deck-level only. HS has no test asserting KIR's TAS-block shape matches what `inject_parasitics`/realism gate/analyst expect. |
| H4 | **Pin/port/data convention mismatch** | High | Heaviside | HS: MOSFET `D/G/S`, diode `A/K`, magnetic `pri.1/sec0.2`, data = URL placeholder until BOM. KIR: `drain/gate/source`, `anode/cathode`, `primary_start`/`secondaryN_end`, always inline PEAS. HS's parasitics regex, realism gate, analyst, stress derivers are keyed on HS conventions. **Largest source of silent downstream degradation.** |
| H5 | **MKF_MODEL still requires external MKF export** | Medium | Heaviside | `MasConverter.cpp:108` throws if `spiceSubcircuit` absent. PyOpenMagnetics stays for `design_converter_components` + magnetic export; HS feeds the subcircuit into the TAS. Aligns with all-magnetics-in-MKF. |
| H6 | **No closed-loop regulation in Kirchhoff** | Medium | Heaviside | KIR is open-loop fixed-duty (`Vstim_*` PULSE from design dutyCycle). HS's `stage3_realize`/`re_testbench` run a damped duty search and fail-loud on non-convergence. Adapter must let HS's search drive KIR's gate net (re-point `_PULSE_LINE_RE` from `Vpwm*` to `Vstim_*`), or treat KIR's analytic duty as an open-loop seed. |
| H7 | **Submodule URL mismatch** in HS `.gitmodules` | Medium | Heaviside | Verified: RAS/CAS/SAS/TAS point at `OpenConverters/*.git`; PEAS/CIAS/CONAS point at `Power-Supply-Manufacturers-Association/*.git`; actual PSMA checkouts are PSMA-org. Reconcile before a Kirchhoff wheel build pulls family libs as submodules. |

**Claims that verification corrected/refuted:**
- *"All 13 eff verified at 3%"* — **refuted**: only 5 are equality-checked; 8 are directional, and 3 of those 8 (SEPIC/Cuk/Zeta) are non-isolated (mislabel corrected).
- *"HS and KIR emit the byte-identical topology.json schema, so the TAS is already the shared contract"* — **partially refuted**: HS's *vendored schema file* is byte-identical to PSMA's, but **Kirchhoff ships no topology.json schema and validates against none** (its proven internal contract is the CIAS atom-brick). HS and KIR are not pinned to the same TAS commit, so schema identity is incidental and could drift. Do **not** assume the TAS document is an already-enforced HS↔KIR contract.
- *"HS has 28/30 topologies"* — **corrected**: 27 registry entries backed by 29 `.py` modules. Don't use "28" for planning math.
- *"spice_sim referenced by a bridge.py docstring"* — **corrected**: `bridge.py` has no such reference; the only non-test reference is an unrelated `"ngspice_sim"` string literal in `cross_check.py:51`.

---

## 5. Topology coverage

**In both (13, direct map):** flyback, boost, buck, single_switch_forward→`forward`, two_switch_forward, sepic, cuk, zeta, push_pull, phase_shifted_full_bridge→`psfb`, asymmetric_half_bridge→`ahb`, active_clamp_forward→`acf`, four_switch_buck_boost→`fsbb`.

**Kirchhoff-missing (HS-only):** llc, cllc, clllc, series_resonant (SRC), dual_active_bridge (DAB), power_factor_correction (PFC), vienna, isolated_buck, isolated_buck_boost, phase_shifted_half_bridge, weinberg, plus the 3 filter/sense magnetics (common_mode_choke, differential_mode_choke, current_transformer — not power-stage converters).

**Implication.** The high-value resonant family (LLC/CLLC/CLLLC/SRC) needs a frequency-domain tank treatment that Kirchhoff's duty/phase-based, time-domain pipeline does not address (HS has a dedicated `resonant_freq.py` stage; Kirchhoff README P4 lists LLC design as pending). **MKF/decompose stays the backend for all 15 KIR-missing topologies indefinitely.** A backend-capability map routes the 13 to Kirchhoff (once bound + validated) and everything else to the unchanged MKF path. There is no scenario where Kirchhoff replaces MKF wholesale.

---

## 6. Recommended migration path

Each phase respects fail-loud (throw, no defaults), all-magnetics-in-MKF, and real (non-mocked) in-suite tests.

### Phase 0 — Wire the seam (backend-agnostic, no Kirchhoff yet). Effort: **M**
Route the realize chain and CRE testbench through `stages/spice_sim.py` so there is ONE backend-selection point, with MKF as the only backend. This is Kirchhoff-independent and de-risks everything after.
- Add `backend='mkf'` to `simulate_from_spec`; default path = current `decompose_from_spec` + `inject_parasitics` + `simulate_*`.
- Re-route `full_design.stage3_realize` (`:546/577/593`) through the stage, preserving the interleaved attach/BOM/enrich steps (the stage must expose hooks or accept a pre-built BOM-enriched TAS — it does not model those steps today).
- Re-route `re_testbench` (`:1023/1203`) through the same stage.
- In-suite test: the routed path reproduces today's `stage3_realize` numbers on an existing buck/flyback golden (no behavior change).
- Confirm `SimError`/`RealizeError` still raised — no fallback.

### Phase 1 — Kirchhoff-side: bind all 13 + minimal importability. Depends on P0. Effort: **M**
- **Kirchhoff:** add `design_<topo>_tas` `m.def` for the 11 unbound topologies in `src/bindings.cpp` (one-liner each); rebuild `PyKirchhoff.so`.
- **Kirchhoff:** remove the **13** soft-default `value("efficiency", 0.88/0.9)` sites — throw on missing efficiency (mirror the `nominal()` helper) before HS depends on the numbers.
- **Heaviside:** add `heaviside/decomposer/kirchhoff_adapter.py` loading `PyKirchhoff` via the `bridge._import_pyom_vendor`-style `importlib.spec_from_file_location` on the built `.so` (no wheel yet).
- **Heaviside:** add Kirchhoff as a 10th submodule; reconcile the `.gitmodules` org URL mismatch.

### Phase 2 — Pilot: ONE topology behind a backend flag, REQUIREMENTS only. Depends on P1. Effort: **L**
Pick **boost** or **flyback** (both already bound — no need to wait on P1 binding for the pilot itself).
- Adapter deck path: `design_<topo>_tas(spec) → tas_to_ngspice(tas, {origin:REQUIREMENTS}) → deck`.
- Write the deck-normalizer: strip KIR's `.control`/`.endc`, derive the output node from the TAS `interStageConnections direction:output` group (NOT `_PROBE_CANDIDATES`), re-inject HS's four `hsv_*` `.meas`, re-point the duty-search `_PULSE_LINE_RE` from `Vpwm*` to `Vstim_<stage>_<comp>`.
- In-suite equivalence harness: run the topology through BOTH backends, assert Vout/Iout within ~2% and eff within ~3%; settle to ≥10·RC to avoid the documented MKF 400-period under-settle artifact.
- The harness must **also assert the realism gate and analyst still see provenance + required fields** on the KIR-produced TAS — treat any UNAVAILABLE-where-MKF-was-FAIL as a test failure.
- Gate the Kirchhoff backend behind a per-topology capability flag defaulting **OFF**; flip ON only on green.

### Phase 3 — DATASHEET fidelity + BOM stamping (real-parasitic path). Depends on P2. Effort: **L**
- Convert HS's `selection_provenance` + flat fields (`rds_on`/`vf_typ`/`esr`, stamped by `catalogue/assemble.py`) into inline PEAS part envelopes (with `manufacturerInfo`) inside `tas.topology.stages[].circuit.components[].data` so `infer_fidelity` selects DATASHEET — **replacing** the regex `inject_parasitics` step.
- Verify the envelope passes KIR's `bound()` check and the SAS/CAS generators emit the expected real R/C+ESR/MOSFET+Coss+body-diode atoms.
- End-to-end real-BOM equivalence test (bound real MOSFET+diode+cap) vs the MKF `inject_parasitics` result — closes the KIR coverage gap where only a capacitor ESR is end-to-end tested.
- **Magnetic stays MKF:** feed `magnetic.modelOutputs.spiceSubcircuit` (from `bridge.attach_components_to_tas`) into the TAS for KIR's MKF_MODEL passthrough. `design_converter_components` stays MKF.

### Phase 4 — Expand to the 13, keep MKF for the rest. Depends on P3. Effort: **XL**
- Add per-topology equivalence + real-BOM tests for the remaining 12; flip each capability flag ON only on green.
- For the 8 directional-eff topologies, **pin an absolute efficiency expectation** in the HS harness (KIR with real bound diodes is the more-physical reference; document the constant with a datasheet/physics sanity check — never an MKF transient number) so a regression in the (MKF, 1.05) band is caught.
- Keep MKF for the 15 KIR-missing topologies via the backend map (unchanged decompose path).
- Once all 13 are green **and** Kirchhoff has a wheel + CI (P5), decommission HS stencils/post-patch **only** for the migrated topologies.

### Phase 5 — Kirchhoff P6/P7 hardening (parallel, Kirchhoff-side). Depends on P1. Effort: **L**
- **P7:** add `scikit-build-core` `pyproject.toml`, PSMA libs as git submodules, remove hardcoded `PSMA_ROOT`, add a SHA-cached wheel CI job mirroring HS's "Build PyOpenMagnetics from submodule" job (`ci.yml:106-155`).
- **HS:** switch the adapter from importlib-of-`.so` to the installed wheel once it exists; add a Kirchhoff submodule-wheel CI job.
- **P6 (nice-to-have, non-blocking):** write results into PEAS/MAS outputs — but HS's runner + `stamp_simulation_results` + analyst keep owning extraction regardless.

---

## 7. Risks & open questions

- **Kirchhoff still links/pins MKF.** The shippable library does *not* runtime-link MKF (`ldd PyKirchhoff.so` shows only libstdc++/libm/libc), but the MKF_MODEL path needs an MKF-exported `spiceSubcircuit`, and reference fixtures are MKF-generated. **PyOpenMagnetics does not go away** — Kirchhoff replaces deck assembly + sim orchestration, not the magnetics engine.
- **Silent downstream degradation from convention mismatch (H4).** If KIR's inline-PEAS-only, `drain/gate/source`-named TAS makes HS's realism gate/analyst/stress derivers see nothing, they degrade to UNAVAILABLE (not FAIL), letting a design pass on metadata alone — explicitly forbidden. *Mitigation:* the Phase 2/4 harness asserts those consumers still see provenance+fields, not just that Vout matches.
- **The CIAS intermediate is Kirchhoff's *real* internal contract**, not topology.json. The TAS document KIR builds is unvalidated against the PSMA schema, C++/in-memory for most topologies, and not pinned to HS's TAS commit. *Open question / action:* vendor the same TAS submodule commit into KIR and add round-trip schema validation on both emit and consume sides before treating topology.json as the shared contract.
- **Steady-state settling caveat.** MKF's PtP reference tests settle only 400 switching periods (~1.7·RC for a 100 µF/24 Ω boost — a transient). KIR's generator overrides this (`kBoostSettlingPeriods=2400`) and the equivalence test runs to ≥30·RC. Any HS harness must settle to ≥10·RC or it will compare under-settled transients.
- **Schema-drift risk between the two TAS shapes.** Pin both repos to one TAS revision; without it, the two emitters can diverge silently.
- **Losing load-bearing post-patch behavior.** HS's unconditional `_patch_spice_defaults` (snubR 100→10k, diodeRS, switch RON for energy-storage topologies) is pinned by golden decks. KIR setting these at emission time could shift numbers. *Mitigation:* the equivalence harness compares KIR decks against the patched MKF goldens; verify KIR's ideal-device constants reproduce the patched values at DC/HF limits and document any intended divergence.
- **Big-bang blast radius.** `decompose_from_spec` has 5 call sites (cli ×2, re_testbench, full_design, spice_sim) each doing different work with both netlist and tas. *Mitigation:* migrate only at the single `spice_sim` seam behind a per-topology flag defaulting OFF; Phase 0 routes everything through `spice_sim` with MKF still the only backend, so the flag flip is the only behavior change.

---

## 8. Bottom line

**Yes — start now, but start with Phase 0, not Kirchhoff.** Kirchhoff is genuinely usable today as a *deck generator* for a flyback/boost pilot: the `.so` imports, the generic `tas_to_ngspice` works, and the C++ equivalence suite passes tight against MKF. But it is a deck-assembly + sim-orchestration replacement, not a magnetics or results-extraction replacement, and it covers 13 of 27 topologies, so MKF stays for the long tail regardless. The highest-leverage, lowest-risk first step is **independent of Kirchhoff**: wire `heaviside/stages/spice_sim.py` into `stage3_realize` and `re_testbench` with MKF as the only backend (Phase 0, effort M), behind an in-suite golden test that proves no behavioral change. That single change closes the long-standing unwired-seam gap, gives both designer pipelines one backend-selection point, and reduces the eventual Kirchhoff adoption to a per-topology flag flip behind a cross-backend equivalence harness — exactly the kind of change that respects fail-loud, keeps all magnetics in MKF, and is validated by real tests rather than a wholesale swap of the working CRE-beats-Proteus pipeline.

**Key files:** Kirchhoff — `src/bindings.cpp:37-49`, `src/TasAssembler.cpp:111-133,149`, `src/*.cpp` (13 efficiency defaults), `CMakeLists.txt:11`, `README.md:97-98`, `tests/test_mkf_equivalence.cpp:56-59,122`; PSMA — `MAS/src/MasConverter.cpp:108-118`, `SAS/src/SasConverter.cpp:63-158`. Heaviside — `heaviside/stages/spice_sim.py`, `heaviside/pipeline/full_design.py:546-595`, `heaviside/pipeline/re_testbench.py:1023,1203`, `heaviside/decomposer/api.py:84-208`, `heaviside/sim/runner.py`, `heaviside/sim/parasitics.py`, `heaviside/bridge.py:869,1797`, `heaviside/topologies/registry.py`, `.gitmodules`, `.github/workflows/ci.yml:106-155`, `docs/pymkf-spiceconfig-binding-request.md`.