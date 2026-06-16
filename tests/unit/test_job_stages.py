"""Job stage tracking for the Jobs-view pipeline visualization.

The ProgressReporter gives every job a stage timeline: explicit named stages
(declared via set_stages/start_stage) or auto-stages (each plain update(msg)
message becomes a stage), each with real start→end timing.
"""
from __future__ import annotations

import time

from heaviside.api.jobs import JobRegistry, ProgressReporter, Stage


def _wait(reg, jid, timeout=5.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        j = reg.get(jid)
        if j and j.status in ("done", "error", "cancelled"):
            return j
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_explicit_named_stages_have_status_and_timing():
    reg = JobRegistry()

    def fn(update):
        update.set_stages(["A", "B", "C"])
        update.start_stage("A"); time.sleep(0.02)
        update.start_stage("B"); time.sleep(0.02)
        update.start_stage("C"); time.sleep(0.02)
        return {"ok": True}

    j = _wait(reg, reg.submit("design", fn))
    assert j.status == "done"
    assert [s.name for s in j.stages] == ["A", "B", "C"]
    assert all(s.status == "done" for s in j.stages)
    assert all(s.duration_s() and s.duration_s() > 0 for s in j.stages)


def test_auto_stages_from_plain_update_calls():
    reg = JobRegistry()

    def fn(update):
        update("Parsing"); time.sleep(0.02)
        update("Matching"); time.sleep(0.02)
        return 1

    j = _wait(reg, reg.submit("crossref", fn))
    assert [s.name for s in j.stages] == ["Parsing", "Matching"]
    assert all(s.status == "done" for s in j.stages)


def test_pending_stages_stay_pending_on_early_finish():
    reg = JobRegistry()

    def fn(update):
        update.set_stages(["A", "B", "C"])
        update.start_stage("A")  # never reaches B/C
        return None

    j = _wait(reg, reg.submit("design", fn))
    by = {s.name: s.status for s in j.stages}
    assert by == {"A": "done", "B": "pending", "C": "pending"}


def test_running_stage_marked_error_on_failure():
    reg = JobRegistry()

    def fn(update):
        update.set_stages(["A", "B"])
        update.start_stage("A")
        update.start_stage("B")
        raise RuntimeError("boom")

    j = _wait(reg, reg.submit("design", fn))
    assert j.status == "error"
    by = {s.name: s.status for s in j.stages}
    assert by["A"] == "done"      # finished before the failure
    assert by["B"] == "error"     # was running when it blew up


def test_start_stage_is_idempotent_while_running():
    reg = JobRegistry()
    jid = reg.submit("design", lambda u: None)
    _wait(reg, jid)
    # direct reporter unit check: re-starting the same running stage doesn't
    # reset its start time
    reg._init_stages(jid, ["X"])
    reg._start_stage(jid, "X")
    s0 = next(s for s in reg.get(jid).stages if s.name == "X")
    t0 = s0.started
    time.sleep(0.01)
    reg._start_stage(jid, "X")  # no-op
    assert reg.get(jid).stages[0].started == t0


def test_stage_duration_counts_up_while_running():
    import pytest

    s = Stage(name="X", status="running", started=100.0)
    assert s.duration_s(now=100.5) == pytest.approx(0.5)
    s2 = Stage(name="Y", status="done", started=100.0, ended=100.3)
    assert s2.duration_s() == pytest.approx(0.3)
    assert Stage(name="Z").duration_s() is None  # not started
