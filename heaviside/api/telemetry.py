"""Heaviside telemetry — records every design and cross-reference job.

Design goals
------------
* **No data loss**: inputs are stored at job *submission* time, before the
  pipeline runs.  Even if the job crashes, the reproduction payload is in the
  DB.
* **Separate error table**: every exception gets its own `errors` row that
  duplicates the full input so a single SELECT gives everything needed to
  reproduce the failure without joining back to `events`.
* **Separate from OM telemetry**: schema is `heaviside_telemetry`, never
  `telemetry` (the OpenMagnetics schema).
* **Silent on DB failure**: telemetry must never crash the API; every call is
  wrapped in a broad except.  DB errors go to the log, not the caller.
"""
from __future__ import annotations

import json
import logging
import os
import traceback
from typing import Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Engine (lazy, singleton)
# ─────────────────────────────────────────────────────────────────────────────

_engine_instance: Any = None
_schema_ready = False


def _env_complete() -> bool:
    return all(
        os.getenv(k)
        for k in ("OM_DB_ADDRESS", "OM_DB_PORT", "OM_DB_NAME", "OM_DB_USER", "OM_DB_PASSWORD")
    )


def _get_engine() -> Any:
    global _engine_instance
    if _engine_instance is not None:
        return _engine_instance
    if not _env_complete():
        return None
    import sqlalchemy

    url = (
        "postgresql://{user}:{password}@{address}:{port}/{name}".format(
            user=os.getenv("OM_DB_USER"),
            password=os.getenv("OM_DB_PASSWORD"),
            address=os.getenv("OM_DB_ADDRESS"),
            port=os.getenv("OM_DB_PORT"),
            name=os.getenv("OM_DB_NAME"),
        )
    )
    _engine_instance = sqlalchemy.create_engine(url, pool_pre_ping=True)
    return _engine_instance


def _ensure_schema() -> bool:
    global _schema_ready
    if _schema_ready:
        return True
    eng = _get_engine()
    if eng is None:
        return False
    try:
        import sqlalchemy

        with eng.begin() as conn:
            conn.execute(sqlalchemy.text("CREATE SCHEMA IF NOT EXISTS heaviside_telemetry"))
            conn.execute(sqlalchemy.text("""
                CREATE TABLE IF NOT EXISTS heaviside_telemetry.events (
                    id                  BIGSERIAL PRIMARY KEY,
                    submitted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_at        TIMESTAMPTZ,
                    job_id              TEXT UNIQUE NOT NULL,
                    job_kind            TEXT NOT NULL,
                    input_type          TEXT NOT NULL,
                    input_spec          JSONB,
                    input_bom           JSONB,
                    input_file_name     TEXT,
                    input_file_data     BYTEA,
                    input_url           TEXT,
                    target_manufacturer TEXT,
                    topology            TEXT,
                    verdict             TEXT,
                    result_summary      JSONB,
                    environment         TEXT NOT NULL DEFAULT 'production'
                )
            """))
            conn.execute(sqlalchemy.text("""
                CREATE TABLE IF NOT EXISTS heaviside_telemetry.errors (
                    id                  BIGSERIAL PRIMARY KEY,
                    occurred_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    job_id              TEXT,
                    error_type          TEXT,
                    error_message       TEXT,
                    traceback           TEXT,
                    job_kind            TEXT,
                    input_type          TEXT,
                    input_spec          JSONB,
                    input_bom           JSONB,
                    input_file_name     TEXT,
                    input_file_data     BYTEA,
                    input_url           TEXT,
                    target_manufacturer TEXT,
                    environment         TEXT NOT NULL DEFAULT 'production'
                )
            """))
        _schema_ready = True
        return True
    except Exception:
        logger.exception("heaviside_telemetry: schema setup failed")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def record_submission(
    job_id: str,
    job_kind: str,
    input_type: str,
    *,
    input_spec: dict[str, Any] | None = None,
    input_bom: list[dict[str, Any]] | None = None,
    input_file_name: str | None = None,
    input_file_data: bytes | None = None,
    input_url: str | None = None,
    target_manufacturer: str | None = None,
    environment: str = "production",
) -> None:
    """Store job input at submission time — called before the pipeline runs."""
    if not _ensure_schema():
        return
    eng = _get_engine()
    try:
        import sqlalchemy

        with eng.begin() as conn:
            conn.execute(
                sqlalchemy.text("""
                    INSERT INTO heaviside_telemetry.events
                        (job_id, job_kind, input_type, input_spec, input_bom,
                         input_file_name, input_file_data, input_url,
                         target_manufacturer, environment)
                    VALUES
                        (:job_id, :job_kind, :input_type,
                         CAST(:input_spec AS JSONB), CAST(:input_bom AS JSONB),
                         :input_file_name, :input_file_data, :input_url,
                         :target_manufacturer, :environment)
                    ON CONFLICT (job_id) DO NOTHING
                """),
                {
                    "job_id": job_id,
                    "job_kind": job_kind,
                    "input_type": input_type,
                    "input_spec": json.dumps(input_spec) if input_spec is not None else None,
                    "input_bom": json.dumps(input_bom) if input_bom is not None else None,
                    "input_file_name": input_file_name,
                    "input_file_data": input_file_data,
                    "input_url": input_url,
                    "target_manufacturer": target_manufacturer,
                    "environment": environment,
                },
            )
    except Exception:
        logger.exception("heaviside_telemetry: record_submission failed (job %s)", job_id)


