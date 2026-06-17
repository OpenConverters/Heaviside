"""Jobs must survive a server restart — the in-memory registry alone loses all
results on exit. Finished jobs are persisted to disk and reloaded on startup."""

from __future__ import annotations

import time

from heaviside.api.jobs import JobRegistry


def _wait(reg, jid, timeout=5.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        j = reg.get(jid)
        if j and j.status in ("done", "error", "cancelled"):
            return j
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_done_job_survives_restart(tmp_path):
    r1 = JobRegistry(persist_dir=tmp_path)
    jid = r1.submit("crossref", lambda u: {"components": [1, 2], "coverage_pct": 0.9})
    _wait(r1, jid)
    assert (tmp_path / f"{jid}.json").exists()

    r2 = JobRegistry(persist_dir=tmp_path)  # simulate restart
    restored = r2.get(jid)
    assert restored is not None
    assert restored.status == "done"
    assert restored.result == {"components": [1, 2], "coverage_pct": 0.9}


def test_error_job_survives_with_traceback(tmp_path):
    def boom(u):
        raise RuntimeError("kaboom")

    r1 = JobRegistry(persist_dir=tmp_path)
    jid = r1.submit("design", boom)
    _wait(r1, jid)

    r2 = JobRegistry(persist_dir=tmp_path)
    restored = r2.get(jid)
    assert restored.status == "error"
    assert "kaboom" in (restored.error or "")


def test_restored_stages_keep_their_durations(tmp_path):
    def fn(update):
        update.set_stages(["A", "B"])
        update.start_stage("A")
        time.sleep(0.02)
        update.start_stage("B")
        time.sleep(0.02)
        return None

    r1 = JobRegistry(persist_dir=tmp_path)
    jid = r1.submit("design", fn)
    _wait(r1, jid)

    r2 = JobRegistry(persist_dir=tmp_path)
    stages = r2.get(jid).stages
    assert [s.name for s in stages] == ["A", "B"]
    assert all(s.status == "done" for s in stages)
    assert all(s.duration_s() and s.duration_s() > 0 for s in stages)


def test_delete_removes_persisted_file(tmp_path):
    r = JobRegistry(persist_dir=tmp_path)
    jid = r.submit("crossref", lambda u: {"ok": True})
    _wait(r, jid)
    assert (tmp_path / f"{jid}.json").exists()
    assert r.delete(jid) is True
    assert not (tmp_path / f"{jid}.json").exists()


def test_non_serializable_result_does_not_crash(tmp_path):
    # A result that can't be JSON-encoded must not break the job — it succeeds
    # in memory; only its on-disk copy is skipped (logged).
    r1 = JobRegistry(persist_dir=tmp_path)
    jid = r1.submit("design", lambda u: {"obj": object()})
    j = _wait(r1, jid)
    assert j.status == "done"  # job itself fine; persistence just warned
