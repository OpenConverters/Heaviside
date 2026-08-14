"""A small job registry for MCP tools that do not finish inside a call.

WHY (ABT #758). A BOM cross-reference takes minutes: measured on this machine,
234 s for ONE line and about fifteen for two. Every consumer in front of it has
a shorter timeout — Moebius's bridge kills its CLI at 600 s, MCP clients give up,
chat hosts gave up long before. A blocking tool that outlives its callers'
timeouts is not a slow tool, it is an unreachable one: the user sees a turn hang
and fail with no way to know whether the work happened, and it may well have
completed after they stopped listening. That was observed, not imagined.

So: submit, poll, fetch — the same shape OMFEM settled on for FEA, deliberately,
because two long-running pipelines with two different job envelopes would make
every consumer learn both.

TWO THINGS THIS DOES NOT PRETEND:

* The queue lives in THIS PROCESS. A restart fails running jobs and loses queued
  ones; only finished results survive, because those are written to disk. So
  "failed" can mean "the server restarted", which a consumer must surface rather
  than silently resubmit — fifteen minutes of work is not a free retry. The
  error text says so explicitly rather than leaving it to be inferred.

* A RUNNING job cannot be cancelled. The pipeline is synchronous Python with no
  cancellation points, and a thread cannot be safely killed mid-way through
  writing lessons to a shared store. `cancel` refuses instead of lying about it;
  a queued job, which has not started, is cancelled properly.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

STATES = ("queued", "running", "done", "failed", "cancelled")

#: Stamped into every persisted job. Bump it whenever the shape of a stored
#: `result` changes, so a file written by an older build is reported as stale
#: rather than replayed into a consumer that will reject it.
FORMAT = "2026-08-14/contract-741"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    label: str
    state: str = "queued"
    phase: str = ""
    submitted_at: str = field(default_factory=_now)
    finished_at: str | None = None
    error: str | None = None
    result: dict | None = None

    def summary(self) -> dict:
        """The listing shape — everything but the payload."""
        out = {"job": self.id, "state": self.state}
        for key, value in (("label", self.label), ("phase", self.phase),
                           ("submittedAt", self.submitted_at),
                           ("finishedAt", self.finished_at)):
            if value:
                out[key] = value
        return out

    def envelope(self, *, with_result: bool = False) -> dict:
        """The `mode: "job"` payload (Moebius contract)."""
        out: dict[str, Any] = {
            "mode": "job", "job": self.id, "state": self.state,
            "submittedAt": self.submitted_at,
        }
        if self.label:
            out["label"] = self.label
        if self.phase:
            out["phase"] = self.phase
        if self.finished_at:
            out["finishedAt"] = self.finished_at
        if self.error:
            out["error"] = self.error
        if with_result and self.result is not None:
            out["result"] = self.result
        return out


class JobRegistry:
    """Runs callables on a small pool and remembers what happened."""

    def __init__(self, *, root: Path | None = None, concurrency: int = 2):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=concurrency,
                                        thread_name_prefix="heaviside-job")
        self._root = root or Path(
            os.environ.get("HEAVISIDE_JOB_DIR") or (Path.home() / ".heaviside" / "jobs"))
        self._root.mkdir(parents=True, exist_ok=True)

    # --- lookup -------------------------------------------------------------

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is not None:
            return job
        # Finished jobs outlive the process because they are on disk. Without
        # this, a restart turns "here is your result" into "unknown job" for
        # work that completed successfully.
        path = self._root / f"{job_id}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            # A persisted result outlives the code that produced it. When the
            # result shape changes, an old file replays a payload that no
            # longer conforms — and the failure surfaces at the CONSUMER as a
            # contract violation, which reads exactly like a live bug in the
            # current build. Say what it actually is instead.
            #
            # (Observed: a job persisted before the ABT #741 reshaping came back
            # with {target_manufacturer, components} and Moebius rejected it.)
            if data.get("format") != FORMAT:
                raise KeyError(
                    f"job {job_id} was recorded by an older build "
                    f"(format {data.get('format', 'unversioned')!r}, this build "
                    f"writes {FORMAT!r}); its stored result no longer matches the "
                    f"current shape. Resubmit the work — the file is kept, not "
                    f"deleted, so nothing is lost if you want to look at it."
                )
            job = Job(id=data["id"], label=data.get("label", ""),
                      state=data.get("state", "done"), phase=data.get("phase", ""),
                      submitted_at=data.get("submitted_at", ""),
                      finished_at=data.get("finished_at"),
                      error=data.get("error"), result=data.get("result"))
            with self._lock:
                self._jobs.setdefault(job_id, job)
            return job
        raise KeyError(
            f"no job {job_id!r}. Ids come from a submit_* tool; list_jobs shows "
            f"what this server knows about. A job submitted before the server "
            f"last restarted is gone unless it had finished."
        )

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.submitted_at, reverse=True)

    # --- lifecycle ----------------------------------------------------------

    def submit(self, label: str, work: Callable[[Callable[[str], None]], dict]) -> Job:
        """Queue `work`, which is handed a `progress(phase)` callback."""
        job = Job(id=uuid.uuid4().hex[:12], label=label)
        with self._lock:
            self._jobs[job.id] = job
        self._pool.submit(self._run, job, work)
        return job

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        with self._lock:
            if job.state == "queued":
                job.state = "cancelled"
                job.finished_at = _now()
            elif job.state == "running":
                # Refused rather than faked. The pipeline is synchronous with no
                # cancellation points, and killing the thread mid-run could
                # leave the shared lesson store half-written.
                raise RuntimeError(
                    f"job {job_id} is already running and cannot be cancelled — "
                    f"the pipeline has no safe interruption point. It will "
                    f"finish or fail on its own."
                )
        return job

    def _run(self, job: Job, work: Callable[[Callable[[str], None]], dict]) -> None:
        with self._lock:
            if job.state == "cancelled":
                return
            job.state = "running"

        def progress(phase: str) -> None:
            job.phase = phase

        result: dict | None = None
        error: str | None = None
        try:
            result = work(progress)
        except Exception as exc:                       # noqa: BLE001 - recorded, not swallowed
            error = f"{type(exc).__name__}: {exc}"
            logger.exception("job %s failed", job.id)

        state = "failed" if error else "done"
        finished = _now()

        # PERSIST BEFORE PUBLISHING THE TERMINAL STATE. `state` is what a caller
        # polls, and the moment it reads "done" it will ask for the result — so
        # the result has to be durable first. The other order has a window in
        # which a job reports done and its file does not exist yet, and a caller
        # that restarts the server inside that window loses finished work while
        # having been told it succeeded.
        self._persist(job, state=state, finished_at=finished, error=error, result=result)

        job.result, job.error, job.finished_at, job.phase = result, error, finished, ""
        job.state = state

    def _persist(self, job: Job, *, state: str, finished_at: str,
                 error: str | None, result: dict | None) -> None:
        """Finished jobs go to disk so a restart does not lose completed work.

        Written to a temporary file and renamed. os.replace is atomic within a
        filesystem, so a reader sees either the previous file or the complete
        new one — never the half-written JSON a plain write exposes if the
        process dies mid-flush.
        """
        path = self._root / f"{job.id}.json"
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps({"format": FORMAT,
                            "id": job.id, "label": job.label, "state": state,
                            "submitted_at": job.submitted_at,
                            "finished_at": finished_at,
                            "error": error, "result": result}),
                encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            # The in-memory job is still correct; only restart-survival is lost.
            # Logged loudly rather than raised, because failing the job over a
            # disk problem would discard work that actually succeeded.
            logger.warning("could not persist job %s: %s", job.id, exc)
            tmp.unlink(missing_ok=True)


_registry: JobRegistry | None = None


def registry() -> JobRegistry:
    global _registry
    if _registry is None:
        _registry = JobRegistry(
            concurrency=int(os.environ.get("HEAVISIDE_MCP_CONCURRENCY", "2")))
    return _registry
