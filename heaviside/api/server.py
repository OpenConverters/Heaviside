"""Heaviside REST API — thin surface over the design pipeline.

Endpoints:
  POST /design          — full auto-design (spec → ranked outcomes)
  POST /design/magnetic — magnetic-only design for a given topology
  POST /design/bom      — BOM selection for a given topology + spec
  GET  /topologies      — list registered topologies
  GET  /health          — liveness check

Launch:
  heaviside serve --api          (via CLI)
  uvicorn heaviside.api:app      (direct)
"""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _load_dotenv_if_present() -> None:
    """Load the repo-root ``.env`` into ``os.environ`` for keys that are not
    already set, so the server has MOONSHOT_API_KEY (and friends) regardless of
    how it was launched — a plain ``uvicorn`` / ``nohup`` start otherwise has no
    LLM key and every crossref/design LLM call fails instantly.

    Only fills UNSET variables (never overrides a real environment value), so a
    supervisor/systemd ``environment=`` in prod stays authoritative. No
    dependency: a minimal ``KEY=VALUE`` parser, quotes stripped, ``#`` comments
    and blank lines skipped. Malformed lines are ignored — a bad .env must never
    stop the server from booting."""
    import os
    from pathlib import Path

    env_path = Path(__file__).resolve().parents[2] / ".env"
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    loaded = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        if not key or key in os.environ:
            continue
        value = value.strip().strip("'").strip('"')
        os.environ[key] = value
        loaded += 1
    if loaded:
        logger.info("loaded %d var(s) from %s", loaded, env_path)


_load_dotenv_if_present()


def _configure_heaviside_logging() -> None:
    """Route the ``heaviside.*`` loggers to stderr at INFO so the pipeline's
    progress lines (CR stage N, correction loops, per-batch timing, …) are
    actually recorded — there was no logging config, so every ``logger.info``
    was silently dropped and prod runs were unobservable. stderr is captured by
    supervisor (heaviside.err.log). Honour HEAVISIDE_LOG_LEVEL if set."""
    import os

    level = getattr(logging, os.environ.get("HEAVISIDE_LOG_LEVEL", "INFO").upper(), logging.INFO)
    hlog = logging.getLogger("heaviside")
    if not any(getattr(h, "_heaviside", False) for h in hlog.handlers):
        handler = logging.StreamHandler()  # stderr
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler._heaviside = True  # type: ignore[attr-defined]
        hlog.addHandler(handler)
    hlog.setLevel(level)
    hlog.propagate = False


_configure_heaviside_logging()

app = FastAPI(
    title="Heaviside",
    description="Power converter auto-design API",
    version="0.1.0",
)


# ---------------------------------------------------------------------------
# Auth + resource guards
# ---------------------------------------------------------------------------

# State-changing / cost-bearing requests (job submission burns LLM tokens +
# ngspice minutes; DELETE destroys results; the from-url fetch is an SSRF
# surface) require an API key when one is configured. Reads (GET) stay open for
# the UI and liveness probes.
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _expected_api_key() -> str | None:
    import os

    return os.environ.get("HEAVISIDE_API_KEY") or None


if _expected_api_key() is None:
    logger.warning(
        "HEAVISIDE_API_KEY is not set — state-changing endpoints (job submission, "
        "deletion, URL fetch) accept UNAUTHENTICATED requests. Set HEAVISIDE_API_KEY "
        "to require a Bearer token / X-API-Key header."
    )


@app.middleware("http")
async def _require_api_key(request: Any, call_next: Any) -> Any:
    import secrets

    from fastapi.responses import JSONResponse

    expected = _expected_api_key()
    if expected and request.method in _MUTATING_METHODS:
        presented: str | None = None
        auth = request.headers.get("authorization")
        if auth and auth.lower().startswith("bearer "):
            presented = auth[7:].strip()
        else:
            presented = request.headers.get("x-api-key")
        if not presented or not secrets.compare_digest(presented, expected):
            return JSONResponse({"detail": "invalid or missing API key"}, status_code=401)
    return await call_next(request)


async def _job_queue_full_handler(request: Any, exc: Exception) -> Any:
    from fastapi.responses import JSONResponse

    return JSONResponse({"detail": str(exc)}, status_code=429)


from heaviside.api.jobs import JobQueueFull  # noqa: E402  (after app is defined)

app.add_exception_handler(JobQueueFull, _job_queue_full_handler)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class DesignRequest(BaseModel):
    spec: dict[str, Any]
    candidates_per_topology: int = Field(default=3, ge=1, le=20)
    pick_criteria: str = "lowest_losses"
    core_mode: str = "standard cores"
    topologies: list[str] | None = Field(
        default=None,
        description="Restrict to these topologies; None = auto-screen all",
    )


class MagneticRequest(BaseModel):
    topology: str
    spec: dict[str, Any]
    max_results: int = Field(default=5, ge=1, le=20)
    core_mode: str = "standard cores"


class BomRequest(BaseModel):
    topology: str
    spec: dict[str, Any]
    tas: dict[str, Any]


class TopologyInfo(BaseModel):
    name: str
    family: str
    kind: str


class DesignOutcomeResponse(BaseModel):
    topology: str
    verdict: str | None
    gatekeeper_approved: bool | None
    scoring: float
    bom: list[dict[str, Any]]
    report: str | None
    diagnostics: list[str]


class DesignResponse(BaseModel):
    stage1_topologies: list[str]
    stage2_picks: int
    stage2_failures: list[dict[str, str]]
    outcomes: list[DesignOutcomeResponse]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/topologies", response_model=list[TopologyInfo])
def list_topologies() -> list[dict[str, Any]]:
    from heaviside.topologies.registry import CONVERTERS

    return [{"name": e.name, "family": e.family, "kind": e.kind} for e in CONVERTERS]


@app.post("/design", response_model=DesignResponse)
def design(req: DesignRequest) -> dict[str, Any]:
    from heaviside.pipeline.full_design import (
        full_design,
    )

    try:
        stage1, stage2, outcomes = full_design(
            req.spec,
            n_candidates_per_topology=req.candidates_per_topology,
            pick_criteria=req.pick_criteria,
            core_mode=req.core_mode,
            parallel=True,
            restrict_topologies=req.topologies or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    outcome_list = []
    for o in outcomes:
        bom_entries = []
        if o.tas:
            for stage in o.tas.get("topology", {}).get("stages", []):
                for comp in stage.get("circuit", {}).get("components", []):
                    prov = comp.get("selection_provenance")
                    if isinstance(prov, dict):
                        bom_entries.append(prov)

        outcome_list.append(
            {
                "topology": o.pick.topology.name,
                "verdict": o.verdict_dict["verdict"] if o.verdict_dict else None,
                "gatekeeper_approved": o.gatekeeper.approved if o.gatekeeper else None,
                "scoring": o.pick.main_magnetic.scoring,
                "bom": bom_entries,
                "report": o.report,
                "diagnostics": list(o.diagnostics),
            }
        )

    return {
        "stage1_topologies": list(stage1.reconciliation.chosen),
        "stage2_picks": len(stage2.picks),
        "stage2_failures": [{"topology": t, "error": e} for t, e in stage2.failures],
        "outcomes": outcome_list,
    }


@app.post("/design/magnetic")
def design_magnetic(req: MagneticRequest) -> dict[str, Any]:
    from heaviside.bridge import BridgeError, design_magnetics_fast

    try:
        candidates = design_magnetics_fast(
            req.topology,
            req.spec,
            max_results=req.max_results,
            core_mode=req.core_mode,
        )
    except BridgeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "topology": req.topology,
        "candidates": [
            {
                "scoring": c.scoring,
                "core_shape": c.core_shape_name,
                "elapsed_s": c.elapsed_s,
            }
            for c in candidates
        ],
    }


