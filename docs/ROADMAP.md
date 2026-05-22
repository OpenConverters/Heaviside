# Heaviside Roadmap

Seven phases from bootstrap to v0.1. Each phase ends with a CI-green commit.
**Status reflects HEAD as of 2026-05-22.** When status drifts from reality,
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
- **Stencils + golden TAS:** 13 topologies bridge-ready (buck, boost, cuk, sepic, zeta, 4sbb, flyback, ssforward, 2sf, ACF, push_pull, PSHB, PSFB, AHB, weinberg, DAB, LLC, CLLLC, isobuck, isobb).
- **Upstream-blocked:** 4 topologies (cllc, vienna, power_factor_correction, series_resonant). See [`mkf-handoff.md`](mkf-handoff.md). Each has the Heaviside-side stencil/golden/binding/drift-row ready to land the moment MKF unblocks it.

## Phase 2 — Regression baselines from TAS ✅ DONE

- 48 entries from `TAS/data/converters.ndjson` ingested as regression fixtures.
- `tests/regression/converters/test_converter_corpus.py` (51 tests) runs all 48 on every PR.
- Honest golden baseline: `golden_baseline.json` snapshots `{verdict, summary, per-check status}` per entry. Today's verdict is INCOMPLETE on every populated entry (no real component data attached → checks UNAVAILABLE). When the librarian populates real components, the guard test `test_entry_is_evaluable_or_explicitly_empty` trips and the golden must be regenerated in the same reviewed commit via `python -m tests.regression.converters.regen_golden`.
- Corpus size pinned to 48 in `test_corpus_size_matches_agents_rule_7`.

## Phase 3 — Realism gate ✅ v0.1 DONE

- `heaviside/pipeline/realism.py` ports all 10 Proteus physics primitives (power balance, voltage derating ×3, Isat margin, duty cycle bounds, no-negative-losses, thermal, efficiency, Vout regulation) with strict "throw on bad input" semantics per CLAUDE.md.
- Orchestrator `evaluate_tas(tas, *, topology, spec)` classifies every check as PASS / FAIL / NOT_APPLICABLE / UNAVAILABLE — nothing silently skipped. Verdict: PASS / FAIL / INCOMPLETE.
- CLI `heaviside design ... --realism` is opt-in and exits 6 on FAIL or INCOMPLETE (fail-closed, no `--force`).
- **19 topologies have analytical extractors that produce PASS verdicts** with real spec input: buck, boost, flyback, cuk, sepic, zeta, single-switch forward, two-switch forward, active-clamp forward, push-pull, four-switch buck-boost, asymmetric half-bridge, weinberg, isolated buck (flybuck), isolated buck-boost, LLC, phase-shifted full bridge, DAB, CLLLC.
- Boundary tests in `tests/realism/`; per-extractor tests under `tests/unit/test_extract_*.py` (300+ tests).
- BOM stress / TAS-component checks gated on the librarian populating real components (Phase 5).

**Phase 3 v0.1 closeout TODO** (carried into Phase 5/6 enrichment, not blocking the phase exit):
- librarian agent populates `vds_rated` / `vrrm_rated` / `v_rated` on TAS components for voltage derating;
- analyst agent computes Tj for `thermal_limit`;
- sim agent populates `simulation_results` / `loss_budget` for the remaining 4 checks.

## Phase 4 — ngspice integration 🚧 NOT STARTED

- `heaviside/sim/` exists but is empty.
- PyOM result caching landed early (`heaviside/_pyom_cache.py`, commit e6a6d8c) — keyed by `(call, args, pyom_sha)`, ready to back the sim runner.
- Pending: wrap `PyOpenMagnetics.export_magnetic_as_subcircuit()` + `MKF.generate_ngspice_circuit()`; sandboxed ngspice runner; analytical-vs-sim delta tracking across the 48 regression designs.

**Exit criteria:** 48/48 simulate without convergence failure; analytical/sim delta < 5 % on efficiency, ripple, and stresses for ≥ 40/48.

**Hard prerequisite:** integration-test caching strategy (currently on BACKLOG "Next"). End-to-end PyOM runs are 1–2 min each; without `(topology, spec_hash, pyom_sha)` → MAS JSON disk cache, CI will not scale past Phase 4.

