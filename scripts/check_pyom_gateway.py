#!/usr/bin/env python3
"""CI guard: all PyOpenMagnetics access goes through the bridge gateway.

``heaviside/bridge.py`` is the single place allowed to import the
PyOpenMagnetics extension (``_import_pyom`` / ``_import_pyom_vendor``
apply and VERIFY the Heaviside settings on every module they hand out).
A direct import anywhere else gets an unconfigured PyOM whose simulator
knobs (saturation, mutual resistance) are still at MKF defaults — wrong
decks, not merely degraded ones.

Allowed:
  * heaviside/bridge.py             — the gateway itself
  * heaviside/_pyom_cache.py        — imports the *package* only to
                                      locate and hash the .so (no API calls)

Everything else under heaviside/ must not:
  * ``import PyOpenMagnetics`` / ``from PyOpenMagnetics import ...``
  * load a PyOpenMagnetics .so via ``importlib.util.spec_from_file_location``

Exit code 1 with file:line diagnostics on any violation.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / "heaviside"

ALLOWED = {
    PACKAGE / "bridge.py",
    PACKAGE / "_pyom_cache.py",
}


def violations_in(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "PyOpenMagnetics":
                    found.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "PyOpenMagnetics":
                found.append((node.lineno, f"from {node.module} import ..."))
        elif isinstance(node, ast.Call):
            fn = node.func
            name = (
                fn.attr
                if isinstance(fn, ast.Attribute)
                else fn.id
                if isinstance(fn, ast.Name)
                else ""
            )
            if name == "spec_from_file_location":
                args = [a for a in node.args if isinstance(a, ast.Constant)]
                if any("PyOpenMagnetics" in str(a.value) for a in args):
                    found.append((node.lineno, "spec_from_file_location(PyOpenMagnetics .so)"))
    return found


def main() -> int:
    bad = 0
    for path in sorted(PACKAGE.rglob("*.py")):
        if path in ALLOWED:
            continue
        for lineno, what in violations_in(path):
            print(f"{path.relative_to(REPO)}:{lineno}: {what} — route through heaviside.bridge._import_pyom[_vendor]")
            bad += 1
    if bad:
        print(f"\n{bad} direct PyOpenMagnetics access(es) outside the gateway.")
        return 1
    print("PyOM gateway check OK — no direct PyOpenMagnetics access outside heaviside/bridge.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
