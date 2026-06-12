#!/usr/bin/env python3
"""Apply datasheet-enrichment patch files to TAS data.

Patch format (one JSON object per line, produced by the enrichment
agents; provenance lives in the patch files, not in the rows):

    {"category": "diodes", "mpn": "...",
     "set": {"manufacturerInfo.datasheetInfo.electrical.forwardVoltage": 0.55, ...},
     "source": "<datasheet url>", "evidence": "..."}

Rules:
  * FILL-ONLY — an existing value is never overwritten. If a row
    already has a DIFFERENT value than the patch, it is counted and
    reported as a conflict, not changed.
  * A patch MPN matches every TAS row with that partNumber (the DB
    carries duplicates).
  * Untouched rows stay byte-identical; atomic replace per file.
  * Unknown category / malformed patch line raises.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "TAS" / "data"
PATCH_DIR = Path(__file__).resolve().parent

ENVELOPE_KIND = {
    "diodes": "diode",
    "mosfets": "mosfet",
    "igbts": "igbt",
    "capacitors": "capacitor",
    "resistors": "resistor",
    "magnetics": "magnetic",
}


def _body(row: dict, category: str) -> dict:
    kind = ENVELOPE_KIND[category]
    sem = row.get("semiconductor")
    if isinstance(sem, dict) and isinstance(sem.get(kind), dict):
        return sem[kind]
    if isinstance(row.get(kind), dict):
        return row[kind]
    return row


def _mpn(body: dict) -> str | None:
    return (
        body.get("manufacturerInfo", {})
        .get("datasheetInfo", {})
        .get("part", {})
        .get("partNumber")
    )


def _set_path(body: dict, dotted: str, value: object) -> str:
    """Returns 'filled' | 'already-equal' | 'conflict'."""
    keys = dotted.split(".")
    node = body
    for k in keys[:-1]:
        nxt = node.get(k)
        if nxt is None:
            nxt = {}
            node[k] = nxt
        if not isinstance(nxt, dict):
            raise RuntimeError(f"path {dotted}: {k} is not an object")
        node = nxt
    leaf = keys[-1]
    if leaf in node and node[leaf] is not None:
        return "already-equal" if node[leaf] == value else "conflict"
    node[leaf] = value
    return "filled"


def load_patches() -> dict[str, dict[str, list[dict]]]:
    by_cat: dict[str, dict[str, list[dict]]] = {}
    for pf in sorted(PATCH_DIR.glob("*_patch.ndjson")):
        for lineno, line in enumerate(pf.open(), 1):
            if not line.strip():
                continue
            p = json.loads(line)
            cat, mpn, to_set = p["category"], p["mpn"], p["set"]
            if cat not in ENVELOPE_KIND:
                raise RuntimeError(f"{pf.name}:{lineno}: unknown category {cat!r}")
            if not isinstance(to_set, dict) or not to_set:
                raise RuntimeError(f"{pf.name}:{lineno}: empty/invalid set")
            by_cat.setdefault(cat, {}).setdefault(mpn, []).append(p)
    return by_cat


def apply_category(category: str, patches: dict[str, list[dict]]) -> None:
    path = DATA / f"{category}.ndjson"
    stats: Counter[str] = Counter()
    matched_mpns: set[str] = set()
    conflicts: list[tuple[str, str]] = []
    out: list[str] = []

    for line in path.open():
        raw = line.rstrip("\n")
        if not raw.strip():
            continue
        stats["rows"] += 1
        row = json.loads(raw)
        body = _body(row, category)
        mpn = _mpn(body)
        plist = patches.get(mpn or "")
        if not plist:
            out.append(raw)
            continue
        matched_mpns.add(mpn)  # type: ignore[arg-type]
        changed = False
        for p in plist:
            for dotted, value in p["set"].items():
                result = _set_path(body, dotted, value)
                stats[result] += 1
                if result == "filled":
                    changed = True
                elif result == "conflict":
                    conflicts.append((mpn, dotted))  # type: ignore[arg-type]
        if changed:
            stats["rows_changed"] += 1
            out.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        else:
            out.append(raw)

    tmp = path.with_suffix(".ndjson.patching")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    tmp.replace(path)

    unmatched = sorted(set(patches) - matched_mpns)
    print(
        f"{category:11s} rows={stats['rows']}  rows_changed={stats['rows_changed']}  "
        f"values_filled={stats['filled']}  already_equal={stats['already-equal']}  "
        f"conflicts={stats['conflict']}  patch_mpns={len(patches)}  unmatched_mpns={len(unmatched)}"
    )
    for mpn, dotted in conflicts[:20]:
        print(f"  CONFLICT {mpn} {dotted} (existing value differs — left unchanged)")
    if unmatched[:10]:
        print(f"  unmatched sample: {unmatched[:10]}")


def main() -> int:
    only = set(sys.argv[1:])  # optional category filter, e.g. `apply_patches.py capacitors diodes`
    by_cat = load_patches()
    if not by_cat:
        print("no *_patch.ndjson files found")
        return 1
    for category, patches in sorted(by_cat.items()):
        if only and category not in only:
            print(f"{category:11s} skipped (not in filter)")
            continue
        apply_category(category, patches)
    return 0


if __name__ == "__main__":
    sys.exit(main())
