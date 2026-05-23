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

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from heaviside.topologies.registry import TopologyEntry, get
from heaviside._pyom_cache import cached_call

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


# Realism-relevant PyOpenMagnetics global settings. Applied on first
# import via :func:`_import_pyom`. These flip OFF-by-default knobs that
# Heaviside relies on for accurate sim + selection:
#
#   * ``circuitSimulatorIncludeSaturation``: model the inductor's BH
#     saturation in the ngspice deck (default OFF — Heaviside's realism
#     gate already checks isat margin, but the sim itself should also
#     reflect saturation for the operating-point sweep to be accurate).
#   * ``circuitSimulatorIncludeMutualResistance``: model winding-to-
#     winding resistive coupling (transformers, coupled inductors).
#
# Per-topology ``SpiceSimulationConfig`` (snubR/snubC, diode model,
# output cap, solver tolerances, samplesPerPeriod) is NOT reachable
# from Python today — ``set_spice_config()`` exists in MKF C++ but
# pybind11 doesn't bind it. Heaviside post-processes the emitted
# netlist (heaviside.sim.runner) to override the worst offenders
# (snubber R/C, diode model) until the binding lands upstream.
_HEAVISIDE_PYOM_SETTINGS: dict[str, Any] = {
    "circuitSimulatorIncludeSaturation": True,
    "circuitSimulatorIncludeMutualResistance": True,
}

_pyom_settings_applied: bool = False


def _import_pyom() -> Any:
    """Lazy import of the PyOpenMagnetics extension (mirrors ``topologies.dispatch``).

    Also applies :data:`_HEAVISIDE_PYOM_SETTINGS` once on first call,
    so every downstream caller sees a consistently-configured PyOM.
    """
    global _pyom_settings_applied
    import contextlib

    from PyOpenMagnetics import PyOpenMagnetics as _ext
    if not _pyom_settings_applied:
        # If a future PyOM build drops one of these keys, swallow it
        # rather than crashing import — the realism gate is independent
        # of these settings.
        with contextlib.suppress(Exception):
            _ext.set_settings(dict(_HEAVISIDE_PYOM_SETTINGS))
        _pyom_settings_applied = True
    return _ext


