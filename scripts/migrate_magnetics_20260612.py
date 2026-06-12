#!/usr/bin/env python3
"""TAS magnetics migration (2026-06-12, user-approved).

The magnetics fetcher wrote fields the MAS schema does not allow.
User decisions: map what maps onto existing MAS, add ONLY ``weight``
to MAS (done separately in MAS/schemas/magnetic.json), drop the rest
and lose the data — no other MAS modifications.

1. ``mechanical.assemblyType`` ('smt'/'tht') → ``mechanical.mounting``
   (MAS connectionType enum already has both values; data has zero
   rows where mounting is already set, verified — a conflict raises).
2. DROP ``part.componentType``, ``part.environment``,
   ``part.application``, ``part.impedanceAt100MHz``,
   ``part.impedanceAt1GHz`` (user-approved data loss).
3. Strip ``part.description: null`` (in-band unknown sentinel).
4. Strip string ``electrical.turnsRatio`` values ('1CT:1CT' — a
   center-tap notation not representable in the numeric field; the
   30 affected rows keep everything else).
5. ``core.functionalDescription.type``: legacy 'two-piece set'
   spelling → MAS enum 'twoPieceSet'.

``weight`` rows are untouched — they become legal via the MAS change.
Untouched rows stay byte-identical; unexpected shapes raise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "TAS" / "data" / "magnetics.ndjson"

DROP_PART_FIELDS = (
    "componentType",
    "environment",
    "application",
    "impedanceAt100MHz",
    "impedanceAt1GHz",
)


class MigrationError(RuntimeError):
    pass


def main() -> int:
    stats = {
        "rows": 0,
        "mounting_mapped": 0,
        "part_fields_dropped": 0,
        "null_description_stripped": 0,
        "string_turns_ratio_stripped": 0,
        "core_type_renamed": 0,
    }
    out_lines: list[str] = []

    for lineno, line in enumerate(DATA.open(), 1):
        raw = line.rstrip("\n")
        if not raw.strip():
            continue
        stats["rows"] += 1
        row = json.loads(raw)
        body = row.get("magnetic", row)
        dsi = body.get("manufacturerInfo", {}).get("datasheetInfo") or {}
        part = dsi.get("part") or {}
        mech = dsi.get("mechanical") or {}
        elec = dsi.get("electrical") or {}
        changed = False

        if "assemblyType" in mech:
            value = mech.pop("assemblyType")
            if value not in ("smt", "tht"):
                raise MigrationError(f"line {lineno}: unexpected assemblyType {value!r}")
            existing = mech.get("mounting")
            if existing is not None and existing != value:
                raise MigrationError(
                    f"line {lineno}: mounting={existing!r} conflicts with assemblyType={value!r}"
                )
            mech["mounting"] = value
            stats["mounting_mapped"] += 1
            changed = True

        for field in DROP_PART_FIELDS:
            if field in part:
                del part[field]
                stats["part_fields_dropped"] += 1
                changed = True

        if part.get("description", "?") is None:
            del part["description"]
            stats["null_description_stripped"] += 1
            changed = True

        if isinstance(elec.get("turnsRatio"), str):
            del elec["turnsRatio"]
            stats["string_turns_ratio_stripped"] += 1
            changed = True

        core_fd = (body.get("core") or {}).get("functionalDescription") or {}
        if core_fd.get("type") == "two-piece set":
            core_fd["type"] = "twoPieceSet"
            stats["core_type_renamed"] += 1
            changed = True

        if changed:
            out_lines.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        else:
            out_lines.append(raw)

    tmp = DATA.with_suffix(".ndjson.migrating")
    tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    tmp.replace(DATA)
    for k, v in stats.items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
