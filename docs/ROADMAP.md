# Heaviside Roadmap

Nine phases from bootstrap to v0.1. Each phase ends with a CI-green commit.
**Status reflects HEAD as of 2026-05-28.** When status drifts from reality,
fix it in the same commit that ships the work.

> Living document. For the actual ordered work queue, see
> [`BACKLOG.md`](BACKLOG.md). For the next-agent onboarding, see
> [`../HANDOFF.md`](../HANDOFF.md). For upstream MKF blockers, see
> [`mkf-handoff.md`](mkf-handoff.md).

## Phase 0 — Bootstrap ✅ DONE

- Proteus tagged `frozen-2026-05-18`; `proteus-maintenance` branch created.
- `OpenConverters/Heaviside` repo, MIT, private.
- Submodules wired: `vendor/PyOpenMagnetics`, `MAS`, `PEAS`, `SAS`, `CAS`, `RAS`, `TAS`.
- `pyproject.toml` (Python 3.12), `uv` env, `.python-version`.
- CI: `ruff` + `mypy --strict` + unit tests + PyOpenMagnetics build-from-source (cached by submodule SHA) + BaseModel cap check (≤ 8).
- `heaviside/llm/` ported (`model_tiers.json` verbatim; Kimi default per AGENTS.md §8).
- Skeleton CLI: `heaviside version`, `heaviside topologies`.

## Phase 1 — Topology coverage + empirical probe ✅ DONE

- `make types` → quicktype → `TypedDict`s generated for MAS topology + core/coil/wire schemas.
- 27 topology modules under `heaviside/topologies/` (24 converters + 3 magnetics) — all < 100 LOC, all importable.
- `heaviside/spec/design_spec.py` — canonical `DesignSpec` (1 BaseModel).
- Empirical extras probe committed: `docs/probe-report.md` + `docs/extras-probe.json`.
- **Stencils + golden TAS:** 21 topologies bridge-ready (buck, boost, cuk, sepic, zeta, 4sbb, flyback, ssforward, 2sf, ACF, push_pull, PSFB, AHB, weinberg, DAB, LLC, CLLC, CLLLC, isobuck, isobb, vienna).
- **Upstream-blocked:** 3 topologies (series_resonant, power_factor_correction, cllc-standard-cores). See [`mkf-handoff.md`](mkf-handoff.md).

## Phase 2 — Regression baselines from TAS ✅ DONE

- 48 entries from `TAS/data/converters.ndjson` ingested as regression fixtures.
- `tests/regression/converters/test_converter_corpus.py` (51 tests) runs all 48 on every PR.
- Honest golden baseline: `golden_baseline.json` snapshots `{verdict, summary, per-check status}` per entry.
- Corpus size pinned to 48 in `test_corpus_size_matches_agents_rule_7`.

## Phase 3 — Realism gate ✅ DONE

- `heaviside/pipeline/realism.py` ports all 10 Proteus physics primitives with strict "throw on bad input" semantics per CLAUDE.md.
- Orchestrator `evaluate_tas(tas, *, topology, spec)` classifies every check as PASS / FAIL / NOT_APPLICABLE / UNAVAILABLE.
- CLI `heaviside design ... --realism` exits 6 on FAIL or INCOMPLETE (fail-closed).
- **19/21 topologies PASS** the realism gate with real spec input + MKF-designed magnetics + ngspice closed-loop simulation. 2 remaining: `cllc` (MKF standard-cores pipeline returns zero designs), `isolated_buck` (MKF timeout).
- Analytical extractors in `heaviside/pipeline/extract.py` for all 19 passing topologies + CLLC (enricher ready, blocked on MKF core design).

## Phase 4 — ngspice integration ✅ DONE

