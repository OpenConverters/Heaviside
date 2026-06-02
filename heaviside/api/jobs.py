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
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from queue import Queue
from typing import Any


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"          # queued | running | done | error
    result: Any = None
    error: str | None = None
    progress: str = ""
    created_monotonic: float | None = None


class JobRegistry:
    """Thread-safe job store with a single serializing worker."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._queue: "Queue[tuple[str, Callable[[], Any]]]" = Queue()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def submit(self, kind: str, fn: Callable[[], Any]) -> str:
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = Job(id=job_id, kind=kind)
        self._queue.put((job_id, fn))
        return job_id

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _set(self, job_id: str, **kw: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for k, v in kw.items():
                setattr(job, k, v)

    def _run(self) -> None:
        while True:
            job_id, fn = self._queue.get()
            self._set(job_id, status="running")
            try:
                result = fn()
                self._set(job_id, status="done", result=result)
            except Exception as exc:  # noqa: BLE001 — surface to the client
                self._set(
                    job_id, status="error",
                    error=f"{type(exc).__name__}: {exc}",
                    result={"traceback": traceback.format_exc()[-2000:]},
                )
            finally:
                self._queue.task_done()


# Module-level singleton used by the API.
registry = JobRegistry()
