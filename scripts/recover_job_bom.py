#!/usr/bin/env python3
"""Recover the ORIGINAL uploaded BOM file for a crossref job, from telemetry.

The persisted job JSON (`~/.heaviside/jobs/<id>.json`) holds only the RESULT —
not the filename or the bytes. The raw upload is in Postgres
`heaviside_telemetry.events`: `input_file_data` (BYTEA), `input_file_name`, and
`input_bom` (JSONB, the parse output). So "what exactly did the user send us?"
is answerable exactly, rather than reconstructed from a screenshot.

Runs ON PROD, where the DB settings already live in the supervisor unit — they
are read from there rather than passed on a command line, so no credential ends
up in a shell history or a process list.

    # on prod (ssh -i ~/.ssh/om_scaleway root@51.15.253.66)
    /home/alf/OpenConverters/Heaviside/.venv/bin/python scripts/recover_job_bom.py --list
    /home/alf/OpenConverters/Heaviside/.venv/bin/python scripts/recover_job_bom.py \
        --job 1b73eec81dbd -o /tmp/recovered.xlsx

``--job`` matches the job id by prefix; ``--name`` matches the filename instead
(ILIKE, so ``TestBOM`` finds ``TestBOM_3.xlsx``).
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

SUPERVISOR_CONF = "/etc/supervisor/conf.d/heaviside.conf"
_REQUIRED = ("OM_DB_ADDRESS", "OM_DB_PORT", "OM_DB_NAME", "OM_DB_USER", "OM_DB_PASSWORD")


def _db_settings() -> dict[str, str]:
    """DB settings from the supervisor unit that already holds them."""
    try:
        conf = pathlib.Path(SUPERVISOR_CONF).read_text()
    except OSError as exc:
        raise SystemExit(
            f"cannot read {SUPERVISOR_CONF}: {exc} (run this on prod, as root)"
        ) from exc
    env = dict(re.findall(r'(OM_DB_[A-Z]+)="([^"]*)"', conf))
    missing = [k for k in _REQUIRED if k not in env]
    if missing:
        raise SystemExit(f"{SUPERVISOR_CONF} lacks {missing}")
    return env


def _connect():
    import psycopg2

    env = _db_settings()
    return psycopg2.connect(
        host=env["OM_DB_ADDRESS"],
        port=env["OM_DB_PORT"],
        dbname=env["OM_DB_NAME"],
        user=env["OM_DB_USER"],
        password=env["OM_DB_PASSWORD"],
        connect_timeout=20,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="list recent uploads, recover nothing")
    ap.add_argument("--job", help="job id (prefix match)")
    ap.add_argument("--name", help="uploaded filename (ILIKE substring match)")
    ap.add_argument("-o", "--out", help="write the recovered file here")
    ap.add_argument("--limit", type=int, default=25)
    args = ap.parse_args()

    cur = _connect().cursor()

    if args.list or not (args.job or args.name):
        cur.execute(
            """
            SELECT id, created_at, input_file_name,
                   octet_length(input_file_data),
                   CASE WHEN input_bom IS NULL THEN NULL
                        ELSE jsonb_array_length(input_bom) END
            FROM heaviside_telemetry.events
            WHERE input_file_name IS NOT NULL
            ORDER BY created_at DESC LIMIT %s
            """,
            (args.limit,),
        )
        rows = cur.fetchall()
        print(f"{len(rows)} recent event(s) carrying an uploaded file:\n")
        for jid, when, name, nbytes, nrows in rows:
            print(f"  {str(jid)[:16]:18} {when}  {str(name)[:30]:32} {nbytes} B  {nrows} rows")
        if not rows:
            print("  (none — telemetry may not be recording uploads on this host)")
        return 0

    if args.job:
        where, param = "CAST(id AS TEXT) LIKE %s", f"{args.job}%"
    else:
        where, param = "input_file_name ILIKE %s", f"%{args.name}%"

    cur.execute(
        f"""
        SELECT id, created_at, input_file_name, input_file_data
        FROM heaviside_telemetry.events
        WHERE {where} AND input_file_data IS NOT NULL
        ORDER BY created_at DESC LIMIT 1
        """,
        (param,),
    )
    row = cur.fetchone()
    if row is None:
        raise SystemExit("no event matched, or the upload bytes were not recorded")

    jid, when, name, data = row
    blob = bytes(data)
    out = pathlib.Path(args.out or (name or f"{jid}.bin"))
    out.write_bytes(blob)
    print(f"job {jid}  {when}\noriginal name: {name}\nwrote {len(blob)} bytes -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
