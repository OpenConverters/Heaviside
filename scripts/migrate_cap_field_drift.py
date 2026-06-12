#!/usr/bin/env python3
"""One-off TAS capacitor field-drift migration (2026-06-12).

Companion to the CAS schema update of the same date (qFactor /
qFactorFrequency, dimensions.thickness and datasheetInfo.application were
ADDED to the schema, and thermal.temperature / mechanical.dimensions were
made optional — those four drifts need no data change). This script handles
the two drifts that DO need a data change, both evidence-backed by a full
sweep of capacitors.ndjson:

1. ``electrical.temperatureCharacteristic`` (29,014 rows, all ceramic) is a
   duplicate of ``part.dielectricCode``: 28,874 rows carry BOTH, and in every
   single case the dielectricCode equals one of the slash-separated
   EIA/JIS dual-notation tokens of the characteristic (e.g. tc "C0G/CG" with
   dc "C0G", tc "C0H/CH" with dc "CH", tc "X5R/B" with dc "X5R").
   - both present & consistent  -> drop temperatureCharacteristic
   - dielectricCode absent      -> promote via the explicit allowlist below
                                   (only values observed in the DB; anything
                                   else aborts the migration — no guessing)
   - genuinely conflicting      -> leave the row untouched, count it
   - tc "(undefined)" carries no information -> drop it (dielectricCode, when
     present, stays as-is)
   A parenthetical measurement-condition annotation (one row:
   "X7R(Specified with 1/2 of rated voltage applied)") matches its base code;
   it is dropped but counted and printed so the loss is surfaced, not silent.

2. ``thermal.temperature: {}`` (3,761 rows) — an empty object is not a valid
   dimensionWithTolerance and carries zero information. The key is removed;
   if the thermal section becomes empty it is removed too (thermal is
   optional at the datasheetInfo level). Rows MISSING temperature (725) are
   already legal after the requiredness relaxation and are not touched.

Anything unexpected raises and the file is left untouched (atomic replace
only on success). Untouched rows stay byte-identical.

Run from the Heaviside repo root: ``python scripts/migrate_cap_field_drift.py``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "TAS" / "data" / "capacitors.ndjson"

#: Explicit promotion allowlist for rows that have a temperatureCharacteristic
#: but NO dielectricCode. Derived from a full DB sweep (2026-06-12): only
#: X8L (112), X5R (20), X6T (5) and X7R (3) occur — all plain EIA codes, all
#: Taiyo Yuden. The remaining observed tc values always co-occur with a
#: dielectricCode and are listed here for completeness (canonical code = the
#: dielectricCode every such row already carries). An unmapped value aborts.
TC_TO_DIELECTRIC: dict[str, str] = {
    "X7R": "X7R",
    "C0G": "C0G",
    "C0G/CG": "C0G",
    "X5R": "X5R",
    "C0H/CH": "CH",
    "X8R": "X8R",
    "X7S": "X7S",
    "NP0": "NP0",
    "JB": "JB",
    "CH": "CH",
    "X6S": "X6S",
    "X7T": "X7T",
    "C0K/CK": "C0K",
    "SL": "SL",
    "X5R/B": "X5R",
    "B": "B",
    "R": "R",
    "X8L": "X8L",
    "E": "E",
    "C0J/CJ": "C0J",
    "Z5U": "Z5U",
    "U2J/UJ": "U2J",
    "X6T": "X6T",
    "F": "F",
    "U2K/UK": "U2K",
}

#: tc value that carries no information at all (observed 84x, always next to
#: dielectricCode "SD") — dropped without promoting anything.
TC_UNDEFINED = "(undefined)"


class MigrationError(RuntimeError):
    pass


def _tc_tokens(tc: str) -> set[str]:
    """Slash-separated EIA/JIS dual-notation tokens, parenthetical
    measurement-condition annotations stripped, upper-cased."""
    base = tc.split("(", 1)[0].strip()
    return {tok.strip().upper() for tok in base.split("/") if tok.strip()}


def _has_annotation(tc: str) -> bool:
    return "(" in tc and tc != TC_UNDEFINED


def migrate() -> None:
    out_lines: list[str] = []
    stats = {
        "rows": 0,
        "tc_dropped_consistent": 0,   # both present, dielectricCode confirmed
        "tc_promoted": 0,             # dielectricCode absent -> set from tc
        "tc_undefined_dropped": 0,    # "(undefined)" -> dropped, nothing set
        "tc_conflict_left": 0,        # genuine conflict -> row untouched
        "tc_annotation_stripped": 0,  # parenthetical condition lost (printed)
        "empty_temp_stripped": 0,     # thermal.temperature == {} removed
        "empty_thermal_removed": 0,   # thermal became {} and was removed
    }
    conflicts: list[str] = []
    annotated: list[str] = []

    for lineno, line in enumerate(PATH.open(encoding="utf-8"), 1):
        if not line.strip():
            continue
        stats["rows"] += 1
        row = json.loads(line)  # corrupt line -> loud JSONDecodeError
        body = row["capacitor"] if isinstance(row.get("capacitor"), dict) else row
        dsi = body.get("manufacturerInfo", {}).get("datasheetInfo")
        where = f"capacitors:{lineno}"
        if not isinstance(dsi, dict):
            raise MigrationError(f"{where}: no manufacturerInfo.datasheetInfo object")
        changed = False

        # 1. temperatureCharacteristic -> part.dielectricCode
        elec = dsi.get("electrical")
        if isinstance(elec, dict) and "temperatureCharacteristic" in elec:
            tc = elec["temperatureCharacteristic"]
            if not isinstance(tc, str) or not tc.strip():
                raise MigrationError(
                    f"{where}: temperatureCharacteristic has unexpected value {tc!r}"
                )
            part = dsi.get("part")
            if not isinstance(part, dict):
                raise MigrationError(f"{where}: row has electrical but no part object")
            dc = part.get("dielectricCode")
            pn = part.get("partNumber", "?")

            if tc == TC_UNDEFINED:
                del elec["temperatureCharacteristic"]
                stats["tc_undefined_dropped"] += 1
                changed = True
            elif isinstance(dc, str) and dc.strip():
                if dc.strip().upper() in _tc_tokens(tc):
                    if _has_annotation(tc):
                        stats["tc_annotation_stripped"] += 1
                        annotated.append(f"{where} pn={pn} tc={tc!r} dc={dc!r}")
                    del elec["temperatureCharacteristic"]
                    stats["tc_dropped_consistent"] += 1
                    changed = True
                else:
                    stats["tc_conflict_left"] += 1
                    conflicts.append(f"{where} pn={pn} tc={tc!r} dc={dc!r}")
            elif dc is None or "dielectricCode" not in part:
                if tc not in TC_TO_DIELECTRIC:
                    raise MigrationError(
                        f"{where}: unmapped temperatureCharacteristic {tc!r} "
                        f"with no dielectricCode (pn={pn})"
                    )
                part["dielectricCode"] = TC_TO_DIELECTRIC[tc]
                del elec["temperatureCharacteristic"]
                stats["tc_promoted"] += 1
                changed = True
            else:
                raise MigrationError(
                    f"{where}: dielectricCode has unexpected type "
                    f"{type(dc).__name__} (pn={pn})"
                )

        # 2. strip empty thermal.temperature objects
        thermal = dsi.get("thermal")
        if isinstance(thermal, dict) and thermal.get("temperature") == {}:
            del thermal["temperature"]
            stats["empty_temp_stripped"] += 1
            changed = True
            if not thermal:
                del dsi["thermal"]
                stats["empty_thermal_removed"] += 1

        if changed:
            out_lines.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        else:
            out_lines.append(line.rstrip("\n"))  # untouched rows stay byte-identical

    tmp = PATH.with_suffix(".ndjson.migrating")
    tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    tmp.replace(PATH)

    print(f"capacitors  rows={stats['rows']}")
    print(f"  tc_dropped_consistent = {stats['tc_dropped_consistent']}")
    print(f"  tc_promoted           = {stats['tc_promoted']}")
    print(f"  tc_undefined_dropped  = {stats['tc_undefined_dropped']}")
    print(f"  tc_conflict_left      = {stats['tc_conflict_left']}")
    print(f"  tc_annotation_stripped= {stats['tc_annotation_stripped']}")
    print(f"  empty_temp_stripped   = {stats['empty_temp_stripped']}")
    print(f"  empty_thermal_removed = {stats['empty_thermal_removed']}")
    for c in conflicts:
        print(f"  CONFLICT (left untouched): {c}")
    for a in annotated:
        print(f"  ANNOTATION STRIPPED: {a}")


def main() -> int:
    migrate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
