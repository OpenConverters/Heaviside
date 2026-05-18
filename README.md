# Heaviside

**PyOpenMagnetics-first, agent-driven power electronics design system.**

Heaviside is the second-generation successor to [Proteus](https://github.com/OpenConverters/Proteus). It keeps the catalogue, the realism gate, the agent loop, and the regression discipline; it discards manual magnetics calculations, parallel UIs, and one-off scripts.

## Principles

1. **PyOpenMagnetics is the engine.** Every magnetic component (inductor, transformer, coupled inductor, common-mode choke) is designed by `PyOpenMagnetics`. Heaviside never reimplements core selection, winding optimisation, loss models, or netlist generation.
2. **Schemas are the type system.** MAS / PEAS / SAS / CAS / RAS define the data model. Heaviside generates `TypedDict`s from the JSON schemas via `quicktype` and uses them everywhere. The codebase is capped at **≤ 8** `pydantic.BaseModel` classes (enforced in CI).
3. **24 topologies, one entry point.** Every topology supported by `MKF/src/converter_models/` is reachable through a uniform `heaviside.topologies.<name>.design()` call, which dispatches to `PyOpenMagnetics.process_converter()` (or the topology-specific binding when available).
4. **Realism gate is fail-closed.** No design leaves the pipeline that violates ratings, stresses, thermal limits, or component availability. There are no overrides.
5. **Reproducibility from day 1.** CI runs `ruff` + `mypy --strict` + unit tests + analytical regression against the 48 designs in `TAS/data/converters.ndjson` on every PR.

## Status

`v0.1.0.dev0` — bootstrapping. See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the seven-phase delivery plan.

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

## Relationship to Proteus

Proteus is frozen at tag `frozen-2026-05-18`. Only TAS-backfill commits are permitted on the `proteus-maintenance` branch. **Heaviside does not import from Proteus.** Every piece of functionality is reimplemented from first principles against PyOpenMagnetics and the schema submodules.

## License

MIT — see [`LICENSE`](LICENSE).