def record_completion(
    job_id: str,
    *,
    topology: str | None = None,
    verdict: str | None = None,
    result_summary: dict[str, Any] | None = None,
) -> None:
    """Back-fill result onto the events row after a job succeeds."""
    if not _ensure_schema():
        return
    eng = _get_engine()
    try:
        import sqlalchemy

        with eng.begin() as conn:
            conn.execute(
                sqlalchemy.text("""
                    UPDATE heaviside_telemetry.events
                    SET completed_at   = NOW(),
                        topology       = :topology,
                        verdict        = :verdict,
                        result_summary = CAST(:result_summary AS JSONB)
                    WHERE job_id = :job_id
                """),
                {
                    "job_id": job_id,
                    "topology": topology,
                    "verdict": verdict,
                    "result_summary": json.dumps(result_summary) if result_summary is not None else None,
                },
            )
    except Exception:
        logger.exception("heaviside_telemetry: record_completion failed (job %s)", job_id)


def record_error(
    job_id: str,
    exc: BaseException,
    *,
    job_kind: str | None = None,
    input_type: str | None = None,
    input_spec: dict[str, Any] | None = None,
    input_bom: list[dict[str, Any]] | None = None,
    input_file_name: str | None = None,
    input_file_data: bytes | None = None,
    input_url: str | None = None,
    target_manufacturer: str | None = None,
    environment: str = "production",
) -> None:
    """Record a job failure with full reproduction inputs in the errors table."""
    if not _ensure_schema():
        return
    eng = _get_engine()
    tb = traceback.format_exc()
    try:
        import sqlalchemy

        with eng.begin() as conn:
            conn.execute(
                sqlalchemy.text("""
                    INSERT INTO heaviside_telemetry.errors
                        (job_id, error_type, error_message, traceback,
                         job_kind, input_type,
                         input_spec, input_bom, input_file_name, input_file_data,
                         input_url, target_manufacturer, environment)
                    VALUES
                        (:job_id, :error_type, :error_message, :traceback,
                         :job_kind, :input_type,
                         CAST(:input_spec AS JSONB), CAST(:input_bom AS JSONB),
                         :input_file_name, :input_file_data,
                         :input_url, :target_manufacturer, :environment)
                """),
                {
                    "job_id": job_id,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:2000],
                    "traceback": tb[:8000],
                    "job_kind": job_kind,
                    "input_type": input_type,
                    "input_spec": json.dumps(input_spec) if input_spec is not None else None,
                    "input_bom": json.dumps(input_bom) if input_bom is not None else None,
                    "input_file_name": input_file_name,
                    "input_file_data": input_file_data,
                    "input_url": input_url,
                    "target_manufacturer": target_manufacturer,
                    "environment": environment,
                },
            )
    except Exception:
        logger.exception("heaviside_telemetry: record_error failed (job %s)", job_id)


# ─────────────────────────────────────────────────────────────────────────────
# Convenience wrapper
# ─────────────────────────────────────────────────────────────────────────────


def wrap_job(
    run_fn: Any,
    *,
    job_kind: str,
    input_type: str,
    input_spec: dict[str, Any] | None = None,
    input_bom: list[dict[str, Any]] | None = None,
    input_file_name: str | None = None,
    input_file_data: bytes | None = None,
    input_url: str | None = None,
    target_manufacturer: str | None = None,
    environment: str = "production",
) -> Any:
    """Return a wrapped job function that records submission + completion/error telemetry.

    The job_id is read from ``update._job_id`` (set by ProgressReporter) at
    run-time — no need to pre-generate or pass it from the submit endpoint.
    """
    _kwargs: dict[str, Any] = dict(
        job_kind=job_kind,
        input_type=input_type,
        input_spec=input_spec,
        input_bom=input_bom,
        input_file_name=input_file_name,
        input_file_data=input_file_data,
        input_url=input_url,
        target_manufacturer=target_manufacturer,
        environment=environment,
    )

    def wrapped(update: Any) -> Any:
        job_id = getattr(update, "_job_id", "unknown")
        # Record input first — before the pipeline does anything, so it's never lost.
        record_submission(job_id, job_kind, input_type, **{
            k: v for k, v in _kwargs.items()
            if k not in ("job_kind", "input_type")
        })
        try:
            result = run_fn(update)
        except Exception as exc:
            record_error(job_id, exc, **_kwargs)
            raise
        if isinstance(result, dict):
            record_completion(
                job_id,
                topology=result.get("topology"),
                verdict=result.get("verdict"),
                result_summary={
                    k: result[k]
                    for k in ("coverage_pct", "coverage_substituted", "coverage_total")
                    if k in result
                } or None,
            )
        return result

    return wrapped
