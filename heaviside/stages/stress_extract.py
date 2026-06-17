"""stress_extract — per-component electrical stress (pure, no LLM).

Two deterministic paths over the existing engines, exposed as one stage:

- ``analytical`` / ``analytical_per_op``: worst-case stresses derived from
  the spec + topology alone (``pipeline.stress.derive_stresses`` — Vds/Id on
  the switch, Vr/If on the diode, V_working/I_ripple on the caps). Used to
  size parts before any simulation exists.
- ``from_simulation``: per-component V/I stress read off the simulation
  waveforms of a ``REState`` (``re_testbench.extract_component_stress``).

Both return the existing engineering types unchanged so the realism gate and
the selector consume one shape. There is no LLM layer — stress is physics.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from heaviside.pipeline.crossref import SimDerivedStress
    from heaviside.pipeline.stress import ComponentStresses


def analytical(topology: str, spec: Mapping[str, Any]) -> ComponentStresses | None:
    """Worst-case component stresses across all operating points for
    ``topology``. Returns None when no per-topology deriver is registered
    (the realism gate then marks the derating checks UNAVAILABLE)."""
    from heaviside.pipeline.stress import derive_stresses

    return derive_stresses(topology, spec)


def analytical_per_op(topology: str, spec: Mapping[str, Any]) -> list[ComponentStresses] | None:
    """One ``ComponentStresses`` per operating point (None if no deriver)."""
    from heaviside.pipeline.stress import derive_stresses_per_op

    return derive_stresses_per_op(topology, spec)


def from_simulation(state: Any) -> dict[str, SimDerivedStress]:
    """Per-component stress read off a ``REState``'s simulation waveforms,
    keyed by ref_des. Components with no role mapping or no sim data get no
    entry (never estimated — CLAUDE.md: no heuristic stand-ins)."""
    from heaviside.pipeline.re_testbench import extract_component_stress

    return extract_component_stress(state)
