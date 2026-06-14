# Heaviside AGENTS.md

> **MKF is authoritative for all magnetics math.** Before implementing
> magnetics calculations or assuming a PyOM API, consult
> [`../../OpenMagnetics/MKF/CAPABILITIES.md`](../../OpenMagnetics/MKF/CAPABILITIES.md).
> No `B_sat·N·A_e/L` (or any analytical magnetics formula) in Heaviside —
> call PyOM. If the capability you need isn't in CAPABILITIES.md, ask;
> don't reimplement it here.

**Read this before touching the repo. It encodes the design rules that make Heaviside different from Proteus.**

## Autonomy (per-repo override of global CLAUDE.md)

Default mode for this repo is **autonomous execution**. Do not ask the user
to confirm every next step.

- After finishing a task, pick the next highest-value item from
  `docs/BACKLOG.md` (or the current in-flight plan) and start it.
- **Commit approval is pre-granted** for this repo. Commit in logical
  chunks as work completes, using `feat(scope): summary` style matching
  the existing git log. Keep commits bisectable.
- **Push approval is still required per-push.** Never `git push` without
  an explicit ask in the current turn.
- When `docs/BACKLOG.md` is empty or the next step is genuinely
  ambiguous, then ask — otherwise, proceed.

**Stop and ask only when:**
1. A decision is irreversible (force push, history rewrite, schema
   break, mass deletion of `TAS/data/` entries, dropping a submodule).
2. The requirement is genuinely ambiguous and the wrong guess would
   waste >30 min of work.
3. A CLAUDE.md guardrail would be violated (no fallbacks/defaults, no
   bypassing librarian for TAS writes, no headed Playwright, no
   OpenMagnetics push without the `hephaestus_om` key).
4. The user has explicitly paused autonomy in the current session.

Honest progress reporting beats checking in. At the end of a chunk,
summarise what was done, what was skipped and why, and what the next
item from the backlog is — then start it.

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

CI gates: `ruff` + `mypy --strict` + unit tests + analytical regression against the 47 designs in `TAS/data/converters.ndjson` + PyOpenMagnetics build-from-source (cached by submodule SHA).

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

## Bridge: TAS ↔ PyOpenMagnetics design loop

`heaviside/bridge.py` is the **only** module that talks to PyOpenMagnetics. Everything else (CLI, agents, MCP, FastAPI) goes through it.

### Pipeline

```
DesignSpec ─┬─► decompose_from_spec(topology, spec)        ► (netlist, tas)
            │     (heaviside/decomposer/api.py)
            │
            └─► design_converter_components(topology, spec) ► ConverterComponents
                  ├─ Phase A: design_magnetics_from_converter      ► main MagneticDesign
                  ├─ Phase B: get_extra_components_inputs(REAL)    ► extra magnetic + capacitor specs
                  └─ Phase C: calculate_advised_magnetics per extra ► extra MagneticDesigns

attach_components_to_tas(tas, components, topology=...) ► populated tas
```

### Public surface (`heaviside.bridge`)

- `MagneticDesign` — frozen dataclass `(mas, scoring, scoring_per_filter)`.
- `ExtraMagneticSpec`, `ExtraCapacitorSpec` — frozen dataclasses `(name, inputs)`.
- `ConverterComponents` — `(main_magnetic, extra_magnetics: dict[str, MagneticDesign], extra_capacitors: tuple[ExtraCapacitorSpec, ...])`.
- `design_magnetics(topology, spec, *, max_results, core_mode, use_ngspice=False)` — Phase A only.
- `extra_components(topology, spec, *, mode, main_magnetic_mas=None)` — wraps PyOM's `get_extra_components_inputs`. **REAL mode requires the Magnetic JSON (`design.magnetic`), NOT the MAS envelope (`design.mas`)** — passing `mas` raises `key 'coil' not found`.
- `design_extra_magnetic(spec, *, max_results, core_mode)` — wraps `calculate_advised_magnetics`.
- `design_converter_components(topology, spec, ...)` — orchestrator for the full A+B+C loop.
- `attach_components_to_tas(tas, components, *, topology)` — registry-driven auto-binding using `TopologyEntry.magnetic_binding`.

### `magnetic_binding` semantics (registry)

Each `TopologyEntry` in `heaviside/topologies/registry.py` carries a `magnetic_binding: dict[str, str | None]` mapping **TAS magnetic component name** (as emitted by the stencil) → **PyOM source**:

- `None` value → the main magnetic from `design_magnetics_from_converter`.
- `str` value → an extras-role name returned by `get_extra_components_inputs` (e.g. `"outputInductor"`, `"seriesInductor"`, `"inputCoupledInductor"`).
- Empty dict `{}` → "no bindings yet"; `attach_components_to_tas` refuses to auto-bind. Caller must supply an explicit mapping.

Exactly one entry per topology must have `value=None`. Enforced at runtime by `attach_components_to_tas` and at unit-test time by `tests/unit/test_stencil_binding_drift.py`, which iterates every golden TAS and asserts the stencil-emitted magnetic set equals the binding keys.

### Coverage snapshot (May 2026)

- **Registry**: 24 converters + 3 magnetics (all 27 entries present).
- **Stencils + golden TAS**: 13 topologies (buck, boost, cuk, sepic, zeta, 4sbb, flyback, ssforward, 2sf, ACF, LLC, isobuck, isobb).
- **`magnetic_binding` wired**: 11 of those 13 (isobuck + isobb skipped — single-magnetic, easy add).
- **End-to-end bridge tested** (`tests/integration/test_bridge_integration.py`): buck (single magnetic, ~92 s), ACF (multi-magnetic T1 + L_out0, ~134 s). LLC end-to-end **not yet verified** — the `L_r → seriesInductor` binding is assumed correct from MKF source but not exercised.
- **Extras probe** (`scripts/probe_extras.py`, `docs/extras-probe.json`): 13/21 topologies return clean extras-spec lists in IDEAL mode; 8 are blocked on spec-construction (nested duty cycle / phase shift / AC input). Vienna + PFC are intentionally extras-free.

When you add a stencil, you **must** also add the corresponding `magnetic_binding` to the registry entry — the drift test will skip silently otherwise, but the topology will not be bridge-usable.

## Adding agents

`agents/prompts/<name>.md` with YAML frontmatter `--- name: ... description: ... allowed_tools: [...] ---`. Target: 10–12 consolidated agents (Proteus had 30, mostly overlapping).

## Phases

See [docs/ROADMAP.md](docs/ROADMAP.md). Current: Phase 0 (bootstrap).
