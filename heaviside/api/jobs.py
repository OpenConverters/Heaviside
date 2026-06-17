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

import contextlib
import json
import logging
import os
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from typing import Any

logger = logging.getLogger(__name__)

# Where finished jobs are persisted so results survive a server restart.
_DEFAULT_JOBS_DIR = Path(
    os.environ.get("HEAVISIDE_JOBS_DIR", str(Path.home() / ".heaviside" / "jobs"))
)
# Terminal states worth persisting (in-flight jobs aren't — they die with the
# process and can't be resumed; only completed results need to outlive it).
_TERMINAL = {"done", "error", "cancelled"}


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
    created_wall: float | None = None  # epoch seconds — survives restart for ordering
    cancel_requested: bool = False
    stages: list[Stage] = field(default_factory=list)


class JobCancelled(Exception):
    """Raised inside a job's ``fn`` when the user requested cancellation.

    The worker checks at every stage boundary (via :class:`ProgressReporter`),
    so a long-running pipeline stops at the next stage transition rather than
    running to completion. Caught by the worker → job status ``cancelled``."""


class ProgressReporter:
    """Passed to a job's ``fn`` as the ``update`` argument.

    Backward-compatible: calling it with a string sets the human-readable
    progress AND auto-advances a stage timeline (each new message becomes a
    stage), so existing jobs get a pipeline view for free. A job that wants
    named stages calls :meth:`set_stages` once then :meth:`start_stage`.

    Every progress/stage call also checks for cancellation and raises
    :class:`JobCancelled` if the user pressed Cancel — giving the pipeline
    frequent, natural abort points without threading a flag through each stage."""

    def __init__(self, registry: JobRegistry, job_id: str) -> None:
        self._registry = registry
        self._job_id = job_id
        self._explicit = False

    def check_cancelled(self) -> None:
        """Raise :class:`JobCancelled` if cancellation was requested."""
        job = self._registry.get(self._job_id)
        if job is not None and job.cancel_requested:
            raise JobCancelled(f"job {self._job_id} cancelled by user")

    def __call__(self, msg: Any) -> None:
        self.check_cancelled()
        self._registry._set(self._job_id, progress=str(msg))
        if not self._explicit:
            self._registry._start_stage(self._job_id, str(msg), create=True)

    def set_stages(self, names: list[str]) -> None:
        self._explicit = True
        self._registry._init_stages(self._job_id, names)

    def start_stage(self, name: str) -> None:
        self.check_cancelled()
        self._explicit = True
        self._registry._start_stage(self._job_id, name, create=True)


class JobRegistry:
    """Thread-safe job store with a single serializing worker."""

    def __init__(self, persist_dir: Path | str | None = None) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._queue: Queue[tuple[str, Callable[[], Any]]] = Queue()
        self._persist_dir = Path(persist_dir) if persist_dir is not None else _DEFAULT_JOBS_DIR
        self._load_persisted()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def submit(self, kind: str, fn: Callable[..., Any]) -> str:
        """Queue a job. `fn` may take zero args, or one arg: an ``update(msg)``
        callable it can call to publish a human-readable progress string."""
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = Job(
                id=job_id, kind=kind,
                created_monotonic=time.monotonic(), created_wall=time.time(),
            )
        self._queue.put((job_id, fn))
        return job_id

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_all(self) -> list[Job]:
        """All jobs, newest first."""
        with self._lock:
            jobs = list(self._jobs.values())
        # created_wall (epoch) orders correctly across a restart; monotonic
        # resets each process so restored jobs would otherwise sort wrong.
        jobs.sort(key=lambda j: j.created_wall or j.created_monotonic or 0.0, reverse=True)
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
        self._delete_persisted(job_id)
        return True

    def cancel(self, job_id: str) -> bool:
        """Request cancellation. A still-queued job is cancelled immediately;
        a running job is flagged (best-effort — the worker is in-thread)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status in ("done", "error", "cancelled"):
                return False
            job.cancel_requested = True
            cancelled_now = job.status == "queued"
            if cancelled_now:
                job.status = "cancelled"
        if cancelled_now:
            self._persist_job(job_id)
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

    # ------------------------------------------------------------------
    # Persistence — finished jobs are written to disk so results survive a
    # server restart (the in-memory dict alone loses everything on exit).
    # ------------------------------------------------------------------

    def _job_path(self, job_id: str) -> Path:
        return self._persist_dir / f"{job_id}.json"

    def _persist_job(self, job_id: str) -> None:
        """Write a terminal job to disk as JSON. Best-effort: a non-serializable
        result or IO error is logged, not raised — the job already succeeded in
        memory; losing its on-disk copy must not break the request."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in _TERMINAL:
                return
            snapshot = {
                "id": job.id, "kind": job.kind, "status": job.status,
                "result": job.result, "error": job.error, "progress": job.progress,
                "created_wall": job.created_wall,
                "stages": [
                    {"name": s.name, "status": s.status, "duration_s": s.duration_s()}
                    for s in job.stages
                ],
            }
        try:
            self._persist_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._job_path(job_id).with_suffix(".json.tmp")
            tmp.write_text(json.dumps(snapshot))
            tmp.replace(self._job_path(job_id))  # atomic
        except (TypeError, ValueError, OSError) as exc:
            logger.warning("could not persist job %s: %s", job_id, exc)

    def _delete_persisted(self, job_id: str) -> None:
        with contextlib.suppress(OSError):
            self._job_path(job_id).unlink(missing_ok=True)

    def _load_persisted(self) -> None:
        """Restore finished jobs from disk on startup (status/result/stages).
        Restored stages carry only their final duration (no live monotonic
        timing); restored jobs are terminal, so that's all the UI needs."""
        if not self._persist_dir.is_dir():
            return
        for path in self._persist_dir.glob("*.json"):
            try:
                d = json.loads(path.read_text())
            except (OSError, ValueError) as exc:
                logger.warning("skipping unreadable persisted job %s: %s", path.name, exc)
                continue
            stages = [
                Stage(name=s.get("name", "?"), status=s.get("status", "done"),
                      started=0.0,
                      ended=s.get("duration_s") if s.get("duration_s") is not None else None)
                for s in (d.get("stages") or [])
            ]
            job = Job(
                id=d.get("id", path.stem), kind=d.get("kind", "?"),
                status=d.get("status", "done"), result=d.get("result"),
                error=d.get("error"), progress=d.get("progress", ""),
                created_wall=d.get("created_wall"), stages=stages,
            )
            self._jobs[job.id] = job
        if self._jobs:
            logger.info("restored %d persisted jobs from %s", len(self._jobs), self._persist_dir)

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
            except JobCancelled:
                # User pressed Cancel mid-run — a clean stop, not a failure.
                logger.info("job %s cancelled mid-run", job_id)
                self._finalize_stages(job_id, errored=True)  # running stage → not "done"
                self._set(job_id, status="cancelled",
                          progress="cancelled by user", result=None)
            except Exception as exc:
                # Log the failure (the worker used to swallow it silently — the
                # traceback only lived in the job's result), then persist it.
                logger.exception("job %s (%s) failed", job_id, job.kind if job else "?")
                self._finalize_stages(job_id, errored=True)
                self._set(
                    job_id,
                    status="error",
                    error=f"{type(exc).__name__}: {exc}",
                    result={"traceback": traceback.format_exc()[-2000:]},
                )
            finally:
                self._persist_job(job_id)  # done/error → survive a restart
                self._queue.task_done()


# Module-level singleton used by the API.
registry = JobRegistry()
