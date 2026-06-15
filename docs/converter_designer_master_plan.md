# Converter Designer — Master Architecture Plan

> Status: design (not yet implemented). Authored 2026-06-16 via a 19-agent grounding+design
> pass; every disputed code fact was verified against source (file:line cited inline).
> Build order starts at **B0** (§5). See also docs/stages_refactor_roadmap.md.

---

## Implementation status & B0 empirical correction (2026-06-16)

**DONE (this session):** `heaviside/stages/converter_spec_build.py` extracted from
`full_design._augment_converter_spec` (now a thin wrapper; 3 call sites unchanged).
Tests: `tests/unit/test_converter_spec_build.py` (9, incl. a guarded MKF integration test).
All green; existing `test_full_design.py` (11) unchanged.

**CORRECTION to B0's premise (verified by running MKF both ways, not just reading code):**
On the BASE buck path **MKF derives its own L and IGNORES an injected `desiredInductance`** —
designing with no `desiredInductance` → L = 17.3 µH; with `desiredInductance = 69 µH` injected
→ still 17.3 µH (ratio 1.000). So the plan's framing ("forbid `desiredInductance` so MKF
derives L") is **moot for the magnetic design — MKF already chooses L.**

The real, subtler issue later steps must address: the **ripple-0.3 seed** that `_design_job`/CRE
inject (5.3 µH for this spec) **differs 3.3× from MKF's derived L (17.3 µH)**, and the buck
isat post-filter (`bridge.py:_ipeak_worst_buck`, ~1266) computes worst-case ripple/ipeak from
`spec.desiredInductance` — i.e. the **seed L, not the L MKF built**. Direction is conservative
(seed < real → over-estimates ripple), so it is *safe* but computes saturation physics on an
inductance that isn't the one designed, and the seed can differ between the fast (screening,
`process_converter`→Advanced) and slow (final, base) MKF paths. **Open decision** (touches the
isat filter + stress — risk): make the isat filter / stress consume the **MKF-harvested L**
(`full_design:596` already re-stamps it post-design) instead of the pre-design seed, and stop
seeding `desiredInductance` on the designer path. Not done yet — needs sign-off because it
changes saturation gating.

---

# The World-Best Converter Designer — Master Architecture (Definitive Plan)

**Synthesis basis:** built on the highest-scoring **optimization-theory** skeleton (block-coordinate descent + the argmin/judgment ledger), grafting **correctness-first**'s provenance + cross-check spine, **mkf-capability-first**'s MKF-authority partition + abt discipline, and **agentic-product**'s minimal-input UX + bounded refinement loop. Every physics/convergence flaw the four critiques surfaced is fixed, and every disputed code fact below was **verified against source this session** (file:line cited).

---

## 0. Ground truth established this session (the facts the plan is built on)

