"""Decomposer→PyOpenMagnetics bridge.

Closes the loop between the ``heaviside.decomposer`` (TAS topology
emission) and PyOpenMagnetics' magnetic-design engine
(``PyOpenMagnetics.design_magnetics_from_converter``).

Pipeline:

    spec (Python dict)
        │
        ├─► heaviside.decomposer.decompose_from_spec(topology, spec, …)
        │       → (mkf_netlist, tas_topology)
        │
        └─► heaviside.bridge.design_magnetics(topology, spec, …)
                → list[MagneticDesign]
                       │
                       └─► heaviside.bridge.attach_magnetics_to_tas(
                               tas_topology, designs[:1])
                               → TAS with each magnetic component's
                                 ``data`` URL replaced by an inline
                                 ``mas`` field containing the resolved
                                 MAS magnetic JSON.

This module is deliberately thin. It does **no** magnetic computation
itself — every core/winding/loss number comes from PyOpenMagnetics. Per
the repository's "no fallbacks" rule it raises ``BridgeError`` loudly
on any engine error or unexpected response shape.

Scope (Phase 2):
  * Single-magnetic topologies (buck, boost, flyback, single-switch
    forward — even the augmented one) bind automatically: the one
    returned MAS magnetic is attached to the one TAS magnetic
    component.
  * Multi-magnetic topologies (cuk/sepic/zeta with L1+L2, ACF/2SF/LLC
    with T1+L_out0, isobuck/isobb with T1+C_pri inductor, …) require
    an explicit ``mapping={"T1": "transformer", "L_out0": "output_choke"}``
    argument that maps TAS component names to PyOM magnetic names.
    The mapping is currently the caller's responsibility — PyOM does
    not yet expose a stable per-magnetic identifier across topologies.
    Calling ``attach_magnetics_to_tas`` without a mapping on a
    multi-magnetic TAS raises ``BridgeError`` with the count mismatch.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from heaviside._pyom_cache import cached_call
from heaviside.topologies.registry import TopologyEntry, get

ExtraComponentsMode = Literal["IDEAL", "REAL"]


class BridgeError(RuntimeError):
    """Raised when the decomposer→PyOM bridge fails."""


@dataclass(frozen=True, slots=True)
class MagneticDesign:
    """One scored magnetic design returned by PyOpenMagnetics.

    Attributes
    ----------
    scoring : float
        Composite design score (higher is better, per PyOM's weighting).
    mas : dict
        Full MAS-shaped JSON: ``{"inputs": …, "magnetic": …, "outputs": …}``.
    elapsed_s : float
        Wall-clock time taken by the PyOM call that produced this batch
        (same value attached to every design in a batch — for telemetry).
    """

    scoring: float
    mas: dict[str, Any]
    elapsed_s: float

    @property
    def magnetic(self) -> dict[str, Any]:
        """The MAS ``magnetic`` sub-document (``core`` + ``coil`` + meta)."""
        return self.mas["magnetic"]

    @property
    def core_shape_name(self) -> str:
        """Canonical name of the chosen core shape (e.g. ``"PQ 20/16"``)."""
        shape = self.magnetic["core"]["functionalDescription"]["shape"]
        return shape["name"] if isinstance(shape, dict) else str(shape)

    @property
    def core_material_name(self) -> str:
        """Canonical name of the chosen core material (e.g. ``"3C95"``)."""
        mat = self.magnetic["core"]["functionalDescription"]["material"]
        return mat["name"] if isinstance(mat, dict) else str(mat)

    @property
    def windings(self) -> list[dict[str, Any]]:
        """List of winding ``functionalDescription`` records (turns, wire, …)."""
        coil = self.magnetic.get("coil", {})
        return list(coil.get("functionalDescription") or [])

    @property
    def winding_names(self) -> tuple[str, ...]:
        """Names of every winding, in coil declaration order."""
        return tuple(w.get("name", f"winding_{i}") for i, w in enumerate(self.windings))


# -----------------------------------------------------------------------------
# PyOpenMagnetics dispatch
# -----------------------------------------------------------------------------


# Realism-relevant PyOpenMagnetics global settings. Applied (and
# verified) by :func:`_apply_pyom_settings` on every module the gateway
# hands out. These flip OFF-by-default knobs that Heaviside relies on
# for accurate sim + selection:
#
#   * ``circuitSimulatorIncludeSaturation``: model the inductor's BH
#     saturation in the ngspice deck (default OFF — Heaviside's realism
#     gate already checks isat margin, but the sim itself should also
#     reflect saturation for the operating-point sweep to be accurate).
#   * ``circuitSimulatorIncludeMutualResistance``: model winding-to-
#     winding resistive coupling (transformers, coupled inductors).
#
# NOTE a ``coreAdviserSaturationMargin`` knob used to be listed here:
# no MKF build ever exposed that key, so ``set_settings`` silently
# dropped it and the documented Maniktala ≥1.2 saturation derating was
# never in effect. Saturation headroom must instead be enforced by the
# realism gate's ``inductor_isat_margin`` check / a real MKF
# MagneticFilterSaturation knob (parked MKF C++ task). Do not re-add
# keys here without the round-trip verification below proving they
# exist.
_HEAVISIDE_PYOM_SETTINGS: dict[str, Any] = {
    "circuitSimulatorIncludeSaturation": True,
    "circuitSimulatorIncludeMutualResistance": True,
}


def _apply_pyom_settings(ext: Any) -> Any:
    """Apply and verify :data:`_HEAVISIDE_PYOM_SETTINGS` on ``ext``.

    Raises :class:`BridgeError` if the build does not expose one of the
    keys or a value does not round-trip — a silently-dropped setting is
    a wrong simulation, not a degraded one.
    """
    current = ext.get_settings()
    missing = sorted(set(_HEAVISIDE_PYOM_SETTINGS) - set(current))
    if missing:
        raise BridgeError(
            f"PyOpenMagnetics build lacks settings keys {missing}; "
            f"_HEAVISIDE_PYOM_SETTINGS no longer matches the MKF build. "
            f"Fix the build or the settings dict — do not drop knobs silently."
        )
    ext.set_settings(dict(_HEAVISIDE_PYOM_SETTINGS))
    applied = ext.get_settings()
    wrong = {
        k: applied.get(k) for k, v in _HEAVISIDE_PYOM_SETTINGS.items() if applied.get(k) != v
    }
    if wrong:
        raise BridgeError(
            f"PyOpenMagnetics did not accept settings {wrong} "
            f"(expected {_HEAVISIDE_PYOM_SETTINGS})."
        )
    return ext


_pyom_settings_applied: bool = False
_pyom_vendor_module: Any = None
_pyom_fast_param_supported: bool | None = None  # None = not yet probed


def _import_pyom() -> Any:
    """Gateway to the installed PyOpenMagnetics extension.

    Every production access to PyOM goes through this function or
    :func:`_import_pyom_vendor` (enforced by
    ``scripts/check_pyom_gateway.py`` in CI), so every caller sees a
    consistently-configured PyOM.
    """
    global _pyom_settings_applied

    from PyOpenMagnetics import PyOpenMagnetics as _ext

    if not _pyom_settings_applied:
        _apply_pyom_settings(_ext)
        _pyom_settings_applied = True
    return _ext


_PYOM_VENDOR_SO = (
    Path(__file__).resolve().parent.parent
    / "vendor"
    / "PyOpenMagnetics"
    / "build"
    / "cp312-cp312-linux_x86_64"
    / "PyOpenMagnetics.cpython-312-x86_64-linux-gnu.so"
)


def _import_pyom_vendor() -> Any:
    """Gateway to the vendored PyOpenMagnetics build, if present.

    Prefers the vendor ``.so`` (needed by the decomposer for
    ``bridge_simulation_mode``); falls back to the installed extension.
    Either way the module comes back with Heaviside settings applied —
    the vendor ``.so`` is a distinct native module whose settings state
    is independent of the installed one.
    """
    global _pyom_vendor_module

    if _pyom_vendor_module is not None:
        return _pyom_vendor_module

    if _PYOM_VENDOR_SO.exists():
        import importlib.util

        # The module name must match the .so's PyInit_PyOpenMagnetics
        # export; the module is deliberately NOT registered in
        # sys.modules so it never shadows the installed package.
        spec = importlib.util.spec_from_file_location(
            "PyOpenMagnetics", str(_PYOM_VENDOR_SO)
        )
        if spec is None or spec.loader is None:
            raise BridgeError(f"cannot build an import spec for {_PYOM_VENDOR_SO}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _pyom_vendor_module = _apply_pyom_settings(mod)
        return _pyom_vendor_module

    _pyom_vendor_module = _import_pyom()
    return _pyom_vendor_module


def _advise_magnetics_fast(
    pyom: Any,
    inputs_raw: Mapping[str, Any],
    label: str,
    max_results: int,
    core_mode: str,
) -> list[MagneticDesign]:
    """Topology-AGNOSTIC tail shared by the converter-spec path and the
    MAS-inputs path: call ``calculate_advised_magnetics_fast`` on a ready
    MAS ``Inputs`` envelope and parse the scored candidates into
    ``MagneticDesign``. ``label`` only colours error messages.

    This is the part that designs the magnetic GEOMETRY from a magnetic
    REQUIREMENT — it never touches a topology model, so it works for every
    converter (incl. sepic/cuk/zeta/fsbb, which MKF's topology-specific
    ``process_converter`` cannot design). The envelope must already carry
    ``designRequirements`` + ``operatingPoints`` (a complete excitation per
    winding) — Kirchhoff's per-topology magnetic seed (ABT #34) provides it.
    """
    t0 = time.monotonic()
    result = pyom.calculate_advised_magnetics_fast(
        dict(inputs_raw),
        int(max_results),
        str(core_mode),
    )
    elapsed = time.monotonic() - t0

    if not isinstance(result, dict):
        raise BridgeError(
            f"calculate_advised_magnetics_fast returned {type(result).__name__}, expected dict."
        )
    if result.get("error"):
        raise BridgeError(
            f"calculate_advised_magnetics_fast rejected inputs ({label}): {result['error']}"
        )
    data = result.get("data")
    if isinstance(data, str):
        # PyOM tunnels an MKF-internal exception through `data` as a STRING (not a list),
        # e.g. "Exception: [GAP_INVALID_DIMENSIONS] Gap Area is not set". Surface it verbatim
        # so the real MKF cause is diagnosable instead of an opaque "no data list".
        raise BridgeError(
            f"calculate_advised_magnetics_fast raised inside MKF ({label}): {data}"
        )
    if not isinstance(data, list):
        raise BridgeError(
            f"calculate_advised_magnetics_fast response has no 'data' "
            f"list (keys: {sorted(result)}, data={type(data).__name__})"
        )
    if not data:
        raise BridgeError(
            f"calculate_advised_magnetics_fast returned zero candidates "
            f"for {label}. Loosen constraints or check inputs."
        )

    designs: list[MagneticDesign] = []
    for item in data:
        if not isinstance(item, Mapping):
            raise BridgeError(
                f"calculate_advised_magnetics_fast entry is "
                f"{type(item).__name__}, expected dict (mas + scoring)."
            )
        mas = item.get("mas")
        scoring = item.get("scoring")
        if not isinstance(mas, Mapping) or not isinstance(scoring, (int, float)):
            raise BridgeError(
                f"calculate_advised_magnetics_fast entry missing mas/scoring: "
                f"{sorted(item) if isinstance(item, Mapping) else type(item).__name__}"
            )
        designs.append(
            MagneticDesign(
                scoring=float(scoring),
                mas=dict(mas),
                elapsed_s=float(elapsed) / max(1, len(data)),
            )
        )
    # PyOM returns ascending losses; lower scoring = better magnetic.
    return designs


def design_magnetic_from_mas_inputs(
    mas_inputs: Mapping[str, Any],
    *,
    max_results: int = 5,
    core_mode: str = "standard cores",
) -> list[MagneticDesign]:
    """Design a magnetic GEOMETRY directly from a MAS ``Inputs`` envelope —
    the topology-AGNOSTIC entry point (ABT #34).

    Unlike :func:`design_magnetics_fast`, this skips
    ``process_converter(topology, spec)`` entirely and hands the supplied
    envelope straight to ``calculate_advised_magnetics_fast``. The envelope is
    expected to come from a Kirchhoff per-component magnetic seed
    (``data.magnetic`` component's ``data.inputs``), which already carries the
    complete requirement: ``designRequirements`` (magnetizingInductance +
    turnsRatios) and ``operatingPoints`` with one winding excitation per
    winding. This is how the cutover keeps MKF "magnetics-geometry-only" and
    designs sepic/cuk/zeta/fsbb that MKF's topology path cannot.

    Raises
    ------
    BridgeError
        If the envelope is malformed (missing designRequirements /
        operatingPoints), or PyOM rejects/returns no candidates.
    """
    if not isinstance(mas_inputs, Mapping):
        raise BridgeError(
            f"design_magnetic_from_mas_inputs: expected a MAS Inputs dict, got "
            f"{type(mas_inputs).__name__}."
        )
    missing = [k for k in ("designRequirements", "operatingPoints") if k not in mas_inputs]
    if missing:
        raise BridgeError(
            f"design_magnetic_from_mas_inputs: envelope missing {missing} "
            f"(keys={sorted(mas_inputs)}). A complete Kirchhoff magnetic seed has "
            f"both — an inductance-only seed cannot be designed. See ABT #34."
        )
    ops = mas_inputs.get("operatingPoints")
    if not isinstance(ops, list) or not ops:
        raise BridgeError(
            "design_magnetic_from_mas_inputs: operatingPoints is empty; the seed "
            "must carry at least one operating point with excitationsPerWinding."
        )
    if not ops[0].get("excitationsPerWinding"):
        raise BridgeError(
            "design_magnetic_from_mas_inputs: operatingPoints[0] has no "
            "excitationsPerWinding — the magnetic cannot be sized without a winding "
            "excitation (ABT #34 requires one per winding)."
        )
    pyom = _import_pyom()
    return _advise_magnetics_fast(pyom, mas_inputs, "mas-inputs seed", max_results, core_mode)


def _resolve_switching_frequency(spec: Mapping[str, Any]) -> float | None:
    """Resolve a scalar switching frequency from an HS converter spec — top-level
    ``switchingFrequency`` first, then ``designRequirements.switchingFrequency``. Accepts a bare
    number or a ``{nominal/maximum/minimum}`` dimensionWithTolerance (preferred order nominal →
    max → min). Returns None if absent (caller decides whether that is fatal)."""
    sf: Any = spec.get("switchingFrequency")
    if sf is None:
        dr = spec.get("designRequirements")
        if isinstance(dr, Mapping):
            sf = dr.get("switchingFrequency")
    if isinstance(sf, (int, float)):
        return float(sf)
    if isinstance(sf, Mapping):
        for k in ("nominal", "maximum", "minimum"):
            v = sf.get(k)
            if isinstance(v, (int, float)):
                return float(v)
    return None


def _spec_with_per_op_fsw(spec: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``spec`` with ``switchingFrequency`` stamped onto every operating point
    that lacks one (resolved via :func:`_resolve_switching_frequency`). ``design_from_hs_spec``
    requires per-op fsw; the sweep seam stamps the swept value, but the topology+spec entries
    (stage-2 pick, agents, REST) carry it only at the spec level. Untouched if already per-op or
    if no spec-level fsw exists (Kirchhoff then surfaces the missing-fsw error, no silent default)."""
    ops = spec.get("operatingPoints")
    if not isinstance(ops, list) or not ops:
        return spec
    fsw = _resolve_switching_frequency(spec)
    if fsw is None:
        return spec
    spec["operatingPoints"] = [
        ({**op, "switchingFrequency": op.get("switchingFrequency", fsw)} if isinstance(op, Mapping) else op)
        for op in ops
    ]
    return spec


def design_magnetics_fast(
    topology: str | TopologyEntry,
    converter_spec: Mapping[str, Any],
    *,
    max_results: int = 5,
    core_mode: str = "standard cores",
) -> list[MagneticDesign]:
    """Fast-mode magnetic candidates for Pareto exploration.

    Bypasses CoilAdviser + full MagneticSimulator. Uses area-product
    filtering, analytical gap/turns, fast_wind(), and Steinmetz core
    losses — physically valid but approximate. ~12× faster than
    :func:`design_magnetics` (~12 s for 5 candidates on a buck spec vs
    ~120 s for the same pool through the slow path).

    Use this for design-space exploration / Pareto fronts where an
    LLM (or heuristic) picks one of several candidates. Use
    :func:`design_magnetics` when you want a single fully-simulated,
    coil-optimised design.

    della-Pollock cutover (abt #48): the MKF ``process_converter`` converter model is RETIRED.
    The magnetic REQUIREMENT (MAS ``Inputs`` seed: designRequirements + operatingPoints) now comes
    from KIRCHHOFF's per-topology design; MKF designs only the geometry from that seed. Same return
    contract — candidates sorted by *ascending* total losses (lower scoring = better). This is the
    topology+spec entry; :func:`design_magnetic_from_mas_inputs` is the seed-in-hand entry, and
    :func:`design_magnetics_at_fsw` is the frequency-swept seam.
    """
    from heaviside.decomposer import kirchhoff_adapter as _ka

    entry = topology if isinstance(topology, TopologyEntry) else get(topology)
    # design_from_hs_spec requires switchingFrequency ON each operating point (the sweep seam
    # design_magnetics_at_fsw stamps the swept fsw there). This topology+spec entry has no swept
    # fsw, so resolve the spec's own switchingFrequency and stamp it onto every op that lacks one.
    spec_f = _spec_with_per_op_fsw(dict(converter_spec))
    k_tas = _ka.design_from_hs_spec(entry.name, spec_f)
    _name, seed = _main_magnetic_seed_from_ktas(entry, k_tas)
    return design_magnetic_from_mas_inputs(seed, max_results=max_results, core_mode=core_mode)


# The realism gate's inductor saturation rule (Isat >= 1.2·Ipeak) plus headroom so the discrete
# core the fast advise picks clears 1.2× AND has room to deliver full output without saturating.
# Mirrors full_design._GATE_ISAT_MARGIN — keep the two in sync (the sweep sizes the main magnetic
# to this margin so the della-Pollock realize, which pins it verbatim, passes the gate).
_GATE_ISAT_MARGIN = 1.3


def _seed_with_isat_margin(seed: Mapping[str, Any], margin: float) -> dict[str, Any]:
    """A copy of the magnetic seed with every winding's CURRENT PEAK scaled by ``margin`` so the
    advised core is sized for ``margin``×Ipeak — the saturation headroom the realism gate requires.
    Only the peak (the saturation driver) is scaled; rms/voltage (loss/flux) stay real so the core
    is not over-sized for loss."""
    import copy

    s = copy.deepcopy(dict(seed))
    for op in s.get("operatingPoints", []):
        if not isinstance(op, Mapping):
            continue
        for exc in op.get("excitationsPerWinding", []):
            proc = ((exc or {}).get("current") or {}).get("processed")
            if isinstance(proc, dict) and isinstance(proc.get("peak"), (int, float)):
                proc["peak"] = abs(float(proc["peak"])) * margin
    return s


def _main_magnetic_seed_from_ktas(
    entry: TopologyEntry, k_tas: Mapping[str, Any]
) -> tuple[str, Mapping[str, Any]]:
    """Pick the MAIN magnetic's MAS ``Inputs`` seed from a Kirchhoff TAS.

    The converter is built AROUND the main magnetic — the inductor for
    buck/boost/etc., the transformer for flyback/forward/push_pull/bridge/
    resonant. The other magnetics (output inductor, resonant inductors) are
    SECONDARY and are designed separately at realize (they do not enter the
    frequency-sweep loss objective; user decision 2026-06-27). The main is
    identified, in priority order:

    1. The registry ``magnetic_binding`` key whose value is ``None`` (the
       authoritative "main" role), matched by name against the KH TAS magnetics.
       The main names (``T1`` / ``L1``) coincide between the registry and
       Kirchhoff; only the secondary names drift (``L_out0`` vs ``Lout``).
    2. Structural fallback when no name matches (Kirchhoff name drift): the
       multi-winding transformer if exactly one is present, else the sole
       single-winding inductor.

    Raises :class:`BridgeError` when the seed is missing/incomplete or the main
    is ambiguous — never silently designs the wrong magnetic (no-fallback rule).
    """
    mags = _tas_magnetic_components(k_tas)
    if not mags:
        raise BridgeError(
            f"_main_magnetic_seed_from_ktas: Kirchhoff TAS for {entry.name!r} "
            f"contains no magnetic components."
        )
    seeds: list[tuple[str, Any]] = []
    for c in mags:
        data = c.get("data")
        seeds.append(
            (c.get("name") or "", data.get("inputs") if isinstance(data, Mapping) else None)
        )

    # 1. registry main role (the single None-valued binding key)
    main_name = next((k for k, v in (entry.magnetic_binding or {}).items() if v is None), None)
    chosen: tuple[str, Any] | None = None
    if main_name is not None:
        chosen = next(((n, s) for n, s in seeds if n == main_name), None)

    # 2. structural fallback (name-independent: transformer-if-present else inductor)
    if chosen is None:
        def _n_windings(seed: Any) -> int:
            if not isinstance(seed, Mapping):
                return 0
            tr = ((seed.get("designRequirements") or {}).get("turnsRatios")) or []
            return len(tr) + 1  # turnsRatios carries (windings - 1) entries
        transformers = [(n, s) for n, s in seeds if _n_windings(s) >= 2]
        if len(transformers) == 1:
            chosen = transformers[0]
        elif not transformers and len(seeds) == 1:
            chosen = seeds[0]
        else:
            raise BridgeError(
                f"_main_magnetic_seed_from_ktas: cannot identify the main magnetic "
                f"for {entry.name!r}: registry main {main_name!r} is not among the "
                f"Kirchhoff TAS magnetics {[n for n, _ in seeds]}, and the structural "
                f"fallback is ambiguous (transformers={[n for n, _ in transformers]}). "
                f"Fix registry.magnetic_binding or the Kirchhoff component names."
            )

    name, seed = chosen
    if not isinstance(seed, Mapping):
        raise BridgeError(
            f"_main_magnetic_seed_from_ktas: main magnetic {name!r} for "
            f"{entry.name!r} carries no MAS Inputs seed on data.inputs "
            f"(got {type(seed).__name__}); Kirchhoff must emit a complete seed."
        )
    return name, seed


def design_magnetics_at_fsw(
    topology: str | TopologyEntry,
    converter_spec: Mapping[str, Any],
    fsw_hz: float,
    *,
    max_results: int = 5,
    core_mode: str = "standard cores",
    fast: bool = True,
) -> list[MagneticDesign]:
    """Design the MAIN magnetic at a specific switching frequency, letting
    Kirchhoff re-derive the inductance for that frequency.

    This is the single seam the frequency sweep (master-plan stage C-hs)
    turns. It stamps ``fsw_hz`` onto every operating point of a copy of the
    BASE converter spec, asks **Kirchhoff** to design the topology at that
    frequency (``kirchhoff_adapter.design_from_hs_spec``), extracts the MAIN
    magnetic's MAS ``Inputs`` seed (:func:`_main_magnetic_seed_from_ktas`), and
    designs its geometry via :func:`design_magnetic_from_mas_inputs`. Heaviside
    never computes L itself — Kirchhoff sizes L for the operating point at this
    ``fsw_hz`` (no ``desiredInductance`` in the BASE spec ⇒ Kirchhoff derives
    it), keeping the sweep house-rule-clean (magnetics geometry stays in MKF;
    topology/converter math is Kirchhoff's).

    **Cutover (abt #48):** this seam previously routed through MKF's
    ``process_converter`` converter model (``design_magnetics_fast`` /
    ``design_magnetics``). Those converter models are being retired — the
    designer is built around Kirchhoff's per-topology magnetic seed. The
    converter is built around the MAIN magnetic; the secondary magnetics
    (output inductor, resonant inductors) are designed separately at realize and
    do NOT enter the sweep loss objective (fsw* minimises main-magnetic + switching
    loss only). The return type is unchanged (``list[MagneticDesign]`` for the
    main magnetic), so the sweep / picker downstream are untouched.

    The BASE-schema guard below stays load-bearing: an injected
    ``desiredInductance`` would make Kirchhoff size the rest of the stage around
    a pre-set L instead of deriving it per-fsw, so it must never reach here.

    Raises
    ------
    BridgeError
        If ``fsw_hz`` is not positive, the spec has no operating points, the
        spec still carries a ``desiredInductance``/``desiredMagnetizingInductance``
        (a BASE-schema violation, surfaced loudly), or ``fast=False`` (the
        full-sim-from-a-MAS-seed path is not wired — surfaced rather than
        falling back to the retired MKF converter model).
    """
    if not isinstance(fsw_hz, (int, float)) or fsw_hz <= 0:
        raise BridgeError(f"design_magnetics_at_fsw: fsw_hz must be > 0, got {fsw_hz!r}")
    for k in ("desiredInductance", "desiredMagnetizingInductance"):
        if k in converter_spec:
            raise BridgeError(
                f"design_magnetics_at_fsw: BASE-schema spec must not carry {k!r} "
                f"(MKF derives L from the operating point + currentRippleRatio; an "
                f"injected seed is ignored and signals a designer-path bug). "
                f"Build the spec via stages.converter_spec_build."
            )
    ops = converter_spec.get("operatingPoints")
    if not isinstance(ops, list) or not ops:
        raise BridgeError(
            "design_magnetics_at_fsw: spec has no operatingPoints to stamp fsw onto."
        )
    if not fast:
        raise BridgeError(
            "design_magnetics_at_fsw: the Kirchhoff seam designs the main magnetic "
            "via the fast core-advise path; fast=False (full-sim from a MAS seed) "
            "is not wired. The frequency sweep uses fast=True. Surfaced rather than "
            "falling back to the retired MKF process_converter converter model (abt #48)."
        )
    spec_f = dict(converter_spec)
    spec_f["operatingPoints"] = [
        {**op, "switchingFrequency": float(fsw_hz)} if isinstance(op, Mapping) else op
        for op in ops
    ]
    # Cutover (abt #48): design the MAIN magnetic from Kirchhoff's per-topology
    # seed instead of MKF's process_converter. Kirchhoff sizes L for this fsw;
    # design_magnetic_from_mas_inputs designs the geometry (calculate_advised_
    # magnetics_fast) from the seed — topology-agnostic, MKF stays geometry-only.
    from heaviside.decomposer import kirchhoff_adapter as _ka

    entry = topology if isinstance(topology, TopologyEntry) else get(topology)
    k_tas = _ka.design_from_hs_spec(entry.name, spec_f)
    _name, seed = _main_magnetic_seed_from_ktas(entry, k_tas)
    # Size the main magnetic for the realism gate's saturation HEADROOM (Isat >= margin·Ipeak):
    # scale the seed's winding peaks by the gate margin before advising, so the swept magnetic the
    # designer later PINS already clears the gate (the della-Pollock realize stamps it verbatim and
    # does not re-add headroom). Mirrors full_design._design_ktas_magnetics' fresh-design scaling.
    return design_magnetic_from_mas_inputs(
        _seed_with_isat_margin(seed, _GATE_ISAT_MARGIN), max_results=max_results, core_mode=core_mode
    )


# -----------------------------------------------------------------------------
# TAS annotation
# -----------------------------------------------------------------------------


def _tas_magnetic_components(tas: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return every TAS component declared as a magnetic.

    Three recognition paths, in priority order:

    1. ``data`` is an inline PEAS document carrying a ``magnetic`` key
       (post-attach shape — what the bridge emits).
    2. ``category == "magnetic"`` — the parallel SPICE→TAS reader
       convention. Not in the TAS schema but emitted by
       ``TAS/scripts/spice_to_tas.py``; round-trip fixtures rely on it.
    3. ``data`` is a URI string pointing at ``magnetics.ndjson`` — the
       stencil's pre-attach placeholder convention.
    """
    out: list[dict[str, Any]] = []
    for stage in tas.get("topology", {}).get("stages", []):
        for c in stage.get("circuit", {}).get("components", []):
            data = c.get("data")
            if isinstance(data, dict) and "magnetic" in data:
                out.append(c)
                continue
            if c.get("category") == "magnetic":
                out.append(c)
                continue
            if isinstance(data, str) and "magnetics.ndjson" in data:
                out.append(c)
    return out


def attach_magnetics_to_tas(
    tas: dict[str, Any],
    designs: Sequence[MagneticDesign],
    *,
    mapping: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Annotate the TAS magnetic components with resolved MAS designs.

    For each TAS magnetic component, the resolved MAS ``magnetic``
    sub-document is attached as ``component["mas"]`` and the
    placeholder ``data`` URL is removed. The original TAS dict is
    mutated **and** returned (for chaining).

    Single-magnetic topology
        If TAS has exactly one magnetic component and ``designs`` has
        at least one entry, ``designs[0]`` is attached. ``mapping`` is
        ignored.

    Multi-magnetic topology
        ``mapping`` must be provided as ``{tas_component_name:
        design_index}``. Every TAS magnetic name must appear as a key,
        and every value must be a valid index into ``designs``.

    Raises
    ------
    BridgeError
        On count mismatch, missing mapping entries, or out-of-range
        indices.
    """
    if not designs:
        raise BridgeError("attach_magnetics_to_tas: 'designs' is empty.")

    magnetics = _tas_magnetic_components(tas)
    if not magnetics:
        raise BridgeError(
            "attach_magnetics_to_tas: TAS topology contains zero magnetic "
            "components. Nothing to attach."
        )

    if len(magnetics) == 1 and mapping is None:
        _attach_one(magnetics[0], designs[0])
        return tas

    if mapping is None:
        names = [c.get("name", "?") for c in magnetics]
        raise BridgeError(
            f"attach_magnetics_to_tas: TAS has {len(magnetics)} magnetic "
            f"components ({names}) but no 'mapping' was provided. Multi-"
            f"magnetic topologies require an explicit "
            f"{{tas_name: design_index}} mapping."
        )

    tas_names = {c.get("name", f"_{i}"): c for i, c in enumerate(magnetics)}
    missing = set(tas_names) - set(mapping)
    extra = set(mapping) - set(tas_names)
    if missing or extra:
        raise BridgeError(
            f"attach_magnetics_to_tas: mapping mismatch. "
            f"missing keys (TAS magnetics not mapped): {sorted(missing)}; "
            f"extra keys (not in TAS): {sorted(extra)}."
        )

    for tas_name, design_idx in mapping.items():
        if not 0 <= design_idx < len(designs):
            raise BridgeError(
                f"attach_magnetics_to_tas: mapping[{tas_name!r}]={design_idx} "
                f"is out of range for {len(designs)} designs."
            )
        _attach_one(tas_names[tas_name], designs[design_idx])

    return tas


def _attach_one(component: dict[str, Any], design: MagneticDesign) -> None:
    """Emit a PEAS magnetic document into ``component['data']`` (mutating).

    ``design.mas`` is already a full MAS envelope
    (``{inputs: {designRequirements, operatingPoints}, magnetic: {...}, outputs: {...}}``)
    which is a valid PEAS document for the magnetic discriminator
    branch. We stamp it verbatim onto ``component['data']`` and stash
    the PyOM design score as a TAS-extra ``scoring`` sibling so
    callers retain access to the ranking metadata.

    Legacy ``category`` / ``mas`` / ``mas_scoring`` siblings are no
    longer written — PEAS-compliant emission lives in ``data``.
    """
    component.pop("data", None)
    component.pop("category", None)
    component.pop("mas", None)
    component.pop("mas_scoring", None)
    component["data"] = design.mas
    component["scoring"] = design.scoring


def _binding_role(name: str, binding: Mapping[str, str | None]) -> str | None | _Unbound:
    """Look up the binding role for a TAS magnetic ``name``.

    Output chokes of a multi-output forward-family converter are named
    ``L_out0``, ``L_out1``, … by the stencil but the registry only
    declares the canonical single-rail ``L_out0`` entry. Treat any
    ``L_out{i}`` (i ≥ 1) as sharing rail 0's binding role so the static
    registry stays small while still covering N rails. Returns the
    sentinel :data:`_UNBOUND` when ``name`` is genuinely unmapped.
    """
    if name in binding:
        return binding[name]
    if re.fullmatch(r"L_out\d+", name or "") and "L_out0" in binding:
        return binding["L_out0"]
    return _UNBOUND


class _Unbound:
    """Sentinel type for a TAS magnetic with no binding entry."""


_UNBOUND = _Unbound()


__all__ = [
    "BridgeError",
    "ExtraComponentsMode",
    "MagneticDesign",
    "attach_magnetics_to_tas",
]


# =============================================================================
# Phase B — extra components (multi-magnetic / clamp-capacitor topologies)
# =============================================================================
#
# After Phase A (``design_magnetics`` → main transformer/inductor),
# MKF can describe the *remaining* components a topology requires
# (output inductors, clamp/snubber capacitors, resonant tanks, etc.)
# as pre-filled ``MAS::Inputs`` / ``CAS::Inputs`` envelopes via
# ``PyOpenMagnetics.get_extra_components_inputs``.
#
# Of MKF's 24 topologies, 21 are wired into ``dispatch_extra_components``
# (vendor/PyOpenMagnetics/src/converter.cpp:1056). The three magnetic-
# only topologies (current_transformer, common_mode_choke,
# differential_mode_choke) and vienna intentionally return no extras.


def _harvest_authoritative_inductance(mas: Mapping[str, Any]) -> float:
    """Return the inductance MKF *actually achieved* with the picked
    magnetic (henries).

    Source of truth: ``mas.outputs[0].inductance.magnetizingInductance.magnetizingInductance.nominal``
    — the simulation-derived inductance of the wound + gapped core.
    This is what stress / sim / analyst should use as L, because it
    matches the magnetic that's actually in the TAS.

    Why not ``designRequirements.magnetizingInductance``: that field
    is the *target / constraint*, not the achieved value. For
    flyback / iso-buck-boost MKF sets ``nominal`` to the user's
    desiredInductance (matches reality only if a candidate that hits
    exactly that L is picked). For buck/boost MKF sets only
    ``minimum`` — the smallest L that keeps ripple under spec —
    which is typically 4–20× *below* the L the picked magnetic
    actually has. Stress derivations using the minimum compute
    massively inflated ripple/ipeak and falsely fail the
    inductor_isat_margin gate.

    Falls back to ``designRequirements`` only when ``outputs`` is
    missing or unusable (e.g. fast-mode candidates that skip the
    simulator). Throws if neither source yields a positive number
    — per CLAUDE.md no-silent-fallback rule.
    """
    # 1. Primary source: outputs[*].inductance.magnetizingInductance.magnetizingInductance.nominal
    outputs = mas.get("outputs")
    if isinstance(outputs, list):
        for op in outputs:
            if not isinstance(op, Mapping):
                continue
            ind = op.get("inductance")
            if not isinstance(ind, Mapping):
                continue
            mi_outer = ind.get("magnetizingInductance")
            if not isinstance(mi_outer, Mapping):
                continue
            mi_inner = mi_outer.get("magnetizingInductance")
            if not isinstance(mi_inner, Mapping):
                continue
            nominal = mi_inner.get("nominal")
            if isinstance(nominal, (int, float)) and nominal > 0:
                return float(nominal)

    # 2. Fallback for fast-mode / older PyOM responses without a full
    #    outputs envelope: read the design-requirements constraint.
    #    Order: nominal → minimum → maximum. Buck/boost only set
    #    minimum and its value is a *floor*, not the L actually used,
    #    so this branch is best-effort.
    mi = mas.get("inputs", {}).get("designRequirements", {}).get("magnetizingInductance", {})
    if isinstance(mi, Mapping):
        for key in ("nominal", "minimum", "maximum"):
            value = mi.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return float(value)

    raise BridgeError(
        "MKF returned a magnetic without a usable inductance — neither "
        "outputs[*].inductance.magnetizingInductance.magnetizingInductance.nominal "
        "nor designRequirements.magnetizingInductance has a positive scalar. "
        f"outputs sample: {mas.get('outputs')!r}, "
        f"designRequirements.magnetizingInductance: {mi!r}"
    )


# -----------------------------------------------------------------------------
# Candidate post-filter (saturation-margin aware)
# -----------------------------------------------------------------------------


def _ipeak_worst_buck(spec: Mapping[str, Any]) -> float | None:
    """Worst-case peak current in a buck inductor across the operating range.

    Mirrors the formula used by ``heaviside.pipeline.extract`` so the
    bridge's post-filter and the realism gate's enrichment agree on what
    counts as "passable". Returns ``None`` if the spec is incomplete
    enough that we cannot compute a number (caller falls back to PyMKF's
    top scorer in that case).
    """
    try:
        vin = spec.get("inputVoltage") or {}
        vmin = vin.get("minimum") if isinstance(vin, Mapping) else None
        vmax = vin.get("maximum") if isinstance(vin, Mapping) else None
        if not (isinstance(vmin, (int, float)) and isinstance(vmax, (int, float))):
            return None
        ops = spec.get("operatingPoints")
        if not (isinstance(ops, list) and ops):
            return None
        op = ops[0]
        if not isinstance(op, Mapping):
            return None
        vouts = op.get("outputVoltages")
        iouts = op.get("outputCurrents")
        fsw = op.get("switchingFrequency")
        if not (isinstance(vouts, list) and vouts and isinstance(vouts[0], (int, float))):
            return None
        if not (isinstance(iouts, list) and iouts and isinstance(iouts[0], (int, float))):
            return None
        if not isinstance(fsw, (int, float)) or fsw <= 0:
            return None
        L = spec.get("desiredInductance")
        if not isinstance(L, (int, float)) or L <= 0:
            return None
        vout = float(vouts[0])
        iout = float(iouts[0])
        if vout >= float(vmin):
            return None  # buck cannot step up; let realism flag it
        d_min = vout / float(vmax)
        L_worst = 0.8 * float(L)
        ripple_worst = vout * (1.0 - d_min) / (L_worst * float(fsw))
        return iout + ripple_worst / 2.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None


# Per-topology worst-case-Ipeak computers. None entries mean "post-filter
# is a no-op for that topology"; the bridge keeps PyMKF's top scorer
# and the realism gate (if it has its own per-topology extractor) will
# report any margin failure honestly.
#
# These delegate to heaviside.pipeline.stress so the same closed-form
# formulas drive both selection (here) and realism-gate stress fields
# (extract.py / analyst.py).


def _ipeak_worst_from_stress(topology: str, spec: Mapping[str, Any]) -> float | None:
    """Generic Ipeak_worst extractor that defers to the per-topology
    stress deriver registered in ``heaviside.pipeline.stress``.

    Returns ``None`` when the topology has no registered deriver OR
    the spec is incomplete; caller (post-filter) treats None as "skip
    the filter" and falls back to PyMKF's top scorer.
    """
    from heaviside.pipeline.stress import StressDerivationError, derive_stresses

    try:
        s = derive_stresses(topology, spec)
    except StressDerivationError:
        return None
    if s is None or s.id_stress is None or s.id_stress <= 0:
        return None
    return float(s.id_stress)


def _ipeak_worst_magnetizing(
    spec: Mapping[str, Any], *, bidirectional: bool
) -> float | None:
    """Peak MAGNETIZING current of a transformer core — the quantity that
    saturates it (the secondary balances the load component, so the load current
    does NOT set the flux).

    The applied volt-seconds during the ON time ramp the magnetizing current::

        ΔIm = Vin_max · D_max / (Lm · fsw)

    * Unidirectional cores (forward-class: single/two-switch, active-clamp) reset
      to zero each cycle, so the peak magnetizing current is the full ΔIm.
    * Bidirectional cores (bridge, push-pull) swing symmetrically ±ΔIm/2, so the
      peak is ΔIm/2.

    ``Vin_max · D_max`` is the conservative worst-case volt-seconds (over-states
    the flux → tightens the gate → never passes a saturating core). ``Lm`` is the
    candidate's magnetizing inductance, which the caller stamps as
    ``desiredInductance`` (``_isat_margin_inputs`` sets it to the harvested L).
    Returns ``None`` when the spec lacks Vin / duty / Lm / fsw.

    NOTE (ABT #16): This is an analytical formula in Heaviside, which violates the
    "all magnetics math lives in MKF" rule. It is kept as a fallback for the fast
    path where MKF provides no winding-current waveform. PyOM exposes
    ``calculate_induced_current(excitation, Lm)`` (wraps
    ``Inputs::calculate_magnetizing_current``) but that requires a voltage waveform
    we don't have on the fast path. Remove this function once ABT #16 lands a
    ``calculate_peak_winding_current(magnetic, operatingPoint)`` Python binding."""
    try:
        iv = spec.get("inputVoltage") or {}
        vmax = (iv.get("maximum") or iv.get("nominal")) if isinstance(iv, Mapping) else None
        d_max = spec.get("maximumDutyCycle")
        lm = spec.get("desiredInductance")
        ops = spec.get("operatingPoints")
        op = ops[0] if isinstance(ops, list) and ops and isinstance(ops[0], Mapping) else None
        fsw = op.get("switchingFrequency") if isinstance(op, Mapping) else None
        if not all(isinstance(x, (int, float)) and x > 0 for x in (vmax, d_max, lm, fsw)):
            return None
        delta_im = float(vmax) * float(d_max) / (float(lm) * float(fsw))
        return delta_im / 2.0 if bidirectional else delta_im
    except Exception:
        return None


# ABT #16: this entire registry is a rule violation — analytical magnetics math
# in Heaviside. It is a fallback for the fast MKF path that returns no winding
# current waveform. Remove once PyOM exposes calculate_peak_winding_current().
_IPEAK_WORST: dict[str, Any] = {
    # Buck stays on the L-derived ripple formula (Vout * (1 - Dmin) /
    # (0.8 * L * fsw) / 2) so the post-filter and realism extract.py
    # agree on Ipeak without requiring currentRippleRatio in the spec.
    "buck": _ipeak_worst_buck,
    # Energy-storage-inductor topologies: the stress deriver's id_stress IS
    # the saturation-relevant inductor peak current (boost/four_switch), or a
    # CONSERVATIVE upper bound on it (cuk/sepic/zeta use the switch sum current
    # iin+iout ≥ the per-winding current — over-estimating Ipeak only ever
    # tightens the saturation gate, never lets a saturating core through). All
    # use the spec.currentRippleRatio path (works without knowing L).
    "boost": lambda spec: _ipeak_worst_from_stress("boost", spec),
    "cuk": lambda spec: _ipeak_worst_from_stress("cuk", spec),
    "flyback": lambda spec: _ipeak_worst_from_stress("flyback", spec),
    "sepic": lambda spec: _ipeak_worst_from_stress("sepic", spec),
    "zeta": lambda spec: _ipeak_worst_from_stress("zeta", spec),
    "four_switch_buck_boost": lambda spec: _ipeak_worst_from_stress(
        "four_switch_buck_boost", spec
    ),
    # Hard-switched transformer families: the core saturates on the MAGNETIZING
    # current Vin·D/(Lm·fsw) (the load component is balanced by the secondary),
    # NOT the primary load current the stress deriver returns. Forward-class
    # cores reset each cycle → unidirectional peak ΔIm; bridge/push-pull cores
    # swing ±ΔIm/2. Lm = the candidate's harvested magnetizing inductance.
    "single_switch_forward": lambda spec: _ipeak_worst_magnetizing(spec, bidirectional=False),
    "two_switch_forward": lambda spec: _ipeak_worst_magnetizing(spec, bidirectional=False),
    "active_clamp_forward": lambda spec: _ipeak_worst_magnetizing(spec, bidirectional=False),
    "push_pull": lambda spec: _ipeak_worst_magnetizing(spec, bidirectional=True),
    "asymmetric_half_bridge": lambda spec: _ipeak_worst_magnetizing(spec, bidirectional=True),
    "phase_shifted_full_bridge": lambda spec: _ipeak_worst_magnetizing(spec, bidirectional=True),
    "phase_shifted_half_bridge": lambda spec: _ipeak_worst_magnetizing(spec, bidirectional=True),
    "weinberg": lambda spec: _ipeak_worst_magnetizing(spec, bidirectional=True),
    # Isolated buck / buck-boost (flybuck family): single-switch isolated converters whose
    # transformer core resets each cycle → unidirectional magnetizing peak ΔIm, same as the
    # forward class (abt #48 della-Pollock long tail).
    "isolated_buck": lambda spec: _ipeak_worst_magnetizing(spec, bidirectional=False),
    "isolated_buck_boost": lambda spec: _ipeak_worst_magnetizing(spec, bidirectional=False),
    # Dual active bridge: a PHASE-SHIFTED isolated bridge (not resonant), square-wave
    # transformer excited symmetrically ±, so the magnetizing peak swings ±ΔIm/2 like the
    # other bridges (psfb/pshb/push_pull). fsw is loss-swept like any isolated bridge.
    "dual_active_bridge": lambda spec: _ipeak_worst_magnetizing(spec, bidirectional=True),
    # NOT registered: resonant topologies (llc/cllc/clllc/src) — fsw comes
    # from the gain law (stages.resonant_freq, B6), not the loss sweep, so they
    # take a separate design flow rather than this saturation-gated sweep.
}


def _isat_from_mas(
    mas: Mapping[str, Any],
    L_henries: float,
    *,
    temperature_c: float = 100.0,
) -> float | None:
    """Authoritative saturation current for a candidate magnetic, or
    ``None`` if it cannot be evaluated.

    Delegates entirely to ``PyOpenMagnetics.calculate_saturation_current(
    magnetic, T)`` — MKF owns this physics (it accounts for the air gap,
    which is vital: gapped cores have Isat several times larger than
    ungapped because the gap dominates the reluctance). There is **no**
    analytical fallback: per the project rule, magnetics math lives in MKF
    and we never substitute a fabricated ``B_sat·N·A_e/L`` scalar.

    When PyOM cannot evaluate the candidate (missing fields, unknown gap
    type, …) this returns ``None`` — an honest "cannot evaluate, skip this
    candidate" signal, not a fabricated value. The post-filter callers
    treat ``None`` accordingly; if *no* candidate can be evaluated the
    strict-mode path surfaces that honestly instead of shipping a guess.
    Note the safety-critical Isat the realism gate consumes comes from the
    extract enricher, which *raises* (never returns) on PyOM rejection.

    The ``L_henries`` parameter is retained for the ``L_henries <= 0``
    sanity guard and caller-side checks. ``temperature_c`` defaults to
    100 °C because that's the conservative worst case for ferrite B_sat
    across a typical 25–125 °C operating range.
    """
    if not isinstance(mas, Mapping) or L_henries <= 0:
        return None
    try:
        pyom = _import_pyom()
        isat = pyom.calculate_saturation_current(dict(mas), float(temperature_c))
    except Exception:
        # PyOM rejected the MAS — cannot evaluate this candidate. Return
        # None so the post-filter skips it; do NOT fabricate a value.
        return None
    if isinstance(isat, (int, float)) and isat > 0:
        return float(isat)
    return None


def _candidate_inductance(cand: "MagneticDesign") -> float | None:
    """The candidate's OWN authoritative magnetizing inductance — the L MKF
    actually built it to — or None if it can't be harvested.

    This is the truth for the ripple / worst-case-Ipeak the saturation margin
    is checked against. It is preferred over ``spec.desiredInductance``: on the
    designer path that key is a pre-design ripple-0.3 *seed* that MKF ignores
    (verified — MKF derives its own L), so it can differ several-fold from the
    L of the core being evaluated. Using the candidate's own L makes the
    pre-filter Ipeak agree with the realism gate (which reads the harvested L
    that ``full_design`` re-stamps post-design).
    """
    try:
        L = _harvest_authoritative_inductance(cand.mas)
    except Exception:
        return None
    return float(L) if isinstance(L, (int, float)) and L > 0 else None


def _ipeak_from_mas(cand: "MagneticDesign") -> float | None:
    """Peak MAGNETIZING current for the saturation check, read straight from MKF's SIMULATED
    excitation — the real flux-driving current PyOM computed for this exact magnetic, NOT an
    analytical Heaviside formula (house rule: magnetics/current math lives in MKF).

    The core saturates on the peak MAGNETIZING current (the net ampere-turns that set the flux),
    which is the quantity ``calculate_saturation_current`` is referred to (winding 0, the primary).
    For a single-winding inductor or a flyback the magnetizing current IS the winding current; but a
    forward/push-pull/bridge primary ALSO carries the reflected LOAD current, which does NOT set the
    flux (the secondary cancels it) — so the raw winding peak over-states the saturating current by a
    large factor (abt #12). MKF stamps the magnetizing current on the primary winding's excitation
    (``magnetizingCurrent.processed.peak``); prefer it.

    FALLBACK (no magnetizing current stamped): the winding currents referred to winding 0 via the
    turns ratio (I_w_ref0 = I_w / turnsRatios[w-1]) — dimensionally consistent with Isat, conservative
    for load-carrying primaries but never a wrong-direction (saturation-passing) estimate.

    Returns ``None`` if neither is present. Supersedes the per-topology analytical ``_IPEAK_WORST``
    computers (which over-/under-estimated)."""
    try:
        mas = cand.mas
    except Exception:
        return None
    if not isinstance(mas, Mapping):
        return None
    inputs = mas.get("inputs") or {}
    ops = inputs.get("operatingPoints")
    if not isinstance(ops, list):
        return None

    # PRIMARY: the magnetizing-current peak (the actual flux driver), max over operating points. MKF
    # stamps it on the primary (winding 0) excitation.
    mag_peak = 0.0
    mag_seen = False
    for op in ops:
        if not isinstance(op, Mapping):
            continue
        excs = op.get("excitationsPerWinding") or []
        if excs and isinstance(excs[0], Mapping):
            mc = excs[0].get("magnetizingCurrent")
            proc = mc.get("processed") if isinstance(mc, Mapping) else None
            pk = proc.get("peak") if isinstance(proc, Mapping) else None
            if isinstance(pk, (int, float)) and pk > 0:
                mag_seen = True
                a = abs(float(pk))
                if a > mag_peak:
                    mag_peak = a
    if mag_seen:
        return mag_peak

    # FALLBACK: winding currents referred to winding 0 via the turns ratio.
    trs_raw = (inputs.get("designRequirements") or {}).get("turnsRatios") or []

    def _ref_factor(winding_index: int) -> float:
        if winding_index <= 0:
            return 1.0  # primary is the reference winding
        if winding_index - 1 < len(trs_raw):
            t = trs_raw[winding_index - 1]
            tr = t.get("nominal") if isinstance(t, Mapping) else t
            if isinstance(tr, (int, float)) and tr != 0:
                return 1.0 / float(tr)
        return 1.0

    peak = 0.0
    seen = False
    for op in ops:
        if not isinstance(op, Mapping):
            continue
        for wi, exc in enumerate(op.get("excitationsPerWinding") or []):
            if not isinstance(exc, Mapping):
                continue
            wf = (exc.get("current") or {}).get("waveform") if isinstance(exc, Mapping) else None
            data = wf.get("data") if isinstance(wf, Mapping) else None
            if isinstance(data, list):
                factor = _ref_factor(wi)
                for v in data:
                    if isinstance(v, (int, float)):
                        seen = True
                        a = abs(float(v)) * factor
                        if a > peak:
                            peak = a
    return peak if (seen and peak > 0) else None


def magnetizing_peaks_per_op(mas: Mapping[str, Any]) -> list[float | None]:
    """Peak MAGNETIZING current (the flux / saturation driver) for EACH operating point of ``mas``,
    indexed to match ``inputs.operatingPoints`` — the per-OP analogue of :func:`_ipeak_from_mas`.

    MKF stamps the magnetizing current on the primary (winding 0) excitation, recomputed at the
    DESIGNED core's inductance. The saturation gate must use this, not the switch/load current
    (which over-states the saturating current for transformers whose primary also carries the
    reflected load). Returns ``None`` for an OP whose magnetizing current is absent (caller falls
    back to a conservative estimate)."""
    inputs = (mas or {}).get("inputs") if isinstance(mas, Mapping) else None
    ops = inputs.get("operatingPoints") if isinstance(inputs, Mapping) else None
    if not isinstance(ops, list):
        return []
    out: list[float | None] = []
    for op in ops:
        pk: float | None = None
        if isinstance(op, Mapping):
            excs = op.get("excitationsPerWinding") or []
            if excs and isinstance(excs[0], Mapping):
                mc = excs[0].get("magnetizingCurrent")
                proc = mc.get("processed") if isinstance(mc, Mapping) else None
                p = proc.get("peak") if isinstance(proc, Mapping) else None
                if isinstance(p, (int, float)) and p > 0:
                    pk = abs(float(p))
        out.append(pk)
    return out


def _turns_ratio_duty_feasible(
    cand: "MagneticDesign", *, tol: float = 0.01
) -> tuple[bool, str | None]:
    """``(feasible, reason)`` — reject a transformer candidate whose REALIZED step-down turns ratio
    (N_primary / N_secondary, from the integer-rounded coil) exceeds the ratio MKF DERIVED to be duty-
    feasible and stamped in ``designRequirements.turnsRatios`` (sized from the topology's maximum duty).

    A higher step-down ratio needs MORE duty to reach Vout; MKF's integer turns realization can round a
    secondary down (e.g. 12/4.41 → 12/4 = 3.0 vs a derived 2.72), pushing the realized ratio past the
    duty ceiling — for push-pull/forward that is per-switch D ≥ 0.5, which shorts the transformer, and
    the design only fails much later at Realize. Rejecting these here lets the pick land on a feasible
    candidate (lower realized ratio) instead. Single-winding inductors (no turns ratios) are always
    feasible. The check is the realized ratio against MKF's OWN duty-derived requirement, so it carries
    no per-topology duty formula."""
    try:
        mas = cand.mas
    except Exception:
        return True, None
    if not isinstance(mas, Mapping):
        return True, None
    coil = ((mas.get("magnetic") or {}).get("coil") or {}).get("functionalDescription")
    trs = ((mas.get("inputs") or {}).get("designRequirements") or {}).get("turnsRatios")
    if not isinstance(coil, list) or len(coil) < 2 or not isinstance(trs, list) or not trs:
        return True, None  # no transformer / no turns-ratio requirement → not duty-limited here
    n0 = coil[0].get("numberTurns") if isinstance(coil[0], Mapping) else None
    if not isinstance(n0, (int, float)) or n0 <= 0:
        return True, None
    for i in range(1, len(coil)):
        w = coil[i]
        ni = w.get("numberTurns") if isinstance(w, Mapping) else None
        if not isinstance(ni, (int, float)) or ni <= 0 or i - 1 >= len(trs):
            continue
        req = trs[i - 1]
        if isinstance(req, Mapping):
            nominal = req.get("nominal")
            if nominal is None:
                lo, hi = req.get("minimum"), req.get("maximum")
                if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
                    nominal = (lo + hi) / 2.0
                else:
                    nominal = lo if isinstance(lo, (int, float)) else hi
            req = nominal
        if not isinstance(req, (int, float)) or req <= 0:
            continue
        realized = float(n0) / float(ni)
        if realized > float(req) * (1.0 + tol):
            return False, (
                f"winding {i}: realized turns ratio {realized:.3f} exceeds the duty-feasible "
                f"{float(req):.3f} (overshoots the topology's maximum duty)"
            )
    return True, None


def _leakage_feasible(
    entry: TopologyEntry, spec: Mapping[str, Any], cand: "MagneticDesign", *, max_xlk_over_rload: float = 1.0
) -> tuple[bool, str | None]:
    """``(feasible, reason)`` — reject a hard-switched isolated transformer candidate whose SERIES LEAKAGE
    reactance exceeds the reflected load, so power cannot transfer through the transformer.

    A forward/bridge/push-pull stage delivers power THROUGH the transformer, so the leakage inductance sits
    in series with the reflected load. When Xlk = 2π·fsw·Llk approaches or exceeds the reflected load
    R_load·n², most of the primary voltage drops across the leakage and the stage CANNOT reach Vout — the
    converter saturates its duty/phase short of target (abt #65: a PSFB pinned a high-Lm magnetic whose
    ~17-20µH leakage at 150 kHz = ~19 Ω ≫ the 4.4 Ω reflected load → Vout capped at 6.8 V). MKF's candidate
    pool DOES contain low-leakage cores (a bigger core / fewer turns gives ~4 µH), but the loss-ranked pick
    lands on the smallest core (most turns → most leakage). Rejecting the high-leakage candidates here lets
    the pick land on a tight-coupled transformer that can deliver power.

    RESONANT topologies are EXEMPT: there the transformer leakage is (part of) the resonant tank inductor —
    wanted, not parasitic. The leakage is read house-rule-clean from MKF's field model
    (``calculate_leakage_inductance``); the magnetic's coil is already wound in the fast-advise MAS."""
    if getattr(entry, "family", None) == "resonant":
        return True, None
    try:
        mas = cand.mas
    except Exception:
        return True, None
    if not isinstance(mas, Mapping):
        return True, None
    coil = ((mas.get("magnetic") or {}).get("coil") or {}).get("functionalDescription")
    if not isinstance(coil, list) or len(coil) < 2:
        return True, None  # single-winding inductor → no through-transformer transfer to strangle
    n0 = coil[0].get("numberTurns") if isinstance(coil[0], Mapping) else None
    n1 = coil[1].get("numberTurns") if isinstance(coil[1], Mapping) else None
    if not all(isinstance(x, (int, float)) and x > 0 for x in (n0, n1)):
        return True, None
    n = float(n0) / float(n1)
    ops = spec.get("operatingPoints")
    if not isinstance(ops, list) or not ops or not isinstance(ops[0], Mapping):
        return True, None
    op = ops[0]
    vouts, iouts, fsw = op.get("outputVoltages"), op.get("outputCurrents"), op.get("switchingFrequency")
    if not (isinstance(vouts, list) and vouts and isinstance(iouts, list) and iouts
            and isinstance(fsw, (int, float)) and fsw > 0):
        return True, None
    vout, iout = vouts[0], iouts[0]
    if not (isinstance(vout, (int, float)) and isinstance(iout, (int, float)) and vout > 0 and iout > 0):
        return True, None
    rload_reflected = (float(vout) / float(iout)) * n * n
    try:
        pyom = _import_pyom()
        out = pyom.calculate_leakage_inductance(dict(cand.magnetic), float(fsw), 0)
        per = out.get("leakageInductancePerWinding") if isinstance(out, Mapping) else None
        llk = per[1].get("nominal") if isinstance(per, list) and len(per) > 1 and isinstance(per[1], Mapping) else None
    except Exception:
        return True, None  # cannot evaluate leakage → keep MKF's pick (surface, don't silently hide)
    if not isinstance(llk, (int, float)) or llk <= 0:
        return True, None
    xlk = 2.0 * math.pi * float(fsw) * float(llk)
    if xlk > max_xlk_over_rload * rload_reflected:
        return False, (
            f"leakage reactance {xlk:.1f}Ω exceeds {max_xlk_over_rload:g}× the reflected load "
            f"{rload_reflected:.1f}Ω (leakage {float(llk) * 1e6:.1f}µH strangles power transfer)"
        )
    return True, None


def _isat_margin_inputs(
    entry: TopologyEntry, spec: Mapping[str, Any], cand: "MagneticDesign"
) -> tuple[float | None, float | None]:
    """``(Ipeak_worst, L_for_guard)`` for an isat-margin check on ``cand``.

    Ipeak comes — house-rule-clean — from the **peak of MKF's simulated winding
    current** (:func:`_ipeak_from_mas`). The per-topology analytical computers
    (:data:`_IPEAK_WORST`) are only a FALLBACK for candidates whose MAS carries
    no excitation waveform (e.g. some fast-mode results). ``L_for_guard`` is the
    candidate's own harvested inductance. Returns ``(None, None)`` when neither a
    waveform nor an analytical computer yields a peak — the caller then keeps
    MKF's top scorer rather than silently hiding the design.
    """
    L_cand = _candidate_inductance(cand)
    L_guard: Any = L_cand if L_cand is not None else spec.get("desiredInductance")
    if not isinstance(L_guard, (int, float)) or L_guard <= 0:
        return None, None
    # PRIMARY: the real simulated peak from MKF's MAS.
    ipeak = _ipeak_from_mas(cand)
    if ipeak is None:
        # FALLBACK: the legacy analytical per-topology computer.
        ipeak_fn = _IPEAK_WORST.get(entry.name)
        if ipeak_fn is not None:
            spec_eff = {**spec, "desiredInductance": L_cand} if L_cand is not None else spec
            ipeak = ipeak_fn(spec_eff)
    if not isinstance(ipeak, (int, float)) or ipeak <= 0:
        return None, None
    return float(ipeak), float(L_guard)


def select_fast_by_isat_margin(
    topology: str | TopologyEntry,
    spec: Mapping[str, Any],
    *,
    n_candidates: int,
    core_mode: str = "standard cores",
    min_isat_ratio: float = 1.2,
    widen_pool: int = 50,
) -> list[MagneticDesign]:
    """Fast-path magnetic candidates with the slow path's Isat post-filter.

    :func:`design_magnetics_fast` returns candidates sorted by ascending
    losses only; its top scorer is the smallest core that passes MKF's
    flux-density saturation filter — which can still be undersized against
    the worst-case PEAK current (gap-aware ``Isat < min_isat_ratio *
    Ipeak_worst``). The slow path guards against this in
    :func:`_select_main_by_isat_margin`; the fast path historically did
    not, so non-isolated topologies (buck/boost/cuk) could surface a core
    the realism gate then fails on ``inductor_isat_margin``.

    This applies the SAME criterion — gap-aware ``Isat`` (PyOM's
    ``calculate_saturation_current``, gap-aware) against the SAME
    ``Ipeak_worst`` (``_IPEAK_WORST`` registry, which mirrors the realism
    gate's stress formulas) — so fast-path selection and the realism gate
    agree. Pure orchestration over MKF math; no duplicated magnetics.

    Returns the clearing subset (order preserved) when non-empty. If the
    initial pool has no clearing candidate, re-requests a larger fast pool
    (``widen_pool``) and re-filters — the fast adviser returns larger,
    higher-Isat cores further down its loss-sorted list. If STILL nothing
    clears — or the topology/spec yields no ``Ipeak_worst`` (no registered
    computer, or incomplete spec) — returns the unfiltered candidates so
    the realism gate fails the design honestly rather than this layer
    silently hiding it (CLAUDE.md: no silent defaults; matches the slow
    path's ``strict=False`` fallthrough).
    """
    entry = topology if isinstance(topology, TopologyEntry) else get(topology)
    candidates = design_magnetics_fast(
        entry,
        spec,
        max_results=n_candidates,
        core_mode=core_mode,
    )
    if min_isat_ratio <= 0 or not candidates:
        return candidates
    if _IPEAK_WORST.get(entry.name) is None:
        return candidates
    # Can't compute Ipeak even for the top candidate (incomplete spec /
    # unharvestable L) → skip the filter, keep MKF's top scorer (no silent hide).
    if _isat_margin_inputs(entry, spec, candidates[0])[0] is None:
        return candidates

    def _clears(c: MagneticDesign) -> bool:
        # Ipeak from THIS candidate's own inductance (not the spec seed).
        ipeak, L = _isat_margin_inputs(entry, spec, c)
        if ipeak is None or L is None:
            return False
        isat = _isat_from_mas(c.magnetic, L)
        return isat is not None and isat >= float(min_isat_ratio) * ipeak

    clearing = [c for c in candidates if _clears(c)]
    if not clearing and n_candidates < widen_pool:
        widened = design_magnetics_fast(
            entry,
            spec,
            max_results=widen_pool,
            core_mode=core_mode,
        )
        clearing = [c for c in widened if _clears(c)]
    return clearing if clearing else candidates