- `heaviside/sim/runner.py` — closed-loop simulation driver with iterative duty-cycle search until Vout matches spec target. Falls back to open-loop steady-state for resonant/bridge topologies.
- Topology-specific probe candidates, ACF topology transform + clamp IC rewrite, Weinberg V2 support.
- Steady-state simulator for topologies without PWM source.
- Sim results stamped into TAS for realism gate consumption (efficiency, power balance, Vout regulation).
- PyOM result caching (`heaviside/_pyom_cache.py`) keyed by `(call, args, pyom_sha)`.
- `scripts/corpus_run.py` exercises the full pipeline (MKF design → SPICE deck → ngspice sim → realism gate) across all 21 topologies unattended.

## Phase 5 — Component librarian + auditor 🚧 SCAFFOLDED

Port from Proteus. Proteus has 18 agents, 50+ knowledge files, 144K component entries at 72.35% audit pass rate. Heaviside needs the core data pipeline without the Strands/LangGraph scaffolding.

**What to port from Proteus:**
- `proteus/agents/component-librarian.md` + `component-selector.md` — prompts + tool definitions for populating TAS/data/ from datasheets and distributor APIs.
- `proteus/agents/component-auditor.md` — critical-field auditor (Isat, DCR, ESR, Coss, Vth, Qg, Qrr checks per component type).
- Digi-Key + Mouser fetcher layer (`heaviside/librarian/fetcher/`).
- Strict PDF datasheet reader (`heaviside/librarian/datasheet/`).
- MOSFET / diode / IGBT / capacitor / resistor converters.
- Repair-recipe artifact for auditor → librarian handoff.

**Heaviside-side scaffolding (already landed):**
- `heaviside/librarian/` directory with safe_access, TAS writer, auditor, repair, fetcher, datasheet reader.
- `heaviside/agents/` with factory, tools, two prompts (librarian + auditor).

**Exit criteria:** librarian run produces ≥ 1 new component per category from live API; auditor pass rate ≥ Proteus baseline (72.35%).

## Phase 6 — Agents + pipeline orchestration 🚧 IN PROGRESS

The Della Pollock pipeline: topology selection → magnetic-first design → component selection → simulation → adversarial review → retry on failure → learning.

**Agents to implement (from Proteus + article vision):**

| Agent | Role | Proteus source | Status |
|---|---|---|---|
| `topology-selector` | Given requirements, suggest 2-3 candidate topologies | `proteus/agents/converter-designer.md` (partial) | Prompt written, static fallback wired |
| `converter-designer` | Full pipeline orchestrator — magnetic first, grow converter around it | `proteus/agents/converter-designer.md` | **DONE** (`full_design.py` stages 1-4) |
| `magnetics-designer` | Inductor/transformer design via PyOpenMagnetics | `proteus/agents/magnetics-designer.md` | **DONE** (MKF direct calls in bridge.py) |
| `simulation-engineer` | Run and analyze ngspice, compare analytical vs sim | `proteus/agents/simulation-engineer.md` | **DONE** (sim/runner.py + sim/parasitics.py) |
| `component-selector` | FET, diode, capacitor selection from TAS DB | `proteus/agents/component-selector.md` | **DONE** (catalogue/selector.py + assemble.py) |
| `ray` | Adversarial reviewer — "the smartass colleague" | `proteus/agents/ray.md` | **DONE** (analytical `stage3b_gatekeeper`) |
| `nicola` | QA reviewer — checks every step against physics | `proteus/agents/nicola.md` | **DONE** (merged into gatekeeper) |
| `teacher` | Analyzes failed pipeline steps, feeds knowledge back to agents | New (from article) | **DONE** (pipeline/teacher.py) |

**Pipeline flow (implemented):**
```
spec → topology-selector → [up to 21 candidates]
  → stage2: MKF designs magnetic (fast Pareto, standard cores)
  → stage3: for each pick:
      1. decompose → SPICE netlist + TAS skeleton
      2. attach MKF magnetics to TAS
      3. assemble_bom_from_tas → select real MOSFET/diode/cap from TAS DB
         (stress-derived constraints: Vds, Id, Vrrm, If, V_rated, C, ESR)
      4. inject_parasitics → patch netlist with real Rds_on, Vf/RS, ESR
      5. simulate with real component parasitics (closed-loop or steady-state)
      6. realism gate (10 physics checks)
      7. gatekeeper review (Ray+Nicola: block on FAIL, warn on tight margins)
  → ranked by verdict + scoring, report generated
  → stage 5: teacher reviews all outcomes, extracts lessons to knowledge/lessons.ndjson
  → future runs query lessons to warn on topologies with recent failures
```

