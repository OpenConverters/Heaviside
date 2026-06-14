# Pipeline → reusable stages refactor (roadmap)

Goal: pull the capabilities currently re-implemented inline across the CRE,
CR/crossref, and converter-designer pipelines into **individual, reusable,
independently unit-tested stages**, the way `value_parse.py` already is.

## Design principles

1. **Canonical types = PEAS.** Stages consume/produce the PEAS-generated
   types already in `heaviside.types` (`Peas` umbrella + `Capacitor`,
   `Resistor`, `Mosfet`, `Diode`, `Igbt`, `Magnetic`, `Controller`, `Core`,
   `Coil`, `Terminal`, …) with shared primitives from `PEAS/utils.json`
   (`dimensionWithTolerance`, `distributorInfo`, `manufacturerInfo`). No
   ad-hoc `BomComponent`/`Candidate` dataclasses — this kills the
   field-drift bug class (`type` vs `component_type`, `part` vs `mpn`) at
   the root and reuses the existing guards/validation.

2. **Two-layer stage shape: deterministic engine + optional bounded LLM.**
   - `<engine>` — pure Python, owns truth + correctness, fully unit-tested.
   - optional `<engine>_select` / `_calibrate` / `_explain` — LLM ON TOP,
     a separate stage. It may *select among valid options* or *calibrate to
     evidence*; it must NEVER fabricate, override physics, or fit to a
     desired answer. Each layer is independently tested.

3. **LLM only where input is unstructured prose or a genuine judgment**
   (extract-from-PDF, name-topology-from-text, engineering review, pick the
   best of a sound candidate set, choose convergent solver settings).
   Everything mechanical — match, simulate, derive stress, check physics,
   look up a part — is plain tested Python.

4. **Tests — REAL, never mocked.** Deterministic cores → free, fast, in the
   normal suite (the bulk). LLM-layer tests → also in the normal suite,
   calling the **real LLM** (NO mocks/stubs — a mocked LLM test proves
   nothing), kept SMALL (a few cache-friendly cases) so per-test cost stays
   low; modest cost is acceptable. Reserve opt-in env gating only for big
   sweeps (full all-10-designs CR). Every stage is independently testable so
   pipelines can be composed from verified pieces with confidence.

5. **Migration safety.** Extract → repoint call sites → prove the existing
   suites (1,325 unit + crossref regression) stay green → delete the inline
   copies. One stage at a time.

## Stages

### Tier 1 (high reuse, high payoff)

| stage | engine (Python, tested) | optional LLM layer | replaces / used by |
|---|---|---|---|
| **bom_extract** | CSV/table/structured adapters → `Peas` components; normalize, dedup, guard-validate | `bom_extract` LLM adapter for unstructured PDF/image → same `Peas` shape | CRE `_stage0`+`_stage2`, CR override/CRE; new multi-modal inputs |
| **component_match** | specs+technology+voltage → correct, complete, ranked candidate set (the supercap-invariant lives here) | `component_select` — pick best (may choose non-#1 for footprint/lifecycle/app) | CR `_rank_candidates`+tools AND designer `catalogue/selector` (unify the two) |
| **spice_sim** | netlist+OP → waveforms/measurements (deterministic given the .so) | `spice_calibrate` — tune solver/uncertain-model knobs toward CONVERGENCE / datasheet GROUND TRUTH, never toward a desired metric | cre_testbench, decomposer, guardrails/realism, bridge |
| **reviewer_panel** | verdict-schema normalization + role gating + aggregation | the Ray/Nicola review call (the LLM boundary) | CRE `_stage4_review`, CR `_stage7_review`, designer `_stage4_adversarial_review` |

### Tier 2 (after Tier 1, once canonical types + pattern are established)

| stage | engine | optional LLM | used by |
|---|---|---|---|
| **topology_id** | feasibility screening / duty-bounds math | PDF/prose → topology name | CRE `_stage2_reverse_engineer`, designer `topology_screen`/`topology_selector_llm` |
| **stress_extract** | waveforms → per-component `ComponentStress` (pure) | — | CRE→CR bridge, realism, guardrails, match_score |
| **realism_gate** | physics PASS/FAIL (pure — verdict stays deterministic) | `_explain` failures / suggest fixes (never flips verdict) | designer, CRE |
| **mpn_verify** | MPN → TAS presence + data (pure) | — | CRE `_stage2_5_verify_mpns`, CR `component_exists` |

### Already modular (the precedent)
`value_parse`, `pdf_extract`, `bridge` (PyOM gateway), partially `catalogue/selector`.

## Sequencing
1. Canonical PEAS-typed contracts (confirm `heaviside.types` coverage; thin views where needed).
2. **bom_extract** (CSV/table deterministic + isolated LLM PDF adapter) + tests.
3. **component_match** (unify selector + crossref ranker; carries the ceramic/technology/voltage invariants) + `component_select` LLM layer.
4. **spice_sim** + `spice_calibrate`.
5. **reviewer_panel**.
6. Tier 2.
