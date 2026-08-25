"""One PyOpenMagnetics build in the process — ABT #897.

Heaviside used to carry two: the installed wheel behind ``_import_pyom`` and a
vendored ``.so`` loaded by path behind ``_import_pyom_vendor``. Two builds
cannot coexist. They share a global symbol namespace, so whichever loads FIRST
wins and the other raises ``undefined symbol: Kirchhoff::api::…`` — measured on
prod, in both orders:

    installed first -> installed OK, vendor then fails to load
    vendor first    -> vendor OK, installed then fails to load

The magnetic adviser loaded the vendor build first, so the saturation gate's
``calculate_saturation_current`` on the installed build could never run. Every
candidate at every frequency came back "unrankable (no isat/loss)" and the
sweep raised ``FrequencySweepError: no feasible (magnetic, fsw) … at 1.2x isat
margin``, telling the user to widen the band or fetch parts. Converter design
was dead from 2026-06-25 to 2026-08-24 for a build reason wearing a physics
message.

These tests hold the invariant that made it possible: exactly one build, and
nobody loads a PyOpenMagnetics ``.so`` by path — the gateway included.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from heaviside import bridge

REPO = Path(__file__).resolve().parents[2]
GUARD = REPO / "scripts" / "check_pyom_gateway.py"


def test_the_second_gateway_is_gone():
    """``_import_pyom_vendor`` was the hazard's entry point. It must not come
    back — not as a function, and not as the path constant it loaded."""
    assert not hasattr(bridge, "_import_pyom_vendor"), (
        "a second PyOM gateway is back; two builds cannot coexist (ABT #897)"
    )
    assert not hasattr(bridge, "_PYOM_VENDOR_SO")


def test_no_source_file_loads_a_pyom_so_by_path():
    """The durable guard: importing the package is fine, loading a second build
    by path is not, anywhere under ``heaviside/``."""
    hits = [
        f"{p.relative_to(REPO)}:{i}"
        for p in sorted((REPO / "heaviside").rglob("*.py"))
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "spec_from_file_location" in line and "PyOpenMagnetics" in line
    ]
    assert not hits, f"a PyOpenMagnetics .so is loaded by path at {hits} (ABT #897)"


def test_the_ci_guard_passes_on_this_tree():
    proc = subprocess.run(
        [sys.executable, str(GUARD)], capture_output=True, text=True, cwd=REPO
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_ci_guard_catches_a_second_build_even_in_the_gateway(tmp_path):
    """The guard used to skip ``bridge.py`` wholesale, so the very file that
    loaded the second build was the one file exempt from the check. Loading a
    ``.so`` by path must now be caught THERE too."""
    offending = REPO / "heaviside" / "_abt897_guard_probe.py"
    offending.write_text(
        textwrap.dedent(
            """
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "PyOpenMagnetics", "/tmp/PyOpenMagnetics.so"
            )
            """
        ),
        encoding="utf-8",
    )
    try:
        proc = subprocess.run(
            [sys.executable, str(GUARD)], capture_output=True, text=True, cwd=REPO
        )
        assert proc.returncode == 1, "the guard did not flag a second build"
        assert "spec_from_file_location" in proc.stdout
    finally:
        offending.unlink()


def test_the_gateway_hands_out_one_and_the_same_module():
    """Design, stamp and the saturation gate must all get the SAME module —
    that identity is what the two-gateway split broke."""
    try:
        first = bridge._import_pyom()
    except Exception as exc:  # pragma: no cover - engine not installed here
        pytest.skip(f"PyOM not available: {exc}")
    assert bridge._import_pyom() is first


def test_the_one_build_carries_both_halves_of_the_design_path():
    """The split existed because each build was thought to carry only half the
    API. The engine we ship must carry both halves: what the adviser needs and
    what the saturation gate needs."""
    try:
        pyom = bridge._import_pyom()
    except Exception as exc:  # pragma: no cover - engine not installed here
        pytest.skip(f"PyOM not available: {exc}")
    missing = [
        name
        for name in (
            "calculate_advised_magnetics_with_filters",
            "calculate_saturation_current",
        )
        if not hasattr(pyom, name)
    ]
    assert not missing, (
        f"the installed PyOM lacks {missing}; a converter design cannot both "
        "advise and gate on one engine (ABT #897)"
    )
