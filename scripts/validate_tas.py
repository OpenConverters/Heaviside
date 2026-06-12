#!/usr/bin/env python3
"""Validate the whole TAS DB two ways, per category.

For every row of every schema-backed TAS category:

1. **JSON-schema validation** through the librarian's strict validator
   (``heaviside.librarian.tas.validate_component`` — the same gate the
   write path uses), and
2. **quicktype-class round-trip** through the generated classes
   (``heaviside.types``): ``Cls.from_dict(inner)`` must accept the row.

Run after any schema-submodule bump or ``gen_types`` change. Exit code 0
only when both checks pass for every parseable row; corrupt NDJSON lines
are reported and counted, never skipped silently.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from heaviside.catalogue._reader import CatalogueReadError, iter_envelopes
from heaviside.librarian.tas import SCHEMA_MAP, ValidationError, validate_component

#: category → (NDJSON filename, generated-class name in heaviside.types)
CATEGORIES: dict[str, tuple[str, str]] = {
    "mosfets": ("mosfets.ndjson", "Mosfet"),
    "diodes": ("diodes.ndjson", "Diode"),
    "igbts": ("igbts.ndjson", "Igbt"),
    "capacitors": ("capacitors.ndjson", "Capacitor"),
    "resistors": ("resistors.ndjson", "Resistor"),
    "magnetics": ("magnetics.ndjson", "Magnetic"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tas-dir", type=Path, default=ROOT / "TAS" / "data")
    parser.add_argument(
        "--limit", type=int, default=None, help="Only check the first N rows per category."
    )
    parser.add_argument(
        "--show-failures", type=int, default=3, help="Failure examples to print per category."
    )
    args = parser.parse_args()

    import heaviside.types as types_facade

    grand_bad = 0
    for category, (fname, cls_name) in CATEGORIES.items():
        path = args.tas_dir / fname
        if not path.exists():
            print(f"{category:11s} — MISSING file {path}")
            grand_bad += 1
            continue

        cls = getattr(types_facade, cls_name)
        _, unwrap = SCHEMA_MAP[category]

        total = schema_bad = class_bad = corrupt = 0
        examples: list[str] = []
        rows = iter_envelopes(path)
        while True:
            try:
                _lineno, env = next(rows)
            except StopIteration:
                break
            except CatalogueReadError as exc:
                corrupt += 1
                examples.append(f"corrupt line: {exc}")
                continue
            total += 1
            if args.limit and total > args.limit:
                total -= 1
                break

            try:
                validate_component(category, env)
            except ValidationError as exc:
                schema_bad += 1
                if len(examples) < args.show_failures:
                    first = exc.errors[0] if getattr(exc, "errors", None) else ("?", str(exc))
                    examples.append(f"schema: mpn={exc.mpn} {first[0]}: {first[1][:120]}")

            try:
                cls.from_dict(unwrap(env))
            except Exception:
                class_bad += 1
                if len(examples) < args.show_failures:
                    tb = traceback.format_exc().strip().splitlines()[-1]
                    examples.append(f"class:  {tb[:160]}")

        ok = total - max(schema_bad, 0)
        print(
            f"{category:11s} — rows={total}  schema_invalid={schema_bad}  "
            f"class_reject={class_bad}  corrupt_lines={corrupt}"
        )
        for e in examples:
            print(f"             ↳ {e}")
        grand_bad += schema_bad + class_bad + corrupt
        _ = ok

    if grand_bad:
        print(f"\nFAIL: {grand_bad} total problems")
        return 1
    print("\nOK: every row passes JSON-schema validation and class round-trip")
    return 0


if __name__ == "__main__":
    sys.exit(main())
