#!/usr/bin/env python3
"""Read a crossref job's recorded telemetry from Postgres (forensics).

The persisted job JSON (`~/.heaviside/jobs/<id>.json`) keeps the RESULT — or,
for a failed run, only the traceback. Everything about the INPUT and the
per-component outcome lives in `heaviside_telemetry.events`. That is where to
look when a run dies at assembly and takes its rows with it.

Runs ON PROD, reading the DB settings from the supervisor unit that already
holds them, so no credential is passed on a command line.

    /home/alf/OpenConverters/Heaviside/.venv/bin/python \
        scripts/inspect_job_telemetry.py --list
    ... --job dcacf7fcd9a6
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

SUPERVISOR_CONF = "/etc/supervisor/conf.d/heaviside.conf"
_REQUIRED = ("OM_DB_ADDRESS", "OM_DB_PORT", "OM_DB_NAME", "OM_DB_USER", "OM_DB_PASSWORD")


def _connect():
    import psycopg2

    try:
        conf = pathlib.Path(SUPERVISOR_CONF).read_text()
    except OSError as exc:
        raise SystemExit(f"cannot read {SUPERVISOR_CONF}: {exc} (run on prod, as root)") from exc
    env = dict(re.findall(r'(OM_DB_[A-Z_]+)\s*=\s*"?([^",\s]+)"?', conf))
    missing = [k for k in _REQUIRED if k not in env]
    if missing:
        raise SystemExit(f"{SUPERVISOR_CONF} lacks {missing}")
    return psycopg2.connect(
        host=env["OM_DB_ADDRESS"], port=env["OM_DB_PORT"], dbname=env["OM_DB_NAME"],
        user=env["OM_DB_USER"], password=env["OM_DB_PASSWORD"], connect_timeout=20,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--job", help="job id (prefix match)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--limit", type=int, default=15)
    args = ap.parse_args()
    cur = _connect().cursor()

    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='heaviside_telemetry' AND table_name='events'
        ORDER BY ordinal_position
    """)
    cols = [r[0] for r in cur.fetchall()]
    if not cols:
        raise SystemExit("heaviside_telemetry.events not found")
    print("columns:", ", ".join(cols), "\n")

    idc = "job_id" if "job_id" in cols else "id"
    tsc = next((c for c in ("created_at", "started_at", "ts", "timestamp") if c in cols), None)
    order = f"ORDER BY {tsc} DESC" if tsc else ""

    if args.list or not args.job:
        sel = [idc] + [c for c in (tsc, "kind", "status", "input_file_name") if c in cols]
        cur.execute(f"SELECT {', '.join(sel)} FROM heaviside_telemetry.events {order} LIMIT %s",
                    (args.limit,))
        for row in cur.fetchall():
            print("  " + "  ".join(str(v)[:34] for v in row))
        return 0

    cur.execute(
        f"SELECT * FROM heaviside_telemetry.events WHERE CAST({idc} AS TEXT) LIKE %s {order} LIMIT 1",
        (f"{args.job}%",),
    )
    row = cur.fetchone()
    if row is None:
        raise SystemExit(f"no telemetry event for job {args.job!r}")
    for name, value in zip(cols, row, strict=True):
        if value is None:
            continue
        if isinstance(value, (bytes, memoryview)):
            print(f"{name}: <{len(bytes(value))} bytes>")
            continue
        text = json.dumps(value, default=str) if isinstance(value, (dict, list)) else str(value)
        print(f"{name}: {text[:4000]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
