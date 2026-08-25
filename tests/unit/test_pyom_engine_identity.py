"""One magnetics engine per machine, not per interpreter — ABT #903.

``tests/unit/test_pyom_single_engine.py`` pins the rule that one PROCESS loads
one PyOpenMagnetics build (ABT #897). This is the same failure one level up: a
machine can hold several installed builds, and which one a run gets is decided
by the interpreter, not by anything this repo says.

Measured here, before the fix — same repo, same cwd, same PYTHONPATH, same test
file, one token of difference in the command:

    python3          -> ~/.local/.../PyOpenMagnetics/...so   sha 8d8d2f8e...  24 Aug
    .venv/bin/python -> <repo>/.venv/.../PyOpenMagnetics.so  sha fd0bfe3a...   3 Aug

Two builds three weeks apart. ``test_kirchhoff_fill.py`` passed 21/21 under one
and failed 3/21 under the other, and neither run said a word about which engine
it had loaded — so two people looking at the same file disagreed about whether
it was broken, and both were reporting honestly.

That is worse than #897, because #897 at least ERRORED (``undefined symbol``).
Here nothing errors: you simply get different physics, quietly. The guard is
therefore on the engine's CONTENT, not its path — several install locations are
fine as long as they are the same build. Divergence is what must be loud.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from heaviside import bridge

REPO = Path(__file__).resolve().parents[2]
VENV_ENGINE = (
    REPO / ".venv" / "lib" / f"python3.{sys.version_info.minor}" / "site-packages"
    / "PyOpenMagnetics" / f"PyOpenMagnetics.cpython-3{sys.version_info.minor}-x86_64-linux-gnu.so"
)
ALLOW_FOREIGN = "HEAVISIDE_ALLOW_FOREIGN_PYOM"


def _sha256(path: Path) -> str:
    import hashlib

    with path.open("rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def test_the_gateway_can_say_which_engine_it_loaded():
    """An engine that cannot identify itself cannot be reconciled between two
    people's runs — which is exactly how #903 stayed invisible."""
    try:
        path, digest = bridge.pyom_engine_identity()
    except Exception as exc:  # pragma: no cover - engine not installed here
        pytest.skip(f"PyOM not available: {exc}")
    assert Path(path).is_file()
    assert len(digest) == 64, "expected a full sha256"


def test_the_loaded_engine_is_the_one_the_repo_provisions():
    """The venv is what ``pyproject.toml`` provisions and what prod runs, so it
    is the reference. A different BUILD loaded here means local results say
    nothing about prod — set HEAVISIDE_ALLOW_FOREIGN_PYOM=1 to override
    deliberately, never to silence this."""
    if os.environ.get(ALLOW_FOREIGN) == "1":
        pytest.skip(f"{ALLOW_FOREIGN}=1 — foreign engine accepted deliberately")
    if not VENV_ENGINE.is_file():
        pytest.skip(f"no repo venv engine at {VENV_ENGINE} to compare against")
    try:
        loaded_path, loaded_sha = bridge.pyom_engine_identity()
    except Exception as exc:  # pragma: no cover - engine not installed here
        pytest.skip(f"PyOM not available: {exc}")

    expected = _sha256(VENV_ENGINE)
    assert loaded_sha == expected, (
        "this run loaded a DIFFERENT PyOpenMagnetics build than the one the repo "
        f"provisions, so its results do not describe the engine prod runs (ABT #903).\n"
        f"  loaded : {loaded_path}\n           sha256 {loaded_sha[:16]}\n"
        f"  venv   : {VENV_ENGINE}\n           sha256 {expected[:16]}\n"
        f"Run under {REPO / '.venv/bin/python'}, or reinstall so both match."
    )


def test_every_installed_engine_on_this_machine_is_the_same_build():
    """Several install locations are fine; several BUILDS are not. Catches the
    drift before it becomes two people disagreeing about a test result."""
    probe = (
        "import glob,os,hashlib\n"
        "import PyOpenMagnetics as p\n"
        "so=sorted(glob.glob(os.path.join(os.path.dirname(p.__file__),'PyOpenMagnetics*.so')))[0]\n"
        "print(so+' '+hashlib.sha256(open(so,'rb').read()).hexdigest())\n"
    )
    interpreters = [sys.executable]
    venv_python = REPO / ".venv" / "bin" / "python"
    if venv_python.is_file() and str(venv_python) != sys.executable:
        interpreters.append(str(venv_python))
    for candidate in ("/usr/bin/python3", "python3"):
        if candidate not in interpreters:
            interpreters.append(candidate)

    seen: dict[str, list[str]] = {}
    for interp in interpreters:
        try:
            out = subprocess.run(
                [interp, "-c", probe], capture_output=True, text=True, timeout=180, cwd=REPO
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if out.returncode != 0 or not out.stdout.strip():
            continue
        so, sha = out.stdout.strip().rsplit(" ", 1)
        seen.setdefault(sha, []).append(f"{interp} -> {so}")

    if len(seen) < 2:
        return  # 0 or 1 distinct builds: nothing to disagree about
    detail = "\n".join(
        f"  sha {sha[:16]}\n" + "\n".join(f"    {w}" for w in where) for sha, where in seen.items()
    )
    pytest.fail(
        f"{len(seen)} DIFFERENT PyOpenMagnetics builds are installed on this machine "
        f"(ABT #903). Which one a run gets depends on the interpreter, and neither "
        f"errors — you just get different physics:\n{detail}"
    )
