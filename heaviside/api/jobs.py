"""In-memory async job registry for long-running pipeline calls.

The design / RE / cross-reference pipelines take minutes (LLM + ngspice +
MKF), far longer than an HTTP request should block. The web UI submits a job
(returns a job_id immediately), then polls for status/result.

Single-process, in-memory: fine for an internal/single-user server. For
multi-worker deployment, swap the dict for Redis/RQ — the interface (submit,
get) stays the same.

LLM-heavy work is SERIALIZED through one worker thread on purpose: running
several RE/design jobs at once trips the Moonshot 429 rate limit
(see kimi-k2-quirks memory). Jobs queue and run one at a time.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from queue import Queue
from typing import Any


@dataclass
class Stage:
    """One pipeline stage in a job's lifecycle, with real timing.

    ``status`` is pending → running → done (or error). ``started`` / ``ended``
    are ``time.monotonic()`` stamps; ``duration_s`` is computed from them (and
    counts up live while the stage is running)."""

    name: str
    status: str = "pending"  # pending | running | done | error
    started: float | None = None
    ended: float | None = None

    def duration_s(self, *, now: float | None = None) -> float | None:
        if self.started is None:
            return None
        end = self.ended if self.ended is not None else (now if self.status == "running" else None)
        return None if end is None else max(0.0, end - self.started)


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"  # queued | running | done | error | cancelled
    result: Any = None
    error: str | None = None
    progress: str = ""
    created_monotonic: float | None = None
    cancel_requested: bool = False
    stages: list[Stage] = field(default_factory=list)


class ProgressReporter:
    """Passed to a job's ``fn`` as the ``update`` argument.

    Backward-compatible: calling it with a string sets the human-readable
    progress AND auto-advances a stage timeline (each new message becomes a
    stage), so existing jobs get a pipeline view for free. A job that wants
    named stages calls :meth:`set_stages` once then :meth:`start_stage`."""

    def __init__(self, registry: JobRegistry, job_id: str) -> None:
        self._registry = registry
        self._job_id = job_id
        self._explicit = False

    def __call__(self, msg: Any) -> None:
        self._registry._set(self._job_id, progress=str(msg))
        if not self._explicit:
            self._registry._start_stage(self._job_id, str(msg), create=True)

    def set_stages(self, names: list[str]) -> None:
        self._explicit = True
        self._registry._init_stages(self._job_id, names)

    def start_stage(self, name: str) -> None:
        self._explicit = True
        self._registry._start_stage(self._job_id, name, create=True)


class JobRegistry:
    """Thread-safe job store with a single serializing worker."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._queue: Queue[tuple[str, Callable[[], Any]]] = Queue()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def submit(self, kind: str, fn: Callable[..., Any]) -> str:
        """Queue a job. `fn` may take zero args, or one arg: an ``update(msg)``
        callable it can call to publish a human-readable progress string."""
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = Job(id=job_id, kind=kind, created_monotonic=time.monotonic())
        self._queue.put((job_id, fn))
        return job_id

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_all(self) -> list[Job]:
        """All jobs, newest first."""
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda j: j.created_monotonic or 0.0, reverse=True)
        return jobs

    def delete(self, job_id: str) -> bool:
        """Drop a finished/errored/cancelled job's record. Returns True if removed."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job.status in ("queued", "running"):
                return False  # don't delete in-flight work
            del self._jobs[job_id]
            return True

    def cancel(self, job_id: str) -> bool:
        """Request cancellation. A still-queued job is cancelled immediately;
        a running job is flagged (best-effort — the worker is in-thread)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status in ("done", "error", "cancelled"):
                return False
            job.cancel_requested = True
            if job.status == "queued":
                job.status = "cancelled"
            return True

    def _set(self, job_id: str, **kw: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for k, v in kw.items():
                setattr(job, k, v)

    def _init_stages(self, job_id: str, names: list[str]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.stages = [Stage(name=n) for n in names]

    def _start_stage(self, job_id: str, name: str, *, create: bool = False) -> None:
        """Mark ``name`` as the running stage: finish whatever was running, then
        start ``name`` (creating it if ``create`` and it isn't declared)."""
        now = time.monotonic()
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            target = next((s for s in job.stages if s.name == name), None)
            if target is not None and target.status == "running":
                return  # already the active stage — no-op
            # finish any currently-running stage(s)
            for s in job.stages:
                if s.status == "running":
                    s.status = "done"
                    s.ended = now
            if target is None:
                if not create:
                    return
                target = Stage(name=name)
                job.stages.append(target)
            target.status = "running"
            target.started = now
            target.ended = None

    def _finalize_stages(self, job_id: str, *, errored: bool) -> None:
        """At job end: the running stage becomes error (if the job failed) or
        done; pending stages that never ran stay pending."""
        now = time.monotonic()
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for s in job.stages:
                if s.status == "running":
                    s.status = "error" if errored else "done"
                    s.ended = now

    def _run(self) -> None:
        import inspect

        while True:
            job_id, fn = self._queue.get()
            job = self.get(job_id)
            if job is not None and job.cancel_requested:
                # Cancelled while still queued — skip without running.
                self._set(job_id, status="cancelled")
                self._queue.task_done()
                continue
            self._set(job_id, status="running")
            update = ProgressReporter(self, job_id)
            try:
                # fn may be zero-arg or take the progress reporter.
                result = fn(update) if len(inspect.signature(fn).parameters) >= 1 else fn()
                self._finalize_stages(job_id, errored=False)
                self._set(job_id, status="done", result=result)
            except Exception as exc:
                self._finalize_stages(job_id, errored=True)
                self._set(
                    job_id,
                    status="error",
                    error=f"{type(exc).__name__}: {exc}",
                    result={"traceback": traceback.format_exc()[-2000:]},
                )
            finally:
                self._queue.task_done()


# Module-level singleton used by the API.
registry = JobRegistry()
