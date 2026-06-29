"""Decomposer constants shared by the live SPICE-sim path.

The MKF-stencil decompose subsystem (``generate_netlist`` /
``decompose_netlist`` / ``decompose_from_spec`` + the per-topology
stencils) was removed in the della-Pollock cutover — Kirchhoff now owns
all converter design and deck generation (see
``heaviside.decomposer.kirchhoff_adapter``). What remains here is the
canonical SPICE-deck default config, which the sim runner documents as
the source of its snubber/diode/switch values.
"""

from __future__ import annotations

from typing import Any

# Realistic SPICE-deck defaults. The sim runner
# (``heaviside.sim.runner``) documents this as the canonical source of
# its snubber/diode/switch values:
#
#   * snubR=100 Ω burns ~25 W on a 60 W deck — use 10 kΩ that still
#     damps switch ringing.
#   * diodeRS=1 µΩ makes DIDEAL behave as a short — use a Schottky-grade
#     model instead.
DEFAULT_SPICE_CONFIG: dict[str, Any] = {
    "snubR": 10_000.0,
    "snubC": 100e-12,
    "diodeIS": 1e-12,
    "diodeRS": 0.05,
    "switchRON": 0.05,
}
