#!/usr/bin/env python3
"""Backfill manufacturerInfo.status='production' on Würth capacitors that are
present in the LIVE REDEXPERT orderable catalogue but carry a null/unknown
status in the internal DB.

Root cause: scripts/fetch_redexpert_wurth.py's convert_ceramic() never emitted a
status (the inductor converter does), so ~3.6k Würth caps landed with a null
status → normalised to 'unknown' → silently excluded by any selector path with
exclude_discontinued=True (the FAE trap C2 blind spot: the DB *has* a
100µF/6.3V/1206, but a production-filtered query returned nothing).

This is a VERIFIED backfill, not fabrication: a part returned by REDEXPERT's
get_products is an active, orderable Würth product → status='production'. Parts
NOT in the live catalogue are left 'unknown' (we cannot confirm them).

Usage:
    # 1. pull the active order-code set once (writes wurth_active_caps.json)
    python scripts/backfill_wurth_cap_status.py pull  <active.json>
    # 2. apply it to the canonical capacitors.ndjson (in place)
    python scripts/backfill_wurth_cap_status.py apply <active.json> <capacitors.ndjson>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CAP_FAMILIES = {"13": "Ceramic", "20": "AlEl/Polymer", "11": "Suppression"}


def pull(out_path: str) -> int:
    sys.path.insert(0, str(Path(__file__).parent))
    from redexpert_client import RedexpertClient

    c = RedexpertClient()
    active: set[str] = set()
    try:
        for fam, name in CAP_FAMILIES.items():
            rows = []
            for _ in range(3):
                try:
                    res = c.products(fam)
                    rows = res.get("results", []) if isinstance(res, dict) else []
                    break
                except Exception:  # noqa: BLE001 - REDEXPERT 500s are transient
                    rows = []
            codes = {str(p["orderCode"]) for p in rows if p.get("orderCode")}
            active |= codes
            print(f"family {fam} ({name}): {len(codes)} active order codes")
    finally:
        c.close()
    Path(out_path).write_text(json.dumps(sorted(active)))
    print(f"wrote {len(active)} active order codes -> {out_path}")
    return 0


def _wurth_cap_null_status(env: dict) -> bool:
    cap = env.get("capacitor")
    if not isinstance(cap, dict):
        return False
    mi = cap.get("manufacturerInfo") or {}
    name = str(mi.get("name") or "").strip().lower()
    if name not in ("würth elektronik", "wurth elektronik"):
        return False
    return mi.get("status") in (None, "", "unknown")


def _mpn_of(env: dict) -> str:
    mi = env["capacitor"]["manufacturerInfo"]
    ref = mi.get("reference")
    if isinstance(ref, str) and ref:
        return ref
    part = (mi.get("datasheetInfo") or {}).get("part") or {}
    return str(part.get("partNumber") or "")


def _is_wurth_cap(env: dict) -> bool:
    cap = env.get("capacitor")
    if not isinstance(cap, dict):
        return False
    name = str((cap.get("manufacturerInfo") or {}).get("name") or "").strip().lower()
    return name in ("würth elektronik", "wurth elektronik")


def apply(active_path: str, ndjson_path: str) -> int:
    active = set(json.loads(Path(active_path).read_text()))
    src = Path(ndjson_path)
    tmp = src.with_suffix(src.suffix + ".backfill.tmp")
    status_set = ref_set = skipped_not_active = 0
    with src.open(encoding="utf-8") as fin, tmp.open("w", encoding="utf-8") as fout:
        for line in fin:
            s = line.rstrip("\n")
            if not s.strip():
                fout.write(line)
                continue
            try:
                env = json.loads(s)
            except json.JSONDecodeError:
                fout.write(line)  # never drop a line we can't parse
                continue
            if not _is_wurth_cap(env):
                fout.write(line)
                continue
            mi = env["capacitor"]["manufacturerInfo"]
            mpn = _mpn_of(env)
            dirty = False
            # (a) reference: the selector keys on manufacturerInfo.reference; when
            # it is null the part is INVISIBLE to selection even though it has a
            # partNumber. Mirror the part's own number (verified, not fabricated).
            if not isinstance(mi.get("reference"), str) or not mi.get("reference"):
                if mpn:
                    mi["reference"] = mpn
                    ref_set += 1
                    dirty = True
            # (b) status: present in the live REDEXPERT catalogue => production.
            if mi.get("status") in (None, "", "unknown"):
                if mpn in active:
                    mi["status"] = "production"
                    status_set += 1
                    dirty = True
                else:
                    skipped_not_active += 1
            if dirty:
                fout.write(json.dumps(env, ensure_ascii=False) + "\n")
            else:
                fout.write(line)  # verbatim: untouched rows keep exact formatting
    tmp.replace(src)
    print(f"set reference (was null) on {ref_set} Würth caps")
    print(f"set status='production' on {status_set} Würth caps")
    print(f"left status 'unknown' (not in live catalogue): {skipped_not_active}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "pull":
        sys.exit(pull(sys.argv[2]))
    if len(sys.argv) >= 4 and sys.argv[1] == "apply":
        sys.exit(apply(sys.argv[2], sys.argv[3]))
    print(__doc__)
    sys.exit(2)
