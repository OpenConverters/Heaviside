"""LLM-based topology selector.

Runs the ``topology-selector`` agent prompt through the shared
:func:`heaviside.agents.llm_call.call_agent` path (Moonshot/Kimi default,
any OpenAI-compatible endpoint via ``HEAVISIDE_LLM_BASE_URL`` /
``HEAVISIDE_LLM_MODEL``) and parses the JSON response. There is no
separate HTTP client here — ``call_agent`` owns provider quirks
(reasoning-model temperature, ``reasoning_content`` fallback, token
accounting) for every agent.

Falls back to the static screen if no API key is configured — this is the
intentional "graceful degradation to deterministic" behaviour rather than a
silent fallback.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)


class LLMUnavailableError(RuntimeError):
    """Raised when the LLM topology selector can't be reached."""


def topology_selector_llm(
    spec: Mapping[str, Any],
) -> tuple[list[str], str]:
    """Call the LLM topology selector.

    Requires ``MOONSHOT_API_KEY`` (or ``OPENAI_API_KEY``) env var.

    Returns ``(viable_names, reasoning)`` — same shape as the static
    screen's return value so the reconciler can merge them.

    Raises ``LLMUnavailableError`` if no API key is set or the call fails.
    """
    from heaviside.agents.llm_call import LLMCallError, call_agent
    from heaviside.pipeline.jev_decisions import jev_enabled

    if jev_enabled():
        from heaviside.llm.jev import JevError

        try:
            return _jev_topology_selector(spec)
        except JevError as exc:
            raise LLMUnavailableError(str(exc)) from exc

    try:
        raw = call_agent(
            "topology-selector",
            json.dumps(dict(spec), indent=2),
            max_tokens=1024,
        )
    except LLMCallError as exc:
        raise LLMUnavailableError(str(exc)) from exc

    from heaviside.pipeline.full_design import _parse_topology_selector_response

    return _parse_topology_selector_response(raw)


#: The registry names the topology-selector prompt allows, with the engineering
#: niche each one fills (the judgment the static screen cannot make).
_TOPOLOGY_NICHES: dict[str, str] = {
    "buck": "non-isolated step-down, the default for DC-DC step-down up to a few hundred watts",
    "boost": "non-isolated step-up",
    "cuk": "non-isolated inverting step-up/down with continuous input and output current",
    "sepic": "non-isolated step-up/down, non-inverting, low to moderate power",
    "zeta": "non-isolated step-up/down, non-inverting, continuous output current",
    "four_switch_buck_boost": "non-isolated step-up/down across an input range that straddles the output",
    "flyback": "isolated, single or multiple outputs, up to about 150 W",
    "single_switch_forward": "isolated, about 50–300 W",
    "two_switch_forward": "isolated, about 100–500 W, higher input voltage",
    "active_clamp_forward": "isolated, about 50–500 W, high efficiency",
    "push_pull": "isolated, low input voltage and high current",
    "isolated_buck": "isolated auxiliary/bias supplies, a few watts, multiple outputs",
    "isolated_buck_boost": "isolated low-power bias supplies",
    "asymmetric_half_bridge": "isolated, a few hundred watts, soft switching",
    "phase_shifted_full_bridge": "isolated, high power (above about 500 W)",
    "phase_shifted_half_bridge": "isolated, medium-high power",
    "weinberg": "isolated, high power, space and satellite buses",
    "llc": "isolated resonant, high efficiency, medium-high power, narrow input range",
    "cllc": "isolated resonant and bidirectional",
    "clllc": "isolated resonant and bidirectional, symmetric",
    "series_resonant": "isolated resonant, high-voltage outputs",
    "dual_active_bridge": "isolated bidirectional power transfer",
    "power_factor_correction": "AC input, single-phase, power factor correction front end",
    "vienna": "AC input, three-phase, power factor correction rectifier",
}


def _jev_topology_selector(spec: Mapping[str, Any]) -> tuple[list[str], str]:
    """One Jev yes/no per registry topology; viable = P > 0.5, most likely first."""
    from heaviside.llm.jev import decide, noul_question, noul_values

    op = (spec.get("operatingPoints") or [{}])[0]
    vouts, iouts = op.get("outputVoltages") or [], op.get("outputCurrents") or []
    power = sum(float(v) * float(i) for v, i in zip(vouts, iouts))
    state = {"spec": dict(spec), "output_power_W": round(power, 3),
             "number_of_outputs": len(vouts)}
    qs = {name: noul_question(
        f"Is the `{name}` topology ({niche}) an engineering-appropriate choice for this converter "
        "`spec` (input voltage range, AC or DC input, isolation, `output_power_W`, "
        "`number_of_outputs`, switching frequency)?",
        true=("Yes: an engineer would shortlist it — it can meet the step direction, isolation and "
              "input type, and it suits this power level without needless complexity."),
        false=("No: it cannot meet the step direction, isolation or input type, or it is badly "
               "sized for this power (e.g. a full bridge for 20 W, a flyback for 2 kW)."))
        for name, niche in _TOPOLOGY_NICHES.items()}
    probs = noul_values(decide(state, qs), list(qs))
    from heaviside.llm.usage import record_avoided

    record_avoided("topology_selector", "topology-selector",
                   payload_chars=len(json.dumps(state, default=str)), output_tokens=200)
    ranked = sorted((n for n, p in probs.items() if p > 0.5), key=lambda n: -probs[n])[:6]
    reasoning = "Decision model P(viable): " + ", ".join(f"{n} {probs[n]:.2f}" for n in ranked)
    return ranked, reasoning


def topology_selector_with_fallback(
    spec: Mapping[str, Any],
) -> tuple[list[str], str]:
    """Try the LLM selector; fall back to static screen on any error.

    This is the function wired into ``full_design()`` as the default
    ``selector_fn`` when an API key is available.
    """
    try:
        return topology_selector_llm(spec)
    except Exception as exc:
        logger.warning(
            "LLM topology selector unavailable (%s) — using static screen",
            exc,
        )
        from heaviside.pipeline.topology_screen import feasible_topology_names

        names = feasible_topology_names(spec)
        return names, f"LLM unavailable ({type(exc).__name__}); mirrored static screen"
