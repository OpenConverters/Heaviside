"""submit_crossref_bom: an uploaded BOM FILE in, a cross-reference job out.

The model never sees an uploaded file's contents, so it cannot build the typed
`source_bom` submit_crossref needs. This tool takes the file's reference and
parses it server-side with bom_import (the parser the web API uses), then queues
the SAME job submit_crossref queues — so job_status / job_result are unchanged.

The 15-minute pipeline is mocked (as in test_mcp_contract): what is tested here
is the file -> rows -> job path and the payload shapes, not the LLM chain. The
header mapper is switched off (HEAVISIDE_JEV=0, no LLM key) so the parse is the
deterministic one and the test does not depend on ambient API keys.
"""

from __future__ import annotations

import asyncio
import io
import time
from types import SimpleNamespace

import pytest

from heaviside import mcp_jobs
from heaviside import mcp_server as ms
from heaviside.pipeline.bom_import import BomImportError

from .test_mcp_contract import _structured, _validate

_CSV = (
    b"Designator,MPN,Manufacturer,Value,Quantity\n"
    b"Q1,IRFZ44N,Infineon,,1\n"
    b"D1,STPS3045,ST,,1\n"
    b"C1,,,100nF,4\n"
)


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    """A fresh job registry, a deterministic parse, and a recorded fake pipeline."""
    monkeypatch.setenv("HEAVISIDE_JEV", "0")
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(mcp_jobs, "_registry",
                        mcp_jobs.JobRegistry(root=tmp_path / "jobs", concurrency=1))
    calls: list[dict] = []

    def fake(rows, target, circuit_context=None, progress=None):
        calls.append({"rows": rows, "target": target, "context": circuit_context})
        if progress:
            progress("CR stage 1: parse", 0.1)
        return SimpleNamespace(
            passed=True, diagnostics=[],
            components=[SimpleNamespace(ref_des=r.get("ref_des"),
                                        original_mpn=r.get("original_mpn", ""),
                                        substitute_mpn="WE-" + r.get("ref_des", "?"),
                                        status=SimpleNamespace(value="recommended"))
                        for r in rows])

    monkeypatch.setattr("heaviside.pipeline.crossref_pipeline.run_crossref_pipeline", fake)
    return calls


def _wait_done(job_id: str) -> None:
    deadline = time.time() + 15
    while time.time() < deadline:
        state = _structured(ms.job_status(job_id))["state"]
        if state in ("done", "failed", "cancelled"):
            assert state == "done", _structured(ms.job_status(job_id))
            return
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


def _submit(ref: str, **kw):
    return asyncio.run(ms.submit_crossref_bom(bom=ref, target_manufacturer="Wurth", **kw))


def test_csv_is_parsed_and_submitted_as_a_job(pipeline, tmp_path) -> None:
    path = tmp_path / "board_bom.csv"
    path.write_bytes(_CSV)

    result = _submit(str(path), circuit_context="48 V buck")
    queued = _structured(result)
    assert queued["mode"] == "job" and queued["state"] in ("queued", "running", "done")
    _validate(queued)
    assert "3 line(s) from board_bom.csv" in queued["label"]
    # The line without a part number is NAMED, not just counted.
    assert "1 without a part number" in queued["caveat"] and "C1" in queued["caveat"]
    assert "Parsed 3 line(s), 1 without a part number" in result.content[0].text

    _wait_done(queued["job"])
    rows = pipeline[0]["rows"]
    assert [r.get("ref_des") for r in rows] == ["Q1", "D1", "C1"]
    assert rows[0]["original_mpn"] == "IRFZ44N" and "original_mpn" not in rows[2]
    assert pipeline[0]["context"] == "48 V buck"

    done = _structured(ms.job_result(queued["job"]))
    _validate(done)
    assert done["result"]["mode"] == "bom" and done["result"]["total"] == 3


def test_xlsx_is_parsed_and_submitted_via_file_uri(pipeline, tmp_path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Ref Des", "Manufacturer Part Number", "Mfr"])
    ws.append(["L1", "744066047", "Wurth"])
    ws.append(["C5", "GRM188R61A106KE69D", "Murata"])
    buf = io.BytesIO()
    wb.save(buf)
    path = tmp_path / "bom.xlsx"
    path.write_bytes(buf.getvalue())

    queued = _structured(_submit(path.as_uri()))
    _validate(queued)
    assert "0 without a part number" in queued["caveat"]
    _wait_done(queued["job"])
    assert [r["original_mpn"] for r in pipeline[0]["rows"]] == ["744066047", "GRM188R61A106KE69D"]


def test_job_is_visible_through_job_status_and_list(pipeline, tmp_path) -> None:
    path = tmp_path / "b.csv"
    path.write_bytes(_CSV)
    job_id = _structured(_submit(str(path)))["job"]

    status = _structured(ms.job_status(job_id))
    _validate(status)
    assert status["job"] == job_id and status["state"] in ("queued", "running", "done")
    listing = _structured(ms.list_jobs())
    assert job_id in [j["job"] for j in listing["jobs"]]
    _wait_done(job_id)


def test_malformed_file_raises_with_the_parsers_reason(pipeline, tmp_path) -> None:
    path = tmp_path / "bad.csv"
    path.write_bytes(b"Colour,Size\nred,large\n")       # no part-number column
    with pytest.raises(BomImportError, match="part-number column"):
        _submit(str(path))
    assert not pipeline, "nothing may be queued for a file that did not parse"
    assert mcp_jobs.registry().list() == []


def test_empty_file_raises(pipeline, tmp_path) -> None:
    path = tmp_path / "empty.xlsx"
    path.write_bytes(b"")
    with pytest.raises(BomImportError, match="empty"):
        _submit(str(path))


def test_missing_file_and_unknown_extension_raise(pipeline, tmp_path) -> None:
    with pytest.raises(ValueError, match="no BOM file at"):
        _submit(str(tmp_path / "nope.csv"))
    (tmp_path / "bom").write_bytes(_CSV)
    with pytest.raises(ValueError, match="extensions"):
        _submit(str(tmp_path / "bom"))
    assert not pipeline