| Claim under dispute | Verified reality (this session) | Consequence |
|---|---|---|
| "Designer path forces Advanced schema via ripple=0.3 `desiredInductance`" | **FALSE for the designer path.** `_augment_converter_spec` (full_design.py:238–244) injects only `maximumDutyCycle` + `maximumDrainSourceVoltage` — already BASE schema. The `desiredInductance` ripple injection lives ONLY in `cre.py:94–99` and `server.py:380–387` (the CRE/REST paths). `full_design.py:595–597` re-stamps the **MKF-harvested** L *post-design* (correct). | The "bug to undo" is in CRE/REST, not the designer. **Do not delete `compute_desired_inductance`** (CRE legitimately reproduces a known reference L). Scope the fix to: never let `desiredInductance` reach the *designer's* MKF call. |
| "Base schema derives L from duty/Vds alone; passing ripple is the bug" | **FALSE.** `Buck.cpp:159` THROWS without `currentRippleRatio` *or* `maximumSwitchCurrent`; `Buck.cpp:193` and `Flyback.cpp:1153` DERIVE L by dividing by `currentRippleRatio`. | `currentRippleRatio` is a **load-bearing base-schema input**. Keep it. The bug is `desiredInductance` (flips to Advanced), not ripple. |
| "`desiredInductance` is silently ignored by the base class" | **FALSE.** PyOM AGENTS.md §4.3 table: `desiredInductance` → *"Causes schema error"* in Method A (Base). | Injecting it doesn't waste a hint — it **throws**. Removing it from the designer path is mandatory, not cosmetic. |
| "Fast and slow paths use the same schema" | **FALSE for flyback.** AGENTS.md §4.3: `design_magnetics_from_converter()` (slow) → `Flyback` BASE; `process_converter()` (fast) → `AdvancedFlyback`. Both Heaviside paths pass the same converter dict (bridge.py:439, 322), but PyOM routes them to different C++ classes. | The fsw sweep's "L re-derived per fsw" only holds cleanly on the **base/slow path**. The fast path's L-contract differs. **Sweep must use a single consistent schema** (see §2). |
| "`get_pareto_magnetics` tool is missing/unwired" | **FALSE.** Implemented at `tools.py:297`, summary table built by `magnetic_picker.pareto_summary`. | No "wire the missing tool" work item. Only **extend its payload** with loss/fsw fields (which `pareto_summary` does NOT currently carry — verified). |
| "Worst-case Isat gate is universal" | **FALSE.** `_IPEAK_WORST` (bridge.py:1302–1312) covers ONLY buck/boost/cuk/flyback. For others `select_fast_by_isat_margin` returns **unfiltered** (bridge.py:1455–1462). | "Feasible across all OPs" silently no-ops for forward/push-pull/SEPIC/isolated-buck. **Must add Ipeak_worst computers** before the sweep can claim universal feasibility. |
| "Resonant ZVS switching loss is handled" | **VERIFIED ZERO.** `_mosfet_loss(zvs=True)` → `budget[f"{name}_switching"] = 0.0` (analyst.py:1520, called for LLC/CLLC/SRC). | For resonant topologies the total-loss objective collapses to magnetic-only → argmin drives fsw to the EMI ceiling. **Resonant carve-out required.** |
| "Switch loss computable from a bare MagneticDesign" | **FALSE.** `compute_buck_loss_budget` (analyst.py:199–209) reads `qg`/`rds_on` from the **stamped Q1** on a fully-assembled TAS. | The fsw sweep cannot call the analyst on a magnetic alone. It needs a **provisional REAL FET per fsw** (or a Qg surrogate from the Vds class). Chicken-and-egg resolved in §2.4. |
| "`isat = B_sat·N·A_e/L` removed; isat is gap-aware from MKF" | **VERIFIED.** `select_fast_by_isat_margin` uses `_isat_from_mas` → PyOM `calculate_saturation_current` (gap-aware). MEMORY confirms the analytical formula was deleted. | Keep using MKF isat everywhere. Never reintroduce the formula. |

**Switch-loss model gap noted (not a blocker):** the analyst uses `0.5·Vds·Id·(Qg/Ig)·fsw` with a fixed 1 A gate drive and `Qg_total` (a conservative over-estimate, analyst.py:746 NOTE) and **no `Coss·Vds²·fsw` term**. This biases fsw* slightly. We file it as an analyst improvement (§7), not an architecture blocker — it is monotone-increasing in fsw, so the *direction* of the trade is correct.

---

## 1. Vision (the user's converged target, unchanged)

Minimal input — **only `inputVoltage{min,nom,max}` + `rails[{Vout,Iout}]`**. An LLM proposes topologies AND guesses `maximumDutyCycle`/`maximumDrainSourceVoltage`. MKF's **BASE schema derives L, turns, duty, mode** (we never pre-compute `desiredInductance`). **Switching frequency is an OUTPUT** of a TOTAL-loss sweep (MKF magnetic + Heaviside switching), with the magnetic **re-derived as fsw moves** (L ∝ 1/fsw). An LLM picks the final point by **suitability** (the loss minimum is a deterministic argmin). Real parts come from TAS. A **bounded refinement loop** feeds the chosen switch's real Vds/Qg back. The deliverable is **ONE magnetic + ONE fsw** (or an fsw-vs-load law for QR/DCM) feasible across **ALL** operating points. House rules throughout: no silent fallbacks, all magnetics math in MKF, cross-checked trustworthy numbers.

**Organizing principle (the argmin/judgment ledger):** every load-bearing *number* is a deterministic argmin or an MKF physics call; every LLM only **prunes a discrete set**, **selects within an already-valid set**, or **seeds** — never emits a load-bearing number.

---

## 2. End-to-end co-design loop (corrected)

