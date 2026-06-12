#!/usr/bin/env python
"""Apply datasheet-extracted enrichment fields to TAS/data/controllers.ndjson.

Reads an enrichment JSON array (objects with name/source/fields/evidence),
matches catalog rows by exact ``name``, and ADDS missing fields only:

- an existing field with the same value is left untouched (verified);
- an existing field with a DIFFERENT value is never overwritten — it is
  reported as a disagreement and the row is left as-is for that field;
- rows not named in the enrichment file stay byte-identical (their original
  line text is written back verbatim);
- the write is atomic (tmp file + os.replace) and the row count is asserted
  unchanged.

Provenance ({name, fields, source, evidence}) is appended to
scripts/enrichment/controllers_provenance.ndjson, replacing any previous
entry for the same name.

Usage:
    python scripts/enrichment/apply_controller_enrichment.py <enrichment.json>
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CATALOG = REPO / "TAS" / "data" / "controllers.ndjson"
PROVENANCE = REPO / "scripts" / "enrichment" / "controllers_provenance.ndjson"

ALLOWED_FIELDS = {
    "feedbackReferenceVoltage",
    "gateDriveVoltage",
    "vccBypassCapacitance",
    "softStartCurrent",
    "currentSenseThresholdVoltage",
}


def main(enrichment_path: str) -> int:
    entries = json.loads(Path(enrichment_path).read_text())
    by_name: dict[str, dict] = {}
    for e in entries:
        bad = set(e.get("fields", {})) - ALLOWED_FIELDS
        if bad:
            raise SystemExit(f"{e['name']}: unexpected fields {sorted(bad)}")
        by_name[e["name"]] = e

    original_lines = CATALOG.read_text().splitlines(keepends=True)
    n_rows_in = sum(1 for l in original_lines if l.strip())

    out_lines: list[str] = []
    added: dict[str, list[str]] = {}
    verified: dict[str, list[str]] = {}
    disagreements: list[dict] = []
    matched: set[str] = set()

    for line in original_lines:
        if not line.strip():
            out_lines.append(line)
            continue
        row = json.loads(line)
        name = row.get("name")
        entry = by_name.get(name)
        if entry is None:
            out_lines.append(line)  # untouched rows stay byte-identical
            continue
        matched.add(name)
        changed = False
        for field, value in entry["fields"].items():
            if field in row:
                if row[field] == value:
                    verified.setdefault(name, []).append(field)
                else:
                    disagreements.append(
                        {
                            "name": name,
                            "field": field,
                            "stored": row[field],
                            "datasheet": value,
                            "source": entry["source"],
                        }
                    )
            else:
                row[field] = value
                added.setdefault(name, []).append(field)
                changed = True
        if changed:
            eol = "\n" if line.endswith("\n") else ""
            out_lines.append(json.dumps(row) + eol)
        else:
            out_lines.append(line)

    n_rows_out = sum(1 for l in out_lines if l.strip())
    if n_rows_out != n_rows_in:
        raise SystemExit(f"row count changed: {n_rows_in} -> {n_rows_out}; aborting")

    # Atomic write of the catalog.
    fd, tmp = tempfile.mkstemp(dir=CATALOG.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.writelines(out_lines)
        os.replace(tmp, CATALOG)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    # Provenance: replace any previous entry for the same name, append new.
    prov_rows: list[dict] = []
    if PROVENANCE.exists():
        for l in PROVENANCE.read_text().splitlines():
            if l.strip() and json.loads(l)["name"] not in by_name:
                prov_rows.append(json.loads(l))
    for name in sorted(by_name):
        e = by_name[name]
        prov_rows.append(
            {
                "name": name,
                "fields": e["fields"],
                "source": e["source"],
                "evidence": e.get("evidence", {}),
                "omitted": e.get("omitted", {}),
            }
        )
    fd, tmp = tempfile.mkstemp(dir=PROVENANCE.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            for r in prov_rows:
                fh.write(json.dumps(r) + "\n")
        os.replace(tmp, PROVENANCE)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    unmatched = sorted(set(by_name) - matched)
    print(f"rows: {n_rows_in} (unchanged)")
    print(f"rows enriched: {len(added)}")
    for name in sorted(added):
        print(f"  + {name}: {', '.join(sorted(added[name]))}")
    print(f"rows verified-only (field already present, equal): {len(verified)}")
    for name in sorted(verified):
        print(f"  = {name}: {', '.join(sorted(verified[name]))}")
    print(f"disagreements (NOT overwritten): {len(disagreements)}")
    for d in disagreements:
        print(f"  ! {d['name']}.{d['field']}: stored={d['stored']} datasheet={d['datasheet']}")
    if unmatched:
        print(f"UNMATCHED enrichment names (no catalog row): {unmatched}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    sys.exit(main(sys.argv[1]))
