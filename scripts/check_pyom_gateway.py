#!/usr/bin/env python3
"""CI guard: all PyOpenMagnetics access goes through the bridge gateway, and
there is exactly ONE PyOpenMagnetics build in the process.

``heaviside/bridge.py`` is the single place allowed to import the
PyOpenMagnetics extension (``_import_pyom`` applies and VERIFIES the Heaviside
settings on every module it hands out). A direct import anywhere else gets an
unconfigured PyOM whose simulator knobs (saturation, mutual resistance) are
still at MKF defaults — wrong decks, not merely degraded ones.

Allowed to import the package:
  * heaviside/bridge.py             — the gateway itself
  * heaviside/_pyom_cache.py        — imports the *package* only to
                                      locate and hash the .so (no API calls)

Everything else under heaviside/ must not ``import PyOpenMagnetics`` /
``from PyOpenMagnetics import ...``.

Loading a PyOpenMagnetics ``.so`` by PATH is forbidden EVERYWHERE, the gateway
included. Two builds cannot coexist: they share a global symbol namespace, so
whichever loads first wins and the other raises ``undefined symbol:
Kirchhoff::api::…``. That is not hypothetical — a second, vendored build was
loaded this way, which made ``calculate_saturation_current`` unreachable on the
installed one and killed converter design for two months behind a message that
blamed the physics (ABT #897). One build, one gateway.

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


def violations_in(path: Path, *, may_import: bool) -> list[tuple[int, str]]:
    """Collect gateway violations. ``may_import`` exempts the two files allowed
    to import the package; loading a .so by path is checked regardless."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if may_import and not isinstance(node, ast.Call):
            continue
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
        for lineno, what in violations_in(path, may_import=path in ALLOWED):
            hint = (
                "one build only — do NOT load a second PyOpenMagnetics .so (ABT #897)"
                if "spec_from_file_location" in what
                else "route through heaviside.bridge._import_pyom"
            )
            print(f"{path.relative_to(REPO)}:{lineno}: {what} — {hint}")
            bad += 1
    if bad:
        print(f"\n{bad} PyOpenMagnetics gateway violation(s).")
        return 1
    print(
        "PyOM gateway check OK — one build, no direct PyOpenMagnetics access "
        "outside heaviside/bridge.py."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