```
INPUT: Vin{min,nom,max} + rails[{Vout,Iout}]            (nothing else mandatory)
  │
  ▼ STAGE A — TOPOLOGY + CONSTRAINT PROPOSAL
  │   engine floor: topology_id.feasible(spec)                         [REUSE stage]
  │   judgment:     topology-constraint-proposer  (NEW Strands agent)
  │       → ordered viable topologies + per-topology
  │         {maximumDutyCycle, maximumDrainSourceVoltage, switch_class}
  │   reconcile (union, warn on Jaccard>0.5)                           [REUSE topology_id]
  │   ── replaces hardcoded 0.5 / 3·Vmax at full_design.py:238/244
  │
  │  (parallel over viable topologies — existing ProcessPoolExecutor)
  ▼ STAGE B — BUILD BASE-SCHEMA CONVERTER SPEC                          [NEW thin stage: converter_spec_build]
  │   BASE schema: KEEP currentRippleRatio (MKF needs it to derive L);
  │                NEVER include desiredInductance/desiredMagnetizingInductance.
  │   guard: if desired* present on the DESIGNER path → raise (house rule, loud).
  │   OP grid = rails × {Vin_min,Vin_nom,Vin_max}.
  │   topology-class branch:
  │     • hard-switched (buck/boost/flyback/forward/…) → fsw-sweep path (Stage C-hs)
  │     • resonant (LLC/CLLC/SRC/CLLLC)               → resonant path  (Stage C-res)
  │
  ▼ STAGE C-hs — fsw SWEEP ON TOTAL LOSS  (hard-switched)               [NEW stage: frequency_sweep]
  │   COARSE grid (log-spaced [f_lo,f_hi]):                              ← fast path, ~12s/call
  │     for fsw in grid:
  │       spec_f = spec with op.switchingFrequency=fsw   (L RE-DERIVED by MKF, ∝1/fsw)
  │       front  = bridge.design_magnetics_fast(topology, spec_f, K)   [REUSE bridge]   (MKF)
  │       harvest L_f = _harvest_authoritative_inductance(front)        (MKF output)
  │       for cand in front:
  │          P_mag(cand)  = analyst._inductor_loss_from_mas(cand.mas)   (MKF numbers)
  │          P_sw(cand,f) = switch_loss_surrogate(Vds_class, Ipk, fsw)  ← Qg-bound, NO part yet
  │          feasible     = isat_margin(cand, L_f, Ipeak_worst@worstOP) (MKF isat)
  │          total        = P_mag + P_sw
  │   bracket the min over FEASIBLE cells (worst-OP total)
  │   FINE refine near bracket: golden-section, RE-RANK the top-K on the
  │       FULL loss model (skin+proximity via design_magnetics slow OR the
  │       single-point loss calls) before the final argmin                ← fixes fast-path bias
  │   → (fsw*, feasible Pareto front at fsw*, loss curve, breakdown)
  │   raise FrequencySweepError if NO feasible (cand,f) exists (never clamp)
  │
  ▼ STAGE C-res — RESONANT fsw (resonant topologies)                    [NEW small: resonant_freq]
  │   fsw is NOT a loss argmin — it is set by the tank gain law / Q-factor
  │   window MKF already derives (minSwitchingFrequency..maxSwitchingFrequency,
  │   full_design.py:302–312). Pick fsw from the gain law; magnetic loss still
  │   from MKF; switching loss ≈ 0 (ZVS) is CORRECT here. No EMI-ceiling runaway.
  │
  ▼ STAGE D — SUITABILITY PICK                                          [REUSE+EXTEND agent: magnetic-pareto-picker]
  │   deterministic argmin already chose loss-optimal; LLM does QUALITATIVE
  │   judgment over the loss-ANNOTATED front: stock, manufacturability,
  │   gapability, turn-count sanity, thermal/mech fit, avoid exotic parts.
  │   picks ONE index (cannot invent a point). Tool payload extended with
  │   total_loss_w + switching_loss_w + fsw (pareto_summary extended).
  │
  ▼ STAGE E — REST-OF-CONVERTER + REAL PARTS                            [REUSE Stage 3 chain]
  │   stress_extract → component_match/selector (real TAS FET/diode/cap/ctrl,
  │   LOWEST_TOTAL_LOSS tiebreaker at fsw*) → assemble → decompose →
  │   inject_parasitics → spice_sim → run_analyst → realism_gate
  │
  ▼ STAGE F — MULTI-OP RECONCILIATION + CROSS-CHECK                     [NEW: op_reconcile + cross_check]
  │   op_reconcile: re-validate the SINGLE chosen (magnetic,fsw*) across ALL
  │       OPs — worst-OP saturation (MKF isat) + worst-OP thermal (analyst Tj).
  │       Identify the BINDING OP; emit machine-readable ConstraintFeedback.
  │       For QR/DCM: emit an fsw-vs-load LAW (per-load sweep), ONE magnetic
  │       feasible across the whole law (worst-OP saturation + high-f light-load
  │       core-loss corner both checked).
  │   cross_check: triangulate INDEPENDENT estimators only (see §3, I3).
  │
  ▼ STAGE G — BOUNDED REFINEMENT (the "few iterations")                 [NEW agent: design-orchestrator]
  │   real picked-FET Vds_rated/Qg_total → re-seed maximumDrainSourceVoltage
  │   + re-cost P_sw → re-run C–F. Inner magnetic pick is DETERMINISTIC during
  │   refinement (LLM suitability pick runs ONCE, after convergence).
  │   Converge: chosen FET MPN stable AND |Δfsw*|/fsw* < ε. Cap N≤3.
  │   Oscillation between two near-equal FETs → surface as reviewer objection
  │   (do NOT silently pick; do NOT loop forever) — raise RefinementStalled.
  │
  ▼ STAGE H — ADVERSARIAL REVIEW                                        [REUSE reviewer_panel → ray + nicola]
  ▼
OUTPUT: ONE magnetic + ONE fsw* (or fsw-vs-load law) feasible across ALL OPs,
        full real-part BOM, loss/volume/cost Pareto context, provenance on
        every number, passed adversarial review.
```

