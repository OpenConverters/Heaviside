# Heaviside AGENTS.md

**Read this before touching the repo. It encodes the design rules that make Heaviside different from Proteus.**

## What Heaviside is

A PyOpenMagnetics-first, agent-driven design system for power converters. It takes a `DesignSpec`, picks a topology (or accepts one), designs the magnetics through `PyOpenMagnetics`, selects the rest of the BOM against `TAS/data/`, runs `ngspice` with real frequency-dependent magnetic subcircuits, and emits a stress-checked, realism-gated report.

## What Heaviside is NOT

- **Not a magnetics calculator.** Never compute turns, inductance, core size, wire gauge, AC/DC losses, or hysteresis losses by hand. Call `PyOpenMagnetics`.
- **Not a netlist synthesiser.** MKF's `generate_ngspice_circuit()` produces every topology's netlist. Heaviside wraps it.
- **Not a parallel UI playground.** v0.1 has exactly three surfaces: CLI, MCP server, FastAPI. No TUI.
- **Not Proteus.** No code is imported from Proteus. Proteus is frozen at `frozen-2026-05-18`; Heaviside reimplements from first principles against the schemas.

## Hard rules

### 1. PyOpenMagnetics is the engine

```python
import PyOpenMagnetics as P
result = P.design_magnetics_from_converter(topology, converter, n_solutions, core_set, use_ngspice, models)
```

Forbidden: textbook formulas, "typical" coefficients, fallback constants. If PyOpenMagnetics rejects the inputs, **raise** — do not patch the inputs to make it accept them. See the [CLAUDE.md "no fallbacks" rule](../../home/alf/.claude/CLAUDE.md).

### 2. Schemas are the type system

| Submodule | Defines |
|-----------|---------|
| `MAS/`    | Magnetic components (cores, coils, wires, materials) |
| `PEAS/`   | Passive electrical components (capacitors, resistors) — via CAS/RAS |
| `SAS/`    | Semiconductor components (MOSFETs, diodes, IGBTs, controllers) |
| `CAS/`    | Capacitor schema |
| `RAS/`    | Resistor schema |
| `TAS/`    | Component **data** (the NDJSON catalogue) |

Heaviside generates `TypedDict`s from these via `make types` → `heaviside/types/_generated/`. **Do not edit `_generated/` by hand.** Regenerate.

### 3. ≤ 8 `pydantic.BaseModel` classes in `heaviside/*`

Enforced in CI (`scripts/check_pydantic_cap.py`). Reserved for genuine user-facing boundaries (`DesignSpec`, config, MCP/FastAPI request bodies). Internal data is `TypedDict`. If you want a 9th, fix the schemas.

### 4. 24 topologies — all of them

Every topology in `vendor/PyOpenMagnetics/MAS/schemas/inputs/topologies/` is represented under `heaviside/topologies/`. Each module is a thin (< 100 LOC) wrapper that:

1. Validates the `DesignSpec` against the topology's MAS schema (TypedDict).
2. Calls `PyOpenMagnetics.process_converter(topology_name, inputs)`.
3. Returns the structured result for the pipeline.

If a binding is missing in PyOpenMagnetics, **add it** in `vendor/PyOpenMagnetics/` and rebuild — do not work around it in Heaviside.

### 5. Realism gate is fail-closed

`heaviside.pipeline.realism` runs after every design. It checks: voltage/current ratings with derating, thermal limits, Isat margin, component availability in TAS, frequency-band sanity. **No overrides, no warnings-only mode, no `--force`.** A design either passes or it is not emitted.

### 6. TAS writes go through the librarian, always

Even from inside Heaviside agents. The `component-librarian` agent is the only writer to `TAS/data/*.ndjson`. Use the lockfile / `Transaction` pattern ported from Proteus. The librarian **writes every time it runs** (no dry-run-only mode).

### 7. Realism, regression, and unit tests on every PR

CI gates: `ruff` + `mypy --strict` + unit tests + analytical regression against the 48 designs in `TAS/data/converters.ndjson` + PyOpenMagnetics build-from-source (cached by submodule SHA).

### 8. Model-agnostic LLM layer

Strands Agents handles provider routing. Default for v0.1: Kimi (Moonshot). Never hard-code Anthropic-only APIs in `heaviside/llm/`. Tier classifications live in `heaviside/llm/model_tiers.json`.

## Repo layout

See [README.md](README.md).

## Working with submodules

```bash
git clone --recurse-submodules git@github.com:OpenConverters/Heaviside.git
git submodule update --init --recursive
git submodule update --remote --merge   # bump to upstream HEAD (PR-only)
```

Submodule bumps must come with passing CI. Never bump `vendor/PyOpenMagnetics` without re-running the regression suite.

## Editing MKF / PyOpenMagnetics

The maintainer (alf) owns both. When Heaviside needs a new PyOpenMagnetics binding, function, or fix:

1. Edit `vendor/PyOpenMagnetics/` (or `~/OpenMagnetics/MKF/`) directly.
2. Rebuild locally: `cd vendor/PyOpenMagnetics && python -m build --wheel && pip install --force-reinstall dist/*.whl`.
3. Push upstream, bump the Heaviside submodule, open the PR. CI re-builds + caches the new wheel by SHA.

## Adding agents

`agents/prompts/<name>.md` with YAML frontmatter `--- name: ... description: ... allowed_tools: [...] ---`. Target: 10–12 consolidated agents (Proteus had 30, mostly overlapping).

## Phases

See [docs/ROADMAP.md](docs/ROADMAP.md). Current: Phase 0 (bootstrap).
