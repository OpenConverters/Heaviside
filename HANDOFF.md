# Heaviside — Handoff for the next agent

**Date:** 2026-05-22
**Branch:** `main`
**Last commit:** `a744c10 fix(mosfets): align code with wrapped semiconductor envelope schema`
**Autonomy:** per `AGENTS.md`, commit pre-granted, push requires explicit ask.

Read [`AGENTS.md`](AGENTS.md) first if you haven't. Then [`docs/ROADMAP.md`](docs/ROADMAP.md) for phase status and [`docs/BACKLOG.md`](docs/BACKLOG.md) for the ordered work queue. This file is the "where we are, where to go next" snapshot.

## Where we are

- **Phases 0–3 are done.** Bootstrap, topology coverage (20/24 stenciled, 4 blocked upstream), 48-design regression corpus, and realism gate v0.1 (19 PASS-capable topologies, 10 physics primitives, fail-closed).
- **Phase 5 (librarian + auditor) is the active phase.** Slices A–H landed. Strands agent scaffold, fetcher layer, strict-mode converters, datasheet PDF reader, repair-recipe artifact, Kimi smoke test, MOSFET semiconductor-envelope alignment. End-to-end live-API run not yet exercised.
- **Phase 4 (sim) and Phase 7 (surfaces) have not started.** `heaviside/sim/` and `heaviside/report/` are skeletons. PyOM result cache (`_pyom_cache.py`) is in place and will back the sim runner.
- **Phase 6 (agents) is scaffolded.** 2/12 prompts in place (`component-librarian`, `component-auditor`). Strands routing through `load_agent` wires Kimi as default.

## Pick the top unblocked item

In priority order. The autonomous default per AGENTS.md is: do (1), commit, do (2), commit. Don't check in.

### 1. Fire the librarian end-to-end on a real catalogue slice

Phase 5's exit criterion is "librarian run produces ≥ 1 new component per category from live API; auditor pass rate ≥ Proteus baseline (72.35 %)." All the parts are in `heaviside/librarian/{fetcher,datasheet,...}`; the integration test is what's missing.

