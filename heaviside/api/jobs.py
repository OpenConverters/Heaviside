"""In-memory async job registry for long-running pipeline calls.

The design / CRE / cross-reference pipelines take minutes (LLM + ngspice +
MKF), far longer than an HTTP request should block. The web UI submits a job
(returns a job_id immediately), then polls for status/result.

Single-process, in-memory: fine for an internal/single-user server. For
multi-worker deployment, swap the dict for Redis/RQ — the interface (submit,
get) stays the same.

LLM-heavy work is SERIALIZED through one worker thread on purpose: running
several CRE/design jobs at once trips the Moonshot 429 rate limit
(see kimi-k2-quirks memory). Jobs queue and run one at a time.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from queue import Queue
from typing import Any


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
            try:

                def update(msg, job_id=job_id):
                    return self._set(job_id, progress=str(msg))

                # fn may be zero-arg or take the progress updater.
                result = fn(update) if len(inspect.signature(fn).parameters) >= 1 else fn()
                self._set(job_id, status="done", result=result)
            except Exception as exc:
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
