"""oc-telemetry — the shared interaction-telemetry receiver for the three
OpenConverters sites (Kelvin, Kirchhoff, Heaviside).

Why this exists: Kelvin and Kirchhoff are static WASM SPAs with no backend, so
they have nowhere to POST "what the user touched" events. This tiny service is
that endpoint. It is fronted same-origin by each site's nginx at ``/telemetry``
(so no CORS, no third-party host), and writes to the shared OpenMagnetics
Postgres under the dedicated ``openconverters_telemetry`` schema — the same
"same DB, another schema" pattern OpenMagnetics uses for ``telemetry`` and
Heaviside uses for ``heaviside_telemetry``.

Design rules (inherited from the OM/Heaviside telemetry):
* Fire-and-forget: writes happen in a background task; the client gets an
  immediate 204 and never waits on the DB.
* Never crash: the DB layer swallows its own errors (see db.py). Bad payloads
  get a 400, everything else best-effort.
* Lightweight only: event_type + target + a small props object. No design
  payloads ever cross this boundary.

Run: ``uvicorn app:app --host 127.0.0.1 --port 8787`` (see oc-telemetry.service).
"""
from __future__ import annotations

import logging

from fastapi import BackgroundTasks, FastAPI, Request, Response
from pydantic import BaseModel, Field

import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("oc_telemetry")

app = FastAPI(title="oc-telemetry", docs_url=None, redoc_url=None, openapi_url=None)

# The only sites allowed to write. Guards against a stray/forged `site` value
# polluting the shared table with junk partitions.
ALLOWED_SITES = {"kelvin", "kirchhoff", "heaviside"}

# Cap sizes so a hostile client cannot bloat the DB. Events are meant to be tiny.
MAX_STR = 256
MAX_PROPS_BYTES = 4096


class EventIn(BaseModel):
    site: str
    session_id: str = Field(..., max_length=MAX_STR)
    event_type: str = Field(..., max_length=MAX_STR)
    target: str | None = Field(default=None, max_length=MAX_STR)
    props: dict | None = None
    environment: str | None = None
    app_version: str | None = Field(default=None, max_length=MAX_STR)


def _clean_env(env: str | None) -> str:
    # Trust a valid client-declared environment; otherwise default to production
    # so untagged rows never masquerade as development (which would hide them
    # from production stats). Mirrors the OM backend's rule.
    return env if env in ("development", "production") else "production"


@app.get("/telemetry/health", include_in_schema=False)
def health() -> dict:
    return {"ok": True}


@app.post("/telemetry", include_in_schema=False, status_code=204)
async def telemetry(ev: EventIn, request: Request, background_tasks: BackgroundTasks) -> Response:
    if ev.site not in ALLOWED_SITES:
        return Response(status_code=400)

    props = ev.props
    if props is not None:
        import json
        try:
            if len(json.dumps(props)) > MAX_PROPS_BYTES:
                props = None  # drop oversized props rather than reject the event
        except (TypeError, ValueError):
            props = None

    background_tasks.add_task(
        db.record_event,
        site=ev.site,
        session_id=ev.session_id or "unknown",
        event_type=ev.event_type,
        target=ev.target,
        props=props,
        environment=_clean_env(ev.environment),
        app_version=ev.app_version,
        user_agent=(request.headers.get("user-agent") or "")[:MAX_STR] or None,
    )
    return Response(status_code=204)
