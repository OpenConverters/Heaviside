# Heaviside — Agent Handoff

> Onboarding + state snapshot for the next agent. **Written 2026-06-07.**
> Read this, then `AGENTS.md` (hard rules), then `docs/BACKLOG.md` (work queue).
> This file captures: what the last session did, a verified quality audit of the
> whole project, the current environment/state (including a `.so`↔MKF mismatch you
> must know about), and a prioritized next-steps roadmap.
>
> The previous handoff (2026-05-26, now superseded — old `.venv`/`PyMKF/build`
> layout, 13/21 corpus) is archived at `docs/archive/HANDOFF-2026-05-26.md`. It
> still has useful MKF-build and SPICE-deck-inspection repro recipes — consult it
> for those, but treat its corpus/pending lists as historical.

---

## 0. TL;DR

- Heaviside is a PyOpenMagnetics-first, agent-driven power-converter auto-design system
  (topology screen → MKF magnetics → BOM from `TAS/data` → ngspice → realism gate → Ray/Nicola review).
- Last session shipped **5 commits** (an isat-sizing fix + 3 reviewer fixes + a new BOM-CSV
  cross-reference feature) and ran a **multi-agent quality audit** (23 agents, every high
  finding adversarially verified).
- **Overall audit grade: C+** — strong design, fail-closed realism gate (A), but `main` does
  **not** meet its own CI bars (3 red gates) and a few stated rules are silently broken.
- **Start here:** the **P0** list in §6 (get `main` green + delete the forbidden analytical
  Isat formula). It's small, mechanical, and is the honest answer to "no tech debt?".
- ⚠️ **State hazard:** the running `.so` was built from MKF `real-winding-geometry@afd71e20`,
  but the MKF working tree is now on `main`. **Do not rebuild the `.so` without reading §4.**

---

## 1. What the last session did (committed to Heaviside `main`)

Parent of the session = `dee7ca5`. Tree is clean (only pre-existing `vendor/PyOpenMagnetics` +
`TAS` submodule noise). Nothing pushed.

| Commit | What | Notes |
|---|---|---|
| `be3fd70` | **fix(designer): gap-aware Isat margin on fast+slow path** | The big one. Fixed buck/boost/cuk/600W-buck failing `inductor_isat_margin`. |
| `170369e` | **fix(reviewers): real JSON output contract + fail-loud validation** | Ray/Nicola were echoing input under `json_mode`; added `normalize_reviewer_verdict`. |
| `0e784e8` | **fix(reviewers): `[SCOPE: …]` marker so reviewers judge the power stage, not INCOMPLETE** | They demanded a full 10-phase design the auto-designer never produces. |
| `438c27a` | **feat(crossref): accept a bare BOM (CSV/TSV/XLSX)** | New: `heaviside/pipeline/bom_import.py` + `POST /jobs/crossref/from-bom` + GUI "Upload BOM" mode. |
| `b4ce8ed` | **fix(reviewers): map native verdict vocabulary onto the enum** | Ray says "PROCEED WITH CAUTION"; was aborting the pipeline. Maps → APPROVED/REJECTED. |

### The isat fix in detail (`be3fd70`) — context for audit finding H1
The non-isolated fast path (`design_magnetics_fast`) returned candidates sorted by losses
only, so its top scorer could be undersized vs worst-case peak. Two changes, **pure Python
orchestration over MKF's `calculate_saturation_current`, no `.so` change**:
1. `bridge.select_fast_by_isat_margin()` — filters fast candidates by `Isat ≥ 1.2·Ipeak_worst`
   (widening the pool if none clear), wired into `full_design` stage 2.
2. A **tier-3 fallback** in `design_converter_components`: when the slow CoreAdviser returns an
   undersized/over-inductance core, prefer the fast path's clearing real-core pick. Guarded so
   it never overrides an already-clearing slow pick → cannot regress passing topologies.

Result (verified): buck `inductor_isat_margin` 0.9175 FAIL → 1.8992 PASS; boost 1.83, cuk 1.75,
600W/25A buck 1.42 all PASS; green transformer set unchanged; full regression 225/1.
**⚠️ This fix calls `bridge._isat_from_mas`, which contains the banned analytical fallback — see H1.**

