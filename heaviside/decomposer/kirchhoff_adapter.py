"""Adapter to the Kirchhoff converter design + SPICE library (``PyKirchhoff``).

Kirchhoff (``OpenConverters/Kirchhoff``) is the C++ library, split out of MKF,
that owns the converter ``design -> TAS -> ngspice`` pipeline. This adapter
loads its pybind11 module and exposes the three-step pipeline to Heaviside as a
candidate backend for the ``spice_sim`` seam (see
``docs/kirchhoff_migration_analysis.md``):

    design_topology_tas(topology, spec) -> TAS document (dict)
    tas_to_ngspice(tas, fidelity)       -> runnable ngspice deck (str)

Loading mirrors the PyOpenMagnetics / ``tas_validator`` vendor pattern: prefer an
already-importable module, else ``importlib`` the compiled ``.so`` from the
Kirchhoff build dir (``$KIRCHHOFF_BUILD`` or the sibling ``../Kirchhoff/build``).

Per CLAUDE.md "no fallbacks, throw":
* a missing compiled module raises :class:`KirchhoffUnavailable` with a build
  instruction — never a silent skip;
* a topology with no Python binding raises :class:`KirchhoffTopologyUnsupported`
  rather than degrading (only ``flyback`` and ``boost`` are bound today; the
  other C++ topologies need an ``m.def`` in ``Kirchhoff/src/bindings.cpp``).
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

#: Heaviside topology name -> the PyKirchhoff ``design_*_tas`` function name.
#: Extend as more designers are bound in ``Kirchhoff/src/bindings.cpp``.
_TOPOLOGY_FUNCS: dict[str, str] = {
    "flyback": "design_flyback_tas",
    "boost": "design_boost_tas",
}

_MODULE: Any = None


class KirchhoffUnavailable(RuntimeError):
    """The compiled ``PyKirchhoff`` module could not be loaded."""


class KirchhoffTopologyUnsupported(RuntimeError):
    """The requested topology has no PyKirchhoff Python binding."""


def _candidate_build_dirs() -> list[Path]:
    dirs: list[Path] = []
    env = os.environ.get("KIRCHHOFF_BUILD")
    if env:
        dirs.append(Path(env))
    # Sibling checkout: heaviside/decomposer/ -> repo root -> OpenConverters/Kirchhoff.
    dirs.append(Path(__file__).resolve().parents[3] / "Kirchhoff" / "build")
    return dirs


def _load() -> Any:
    global _MODULE
    if _MODULE is not None:
        return _MODULE
    try:
        import PyKirchhoff as _m  # type: ignore[import-not-found]

        _MODULE = _m
        return _MODULE
    except ModuleNotFoundError:
        pass
    for d in _candidate_build_dirs():
        matches = sorted(d.glob("PyKirchhoff*.so")) if d.is_dir() else []
        if not matches:
            continue
        so = matches[0]
        spec = importlib.util.spec_from_file_location("PyKirchhoff", so)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules["PyKirchhoff"] = module
        spec.loader.exec_module(module)
        _MODULE = module
        return _MODULE
    searched = ", ".join(str(d) for d in _candidate_build_dirs())
    raise KirchhoffUnavailable(
        "PyKirchhoff compiled module not found (searched: "
        f"{searched}). Build it:  cmake -S <KIRCHHOFF> -B <KIRCHHOFF>/build -G Ninja "
        "&& ninja -C <KIRCHHOFF>/build -j3  (or set KIRCHHOFF_BUILD). The backend is "
        "never silently skipped."
    )


def available() -> bool:
    """True iff PyKirchhoff can be loaded — for capability gating/diagnostics."""
    try:
        _load()
        return True
    except KirchhoffUnavailable:
        return False


def available_topologies() -> tuple[str, ...]:
    """Heaviside topology names that PyKirchhoff currently has a binding for."""
    mod = _load()
    return tuple(t for t, fn in _TOPOLOGY_FUNCS.items() if hasattr(mod, fn))


def design_topology_tas(topology: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Design ``topology`` for ``spec`` and return its full TAS document.

    Raises :class:`KirchhoffTopologyUnsupported` if the topology is not bound.
    """
    mod = _load()
    fn = _TOPOLOGY_FUNCS.get(topology)
    if fn is None or not hasattr(mod, fn):
        raise KirchhoffTopologyUnsupported(
            f"Kirchhoff has no Python binding for topology {topology!r}; "
            f"bound: {available_topologies()}. Add an m.def in Kirchhoff/src/bindings.cpp."
        )
    return getattr(mod, fn)(spec)


def tas_to_ngspice(tas: dict[str, Any], fidelity: str | dict[str, Any] = "REQUIREMENTS") -> str:
    """Assemble any TAS document into a runnable ngspice deck.

    ``fidelity`` selects component models — a bare string is taken as the
    ``origin`` (``REQUIREMENTS`` ideal / ``DATASHEET`` real / ``MKF_MODEL``).
    """
    mod = _load()
    fid = {"origin": fidelity} if isinstance(fidelity, str) else dict(fidelity)
    return mod.tas_to_ngspice(tas, fid)
