"""LLM-based topology selector using the OpenAI-compatible API.

Reads the topology-selector prompt from ``agents/prompts/topology-selector.md``,
sends the spec to the configured LLM (Moonshot/Kimi default, any OpenAI-compatible
endpoint), and parses the JSON response.

Falls back to the static screen if no API key is configured — this is the
intentional "graceful degradation to deterministic" behaviour rather than a
silent fallback.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_system_prompt() -> str:
    """Read the topology-selector.md prompt, stripping YAML frontmatter."""
    path = _PROMPTS_DIR / "topology-selector.md"
    text = path.read_text()
    if text.startswith("---"):
        end = text.index("---", 3)
        text = text[end + 3:].strip()
    return text


def topology_selector_llm(
    spec: Mapping[str, Any],
) -> tuple[list[str], str]:
    """Call the LLM topology selector.

    Uses the OpenAI-compatible chat completions endpoint.
    Requires ``MOONSHOT_API_KEY`` (or ``OPENAI_API_KEY``) env var.

    Returns ``(viable_names, reasoning)`` — same shape as the static
    screen's return value so the reconciler can merge them.

    Raises ``LLMUnavailableError`` if no API key is set or the call fails.
    """
    api_key = (
        os.environ.get("MOONSHOT_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    if not api_key:
        raise LLMUnavailableError(
            "no MOONSHOT_API_KEY or OPENAI_API_KEY in environment"
        )

    base_url = os.environ.get(
        "HEAVISIDE_LLM_BASE_URL",
        "https://api.moonshot.cn/v1",
    )
    model = os.environ.get("HEAVISIDE_LLM_MODEL", "kimi-k2.5")

    try:
        import httpx
    except ImportError as exc:
        raise LLMUnavailableError("httpx not installed") from exc

    system_prompt = _load_system_prompt()
    user_message = json.dumps(dict(spec), indent=2)

    response = httpx.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.3,
            "max_tokens": 1024,
        },
        timeout=30.0,
    )

    if response.status_code != 200:
        raise LLMUnavailableError(
            f"LLM API returned {response.status_code}: {response.text[:200]}"
        )

    data = response.json()
    text = data["choices"][0]["message"]["content"]

    from heaviside.pipeline.full_design import _parse_topology_selector_response
    return _parse_topology_selector_response(text)


class LLMUnavailableError(RuntimeError):
    """Raised when the LLM topology selector can't be reached."""


def topology_selector_with_fallback(
    spec: Mapping[str, Any],
) -> tuple[list[str], str]:
    """Try the LLM selector; fall back to static screen on any error.

    This is the function wired into ``full_design()`` as the default
    ``selector_fn`` when an API key is available.
    """
    try:
        return topology_selector_llm(spec)
    except (LLMUnavailableError, Exception) as exc:
        logger.warning(
            "LLM topology selector unavailable (%s) — using static screen",
            exc,
        )
        from heaviside.pipeline.topology_screen import feasible_topology_names
        names = feasible_topology_names(spec)
        return names, f"LLM unavailable ({type(exc).__name__}); mirrored static screen"
