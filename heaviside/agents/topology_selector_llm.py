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
