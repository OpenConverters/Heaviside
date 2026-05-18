"""``heaviside.topologies`` — one thin module per MKF topology.

Each topology module exports a single ``design(spec, *, use_ngspice=True)``
function that dispatches to PyOpenMagnetics. The module is intentionally
minimal — no physics lives here, only the dispatch glue.

The canonical registry of all 24 + 3 entries is ``heaviside.topologies.registry``.
"""

from __future__ import annotations

from heaviside.topologies.dispatch import ProcessConverterResult, design
from heaviside.topologies.registry import (
    CONVERTERS,
    MAGNETICS_ONLY,
    TOPOLOGIES,
    TopologyEntry,
    get,
    names,
)

__all__ = [
    "CONVERTERS",
    "MAGNETICS_ONLY",
    "TOPOLOGIES",
    "ProcessConverterResult",
    "TopologyEntry",
    "design",
    "get",
    "names",
]
