"""High-level decomposer API.

Three public entry points:

* :func:`generate_netlist` — call ``PyOpenMagnetics.generate_ngspice_circuit``
  with a converter spec and return the raw SPICE deck string.
* :func:`decompose_netlist` — parse a SPICE deck + apply a topology stencil
  → topology-shaped ``{"stages": ..., "interStageConnections": ...}`` dict
  (the inner ``topology`` block of a TAS document).
* :func:`decompose_from_spec` — the full pipeline, returning ``(netlist, tas)``
  where ``tas`` is the fully wrapped TAS document
  ``{"inputs": ..., "topology": {"stages": ..., "interStageConnections": ...}}``.

All three raise loudly on any error (PyOM engine error, parser failure,
stencil mismatch) — no silent fallbacks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from heaviside.decomposer.inputs_mapper import InputsMappingError, build_tas_inputs
from heaviside.decomposer.spice_parser import SpiceDeck, parse_spice
from heaviside.decomposer.stencils import _attach_external_terminals, get_stencil


class DecomposerError(RuntimeError):
    """Raised when MKF→TAS decomposition fails at any stage."""


_HAS_SPICE_CONFIG: bool = False


def _import_pyom() -> Any:
    """PyOM access via the bridge gateway (vendor build preferred).

    ``heaviside.bridge._import_pyom_vendor`` loads the vendored ``.so``
    when present (it carries ``bridge_simulation_mode``) and applies the
    Heaviside settings to it — the vendor module has its own native
    settings state, so the gateway parity matters for the emitted decks.
    The capability check below stays here: it is a decomposer concern.
    """
    global _HAS_SPICE_CONFIG

    from heaviside.bridge import _import_pyom_vendor

    _ext = _import_pyom_vendor()
    doc = _ext.generate_ngspice_circuit.__doc__ or ""
    if "bridge_simulation_mode" not in doc:
        raise DecomposerError(
            "PyOpenMagnetics.generate_ngspice_circuit lacks the "
            "bridge_simulation_mode parameter — you are running against a "
            "PyPI wheel (likely 1.3.10/1.3.12) rather than the vendored "
            "build. Install the vendor wheel: "
            "`pip install vendor/PyOpenMagnetics/dist/*.whl` (or run "
            "`python -m build --wheel` inside vendor/PyOpenMagnetics first). "
            "See HANDOFF.md for the 1.3.12 upstream regression details."
        )
    _HAS_SPICE_CONFIG = "spice_config" in doc
    return _ext


# Realistic SPICE-deck defaults applied via MKF's SpiceSimulationConfig
# override. MKF's per-topology defaults bake in lossy values that
# dominate measured efficiency on small converters:
#
#   * snubR=100 Ω — burns ~25 W on a 60 W buck deck
#   * diodeRS=1 µΩ — DIDEAL behaves as a short circuit at conduction
#
# Replacing them with realistic values (10 kΩ snubber R that still
# damps switch ringing; a Schottky-grade diode model) makes sim
# results match analytical efficiency. Passed verbatim into PyOM's
# spice_config dict by every call here.
DEFAULT_SPICE_CONFIG: dict[str, Any] = {
    "snubR": 10_000.0,
    "snubC": 100e-12,
    "diodeIS": 1e-12,
    "diodeRS": 0.05,
    "switchRON": 0.05,
}


def _patch_spice_defaults(netlist: str, cfg: dict[str, Any]) -> str:
    """Post-process a netlist to apply realistic snubber/diode values.

    Used when the PyOM binding lacks the spice_config parameter.
    """
    import re

    snub_r = cfg.get("snubR", 10_000.0)
    snub_c = cfg.get("snubC", 100e-12)
    diode_is = cfg.get("diodeIS", 1e-12)
    diode_rs = cfg.get("diodeRS", 0.05)

    netlist = re.sub(
        r"^(Rsnub_\w+\s+\S+\s+\S+\s+)[\d.eE+\-]+",
        rf"\g<1>{snub_r:.6f}",
        netlist,
        flags=re.MULTILINE,
    )
    netlist = re.sub(
        r"^(Csnub_\w+\s+\S+\s+\S+\s+)[\d.eE+\-]+",
        rf"\g<1>{snub_c:.6e}",
        netlist,
        flags=re.MULTILINE,
    )
    netlist = re.sub(
        r"(\.model\s+DIDEAL\s+D\(IS=)[\d.eE+\-]+(\s+RS=)[\d.eE+\-]+",
        rf"\g<1>{diode_is:.6e}\g<2>{diode_rs:.6e}",
        netlist,
        flags=re.IGNORECASE,
    )
    # Add realistic RON and reduced VH to SW models
    sw_ron = cfg.get("switchRON", 0.05)
    netlist = re.sub(
        r"(\.model\s+SW\d+\s+SW\s+)VT=[\d.]+\s+VH=[\d.]+",
        rf"\1VT=2.500000 VH=0.100000 RON={sw_ron:.6f}",
        netlist,
        flags=re.IGNORECASE,
    )
    return netlist


def generate_netlist(
    topology: str,
    converter_json: Mapping[str, Any],
    turns_ratios: Sequence[float],
    magnetizing_inductance: float,
    *,
    vin_index: int = 0,
    op_index: int = 0,
    bridge_simulation_mode: str = "",
    spice_config: Mapping[str, Any] | None = None,
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
    cfg = dict(DEFAULT_SPICE_CONFIG)
    if spice_config:
        cfg.update(dict(spice_config))
    # MKF's generate_ngspice_circuit dispatch keys on the PyOM *circuit*
    # name, which for most topologies equals the canonical Python name but
    # diverges for the series-resonant converter (canonical
    # ``series_resonant`` → PyOM ``src``). Translate so the binding's
    # topology switch (which only knows ``src``/``advanced_src``) matches.
    # process_converter already resolves the alias on the design side; this
    # keeps the netlist-generation side consistent. The dual active bridge's
    # canonical name ``dual_active_bridge`` maps to the binding's ``dab``
    # circuit key the same way SRC's ``series_resonant`` maps to ``src``.
    _NGSPICE_TOPOLOGY_ALIASES = {
        "series_resonant": "src",
        "dual_active_bridge": "dab",
    }
    ngspice_topology = _NGSPICE_TOPOLOGY_ALIASES.get(topology, topology)
    args: list[Any] = [
        ngspice_topology,
        dict(converter_json),
        list(turns_ratios),
        float(magnetizing_inductance),
        int(vin_index),
        int(op_index),
        str(bridge_simulation_mode),
    ]
    if _HAS_SPICE_CONFIG:
        args.append(cfg)
    result = pyom.generate_ngspice_circuit(*args)
    # Post-process the netlist to apply realistic snubber/diode/switch
    # values. This runs UNCONDITIONALLY — even when the binding accepts
    # spice_config natively — because MKF's native config is incomplete
    # for the energy-storage topologies (buck/cuk/flyback/sepic/zeta):
    # their switch model is emitted without a RON term (so RON falls back
    # to ngspice's 1 Ω default), and the snubber damping resistor
    # (snubDampR) is not exposed by the PyOM dict→struct mapping. The
    # post-patch's regexes set RON, VH, snubber R (both the series and
    # damping resistors) and the diode model from cfg, reproducing the
    # canonical decks the goldens pin. Drop this once MKF emits swModelRON
    # for those topologies and the binding maps snubDampR.
    if isinstance(result, dict) and "netlist" in result:
        result["netlist"] = _patch_spice_defaults(result["netlist"], cfg)
    if not isinstance(result, dict):
        raise DecomposerError(
            f"generate_ngspice_circuit returned {type(result).__name__}, "
            f"expected dict for topology {topology!r}"
        )
    if "error" in result:
        raise DecomposerError(f"PyOpenMagnetics rejected {topology!r}: {result['error']}")
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

    This is the inner block (``{"stages": [...], "interStageConnections": [...]}``).
    To obtain a fully wrapped TAS document use :func:`decompose_from_spec` instead.
    """
    deck: SpiceDeck = parse_spice(netlist)
    stencil = get_stencil(topology)
    topology_block = stencil(deck)
    _attach_external_terminals(topology_block)
    return topology_block


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
    spice_config: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Full pipeline: spec → MKF spice → wrapped TAS document.

    The returned ``tas`` is the fully wrapped TAS document required by
    the current TAS root schema:

    ``{"inputs": {designRequirements, operatingPoints},
       "topology": {stages, interStageConnections}}``

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
        spice_config=spice_config,
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
