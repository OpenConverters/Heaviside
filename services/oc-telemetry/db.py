"""Postgres layer for the shared OpenConverters interaction-telemetry receiver.

Mirrors the OpenMagnetics/Heaviside pattern exactly:

* Same managed DB (the ``OM_DB_*`` env vars) — NOT a new database/server.
* A DEDICATED schema ``openconverters_telemetry`` (never ``public`` /
  ``telemetry`` / ``heaviside_telemetry`` / ``umami``), so OpenConverters
  interaction data is isolated from every other consumer of the DB.
* Schema + tables are auto-created on first use, so there is no manual DDL step
  to forget on a fresh box (same as ``heaviside/api/telemetry.py``).
* **Silent on DB failure**: telemetry must never take the receiver down; every
  path is guarded and logs instead of raising to the caller.

Shape: a thin ``sessions`` upsert (one row per browser tab) + an ``events``
append stream carrying ``event_type`` ("what happened"), ``target`` ("what the
user touched" — a part name, topology, filter column, …) and a lightweight
``props`` JSONB for any extra context. No heavy payloads (no MAS, no BOM) — the
static sites keep the "nothing leaves your machine" promise for real designs.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger("oc_telemetry.db")

SCHEMA = "openconverters_telemetry"

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
        logger.warning("oc_telemetry: OM_DB_* env incomplete — telemetry disabled")
        return None
    import sqlalchemy

    url = "postgresql://{user}:{password}@{address}:{port}/{name}".format(
        user=os.getenv("OM_DB_USER"),
        password=os.getenv("OM_DB_PASSWORD"),
        address=os.getenv("OM_DB_ADDRESS"),
        port=os.getenv("OM_DB_PORT"),
        name=os.getenv("OM_DB_NAME"),
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
            conn.execute(sqlalchemy.text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
            conn.execute(sqlalchemy.text(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.sessions (
                    session_id  TEXT PRIMARY KEY,
                    site        TEXT NOT NULL,
                    environment TEXT NOT NULL DEFAULT 'production',
                    app_version TEXT,
                    user_agent  TEXT,
                    first_seen  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))
            conn.execute(sqlalchemy.text(f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.events (
                    id          BIGSERIAL PRIMARY KEY,
                    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    site        TEXT NOT NULL,
                    session_id  TEXT NOT NULL,
                    event_type  TEXT NOT NULL,
                    target      TEXT,
                    props       JSONB,
                    environment TEXT NOT NULL DEFAULT 'production',
                    app_version TEXT
                )
            """))
            conn.execute(sqlalchemy.text(
                f"CREATE INDEX IF NOT EXISTS events_site_type_time_idx "
                f"ON {SCHEMA}.events (site, event_type, occurred_at)"))
            conn.execute(sqlalchemy.text(
                f"CREATE INDEX IF NOT EXISTS events_session_idx "
                f"ON {SCHEMA}.events (session_id)"))
        _schema_ready = True
        return True
    except Exception:
        logger.exception("oc_telemetry: schema setup failed")
        return False


def record_event(
    *,
    site: str,
    session_id: str,
    event_type: str,
    target: str | None = None,
    props: dict[str, Any] | None = None,
    environment: str = "production",
    app_version: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Upsert the session, then append one event. Never raises to the caller."""
    if not _ensure_schema():
        return
    eng = _get_engine()
    try:
        import sqlalchemy

        with eng.begin() as conn:
            conn.execute(
                sqlalchemy.text(f"""
                    INSERT INTO {SCHEMA}.sessions
                        (session_id, site, environment, app_version, user_agent)
                    VALUES (:sid, :site, :env, :ver, :ua)
                    ON CONFLICT (session_id) DO UPDATE
                        SET last_seen = NOW(),
                            app_version = COALESCE(EXCLUDED.app_version, {SCHEMA}.sessions.app_version)
                """),
                {"sid": session_id, "site": site, "env": environment,
                 "ver": app_version, "ua": user_agent},
            )
            conn.execute(
                sqlalchemy.text(f"""
                    INSERT INTO {SCHEMA}.events
                        (site, session_id, event_type, target, props, environment, app_version)
                    VALUES
                        (:site, :sid, :etype, :target, CAST(:props AS JSONB), :env, :ver)
                """),
                {"site": site, "sid": session_id, "etype": event_type, "target": target,
                 "props": json.dumps(props) if props is not None else None,
                 "env": environment, "ver": app_version},
            )
    except Exception:
        logger.exception("oc_telemetry: record_event failed (site=%s type=%s)", site, event_type)