## Phase 5 — Component librarian + auditor 🚧 ACTIVE

Highest-velocity phase right now. Slices landed (newest first):

- **Slice H** — Kimi-family model ids routed through Moonshot builder in `load_agent` (`heaviside/agents/factory.py`).
- **Slice G** — env-gated live Kimi smoke test + strict-mode credentials.
- **Slice F** — repair-recipe artifact for auditor → librarian handoff (`heaviside/librarian/repair.py`).
- **Slice E** — strict-mode datasheet PDF reader (`heaviside/librarian/datasheet/`).
- **Slice D1/D2/D3** — Digi-Key + Mouser fetcher layer, strict-mode MOSFET / diode / IGBT / capacitor / resistor converters, staging layer.
- **Earlier** — Strands agent scaffold + `component-librarian` / `component-auditor` prompts, TAS writer with strict schema validation, `safe_access` (lockfile + Transaction) port, pipeline-critical-field auditor port.

**Status of librarian by component:**

| Module | State |
|---|---|
| `librarian/safe_access.py` | ✅ Lockfile + Transaction port |
| `librarian/tas.py` | ✅ Strict schema-validating writer |
| `librarian/auditor.py` | ✅ Critical-field auditor port |
| `librarian/repair.py` | ✅ Repair-recipe artifacts |
| `librarian/fetcher/{digikey,mouser}.py` | ✅ Strict-mode API fetchers |
| `librarian/fetcher/staging.py` | ✅ Staging layer |
| `librarian/fetcher/convert.py` | ✅ MOSFET / diode / IGBT / cap / resistor converters |
| `librarian/datasheet/{reader,extract,cache,patterns}.py` | ✅ Strict PDF reader pipeline |
| **Vendor scrape fallback** | ❌ Not ported. Decision pending: APIs-only or carve a documented headed-Playwright exemption for PerimeterX-protected pages. |
| **End-to-end live run** | ❌ Not exercised yet on real catalogue. |

**Exit criteria:** librarian run produces ≥ 1 new component per category from live API; auditor pass rate ≥ Proteus baseline (72.35 %).

## Phase 6 — Agents 🚧 SCAFFOLDED

- Strands agent layer wired in `heaviside/agents/` (`factory.py`, `tools.py`).
- Two prompts in place: `component-librarian.md`, `component-auditor.md` (out of the 10–12 target).
- Per-agent steering + `allowed_tools` per AGENTS.md §"Adding agents".
- Agent evals (`tests/evals/`) gated on API key (not blocking PRs without secret).

**Exit criteria:** end-to-end `heaviside design <spec.json>` runs through the agent loop and produces a realism-passing design + report. Requires Phase 4 (sim) + a `converter-designer` agent + a reviewer agent (`ray` or `nicola`).

## Phase 7 — Surfaces 🚧 NOT STARTED

- `api/components/` scaffold present; no FastAPI server yet.
- `heaviside/report/` empty (HTML/PDF rendering not started).
- MCP server surface: not started.

**Exit criteria:** `heaviside serve --api` and `heaviside serve --mcp` both work; demo report from one TAS reference design committed to `docs/`.

## v0.1.0 release

After Phase 7. Tag `v0.1.0`. Public-private decision revisited.

## Cross-cutting tracks (always running)

- **Upstream MKF bugs.** 4 topologies blocked, all owned by the maintainer (alf). See [`mkf-handoff.md`](mkf-handoff.md) for per-bug repros + fix sketches. The Phase 1 exit is structurally a 20/24 until these land.
- **TAS data debt.** `tests/regression/tas/test_semiconductor_wrap.py` is a red CI gate by design: diodes + IGBTs pass cleanly, mosfets fails on 7603 rows (legacy flat `{"mosfet": {...}}` shape) plus 3 unresolved merge-conflict markers at lines 2802/2806/2810. Repair path is through the Phase 5 librarian — do NOT edit `TAS/data/*.ndjson` directly.
- **BaseModel cap.** `scripts/check_pydantic_cap.py` enforces ≤ 8. Today: `DesignSpec` is 1. Budget intentionally tight — if you want a 9th, fix the schemas instead.