@app.post("/design/report", response_class=None)
def design_report(req: DesignRequest) -> Any:
    """Run the full pipeline and return an HTML report for the best outcome."""
    from fastapi.responses import HTMLResponse

    from heaviside.pipeline.full_design import full_design
    from heaviside.report import render_html

    try:
        _, _, outcomes = full_design(
            req.spec,
            n_candidates_per_topology=req.candidates_per_topology,
            pick_criteria=req.pick_criteria,
            core_mode=req.core_mode,
            parallel=True,
            restrict_topologies=req.topologies or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    best = next(
        (
            o
            for o in outcomes
            if o.verdict_dict
            and o.verdict_dict["verdict"] == "pass"
            and o.gatekeeper
            and o.gatekeeper.approved
        ),
        outcomes[0] if outcomes else None,
    )
    if best is None:
        raise HTTPException(status_code=404, detail="no design survived the pipeline")

    return HTMLResponse(content=render_html(best))


@app.post("/design/bom")
def design_bom(req: BomRequest) -> dict[str, Any]:
    from heaviside.catalogue import SelectionError, assemble_bom_from_tas
    from heaviside.pipeline.stress import StressDerivationError

    try:
        result = assemble_bom_from_tas(
            req.tas,
            topology=req.topology,
            spec=req.spec,
        )
    except (SelectionError, StressDerivationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    bom = []
    for stage in result.get("topology", {}).get("stages", []):
        for comp in stage.get("circuit", {}).get("components", []):
            prov = comp.get("selection_provenance")
            if isinstance(prov, dict):
                bom.append(prov)
    return {"topology": req.topology, "bom": bom}


# ---------------------------------------------------------------------------
# RE (Reverse-Engineering)
# ---------------------------------------------------------------------------


class RERequest(BaseModel):
    reference: str
    pdf_text: str | None = None


@app.post("/reverse-engineer")
def cre_endpoint(req: RERequest) -> dict[str, Any]:
    """Run the RE pipeline on a reference design."""
    import tempfile
    from pathlib import Path

    from heaviside.pipeline.re_pipeline import run_re_pipeline

    pdf_path = None
    if req.pdf_text:
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as tmp:
            tmp.write(req.pdf_text)
        pdf_path = Path(tmp.name)

    try:
        outcome = run_re_pipeline(req.reference, pdf_path=pdf_path)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        if pdf_path is not None:
            pdf_path.unlink(missing_ok=True)

    return {
        "reference": outcome.reference,
        "passed": outcome.passed,
        "ref_spec": outcome.ref_spec.__dict__ if outcome.ref_spec else None,
        "ref_bom": list(outcome.ref_bom),
        "review_verdicts": list(outcome.review_verdicts),
        "diagnostics": list(outcome.diagnostics),
    }


# ---------------------------------------------------------------------------
# Cross-Reference
# ---------------------------------------------------------------------------


class CrossRefRequest(BaseModel):
    source_bom: list[dict[str, Any]]
    target_manufacturer: str
    circuit_context: str | None = None


@app.post("/crossref")
def crossref_endpoint(req: CrossRefRequest) -> dict[str, Any]:
    """Run the cross-reference pipeline."""
    from heaviside.pipeline.crossref_pipeline import run_crossref_pipeline

    try:
        outcome = run_crossref_pipeline(
            req.source_bom,
            req.target_manufacturer,
            circuit_context=req.circuit_context,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "target_manufacturer": outcome.target_manufacturer,
        "passed": outcome.passed,
        "components": [
            {
                "ref_des": c.ref_des,
                "component_type": c.component_type,
                "original_mpn": c.original_mpn,
                "substitute_mpn": c.substitute_mpn,
                "status": c.status.value,
                "notes": c.notes,
            }
            for c in outcome.components
        ],
        "guardrail_fires": list(outcome.guardrail_log),
        "diagnostics": list(outcome.diagnostics),
    }


# ---------------------------------------------------------------------------
# Async jobs — the design / RE / cross-reference pipelines take minutes, so
# the UI submits a job and polls. Workers serialize LLM-heavy runs (avoids
# Moonshot 429). See heaviside/api/jobs.py.
# ---------------------------------------------------------------------------


def _crossref_outcome_dict(outcome: Any) -> dict[str, Any]:
    components = [
        {
            "ref_des": c.ref_des,
            "component_type": c.component_type,
            "original_mpn": c.original_mpn,
            "original_value": c.original_value,
            "original_voltage": c.original_voltage,
            "original_package": c.original_package,
            "substitute_mpn": c.substitute_mpn,
            "substitute_value": c.substitute_value,
            "substitute_voltage": c.substitute_voltage,
            "substitute_package": c.substitute_package,
            "status": c.status.value,
            "match_detail": c.match_detail,  # per-parameter rationale (why this status)
            "guardrail_fires": list(c.guardrail_fires),
            "notes": c.notes,
        }
        for c in outcome.components
    ]
    # Coverage = parts with a concrete substitute over those that needed one
    # (keep_original / not_fitted parts don't count against coverage).
    _SUBBED = {"exact", "recommended", "partial"}
    _NEEDS = _SUBBED | {"no_substitute"}
    needing = [c for c in components if c["status"] in _NEEDS]
    subbed = [c for c in needing if c["status"] in _SUBBED]
    coverage_pct = round(100 * len(subbed) / len(needing)) if needing else None
    return {
        "target_manufacturer": outcome.target_manufacturer,
        "passed": outcome.passed,
        "components": components,
        "coverage_substituted": len(subbed),
        "coverage_total": len(needing),
        "coverage_pct": coverage_pct,
        "diagnostics": list(outcome.diagnostics),
    }


def _design_job(
    spec: dict[str, Any],
    n: int,
    topologies: list[str] | None = None,
    update: Any = None,
) -> dict[str, Any]:
    from heaviside.pipeline.full_design import full_design
    from heaviside.pipeline.re_state import compute_desired_inductance
    from heaviside.report import render_html

    # The web form posts a bare electrical spec. The MKF magnetic designer
    # additionally needs `desiredInductance` (and, for isolated topologies,
    # turns ratios) — the RE path computes these in to_heaviside_spec. Mirror
    # the inductance sizing here so a minimal form yields a real design.
    spec = dict(spec)
    # The web form no longer asks for fsw — the designer is meant to pick it from
    # the magnetic's total-loss sweep. Until that sweep pipeline is wired into
    # this endpoint (B9), seed a starting switching frequency so the current
    # full_design pipeline (which designs at one fixed fsw) still produces a
    # design. The frequency_sweep stage overrides this per-point when it lands.
    spec["operatingPoints"] = [
        {**o, "switchingFrequency": o.get("switchingFrequency", 500000)}
        if isinstance(o, dict)
        else o
        for o in (spec.get("operatingPoints") or [{}])
    ]
    op = (spec.get("operatingPoints") or [{}])[0]
    vouts = op.get("outputVoltages") or []
    iouts = op.get("outputCurrents") or []
    n_out = min(len(vouts), len(iouts))
    total_pout = sum(float(v) * float(i) for v, i in zip(vouts, iouts, strict=False))
    if "desiredInductance" not in spec:
        vin = (spec.get("inputVoltage") or {}).get("nominal")
        fsw = op.get("switchingFrequency")
        ripple = spec.get("currentRippleRatio", 0.3)
        if vin and vouts and iouts and fsw:
            l = compute_desired_inductance(vin, vouts[0], iouts[0], fsw, ripple_ratio=ripple)
            if l is not None:
                spec["desiredInductance"] = l

    progress_cb = None
    if update is not None:
        # Declare the design pipeline as named stages so the Jobs view can draw
        # the flow with per-stage timing. full_design emits (msg, pct) at coarse
        # milestones (5 topology, 15 magnetics, 95 review, 100 done); map each
        # pct band to its stage.
        _DESIGN_STAGES = ["Topology screen", "Magnetics & realize", "Adversarial review"]
        if hasattr(update, "set_stages"):
            update.set_stages(_DESIGN_STAGES)

        def progress_cb(msg, pct):
            if hasattr(update, "start_stage"):
                stage = (
                    _DESIGN_STAGES[0]
                    if pct < 15
                    else _DESIGN_STAGES[1]
                    if pct < 95
                    else _DESIGN_STAGES[2]
                )
                update.start_stage(stage)
            return update(f"{pct}% — {msg}")

    _, stage2, outcomes = full_design(
        spec,
        n_candidates_per_topology=n,
        parallel=True,
        restrict_topologies=topologies or None,
        progress_cb=progress_cb,
    )
    if not outcomes:
        # Surface WHY nothing survived (per "no silent shortcuts"): the
        # per-topology magnetic-design failures are the real signal.
        fails = "".join(f"<li><code>{t}</code>: {e}</li>" for t, e in stage2.failures[:12])
        detail = f"<ul>{fails}</ul>" if fails else "<p>No topology was selected for this spec.</p>"
        return {
            "html": f"<p><b>No design survived the pipeline.</b></p>{detail}",
            "topology": None,
            "verdict": None,
        }
    best = next(
        (o for o in outcomes if o.verdict_dict and o.verdict_dict.get("verdict") == "pass"),
        outcomes[0],
    )
    html = render_html(best)
    if n_out > 1:
        # Be explicit (never silent): the design honours all rails for topology
        # selection + netlist, but stress/realism/BOM are primary-rail today.
        rails = ", ".join(
            f"{float(v):g} V @ {float(i):g} A" for v, i in zip(vouts, iouts, strict=False)
        )
        html = (
            f'<div style="background:rgba(180,120,30,.12);border:1px solid '
            f'rgba(180,120,30,.4);border-radius:10px;padding:.7rem 1rem;margin-bottom:1rem">'
            f"<b>Multi-output converter</b> — {n_out} rails ({rails}), "
            f"{total_pout:g} W total. Topology screening, magnetics and the netlist "
            f"use all rails; per-secondary stress, realism and component selection are "
            f"currently summarised on the primary rail (OUT0).</div>"
        ) + html
    return {
        "topology": best.pick.topology.name,
        "verdict": best.verdict_dict.get("verdict") if best.verdict_dict else None,
        "html": html,
        "alternatives": [
            {
                "topology": o.pick.topology.name,
                "verdict": o.verdict_dict.get("verdict") if o.verdict_dict else None,
            }
            for o in outcomes
        ],
    }


@app.post("/jobs/design")
def submit_design(req: DesignRequest) -> dict[str, str]:
    from heaviside.api.jobs import registry
    from heaviside.api.telemetry import wrap_job

    job_id = registry.submit(
        "design",
        wrap_job(
            lambda update: _design_job(
                req.spec, req.candidates_per_topology, req.topologies, update
            ),
            job_kind="design",
            input_type="spec",
            input_spec=req.spec,
        ),
    )
    return {"job_id": job_id}


# Closed-loop (fsw-from-magnetic) designer — drives the NEW pipeline stages so
# the Jobs view shows them: constraints → sweep → pick → reconcile → realize
# (real BOM + MKF SPICE) → review. Single-inductor hard-switched topologies.
_CLOSED_LOOP_STAGES = [
    "Topology constraints",
    "Converter spec",
    "Frequency sweep",
    "Magnetic pick",
    "Cross-OP reconcile",
    "Realize: real BOM + SPICE",
    "Realism gate + gatekeeper",
    "Review: Ray (engineering)",
    "Review: Nicola (quality)",
]

# Map a pipeline progress message → the stage it belongs to, by a distinctive
# keyword in the message design_converter emits (robust to wording/pct drift —
# the pipeline owns the granularity, the UI just mirrors it). First match wins.
_STAGE_KEYWORDS: list[tuple[str, str]] = [
    ("Proposing", "Topology constraints"),
    ("base converter spec", "Converter spec"),
    ("Sweep", "Frequency sweep"),  # "Sweeping…" and "Sweep done…"
    ("Picking the magnetic", "Magnetic pick"),
    ("Reconciling", "Cross-OP reconcile"),
    # della-Pollock cutover: realize + real-part sim are ONE Kirchhoff pass, so the
    # message is "Realizing via Kirchhoff …" (not the old "Realizing converter") and
    # there is no separate "Re-simulating"/tune stage anymore. Match "Realizing" and
    # keep "Realism gate" AFTER it so the shared "Real…" prefix resolves correctly.
    ("Realizing", "Realize: real BOM + SPICE"),
    ("Realism gate", "Realism gate + gatekeeper"),
    ("Ray", "Review: Ray (engineering)"),
    ("Nicola", "Review: Nicola (quality)"),
]


def _stage_for_message(msg: str) -> str | None:
    """Resolve the current stage from a pipeline progress message."""
    for keyword, stage in _STAGE_KEYWORDS:
        if keyword.lower() in msg.lower():
            return stage
    return None


def _design_converter_job(
    spec: dict[str, Any], topology: str | None, update: Any
) -> dict[str, Any]:
    from heaviside.pipeline.converter_designer import design_converter
    from heaviside.report import render_html

    if hasattr(update, "set_stages"):
        update.set_stages(_CLOSED_LOOP_STAGES)

    # Resolve a topology if "Auto": take the first hard-switched single-inductor
    # one the screen finds (the sweep only covers those today).
    topo = topology
    if not topo:
        from heaviside.pipeline.topology_screen import feasible_topology_names

        supported = {"buck", "boost", "cuk", "sepic", "zeta", "four_switch_buck_boost"}
        names = [n for n in feasible_topology_names(spec) if n in supported]
        if not names:
            return {
                "topology": None,
                "verdict": None,
                "html": "<p>No hard-switched single-inductor topology is feasible for "
                "this spec yet (the closed-loop designer covers buck/boost/cuk/"
                "sepic/zeta/4SBB). Use the standard designer for others.</p>",
            }
        topo = names[0]

    def cb(msg: str, pct: int) -> None:
        # Drive the per-stage UI from the message the pipeline emits (the
        # pipeline owns the granularity); fall back to a pct band only if a
        # message has no recognised keyword, so the flow never stalls.
        if hasattr(update, "start_stage"):
            stage = _stage_for_message(msg)
            if stage is None and pct >= 100:
                stage = _CLOSED_LOOP_STAGES[-1]  # "Done" → last stage complete
            if stage is not None:
                update.start_stage(stage)
        update(f"{pct}% — {msg}")

    # Proxy check_cancelled so the pipeline can honour user-requested cancellation.
    cb.check_cancelled = update.check_cancelled  # type: ignore[attr-defined]

    design = design_converter(topo, spec, use_llm=True, with_reviewers=True, progress=cb)

    # Per-operating-point electrical summary so the interactive report can label
    # each waveform with its Vin/Vout/Iout (the waveforms themselves carry only
    # op_index + label). Built from the spec the design ran at — no fabrication.
    vin = spec.get("inputVoltage") or {}
    op_summary: list[dict[str, Any]] = []
    for i, o in enumerate(spec.get("operatingPoints") or []):
        if not isinstance(o, dict):
            continue
        op_summary.append(
            {
                "op_index": i,
                "vin_nominal": vin.get("nominal"),
                "output_voltages": o.get("outputVoltages"),
                "output_currents": o.get("outputCurrents"),
                "ambient_c": o.get("ambientTemperature"),
                "fsw_hz": o.get("switchingFrequency") or design.fsw_hz,
            }
        )

    return {
        "topology": topo,
        "verdict": design.verdict,
        "fsw_hz": design.fsw_hz,
        "html": render_html(design.outcome),
        "bom": design.bom,
        # Structured data for the interactive report (the HTML above stays the
        # PDF/source-of-truth fallback). waveforms: per-OP inductor/primary
        # winding current + voltage from PyOM's ngspice.
        "waveforms": design.waveforms,
        "operating_points": op_summary,
    }


@app.post("/jobs/design/closed-loop")
def submit_design_closed_loop(req: DesignRequest) -> dict[str, str]:
    from heaviside.api.jobs import registry
    from heaviside.api.telemetry import wrap_job

    topo = (req.topologies or [None])[0]
    return {
        "job_id": registry.submit(
            "design",
            wrap_job(
                lambda update: _design_converter_job(req.spec, topo, update),
                job_kind="design",
                input_type="spec",
                input_spec=req.spec,
            ),
        )
    }


def _pdf_cache_path(job_id: str):
    """Where a rendered report PDF is cached (alongside the persisted job)."""
    import os
    from pathlib import Path

    base = Path(os.environ.get("HEAVISIDE_JOBS_DIR", str(Path.home() / ".heaviside" / "jobs")))
    return base / f"{job_id}.report.pdf"


def _build_job_pdf(job: Any, job_id: str) -> bytes | None:
    """Render a finished job's report HTML → PDF bytes, or None if it has no
    renderable report. Raises ReporterError if the PDF backend fails."""
    from heaviside.stages.reporter import html_to_pdf

    result = job.result if isinstance(job.result, dict) else {}
    html = result.get("html")
    if not html and "components" in result:
        from heaviside.report.crossref_html import render_crossref_html

        html = render_crossref_html(result, title=f"{job.kind} · {job_id}")
    if not html:
        return None
    import time as _t

    t0 = _t.monotonic()
    pdf = html_to_pdf(html)
    logger.info(
        "rendered PDF for job %s (%d bytes) in %.0fs", job_id, len(pdf), _t.monotonic() - t0
    )
    return pdf


def _ensure_pdf_cached(job_id: str) -> Path | None:  # noqa: F821
    """Render + cache a finished job's PDF if not already cached. Returns the
    cache path (or None if the job has no report). Used by the pre-render hook
    and as the on-demand fallback."""
    from heaviside.api.jobs import registry

    cache = _pdf_cache_path(job_id)
    if cache.is_file():
        return cache
    job = registry.get(job_id)
    if job is None or job.status != "done":
        return None
    pdf = _build_job_pdf(job, job_id)
    if pdf is None:
        return None
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(pdf)
    return cache


def _prerender_report_pdf(job_id: str) -> None:
    """on_job_done hook: render + cache the report PDF as a TRACKED post-job
    stage ('Generate report PDF') in a background thread, so the job's results
    show immediately while the PDF renders, and the UI can display
    preparing→ready on the download button (job.report_pdf). WeasyPrint on a
    large report takes minutes; keeping it off the worker thread means the next
    job isn't blocked. Best-effort — a render failure marks report_pdf=error but
    never touches the job's own result."""
    import threading

    from heaviside.api.jobs import registry

    def _work() -> None:
        # Does this job even have a renderable report?
        job = registry.get(job_id)
        result = job.result if (job and isinstance(job.result, dict)) else {}
        if not (result.get("html") or "components" in result):
            registry.set_report_pdf(job_id, "none")
            return
        registry.report_stage_begin(job_id)
        try:
            ok = _ensure_pdf_cached(job_id) is not None
            registry.report_stage_end(job_id, ok=ok)
            if ok:
                logger.info("pre-rendered report PDF cache for job %s", job_id)
            else:
                logger.info("job %s has no report to render", job_id)
        except Exception as exc:
            logger.warning("pre-render PDF for job %s failed: %s", job_id, exc)
            registry.report_stage_end(job_id, ok=False)

    threading.Thread(target=_work, daemon=True).start()


# Register the pre-render hook so finished jobs cache their PDF up front.
try:
    from heaviside.api.jobs import registry as _registry

    _registry.on_job_done = _prerender_report_pdf
except Exception:
    pass


@app.get("/jobs/{job_id}/report.pdf")
def job_report_pdf(job_id: str):
    """Serve a finished job's report PDF — design report for design jobs, full
    cross-reference report for crossref jobs.

    The PDF is cached on disk (rendered once, by the on_job_done pre-render hook
    or on first request): WeasyPrint on a large report takes minutes, which would
    otherwise re-render on every download and trip the gateway timeout."""
    from fastapi.responses import Response

    from heaviside.api.jobs import registry
    from heaviside.stages.reporter import ReporterError

    job = registry.get(job_id)
    if job is None or job.status != "done":
        raise HTTPException(status_code=404, detail="no finished job with that id")
    result = job.result if isinstance(job.result, dict) else {}
    prefix = "crossref" if (not result.get("html") and "components" in result) else "design"
    headers = {"Content-Disposition": f'attachment; filename="{prefix}_{job_id}.pdf"'}

    cache = _pdf_cache_path(job_id)
    if cache.is_file():
        return Response(content=cache.read_bytes(), media_type="application/pdf", headers=headers)
    # Not cached yet (pre-render still running or an older job) — render on demand.
    try:
        path = _ensure_pdf_cached(job_id)
    except ReporterError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if path is None:
        raise HTTPException(status_code=409, detail="job has no report to render")
    return Response(content=path.read_bytes(), media_type="application/pdf", headers=headers)


# Cross-reference pipeline stages, mirrored into the Jobs view so the run shows
# its real per-stage progress (driven by the messages run_crossref_pipeline /
# run_crossref_with_cre emit — the pipeline owns the granularity, the UI just
# reflects it). The RE-fronted paths (from-pdf / from-url) reverse-engineer the
# reference first, so they declare the RE prefix stages on top of the CR core.
_CROSSREF_CORE_STAGES = [
    "Resolve part numbers",
    "Prefetch TAS candidates",
    "Librarian: source missing parts",
    "Pre-classify components",
    "Cross-reference (LLM)",
    "Guardrails",
    "Score candidates",
    "Otto challenge",
    "In-kind rescue",
    "Review: Ray + Nicola",
    "Learn",
]
_CROSSREF_CRE_PREFIX = [
    "Extract reference document",
    "Spec extract",
    "Reverse-engineer schematic",
    "Verify MPNs",
    "Extract RDS(on)",
    "Extract datasheet claims",
    "Testbench simulation",
    "RE→CR stress bridge",
]
_CROSSREF_FULL_STAGES = _CROSSREF_CRE_PREFIX + _CROSSREF_CORE_STAGES
# from-url adds a download step ahead of everything else.
_CROSSREF_URL_STAGES = ["Download reference", *_CROSSREF_FULL_STAGES]

# (distinctive keyword in the emitted message, stage). First match wins, so put
# more specific keywords before substrings that could also match them.
_CROSSREF_KEYWORDS: list[tuple[str, str]] = [
    ("Downloading", "Download reference"),
    # RE prefix (from-pdf / from-url)
    ("reference document", "Extract reference document"),
    ("Spec extract", "Spec extract"),
    ("Reverse-engineering", "Reverse-engineer schematic"),
    ("Verifying extracted MPNs", "Verify MPNs"),
    ("RDS(on)", "Extract RDS(on)"),
    ("performance claims", "Extract datasheet claims"),
    ("Testbench", "Testbench simulation"),
    ("RE→CR bridge", "RE→CR stress bridge"),
    # CR core (all paths). "Resolving" is the part-resolver (stage 0) — its own
    # stage so the bar advances immediately instead of sitting at 0 through that
    # first (often slow) LLM call on messy pasted cells like "Phoenix C : 1707654".
    ("Resolving", "Resolve part numbers"),
    ("Prefetching", "Prefetch TAS candidates"),
    ("Librarian", "Librarian: source missing parts"),
    ("Pre-classifying", "Pre-classify components"),
    ("Cross-referencing", "Cross-reference (LLM)"),
    ("guardrails", "Guardrails"),
    ("Scoring", "Score candidates"),
    ("Otto", "Otto challenge"),
    ("rescue", "In-kind rescue"),
    ("review", "Review: Ray + Nicola"),
    ("Correction loop", "Review: Ray + Nicola"),
    ("Learning", "Learn"),
]


def _crossref_stage_for_message(msg: str) -> str | None:
    """Resolve the current crossref stage from a pipeline progress message."""
    for keyword, stage in _CROSSREF_KEYWORDS:
        if keyword.lower() in msg.lower():
            return stage
    return None


def _crossref_progress_cb(update: Any, stages: list[str]) -> Any:
    """Build a ``(msg, pct)`` progress callback that advances the declared
    crossref ``stages`` from the pipeline's emitted messages."""
    if hasattr(update, "set_stages"):
        update.set_stages(stages)

    def cb(msg: str, pct: int) -> None:
        if hasattr(update, "start_stage"):
            stage = _crossref_stage_for_message(msg)
            if stage is not None:
                update.start_stage(stage)
        update(f"{pct}% — {msg}")

    return cb


@app.post("/jobs/crossref")
def submit_crossref(req: CrossRefRequest) -> dict[str, str]:
    from heaviside.api.jobs import registry
    from heaviside.api.telemetry import wrap_job
    from heaviside.pipeline.crossref_pipeline import run_crossref_pipeline

    def run(update: Any) -> dict[str, Any]:
        cb = _crossref_progress_cb(update, _CROSSREF_CORE_STAGES)
        outcome = run_crossref_pipeline(
            req.source_bom,
            req.target_manufacturer,
            circuit_context=req.circuit_context,
            progress=cb,
        )
        return _crossref_outcome_dict(outcome)

    return {
        "job_id": registry.submit(
            "crossref",
            wrap_job(
                run,
                job_kind="crossref",
                input_type="bom_json",
                input_bom=req.source_bom,
                target_manufacturer=req.target_manufacturer,
            ),
        )
    }


@app.post("/jobs/crossref/from-pdf")
async def submit_crossref_from_pdf(
    target_manufacturer: str,
    file: UploadFile = File(...),
) -> dict[str, str]:
    """Upload a reference-design PDF → RE simulate → stress → cross-reference."""
    from heaviside.api.jobs import registry
    from heaviside.api.telemetry import wrap_job

    raw = await file.read()
    orig_name = file.filename or "reference.pdf"

    def run(update: Any) -> dict[str, Any]:
        import os
        import tempfile
        from pathlib import Path

        from heaviside.pipeline.crossref_pipeline import run_crossref_with_cre

        cb = _crossref_progress_cb(update, _CROSSREF_FULL_STAGES)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(raw)
            tmp = f.name
        try:
            outcome = run_crossref_with_cre(
                Path(orig_name).stem,
                target_manufacturer,
                pdf_path=Path(tmp),
                progress=cb,
                review_llm=True,
            )
        finally:
            os.unlink(tmp)
        return _crossref_outcome_dict(outcome)

    return {
        "job_id": registry.submit(
            "crossref_pdf",
            wrap_job(
                run,
                job_kind="crossref_pdf",
                input_type="pdf_file",
                input_file_name=orig_name,
                input_file_data=raw,
                target_manufacturer=target_manufacturer,
            ),
        )
    }


@app.post("/jobs/crossref/from-bom")
async def submit_crossref_from_bom(
    target_manufacturer: str,
    file: UploadFile = File(...),
) -> dict[str, str]:
    """Upload a bare BOM (CSV/TSV/XLSX) → cross-reference each component to the
    target manufacturer. No reference-design extraction — the file IS the
    component list. The BOM is parsed up front so a malformed file fails fast
    with 422 instead of inside the background job."""
    import asyncio
    import hashlib

    from heaviside.api.jobs import registry
    from heaviside.api.telemetry import wrap_job
    from heaviside.pipeline.bom_import import BomImportError, parse_bom_file

    raw = await file.read()
    orig_name = file.filename or "bom.csv"
    # Stamp the input's identity onto the job so "is this the same BOM?" is a
    # one-glance check, not a forensic diff of two result sets. The SHA-256 is of
    # the raw uploaded bytes — two uploads of the same file share a hash; an
    # edited/re-exported file (even at the same path) does not.
    input_sha256 = hashlib.sha256(raw).hexdigest()
    try:
        source_bom = await asyncio.to_thread(parse_bom_file, raw, orig_name)
    except BomImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    input_bom_rows = len(source_bom)

    def run(update: Any) -> dict[str, Any]:
        from heaviside.pipeline.crossref_pipeline import run_crossref_pipeline

        cb = _crossref_progress_cb(update, _CROSSREF_CORE_STAGES)
        outcome = run_crossref_pipeline(source_bom, target_manufacturer, progress=cb)
        result = _crossref_outcome_dict(outcome)
        result["input_file_name"] = orig_name
        result["input_sha256"] = input_sha256
        result["input_bom_rows"] = input_bom_rows
        return result

    return {
        "job_id": registry.submit(
            "crossref_bom",
            wrap_job(
                run,
                job_kind="crossref_bom",
                input_type="bom_file",
                input_file_name=orig_name,
                input_file_data=raw,
                input_bom=source_bom,
                target_manufacturer=target_manufacturer,
            ),
        )
    }


class CrossRefUrlRequest(BaseModel):
    url: str
    target_manufacturer: str


def _html_to_text(html: str) -> str:
    """Strip an HTML app-note page down to readable text (best-effort)."""
    import re

    html = re.sub(r"(?is)<(script|style|head|nav|footer)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = re.sub(r"&nbsp;", " ", html)
    html = re.sub(r"&amp;", "&", html)
    return re.sub(r"[ \t]*\n\s*", "\n", re.sub(r"[ \t]+", " ", html)).strip()


@app.post("/jobs/crossref/from-url")
def submit_crossref_from_url(req: CrossRefUrlRequest) -> dict[str, str]:
    """Fetch a reference design from a URL (PDF or HTML app-note), reverse-
    engineer it, then cross-reference its BOM to the target manufacturer."""
    from heaviside.api.jobs import registry
    from heaviside.api.telemetry import wrap_job
    from heaviside.pipeline.url_fetch import UnsafeURLError, guard_public_url

    # SSRF: validate the target synchronously so an internal/metadata URL is a
    # clean 400 (not a queued job), before spending any worker time on it.
    _pre = req.url.strip()
    if not _pre.lower().startswith(("http://", "https://")):
        _pre = "https://" + _pre
    try:
        guard_public_url(_pre)
    except UnsafeURLError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    def run(update: Any) -> dict[str, Any]:
        import os
        import tempfile
        from pathlib import Path

        from heaviside.pipeline.crossref_pipeline import run_crossref_with_cre
        from heaviside.pipeline.url_fetch import DocumentFetchError, fetch_document

        cb = _crossref_progress_cb(update, _CROSSREF_URL_STAGES)
        url = req.url.strip()
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url  # forgive a pasted bare URL
        name = (url.rsplit("/", 1)[-1].split("?")[0] or "design")[:50]
        cb(f"Downloading the reference from {name}", 0)
        # Manufacturer CDNs (Analog Devices, TI, Infineon) sit behind Akamai bot
        # protection that 403s a bare request; fetch_document escalates from a
        # browser-profile httpx call to a real headless Chromium when blocked.
        try:
            doc = fetch_document(url, timeout=90.0)
        except DocumentFetchError as exc:
            raise ValueError(str(exc)) from exc
        body = doc.content
        is_pdf = body[:5] == b"%PDF-" or "pdf" in doc.content_type.lower()

        if is_pdf:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(body)
                tmp = f.name
            try:
                outcome = run_crossref_with_cre(
                    Path(tmp).stem,
                    req.target_manufacturer,
                    pdf_path=Path(tmp),
                    progress=cb,
                    review_llm=True,
                )
            finally:
                os.unlink(tmp)
        else:
            text = _html_to_text(body.decode("utf-8", "ignore"))
            if len(text) < 200:
                raise ValueError(
                    f"fetched {len(body)} bytes from URL but extracted only "
                    f"{len(text)} chars of text — not a usable design document"
                )
            outcome = run_crossref_with_cre(
                name,
                req.target_manufacturer,
                pdf_text=text,
                progress=cb,
                review_llm=True,
            )
        return _crossref_outcome_dict(outcome)

    return {
        "job_id": registry.submit(
            "crossref_url",
            wrap_job(
                run,
                job_kind="crossref_url",
                input_type="url",
                input_url=req.url,
                target_manufacturer=req.target_manufacturer,
            ),
        )
    }


def _serialize_stages(job: Any) -> list[dict[str, Any]]:
    """Per-stage pipeline state for the Jobs UI: name, status, and real
    duration (counting up live while a stage runs)."""
    import time

    stages = getattr(job, "stages", None) or []
    now = time.monotonic()
    out: list[dict[str, Any]] = []
    for s in stages:
        dur = s.duration_s(now=now)
        out.append(
            {
                "name": s.name,
                "status": s.status,
                "duration_s": round(dur, 2) if dur is not None else None,
            }
        )
    return out


def _job_summary(job: Any) -> dict[str, Any]:
    """Compact view for the jobs list — no heavy `result` payload."""
    summary: str | None = None
    if job.status == "done" and isinstance(job.result, dict):
        r = job.result
        if "verdict" in r:  # design job
            summary = f"{r.get('topology') or '?'} — {r.get('verdict') or '?'}"
        elif "coverage_pct" in r and r.get("coverage_pct") is not None:
            summary = (
                f"{r['coverage_substituted']}/{r['coverage_total']} "
                f"({r['coverage_pct']}%) → {r.get('target_manufacturer')}"
            )
            # Append the input's identity so two runs of the "same" BOM are
            # distinguishable at a glance: name, parsed row count, short hash.
            name = r.get("input_file_name")
            sha = r.get("input_sha256")
            rows = r.get("input_bom_rows")
            if name and sha:
                summary += f" · {name} ({rows} rows, {sha[:12]})"
    elif job.status == "error":
        summary = job.error
    elif job.status in ("running", "queued"):
        summary = job.progress or None
    return {
        "job_id": job.id,
        "kind": job.kind,
        "status": job.status,
        "progress": job.progress,
        "summary": summary,
        "stages": _serialize_stages(job),
        "report_pdf": getattr(job, "report_pdf", "none"),
    }


@app.get("/jobs")
def list_jobs() -> dict[str, Any]:
    from heaviside.api.jobs import registry

    return {"jobs": [_job_summary(j) for j in registry.list_all()]}


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    from heaviside.api.jobs import registry

    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job_id")
    return {
        "job_id": job.id,
        "kind": job.kind,
        "status": job.status,
        "progress": job.progress,
        "result": job.result if job.status == "done" else None,
        "error": job.error,
        "stages": _serialize_stages(job),
        "report_pdf": getattr(job, "report_pdf", "none"),
    }


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    from heaviside.api.jobs import registry

    if not registry.cancel(job_id):
        raise HTTPException(status_code=409, detail="job not found or already finished")
    return {"job_id": job_id, "status": "cancel_requested"}


@app.delete("/jobs/{job_id}")
def delete_job(job_id: str) -> dict[str, Any]:
    from heaviside.api.jobs import registry

    if not registry.delete(job_id):
        raise HTTPException(status_code=409, detail="job not found or still in-flight")
    return {"job_id": job_id, "deleted": True}


# ---------------------------------------------------------------------------
# TAS catalogue browser — read-only parametric search over TAS/data/*.ndjson.
# Streams the NDJSON and early-stops once `limit` matches are found, so even
# the 130k-row capacitor file responds promptly for a typed query.
# ---------------------------------------------------------------------------


_MANUFACTURER_CACHE: dict[str, Any] | None = None


def _manufacturer_counts() -> dict[str, Any]:
    """Per-manufacturer component counts across the whole catalogue.

    Scans every TAS/data/*.ndjson once and caches it — the corpus is static for
    the life of the process. Manufacturer name lives at
    ``env[root].manufacturerInfo.name`` in every category.
    """
    global _MANUFACTURER_CACHE
    if _MANUFACTURER_CACHE is not None:
        return _MANUFACTURER_CACHE

    from collections import Counter

    from heaviside.catalogue._reader import iter_envelopes
    from heaviside.catalogue.selector import _tas_data_dir

    roots = {
        "mosfets.ndjson": "semiconductor",
        "diodes.ndjson": "semiconductor",
        "igbts.ndjson": "semiconductor",
        "capacitors.ndjson": "capacitor",
        "resistors.ndjson": "resistor",
        "magnetics.ndjson": "magnetic",
    }
    counts: Counter[str] = Counter()
    total = 0
    data_dir = _tas_data_dir()
    for filename, root in roots.items():
        path = data_dir / filename
        if not path.is_file():
            continue
        for _lineno, env in iter_envelopes(path):
            node = env.get(root)
            if root == "semiconductor" and isinstance(node, dict) and node:
                node = next(iter(node.values()), None)
            mi = node.get("manufacturerInfo") if isinstance(node, dict) else None
            name = mi.get("name") if isinstance(mi, dict) else None
            if isinstance(name, str) and name.strip():
                counts[name.strip()] += 1
                total += 1

    _MANUFACTURER_CACHE = {"total": total, "counts": counts}
    return _MANUFACTURER_CACHE


@app.get("/manufacturers")
def manufacturers(min_pct: float = 1.0) -> dict[str, Any]:
    """Vendors holding > min_pct of all catalogued components — the meaningful
    set of cross-reference targets (vendors we can actually source from).

    Default 1%: a whole-DB 5% cut is Vishay-dominated and would drop Würth and
    every magnetics/semiconductor vendor, so 1% is the practical floor.
    """
    data = _manufacturer_counts()
    total = data["total"]
    threshold = total * min_pct / 100.0
    leaders = [
        {"name": n, "count": c, "pct": round(100 * c / total, 1)}
        for n, c in data["counts"].most_common()
        if c >= threshold
    ]
    return {"total": total, "min_pct": min_pct, "manufacturers": leaders}


def _fold(s: str | None) -> str:
    """Accent- and case-insensitive key for search/grouping.

    Strips diacritics (ü→u, é→e) and case so a query like ``wurth`` matches
    the stored ``Würth Elektronik``, and the two spellings group together.
    Manufacturer-agnostic: it folds any accented name, not a hardcoded list.
    """
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch)).casefold().strip()


def _merge_by_fold(counter: Mapping[str, int]) -> list[tuple[str, int]]:
    """Merge name→count entries whose folded keys collide (e.g. ``Würth``
    and ``Wurth``), summing counts and displaying the dominant spelling.
    Returns (display_name, total) sorted by count descending.
    """
    from collections import Counter

    groups: dict[str, dict[str, Any]] = {}
    for name, cnt in counter.items():
        g = groups.setdefault(_fold(name), {"total": 0, "spellings": Counter()})
        g["total"] += cnt
        g["spellings"][name] += cnt
    merged = [(g["spellings"].most_common(1)[0][0], g["total"]) for g in groups.values()]
    merged.sort(key=lambda x: -x[1])
    return merged


def _fmt_eng(value: float | None, unit: str) -> str | None:
    """Engineering-notation string (e.g. 4.7e-6 F → '4.7 µF'). None passes through."""
    if value is None or not isinstance(value, (int, float)):
        return None
    if value == 0:
        return f"0 {unit}"
    prefixes = [
        (1e9, "G"),
        (1e6, "M"),
        (1e3, "k"),
        (1.0, ""),
        (1e-3, "m"),
        (1e-6, "µ"),
        (1e-9, "n"),
        (1e-12, "p"),
    ]
    av = abs(value)
    for scale, pre in prefixes:
        if av >= scale:
            return f"{value / scale:.3g} {pre}{unit}"
    return f"{value / 1e-12:.3g} p{unit}"


# ---------------------------------------------------------------------------
# Per-process caches: stats and facets are static for the lifetime of the
# process (TAS data files don't change while the server is running).
# ---------------------------------------------------------------------------
_STATS_CACHE: dict[str, int] | None = None
_FACETS_CACHE: dict[str, dict[str, Any]] = {}
_OVERVIEW_CACHE: dict[str, Any] | None = None
# Folded manufacturer key -> dominant (canonical) spelling, e.g.
# "wurth elektronik" -> "Würth Elektronik". Built during _build_overview
# (warmed on app load); used to normalize browse-row manufacturer display.
_MFR_CANONICAL: dict[str, str] = {}

# SI unit per category parameter, parallel to _CATALOG_LABELS. "%" is a sentinel
# meaning "format as a percentage" (used for resistor tolerance).
_CATALOG_UNITS: dict[str, list[str]] = {
    "mosfets": ["V", "Ω", "A"],
    "diodes": ["V", "A", "V"],
    "capacitors": ["F", "V", "Ω"],
    "resistors": ["Ω", "%", "W"],
    "magnetics": ["H", "A", "Ω"],
    "connectors": ["V", "A", "pos"],
    "analog": ["Hz", "V", "V"],
    "timing_devices": ["Hz", "ppm", "F"],
    "varistors": ["V", "V", "A"],
}

_CATALOG_FILES: dict[str, str] = {
    "mosfets": "mosfets.ndjson",
    "diodes": "diodes.ndjson",
    "capacitors": "capacitors.ndjson",
    "resistors": "resistors.ndjson",
    "magnetics": "magnetics.ndjson",
    "connectors": "connectors.ndjson",
    "analog": "analog_ics.ndjson",
    "timing_devices": "timing_devices.ndjson",
    "varistors": "varistors.ndjson",
}

_CATALOG_LABELS: dict[str, list[str]] = {
    "mosfets": ["Vds", "Rds(on)", "Id"],
    "diodes": ["Vrrm", "If(avg)", "Vf"],
    "capacitors": ["C", "V", "ESR"],
    "resistors": ["R", "Tol", "P"],
    "magnetics": ["L", "Isat", "DCR"],
    "connectors": ["V", "I/contact", "Pos"],
    "analog": ["GBW", "Vos", "Vs max"],
    "timing_devices": ["f", "Tol", "CL"],
    "varistors": ["Vv", "Vclamp", "Isurge"],
}


def _catalog_projectors() -> dict[str, Any]:
    """Return per-category (filename, project_fn) pairs.

    Each project_fn(env) returns a dict with string fields mpn/manufacturer/
    tech/p1/p2/p3/status PLUS float|None fields _p1n/_p2n/_p3n used for
    numeric filtering and sorting. Callers strip the _* fields before
    returning to the API client.
    """
    from heaviside.catalogue._reader import (
        iter_envelopes,  # noqa: F401 (unused here, kept for type ref)
    )
    from heaviside.catalogue.selector import Capacitor, Diode, Mosfet, Resistor

    def _mosfet(env: dict[str, Any]) -> dict[str, Any] | None:
        m = Mosfet.from_envelope(env)
        if m is None:
            return None
        return {
            "mpn": m.mpn,
            "manufacturer": m.manufacturer,
            "tech": m.technology,
            "p1": _fmt_eng(m.vds_rated, "V"),
            "p2": _fmt_eng(m.rds_on, "Ω"),
            "p3": _fmt_eng(m.id_continuous, "A"),
            "status": m.status,
            "_p1n": m.vds_rated,
            "_p2n": m.rds_on,
            "_p3n": m.id_continuous,
        }

    def _diode(env: dict[str, Any]) -> dict[str, Any] | None:
        d = Diode.from_envelope(env)
        if d is None:
            return None
        return {
            "mpn": d.mpn,
            "manufacturer": d.manufacturer,
            "tech": d.technology,
            "p1": _fmt_eng(d.vrrm_rated, "V"),
            "p2": _fmt_eng(d.if_avg_rated, "A"),
            "p3": _fmt_eng(d.vf_typ, "V"),
            "status": d.status,
            "_p1n": d.vrrm_rated,
            "_p2n": d.if_avg_rated,
            "_p3n": d.vf_typ,
        }

    def _cap(env: dict[str, Any]) -> dict[str, Any] | None:
        c = Capacitor.from_envelope(env)
        if c is None:
            return None
        try:
            cap_tech = (
                env["capacitor"]["manufacturerInfo"]["datasheetInfo"]["part"].get("technology")
                or ""
            )
        except (KeyError, TypeError):
            cap_tech = ""
        return {
            "mpn": c.mpn,
            "manufacturer": c.manufacturer,
            "tech": cap_tech,
            "p1": _fmt_eng(c.capacitance, "F"),
            "p2": _fmt_eng(c.v_rated, "V"),
            "p3": _fmt_eng(c.esr, "Ω"),
            "status": c.status,
            "_p1n": c.capacitance,
            "_p2n": c.v_rated,
            "_p3n": c.esr if c.esr else None,
        }

    def _res(env: dict[str, Any]) -> dict[str, Any] | None:
        r = Resistor.from_envelope(env)
        if r is None:
            return None
        return {
            "mpn": r.mpn,
            "manufacturer": r.manufacturer,
            "tech": "",
            "p1": _fmt_eng(r.resistance, "Ω"),
            "p2": f"{r.tolerance * 100:.3g}%",
            "p3": _fmt_eng(r.power_rating, "W"),
            "status": r.status,
            "_p1n": r.resistance,
            "_p2n": r.tolerance,
            "_p3n": r.power_rating if r.power_rating else None,
        }

    def _mag(env: dict[str, Any]) -> dict[str, Any] | None:
        try:
            m = env["magnetic"]["manufacturerInfo"]
            el_raw = m["datasheetInfo"].get("electrical")
            if isinstance(el_raw, list):
                el = next(
                    (i for i in el_raw if isinstance(i, dict) and "inductance" in i),
                    el_raw[0] if el_raw else {},
                )
            else:
                el = el_raw or {}
        except (KeyError, TypeError):
            return None
        ref, name = m.get("reference"), m.get("name")
        if not isinstance(ref, str) or not isinstance(name, str):
            return None

        def _scalar(v: Any) -> float | None:
            if isinstance(v, Mapping):
                v = v.get("nominal", v.get("maximum", v.get("minimum")))
            return v if isinstance(v, (int, float)) else None

        p1n = _scalar(el.get("inductance"))
        p2n = _scalar(el.get("saturationCurrentPeak"))
        p3n = _scalar(el.get("dcResistance"))
        return {
            "mpn": ref,
            "manufacturer": name,
            "tech": el.get("subtype") or "",
            "p1": _fmt_eng(p1n, "H"),
            "p2": _fmt_eng(p2n, "A"),
            "p3": _fmt_eng(p3n, "Ω"),
            "status": m.get("status", ""),
            "_p1n": p1n,
            "_p2n": p2n,
            "_p3n": p3n,
        }

    def _connector(env: dict[str, Any]) -> dict[str, Any] | None:
        try:
            mi = env["connector"]["manufacturerInfo"]
        except (KeyError, TypeError):
            return None
        ref, name = mi.get("reference"), mi.get("name")
        if not isinstance(ref, str) or not isinstance(name, str):
            return None
        di = mi.get("datasheetInfo") or {}
        el = di.get("electrical") or {}
        mech = di.get("mechanical") or {}
        fd = di.get("familyDetails") or {}

        def _scalar(v: Any) -> float | None:
            if isinstance(v, Mapping):
                v = v.get("nominal", v.get("maximum", v.get("minimum")))
            return v if isinstance(v, (int, float)) else None

        p1n = _scalar(el.get("ratedVoltage"))
        p2n = _scalar(el.get("ratedCurrentPerContact"))
        p3n = _scalar(mech.get("positions"))
        return {
            "mpn": ref,
            "manufacturer": name,
            "tech": fd.get("family") or "",
            "p1": _fmt_eng(p1n, "V"),
            "p2": _fmt_eng(p2n, "A"),
            "p3": f"{int(p3n)} pos" if p3n is not None else None,
            "status": mi.get("status", ""),
            "_p1n": p1n,
            "_p2n": p2n,
            "_p3n": p3n,
        }

    def _analog(env: dict[str, Any]) -> dict[str, Any] | None:
        from heaviside.pipeline.crossref_pipeline import _analog_subtype_block

        subtype, record = _analog_subtype_block(env.get("analog"))
        if record is None:
            return None
        mi = record.get("manufacturerInfo") or {}
        ref, name = mi.get("reference"), mi.get("name")
        if not isinstance(ref, str) or not isinstance(name, str):
            return None
        elec = (mi.get("datasheetInfo") or {}).get("electrical") or {}
        supply = elec.get("supply") or {}
        p1n = elec.get("gainBandwidthProduct")
        p2n = elec.get("inputOffsetVoltage")
        p3n = supply.get("maximumSupplyVoltage")
        p1n = p1n if isinstance(p1n, (int, float)) else None
        p2n = p2n if isinstance(p2n, (int, float)) else None
        p3n = p3n if isinstance(p3n, (int, float)) else None
        return {
            "mpn": ref,
            "manufacturer": name,
            "tech": subtype or "",
            "p1": _fmt_eng(p1n, "Hz"),
            "p2": _fmt_eng(p2n, "V"),
            "p3": _fmt_eng(p3n, "V"),
            "status": mi.get("status", ""),
            "_p1n": p1n,
            "_p2n": p2n,
            "_p3n": p3n,
        }

    def _timebase(env: dict[str, Any]) -> dict[str, Any] | None:
        from heaviside.pipeline.crossref_pipeline import _analog_subtype_block

        subtype, record = _analog_subtype_block(env.get("timeBase"))
        if record is None:
            return None
        mi = record.get("manufacturerInfo") or {}
        ref, name = mi.get("reference"), mi.get("name")
        if not isinstance(ref, str) or not isinstance(name, str):
            return None
        elec = (mi.get("datasheetInfo") or {}).get("electrical") or {}
        p1n = elec.get("frequency")
        p2n = elec.get("frequencyTolerance")  # fractional (2e-05 = 20 ppm)
        p3n = elec.get("loadCapacitance")
        p1n = p1n if isinstance(p1n, (int, float)) else None
        p2n = p2n if isinstance(p2n, (int, float)) else None
        p3n = p3n if isinstance(p3n, (int, float)) else None
        return {
            "mpn": ref,
            "manufacturer": name,
            "tech": elec.get("technology") or subtype or "",
            "p1": _fmt_eng(p1n, "Hz"),
            "p2": f"{p2n * 1e6:g} ppm" if p2n is not None else None,
            "p3": _fmt_eng(p3n, "F"),
            # Missing lifecycle displays as production (established webui
            # convention): rows come from a live distributor catalogue, so
            # they're orderable unless stated otherwise.
            "status": mi.get("status") or "production",
            "_p1n": p1n,
            "_p2n": p2n,
            "_p3n": p3n,
        }

    def _varistor(env: dict[str, Any]) -> dict[str, Any] | None:
        try:
            mi = env["varistor"]["manufacturerInfo"]
        except (KeyError, TypeError):
            return None
        ref, name = mi.get("reference"), mi.get("name")
        if not isinstance(ref, str) or not isinstance(name, str):
            return None
        di = mi.get("datasheetInfo") or {}
        el = di.get("electrical") or {}
        part = di.get("part") or {}

        def _scalar(v: Any) -> float | None:
            if isinstance(v, Mapping):
                v = v.get("nominal", v.get("maximum", v.get("minimum")))
            return v if isinstance(v, (int, float)) else None

        p1n = _scalar(el.get("varistorVoltage"))
        p2n = _scalar(el.get("clampingVoltage"))
        p3n = _scalar(el.get("peakSurgeCurrent"))
        return {
            "mpn": ref,
            "manufacturer": name,
            "tech": part.get("technology") or "",
            "p1": _fmt_eng(p1n, "V"),
            "p2": _fmt_eng(p2n, "V"),
            "p3": _fmt_eng(p3n, "A"),
            "status": mi.get("status", ""),
            "_p1n": p1n,
            "_p2n": p2n,
            "_p3n": p3n,
        }

    return {
        "mosfets": ("mosfets.ndjson", _mosfet),
        "diodes": ("diodes.ndjson", _diode),
        "capacitors": ("capacitors.ndjson", _cap),
        "resistors": ("resistors.ndjson", _res),
        "magnetics": ("magnetics.ndjson", _mag),
        "connectors": ("connectors.ndjson", _connector),
        "analog": ("analog_ics.ndjson", _analog),
        "timing_devices": ("timing_devices.ndjson", _timebase),
        "varistors": ("varistors.ndjson", _varistor),
    }


def _catalog_scan(
    category: str,
    *,
    query: str = "",
    tech: str = "",
    p1_min: float | None = None,
    p1_max: float | None = None,
    p2_min: float | None = None,
    p2_max: float | None = None,
    p3_min: float | None = None,
    p3_max: float | None = None,
    sort: str = "",
    order: str = "asc",
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[dict[str, Any]]]:
    """Full-scan a category NDJSON with all filters applied server-side.

    Returns (total_matching, page_of_rows). Numeric fields _p1n/_p2n/_p3n are
    stripped before returning.
    """
    from heaviside.catalogue._reader import iter_envelopes
    from heaviside.catalogue.selector import _tas_data_dir

    projectors = _catalog_projectors()
    if category not in projectors:
        raise HTTPException(
            status_code=404,
            detail=f"unknown category '{category}'; choose one of {sorted(projectors)}",
        )
    filename, project = projectors[category]
    path = _tas_data_dir() / filename
    q = _fold(query)
    tech_lo = tech.strip().lower()

    matched: list[dict[str, Any]] = []
    for _lineno, env in iter_envelopes(path):
        try:
            row = project(env)
        except Exception:
            continue
        if row is None:
            continue
        if q and q not in _fold(row["mpn"]) and q not in _fold(row["manufacturer"]):
            continue
        if tech_lo and tech_lo != (row["tech"] or "").lower():
            continue
        p1n: float | None = row["_p1n"]
        p2n: float | None = row["_p2n"]
        p3n: float | None = row["_p3n"]
        if p1_min is not None and (p1n is None or p1n < p1_min):
            continue
        if p1_max is not None and (p1n is None or p1n > p1_max):
            continue
        if p2_min is not None and (p2n is None or p2n < p2_min):
            continue
        if p2_max is not None and (p2n is None or p2n > p2_max):
            continue
        if p3_min is not None and (p3n is None or p3n < p3_min):
            continue
        if p3_max is not None and (p3n is None or p3n > p3_max):
            continue
        matched.append(row)

    # Sort on the numeric field (None values sort last regardless of direction).
    if sort in ("p1", "p2", "p3"):
        key_field = f"_{sort}n"
        rev = order == "desc"
        matched.sort(
            key=lambda r: (
                r[key_field] is None,
                -(r[key_field] or 0) if rev else (r[key_field] or 0),
            )
        )
    elif sort == "mpn":
        matched.sort(key=lambda r: (r["mpn"] or "").lower(), reverse=(order == "desc"))
    elif sort == "mfr":
        matched.sort(key=lambda r: (r["manufacturer"] or "").lower(), reverse=(order == "desc"))

    total = len(matched)
    page = matched[offset : offset + limit]
    for row in page:
        row.pop("_p1n", None)
        row.pop("_p2n", None)
        row.pop("_p3n", None)
        # Normalize manufacturer display to the canonical spelling (e.g.
        # "Wurth Elektronik" -> "Würth Elektronik") when the map is warmed.
        canon = _MFR_CANONICAL.get(_fold(row.get("manufacturer")))
        if canon:
            row["manufacturer"] = canon
    return total, page


def _build_stats() -> dict[str, int]:
    global _STATS_CACHE
    if _STATS_CACHE is not None:
        return _STATS_CACHE
    from heaviside.catalogue._reader import iter_envelopes
    from heaviside.catalogue.selector import _tas_data_dir

    projectors = _catalog_projectors()
    result: dict[str, int] = {}
    for cat, (fname, project) in projectors.items():
        path = _tas_data_dir() / fname
        n = 0
        for _, env in iter_envelopes(path):
            try:
                if project(env) is not None:
                    n += 1
            except Exception:
                pass
        result[cat] = n
    _STATS_CACHE = result
    return result


def _build_facets(category: str) -> dict[str, Any]:
    if category in _FACETS_CACHE:
        return _FACETS_CACHE[category]
    from heaviside.catalogue._reader import iter_envelopes
    from heaviside.catalogue.selector import _tas_data_dir

    projectors = _catalog_projectors()
    if category not in projectors:
        raise HTTPException(status_code=404, detail=f"unknown category '{category}'")
    filename, project = projectors[category]
    path = _tas_data_dir() / filename

    techs: set[str] = set()
    p1_vals: list[float] = []
    p2_vals: list[float] = []
    p3_vals: list[float] = []
    total = 0
    for _, env in iter_envelopes(path):
        try:
            row = project(env)
        except Exception:
            continue
        if row is None:
            continue
        total += 1
        t = row.get("tech") or ""
        if t:
            techs.add(t)
        v1: float | None = row.get("_p1n")
        v2: float | None = row.get("_p2n")
        v3: float | None = row.get("_p3n")
        if v1 is not None and v1 > 0:
            p1_vals.append(v1)
        if v2 is not None and v2 > 0:
            p2_vals.append(v2)
        if v3 is not None and v3 > 0:
            p3_vals.append(v3)

    result = {
        "total": total,
        "techs": sorted(techs),
        "p1": {"min": min(p1_vals) if p1_vals else None, "max": max(p1_vals) if p1_vals else None},
        "p2": {"min": min(p2_vals) if p2_vals else None, "max": max(p2_vals) if p2_vals else None},
        "p3": {"min": min(p3_vals) if p3_vals else None, "max": max(p3_vals) if p3_vals else None},
    }
    _FACETS_CACHE[category] = result
    return result


def _build_overview() -> dict[str, Any]:
    """Rich per-category aggregates for the catalog overview dashboard.

    One scan per category (cached for process lifetime). Per category we surface:
    count, production count, technology histogram, top manufacturers, and the
    covered span of each parametric axis. This powers the "show-off" overview
    view; it is intentionally heavier than ``/catalog/stats`` (counts only).
    """
    global _OVERVIEW_CACHE, _MFR_CANONICAL
    if _OVERVIEW_CACHE is not None:
        return _OVERVIEW_CACHE
    from collections import Counter

    # Folded key -> all observed spellings (across every category), used to
    # pick one canonical display name per manufacturer.
    global_spellings: dict[str, Counter] = {}

    from heaviside.catalogue._reader import iter_envelopes
    from heaviside.catalogue.selector import _tas_data_dir

    def _fmt_param(value: float | None, unit: str) -> str | None:
        if value is None:
            return None
        if unit == "%":
            return f"{value * 100:.3g}%"
        if unit == "ppm":
            # Stored as a fraction (2e-05); engineers read ppm, and the
            # engineering-prefix formatter would render "20 µppm".
            return f"{value * 1e6:g} ppm"
        return _fmt_eng(value, unit)

    projectors = _catalog_projectors()
    categories: dict[str, Any] = {}
    grand_total = 0
    all_mfrs: set[str] = set()

    for cat, (fname, project) in projectors.items():
        path = _tas_data_dir() / fname
        labels = _CATALOG_LABELS.get(cat, ["", "", ""])
        units = _CATALOG_UNITS.get(cat, ["", "", ""])

        count = 0
        production = 0
        techs: Counter[str] = Counter()
        mfrs: Counter[str] = Counter()
        mins: list[float | None] = [None, None, None]
        maxs: list[float | None] = [None, None, None]

        for _, env in iter_envelopes(path):
            try:
                row = project(env)
            except Exception:
                continue
            if row is None:
                continue
            count += 1
            if (row.get("status") or "").lower() == "production":
                production += 1
            t = row.get("tech") or ""
            if t:
                techs[t] += 1
            m = row.get("manufacturer") or ""
            if m:
                mfrs[m] += 1
                all_mfrs.add(_fold(m))
            for i, key in enumerate(("_p1n", "_p2n", "_p3n")):
                v = row.get(key)
                if not isinstance(v, (int, float)) or v <= 0:
                    continue
                if mins[i] is None or v < mins[i]:
                    mins[i] = v
                if maxs[i] is None or v > maxs[i]:
                    maxs[i] = v

        params = [
            {
                "label": labels[i],
                "min": mins[i],
                "max": maxs[i],
                "minFmt": _fmt_param(mins[i], units[i]),
                "maxFmt": _fmt_param(maxs[i], units[i]),
            }
            for i in range(3)
        ]
        for name, cnt in mfrs.items():
            global_spellings.setdefault(_fold(name), Counter())[name] += cnt
        merged_mfrs = _merge_by_fold(mfrs)
        categories[cat] = {
            "count": count,
            "production": production,
            "manufacturerCount": len(merged_mfrs),
            "techs": [{"name": n, "count": c} for n, c in techs.most_common()],
            "manufacturers": [{"name": n, "count": c} for n, c in merged_mfrs[:8]],
            "params": params,
        }
        grand_total += count

    _MFR_CANONICAL = {
        key: spellings.most_common(1)[0][0] for key, spellings in global_spellings.items()
    }
    _OVERVIEW_CACHE = {
        "total": grand_total,
        "manufacturerTotal": len(all_mfrs),
        "categories": categories,
    }
    return _OVERVIEW_CACHE


@app.get("/catalog/overview")
def catalog_overview() -> dict[str, Any]:
    """Rich aggregate stats across the whole component DB (cached)."""
    return _build_overview()


def _warm_catalog_caches() -> None:
    """Pre-compute every catalog aggregate so the first visitor to the Catalog /
    Overview tab doesn't pay for a full NDJSON scan. Each builder caches for the
    process lifetime and is idempotent, so a later request just reads the cache.

    Run in a background thread at startup: the server is ready to serve
    immediately, and the (multi-second) corpus scans finish shortly after boot.
    A failure here is logged but never fatal — the endpoints still compute
    lazily on demand, exactly as before warming was added."""
    import time

    t0 = time.monotonic()
    try:
        _build_overview()  # also warms _MFR_CANONICAL (browse-row display)
        _build_stats()
        _manufacturer_counts()
        for category in _catalog_projectors():
            _build_facets(category)
    except Exception:
        logger.exception("catalog cache warming failed (endpoints will compute lazily)")
        return
    logger.info("catalog caches warmed in %.1fs", time.monotonic() - t0)


@app.on_event("startup")
def _warm_caches_on_startup() -> None:
    """Kick catalog cache warming onto a daemon thread at boot — non-blocking so
    the server becomes ready instantly and health checks pass during the scan."""
    import threading

    threading.Thread(target=_warm_catalog_caches, name="catalog-cache-warm", daemon=True).start()


@app.get("/catalog/stats")
def catalog_stats() -> dict[str, Any]:
    """Total valid component counts per category (cached for process lifetime)."""
    counts = _build_stats()
    return {"counts": counts, "total": sum(counts.values())}


@app.get("/catalog/{category}/facets")
def catalog_facets(category: str) -> dict[str, Any]:
    """Available filter values and numeric ranges for a category (cached)."""
    return _build_facets(category)


@app.get("/catalog/{category}/{mpn}/detail")
def catalog_detail(category: str, mpn: str) -> dict[str, Any]:
    """Return the raw full PEAS data for a single MPN so the UI can render a datasheet."""
    from heaviside.catalogue._reader import iter_envelopes
    from heaviside.catalogue.selector import _tas_data_dir

    _NDJSON: dict[str, str] = {
        "mosfets": "mosfets.ndjson",
        "diodes": "diodes.ndjson",
        "capacitors": "capacitors.ndjson",
        "resistors": "resistors.ndjson",
        "magnetics": "magnetics.ndjson",
        "connectors": "connectors.ndjson",
        "analog": "analog_ics.ndjson",
        "timing_devices": "timing_devices.ndjson",
        "varistors": "varistors.ndjson",
    }
    if category not in _NDJSON:
        raise HTTPException(status_code=404, detail=f"unknown category '{category}'")
    path = _tas_data_dir() / _NDJSON[category]
    mpn_lo = mpn.strip().lower()
    for _lineno, env in iter_envelopes(path):
        # Locate the manufacturerInfo dict regardless of the envelope key.
        for key in (
            "semiconductor",
            "capacitor",
            "resistor",
            "magnetic",
            "connector",
            "analog",
            "timeBase",
            "varistor",
        ):
            sub = env.get(key, {})
            if not sub:
                continue
            for inner_key in list(sub.keys()):
                # Inner values may be lists (e.g. distributorsInfo) — only
                # dicts can nest a manufacturerInfo. A TBAS document's first
                # child may be `inputs` (no manufacturerInfo) with the real
                # record under a later sibling — keep scanning, don't break.
                inner = sub[inner_key]
                mi = (
                    inner.get("manufacturerInfo") if isinstance(inner, dict) else None
                ) or sub.get("manufacturerInfo")
                if mi is None:
                    continue
                # Capacitors / resistors carry the MPN in part.partNumber, not
                # in manufacturerInfo.reference.
                part = (mi.get("datasheetInfo") or {}).get("part") or {}
                ref = mi.get("reference") or part.get("partNumber") or mi.get("name", "")
                if ref.lower() == mpn_lo:
                    return {"category": category, "mpn": mpn, "data": env}
    raise HTTPException(status_code=404, detail=f"MPN '{mpn}' not found in {category}")


@app.get("/catalog/{category}")
def catalog(
    category: str,
    q: str = "",
    limit: int = 50,
    offset: int = 0,
    tech: str = "",
    sort: str = "",
    order: str = "asc",
    p1_min: float | None = None,
    p1_max: float | None = None,
    p2_min: float | None = None,
    p2_max: float | None = None,
    p3_min: float | None = None,
    p3_max: float | None = None,
) -> dict[str, Any]:
    """Parametric browse with full filtering, sorting, and pagination.

    Numeric range params (p1_min/p1_max etc.) are in SI units (V, Ω, A, F, H).
    `sort` accepts p1|p2|p3|mpn|mfr; `order` accepts asc|desc.
    Returns `total` (all matches before limit) alongside the `rows` page.
    """
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    total, rows = _catalog_scan(
        category,
        query=q,
        tech=tech,
        sort=sort,
        order=order,
        p1_min=p1_min,
        p1_max=p1_max,
        p2_min=p2_min,
        p2_max=p2_max,
        p3_min=p3_min,
        p3_max=p3_max,
        limit=limit,
        offset=offset,
    )
    return {
        "category": category,
        "total": total,
        "offset": offset,
        "param_labels": _CATALOG_LABELS.get(category, ["", "", ""]),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Static SPA (Vue 3 + PrimeVue). Served last so API routes take precedence.
# ---------------------------------------------------------------------------

from pathlib import Path as _Path  # noqa: E402

_STATIC_DIR = _Path(__file__).parent / "static"


@app.get("/")
def index() -> Any:
    from fastapi.responses import FileResponse, HTMLResponse

    idx = _STATIC_DIR / "index.html"
    if idx.is_file():
        return FileResponse(str(idx))
    return HTMLResponse("<h1>Heaviside</h1><p>UI not built.</p>")


@app.get("/favicon.ico")
def favicon() -> Any:
    from fastapi.responses import Response

    return Response(status_code=204)


def _mount_static() -> None:
    from fastapi.staticfiles import StaticFiles

    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


_mount_static()
