# Web Interface — Exploration & Proposal

*Status: exploration (2026-06-02). Scoping a simple web UI that runs on a web
server, fronting the two PUBLIC pipelines: converter-designer and
cross-reference-from-a-reference-design.*

## What already exists (≈80% of the backend)

- **`heaviside/api/server.py`** — a FastAPI app with real endpoints:
  - `POST /design` — spec → ranked design outcomes (JSON)
  - `POST /design/report` — spec → **HTML report** (`render_html`)
  - `POST /design/magnetic`, `POST /design/bom` — sub-steps
  - `POST /cre` — reverse-engineer a reference (takes `pdf_text` string)
  - `POST /crossref` — BOM + target manufacturer → substitutions
  - `GET /topologies`, `GET /health`
- **CLI wiring**: `heaviside serve --api` → `uvicorn.run("heaviside.api:app")`
  (and `--mcp` for the MCP server alternative).
- **`heaviside/report/html.py: render_html(outcome)`** already emits a full
  HTML report (BOM table, verdict, etc.) — the UI's result view is half-done.
- FastAPI 0.108 + uvicorn 0.44 are installed.

## Gaps to a usable UI (in priority order)

1. **BLOCKING — dependency skew.** `fastapi 0.108.0` + `starlette 1.0.0` are
   incompatible: instantiating `FastAPI(...)` raises
   `Router.__init__() got an unexpected keyword argument 'on_startup'`
   (starlette 1.0 dropped `on_startup`/`on_shutdown` for `lifespan`). The app
   won't import as-is. Fix: bump `fastapi` to a starlette-1.0-compatible
   release (≥0.115) OR pin `starlette<0.36`. (Env is PEP-668 managed — use a
   venv or `--break-system-packages`.)

2. **No frontend.** The API is JSON-only (except `/design/report` which
   returns HTML for a posted spec). A browser user can't fill a form. Need a
   root `/` page: (a) a converter-designer **spec form** (Vin range, Vout,
   Iout, fsw, efficiency), (b) a cross-reference panel (**PDF upload** or BOM
   paste + target-manufacturer dropdown), submit → render result. A single
   static `index.html` + `fetch()` is enough; no SPA framework needed.

3. **Long-running pipelines need async jobs.** `full_design`, CRE, and CR each
   take MINUTES (LLM calls + ngspice/MKF). A synchronous `POST /design` will
   exceed browser/proxy timeouts (~30–60 s). Add a background-job pattern:
   `POST /jobs/design` → `{job_id}` (run via `BackgroundTasks` or a worker) →
   `GET /jobs/{id}` polls `{status, result}`. The front end polls every few
   seconds and shows a progress line.

4. **PDF upload + PDF-driven flows.** `/cre` takes `pdf_text`, not a file.
   Expose: `POST /crossref/from-pdf` (multipart upload → `run_crossref_with_cre`)
   and optionally `POST /design/from-pdf` (CRE-extract spec → `full_design`),
   reusing `heaviside/pipeline/pdf_extract.py`.

## Recommended minimal build (smallest path to "open browser, get a design")

1. Fix the fastapi/starlette pin (Gap 1) — unblocks everything.
2. Add a `jobs` module (in-memory dict of `job_id → {status, result}`) +
   `POST /jobs/{design,crossref}` and `GET /jobs/{id}`. Run the pipeline in a
   thread/`BackgroundTasks`. (In-memory is fine for a single-user/internal
   server; swap for Redis/RQ if it ever needs multi-worker.)
3. Serve one static `index.html` at `/`: two tabs (Design / Cross-reference),
   a form each, JS that POSTs a job, polls, then injects the returned HTML
   report (design) or BOM table (crossref).
4. Add `POST /crossref/from-pdf` (multipart) wrapping `run_crossref_with_cre`.

## Operational caveats (already known)

- **PyOpenMagnetics .so**: the server process needs the cp312
  `PyOpenMagnetics` built from MKF on `PYTHONPATH`/site-packages (see
  [[mkf-isat-oversized-inductance]] / [[pyom-vendor-build]]).
- **Moonshot rate limits**: parallel CRE/design jobs trip 429
  (see [[kimi-k2-quirks]]). The job queue should serialize LLM-heavy work or
  cap concurrency.
- **Cost/runtime**: a design ≈ minutes and a CRE→CR ≈ $0.4 + ~10 min — the UI
  must set expectations (progress + ETA), not pretend it's instant.

## Bottom line

The backend + CLI serving already exist and are well-shaped. A usable web UI
is mostly: (1) a one-line dependency fix, (2) an async-job wrapper for the
slow pipelines, (3) a single static HTML page, (4) one PDF-upload endpoint.
No architectural rework needed.
