"""Static topology-feasibility screen.

Given a converter spec, return the set of registry topologies that
could physically realise it. The check is conservative — it rejects
topologies that violate hard physics (buck cannot step up, boost
cannot step down) but admits anything else.

This is the deterministic half of the topology-gate step in the
della Pollock multi-stage flow (see ``heaviside.pipeline.full_design``).
The LLM-driven half lives in
``heaviside/agents/prompts/topology-selector.md`` and the two are
reconciled by the orchestrator: anything either side admits is
included; large disagreement (> 50 %) raises a warning so we can
audit one of the two checks.

No magnetics math here. Just topology bookkeeping (step direction,
isolation requirement, output count).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from heaviside.topologies.registry import TOPOLOGIES, TopologyEntry


class TopologyScreenError(ValueError):
    """Raised when the spec is too malformed to even attempt the screen."""


# ---------------------------------------------------------------------------
# Family-level capability flags
# ---------------------------------------------------------------------------
#
# Each capability is a hard physical constraint. A topology is admitted if
# *all* required spec conditions are compatible with its family's flags.

# Step direction: which Vout/Vin ratios the family can physically deliver.
#   "step_down"   = Vout < Vin always (buck, forward family, isolated_buck)
#   "step_up"     = Vout > Vin always (boost)
#   "step_either" = Vout can be < or > Vin (cuk, sepic, zeta, 4sbb, flyback,
#                                            iso-buck-boost, AHB, LLC, etc.)
_STEP_DIRECTION_BY_FAMILY: dict[str, str] = {
    "non_isolated": "step_either",        # most non-iso are buck-boost class;
                                          # buck/boost themselves get overridden below
    "isolated_single_switch": "step_either",  # flyback / fwd / iso-buck-boost
    "isolated_two_switch": "step_either",     # two-switch fwd, AHB
    "isolated_push_pull": "step_either",      # push-pull / weinberg
    "isolated_bridge": "step_either",         # PSFB / PSHB / DAB
    "resonant": "step_either",            # LLC / CLLC / etc.
    "ac_dc": "step_either",               # Vienna / PFC have AC input
}

# Per-topology overrides for step direction (where the family default is wrong).
_STEP_DIRECTION_OVERRIDES: dict[str, str] = {
    "buck": "step_down",
    "boost": "step_up",
    "isolated_buck": "step_down",
    "single_switch_forward": "step_down",
    "two_switch_forward": "step_down",
    "active_clamp_forward": "step_down",
    "push_pull": "step_down",          # Vout(reflected) < Vin·N for typical n
    "weinberg": "step_down",
    "phase_shifted_full_bridge": "step_down",
    "phase_shifted_half_bridge": "step_down",
    # All resonant tanks can step either way depending on turns ratio.
}

# AC-input topologies — these need a line-frequency or RMS input, not a
# DC ``inputVoltage`` envelope. The CLI's normal DC spec doesn't fit them.
_AC_INPUT_TOPOLOGIES: frozenset[str] = frozenset({
    "vienna",
    "power_factor_correction",
})

# Topologies whose spec contract is so specialised that the screen
# refuses to evaluate them — caller must include them explicitly.
_REQUIRES_EXPLICIT_OPT_IN: frozenset[str] = frozenset({
    "common_mode_choke",
    "differential_mode_choke",
    "current_transformer",
})


# ---------------------------------------------------------------------------
# Spec readers
# ---------------------------------------------------------------------------


def _resolve_vin_range(spec: Mapping[str, Any]) -> tuple[float, float]:
    """Return ``(vin_min, vin_max)`` from the spec. Per CLAUDE.md
    no-fallback rule — throw if unset rather than guess.

    For AC-input topologies (PFC, Vienna) the relevant field is
    ``lineToLineVoltage`` not ``inputVoltage`` — accept either.
    """
    iv = spec.get("inputVoltage") or spec.get("lineToLineVoltage")
    if isinstance(iv, Mapping):
        vmin = iv.get("minimum")
        vmax = iv.get("maximum")
        nom = iv.get("nominal")
        # Allow nominal-only specs to imply ±25 % bounds (matches the
        # corpus-runner's enrichment) — the screen is permissive on
        # missing data.
        if not isinstance(vmin, (int, float)) and isinstance(nom, (int, float)):
            vmin = 0.75 * nom
        if not isinstance(vmax, (int, float)) and isinstance(nom, (int, float)):
            vmax = 1.25 * nom
        if isinstance(vmin, (int, float)) and isinstance(vmax, (int, float)):
            return float(vmin), float(vmax)
    if isinstance(iv, (int, float)):
        return 0.75 * float(iv), 1.25 * float(iv)
    raise TopologyScreenError(
        "spec.inputVoltage: unable to derive (min, max) — expected "
        f"{{minimum, maximum}} or {{nominal}} or a scalar, got {iv!r}"
    )


def _resolve_vout(spec: Mapping[str, Any]) -> float:
    """Return the first operating point's first output voltage."""
    ops = spec.get("operatingPoints")
    if not isinstance(ops, list) or not ops:
        raise TopologyScreenError(
            "spec.operatingPoints: missing or empty"
        )
    op = ops[0]
    if not isinstance(op, Mapping):
        raise TopologyScreenError(
            f"spec.operatingPoints[0]: expected mapping, got {type(op).__name__}"
        )
    vouts = op.get("outputVoltages")
    if not isinstance(vouts, list) or not vouts:
        raise TopologyScreenError(
            "spec.operatingPoints[0].outputVoltages: missing or empty"
        )
    vout = vouts[0]
    if isinstance(vout, Mapping):
        for key in ("nominal", "minimum", "maximum"):
            v = vout.get(key)
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
        raise TopologyScreenError(
            f"spec.operatingPoints[0].outputVoltages[0]: no usable scalar in {vout!r}"
        )
    if isinstance(vout, (int, float)) and vout > 0:
        return float(vout)
    raise TopologyScreenError(
        f"spec.operatingPoints[0].outputVoltages[0]: expected number, got {vout!r}"
    )