def design_magnetics(
    topology: str | TopologyEntry,
    converter_spec: Mapping[str, Any],
    *,
    max_results: int = 1,
    core_mode: str = "available cores",
    use_ngspice: bool = False,
    weights: Mapping[str, float] | None = None,
) -> list[MagneticDesign]:
    """Design the magnetic component(s) for a converter spec.

    Calls ``PyOpenMagnetics.design_magnetics_from_converter`` retrying
    each name variant registered for the topology (mirrors the existing
    ``heaviside.topologies.dispatch`` behaviour).

    Parameters
    ----------
    topology : str | TopologyEntry
        Canonical Python name or a registry entry.
    converter_spec : Mapping
        Converter inputs JSON (``inputVoltage``, ``desiredInductance``,
        ``operatingPoints``, …). See PyOM's ``AGENTS.md §5``.
    max_results : int
        Number of top-scoring designs to return.
    core_mode : str
        PyOM core search mode. Must be lowercase, space-separated
        (``"available cores"``, ``"standard cores"``). ``"AVAILABLE_CORES"``
        raises in PyOM.
    use_ngspice : bool
        Whether PyOM should drive its design loop with ngspice waveforms
        instead of analytical models. Off by default — analytical is
        roughly 10× faster and accurate enough for first-pass design.
    weights : Mapping[str, float] | None
        Optional scoring weights passed through to PyOM (e.g.
        ``{"EFFICIENCY": 2.0, "DIMENSIONS": 0.5}``).

    Returns
    -------
    list[MagneticDesign]
        At most ``max_results`` entries, sorted by descending ``scoring``.

    Raises
    ------
    BridgeError
        If every registered PyOM name variant returns an error envelope,
        or if the response shape is unexpected, or if zero designs come
        back.
    """
    entry = topology if isinstance(topology, TopologyEntry) else get(topology)
    pyom = _import_pyom()

    last_error: str | None = None
    for variant in entry.pyom_names:
        t0 = time.monotonic()
        _spec_arg = dict(converter_spec)
        _weights_arg = dict(weights) if weights is not None else None
        result = cached_call(
            "design_magnetics_from_converter",
            (variant, _spec_arg, int(max_results), str(core_mode),
             bool(use_ngspice), _weights_arg),
            call=lambda v=variant, s=_spec_arg, w=_weights_arg: (
                pyom.design_magnetics_from_converter(
                    v, s, int(max_results), str(core_mode),
                    bool(use_ngspice), w,
                )
            ),
        )
        elapsed = time.monotonic() - t0

        if not isinstance(result, dict):
            raise BridgeError(
                f"design_magnetics_from_converter({variant!r}) returned "
                f"{type(result).__name__}, expected dict."
            )

        err = result.get("error")
        if err is not None:
            # PyOM uses "Unknown topology" to signal a missing binding —
            # try the next variant. Any other error is fatal (no silent
            # fallbacks to a different topology).
            if isinstance(err, str) and "Unknown topology" in err:
                last_error = err
                continue
            raise BridgeError(
                f"PyOpenMagnetics rejected topology {variant!r}: {err}"
            )

        data = result.get("data")
        if not isinstance(data, list):
            raise BridgeError(
                f"design_magnetics_from_converter({variant!r}) returned "
                f"data={type(data).__name__}, expected list. "
                f"Result keys: {sorted(result)}"
            )
        if not data:
            raise BridgeError(
                f"design_magnetics_from_converter({variant!r}) returned "
                f"zero designs for spec. Loosen constraints or check "
                f"converter inputs."
            )

        designs: list[MagneticDesign] = []
        for raw in data:
            if not isinstance(raw, dict) or "mas" not in raw:
                raise BridgeError(
                    f"design_magnetics_from_converter({variant!r}) returned "
                    f"a design entry with no 'mas' field: keys={list(raw)}"
                )
            designs.append(
                MagneticDesign(
                    scoring=float(raw.get("scoring", 0.0)),
                    mas=raw["mas"],
                    elapsed_s=elapsed,
                )
            )
        designs.sort(key=lambda d: d.scoring, reverse=True)
        return designs

    # Every variant returned "Unknown topology" — same condition as
    # ``topologies.dispatch.TopologyDispatchError`` but distinct symptom,
    # so we raise BridgeError with the upstream-binding hint.
    raise BridgeError(
        f"PyOpenMagnetics does not recognise any variant of topology "
        f"{entry.name!r}. Tried: {entry.pyom_names}. Last error: "
        f"{last_error!r}. This is an upstream binding gap — add it to "
        f"vendor/PyOpenMagnetics/ and rebuild (do not work around it here)."
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


def _tas_capacitor_components(tas: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return every TAS component declared as a capacitor.

    Mirror of :func:`_tas_magnetic_components`. Three recognition
    paths: inline PEAS ``data.capacitor`` (post-attach), the SPICE
    reader's ``category == "capacitor"`` convention, and the
    stencil's ``capacitors.ndjson`` placeholder URL.
    """
    out: list[dict[str, Any]] = []
    for stage in tas.get("topology", {}).get("stages", []):
        for c in stage.get("circuit", {}).get("components", []):
            data = c.get("data")
            if isinstance(data, dict) and "capacitor" in data:
                out.append(c)
                continue
            if c.get("category") == "capacitor":
                out.append(c)
                continue
            if isinstance(data, str) and "capacitors.ndjson" in data:
                out.append(c)
    return out


def _attach_one_capacitor(
    component: dict[str, Any], spec: ExtraCapacitorSpec
) -> None:
    """Emit a PEAS capacitor document into ``component['data']`` (mutating).

    Pre-binding the capacitor body is an empty stub ``{}`` (allowed by
    CAS/capacitor.json which has no required fields); the design
    intent lives in ``data.inputs`` which carries the full CAS::Inputs
    envelope (``designRequirements + operatingPoints``) produced by
    PyOM's ``get_extra_components_inputs``.

    Downstream the component-librarian agent reads ``data.inputs`` and
    fills in ``data.capacitor`` with the chosen catalog entry.

    Legacy ``category`` / ``cas_inputs`` siblings are no longer written.
    """
    component.pop("data", None)
    component.pop("category", None)
    component.pop("cas_inputs", None)
    component["data"] = {"capacitor": {}, "inputs": spec.inputs}


def attach_components_to_tas(
    tas: dict[str, Any],
    components: ConverterComponents,
    *,
    topology: str | TopologyEntry,
) -> dict[str, Any]:
    """Attach a full :class:`ConverterComponents` bundle to a TAS topology.

    Uses ``entry.magnetic_binding`` to route each TAS magnetic
    component to either ``components.main_magnetic`` (binding value
    ``None``) or ``components.extra_magnetics[<pyom_name>]``.

    If ``entry.capacitor_binding`` is non-empty (resonant topologies
    only — LLC / CLLC / CLLLC), each TAS capacitor named in the
    binding is annotated with the matching
    ``components.extra_capacitors[role].inputs`` envelope as
    ``component["cas_inputs"]``. The bridge does not pick capacitor
    MPNs; the downstream component-librarian agent reads
    ``cas_inputs`` and writes back ``component["cas"]``.

    Capacitors not listed in ``capacitor_binding`` (output filter
    caps in non-resonant topologies) are left as untouched
    placeholders — they are sized later from operating-point ripple.

    Raises
    ------
    BridgeError
        If the topology has no ``magnetic_binding`` configured, if a
        TAS magnetic / capacitor has no entry in the binding, or if
        a binding points at a missing extras-role name.
    """
    entry = topology if isinstance(topology, TopologyEntry) else get(topology)
    binding = entry.magnetic_binding
    if not binding:
        raise BridgeError(
            f"attach_components_to_tas: topology {entry.name!r} has no "
            f"magnetic_binding in the registry. Add one to "
            f"heaviside/topologies/registry.py, or call the lower-level "
            f"attach_magnetics_to_tas() with an explicit mapping."
        )

    magnetics = _tas_magnetic_components(tas)
    if not magnetics:
        raise BridgeError(
            "attach_components_to_tas: TAS topology contains zero "
            "magnetic components. Nothing to attach."
        )

    tas_names = [c.get("name") for c in magnetics]
    missing = [n for n in tas_names if n not in binding]
    if missing:
        raise BridgeError(
            f"attach_components_to_tas: TAS magnetics {missing} have no "
            f"entry in {entry.name!r}.magnetic_binding "
            f"(known: {sorted(binding)}). Update the registry."
        )

    main_count = sum(1 for v in binding.values() if v is None)
    if main_count != 1:
        raise BridgeError(
            f"attach_components_to_tas: {entry.name!r}.magnetic_binding "
            f"must have exactly one entry with value None (the main "
            f"magnetic); got {main_count}."
        )

    for component in magnetics:
        name = component.get("name")
        target = binding[name]
        if target is None:
            _attach_one(component, components.main_magnetic)
        else:
            if target not in components.extra_magnetics:
                raise BridgeError(
                    f"attach_components_to_tas: TAS magnetic {name!r} "
                    f"is bound to PyOM extras role {target!r}, but "
                    f"ConverterComponents.extra_magnetics has keys "
                    f"{sorted(components.extra_magnetics)}. Did Phase B "
                    f"complete?"
                )
            _attach_one(component, components.extra_magnetics[target])

    # ---- Capacitor extras (resonant topologies) ------------------------
    #
    # ``capacitor_binding`` is empty for most topologies — their caps
    # (output filter, bulk bus) are sized by the librarian from the
    # operating-point ripple, not from a PyOM CAS::Inputs envelope. For
    # resonant topologies (LLC / CLLC / CLLLC), MKF emits one or two
    # ``resonantCapacitor_*`` extras describing the tuned-tank cap; the
    # stencil's ``Cr*`` TAS components must be bound to those.
    cap_binding = entry.capacitor_binding
    if cap_binding:
        capacitors = _tas_capacitor_components(tas)
        tas_cap_names = {c.get("name"): c for c in capacitors}
        spec_by_name = {s.name: s for s in components.extra_capacitors}

        # Every name in the binding must exist in TAS — otherwise the
        # stencil and registry disagree.
        binding_missing_in_tas = sorted(set(cap_binding) - set(tas_cap_names))
        if binding_missing_in_tas:
            raise BridgeError(
                f"attach_components_to_tas: {entry.name!r}.capacitor_binding "
                f"references TAS capacitors {binding_missing_in_tas} that "
                f"are not present in the decomposed TAS (have "
                f"{sorted(n for n in tas_cap_names if n)}). Stencil / "
                f"registry drift."
            )

        for tas_name, role in cap_binding.items():
            if role not in spec_by_name:
                raise BridgeError(
                    f"attach_components_to_tas: TAS capacitor {tas_name!r} "
                    f"is bound to PyOM extras role {role!r}, but "
                    f"ConverterComponents.extra_capacitors has roles "
                    f"{sorted(spec_by_name)}. Phase B did not emit the "
                    f"expected cap envelope."
                )
            _attach_one_capacitor(tas_cap_names[tas_name], spec_by_name[role])

    return tas


__all__ = [
    "BridgeError",
    "ConverterComponents",
    "ExtraCapacitorSpec",
    "ExtraComponentsMode",
    "ExtraMagneticSpec",
    "MagneticDesign",
    "attach_components_to_tas",
    "attach_magnetics_to_tas",
    "design_converter_components",
    "design_extra_magnetic",
    "design_magnetics",
    "extra_components",
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


@dataclass(frozen=True, slots=True)
class ExtraMagneticSpec:
    """A pre-filled ``MAS::Inputs`` for a single extra magnetic.

    ``name`` is taken from ``inputs.designRequirements.name`` and
    matches the canonical role string produced by the corresponding
    MKF topology model (e.g. ``"outputInductor"``,
    ``"resonantInductor"``). It is the binding key used by
    ``TopologyEntry.extras_binding`` to attach this design back to
    the right TAS component.
    """

    name: str
    inputs: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ExtraCapacitorSpec:
    """A pre-filled ``CAS::Inputs`` for a single extra capacitor.

    The bridge does **not** design capacitors — Phase B emits these
    specs and they are handed off to the component-librarian agent for
    catalog selection.
    """

    name: str
    inputs: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ConverterComponents:
    """Full PyOM-side component design for a converter spec.

    Attributes
    ----------
    main_magnetic : MagneticDesign
        The transformer (isolated topologies) or main inductor
        (non-isolated) returned by Phase A
        (``design_magnetics_from_converter``).
    extra_magnetics : dict[str, MagneticDesign]
        Map from extras-role name (``"outputInductor"`` etc.) to its
        designed MagneticDesign from Phase B.
    extra_capacitors : tuple[ExtraCapacitorSpec, ...]
        Untouched CAS::Inputs envelopes for downstream librarian
        selection — the bridge never picks a capacitor MPN.
    """

    main_magnetic: MagneticDesign
    extra_magnetics: dict[str, MagneticDesign] = field(default_factory=dict)
    extra_capacitors: tuple[ExtraCapacitorSpec, ...] = ()


def extra_components(
    topology: str | TopologyEntry,
    converter_spec: Mapping[str, Any],
    *,
    mode: ExtraComponentsMode = "REAL",
    main_magnetic_mas: Mapping[str, Any] | None = None,
) -> tuple[list[ExtraMagneticSpec], list[ExtraCapacitorSpec]]:
    """Probe PyOM for the extra-components inputs of a topology.

    Wraps ``PyOpenMagnetics.get_extra_components_inputs``. ``REAL``
    mode requires ``main_magnetic_mas`` — the **Magnetic** JSON
    sub-document (``designs[0].magnetic``, NOT the wrapping MAS
    envelope ``designs[0].mas``). ``IDEAL`` mode does not.

    Returns ``(extra_magnetics, extra_capacitors)``, both in the
    declaration order PyOM emits them — which is the order baked into
    each topology's ``dispatch_extra_components`` implementation and
    used as the binding for :class:`TopologyEntry.extras_binding`.

    Raises
    ------
    BridgeError
        On engine error, unexpected shape, or REAL mode called with
        no main magnetic.
    """
    entry = topology if isinstance(topology, TopologyEntry) else get(topology)
    pyom = _import_pyom()

    if mode not in ("IDEAL", "REAL"):
        raise BridgeError(
            f"extra_components: mode must be 'IDEAL' or 'REAL', got {mode!r}."
        )
    if mode == "REAL" and main_magnetic_mas is None:
        raise BridgeError(
            "extra_components: mode='REAL' requires main_magnetic_mas "
            "(pass designs[0].magnetic from design_magnetics() — the "
            "Magnetic sub-document, not the MAS envelope). Use "
            "mode='IDEAL' for spec probing without a designed main "
            "magnetic."
        )

    last_error: str | None = None
    for variant in entry.pyom_names:
        _spec_arg = dict(converter_spec)
        _mmm_arg = (
            dict(main_magnetic_mas) if main_magnetic_mas is not None else None
        )
        result = cached_call(
            "get_extra_components_inputs",
            (variant, _spec_arg, mode, _mmm_arg),
            call=lambda v=variant, s=_spec_arg, m=_mmm_arg: (
                pyom.get_extra_components_inputs(v, s, mode, m)
            ),
        )

        # PyOM error envelopes are dicts with an "error" key.
        if isinstance(result, dict) and "error" in result:
            err = result["error"]
            if isinstance(err, str) and "Unknown topology" in err:
                last_error = err
                continue
            raise BridgeError(
                f"get_extra_components_inputs({variant!r}) failed: {err}"
            )

        if not isinstance(result, list):
            raise BridgeError(
                f"get_extra_components_inputs({variant!r}) returned "
                f"{type(result).__name__}, expected list. Got: {result!r}"
            )

        mags: list[ExtraMagneticSpec] = []
        caps: list[ExtraCapacitorSpec] = []
        for i, raw in enumerate(result):
            if not isinstance(raw, dict) or "kind" not in raw or "inputs" not in raw:
                raise BridgeError(
                    f"get_extra_components_inputs({variant!r})[{i}] has bad "
                    f"shape: keys={list(raw) if isinstance(raw, dict) else type(raw).__name__}"
                )
            kind = raw["kind"]
            inputs = raw["inputs"]
            if not isinstance(inputs, dict):
                raise BridgeError(
                    f"get_extra_components_inputs({variant!r})[{i}] 'inputs' "
                    f"is {type(inputs).__name__}, expected dict."
                )
            name = (
                inputs.get("designRequirements", {}).get("name")
                if isinstance(inputs.get("designRequirements"), dict)
                else None
            )
            if not name:
                raise BridgeError(
                    f"get_extra_components_inputs({variant!r})[{i}] missing "
                    f"designRequirements.name — cannot bind to TAS."
                )
            if kind == "magnetic":
                mags.append(ExtraMagneticSpec(name=name, inputs=inputs))
            elif kind == "capacitor":
                caps.append(ExtraCapacitorSpec(name=name, inputs=inputs))
            else:
                raise BridgeError(
                    f"get_extra_components_inputs({variant!r})[{i}] unknown "
                    f"kind={kind!r}; expected 'magnetic' or 'capacitor'."
                )
        return mags, caps

    raise BridgeError(
        f"PyOpenMagnetics does not recognise any variant of topology "
        f"{entry.name!r}. Tried: {entry.pyom_names}. Last error: "
        f"{last_error!r}."
    )


def design_extra_magnetic(
    spec: ExtraMagneticSpec,
    *,
    max_results: int = 1,
    core_mode: str = "available cores",
) -> list[MagneticDesign]:
    """Design a single extra magnetic from its pre-filled MAS::Inputs.

    Wraps ``PyOpenMagnetics.calculate_advised_magnetics`` — the
    standalone equivalent of ``design_magnetics_from_converter`` for
    a MAS::Inputs that already has ``designRequirements`` +
    ``operatingPoints`` filled in.

    Note
    ----
    ``calculate_advised_magnetics`` is documented to accept
    ``"AVAILABLE_CORES"`` / ``"STANDARD_CORES"`` but actually requires
    the lowercase, space-separated form (``"available cores"``) — same
    quirk as ``design_magnetics_from_converter``. The default here is
    therefore the working form.
    """
    if not isinstance(spec, ExtraMagneticSpec):
        raise BridgeError(
            f"design_extra_magnetic: spec must be ExtraMagneticSpec, "
            f"got {type(spec).__name__}."
        )
    pyom = _import_pyom()

    t0 = time.monotonic()
    _spec_inputs = dict(spec.inputs)
    result = cached_call(
        "calculate_advised_magnetics",
        (_spec_inputs, int(max_results), str(core_mode)),
        call=lambda s=_spec_inputs: (
            pyom.calculate_advised_magnetics(s, int(max_results), str(core_mode))
        ),
    )
    elapsed = time.monotonic() - t0

    if not isinstance(result, dict):
        raise BridgeError(
            f"calculate_advised_magnetics({spec.name!r}) returned "
            f"{type(result).__name__}, expected dict."
        )
    data = result.get("data")
    if isinstance(data, str):
        # PyOM signals catalog-empty / schema errors by putting an error
        # string in 'data' rather than 'error'.
        raise BridgeError(
            f"calculate_advised_magnetics({spec.name!r}) failed: {data}"
        )
    if not isinstance(data, list):
        raise BridgeError(
            f"calculate_advised_magnetics({spec.name!r}) returned "
            f"data={type(data).__name__}, expected list."
        )
    if not data:
        raise BridgeError(
            f"calculate_advised_magnetics({spec.name!r}) returned zero "
            f"designs. Loosen constraints or check MAS::Inputs."
        )

    designs: list[MagneticDesign] = []
    for raw in data:
        if not isinstance(raw, dict) or "mas" not in raw:
            raise BridgeError(
                f"calculate_advised_magnetics({spec.name!r}) entry "
                f"missing 'mas': keys={list(raw) if isinstance(raw, dict) else type(raw).__name__}"
            )
        designs.append(
            MagneticDesign(
                scoring=float(raw.get("scoring", 0.0)),
                mas=raw["mas"],
                elapsed_s=elapsed,
            )
        )
    designs.sort(key=lambda d: d.scoring, reverse=True)
    return designs


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


_IPEAK_WORST: dict[str, Any] = {
    # Buck stays on the L-derived ripple formula (Vout * (1 - Dmin) /
    # (0.8 * L * fsw) / 2) so the post-filter and realism extract.py
    # agree on Ipeak without requiring currentRippleRatio in the spec.
    "buck":    _ipeak_worst_buck,
    # Other topologies use the spec.currentRippleRatio path via the
    # stress deriver (less restrictive — works without knowing L).
    "boost":   lambda spec: _ipeak_worst_from_stress("boost", spec),
    "cuk":     lambda spec: _ipeak_worst_from_stress("cuk", spec),
    "flyback": lambda spec: _ipeak_worst_from_stress("flyback", spec),
}


def _isat_from_mas(
    mas: Mapping[str, Any],
    L_henries: float,
    *,
    temperature_c: float = 100.0,
) -> float | None:
    """Authoritative saturation current for a candidate magnetic.

    Calls ``PyOpenMagnetics.calculate_saturation_current(magnetic, T)``
    which accounts for the air gap (vital — gapped cores have Isat several
    times larger than ungapped, because the gap dominates the reluctance).
    Falls back to the analytical ``B_sat * N * A_e / L`` only if the PyOM
    call fails; that fallback is wildly conservative for gapped cores so
    we log nothing — the caller treats ``None`` as "skip this candidate".

    The ``L_henries`` parameter is retained for the fallback branch and
    for caller-side sanity checks. ``temperature_c`` defaults to 100 °C
    because that's the conservative worst case for ferrite B_sat across
    a typical 25–125 °C operating range.
    """
    if not isinstance(mas, Mapping) or L_henries <= 0:
        return None
    try:
        pyom = _import_pyom()
        isat = pyom.calculate_saturation_current(dict(mas), float(temperature_c))
        if isinstance(isat, (int, float)) and isat > 0:
            return float(isat)
    except Exception:
        # PyOM may reject the MAS (missing fields, unknown gap type, …)
        # — fall through to the analytical fallback rather than crashing
        # the whole post-filter run.
        pass
    # Analytical fallback (treats gap as zero — wildly conservative for
    # gapped cores; only useful as a "better than nothing" lower bound).
    try:
        A_e = (
            mas.get("core", {})
            .get("processedDescription", {})
            .get("effectiveParameters", {})
            .get("effectiveArea")
        )
        if not isinstance(A_e, (int, float)) or A_e <= 0:
            return None
        fd = mas.get("coil", {}).get("functionalDescription")
        if not (isinstance(fd, list) and fd and isinstance(fd[0], Mapping)):
            return None
        N = fd[0].get("numberTurns")
        if not isinstance(N, (int, float)) or N <= 0:
            return None
        sat = (
            mas.get("core", {})
            .get("functionalDescription", {})
            .get("material", {})
            .get("saturation")
        )
        if not (isinstance(sat, list) and sat):
            return None
        b_values = [
            p["magneticFluxDensity"] for p in sat
            if isinstance(p, Mapping)
            and isinstance(p.get("magneticFluxDensity"), (int, float))
            and p.get("magneticFluxDensity") > 0
        ]
        if not b_values:
            return None
        b_sat = min(b_values)
        return float(b_sat) * float(N) * float(A_e) / float(L_henries)
    except (KeyError, TypeError, AttributeError):
        return None


def _select_main_by_isat_margin(
    candidates: Sequence[MagneticDesign],
    entry: TopologyEntry,
    spec: Mapping[str, Any],
    *,
    min_isat_ratio: float,
    strict: bool = False,
) -> MagneticDesign | None:
    """Pick the highest-scoring candidate whose Isat margin clears
    ``min_isat_ratio`` against the spec's worst-case peak current.

    Default behaviour (``strict=False``) falls back to ``candidates[0]``
    (PyMKF's top scorer) when:
      * the post-filter is disabled (``min_isat_ratio <= 0``);
      * no per-topology Ipeak_worst computer is registered for
        ``entry.name``;
      * the spec is too incomplete to compute Ipeak_worst;
      * the candidate pool has no MAS we can evaluate;
      * NO candidate clears the margin (then we deliberately keep
        PyMKF's pick so the realism gate can FAIL the design honestly).

    With ``strict=True`` the last bullet returns ``None`` instead — the
    caller (typically :func:`design_converter_components`) uses that
    signal to retry with a larger candidate pool / wider catalogue
    before accepting an under-margin design. The other fallthroughs
    still return ``candidates[0]`` because they indicate "no margin
    check possible" rather than "no margin-satisfying candidate."
    """
    if not candidates:
        raise BridgeError("_select_main_by_isat_margin: empty candidate list")
    if min_isat_ratio <= 0:
        return candidates[0]
    ipeak_fn = _IPEAK_WORST.get(entry.name)
    if ipeak_fn is None:
        return candidates[0]
    ipeak = ipeak_fn(spec)
    if ipeak is None or ipeak <= 0:
        return candidates[0]
    L = spec.get("desiredInductance")
    if not isinstance(L, (int, float)) or L <= 0:
        return candidates[0]
    threshold = float(min_isat_ratio) * float(ipeak)
    for cand in candidates:
        isat = _isat_from_mas(cand.magnetic, float(L))
        if isat is None:
            continue
        if isat >= threshold:
            return cand
    return None if strict else candidates[0]


def _try_pick_main(
    entry: TopologyEntry,
    converter_spec: Mapping[str, Any],
    *,
    pool: int,
    core_mode: str,
    use_ngspice: bool,
    weights: Mapping[str, float] | None,
    min_isat_ratio: float,
    strict: bool,
) -> tuple[MagneticDesign | None, list[MagneticDesign]]:
    """Single retrieval pass. Returns ``(picked_or_None_if_strict_miss,
    raw_candidates_list)``. The raw list is returned so the caller can
    fall back to ``candidates[0]`` (PyMKF's top scorer) after the final
    retry without a third design_magnetics call.
    """
    candidates = design_magnetics(
        entry, converter_spec, max_results=pool,
        core_mode=core_mode, use_ngspice=use_ngspice, weights=weights,
    )
    picked = _select_main_by_isat_margin(
        candidates, entry, converter_spec,
        min_isat_ratio=min_isat_ratio, strict=strict,
    )
    return picked, candidates


def design_converter_components(
    topology: str | TopologyEntry,
    converter_spec: Mapping[str, Any],
    *,
    max_results: int = 1,
    core_mode: str = "available cores",
    use_ngspice: bool = False,
    weights: Mapping[str, float] | None = None,
    min_isat_ratio: float = 1.2,
    candidate_pool_size: int = 10,
    fallback_pool_size: int = 50,
) -> ConverterComponents:
    """End-to-end Phase A + Phase B for a converter spec.

    1. Design the main magnetic via :func:`design_magnetics`. Initial
       request asks for ``candidate_pool_size`` candidates (default 10)
       so the post-filter has room to pick a properly-sized core.
    2. Post-filter by saturation margin: pick the highest-scoring main
       whose ``Isat >= min_isat_ratio * Ipeak_worst`` (default 1.2x,
       matching the realism gate).
    3. **Interim workaround for upstream MKF gap** (see
       ``docs/pymkf-spiceconfig-binding-request.md`` — `MagneticFilterSaturation`
       has no derating margin so its top-K can all sit at 95-100 % of
       B_sat). If the initial pool has no candidate satisfying the
       margin, retry with ``fallback_pool_size`` candidates AND the
       full core catalogue (``useOnlyCoresInStock=False`` — 10K cores
       instead of 1.5K stock-only). Pays the slower-search cost only
       when the cheap path fails. Removable when upstream MKF lands
       the saturation-margin scoring change.
    4. Probe extras in REAL mode against the chosen main magnetic.
    5. Design each extra magnetic via :func:`design_extra_magnetic`.
    6. Pass capacitor specs through untouched (bridge does not pick caps).

    Pass ``min_isat_ratio=0`` to disable the post-filter AND the retry,
    restoring the pre-2026-05-22 behaviour (always take PyMKF's top
    scorer). Pass ``fallback_pool_size=0`` to disable just the retry.
    """
    entry = topology if isinstance(topology, TopologyEntry) else get(topology)

    pool = max(int(max_results), int(candidate_pool_size))
    main: MagneticDesign | None
    main, main_designs = _try_pick_main(
        entry, converter_spec, pool=pool,
        core_mode=core_mode, use_ngspice=use_ngspice, weights=weights,
        min_isat_ratio=min_isat_ratio,
        strict=(min_isat_ratio > 0 and int(fallback_pool_size) > pool),
    )

    if main is None:
        # Tier-2 retry: widen the candidate pool + flip
        # useOnlyCoresInStock off so PyMKF sees the full 10K-core
        # catalogue instead of the 1.5K stock subset. Temporarily,
        # via _import_pyom's set_settings — restored after.
        #
        # Crash safety: PyMKF has a SIGSEGV when asked for more
        # candidates than its accessible pool actually contains
        # (observed on flyback with stock cores: max_results=15
        # returns 14 designs and works; max_results=20 segfaults).
        # The C++ design loop appears to reserve a vector of size
        # max_results and overshoots when fewer candidates exist.
        # Defence: cap tier-2 pool at 2x what tier-1 actually
        # returned. If tier-1 returned the full pool requested, we
        # have headroom to grow; if PyMKF gave us fewer (e.g. 8
        # of 10 requested), we know the accessible pool is small
        # and we stay within ~2x of that.
        safe_pool = max(pool, 2 * len(main_designs)) if main_designs else pool
        tier2_pool = min(int(fallback_pool_size), safe_pool)
        if tier2_pool <= pool:
            # No room to actually escalate — accept tier-1's top scorer.
            main = main_designs[0]
        else:
            pyom = _import_pyom()
            try:
                prior = pyom.get_settings()
                prior_in_stock = bool(prior.get("useOnlyCoresInStock", True))
            except Exception:
                prior_in_stock = True
            try:
                if prior_in_stock:
                    pyom.set_settings({"useOnlyCoresInStock": False})
                main2, main_designs2 = _try_pick_main(
                    entry, converter_spec,
                    pool=tier2_pool,
                    core_mode=core_mode, use_ngspice=use_ngspice, weights=weights,
                    min_isat_ratio=min_isat_ratio,
                    strict=False,  # last attempt: honest fallback to top scorer
                )
                main = main2
                main_designs = main_designs2
            finally:
                if prior_in_stock:
                    try:
                        pyom.set_settings({"useOnlyCoresInStock": True})
                    except Exception:
                        pass

    if main is None:  # pragma: no cover — _try_pick_main with strict=False never returns None
        main = main_designs[0]

    mag_specs, cap_specs = extra_components(
        entry,
        converter_spec,
        mode="REAL",
        main_magnetic_mas=main.magnetic,
    )

    extra_mag_designs: dict[str, MagneticDesign] = {}
    for ms in mag_specs:
        # max_results=1 here — orchestrator picks the best per role.
        # Callers wanting Pareto fronts should drive design_extra_magnetic
        # themselves.
        results = design_extra_magnetic(ms, max_results=1, core_mode=core_mode)
        extra_mag_designs[ms.name] = results[0]

    return ConverterComponents(
        main_magnetic=main,
        extra_magnetics=extra_mag_designs,
        extra_capacitors=tuple(cap_specs),
    )
