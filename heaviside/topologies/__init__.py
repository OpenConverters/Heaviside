"""``heaviside.topologies`` — the canonical registry of converter + magnetic topologies.

The registry of all 24 + 3 entries is ``heaviside.topologies.registry`` (``get``/``names``/
``TOPOLOGIES``). The della-Pollock cutover (abt #48) retired the MKF converter models, so the
old per-topology ``design()`` dispatch glue (``process_converter``) is gone — Kirchhoff designs
every converter and MKF designs only magnetic geometry.
"""

from __future__ import annotations

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
    "TopologyEntry",
    "get",
    "names",
]
