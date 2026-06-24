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

#: Heaviside topology name -> Kirchhoff base name. The PyKirchhoff designer is
#: ``design_<base>_tas`` (e.g. phase_shifted_full_bridge -> psfb -> design_psfb_tas).
#: Covers every topology bound in ``Kirchhoff/src/bindings.cpp``; HS filter
#: magnetics (common/differential-mode chokes, current transformer) have no
#: Kirchhoff converter designer and are intentionally absent.
_HS_TO_KIRCHHOFF: dict[str, str] = {
    "flyback": "flyback",
    "boost": "boost",
    "buck": "buck",
    "single_switch_forward": "forward",
    "two_switch_forward": "two_switch_forward",
    "sepic": "sepic",
    "cuk": "cuk",
    "zeta": "zeta",
    "push_pull": "push_pull",
    "phase_shifted_full_bridge": "psfb",
    "phase_shifted_half_bridge": "pshb",
    "asymmetric_half_bridge": "ahb",
    "active_clamp_forward": "acf",
    "four_switch_buck_boost": "fsbb",
    "llc": "llc",
    "cllc": "cllc",
    "clllc": "clllc",
    "series_resonant": "src",
    "dual_active_bridge": "dab",
    "isolated_buck": "isolated_buck",
    "isolated_buck_boost": "isolated_buck_boost",
    "weinberg": "weinberg",
    "power_factor_correction": "pfc",
    "vienna": "vienna",
}


def _design_fn(base: str) -> str:
    return f"design_{base}_tas"


def kirchhoff_base(topology: str) -> str | None:
    """Kirchhoff base name for a Heaviside topology (e.g. ``series_resonant`` ->
    ``src``), or ``None`` if Kirchhoff has no designer for it. Handy for callers
    that key off Kirchhoff's own artifacts (e.g. reference fixtures)."""
    return _HS_TO_KIRCHHOFF.get(topology)

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
    return tuple(t for t, base in _HS_TO_KIRCHHOFF.items() if hasattr(mod, _design_fn(base)))


def design_topology_tas(topology: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Design ``topology`` for ``spec`` and return its full TAS document.

    Raises :class:`KirchhoffTopologyUnsupported` if the topology is not bound.
    """
    mod = _load()
    base = _HS_TO_KIRCHHOFF.get(topology)
    if base is None or not hasattr(mod, _design_fn(base)):
        raise KirchhoffTopologyUnsupported(
            f"Kirchhoff has no Python binding for topology {topology!r}; "
            f"bound: {available_topologies()}. Add an m.def in Kirchhoff/src/bindings.cpp."
        )
    return getattr(mod, _design_fn(base))(spec)


def tas_to_ngspice(tas: dict[str, Any], fidelity: str | dict[str, Any] = "REQUIREMENTS") -> str:
    """Assemble any TAS document into a runnable ngspice deck.

    ``fidelity`` selects component models — a bare string is taken as the
    ``origin`` (``REQUIREMENTS`` ideal / ``DATASHEET`` real / ``MKF_MODEL``).
    """
    mod = _load()
    fid = {"origin": fidelity} if isinstance(fidelity, str) else dict(fidelity)
    return mod.tas_to_ngspice(tas, fid)
