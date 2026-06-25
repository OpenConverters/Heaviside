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


class KirchhoffSpecError(ValueError):
    """A Heaviside converter spec could not be translated to a Kirchhoff spec."""


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


def hs_spec_to_kirchhoff(hs_spec: dict[str, Any]) -> dict[str, Any]:
    """Translate a Heaviside converter spec into a Kirchhoff design spec.

    HS specs (what ``decomposer.inputs_mapper`` consumes) carry a top-level
    ``inputVoltage`` (a dimensionWithTolerance ``{nominal,minimum,maximum}``),
    a scalar ``efficiency``, and ``operatingPoints[]`` with per-op scalar
    ``inputVoltage``, ``switchingFrequency`` and parallel ``outputVoltages`` /
    ``outputCurrents`` lists. Kirchhoff wants ``designRequirements`` (efficiency,
    inputVoltage, switchingFrequency, outputs[].voltage) + ``operatingPoints``
    whose outputs carry ``power`` (= V·I).

    Fail-loud: every missing/malformed required field raises
    :class:`KirchhoffSpecError` — values are read, never fabricated.
    """
    iv = hs_spec.get("inputVoltage")
    if not isinstance(iv, dict) or not any(k in iv for k in ("nominal", "minimum", "maximum")):
        raise KirchhoffSpecError("spec.inputVoltage must be a {nominal/minimum/maximum} dict")
    eff = hs_spec.get("efficiency")
    if not isinstance(eff, (int, float)):
        raise KirchhoffSpecError("spec.efficiency is required (numeric)")
    ops = hs_spec.get("operatingPoints")
    if not isinstance(ops, list) or not ops:
        raise KirchhoffSpecError("spec.operatingPoints must be a non-empty list")
    op0 = ops[0]
    fsw = op0.get("switchingFrequency")
    if not isinstance(fsw, (int, float)):
        raise KirchhoffSpecError("operatingPoints[0].switchingFrequency is required (numeric)")
    v0 = op0.get("outputVoltages")
    i0 = op0.get("outputCurrents")
    if not isinstance(v0, list) or not v0:
        raise KirchhoffSpecError("operatingPoints[0].outputVoltages must be a non-empty list")
    if not isinstance(i0, list) or len(i0) != len(v0):
        raise KirchhoffSpecError("operatingPoints[0].outputCurrents must match outputVoltages length")

    dr_iv = {k: float(iv[k]) for k in ("minimum", "nominal", "maximum") if isinstance(iv.get(k), (int, float))}
    outputs = [{"name": f"out{i}", "voltage": {"nominal": float(v)}} for i, v in enumerate(v0)]
    iv_default = dr_iv.get("nominal") or dr_iv.get("minimum") or dr_iv.get("maximum")

    k_ops: list[dict[str, Any]] = []
    for n, op in enumerate(ops):
        vs = op.get("outputVoltages", v0)
        cs = op.get("outputCurrents", i0)
        if not isinstance(vs, list) or not isinstance(cs, list) or len(vs) != len(cs) or not vs:
            raise KirchhoffSpecError(f"operatingPoints[{n}] output lists missing/mismatched")
        vin = op.get("inputVoltage", iv_default)
        if not isinstance(vin, (int, float)):
            raise KirchhoffSpecError(f"operatingPoints[{n}].inputVoltage unresolved (no scalar, no spec default)")
        k_ops.append(
            {"inputVoltage": float(vin), "outputs": [{"power": float(v) * float(c)} for v, c in zip(vs, cs)]}
        )

    dr: dict[str, Any] = {
        "efficiency": float(eff),
        "inputVoltage": dr_iv,
        "switchingFrequency": {"nominal": float(fsw)},
        "outputs": outputs,
    }
    # della-Pollock "design around the magnetic": when HS has already designed the
    # magnetic (magnetics-first), pass its inductance so Kirchhoff sizes the rest of
    # the stage around it instead of computing its own L (Kirchhoff honours this as
    # of abt #30). Keeps the design consistent with the stamped MKF_MODEL magnetic.
    desired_l = hs_spec.get("desiredInductance") or hs_spec.get("desiredMagnetizingInductance")
    if isinstance(desired_l, (int, float)) and desired_l > 0:
        dr["magnetizingInductance"] = {"nominal": float(desired_l)}

    return {"designRequirements": dr, "operatingPoints": k_ops}


def design_from_hs_spec(topology: str, hs_spec: dict[str, Any]) -> dict[str, Any]:
    """Translate a Heaviside converter spec (see :func:`hs_spec_to_kirchhoff`)
    and design the topology's TAS — the HS-pipeline entry point to Kirchhoff."""
    return design_topology_tas(topology, hs_spec_to_kirchhoff(hs_spec))


_COMPONENT_FAMILIES = ("semiconductor", "magnetic", "capacitor", "resistor", "analog", "controller")


