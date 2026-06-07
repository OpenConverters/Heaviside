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
# CRE (Competitor Reverse-Engineering)
# ---------------------------------------------------------------------------


class CRERequest(BaseModel):
    reference: str
    pdf_text: str | None = None


@app.post("/cre")
def cre_endpoint(req: CRERequest) -> dict[str, Any]:
    """Run the CRE pipeline on a reference design."""
    import tempfile
    from pathlib import Path

    from heaviside.pipeline.cre_pipeline import run_cre_pipeline

    pdf_path = None
    if req.pdf_text:
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as tmp:
            tmp.write(req.pdf_text)
        pdf_path = Path(tmp.name)

    try:
        outcome = run_cre_pipeline(req.reference, pdf_path=pdf_path)
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
# Async jobs — the design / CRE / cross-reference pipelines take minutes, so
# the UI submits a job and polls. Workers serialize LLM-heavy runs (avoids
# Moonshot 429). See heaviside/api/jobs.py.
# ---------------------------------------------------------------------------


def _crossref_outcome_dict(outcome: Any) -> dict[str, Any]:
    components = [
        {
            "ref_des": c.ref_des,
            "component_type": c.component_type,
            "original_mpn": c.original_mpn,
            "substitute_mpn": c.substitute_mpn,
            "status": c.status.value,
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
    from heaviside.pipeline.cre import compute_desired_inductance
    from heaviside.pipeline.full_design import full_design
    from heaviside.report import render_html

    # The web form posts a bare electrical spec. The MKF magnetic designer
    # additionally needs `desiredInductance` (and, for isolated topologies,
    # turns ratios) — the CRE path computes these in to_heaviside_spec. Mirror
    # the inductance sizing here so a minimal form yields a real design.
    spec = dict(spec)
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

        def progress_cb(msg, pct):
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

    job_id = registry.submit(
        "design",
        lambda update: _design_job(req.spec, req.candidates_per_topology, req.topologies, update),
    )
    return {"job_id": job_id}


@app.post("/jobs/crossref")
def submit_crossref(req: CrossRefRequest) -> dict[str, str]:
    from heaviside.api.jobs import registry
    from heaviside.pipeline.crossref_pipeline import run_crossref_pipeline

    def run(update: Any) -> dict[str, Any]:
        update(f"Cross-referencing {len(req.source_bom)} parts → {req.target_manufacturer}")
        outcome = run_crossref_pipeline(
            req.source_bom,
            req.target_manufacturer,
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
    orig_name = file.filename or "reference.pdf"

    def run(update: Any) -> dict[str, Any]:
        import os
        import tempfile
        from pathlib import Path

        from heaviside.pipeline.crossref_pipeline import run_crossref_with_cre

        update(f"Reverse-engineering {orig_name} → {target_manufacturer}")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(raw)
            tmp = f.name
        try:
            outcome = run_crossref_with_cre(
                Path(tmp).stem,
                target_manufacturer,
                pdf_path=Path(tmp),
            )
        finally:
            os.unlink(tmp)
        return _crossref_outcome_dict(outcome)

    return {"job_id": registry.submit("crossref_pdf", run)}


@app.post("/jobs/crossref/from-bom")
async def submit_crossref_from_bom(
    target_manufacturer: str,
    file: UploadFile = File(...),
) -> dict[str, str]:
    """Upload a bare BOM (CSV/TSV/XLSX) → cross-reference each component to the
    target manufacturer. No reference-design extraction — the file IS the
    component list. The BOM is parsed up front so a malformed file fails fast
    with 422 instead of inside the background job."""
    from heaviside.api.jobs import registry
    from heaviside.pipeline.bom_import import BomImportError, parse_bom_file

    raw = await file.read()
    orig_name = file.filename or "bom.csv"
    try:
        source_bom = parse_bom_file(raw, orig_name)
    except BomImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    def run(update: Any) -> dict[str, Any]:
        from heaviside.pipeline.crossref_pipeline import run_crossref_pipeline

        update(
            f"Cross-referencing {len(source_bom)} parts from {orig_name} → {target_manufacturer}"
        )
        outcome = run_crossref_pipeline(source_bom, target_manufacturer)
        return _crossref_outcome_dict(outcome)

    return {"job_id": registry.submit("crossref_bom", run)}


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

    def run(update: Any) -> dict[str, Any]:
        import os
        import tempfile
        from pathlib import Path

        import httpx

        from heaviside.pipeline.crossref_pipeline import run_crossref_with_cre

        url = req.url.strip()
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url  # forgive a pasted bare URL
        name = (url.rsplit("/", 1)[-1].split("?")[0] or "design")[:50]
        update(f"Downloading {name}…")
        resp = httpx.get(
            url,
            follow_redirects=True,
            timeout=90.0,
            headers={"User-Agent": "Mozilla/5.0 (Heaviside crossref)"},
        )
        resp.raise_for_status()
        body = resp.content
        is_pdf = body[:5] == b"%PDF-" or "pdf" in resp.headers.get("content-type", "").lower()

        update(f"Reverse-engineering {name} → {req.target_manufacturer}")
        if is_pdf:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(body)
                tmp = f.name
            try:
                outcome = run_crossref_with_cre(
                    Path(tmp).stem,
                    req.target_manufacturer,
                    pdf_path=Path(tmp),
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
            )
        return _crossref_outcome_dict(outcome)

    return {"job_id": registry.submit("crossref_url", run)}


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


def _catalog_rows(category: str, query: str, limit: int) -> list[dict[str, Any]]:
    from heaviside.catalogue._reader import iter_envelopes
    from heaviside.catalogue.selector import (
        Capacitor,
        Diode,
        Mosfet,
        Resistor,
        _tas_data_dir,
    )

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
        }

    def _cap(env: dict[str, Any]) -> dict[str, Any] | None:
        c = Capacitor.from_envelope(env)
        if c is None:
            return None
        return {
            "mpn": c.mpn,
            "manufacturer": c.manufacturer,
            "tech": c.technology,
            "p1": _fmt_eng(c.capacitance, "F"),
            "p2": _fmt_eng(c.v_rated, "V"),
            "p3": _fmt_eng(c.esr, "Ω"),
            "status": c.status,
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
        }

    def _mag(env: dict[str, Any]) -> dict[str, Any] | None:
        try:
            m = env["magnetic"]["manufacturerInfo"]
            el = m["datasheetInfo"].get("electrical", {})
        except (KeyError, TypeError):
            return None
        ref, name = m.get("reference"), m.get("name")
        if not isinstance(ref, str) or not isinstance(name, str):
            return None

        def _scalar(v: Any) -> float | None:
            if isinstance(v, Mapping):
                v = v.get("nominal", v.get("maximum", v.get("minimum")))
            return v if isinstance(v, (int, float)) else None

        return {
            "mpn": ref,
            "manufacturer": name,
            "tech": m.get("family", ""),
            "p1": _fmt_eng(_scalar(el.get("inductance")), "H"),
            "p2": _fmt_eng(_scalar(el.get("saturationCurrentPeak")), "A"),
            "p3": _fmt_eng(_scalar(el.get("dcResistance")), "Ω"),
            "status": m.get("status", ""),
        }

    catalog: dict[str, tuple[str, Any]] = {
        "mosfets": ("mosfets.ndjson", _mosfet),
        "diodes": ("diodes.ndjson", _diode),
        "capacitors": ("capacitors.ndjson", _cap),
        "resistors": ("resistors.ndjson", _res),
        "magnetics": ("magnetics.ndjson", _mag),
    }
    if category not in catalog:
        raise HTTPException(
            status_code=404,
            detail=f"unknown category '{category}'; choose one of {sorted(catalog)}",
        )
    filename, project = catalog[category]
    path = _tas_data_dir() / filename
    q = query.strip().lower()
    rows: list[dict[str, Any]] = []
    for _lineno, env in iter_envelopes(path):
        try:
            row = project(env)
        except Exception:
            continue
        if row is None:
            continue
        if (
            q
            and q not in (row["mpn"] or "").lower()
            and q not in (row["manufacturer"] or "").lower()
        ):
            continue
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


@app.get("/catalog/{category}")
def catalog(category: str, q: str = "", limit: int = 50) -> dict[str, Any]:
    """Parametric browse of a TAS component category. `q` matches MPN or
    manufacturer (case-insensitive substring). Columns p1/p2/p3 are the three
    headline parameters for that category (units vary — see `param_labels`)."""
    limit = max(1, min(limit, 200))
    labels = {
        "mosfets": ["Vds", "Rds(on)", "Id"],
        "diodes": ["Vrrm", "If(avg)", "Vf"],
        "capacitors": ["C", "V", "ESR"],
        "resistors": ["R", "Tol", "P"],
        "magnetics": ["L", "Isat", "DCR"],
    }
    rows = _catalog_rows(category, q, limit)
    return {
        "category": category,
        "count": len(rows),
        "param_labels": labels.get(category, ["", "", ""]),
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
