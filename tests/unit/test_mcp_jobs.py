"""The job registry behind submit_crossref (ABT #758).

No LLM here: the point of these is the LIFECYCLE — what a caller can learn about
work that has not finished, and what the registry refuses to pretend.
"""

from __future__ import annotations

import threading
import time

import pytest

from heaviside.mcp_jobs import JobRegistry


def test_submit_returns_immediately_and_the_result_arrives_later(tmp_path) -> None:
    """The whole reason this exists: the caller gets a handle, not a wait."""
    reg = JobRegistry(root=tmp_path, concurrency=2)
    release = threading.Event()

    def work(progress):
        progress("stage 1")
        release.wait(timeout=5)
        progress("stage 2")
        return {"mode": "bom", "total": 1, "sourced": 1,
                "lines": [{"ref": "C1", "status": "exact"}]}

    t0 = time.time()
    job = reg.submit("1 line", work)
    assert time.time() - t0 < 1.0, "submit must not block on the work"
    assert job.state in ("queued", "running")

    release.set()
    deadline = time.time() + 10
    while reg.get(job.id).state not in ("done", "failed") and time.time() < deadline:
        time.sleep(0.05)

    done = reg.get(job.id)
    assert done.state == "done", done.error
    assert done.result["mode"] == "bom"
    env = done.envelope(with_result=True)
    assert env["mode"] == "job" and env["state"] == "done" and env["result"]["mode"] == "bom"


def test_phase_reports_the_stage_by_name(tmp_path) -> None:
    """A stage name tells an engineer whether to wait; a percentage does not."""
    reg = JobRegistry(root=tmp_path)
    seen = threading.Event()

    def work(progress):
        progress("CR stage 6: Otto")
        seen.set()
        time.sleep(0.3)
        return {"mode": "catalogue", "families": []}

    job = reg.submit("x", work)
    assert seen.wait(timeout=5)
    assert reg.get(job.id).phase == "CR stage 6: Otto"


def test_a_failure_is_recorded_not_swallowed(tmp_path) -> None:
    reg = JobRegistry(root=tmp_path)

    def work(progress):
        raise RuntimeError("no MOONSHOT_API_KEY or OPENAI_API_KEY in environment")

    job = reg.submit("x", work)
    deadline = time.time() + 10
    while reg.get(job.id).state not in ("done", "failed") and time.time() < deadline:
        time.sleep(0.05)

    failed = reg.get(job.id)
    assert failed.state == "failed"
    # The cause must reach the caller. It was a server-side WARNING before, which
    # no MCP client ever reads (ABT #748).
    assert "MOONSHOT_API_KEY" in failed.error
    assert failed.envelope()["error"] == failed.error


def test_a_running_job_refuses_to_be_cancelled(tmp_path) -> None:
    """The pipeline is synchronous with no cancellation points, and killing the
    thread could leave the shared lesson store half-written. Refusing is honest;
    reporting 'cancelled' while it keeps running is not."""
    reg = JobRegistry(root=tmp_path)
    started, release = threading.Event(), threading.Event()

    def work(progress):
        started.set()
        release.wait(timeout=5)
        return {"mode": "catalogue", "families": []}

    job = reg.submit("x", work)
    assert started.wait(timeout=5)
    with pytest.raises(RuntimeError, match="cannot be cancelled"):
        reg.cancel(job.id)
    release.set()


def test_a_finished_job_survives_a_restart(tmp_path) -> None:
    """Finished results are on disk, so a restart does not turn 'here is your
    result' into 'unknown job' for work that completed."""
    reg = JobRegistry(root=tmp_path)
    job = reg.submit("x", lambda progress: {"mode": "catalogue", "families": []})
    deadline = time.time() + 10
    while reg.get(job.id).state != "done" and time.time() < deadline:
        time.sleep(0.05)

    fresh = JobRegistry(root=tmp_path)          # a new process would look like this
    recovered = fresh.get(job.id)
    assert recovered.state == "done" and recovered.result["mode"] == "catalogue"


def test_an_unknown_job_says_what_to_do(tmp_path) -> None:
    reg = JobRegistry(root=tmp_path)
    with pytest.raises(KeyError) as exc:
        reg.get("nosuchjob")
    # A queued job really is gone after a restart; the message says so rather
    # than leaving a caller to wonder whether to keep polling.
    assert "restarted" in str(exc.value)