def _n_outputs(spec: Mapping[str, Any]) -> int:
    """Number of distinct outputs in the first operating point."""
    ops = spec.get("operatingPoints") or []
    if not ops:
        return 0
    op = ops[0]
    vouts = op.get("outputVoltages") if isinstance(op, Mapping) else None
    return len(vouts) if isinstance(vouts, list) else 0


# ---------------------------------------------------------------------------
# Per-topology gate
# ---------------------------------------------------------------------------


def _is_topology_feasible(
    entry: TopologyEntry,
    *,
    vin_min: float,
    vin_max: float,
    vout: float,
    n_outputs: int,
    is_ac_input: bool,
) -> bool:
    """True if ``entry`` could physically deliver this spec."""
    if entry.kind != "converter":
        return False
    if entry.name in _REQUIRES_EXPLICIT_OPT_IN:
        return False

    is_ac_topo = entry.name in _AC_INPUT_TOPOLOGIES
    if is_ac_topo != is_ac_input:
        return False  # AC topology needs AC spec; DC topology needs DC spec

    step_dir = _STEP_DIRECTION_OVERRIDES.get(
        entry.name, _STEP_DIRECTION_BY_FAMILY.get(entry.family, "step_either"),
    )

    # Step direction must be compatible across the WHOLE Vin range.
    # For step_down: Vout < vin_min so even the worst-case input can step
    # down. For step_up: Vout > vin_max. Anything in between only works
    # for step_either topologies.
    if step_dir == "step_down" and vout >= vin_min:
        return False
    if step_dir == "step_up" and vout <= vin_max:
        return False

    # Multi-output: only isolated topologies with declared secondaries
    # can fan out. Buck/boost/cuk/sepic/zeta are single-output (cuk's
    # second inductor is the output inductor, not a second output).
    if n_outputs > 1 and entry.family == "non_isolated":
        return False

    return True


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def feasible_topologies(spec: Mapping[str, Any]) -> list[TopologyEntry]:
    """Return registry topologies that could physically deliver ``spec``.

    Conservative: admits anything the hard physics doesn't rule out.
    The LLM ``topology-selector`` agent does the ranking / preferred-set
    refinement; this function's job is just to cut the obvious ones.
    """
    vin_min, vin_max = _resolve_vin_range(spec)
    vout = _resolve_vout(spec)
    n_outs = _n_outputs(spec)
    is_ac_input = isinstance(spec.get("lineToLineVoltage"), Mapping) or isinstance(
        spec.get("lineVoltage"), Mapping,
    )

    return [
        t for t in TOPOLOGIES
        if _is_topology_feasible(
            t,
            vin_min=vin_min,
            vin_max=vin_max,
            vout=vout,
            n_outputs=n_outs,
            is_ac_input=is_ac_input,
        )
    ]


