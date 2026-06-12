#!/usr/bin/env python3
"""One-off TAS mosfet structural migration (2026-06-12), user-approved.

Two mechanical, loss-free transforms on ``TAS/data/mosfets.ndjson``
(envelope ``{"semiconductor": {"mosfet": {...}}}``):

1. **Misplaced ``datasheetInfo``** — 2,426 rows carry ``datasheetInfo`` at
   the mosfet body root (a sibling of ``manufacturerInfo``) where the SAS
   schema requires it *under* ``manufacturerInfo``. The fix moves it there.
   If ``manufacturerInfo`` already has its own ``datasheetInfo``, the two
   are deep-merged only when no key holds conflicting values; on any value
   conflict the row is NOT merged — by default the whole migration aborts
   with ``MigrationError`` (file untouched), or with ``--leave-conflicts``
   the conflicting rows are left byte-identical and reported in full so a
   human can decide. The script never picks a side.

2. **Scalar ``gateThresholdVoltage``** — SAS specifies PEAS
   ``dimensionWithTolerance``; ~328 rows store a bare number. Wrapped as
   ``{"nominal": x}`` (same pattern as the approved magnetics dcResistance
   migration). ``null`` / missing values are left as-is — restoring them is
   datasheet-enrichment work, not a mechanical migration.

Untouched rows stay byte-identical; the file is replaced atomically only
on success. Anything structurally unexpected (non-dict ``datasheetInfo``,
missing envelope or ``manufacturerInfo``, a string/bool/list threshold
voltage, ...) raises ``MigrationError`` and leaves the file untouched.

Run from the Heaviside repo root:
    .venv-web/bin/python scripts/migrate_mosfet_structure.py [--leave-conflicts]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "TAS" / "data" / "mosfets.ndjson"


class MigrationError(RuntimeError):
    pass


def _body(row: dict, where: str) -> dict:
    sem = row.get("semiconductor")
    if not (isinstance(sem, dict) and isinstance(sem.get("mosfet"), dict)):
        raise MigrationError(f"{where}: row lacks the semiconductor.mosfet envelope")
    return sem["mosfet"]


def _find_conflicts(root_dsi: dict, mi_dsi: dict, path: str = "datasheetInfo") -> list[str]:
    """Paths where the two datasheetInfo trees hold *different* values."""
    conflicts: list[str] = []
    for key in sorted(set(root_dsi) & set(mi_dsi)):
        a, b = root_dsi[key], mi_dsi[key]
        p = f"{path}.{key}"
        if isinstance(a, dict) and isinstance(b, dict):
            conflicts.extend(_find_conflicts(a, b, p))
        elif a != b:
            conflicts.append(
                f"{p}: root={json.dumps(a, ensure_ascii=False)[:80]} "
                f"vs manufacturerInfo={json.dumps(b, ensure_ascii=False)[:80]}"
            )
    return conflicts


def _merge_into(root_dsi: dict, mi_dsi: dict) -> None:
    """Merge root_dsi into mi_dsi. Only call after _find_conflicts() == []."""
    for key, value in root_dsi.items():
        if key not in mi_dsi:
            mi_dsi[key] = value
        elif isinstance(value, dict) and isinstance(mi_dsi[key], dict):
            _merge_into(value, mi_dsi[key])
        # else: identical values (guaranteed conflict-free) — keep mi's copy.


def migrate(path: Path, leave_conflicts: bool) -> int:
    out_lines: list[str] = []
    stats = {"rows": 0, "dsi_moved": 0, "dsi_merged": 0, "gth_wrapped": 0, "conflict_rows": 0}
    conflict_reports: list[str] = []

    for lineno, line in enumerate(path.open(encoding="utf-8"), 1):
        if not line.strip():
            raise MigrationError(f"mosfets:{lineno}: blank line in NDJSON")
        stats["rows"] += 1
        where = f"mosfets:{lineno}"
        row = json.loads(line)  # corrupt line -> loud JSONDecodeError
        body = _body(row, where)
        mi = body.get("manufacturerInfo")
        if not isinstance(mi, dict):
            raise MigrationError(f"{where}: manufacturerInfo missing or not an object")
        changed = False

        # 1. Move/merge stray body-root datasheetInfo under manufacturerInfo.
        if "datasheetInfo" in body:
            root_dsi = body["datasheetInfo"]
            if not isinstance(root_dsi, dict):
                raise MigrationError(
                    f"{where}: body-root datasheetInfo is {type(root_dsi).__name__}, not an object"
                )
            if "datasheetInfo" not in mi:
                mi["datasheetInfo"] = body.pop("datasheetInfo")
                stats["dsi_moved"] += 1
                changed = True
            else:
                mi_dsi = mi["datasheetInfo"]
                if not isinstance(mi_dsi, dict):
                    raise MigrationError(
                        f"{where}: manufacturerInfo.datasheetInfo is "
                        f"{type(mi_dsi).__name__}, not an object"
                    )
                conflicts = _find_conflicts(root_dsi, mi_dsi)
                if conflicts:
                    ref = mi.get("reference", "?")
                    report = f"{where} (reference={ref}): {len(conflicts)} conflicting value(s):\n" + \
                        "\n".join(f"    {c}" for c in conflicts)
                    if not leave_conflicts:
                        raise MigrationError(
                            "merge conflict — refusing to pick a side "
                            "(re-run with --leave-conflicts to skip such rows):\n" + report
                        )
                    conflict_reports.append(report)
                    stats["conflict_rows"] += 1
                    out_lines.append(line.rstrip("\n"))  # whole row left byte-identical
                    continue
                _merge_into(root_dsi, mi_dsi)
                del body["datasheetInfo"]
                stats["dsi_merged"] += 1
                changed = True

        # 2. Wrap scalar gateThresholdVoltage as dimensionWithTolerance.
        elec = mi.get("datasheetInfo", {}).get("electrical")
        if isinstance(elec, dict):
            gth = elec.get("gateThresholdVoltage", "?")
            if isinstance(gth, bool):
                raise MigrationError(f"{where}: gateThresholdVoltage is a bool")
            if isinstance(gth, (int, float)):
                elec["gateThresholdVoltage"] = {"nominal": gth}
                stats["gth_wrapped"] += 1
                changed = True
            elif not (gth == "?" or gth is None or isinstance(gth, dict)):
                raise MigrationError(
                    f"{where}: gateThresholdVoltage has unexpected type {type(gth).__name__}"
                )

        if changed:
            out_lines.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        else:
            out_lines.append(line.rstrip("\n"))  # untouched rows stay byte-identical

    tmp = path.with_suffix(".ndjson.migrating")
    tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    tmp.replace(path)

    print(
        f"mosfets     rows={stats['rows']}  dsi_moved={stats['dsi_moved']}  "
        f"dsi_merged={stats['dsi_merged']}  gth_wrapped={stats['gth_wrapped']}  "
        f"conflict_rows_left_untouched={stats['conflict_rows']}"
    )
    if conflict_reports:
        print(
            f"\nWARNING: {stats['conflict_rows']} row(s) had datasheetInfo in BOTH places "
            "with conflicting values; they were left byte-identical and still fail "
            "schema validation. A human must decide which side is correct:"
        )
        for report in conflict_reports:
            print(f"  {report}")
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--leave-conflicts",
        action="store_true",
        help="On a datasheetInfo merge conflict, leave the row byte-identical and "
        "report it instead of aborting the whole migration.",
    )
    args = parser.parse_args()
    return migrate(PATH, leave_conflicts=args.leave_conflicts)


if __name__ == "__main__":
    sys.exit(main())
