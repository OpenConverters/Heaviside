#!/usr/bin/env python3
"""One-off TAS data migration (2026-06-12), user-approved.

Four mechanical, loss-free transforms — anything unexpected raises, the
file is then left untouched (atomic replace only on success):

1. diodes:    drop literal-null ``reverseRecoveryCharge`` (schema says
              number-or-absent; null was an in-band "unknown" sentinel).
2. resistors, diodes, mosfets:
              ``business.distribution`` is the wrong home for stocking
              info — convert to ``distributorsInfo`` entries (PEAS
              ``distributorInfo``, required: name) at the component body
              root, and delete the business key. Only values seen in the
              DB are mapped; an unknown value aborts the migration.
3. magnetics: wrap scalar ``dcResistance`` as ``{"nominal": x}``
              (MAS ``dimensionWithTolerance``).

Run from the Heaviside repo root: ``python scripts/migrate_tas_20260612.py``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "TAS" / "data"

# Explicit allowlist — an unmapped distribution value is an error, not a guess.
DISTRIBUTION_MAP = {
    "Mouser/DigiKey": ["Mouser", "DigiKey"],
    "TI Direct": ["TI Direct"],
}


class MigrationError(RuntimeError):
    pass


def _body(row: dict, category: str) -> dict:
    """Return the component body the schemas validate (mutable view)."""
    if category in ("diodes", "mosfets", "igbts"):
        kind = category[:-1]
        sem = row.get("semiconductor")
        if isinstance(sem, dict) and isinstance(sem.get(kind), dict):
            return sem[kind]
        if isinstance(row.get(kind), dict):
            return row[kind]
        return row
    kind = {"resistors": "resistor", "capacitors": "capacitor", "magnetics": "magnetic"}[category]
    return row[kind] if isinstance(row.get(kind), dict) else row


def _move_distribution(body: dict, where: str) -> bool:
    business = (
        body.get("manufacturerInfo", {}).get("datasheetInfo", {}).get("business")
    )
    if not isinstance(business, dict) or "distribution" not in business:
        return False
    value = business.pop("distribution")
    if value is None:
        return True  # null sentinel: nothing to preserve
    if value not in DISTRIBUTION_MAP:
        raise MigrationError(f"{where}: unmapped distribution value {value!r}")
    entries = body.get("distributorsInfo")
    if entries is None or entries == []:
        entries = []
        body["distributorsInfo"] = entries
    if not isinstance(entries, list):
        raise MigrationError(f"{where}: distributorsInfo is not a list")
    have = {e.get("name") for e in entries if isinstance(e, dict)}
    for name in DISTRIBUTION_MAP[value]:
        if name not in have:
            entries.append({"name": name})
    return True


def migrate(category: str) -> None:
    path = DATA / f"{category}.ndjson"
    out_lines: list[str] = []
    stats = {"rows": 0, "qrr_stripped": 0, "distribution_moved": 0, "dcr_wrapped": 0}

    for lineno, line in enumerate(path.open(), 1):
        if not line.strip():
            continue
        stats["rows"] += 1
        row = json.loads(line)  # corrupt line -> loud JSONDecodeError
        body = _body(row, category)
        where = f"{category}:{lineno}"
        changed_before = sum(stats[k] for k in ("qrr_stripped", "distribution_moved", "dcr_wrapped"))

        if category == "diodes":
            elec = body.get("manufacturerInfo", {}).get("datasheetInfo", {}).get("electrical")
            if isinstance(elec, dict) and elec.get("reverseRecoveryCharge", "?") is None:
                del elec["reverseRecoveryCharge"]
                stats["qrr_stripped"] += 1

        if category in ("resistors", "diodes", "mosfets") and _move_distribution(body, where):
            stats["distribution_moved"] += 1

        if category == "magnetics":
            elec = body.get("manufacturerInfo", {}).get("datasheetInfo", {}).get("electrical")
            if isinstance(elec, dict):
                dcr = elec.get("dcResistance", "?")
                if isinstance(dcr, (int, float)) and not isinstance(dcr, bool):
                    elec["dcResistance"] = {"nominal": dcr}
                    stats["dcr_wrapped"] += 1
                elif not (dcr == "?" or dcr is None or isinstance(dcr, dict)):
                    raise MigrationError(f"{where}: dcResistance has unexpected type {type(dcr).__name__}")

        changed_after = sum(stats[k] for k in ("qrr_stripped", "distribution_moved", "dcr_wrapped"))
        if changed_after == changed_before:
            out_lines.append(line.rstrip("\n"))  # untouched rows stay byte-identical
        else:
            out_lines.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))

    tmp = path.with_suffix(".ndjson.migrating")
    tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    tmp.replace(path)
    changed = stats["qrr_stripped"] + stats["distribution_moved"] + stats["dcr_wrapped"]
    print(f"{category:11s} rows={stats['rows']}  qrr_stripped={stats['qrr_stripped']}  "
          f"distribution_moved={stats['distribution_moved']}  dcr_wrapped={stats['dcr_wrapped']}"
          f"  ({'unchanged' if changed == 0 else 'MIGRATED'})")


def main() -> int:
    for category in ("diodes", "mosfets", "resistors", "magnetics"):
        migrate(category)
    return 0


if __name__ == "__main__":
    sys.exit(main())
