#!/usr/bin/env python3
"""Merge a TAS delta journal into the canonical ``TAS/data``.

The journal is produced by :mod:`heaviside.librarian.delta` on a deployed host
(one append-only ``<category>.ndjson`` per category of parts added at runtime).
This replays each row through the librarian's :func:`add_component`, so every
merged part is RE-validated (schema + the C++ physics validator, "Blade Runner")
on the way in and de-duplicated by MPN — a foreign or corrupt row can never enter
canonical, and re-running is idempotent (already-present MPNs are skipped).

Usage
-----
    python scripts/merge_tas_delta.py --delta-dir <dir> [--data-dir <TAS/data>]

``--data-dir`` retargets the canonical ``TAS/data`` the librarian writes to (by
default it uses the repo's ``TAS/data``, which is symlinked to the canonical
checkout in this environment).  Exits non-zero if any row failed to merge.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--delta-dir", required=True, help="the harvested delta journal directory")
    ap.add_argument("--data-dir", default=None, help="override canonical TAS/data location")
    args = ap.parse_args()

    delta = Path(args.delta_dir).expanduser()
    if not delta.is_dir():
        print(f"merge_tas_delta: delta dir {delta} does not exist", file=sys.stderr)
        return 2

    from heaviside.librarian import safe_access as sa

    if args.data_dir:
        sa.TAS_DATA_DIR = Path(args.data_dir).expanduser()  # retarget the librarian writer

    from heaviside.librarian.tas import DuplicateComponentError, add_component

    added = skipped = failed = 0
    files = sorted(delta.glob("*.ndjson"))
    if not files:
        print("merge_tas_delta: journal is empty — nothing to merge")
        return 0

    for f in files:
        category = f.stem
        for lineno, raw in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                add_component(category, json.loads(raw))
                added += 1
            except DuplicateComponentError:
                skipped += 1
            except Exception as exc:  # validation / physics / parse — surface, don't swallow
                failed += 1
                print(f"  FAIL {f.name}:{lineno}: {str(exc)[:160]}", file=sys.stderr)

    print(f"merge_tas_delta: added={added} skipped(dup)={skipped} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