**Teacher (learning loop):**
- `pipeline/teacher.py`: analyzes DesignOutcome failures, extracts structured Lesson objects
- Categories: `realism_fail`, `margin_violation`, `design_failure`, `component_unavailable`, `simulation_failure`, `check_unavailable`, `gatekeeper_block`, `missing_spec_field`
- Each lesson has: topology, severity, detail, spec fingerprint, actionable suggestion, TTL
- NDJSON store at `knowledge/lessons.ndjson` (append-only, deduplicated by ID)
- Pipeline queries lessons before Stage 2: warns on topologies with recent failures
- CLI: `heaviside lessons --severity error --suggestions` to inspect the store

**Component selection coverage:**
- Stress derivers: all 21 topologies (`pipeline/stress.py`)
- MOSFET selector: tiebreakers (lowest_rds_on, lowest_qg, highest_vds_margin, highest_id_margin)
- Diode selector: tiebreakers (lowest_vf, lowest_qrr, highest_vrrm_margin, highest_if_margin)
- Capacitor selector: tiebreakers (lowest_esr, highest_ripple_headroom, highest_voltage_margin, highest_capacitance)
- Parasitic injection: Rds_on → SW model RON, Vf → DIDEAL RS, ESR → series R on Cout

**Exit criteria:** ~~end-to-end `heaviside design <spec.json>` runs through the agent loop and produces a realism-passing design + report without human intervention.~~ **MET:** `heaviside auto-design spec.json` runs full pipeline. 6/8 topologies PASS with real BOM for a 48→12V 60W spec.

## Phase 7 — Knowledge port ✅ DONE

Ported 54 knowledge files (33K lines) from Proteus across 11 categories:

- `knowledge/topologies/` — 25 files: topology guides + selection guide + resonant theory
- `knowledge/magnetics/` — 7 files: design guide, PyOpenMagnetics API, PSMA resources
- `knowledge/simulation/` — 6 files: ngspice guide, RMS waveforms, DAB/LLC sim lessons
- `knowledge/components/` — 6 files: selection guide, GaN design, switching-loss models
- `knowledge/control/` — 3 files: feedback loop design, digital control, Ridley resources
- `knowledge/emc/` — 2 files: EMI design guide + input filter design
- `knowledge/gate-drive/`, `knowledge/thermal/`, `knowledge/protection/`, `knowledge/reliability/`, `knowledge/pcb-layout/` — 1 file each

Proteus→Heaviside references cleaned. Agent prompts reference knowledge files. `topology-selector.md` wired to topology-selection-guide.

**Exit criteria:** ~~every agent prompt references at least one knowledge file; knowledge coverage matches or exceeds Proteus baseline.~~ **MET:** 54 files vs Proteus's 121 (excluded: 58 dated trainer-lessons, 9 schema/reading-list files). All engineering content preserved.

## Phase 7b — CRE + CR Pipelines ✅ DONE

Ported from Proteus's competitor reverse-engineering and cross-reference pipelines.

**CRE (Competitor Reverse-Engineering) pipeline:**
```
PDF → competitor agent (extract specs) → reverse-engineer agent (extract BOM)
  → verify MPNs in TAS → full_design pipeline (design competing converter)
  → reviewer agent (adversarial + quality review)
```
- CLI: `heaviside reverse-engineer "TI TIDA-050072" --pdf path/to/pdf`
- API: `POST /cre`
- MCP: `reverse_engineer` tool
- Agent prompts: `reverse-engineer.md`, `competitor.md`, `reviewer.md`

