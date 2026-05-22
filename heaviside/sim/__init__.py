"""Heaviside simulation runner (Phase 4 v0.1).

Public surface: thin wrapper around ngspice for steady-state averages.
"""

from heaviside.sim.runner import (
    SimError,
    SimResult,
    simulate_steady_state,
    stamp_simulation_results,
)

__all__ = [
    "SimError",
    "SimResult",
    "simulate_steady_state",
    "stamp_simulation_results",
]
