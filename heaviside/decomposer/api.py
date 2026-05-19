"""High-level decomposer API.

Three public entry points:

* :func:`generate_netlist` — call ``PyOpenMagnetics.generate_ngspice_circuit``
  with a converter spec and return the raw SPICE deck string.
* :func:`decompose_netlist` — parse a SPICE deck + apply a topology stencil
  → topology-shaped ``{"stages": ..., "interStageCircuit": ...}`` dict
  (the inner ``topology`` block of a TAS document).
* :func:`decompose_from_spec` — the full pipeline, returning ``(netlist, tas)``
  where ``tas`` is the fully wrapped TAS document
  ``{"inputs": ..., "topology": {"stages": ..., "interStageCircuit": ...}}``.

All three raise loudly on any error (PyOM engine error, parser failure,
stencil mismatch) — no silent fallbacks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from heaviside.decomposer.inputs_mapper import InputsMappingError, build_tas_inputs
from heaviside.decomposer.spice_parser import SpiceDeck, parse_spice
from heaviside.decomposer.stencils import get_stencil


class DecomposerError(RuntimeError):
    """Raised when MKF→TAS decomposition fails at any stage."""


def _import_pyom() -> Any:
    from PyOpenMagnetics import PyOpenMagnetics as _ext
    return _ext


def generate_netlist(
    topology: str,
    converter_json: Mapping[str, Any],
    turns_ratios: Sequence[float],
    magnetizing_inductance: float,
    *,
    vin_index: int = 0,
    op_index: int = 0,
    bridge_simulation_mode: str = "",
) -> str:
    """Ask MKF to emit the canonical ngspice deck for ``topology``.

    ``bridge_simulation_mode`` selects how bridge topologies (LLC, PSFB,
    PSHB, DAB, Weinberg push-pull) model their switching cell:

    * ``""`` / ``"pulse"`` (default) — MKF emits a single PULSE voltage
      source in place of the bridge. Fast, but the per-switch detail is
      lost — the deck has no real MOSFETs to decompose.
    * ``"switch"`` — MKF emits real ``SW1`` switches with body diodes,
      snubbers, and 50%-complementary gate drives. Required when
      downstream tooling needs to size the bridge MOSFETs (e.g. the TAS
      decomposer's bridge stencils). Non-bridge topologies ignore it.

    Raises :class:`DecomposerError` if PyOpenMagnetics returns an error
    envelope or an unexpected response shape.
    """
    pyom = _import_pyom()
    result = pyom.generate_ngspice_circuit(
        topology,
        dict(converter_json),
        list(turns_ratios),
        float(magnetizing_inductance),
        int(vin_index),
        int(op_index),
        str(bridge_simulation_mode),
    )
    if not isinstance(result, dict):
        raise DecomposerError(
            f"generate_ngspice_circuit returned {type(result).__name__}, "
            f"expected dict for topology {topology!r}"
        )
    if "error" in result:
        raise DecomposerError(
            f"PyOpenMagnetics rejected {topology!r}: {result['error']}"
        )
    netlist = result.get("netlist")
    if not isinstance(netlist, str):
        raise DecomposerError(
            f"generate_ngspice_circuit response for {topology!r} has no "
            f"'netlist' field: keys={list(result)}"
        )
    return netlist


def decompose_netlist(topology: str, netlist: str) -> dict[str, Any]:
    """Parse ``netlist`` and apply the topology stencil to produce a TAS
    ``topology`` block (``{"stages": [...], "interStageCircuit": [...]}``).

    This is the inner block — to obtain a fully wrapped TAS document
    use :func:`decompose_from_spec` instead.
    """
    deck: SpiceDeck = parse_spice(netlist)
    stencil = get_stencil(topology)
    return stencil(deck)


def decompose_from_spec(
    topology: str,
    converter_json: Mapping[str, Any],
    turns_ratios: Sequence[float],
    magnetizing_inductance: float,
    *,
    vin_index: int = 0,
    op_index: int = 0,
    bridge_simulation_mode: str = "",
    output_names: Sequence[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Full pipeline: spec → MKF spice → wrapped TAS document.

    The returned ``tas`` is the fully wrapped TAS document required by
    the current TAS root schema:

    ``{"inputs": {designRequirements, operatingPoints},
       "topology": {stages, interStageCircuit}}``

    ``inputs`` is built from ``converter_json`` via
    :func:`heaviside.decomposer.inputs_mapper.build_tas_inputs`. ``topology``
    is the stencil's decomposition of the MKF-emitted netlist.

    See :func:`generate_netlist` for ``bridge_simulation_mode``.
    Returns ``(netlist, tas)`` so callers can fixture-lock both.

    Raises
    ------
    DecomposerError
        On any failure in netlist generation, parsing, stencil application,
        or inputs mapping.
    """
    netlist = generate_netlist(
        topology,
        converter_json,
        turns_ratios,
        magnetizing_inductance,
        vin_index=vin_index,
        op_index=op_index,
        bridge_simulation_mode=bridge_simulation_mode,
    )
    topology_block = decompose_netlist(topology, netlist)
    try:
        inputs_block = build_tas_inputs(converter_json, output_names=output_names)
    except InputsMappingError as exc:
        raise DecomposerError(
            f"cannot map spec to TAS inputs for topology {topology!r}: {exc}"
        ) from exc
    tas: dict[str, Any] = {"inputs": inputs_block, "topology": topology_block}
    return netlist, tas