### Reviewer fixes — verified live against Kimi
The Ray/Nicola reviewers (stage4 design review, stage7 CR review) **do** make real, billed Kimi
calls (proven: 30–727 token responses, ray.md-specific reasoning, 50–110 s each). They were
producing *valid-but-useless* JSON (echoing input / dumping `{"scratchpad":…}`) because the prose
prompts conflicted with `json_mode`. Now: strict `{verdict, summary, objections[]}` schema +
`normalize_reviewer_verdict` (raises on echo/scratchpad; maps PROCEED/NOT-ACCEPTABLE/etc.).
**Known limitation:** a clean 100%-coverage cross-ref shows status "REVIEW" with `reviews=[]` —
the reviewers only engage contested/no-substitute items. Decide if every CR should carry a verdict.

### BOM-CSV cross-reference (`438c27a`) — verified end-to-end through the GUI
`run_crossref_pipeline` already accepted a BOM list; the new piece is ingestion. Demonstrated
live via Playwright MCP: uploaded a Murata MLCC CSV → **C1 GRM188R61A106KE69D → Würth 860010372001
[recommended], 100% coverage**. `bom_import.parse_bom_file(raw, filename)` handles CSV/TSV
(delimiter-sniffed) + XLSX (openpyxl), aliases real-world headers, throws on bad input. 9 unit
tests (`test_bom_import.py`) + 23 (`test_reviewer_verdict.py`).

### MKF-side work (separate repo, NOT pushed) — see §4 for the state hazard
On branch `real-winding-geometry`: `2181dd1c` (a current-based saturation gate — **reverted**, it
over-rejected and regressed buck), `a2a07ea5` (the revert), `afd71e20` (cherry-picked the
`fix-fast-adviser-isat` inductance-validity filter). The active `.so` `b7ed94e9` was built from
`afd71e20`. None pushed (OpenMagnetics repos need the `hephaestus_om` key).

---

## 2. Quality audit — verdict (full report below)

Ran a multi-agent audit (8 dimension auditors → adversarial verification of every critical/high
finding → synthesis), measured against the project's *own* `AGENTS.md` rules. **14 high findings,
all 14 confirmed**, 0 fully refuted (several correctly downgraded).

**Overall: C+.** Excellent engineering judgment; the realism gate is genuinely fail-closed; the
core loop is truly PyOM-first; fail-loud discipline in new code is A-grade. But the project is
**not currently honest with its own CI** — 3 red gates on `main` and a few stated rules broken.

| Dimension | Grade | One-liner |
|---|---|---|
| pipelines-realism | **A** | Fail-closed, no `--force`, exit-6 enforcement, TAS-write discipline honored. |
| tests | **B** | 1436/1448 pass, exemplary mocking; held back by 11 stale-golden reds + empty test dirs. |
| pyom-first | **C** | Core loop genuinely PyOM-first, but 4 modules bypass the bridge + banned Isat formula in 3 fallbacks. |
| no-fallbacks | **C** | Exemplary fail-loud in new code; magnetics extraction swallows PyOM errors into the banned formula. |
| schema-coas-basemodel | **C** | Schemas/COAS real + high-quality, but BaseModel cap red (12>8) and `_generated/` TypedDict layer empty. |
| structure-layering | **C** | Sound decomposition; same 3 hard rules broken (gateway, cap, isat formula). |
| tech-debt-deadcode | **C** | Docs honest+current, but 2 CI gates fail on HEAD + confirmed dead code. |
| agents | **C** | Good prompt corpus, but the model-agnostic Strands factory is dead at runtime; all calls go Kimi-hardcoded. |

### Confirmed HIGH findings (no critical confirmed)
- **H1 — Forbidden `B_sat·N·A_e/L` Isat formula live in 3 fallbacks.** `bridge.py:1311`,
  `extract.py:273`, `extract.py:591`, all inside `except`-driven fallthroughs. Violates PyOM-first
  *and* no-fallback rules. The fabricated scalar flows into the fail-closed realism gate
  (`realism.py:756`) which doesn't inspect provenance. HIGH not critical: conservative-low (over-rejects,
  never ships undersized). **This is in `_isat_from_mas`, used by the session's isat fix.** Fix:
  delete the branches, raise on PyOM rejection; repair the 2 fixtures that assert the formula
  (`test_extract_boost_flyback.py:140`, `test_bridge_isat_postfilter.py:90-103`).
- **H2 — `bridge.py` is not the sole PyOM gateway.** 4 access points: `extract.py` (3 isat calls),
  `topologies/dispatch.py:62`, `decomposer/api.py:61`. The divergent `_import_pyom` copies don't apply
  `_HEAVISIDE_PYOM_SETTINGS`, so the CLI `--no-attach` path emits ngspice decks with saturation/mutual-R
  *off*. Fix: route through thin bridge fns + add `scripts/check_pyom_gateway.py` to CI.