def feasible_topology_names(spec: Mapping[str, Any]) -> list[str]:
    """Convenience: just the names (canonical, ordered as in registry)."""
    return [t.name for t in feasible_topologies(spec)]


# ---------------------------------------------------------------------------
# Cross-validation between static + LLM topology screens
# ---------------------------------------------------------------------------


from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TopologyReconciliation:
    """Outcome of fusing the static screen with the LLM topology-selector.

    Attributes
    ----------
    chosen : tuple[str, ...]
        Final topology set (union of static + agent). Ordered by
        agent preference where overlap exists, then by registry
        order for static-only additions.
    static_only : tuple[str, ...]
        Names the static screen admitted but the agent didn't.
        Useful for spotting prompt-shaping bugs.
    agent_only : tuple[str, ...]
        Names the agent recommended but the static screen rejected.
        Either the static rules need an exception OR the agent is
        recommending a topology that won't physically work.
    jaccard_disagreement : float
        ``1 - |A∩B|/|A∪B|``. 0 = perfect agreement, 1 = no overlap.
    warning : str | None
        Non-None if the two paths disagree on > 50 % of the union.
        Surface in caller logs / agent reports so prompts / static
        rules can be audited.
    """
    chosen: tuple[str, ...]
    static_only: tuple[str, ...]
    agent_only: tuple[str, ...]
    jaccard_disagreement: float
    warning: str | None


def reconcile_topology_choices(
    static_names: list[str],
    agent_names: list[str],
    *,
    disagreement_threshold: float = 0.5,
) -> TopologyReconciliation:
    """Fuse the deterministic-screen and LLM-screen outputs.

    Strategy
    --------
    * **Chosen** = union, ordered by agent preference first (most-
      preferred topologies the agent suggested), then by registry
      order for any static-only stragglers. Permissive on purpose:
      Phase 2 is cheap, missing a viable topology here is worse than
      attempting an extra one.
    * Disagreement is reported via Jaccard distance (1 - |A∩B|/|A∪B|).
      Above ``disagreement_threshold`` (default 0.5) the
      ``warning`` field is populated so callers can log + audit the
      drift.
    """
    s, a = set(static_names), set(agent_names)
    static_only = tuple(n for n in static_names if n not in a)
    agent_only = tuple(n for n in agent_names if n not in s)

    # Order: agent's order for shared+agent-only, then static-only fallback.
    seen: set[str] = set()
    chosen: list[str] = []
    for n in agent_names:
        if n not in seen:
            chosen.append(n)
            seen.add(n)
    for n in static_names:
        if n not in seen:
            chosen.append(n)
            seen.add(n)

    union = s | a
    inter = s & a
    jaccard = 1.0 - (len(inter) / len(union)) if union else 0.0
    warning: str | None = None
    if jaccard > disagreement_threshold and union:
        warning = (
            f"static vs agent topology screen disagree on "
            f"{jaccard:.0%} of the union — review prompt/rules. "
            f"static-only={static_only!r} agent-only={agent_only!r}"
        )

    return TopologyReconciliation(
        chosen=tuple(chosen),
        static_only=static_only,
        agent_only=agent_only,
        jaccard_disagreement=jaccard,
        warning=warning,
    )
