# Heaviside

**PyOpenMagnetics-first, agent-driven power electronics design system.**

Heaviside is the second-generation successor to [Proteus](https://github.com/OpenConverters/Proteus). It keeps the catalogue, the realism gate, the agent loop, and the regression discipline; it discards manual magnetics calculations, parallel UIs, and one-off scripts.

## Principles

1. **PyOpenMagnetics is the engine.** Every magnetic component (inductor, transformer, coupled inductor, common-mode choke) is designed by `PyOpenMagnetics`. Heaviside never reimplements core selection, winding optimisation, loss models, or netlist generation.
2. **Schemas are the type system.** MAS / PEAS / SAS / CAS / RAS define the data model. Heaviside generates `TypedDict`s from the JSON schemas via `quicktype` and uses them everywhere. The codebase is capped at **≤ 8** `pydantic.BaseModel` classes (enforced in CI).
3. **24 topologies, one entry point.** Every topology supported by `MKF/src/converter_models/` is reachable through a uniform `heaviside.topologies.<name>.design()` call, which dispatches to `PyOpenMagnetics.process_converter()` (or the topology-specific binding when available).
4. **Realism gate is fail-closed.** No design leaves the pipeline that violates ratings, stresses, thermal limits, or component availability. There are no overrides.
5. **Reproducibility from day 1.** CI runs `ruff` + `mypy --strict` + unit tests + analytical regression against the 48 designs in `TAS/data/converters.ndjson` on every PR.

## What works today

Heaviside runs as a local web bench (`uvicorn heaviside.api:app`, then open `http://localhost:8000`) with four surfaces:

- **Cross-Reference** — upload a BOM (CSV/XLSX) or a reference-design PDF/URL and re-source it to a target manufacturer. An LLM column-mapper understands messy PLM exports; a reverse-engineering front (`RE = spec_extract + reverse_engineer + verify + sim`) simulates the reference so substitutes are ranked against **real per-component stress**, not nameplate. Every substitution carries a **deterministic per-parameter rationale** (value / voltage / package → exact / exceeds / deviates) so each `recommended`/`partial` is auditable. Beats the previous Proteus system on all 10 Würth reference designs. Exportable as a PDF report.
- **Converter Designer** — minimal input (Vin + output rails); the designer sweeps switching frequency against the magnetic's total loss, sizes the inductor via MKF, builds a full BOM from real TAS parts, simulates it in MKF SPICE, and runs a Ray + Nicola adversarial review.
- **Jobs** — every run is a job with a live per-stage pipeline view; results persist to disk (survive restart) and have shareable `#/jobs/<id>` URLs.
- **TAS Catalog** — browse the component database.

High-risk LLM stages are gated by a **Ray + Nicola review-and-retry** loop (produce → review → re-run with their objections if rejected). See [`docs/cross-reference.md`](docs/cross-reference.md).

## Status

`v0.1.0.dev0`. See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the delivery plan.

## Layout

```
heaviside/
  spec/             User-facing DesignSpec (the single canonical Pydantic model)
  topologies/       One thin module per MKF topology (24 total)
  types/_generated/ TypedDicts from MAS/PEAS/SAS/CAS/RAS (do not edit by hand)
  components/       TAS query + librarian write path
  pipeline/         Realism gate, loss budget, BOM assembly
  sim/              ngspice runner + PyOpenMagnetics SPICE subcircuit export
  knowledge/        Curated lessons / topology guidance for agents
  report/           Design report rendering (HTML/PDF)
  llm/              Model factory, tiers, cache, context guard
agents/
  prompts/          10–12 consolidated agent prompts
  steering/         Per-agent steering / tool allow-lists
  evals/            Agent-level eval suites
api/                FastAPI + MCP surfaces
data/               Local-only runtime artefacts (gitignored except seeds)
tests/
  unit/             Fast pure-Python
  property/         Hypothesis
  realism/          Realism-gate boundary conditions
  regression/       48 TAS converter designs as golden baselines
  integration/      End-to-end via PyOpenMagnetics + ngspice
  evals/            Agent evals (gated on API key)
docs/               Architecture, ADRs, migration log
vendor/PyOpenMagnetics  Submodule, built from source in CI with SHA cache
```

## Prerequisites

- Python 3.12 (pinned in `.python-version`)
- `ngspice` on `PATH`
- `uv` for environment management (`pip install uv` or via your package manager)
- `git` with SSH access to `OpenConverters/*`, `OpenMagnetics/*`, `Power-Supply-Manufacturers-Association/PEAS`

## Quick start

```bash
git clone --recurse-submodules git@github.com:OpenConverters/Heaviside.git
cd Heaviside
uv venv && source .venv/bin/activate
uv pip install -e '.[dev]'
make types          # regenerate TypedDicts from schema submodules
pytest -m unit      # ~seconds
```

### Decomposer regression suite needs the vendor PyOpenMagnetics wheel

`uv pip install -e '.[dev]'` pulls the PyPI `PyOpenMagnetics` wheel, which currently lacks the `bridge_simulation_mode` argument on `generate_ngspice_circuit`. Every bridge-topology regression test (`test_llc`, `test_dual_active_bridge`, `test_phase_shifted_full_bridge`, `test_clllc`, `test_weinberg`, `test_active_clamp_forward`, …) needs the vendor build that ships with this repo:

```bash
cd vendor/PyOpenMagnetics
python -m build --wheel        # produces dist/PyOpenMagnetics-*.whl
pip install --force-reinstall dist/PyOpenMagnetics-*.whl
cd -
```

Heaviside's decomposer detects the mismatch at import time and throws a `DecomposerError` with the exact install command — if you see that error, run the snippet above. See `HANDOFF.md` for upstream-regression details.

## Relationship to Proteus

Proteus is frozen at tag `frozen-2026-05-18`. Only TAS-backfill commits are permitted on the `proteus-maintenance` branch. **Heaviside does not import from Proteus.** Every piece of functionality is reimplemented from first principles against PyOpenMagnetics and the schema submodules.

## License

MIT — see [`LICENSE`](LICENSE).
