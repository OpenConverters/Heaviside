#!/usr/bin/env python3
"""Apply datasheet-URL repair patches (overwrite-mode, guard-gated).

``apply_patches.py`` is deliberately fill-only, but the URL-repair
campaign (datasheet_url_patches_*.ndjson, see datasheet_url_report.md)
exists precisely because ~134k rows hold a KNOWN-BAD value: a search
page / aggregator / placeholder URL matched by
``heaviside.librarian.guards.BAD_DATASHEET_URL_PATTERNS``.

This applier overwrites ``datasheetUrl`` ONLY when:

  * the row's current value is absent, equal to the patch, or matches a
    BAD_DATASHEET_URL_PATTERNS entry (i.e. provably junk), AND
  * the patch's new value does NOT itself match a bad pattern and is an
    http(s) URL.

A current value that is neither equal nor bad is a CONFLICT: reported,
never changed. Untouched rows stay byte-identical; atomic replace.

Usage:
    .venv-web/bin/python scripts/enrichment/apply_url_repair.py <category> [...]
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from heaviside.librarian.guards import BAD_DATASHEET_URL_PATTERNS

PATCH_DIR = Path(__file__).resolve().parent
DATA = REPO / "TAS" / "data"

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


def _is_bad(url: str) -> bool:
    return any(rx.search(url) for rx, _reason in BAD_DATASHEET_URL_PATTERNS)


def apply_category(category: str) -> None:
    patch_path = PATCH_DIR / f"datasheet_url_patches_{category}.ndjson"
    if not patch_path.exists():
        print(f"{category:11s} no patch file ({patch_path.name})")
        return
    patches: dict[str, dict[str, str]] = {}
    for line in patch_path.open():
        if not line.strip():
            continue
        p = json.loads(line)
        if p["category"] != category:
            raise RuntimeError(f"{patch_path.name}: stray category {p['category']}")
        for dotted, new_url in p["set"].items():
            if not isinstance(new_url, str) or not new_url.startswith(("http://", "https://")):
                raise RuntimeError(f"{p['mpn']}: non-URL patch value {new_url!r}")
            if _is_bad(new_url):
                raise RuntimeError(f"{p['mpn']}: patch value is itself a bad URL: {new_url}")
            patches.setdefault(p["mpn"], {})[dotted] = new_url

    path = DATA / f"{category}.ndjson"
    out: list[str] = []
    stats: Counter[str] = Counter()
    conflicts: list[str] = []
    for line in path.open():
        raw = line.rstrip("\n")
        if not raw.strip():
            continue
        row = json.loads(raw)
        body = _body(row, category)
        mpn = _mpn(body)
        todo = patches.get(mpn or "")
        if not todo:
            out.append(raw)
            continue
        changed = False
        for dotted, new_url in todo.items():
            keys = dotted.split(".")
            node = body
            for k in keys[:-1]:
                nxt = node.get(k)
                if not isinstance(nxt, dict):
                    nxt = {}
                    node[k] = nxt
                node = nxt
            leaf = keys[-1]
            cur = node.get(leaf)
            if cur == new_url:
                stats["already-equal"] += 1
            elif cur is None:
                node[leaf] = new_url
                stats["filled"] += 1
                changed = True
            elif isinstance(cur, str) and _is_bad(cur):
                node[leaf] = new_url
                stats["overwritten-bad"] += 1
                changed = True
            else:
                stats["conflict-good-value"] += 1
                if len(conflicts) < 5:
                    conflicts.append(f"{mpn}: kept {str(cur)[:60]}")
        if changed:
            stats["rows_changed"] += 1
            out.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        else:
            out.append(raw)

    tmp = path.with_suffix(".ndjson.urlrepair")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    tmp.replace(path)
    print(f"{category:11s} rows={len(out)}  {dict(stats)}")
    for c in conflicts:
        print(f"             conflict: {c}")


def main() -> int:
    cats = sys.argv[1:]
    if not cats:
        print("usage: apply_url_repair.py <category> [...]", file=sys.stderr)
        return 2
    unknown = set(cats) - set(ENVELOPE_KIND)
    if unknown:
        raise SystemExit(f"unknown categories: {sorted(unknown)}")
    for c in cats:
        apply_category(c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