- **H3 — Pydantic BaseModel cap (≤8) RED at 12.** 3 in `spec/design_spec.py` + 9 FastAPI DTOs in
  `api/server.py`. CI red 10–20+ commits. The 9 API DTOs are the legit "user-facing boundary"
  pydantic is reserved for, but the cap was never re-derived. Fix: exclude `api/` HTTP DTOs in
  `check_pydantic_cap.py` with a documented rationale, drive green.
- **H4 — `ruff check` + `ruff format` fail on HEAD.** ~45 F401/F841 dead imports/locals, ~165 files
  unformatted. Violates the project's own "every phase ends CI-green." Fix: `ruff check . --fix &&
  ruff format .`, pin the ruff version in `pyproject` + `ci.yml`.
- **H5 — `heaviside/types/_generated/` is empty/unwired.** Only `.gitkeep` + stub. ~609 signatures
  pass untyped `dict[str,Any]`. This is the entire justification for the 8-model cap. Docstrings
  claim TypedDicts exist (present tense) — aspirational. Fix: populate (`make types` + drift gate +
  import at boundaries) OR fix the docstrings to stop claiming a layer that won't be filled.
- **H6 — Model-agnostic Strands factory is dead code.** `factory.load_agent` has zero runtime call
  sites; `strands` isn't installed; all live calls go through `llm_call.call_llm` (Kimi-hardcoded
  default at `:83`). `topology_selector_llm.py:62` is a third raw-httpx path. Fix: wire `load_agent`
  into the pipelines OR demote `factory.py` to "future/MCP-only" in AGENTS.md and move the
  `is_review_role_allowed` policy into `call_llm`.

### What's genuinely strong (don't "fix")
Realism gate (fail-closed, refuses to clamp η>1, exit-6); PyOM-first core loop (`magnetic_picker`
reports missing fields as `None`, never substitutes); fail-loud `stress.py`/`selector.py`; TAS
writes librarian-only (verified, no bypass); exemplary test isolation (LLM dependency-injected,
no real network); real high-quality schemas + the delivered PEAS shared-utils lift; honest docs;
the session's reviewer verdict-normalization ("the best agent work").

### Refuted / downgraded by the adversarial pass (already checked — don't re-litigate)
- bridge isat post-filter: CRITICAL → **HIGH** (fail-safe direction: over-rejects only).
- "extract.py runs saturation against unconfigured PyOM": **partially overstated** —
  `Magnetic::calculate_saturation_current` reads only temperature, none of bridge's settings; so
  extract's bypass is a gateway/duplication violation, not numeric drift. (The *decomposer* leg's
  ngspice-deck drift is genuine — that part holds.)
- 11 stale-golden test reds: framed as "recent drift" → **DOWNGRADED to pre-existing** (confirmed
  identical at `dee7ca5`).
- ray.md/nicola.md trainer-lesson bloat (694 lessons, 87% append cruft): HIGH → **medium**
  (maintainability/token cost only, no correctness impact).

---

## 3. COAS / PEAS status (the "is everything using PEAS/COAS?" question)
- **PEAS** (Passive Electrical schema family → CAS caps / RAS resistors) and **MAS/SAS/CAS/RAS/TAS**
  are real, `$id`-bearing, `additionalProperties:false` schemas. The PEAS shared-utils lift (all 10
  utils) is genuinely delivered. Used as the *intended* type system — but see H5 (the generated
  TypedDicts that would enforce them at the Python boundary don't exist yet; data flows as untyped dicts).
- **COAS** ("Converter Agnostic Structure", `docs/COAS-proposal.md`) is **schema-complete but
  functionally unwired** — only referenced in `heaviside/validate.py`. No `validate_coas()`, no
  pipeline emits a COAS document. This is the largest *unfinished design goal* (P2 item 12).

---

## 4. ⚠️ Environment & state — READ BEFORE REBUILDING THE .so

- **Python:** `.venv-web/bin/python` (FastAPI 0.136 + the working PyOM `.so`). NOTE: the older
  `.venv` referenced in the archived handoff is superseded. Unit/CLI tests need a venv with
  `typer` (NOT `.venv-web` → that's why `test_cli.py` fails to collect there).
- **Active `.so`:** `b7ed94e9` at `.venv-web/lib/python3.12/site-packages/PyOpenMagnetics/` AND
  `vendor/PyOpenMagnetics/build/cp312-cp312-linux_x86_64/`. Built from MKF
  **`real-winding-geometry@afd71e20`** (gate-revert + inductance-filter cherry-pick).
- **🚨 MISMATCH:** the MKF working tree (`/home/alf/OpenMagnetics/MKF`) is currently on **`main`
  (`1c1ffd62`)**, NOT `real-winding-geometry`. **Rebuilding the `.so` from the current checkout will
  NOT reproduce `b7ed94e9`** and may drop the gate-revert / inductance-filter / weinberg-crash-fix /
  cllc-dispatch work that lives on `real-winding-geometry`. Before any rebuild: `git -C
  ~/OpenMagnetics/MKF checkout real-winding-geometry` and confirm `afd71e20` is present, OR decide
  to merge that branch to `main`. Known-good pre-this-round backup: `/tmp/pyom_so_backup`
  (`e1a477d1`, but lacks the weinberg-crash-fix + cllc dispatch). See memory
  `mkf-isat-oversized-inductance` for the full saga.
- **Rebuild recipe:** `cd vendor/PyOpenMagnetics/build/cp312-cp312-linux_x86_64 && LOCAL_MKF_DIR=1
  ninja -j3 PyOpenMagnetics` (compiles from the `_mkf_local` symlink → `~/OpenMagnetics/MKF`), then
  copy the `.so` to the `.venv-web` site-packages path; md5-match both. `ninja -j3` via Bash
  `run_in_background:true` works (the harness tracks it + notifies). (The archived handoff's
  "harness kills backgrounded ninja / use -j2 / cmake-reconfigure-broken" notes were for the old
  `PyMKF/build` dir — verify before trusting; `-j3` foreground/background worked fine this round.)
- **Web GUI / server:** `MOONSHOT_API_KEY=… PYTHONPATH=. .venv-web/bin/python -m uvicorn
  heaviside.api:app --host 127.0.0.1 --port 8773` (a server is currently up on :8773). SPA at `/`;
  Cross-Reference tab → "Upload BOM (CSV/XLSX)". Web UI source is a Vite app in `heaviside/webui/`;
  `npm run build` outputs to `heaviside/api/static/` (tracked).
- **Kimi key:** in `.claude/settings.local.json` permissions; extract with:
  `python3 -c "import json,re; d=json.load(open('.claude/settings.local.json')); ps=[x for v in
  d['permissions'].values() if isinstance(v,list) for x in v]; print(re.search(r'sk-[A-Za-z0-9_-]{20,}',
  [p for p in ps if 'sk-' in p][0]).group(0))"`. Model `kimi-k2.5`, base `api.moonshot.ai/v1`.