### The traps, provably closed
1. **Loop-order (L ∝ 1/fsw):** magnetic re-derived by MKF *inside* the sweep loop (Stage C-hs); test asserts L scales ≈ 1/fsw across grid points.
2. **Magnetic-loss-only fsw:** objective is named `total_loss_magnetic_plus_switching`; a regression test fails if the switching term is dropped.
3. **Fast-path loss bias (skin/proximity omitted):** coarse grid uses fast path to *locate the basin*, but the **final argmin re-ranks the bracketed top-K on the FULL loss model** — the fast path never decides the optimum alone.
4. **Resonant EMI-ceiling runaway:** resonant topologies branch to the **gain-law fsw** path (Stage C-res), never the loss-argmin; ZVS P_sw=0 is correct there.
5. **Switch-loss chicken-and-egg:** the coarse sweep uses a **Qg surrogate** bounded by the seeded Vds class (a real-part *envelope*, not a fabricated constant); real FET selection happens once per refinement iteration in Stage E, then re-costs the sweep in Stage G.
6. **Per-OP infeasibility:** argmin runs over `max(per_op_total)` with a `feasible_all_ops` pre-filter; `op_reconcile` re-checks the single chosen design at every OP.
7. **Isat gate gaps:** Stage C-hs requires an `Ipeak_worst` computer for the topology; if none registered → **raise** (no silent no-op), forcing us to add computers before claiming feasibility (MKF-gap/Heaviside-work item §7).

---

## 3. Component responsibilities (MKF / Heaviside / LLM)

| Concern | Owner | API / mechanism |
|---|---|---|
| Derive L, turns ratio, duty, conduction mode | **MKF** base classes | `Flyback`/`Buck`::`process_design_requirements` via `design_magnetics_from_converter` (slow) — from OP + `maximumDutyCycle` + `maximumDrainSourceVoltage` + `currentRippleRatio` |
| Magnetic Pareto front, gapping, winding | **MKF** `MagneticAdviser` | `bridge.design_magnetics(_fast)` → `calculate_advised_magnetics(_fast)` |
| Core + copper loss numbers | **MKF** (Steinmetz/iGSE + ohmic/skin/proximity) | read from MAS via `analyst._inductor_loss_from_mas` |
| Saturation current (gap-aware) | **MKF** `calculate_saturation_current` | via `_isat_from_mas`; **never** `B_sat·N·A_e/L` |
| Switching/conduction/RR/ESR loss | **Heaviside** `analyst.py` | `0.5·Vds·Id·(Qg/Ig)·fsw` + RR + ESR; lives outside MKF by definition |
| Switch-loss **surrogate** for the coarse sweep | **Heaviside** `frequency_sweep` | Qg-bound from seeded Vds class (envelope, not fabricated) |
| Component stress (worst-case) | **Heaviside** `stress.py` | `stress_extract.analytical(_per_op)` |
| Real part selection | **Heaviside** `selector.py` + TAS | `LOWEST_TOTAL_LOSS` tiebreaker (fsw-aware) |
| Physics PASS/FAIL | **Heaviside** `realism_gate` | deterministic verdict; LLM `explain` never flips it |
| **Topology + constraint proposal** | **LLM** Strands | `topology-constraint-proposer` (NEW); floored by `topology_id.feasible` |
| **Pareto suitability pick** | **LLM** Strands | `magnetic-pareto-picker` (EXTENDED); within the feasible, loss-ranked set |
| **Refinement orchestration** | **LLM** Strands | `design-orchestrator` (NEW); bounded N≤3, deterministic loop wrapper |
| **Adversarial review** | **LLM** Strands | `ray` + `nicola` via `reviewer_panel` |

