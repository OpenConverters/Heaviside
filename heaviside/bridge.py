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


def _import_pyom() -> Any:
    """Lazy import of the PyOpenMagnetics extension (mirrors ``topologies.dispatch``)."""
    from PyOpenMagnetics import PyOpenMagnetics as _ext
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
        result = pyom.design_magnetics_from_converter(
            variant,
            dict(converter_spec),
            int(max_results),
            str(core_mode),
            bool(use_ngspice),
            dict(weights) if weights is not None else None,
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

    Components are classified as magnetic when their ``data`` URL points
    at ``magnetics.ndjson`` (the placeholder convention used by the
    stencils) **or** when they carry an explicit ``category == "magnetic"``
    (the inline convention used by the SPICE→TAS reader and round-trip
    fixtures).
    """
    out: list[dict[str, Any]] = []
    for stage in tas.get("stages", []):
        for c in stage.get("circuit", {}).get("components", []):
            if c.get("category") == "magnetic":
                out.append(c)
                continue
            data = c.get("data", "")
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
    """Stamp ``design.magnetic`` onto ``component['mas']`` (mutating)."""
    component.pop("data", None)
    component["category"] = "magnetic"
    component["mas"] = design.magnetic
    component["mas_scoring"] = design.scoring


def _tas_capacitor_components(tas: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return every TAS component declared as a capacitor.

    Mirror of :func:`_tas_magnetic_components` for the capacitor
    category. Recognises both the inline ``category == "capacitor"``
    convention and the stencil's placeholder ``data`` URL pointing at
    ``capacitors.ndjson``.
    """
    out: list[dict[str, Any]] = []
    for stage in tas.get("stages", []):
        for c in stage.get("circuit", {}).get("components", []):
            if c.get("category") == "capacitor":
                out.append(c)
                continue
            data = c.get("data", "")
            if isinstance(data, str) and "capacitors.ndjson" in data:
                out.append(c)
    return out


def _attach_one_capacitor(
    component: dict[str, Any], spec: ExtraCapacitorSpec
) -> None:
    """Stamp ``spec.inputs`` (a CAS::Inputs envelope) onto a TAS cap.

    The bridge does not pick capacitor MPNs — it only routes PyOM's
    pre-filled CAS::Inputs (designRequirements + operatingPoints) onto
    the corresponding TAS component as ``component["cas_inputs"]``.
    The downstream component-librarian agent reads ``cas_inputs`` and
    writes the chosen catalog entry back as ``component["cas"]``.
    """
    component.pop("data", None)
    component["category"] = "capacitor"
    component["cas_inputs"] = spec.inputs


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
        result = pyom.get_extra_components_inputs(
            variant,
            dict(converter_spec),
            mode,
            dict(main_magnetic_mas) if main_magnetic_mas is not None else None,
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
    result = pyom.calculate_advised_magnetics(
        dict(spec.inputs), int(max_results), str(core_mode)
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


def design_converter_components(
    topology: str | TopologyEntry,
    converter_spec: Mapping[str, Any],
    *,
    max_results: int = 1,
    core_mode: str = "available cores",
    use_ngspice: bool = False,
    weights: Mapping[str, float] | None = None,
) -> ConverterComponents:
    """End-to-end Phase A + Phase B for a converter spec.

    1. Design the main magnetic via :func:`design_magnetics`.
    2. Probe extras in REAL mode against that main magnetic.
    3. Design each extra magnetic via :func:`design_extra_magnetic`.
    4. Pass capacitor specs through untouched (bridge does not pick caps).

    All results bundled into a :class:`ConverterComponents` record.
    """
    entry = topology if isinstance(topology, TopologyEntry) else get(topology)

    main_designs = design_magnetics(
        entry,
        converter_spec,
        max_results=max_results,
        core_mode=core_mode,
        use_ngspice=use_ngspice,
        weights=weights,
    )
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
