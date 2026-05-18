#!/usr/bin/env python3
"""Generate ``TypedDict`` modules from MAS / PEAS / SAS / CAS / RAS schemas.

Uses `quicktype` (installed via ``npx``) to convert each JSON schema into a
single Python file under ``heaviside/types/_generated/``. The generated files
are checked in so contributors who don't have Node installed can still work
on Heaviside; ``make types`` regenerates them when schemas change.

Per Heaviside design rules: never edit the generated files by hand. Edit the
schema in the submodule, push upstream, bump the submodule pin, regenerate.

Layout written:

    heaviside/types/_generated/
        topologies/<schema>.py     — one module per MAS topology schema
        mas/<schema>.py            — magnetics components (core, coil, wire, …)
        peas/<schema>.py           — PEAS top-level
        cas/<schema>.py            — capacitors
        ras/<schema>.py            — resistors
        sas/<schema>.py            — semiconductors

We deliberately do **not** generate Pydantic models — TypedDicts only. The
project keeps a hard cap of 8 BaseModel classes (enforced by CI); generated
schemas must never count against that cap.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "heaviside" / "types" / "_generated"

# Source schema roots. Each entry: (submodule path, relative subdir, output bucket).
SOURCES: list[tuple[Path, str, str]] = [
    (ROOT / "MAS" / "schemas" / "inputs" / "topologies", "", "topologies"),
    (ROOT / "MAS" / "schemas" / "magnetic", "", "mas"),
    (ROOT / "PEAS" / "schemas", "", "peas"),
    (ROOT / "CAS" / "schemas", "", "cas"),
    (ROOT / "RAS" / "schemas", "", "ras"),
    (ROOT / "SAS" / "schemas", "", "sas"),
]


def _check_quicktype() -> None:
    if shutil.which("npx") is None:
        print(
            "ERROR: `npx` not found on PATH. Install Node.js ≥ 18 to run `make types`.",
            file=sys.stderr,
        )
        sys.exit(2)


def _gen_one(schema: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{schema.stem.replace('-', '_')}.py"
    # quicktype: python target, TypedDict output, strict optional handling.
    cmd = [
        "npx",
        "--yes",
        "quicktype@23",
        "--src",
        str(schema),
        "--src-lang",
        "schema",
        "--lang",
        "python",
        "--python-version",
        "3.12",
        "--just-types-and-package",
        "--nice-property-names",
        "-o",
        str(out_file),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"FAIL {schema.name}:\n{res.stderr}", file=sys.stderr)
        raise SystemExit(res.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Only verify schema directories exist."
    )
    args = parser.parse_args()

    missing = [src for src, _, _ in SOURCES if not src.exists()]
    if missing:
        print("Missing schema sources (submodules not initialised?):", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        return 1

    if args.check:
        print("All schema source directories present:")
        for src, _, _ in SOURCES:
            print(f"  - {src.relative_to(ROOT)}")
        return 0

    _check_quicktype()

    # Ensure the output tree exists and is empty (except for __init__.py + .gitkeep).
    if OUT.exists():
        for p in OUT.rglob("*.py"):
            if p.name not in ("__init__.py",):
                p.unlink()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "__init__.py").write_text('"""Generated TypedDicts. Do not edit by hand."""\n')

    total = 0
    for src, _, bucket in SOURCES:
        bucket_dir = OUT / bucket
        bucket_dir.mkdir(parents=True, exist_ok=True)
        (bucket_dir / "__init__.py").write_text(f'"""Generated TypedDicts: {bucket}."""\n')
        for schema in sorted(src.glob("*.json")):
            _gen_one(schema, bucket_dir)
            total += 1
            print(f"  {bucket}/{schema.stem}")

    print(f"\nGenerated {total} TypedDict modules under {OUT.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
