#!/usr/bin/env python3
"""Fail CI if `heaviside/` exceeds the hard cap of 8 ``pydantic.BaseModel`` classes.

Per Heaviside design rule: schemas (MAS/PEAS/SAS/CAS/RAS) are the type system.
Pydantic is reserved for the very few user-facing boundaries (DesignSpec,
config, etc.). If you find yourself wanting to add a 9th BaseModel, fix the
schemas instead.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

CAP = 8
ROOT = Path(__file__).resolve().parents[1] / "heaviside"
EXCLUDE_DIRS = {"types"}  # generated TypedDicts only


def _is_base_model(base: ast.expr) -> bool:
    if isinstance(base, ast.Name):
        return base.id == "BaseModel"
    if isinstance(base, ast.Attribute):
        return base.attr == "BaseModel"
    return False


def find_basemodels(root: Path) -> list[tuple[Path, str, int]]:
    hits: list[tuple[Path, str, int]] = []
    for path in root.rglob("*.py"):
        if any(part in EXCLUDE_DIRS for part in path.relative_to(root).parts):
            continue
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError as exc:
            print(f"syntax error in {path}: {exc}", file=sys.stderr)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(_is_base_model(b) for b in node.bases):
                hits.append((path.relative_to(root.parent), node.name, node.lineno))
    return hits


def main() -> int:
    hits = find_basemodels(ROOT)
    print(f"Found {len(hits)} pydantic.BaseModel subclasses in heaviside/ (cap = {CAP}):")
    for path, name, line in hits:
        print(f"  {path}:{line}: class {name}(BaseModel)")
    if len(hits) > CAP:
        print(
            f"\nFAIL: BaseModel count {len(hits)} exceeds cap {CAP}.\n"
            "Use MAS/PEAS/SAS/CAS/RAS-derived TypedDicts instead "
            "(see heaviside/types/_generated/).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
