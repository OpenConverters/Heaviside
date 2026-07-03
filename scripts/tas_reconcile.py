#!/usr/bin/env python3
"""One-off reconcile helper for a divergent TAS data copy (e.g. prod).

Two modes, bandwidth-light (only MPN lists + novel rows move between hosts):

  --mode dump-mpns   : write one <category>.txt of sorted unique MPNs per main
                       category file in --data-dir, to --out-dir. (Run on the
                       canonical host; ship the small output to the divergent one.)

  --mode extract-novel : scan --data-dir (the divergent copy) and emit, to
                       --out-dir, one <category>.ndjson of the rows whose MPN is
                       NOT in --known-mpns-dir/<category>.txt — i.e. the parts the
                       divergent copy has that canonical lacks. Feed the output to
                       scripts/merge_tas_delta.py to fold them into canonical.

Only the librarian's real categories are processed (quarantine / aux ndjson are
skipped). MPNs are compared case-insensitively via the librarian's envelope-aware
extractor, so envelope shape differences don't matter.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _iter_category_files(data_dir: Path):
    # Only the librarian's schema-backed categories — the ones add_component can
    # re-validate + persist. Skips quarantine/aux ndjson and schema-less
    # categories (controllers/converters/quarantine/analog_ics) that a runtime
    # reconcile must not touch.
    from heaviside.librarian.tas import SCHEMA_MAP

    for f in sorted(data_dir.glob("*.ndjson")):
        if f.stem in SCHEMA_MAP:
            yield f


def _mpn_of(raw: str):
    from heaviside.librarian.tas import _envelope_mpn

    try:
        return _envelope_mpn(json.loads(raw))
    except Exception:
        return None


def dump_mpns(data_dir: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in _iter_category_files(data_dir):
        mpns = set()
        for raw in f.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            m = _mpn_of(raw)
            if m:
                mpns.add(m)
        (out_dir / f"{f.stem}.txt").write_text("\n".join(sorted(mpns)) + "\n", encoding="utf-8")
        print(f"  {f.stem}: {len(mpns)} mpns")


def extract_novel(data_dir: Path, known_dir: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for f in _iter_category_files(data_dir):
        known_file = known_dir / f"{f.stem}.txt"
        known = (
            {ln.strip().upper() for ln in known_file.read_text().splitlines() if ln.strip()}
            if known_file.exists()
            else set()
        )
        novel = []
        for raw in f.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            m = _mpn_of(raw)
            if m and m.upper() not in known:
                novel.append(raw)
        if novel:
            (out_dir / f"{f.stem}.ndjson").write_text("\n".join(novel) + "\n", encoding="utf-8")
            print(f"  {f.stem}: {len(novel)} novel (of {len(known)} known)")
            total += len(novel)
    print(f"total novel rows: {total}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", required=True, choices=["dump-mpns", "extract-novel"])
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--known-mpns-dir", help="required for extract-novel")
    args = ap.parse_args()

    data_dir = Path(args.data_dir).expanduser()
    out_dir = Path(args.out_dir).expanduser()
    if args.mode == "dump-mpns":
        dump_mpns(data_dir, out_dir)
    else:
        if not args.known_mpns_dir:
            ap.error("--known-mpns-dir is required for --mode extract-novel")
        extract_novel(data_dir, Path(args.known_mpns_dir).expanduser(), out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
