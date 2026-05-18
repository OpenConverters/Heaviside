# Heaviside Roadmap

Seven phases from bootstrap to v0.1. Each phase ends with a CI-green commit.

## Phase 0 — Bootstrap ✅ (in progress)

- Tag Proteus `frozen-2026-05-18`, create `proteus-maintenance` branch.
- New repo `OpenConverters/Heaviside` (private, MIT).
- Submodules: `vendor/PyOpenMagnetics`, `MAS`, `PEAS`, `SAS`, `CAS`, `RAS`, `TAS`.
- `pyproject.toml` (Python 3.12 only), `uv` env, `.python-version`.
- CI: `ruff` + `mypy --strict` + unit tests + PyOpenMagnetics build-from-source (cached by submodule SHA) + BaseModel cap check (≤ 8).
- `heaviside/llm/` ported from Proteus (`model_tiers.json` verbatim).
- Skeleton CLI (`heaviside version`, `heaviside topologies`).

**Exit criteria:** CI green on `main`. One unit test passing. README, AGENTS.md, LICENSE in place.

## Phase 1 — Topology coverage + empirical probe

- `make types` → quicktype → `TypedDict`s for all 25 MAS topology schemas + MAS core/coil/wire schemas.
- One `heaviside/topologies/<name>.py` module per MKF topology (24 total). Each is < 100 LOC.
- `heaviside/spec/design_spec.py` — the canonical `DesignSpec` (1 BaseModel).
- Empirical probe: a CI job that calls `PyOpenMagnetics.process_converter()` for each of the 24 topologies with a minimal valid spec, recording which raise `NotImplementedError` vs. which return a result.
- Add missing per-topology bindings in `vendor/PyOpenMagnetics/` for the 15 topologies that currently lack them.

**Exit criteria:** all 24 topologies importable; probe report committed to `docs/probe-report.md`; bindings present for ≥ 20 of 24 (LLC, DAB, PSFB, Cuk, SEPIC, Zeta, AHB, Vienna at minimum).

## Phase 2 — Regression baselines from TAS

- Ingest the 48 designs in `TAS/data/converters.ndjson` as regression fixtures.
- For each, run the design pipeline analytically (no ngspice yet) and store a golden output.
- `tests/regression/` runs all 48 on every PR; tolerances explicit per field.

**Exit criteria:** 48/48 regression tests passing; baseline JSONs committed.

## Phase 3 — Realism gate

- Port physics validators from `proteus/validators/physics.py` → `heaviside/pipeline/realism.py`.
- Fail-closed, no overrides. Boundary tests in `tests/realism/`.
- BOM stress checks against TAS components.

**Exit criteria:** every regression baseline emits a passing realism report; intentional bad designs in `tests/realism/` correctly rejected.

## Phase 4 — ngspice integration

- `heaviside/sim/` wraps `PyOpenMagnetics.export_magnetic_as_subcircuit()` + `MKF.generate_ngspice_circuit()`.
- ngspice runner with sandboxed execution.
- Compare ngspice vs. analytical results across the 48 regression designs; track delta.

**Exit criteria:** 48/48 simulate without convergence failure; analytical/sim delta < 5 % on efficiency, ripple, and stresses for ≥ 40/48.

## Phase 5 — Component librarian + auditor

- Port `librarian_tas.py`, `librarian_safe_access.py`, `vendor_scrape.py`, `vendor_enrich.py`, `component_auditor_focused.py`.
- Librarian writes on every run (no dry-run-only mode); auditor blocks PRs on critical-field gaps.

**Exit criteria:** librarian run produces ≥ 1 new component per category from live API; auditor pass rate ≥ current Proteus baseline (72.35 %).

## Phase 6 — Agents

- Consolidate Proteus's 30 prompts into 10–12 Strands agents in `agents/prompts/`.
- Per-agent steering + allowed_tools in `agents/steering/`.
- Agent evals in `tests/evals/` (gated on API key; not blocking on PRs without secret).

**Exit criteria:** end-to-end `heaviside design <spec.json>` runs through the agent loop and produces a realism-passing design + report.

## Phase 7 — Surfaces

- FastAPI in `api/` (REST + WebSocket for streaming agent traces).
- MCP server exposing the same tools.
- HTML/PDF report rendering in `heaviside/report/`.

**Exit criteria:** `heaviside serve --api` and `heaviside serve --mcp` both work; demo report from one TAS reference design committed to `docs/`.

## v0.1.0 release

After Phase 7. Tag `v0.1.0`. Public-private decision revisited.
