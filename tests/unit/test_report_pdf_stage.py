"""The report PDF is a TRACKED post-job stage: report_pdf goes
rendering→ready(/error), a 'Generate report PDF' stage appears in the timeline,
and both survive persistence."""

from __future__ import annotations

import tempfile
import time

from heaviside.api.jobs import REPORT_PDF_STAGE, Job, JobRegistry


def _reg():
    return JobRegistry(persist_dir=tempfile.mkdtemp())


def _done_job(reg, jid="j1", result=None):
    job = Job(
        id=jid,
        kind="crossref_bom",
        status="done",
        result=result if result is not None else {"components": [{"ref_des": "C1"}]},
        created_wall=time.time(),
    )
    with reg._lock:
        reg._jobs[jid] = job
    return job


def test_report_stage_begin_marks_rendering():
    reg = _reg()
    _done_job(reg)
    reg.report_stage_begin("j1")
    j = reg.get("j1")
    assert j.report_pdf == "rendering"
    st = next(s for s in j.stages if s.name == REPORT_PDF_STAGE)
    assert st.status == "running"


def test_report_stage_end_ok_ready():
    reg = _reg()
    _done_job(reg)
    reg.report_stage_begin("j1")
    reg.report_stage_end("j1", ok=True)
    j = reg.get("j1")
    assert j.report_pdf == "ready"
    assert next(s for s in j.stages if s.name == REPORT_PDF_STAGE).status == "done"


def test_report_stage_end_fail_error():
    reg = _reg()
    _done_job(reg)
    reg.report_stage_begin("j1")
    reg.report_stage_end("j1", ok=False)
    j = reg.get("j1")
    assert j.report_pdf == "error"
    assert next(s for s in j.stages if s.name == REPORT_PDF_STAGE).status == "error"


def test_report_pdf_persisted_and_restored():
    d = tempfile.mkdtemp()
    reg = JobRegistry(persist_dir=d)
    _done_job(reg)
    reg.report_stage_begin("j1")
    reg.report_stage_end("j1", ok=True)
    # a fresh registry restores from disk
    reg2 = JobRegistry(persist_dir=d)
    j = reg2.get("j1")
    assert j is not None and j.report_pdf == "ready"
    assert any(s.name == REPORT_PDF_STAGE for s in j.stages)


def test_default_report_pdf_is_none():
    reg = _reg()
    j = _done_job(reg, "j2")
    assert j.report_pdf == "none"
