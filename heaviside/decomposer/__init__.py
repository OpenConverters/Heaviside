"""Decomposer package — SPICE parsing + the live Kirchhoff design seam.

The MKF-stencil decompose subsystem (per-topology stencils that mapped an
MKF-emitted ngspice deck back to a TAS topology block) was removed in the
della-Pollock cutover: Kirchhoff now owns all converter design and deck
generation. The live members of this package are:

* :mod:`heaviside.decomposer.kirchhoff_adapter` — the Kirchhoff design seam.
* :mod:`heaviside.decomposer.spice_parser` — the SPICE deck parser.
* :data:`heaviside.decomposer.api.DEFAULT_SPICE_CONFIG` — canonical deck defaults.
"""

from heaviside.decomposer.api import DEFAULT_SPICE_CONFIG
from heaviside.decomposer.spice_parser import (
    SpiceDeck,
    SpiceElement,
    parse_spice,
)

__all__ = [
    "DEFAULT_SPICE_CONFIG",
    "SpiceDeck",
    "SpiceElement",
    "parse_spice",
]
