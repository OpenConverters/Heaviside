"""topology_id — identify / validate a converter topology.

Engine (deterministic, this module): the static feasibility screen
(``feasible``) and the canonical-name resolver (``resolve``) — pure physics
+ alias normalization, no LLM. ``feasible`` reuses the step-direction /
multi-output / AC-input screen in ``pipeline.topology_screen``; ``resolve``
reuses the alias/fuzzy normalizer the RE pipeline uses, so a topology
string from anywhere maps to one canonical registry name in one place.

LLM layer (``identify``): the topology selector — given a spec it returns
the viable topologies with reasoning, choosing among the physically
feasible set. It falls back to the deterministic screen when no LLM key is
configured (never invents an infeasible topology).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TopologyChoice:
    viable: list[str]  # canonical topology names, agent-preferred order first
    reasoning: str
    used_llm: bool
    static: list[str] = field(default_factory=list)  # the deterministic screen result


def canonical_names() -> list[str]:
    """Every canonical topology name in the registry."""
    from heaviside.topologies import TOPOLOGIES

    return sorted(t.name for t in TOPOLOGIES)


def resolve(raw: str) -> str:
    """Map any topology string (alias, spacing, casing) to its canonical
    registry name, or return it unchanged if nothing resolves."""
    from heaviside.pipeline.re_pipeline import _resolve_canonical_topology

    return _resolve_canonical_topology(raw)


def feasible(spec: Mapping[str, Any]) -> list[str]:
    """Deterministic engine: canonical names physically feasible for ``spec``
    (step direction, isolation/multi-output, AC vs DC input)."""
    from heaviside.pipeline.topology_screen import feasible_topology_names

    return feasible_topology_names(spec)


def identify(spec: Mapping[str, Any]) -> TopologyChoice:
    """LLM layer: viable topologies + reasoning, choosing among the feasible
    set. Falls back to the deterministic screen without an LLM key — the
    selector can never return an infeasible topology."""
    from heaviside.agents.topology_selector_llm import topology_selector_with_fallback

    static = feasible(spec)
    names, reasoning = topology_selector_with_fallback(spec)
    used_llm = names != static or bool(reasoning and reasoning != "static screen")
    return TopologyChoice(
        viable=list(names), reasoning=reasoning, used_llm=used_llm, static=static
    )
