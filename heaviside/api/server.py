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

import json
import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Heaviside",
    description="Power converter auto-design API",
    version="0.1.0",
)


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

    return [
        {"name": e.name, "family": e.family, "kind": e.kind}
        for e in CONVERTERS
    ]


@app.post("/design", response_model=DesignResponse)
def design(req: DesignRequest) -> dict[str, Any]:
    from heaviside.pipeline.full_design import (
        full_design,
        stage1_topology_screen,
        stage2_pick_magnetics,
    )

    try:
        selector_fn = None
        if req.topologies:
            selector_fn = lambda s: (req.topologies, "user-specified")

        stage1, stage2, outcomes = full_design(
            req.spec,
            n_candidates_per_topology=req.candidates_per_topology,
            pick_criteria=req.pick_criteria,
            core_mode=req.core_mode,
            parallel=True,
            selector_fn=selector_fn,
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

        outcome_list.append({
            "topology": o.pick.topology.name,
            "verdict": o.verdict_dict["verdict"] if o.verdict_dict else None,
            "gatekeeper_approved": o.gatekeeper.approved if o.gatekeeper else None,
            "scoring": o.pick.main_magnetic.scoring,
            "bom": bom_entries,
            "report": o.report,
            "diagnostics": list(o.diagnostics),
        })

    return {
        "stage1_topologies": list(stage1.reconciliation.chosen),
        "stage2_picks": len(stage2.picks),
        "stage2_failures": [
            {"topology": t, "error": e} for t, e in stage2.failures
        ],
        "outcomes": outcome_list,
    }


@app.post("/design/magnetic")
def design_magnetic(req: MagneticRequest) -> dict[str, Any]:
    from heaviside.bridge import BridgeError, design_magnetics_fast

    try:
        candidates = design_magnetics_fast(
            req.topology, req.spec,
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
        selector_fn = None
        if req.topologies:
            selector_fn = lambda s: (req.topologies, "user-specified")

        _, _, outcomes = full_design(
            req.spec,
            n_candidates_per_topology=req.candidates_per_topology,
            pick_criteria=req.pick_criteria,
            core_mode=req.core_mode,
            parallel=True,
            selector_fn=selector_fn,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    best = next(
        (o for o in outcomes
         if o.verdict_dict and o.verdict_dict["verdict"] == "pass"
         and o.gatekeeper and o.gatekeeper.approved),
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
            req.tas, topology=req.topology, spec=req.spec,
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
# CRE (Competitor Reverse-Engineering)
# ---------------------------------------------------------------------------


class CRERequest(BaseModel):
    reference: str
    pdf_text: str | None = None


@app.post("/cre")
def cre_endpoint(req: CRERequest) -> dict[str, Any]:
    """Run the CRE pipeline on a reference design."""
    from heaviside.pipeline.cre_pipeline import run_cre_pipeline
    import tempfile
    from pathlib import Path

    pdf_path = None
    if req.pdf_text:
        tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w")
        tmp.write(req.pdf_text)
        tmp.close()
        pdf_path = Path(tmp.name)

    try:
        outcome = run_cre_pipeline(req.reference, pdf_path=pdf_path)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

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
            req.source_bom, req.target_manufacturer,
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
# Async jobs — the design / CRE / cross-reference pipelines take minutes, so
# the UI submits a job and polls. Workers serialize LLM-heavy runs (avoids
# Moonshot 429). See heaviside/api/jobs.py.
# ---------------------------------------------------------------------------


def _crossref_outcome_dict(outcome: Any) -> dict[str, Any]:
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
        "diagnostics": list(outcome.diagnostics),
    }


def _design_job(spec: dict[str, Any], n: int) -> dict[str, Any]:
    from heaviside.pipeline.full_design import full_design
    from heaviside.report import render_html

    _, _, outcomes = full_design(spec, n_candidates_per_topology=n, parallel=True)
    if not outcomes:
        return {"html": "<p>No design survived the pipeline.</p>", "topology": None,
                "verdict": None}
    best = next(
        (o for o in outcomes
         if o.verdict_dict and o.verdict_dict.get("verdict") == "pass"),
        outcomes[0],
    )
    return {
        "topology": best.pick.topology.name,
        "verdict": best.verdict_dict.get("verdict") if best.verdict_dict else None,
        "html": render_html(best),
        "alternatives": [
            {"topology": o.pick.topology.name,
             "verdict": o.verdict_dict.get("verdict") if o.verdict_dict else None}
            for o in outcomes
        ],
    }


@app.post("/jobs/design")
def submit_design(req: DesignRequest) -> dict[str, str]:
    from heaviside.api.jobs import registry
    job_id = registry.submit(
        "design", lambda: _design_job(req.spec, req.candidates_per_topology)
    )
    return {"job_id": job_id}


@app.post("/jobs/crossref")
def submit_crossref(req: CrossRefRequest) -> dict[str, str]:
    from heaviside.api.jobs import registry
    from heaviside.pipeline.crossref_pipeline import run_crossref_pipeline

    def run() -> dict[str, Any]:
        outcome = run_crossref_pipeline(
            req.source_bom, req.target_manufacturer,
            circuit_context=req.circuit_context,
        )
        return _crossref_outcome_dict(outcome)

    return {"job_id": registry.submit("crossref", run)}


@app.post("/jobs/crossref/from-pdf")
async def submit_crossref_from_pdf(
    target_manufacturer: str,
    file: UploadFile = File(...),
) -> dict[str, str]:
    """Upload a reference-design PDF → CRE simulate → stress → cross-reference."""
    from heaviside.api.jobs import registry

    raw = await file.read()

    def run() -> dict[str, Any]:
        import tempfile, os
        from pathlib import Path
        from heaviside.pipeline.crossref_pipeline import run_crossref_with_cre
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(raw)
            tmp = f.name
        try:
            outcome = run_crossref_with_cre(
                Path(tmp).stem, target_manufacturer, pdf_path=Path(tmp),
            )
        finally:
            os.unlink(tmp)
        return _crossref_outcome_dict(outcome)

    return {"job_id": registry.submit("crossref_pdf", run)}


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