- **GOTCHA — rtk proxy:** the shell hook rewrites `curl` and *summarizes JSON bodies to type stubs*
  (`{ job_id: string }`). Use `.venv-web/bin/python -c "import httpx; …"` for real API responses.
- **GOTCHA — `/jobs` list omits `result`:** the list endpoint returns `{job_id, kind, status,
  progress, summary}`; fetch `GET /jobs/{job_id}` for the full result. (Key is `job_id`, not `id`.)
- **GOTCHA — Playwright MCP:** sandboxes file access to the repo root; put upload files in
  `.playwright-mcp/` (gitignored). Don't overwrite a file after the chooser grabbed it →
  `ERR_UPLOAD_FILE_CHANGED`; use a fresh filename. Always headless (per CLAUDE.md).

### Tests
- `PYTHONPATH=. .venv-web/bin/python -m pytest tests/regression -q` → **225 passed / 1 xfailed**.
- Full unit+regression (excluding the typer-gated CLI test):
  `… pytest tests/regression tests/unit -q --ignore=tests/unit/test_cli.py` → **1436 passed, 11
  failed, 1 xfailed**. The **11 failures are all pre-existing** (proven identical at `dee7ca5`):
  env gaps (`typer` missing → `test_cli`; fetcher fixtures → `test_fetcher_convert`) + known
  stale-golden reds (analyst Tj, realism sim-measurement, stencil-drift, bridge core_mode). The
  xfail is `converters.ndjson` L48 (a telemetry record in the converter corpus, librarian-repair pending).

---

## 5. Other known issues (smaller)
- `/openapi.json` 500s — `response_class=None` on the `/design/report` endpoint (`server.py`);
  Swagger docs broken, API itself fine. `HTMLResponse` already imported.