**CR (Cross-Reference) pipeline:**
```
source BOM → prefetch TAS candidates → LLM cross-referencer (constrained)
  → engineering guardrails (10 checks) → match scoring → sourcing annotation
  → Otto challenge (Würth-specific) → reviewer → self-audit
```
- CLI: `heaviside crossref bom.json --mfr "Wurth"`
- API: `POST /crossref`
- MCP: `cross_reference` tool
- Agent prompts: `cross-referencer.md`, `otto.md`, `crowbar.md`, `hatchet.md`

**Deterministic backbone (6 modules):**
- `pipeline/verdict.py` — LLM verdict parsing (APPROVED/REJECTED/PROCEED/BLOCK)
- `pipeline/pdf_extract.py` — PDF text + table extraction (pdfplumber)
- `pipeline/value_parse.py` — SI value parsing (capacitance, inductance, resistance, voltage)
- `pipeline/match_score.py` — substitution quality scoring
- `pipeline/guardrails.py` — 10 engineering guardrails (G0-G6, GAECQ, GFoot, GStack)
- `pipeline/sourcing.py` — distributor cost/stock annotation
- `agents/llm_call.py` — lightweight OpenAI-compatible LLM caller

**Key architectural decisions:**
- LLM only where judgment is needed (PDF extraction, crossref selection, review). All validation is deterministic.
- CRE designs via existing `full_design()` pipeline, not LLM-generated netlists.
- Cross-referencer operates on constrained TAS candidates (prefetched), not open-ended LLM memory.
- Prompts trimmed from Proteus bloat: `reverse-engineer.md` 533K→5K, `ray.md`+`nicola.md` 231K→5K (merged into `reviewer.md`).

## Phase 8 — Surfaces 🚧 IN PROGRESS

**REST API (DONE):**
- `heaviside serve` → FastAPI + uvicorn on port 8000
- `POST /design` — full auto-design pipeline (spec → ranked outcomes with BOM)
- `POST /design/magnetic` — magnetic-only design for a given topology
- `POST /design/bom` — BOM selection for a given topology + spec
- `POST /design/report` — full pipeline → HTML report for best outcome
- `GET /topologies` — list registered topologies
- `GET /health` — liveness check

**HTML Report (DONE):**
- `heaviside auto-design spec.json --report report.html`
- `heaviside.report.render_html(outcome)` → self-contained HTML with:
  - Magnetic section (core, windings, scoring)
  - BOM section (MPN, manufacturer, tiebreaker, margins)
  - Realism gate (10 checks with values + margins, tight-margin highlighting)
  - Gatekeeper review (objections + warnings)
  - Diagnostics

**MCP Server (DONE):**
- `heaviside serve --mcp` → MCP stdio server
- Tools: `design_magnetic`, `design_bom`, `list_topologies`, `query_lessons`
- Claude Code / external agents can call the pipeline directly

**Remaining:**
- PDF rendering: not started (HTML is sufficient for v0.1).

**Exit criteria:** ~~`heaviside serve --api` and `heaviside serve --mcp` both work.~~ **MET.** Both API and MCP servers work.

## v0.1.0 release

After Phase 8. Tag `v0.1.0`. Public-private decision revisited.

## Cross-cutting tracks (always running)

- **Upstream MKF bugs.** 2 topologies still blocked: `isolated_buck` (timeout in MagneticAdviser), `cllc` (standard-cores pipeline returns zero designs). See [`mkf-handoff.md`](mkf-handoff.md).
- **Enricher coverage.** Loss-budget analysts added for 20/21 topologies. Sim probe quadruples added for push_pull, weinberg, AHB, PSFB, PSHB, DAB, LLC-family. Remaining UNAVAILABLE checks are mostly `thermal_limit` (needs full thermal model) and sim-dependent checks for topologies whose MKF decks are missing probe-able nodes.
- **TAS data debt.** 39,907 entries failing audit (magnetics: 32K need Isat/DCR; capacitors: 5K need ESR/rippleCurrent). Repair path is through the Phase 5 librarian.
- **BaseModel cap.** `scripts/check_pydantic_cap.py` enforces ≤ 8. Today: `DesignSpec` is 1.
