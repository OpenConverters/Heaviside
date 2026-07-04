#!/usr/bin/env python3
"""Backfill the specific EIA dielectric code (X7R/X5R/C0G/…) on MLCC records
that have a null ``part.dielectricCode``.

Why: the crossref param check gained a dielectric-code comparison + a max-temp
comparison (X5R 85 °C must not replace X7R 125 °C). Max operating temperature is
already populated on ~90% of ceramic records, so the physics gate works today;
this backfill additionally recovers the *label* where it is honestly derivable —
NO fabrication (the third EIA letter, e.g. R vs S, is not derivable from the
temperature range, so we never invent it).

Sources of the code, in order, all HONEST (same haystacks the live ingestion
`_resolve_capacitor_technology` uses):
  1. a literal EIA code printed in the MPN / series / partNumber
     (e.g. "...X7R..." — many vendors print it verbatim).
That's the only reliable, non-inferential source without a per-vendor MPN
decoder, so records whose code is not printed literally stay null (and the
max-temp gate covers the physics for them). Run with --apply to write.

Usage:
    python3 scripts/backfill_dielectric_code.py            # dry run (report only)
    python3 scripts/backfill_dielectric_code.py --apply    # rewrite in place
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from heaviside.catalogue.selector import _tas_data_dir  # noqa: E402
from heaviside.librarian.fetcher.convert import _CAP_EIA_CLASS  # noqa: E402

# Longest codes first so "X8L" wins over a stray "X8" substring, etc.
_CODES = sorted(_CAP_EIA_CLASS.keys(), key=len, reverse=True)


def _literal_code(part: dict) -> str | None:
    """Return the EIA code printed literally in the MPN/series/partNumber, or
    None. Case-insensitive exact-substring match against the known code set."""
    hay = " ".join(
        str(part.get(k) or "") for k in ("partNumber", "series")
    ).upper()
    for code in _CODES:
        if code in hay:
            return code
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write changes in place")
    ap.add_argument("--file", default=None, help="capacitors ndjson (default: TAS data dir)")
    args = ap.parse_args()

    path = Path(args.file) if args.file else (_tas_data_dir() / "capacitors.ndjson")
    if not path.exists():
        print(f"not found: {path}", file=sys.stderr)
        return 2

    total = ceramic = null_dc = filled = 0
    samples: list[tuple[str, str]] = []
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")

    with os.fdopen(tmp_fd, "w") as out, path.open() as fh:
        for line in fh:
            s = line.strip()
            if not s:
                continue
            total += 1
            env = json.loads(s)
            try:
                part = env["capacitor"]["manufacturerInfo"]["datasheetInfo"]["part"]
            except (KeyError, TypeError):
                out.write(line)
                continue
            tech = str(part.get("technology") or "")
            if "ceramic" not in tech:
                out.write(line)
                continue
            ceramic += 1
            if part.get("dielectricCode"):
                out.write(line)
                continue
            null_dc += 1
            code = _literal_code(part)
            if code:
                filled += 1
                if len(samples) < 12:
                    samples.append((part.get("partNumber") or "?", code))
                if args.apply:
                    part["dielectricCode"] = code
                    out.write(json.dumps(env, ensure_ascii=False) + "\n")
                    continue
            out.write(line)

    if args.apply and filled:
        os.replace(tmp_name, path)
        print(f"APPLIED — rewrote {path}")
    else:
        os.unlink(tmp_name)
        if args.apply:
            print("nothing to fill; left file unchanged")

    print(f"records: {total} | ceramic: {ceramic} | null dielectricCode: {null_dc} "
          f"| literal-code fillable: {filled}")
    print("samples (MPN -> code):")
    for mpn, code in samples:
        print(f"  {mpn} -> {code}")
    if not args.apply:
        print("\n(dry run — pass --apply to write)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