- `/cre` endpoint leaks a temp file (`delete=False`, never unlinked).
- Dead `_estimate_ron` heuristic (zero callers, violates no-estimates rule — delete it).
- `topology_selector_llm.py` has a `.cn` Moonshot base-URL default (latent connectivity bug; should be `.ai`).
- `extract.py` (3081 LoC, 51 enrichers) is a god-module re-deriving ripple/ipeak that `stress.py`
  already computes (CLAUDE.md: don't duplicate magnetics math downstream).

---

## 6. What to do next — prioritized roadmap

### P0 — get `main` green + remove the forbidden physics (days, mechanical)
1. **Delete the 3 analytical Isat fallbacks (H1)** → raise naming the rejected MAS field; repair the
   2 fixtures that assert the formula. The single most important fix — a verbatim violation of the
   rule the maintainer cares most about, feeding the safety gate. Surfaced in 4 dimensions.
2. **`ruff check . --fix && ruff format .` (H4)** → delete ~45 dead imports/locals, commit, pin ruff.
3. **Resolve the BaseModel cap (H3)** → exclude `api/` HTTP DTOs in `check_pydantic_cap.py` with a
   documented rationale; drive exit 0.

### P1 — close the silently-broken architecture rules (1–2 weeks)
4. **Route the 4 PyOM access points through the bridge (H2)** + add `scripts/check_pyom_gateway.py`
   to CI. Also fixes the verified `--no-attach` ngspice-deck setting drift.
5. **Decide the Strands-factory question (H6)** — wire it in or demote it; collapse the third
   httpx path in `topology_selector_llm.py` and fix its `.cn` default.
6. **Decide the `_generated/` TypedDict question (H5)** — populate + import, or fix the docstrings.
7. **Triage the 11 stale-golden reds** — update intended goldens; but **investigate the
   Qrr/forwardVoltage/ESR "made optional" reds** (`convert.py:841`) — that may be a real
   no-fallback regression, not a golden update.

### P2 — debt + unfinished design goals (opportunistic)
8. Delete dead `_estimate_ron`. 9. Fix the openapi 500 + `/cre` temp-file leak. 10. Curate the
   694 trainer lessons into the existing `knowledge/lessons.ndjson` store; add a CI prompt-size budget.
11. Split the `extract.py` god-module per-topology (consume `stress.py` instead of re-deriving).
12. **Wire COAS end-to-end** — `validate_coas()` + pipelines emit a COAS doc wrapping the TAS,
   making the inputs→outputs reproducibility contract testable. Fill empty `tests/property/` +
   `tests/realism/` dirs; add the missing `stage3b_gatekeeper` test + goldens for ~6 uncovered topologies.

### Parked / blocked (not P-ranked)
- 3 transformer topologies still fail deeper MKF core-adviser issues (weinberg isat on canonical
  spec, isolated_buck SPICE non-convergence, cllc 0-picks) — need supervised MKF C++ work, not
  autonomous. See memory `mkf-isat-oversized-inductance` + `project-next-steps`.
- Training round (10 golden designs → designer → compare) was 3/10 PASS pre-isat-fix; the isat +
  reviewer fixes should raise it — re-run `scripts/run_training_round.py` (one-at-a-time, Kimi 429).
- MKF `real-winding-geometry` branch is unpushed (needs `hephaestus_om` key + maintainer decision).

---

## 7. Memory pointers (auto-memory, loaded each session)
`/home/alf/.claude/projects/-home-alf-OpenConverters-Heaviside/memory/` — see `MEMORY.md` index.
Most relevant: `mkf-isat-oversized-inductance` (the full isat saga + the `.so`/branch state),
`kimi-k2-quirks` (reviewer json_mode + verdict mapping + scope marker), `project-next-steps`,
`triangle-and-bom-completeness`, `feedback-*` (user's no-fallback / no-estimate / named-reviewer rules).

## 8. The governing rules (do not violate — from CLAUDE.md + AGENTS.md)
No fallbacks/defaults/sentinels — throw. All magnetics math in MKF (no `B_sat·N·A_e/L` in Python).
Never route around broken things — surface + ask. Realism gate is fail-closed. TAS writes via
librarian only. Playwright always headless. OpenMagnetics pushes use the `hephaestus_om` key,
per-push approval. Commit approval is pre-granted for Heaviside; push approval is not.