- Pick one category (suggest: capacitors — smallest blast radius, biggest gap from Proteus's 88.6 %).
- Drive the fetcher → staging → convert → TAS-writer pipeline against ≤ 50 MPNs.
- Run `heaviside/librarian/auditor.py` and compare pass rate to Proteus's published baseline.
- If anything in the chain has a `# TODO` or a silent-skip path, **throw** instead per CLAUDE.md "no fallbacks" rule.

Acceptance: a regression test in `tests/regression/librarian/` that captures the new components' shape and the auditor's reported pass rate. Commit per category.

### 2. Fix the red CI gate on mosfets.ndjson

`tests/regression/tas/test_semiconductor_wrap.py` is failing on 7603 rows in `TAS/data/mosfets.ndjson` plus 3 unresolved git-merge-conflict markers at lines 2802 / 2806 / 2810. The repair script `TAS/scripts/wrap_semiconductor_data.py` is idempotent and creates `.pre_semiconductor_wrap.bak` backups.

**Do not edit `TAS/data/*.ndjson` directly** — that's a Proteus AGENTS.md guardrail Heaviside inherits. The repair runs through the librarian.

Acceptance: the test goes green, the conflict markers are gone, the `.bak` is in place, and the TAS submodule bump references the wrap commit.

### 3. Integration-test caching strategy

Phase 4 hard prerequisite. PyOM end-to-end runs are 1–2 min each; the suite will not scale past 24 topologies × N specs without a disk cache keyed by `(topology, spec_hash, pyom_sha)` → MAS JSON. `_pyom_cache.py` already caches by `(call, args, pyom_sha)`; the question is whether to extend it or build a sibling cache at the integration-test layer.

Acceptance: documented design note in `docs/`, prototype on one slow integration test (probably `test_bridge_integration.py::test_acf_end_to_end` at ~134 s), measured speed-up.

### 4. LLC end-to-end verification — *blocked upstream*

`L_r → seriesInductor` binding for LLC is an assumption from MKF source, not a runtime-verified fact. The `tests/integration/test_bridge_integration.py` LLC test is skipped behind a subprocess canary because `design_magnetics_from_converter('llc', ...)` segfaults upstream. The canary will auto-enable the test the moment the segfault is fixed. **Watch [`docs/mkf-handoff.md`](docs/mkf-handoff.md)** — when LLC unblocks, this jumps to #1.

### 5. Backfill closeout for Phase 3 v0.1

- librarian agent populates `vds_rated` / `vrrm_rated` / `v_rated` on TAS components for voltage derating checks;
- analyst agent computes `Tj` for `thermal_limit`;
- sim agent populates `simulation_results` / `loss_budget` for the remaining 4 checks.

This is interleaved with (1). Each new field lets a regression entry move from INCOMPLETE → PASS in `golden_baseline.json`; **regenerate the golden in the same reviewed commit** via `python -m tests.regression.converters.regen_golden`.

## What NOT to start yet

- **The remaining 4 topology stencils** (`cllc`, `series_resonant`, `vienna`, `power_factor_correction`). All four are upstream-blocked in MKF — Heaviside-side stencil/golden/binding/drift-row infrastructure is ready and waiting. See [`docs/mkf-handoff.md`](docs/mkf-handoff.md) for the per-bug fix sketches.
- **Phase 7 surfaces** (FastAPI, MCP, HTML report). Premature until Phase 4 sim + Phase 6 agent loop close.
- **Vendor scraping (headed Playwright) fallback.** Conflicts with the global CLAUDE.md "Playwright: always run headless" rule. Decision pending from the user: APIs-only, or carve a documented exemption for the human-primed PerimeterX profile. Don't ship a scraper without that decision.
- **Adding a 9th `pydantic.BaseModel`.** CI gate enforces ≤ 8. If the urge appears, fix the schemas instead.

## Open structural risks (track, don't fix solo)

1. **Upstream MKF blockers concentrate on alf.** 4 topologies, 5 bugs (LLC segfault, SRC behavioural bridge, CLLC unknown topology, PFC unknown topology, Vienna `at() with string`). All blocking, all owned by one person. See [`docs/mkf-handoff.md`](docs/mkf-handoff.md).
2. **TAS data quality is the rate-limiting constraint** for Phase 5 exit. Heaviside inherits Proteus's 72.35 % auditor pass rate as the floor — that floor is the gate.
3. **Phase 4 (ngspice) gated on caching.** Without it, CI minutes blow up the moment sim lands.
4. **Agent-layer non-determinism has no mandatory CI gate.** Agent evals are API-key-gated → not blocking on secret-less PRs. Fine for now; revisit before Phase 6 exit.

## Conventions for the next commit

- Commit style per existing log: `feat(scope): summary`, `fix(scope): ...`, `tests(scope): ...`, `docs(...)`. Slices use single-letter monikers (`D1`, `E`, `F`, `H`). Stay bisectable.
- Push requires explicit user ask in the current turn. Don't push.
- Honest reporting > fake-green: an INCOMPLETE verdict that becomes PASS on real data is better than a PASS that papers over UNAVAILABLE checks. The regression baseline is built around this; don't break the contract.

## Quick orientation commands

```bash
# state of the world
git log --oneline -20
cat docs/BACKLOG.md          # ordered work queue
cat docs/ROADMAP.md          # phase status
cat docs/mkf-handoff.md      # upstream bugs

# CI gate locally
ruff check && mypy --strict heaviside && pytest -m unit

# regression sweep
pytest tests/regression/converters tests/regression/tas

# golden regeneration (only after a legitimate librarian repair)
python -m tests.regression.converters.regen_golden
```

Good luck. The plan is sound, the codebase is honest, the next step is to fire the librarian.