def kirchhoff_component_requirements(tas: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract Kirchhoff's per-component design requirements — the *BOM to fill*.

    Kirchhoff emits each TAS component as a SEED: an empty family slot
    (``data.semiconductor.mosfet={}``, ``data.magnetic={}``, ``data.capacitor``…)
    plus the requirements that slot must satisfy in
    ``data.inputs.designRequirements`` — a MOSFET's ``maximumOnResistance`` /
    ``ratedContinuousDrainCurrent``, a diode's ``maximumForwardVoltage``, a
    capacitor's ``capacitance`` / ``maximumEsr`` / ``ratedVoltage``, a magnetic's
    ``magnetizingInductance``. This is the "Kirchhoff returns a list of design
    requirements as a BOM" contract: HS fills each by selecting a real part
    (librarian, for semis/passives) or designing the magnetic (MKF, della-Pollock
    magnetic-first) and stamping it back into the component's family slot — at
    which point Kirchhoff's per-component fidelity inference promotes it to
    DATASHEET / MKF_MODEL.

    Returns one dict per component: ``{stage, name, family, kind, requirements}``
    (``kind`` is mosfet/diode for semiconductors, ``None`` for passives/magnetics).
    Fail-loud: a malformed TAS (no ``topology.stages``) raises ``KirchhoffSpecError``.
    """
    topo = tas.get("topology")
    if not isinstance(topo, dict) or not isinstance(topo.get("stages"), list):
        raise KirchhoffSpecError("TAS has no topology.stages[] to read component requirements from")
    out: list[dict[str, Any]] = []
    for st in topo["stages"]:
        for comp in st.get("circuit", {}).get("components", []):
            data = comp.get("data", {})
            family = next((f for f in _COMPONENT_FAMILIES if f in data), None)
            if family is None:
                continue
            slot = data.get(family)
            kind = next(iter(slot), None) if isinstance(slot, dict) and slot else None
            out.append(
                {
                    "stage": st.get("role") or st.get("name"),
                    "name": comp.get("name"),
                    "family": family,
                    "kind": kind,
                    "requirements": data.get("inputs", {}).get("designRequirements", {}),
                }
            )
    return out


def tas_to_ngspice(tas: dict[str, Any], fidelity: str | dict[str, Any] = "REQUIREMENTS") -> str:
    """Assemble any TAS document into a runnable ngspice deck.

    ``fidelity`` selects component models — a bare string is taken as the
    ``origin`` (``REQUIREMENTS`` ideal / ``DATASHEET`` real / ``MKF_MODEL``).
    """
    mod = _load()
    fid = {"origin": fidelity} if isinstance(fidelity, str) else dict(fidelity)
    return mod.tas_to_ngspice(tas, fid)


_REGULATE: Any = None


def _load_regulate() -> Any:
    """Load Kirchhoff's ``scripts/regulate.py`` (the closed-loop regulator, abt #28).

    It lives beside the build dir (``<KIRCHHOFF>/scripts/regulate.py``) and does
    ``import PyKirchhoff`` — which resolves because :func:`_load` registers the
    compiled module in ``sys.modules`` first."""
    global _REGULATE
    if _REGULATE is not None:
        return _REGULATE
    _load()  # ensure `import PyKirchhoff` resolves inside regulate.py
    for d in _candidate_build_dirs():
        cand = d.parent / "scripts" / "regulate.py"
        if cand.is_file():
            spec = importlib.util.spec_from_file_location("kirchhoff_regulate", cand)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _REGULATE = mod
            return mod
    raise KirchhoffUnavailable(
        "Kirchhoff scripts/regulate.py not found (searched <build>/../scripts). It "
        "provides the closed-loop REGULATED operating point the realism gate needs."
    )


def simulate_regulated(
    tas: dict[str, Any],
    target_vout: float,
    topology: str,
    *,
    fidelity: str | dict[str, Any] = "DATASHEET",
    tol: float = 0.01,
) -> dict[str, Any]:
    """Closed-loop REGULATED operating point (Kirchhoff ``regulate.simulate_regulated``).

    Bisects the topology's control variable (duty / phase / frequency) until the
    simulated Vout reaches ``target_vout``, then returns the regulated operating
    point: ``{converged, regulated, control, value, vout, pin, pout, efficiency,
    ...}``. This is what feeds the realism gate (a regulated point with a real
    efficiency — not the open-loop fixed-duty artifact). ``topology`` is the HS
    name (mapped to Kirchhoff's base). Raises
    :class:`KirchhoffTopologyUnsupported` if Kirchhoff has no control mapping."""
    mod = _load_regulate()
    base = kirchhoff_base(topology) or topology
    fid = {"origin": fidelity} if isinstance(fidelity, str) else dict(fidelity)
    try:
        return mod.simulate_regulated(tas, float(target_vout), base, fidelity=fid, tol=tol)
    except ValueError as exc:  # "no control-variable mapping for topology '<x>'"
        raise KirchhoffTopologyUnsupported(str(exc)) from exc