**Cross-check (I3), corrected — independent estimators only:** the analyst's magnetic loss is **read from MKF's MAS**, so analyst-vs-MKF magnetic loss is the *same number* — a vacuous comparison. `cross_check` therefore triangulates only **genuinely independent** estimators with **per-quantity tolerances**: (a) **efficiency**: analyst closed-form vs ngspice sim (tol per topology; ZVS-resonant gets a wider band since analyst P_sw=0 vs sim transition loss legitimately differ); (b) **total loss**: analyst sum vs sim Pin−Pout; (c) **Tj**: analyst Rth·P vs any sim thermal. Disagreement > tol → realism check FAILs (surface, don't average).

---

## 4. Reused vs New (the explicit ledger — honoring hard constraints A & B)

### Strands agents

| Agent | Status | Justification |
|---|---|---|
| `ray`, `nicola` | **REUSE** | Stage H via `reviewer_panel.review`; review-role gated; unchanged. |
| `reviewer` | **REUSE** | Optional mid-loop sanity / realism `explain`. |
| `magnetic-pareto-picker` | **REUSE + EXTEND** | Prompt/role exist and `get_pareto_magnetics` is **already registered** (tools.py:297, verified). Only work: extend `pareto_summary` to carry `total_loss_w`/`switching_loss_w`/`fsw`, and add the loss-curve to the agent's input. Same "read table, pick one index, no arithmetic" contract. |
| `topology-selector` | **REUSE** (CRE keeps it) | Pure topology screen; still used by CRE. The designer uses the new proposer instead. |
| 8 TAS/CRE agents (`component-librarian`, `bom-extractor`, `cross-referencer`, `otto`, `component-auditor`, `reverse-engineer`, `competitor`, `crowbar`, `hatchet`) | **REUSE / untouched** | Out of the designer loop. |
| **`topology-constraint-proposer`** | **NEW** | No existing agent emits `maximumDutyCycle`/`maximumDrainSourceVoltage` (today hardcoded 0.5 / 3·Vmax). Distinct output contract (switch-class/duty seeding). `allowed_tools: []`, single-shot, slots into `call_agent_json` — zero new plumbing. Kept separate from `topology-selector` to preserve that agent's clean, CRE-shared spec→topology contract. |
| **`design-orchestrator`** | **NEW** | Fills the already-referenced `design-orchestrator.md` slot (full_design.py:34). Tool-using; tools WRAP existing pipeline functions (no re-implemented physics). Decides *whether/how* to re-seed and re-run; the loop bound + convergence test are deterministic in the wrapper. |

> Net new agents: **2**. Extended: **1**. Reused: all others.

### PEAS stages (`heaviside/stages/`)

| Stage | Status | Role |
|---|---|---|
| `topology_id` (`feasible`/`resolve`/`identify`) | **REUSE** | Stage A floor + reconcile |
| `stress_extract` (`analytical`/`analytical_per_op`/`_worst_case_across_ops`) | **REUSE** | Stages C/E/F stress + worst-OP |
| `component_match` (`find_candidates`/`select_candidate`) | **REUSE** | Stage E part ranking |
| `mpn_verify` | **REUSE** | Stage E/F BOM gating vs TAS |
| `spice_sim` | **REUSE** | Stage E sim (one cross-check estimator) |
| `realism_gate` (`evaluate`/`explain`) | **REUSE** (+1 check `estimators_agree`) | Stages E/F verdict |
| `reviewer_panel` (`aggregate`/`review`) | **REUSE** | Stage H |
| `bom_extract` | **NOT USED** | Designer builds BOM from TAS, not PDF |
| **`converter_spec_build`** | **NEW (thin)** | Stage B: assemble BASE-schema dict (keep ripple, forbid desired* on designer path), build rail×Vin OP grid, topology-class branch. Engine-only. Centralizes logic today smeared across `_augment_converter_spec` + CRE/server. |
| **`frequency_sweep`** | **NEW** | Stage C-hs: the heart. Coarse-fast → bracket → fine-full-loss re-rank → worst-OP argmin of TOTAL loss, magnetic re-derived per fsw. Engine-only; composes `bridge` + `analyst` + `stress_extract`. |
| **`resonant_freq`** | **NEW (small)** | Stage C-res: gain-law fsw for resonant topologies (no loss argmin). |
| **`op_reconcile`** | **NEW** | Stage F: single-design cross-OP feasibility + binding OP + ConstraintFeedback + QR/DCM fsw-law. |
| **`cross_check`** | **NEW (small)** | Stage F: independent-estimator triangulation (I3). |

> Net new stages: **5** (one thin, two small). Reused: 7. Each new stage owns a capability the survey + this session's verification prove absent.

---

## 5. Build roadmap (staged, each independently testable)

| Step | Deliverable | Touches | Test gate |
|---|---|---|---|
| **B0 — UNBLOCK (start here)** | Guarantee the designer path is pure BASE schema: assert no `desiredInductance`/`desiredMagnetizingInductance` reaches `design_magnetics` on the designer route; **keep `currentRippleRatio`**. Leave CRE/server `compute_desired_inductance` intact (CRE reproduces reference L). Extract `converter_spec_build` from `_augment_converter_spec`. | `full_design.py:230–342`, NEW `stages/converter_spec_build.py` | Unit: designer spec has no `desired*`, has `currentRippleRatio`; MKF returns an L; harvested L matches Maniktala reference for a Flyback; regression that existing designs are unchanged-or-improved. **Crucially: a base spec WITH `desiredInductance` now raises (matches AGENTS.md schema error) instead of silently flipping to Advanced.** |
| **B1 — Provenance** | `provenance.py`; retrofit `selection_provenance`/`tj_provenance` to a uniform `{producer, method, source_ref, inputs_hash}`; realism gate treats no-provenance ⇒ UNAVAILABLE. | NEW small + realism gate | Unit: every stamped field has provenance; sourceless field ⇒ UNAVAILABLE. |
| **B2 — Constraint proposer** | `topology-constraint-proposer` agent + `design_constraints` envelope (deterministic band: `0.05<D<0.95`, `vmax<Vds<20·vmax`, **and Vds class must map to a real switch class present in TAS** to avoid Stage-G thrash); wire into Stage A; remove hardcoded 0.5/3·Vmax. | NEW agent + `topology_id` consumer | Unit (fake LLM) + LLM smoke: emits plausible per-topology D/Vds; out-of-band ⇒ raise; deterministic fallback when no API key. |
| **B3 — Ipeak_worst coverage** | Add `Ipeak_worst` computers for every hard-switched topology the designer targets (forward, push-pull, SEPIC, isolated-buck, …); make `select_fast_by_isat_margin` **raise** (not silently pass) when no computer is registered for a topology entering the sweep. | `bridge.py:1302`, `stress.py` | Unit per topology: Ipeak_worst matches the realism-gate stress formula; missing computer ⇒ raise. |
| **B4 — frequency_sweep (hard-switched)** | Coarse-fast grid → bracket → golden-section → **full-loss re-rank** of bracketed top-K → worst-OP argmin of `P_mag(MKF)+P_sw(surrogate)`; magnetic re-derived per fsw; `FrequencySweepError` on no-feasible. | NEW `stages/frequency_sweep.py` (REUSE bridge+analyst+stress) | Unit on synthetic U-curve: argmin correct, not at endpoint; L≈1/fsw asserted; dropping P_sw changes fsw* (trap-2 guard); raises when no feasible cell. Integration: real Buck fsw* balances core+copper+switching. |
| **B5 — Suitability pick** | Extend `pareto_summary` with `total_loss_w`/`switching_loss_w`/`fsw`; extend `magnetic-pareto-picker` to read the fsw-annotated front; deterministic `pick_best_pareto` stays as offline/smoke path. | `magnetic_picker.py`, `tools.py`, prompt | Unit: deterministic path unchanged with no key; LLM picks a valid index, never invents one; picks nearby cell + justifies when argmin part is exotic/out-of-stock. |
| **B6 — Resonant branch** | `resonant_freq` (gain-law fsw); Stage B routes resonant family to it; ZVS P_sw=0 retained as correct. | NEW small | Unit: LLC fsw lands in MKF's gain-law window; no EMI-ceiling runaway; magnetic loss still from MKF. |
| **B7 — op_reconcile + cross_check** | Cross-OP saturation+thermal on the single chosen design; binding-OP id; `ConstraintFeedback`; QR/DCM fsw-law; `cross_check` independent-estimator triangulation + `estimators_agree` realism check. | NEW stages + realism gate | Unit: 2-OP fixture where OP2 binds; corner-OP saturation ⇒ InfeasibleAtOP; QR topology emits monotone law; injected estimator disagreement FAILs the gate; analyst-vs-MKF magnetic loss NOT treated as independent. |
| **B8 — Refinement loop** | `design-orchestrator` + deterministic wrapper (N≤3); real FET Vds/Qg re-seeds constraints + re-costs sweep; **inner magnetic pick deterministic during refinement**, LLM suitability pick once at the end; oscillation ⇒ `RefinementStalled` surfaced to reviewer. | NEW agent + `full_design` wiring | Integration: a design failing FET derating on pass 1 re-seeds Vds and passes on pass 2; oscillation surfaces loudly; cost cap honored. |
| **B9 — E2E + UX** | Minimal-input `/design` endpoint (Vin+rails only); streamed stage events; loss-vs-fsw curve artifact; `design_provenance`; optional HITL checkpoints (off by default). | API + web | E2E on real specs (Würth/Proteus reference set): minimal input → reviewed design; datasheet cross-check on chosen parts; ray+nicola attached. |

**Order rationale:** B0 unblocks the base-schema thesis and is mostly *assertion + extraction* (low risk, immediate value). B1 makes every later number auditable. B2–B6 build the fsw/suitability core. B3 (Ipeak coverage) gates B4's feasibility honesty. B7 is the verification spine. B8 (most expensive, depends on everything) is last. Each step leaves the suite green — **no step disables a test or routes around a failure** (house rule).

---

## 6. Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Cost blowup** — naïve grid × K candidates × Vin-corners × N iters × slow path = hundreds of MKF calls/run. | Coarse grid on the **fast path** (~12 s/call, verified bridge.py:421); promote only the **2–3 bracketed points** to the full-loss path; resonant branch skips the sweep entirely; per-topology `ProcessPoolExecutor`; hard per-design MKF-call budget enforced by the orchestrator (surface, don't truncate). |
| **Fast-path loss bias** (skin/proximity omitted → fsw* too high). | Coarse grid only *locates the basin*; **final argmin re-ranks the bracketed top-K on the full loss model**. Fast path never decides the optimum alone. |
| **Switch-loss chicken-and-egg** (analyst needs a stamped FET; sweep has none). | Coarse sweep uses a **Qg surrogate** from the seeded Vds class (a real-part *envelope*, house-rule-compliant — not a fabricated constant). Real FET picked once per refinement iteration (Stage E), then re-costs the sweep (Stage G). |
| **Refinement non-convergence / oscillation** (negative feedback over a discrete catalog; LLM is not an argmin). | **Inner magnetic pick is deterministic during refinement** (LLM suitability runs once, after convergence) — restores the block-coordinate monotonicity. Convergence = stable FET MPN AND |Δfsw*|/fsw*<ε. Cap N≤3. Oscillation ⇒ `RefinementStalled` surfaced to the reviewer, never silently resolved. |
| **Cross-OP infeasibility is the common case** at fixed fsw (wide Vin + multi-rail). | This is *why* QR/DCM variable-frequency exists. `op_reconcile` emits an **fsw-vs-load law** (per-load sweep, ONE magnetic feasible across the whole law) instead of forcing a single scalar fsw. If even the law is infeasible → surface to reviewer with the binding OP. |
| **Isat gate silently no-ops** for unsupported topologies. | B3 makes the sweep **raise** when no `Ipeak_worst` computer is registered — converts a silent gap into a loud work item. |
| **Resonant runaway** (P_sw=0 → fsw to EMI ceiling). | Resonant topologies never enter the loss-argmin; fsw from the gain law (Stage C-res). |
| **Switch-loss model crude** (no Coss, Qg over-estimate). | Filed as analyst improvement (§7). Monotone-in-fsw so trade *direction* is correct; conservative (over-estimates switching loss → fsw* biased *down*, the safe direction). |

---

## 7. MKF / cross-repo gaps to file (abt board)

1. **`abt: MagneticAdviser is a weighted-sum scalarizer, not a true Pareto front`** (`MagneticAdviser.h:29`, LOGIC-3). For a meaningful suitability pick the LLM needs a non-dominated set, not one weighted winner. *Workaround now:* call `design_magnetics` with a small set of weight vectors (loss/volume/cost-heavy) and union — `bridge.design_magnetics` already accepts a `weights` mapping (verified). *Ask:* epsilon-constraint / non-dominated return.
2. **`abt: no native fsw-resolved magnetic sweep API`** (`sweep_*_over_frequency` do NOT exist in the .pyi — verified). *Workaround now:* Heaviside orchestrates via per-fsw `design_magnetics(_fast)`. *Ask (perf, non-blocking):* `calculate_advised_magnetics_over_frequency(inputs, fsw_grid, N)` re-deriving L per fsw in C++.
3. **`abt: offline-flyback may need Advanced schema`** (AGENTS.md:200, §4.3). The BASE flyback DCM path can return zero candidates for offline (high-step-down) cases. *Decision needed:* topology+OP-aware base-vs-advanced selector. Until resolved, the designer **uses base where it works** (buck/boost/CCM flyback/forward/push-pull) and surfaces offline-flyback-DCM as a known limitation — **never** silently falls back to a fabricated ripple L.
4. **Heaviside analyst improvement (own repo, file as issue):** add the `0.5·Coss·Vds²·fsw` turn-off term and `Qgd`-based (not `Qg_total`) transition charge to `_mosfet_loss` once `Coss`/`Qgd` land in TAS. Bias is currently conservative (over-estimates), so non-blocking.
5. **`abt: confirm `calculate_saturation_current` is the single isat source`** — verified it is used; keep the analytical `B_sat·N·A_e/L` deleted (MEMORY confirms). No action unless a regression reintroduces it.

---

## 8. First concrete step

**Step B0 — the base-schema unblock**, because it is the precondition for the whole fsw co-design and it is low-risk (assertion + extraction, no new physics):

1. Create `heaviside/stages/converter_spec_build.py` extracting `_augment_converter_spec`'s logic, with one new invariant: on the **designer path**, if the spec carries `desiredInductance` or `desiredMagnetizingInductance`, **raise** `BridgeError("designer path must use BASE schema — desiredInductance flips MKF to AdvancedFlyback (schema error); derive L via MKF, don't pre-compute it")`. **Keep `currentRippleRatio`** (MKF base classes require it — Buck.cpp:159 throws without it; Flyback.cpp:1153 divides by it).
2. Leave `cre.py:compute_desired_inductance` and `server.py` untouched (CRE legitimately reproduces a known reference L; deleting it breaks CRE — verified).
3. Add a regression test: a designer spec round-trips through `design_magnetics` with no `desired*` keys, MKF derives L, and `_harvest_authoritative_inductance` returns it; assert L matches the Maniktala worked-example value for a reference Flyback; assert a designer spec *with* `desiredInductance` raises.

This delivers a clean, tested BASE-schema designer entry — the foundation every later stage (`frequency_sweep` especially) stands on — without touching the working CRE pipeline.

---

**Bottom line.** The world-best converter designer is the existing Heaviside spine evolved with **2 new Strands agents** (`topology-constraint-proposer`, `design-orchestrator`), **1 extended agent** (`magnetic-pareto-picker`), **5 new deterministic stages** (`converter_spec_build`, `frequency_sweep`, `resonant_freq`, `op_reconcile`, `cross_check`), a **provenance + independent-estimator cross-check** layer, and **scoped CRE-only** handling of the ripple injection. It is a block-coordinate descent — enumerate LLM-pruned topologies, **deterministically minimize TOTAL loss over fsw while MKF re-derives L∝1/fsw** (re-ranked on the full loss model near the optimum, carved out for resonant topologies), let MKF own the magnetic and an LLM pick the suitable point, select real TAS parts, and **fixed-point refine** from realized part data until fsw* and the BOM stop moving — reconciled to one design feasible at every operating point, with every number sourced from MKF or a deterministic argmin and cross-checked before it can pass a gate.