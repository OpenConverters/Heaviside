"""MKF spice-netlist → TAS topology-stage decomposer.

Given a converter spec, this package can:

1. Ask PyOpenMagnetics to generate the canonical ngspice deck for the
   topology (``decomposer.api.generate_netlist``).
2. Parse that deck into a list of :class:`spice_parser.SpiceElement`
   objects, preserving section comments.
3. Apply a per-topology *stencil* (``decomposer.stencils``) that maps
   each circuit-relevant SPICE element to a TAS stage / component, then
   emits a TAS-shaped ``{"stages": [...], "interStageCircuit": [...]}``
   dict matching ``MAS/schemas/inputs/topologies/<topology>.json``.

Round-tripping (``decomposer.api.decompose_from_spec``) gives us a
deterministic SPICE → TAS pipeline that is fixture-locked in
``tests/regression/decomposer/``. If MKF refactors its netlist generator
the fixtures will diff loudly, which is the entire point.
"""

from heaviside.decomposer.api import (
    DecomposerError,
    decompose_from_spec,
    decompose_netlist,
    generate_netlist,
)
from heaviside.decomposer.spice_parser import (
    SpiceDeck,
    SpiceElement,
    parse_spice,
)

__all__ = [
    "DecomposerError",
    "SpiceDeck",
    "SpiceElement",
    "decompose_from_spec",
    "decompose_netlist",
    "generate_netlist",
    "parse_spice",
]
