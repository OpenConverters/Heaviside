"""Lightweight LLM call for agent prompts.

Sends a system prompt + user message to the configured LLM endpoint
and returns the raw text response. Used by CRE and CR pipeline stages
that need LLM judgment.

Provider: Moonshot/Kimi default, any OpenAI-compatible endpoint via
HEAVISIDE_LLM_BASE_URL / HEAVISIDE_LLM_MODEL env vars.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


class LLMCallError(RuntimeError):
    """Raised when an LLM call fails (no API key, bad response, etc.)."""


def load_prompt(name: str) -> str:
    """Read an agent prompt file, stripping YAML frontmatter."""
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise LLMCallError(f"prompt not found: {path}")
    text = path.read_text()
    if text.startswith("---"):
        try:
            end = text.index("---", 3)
            text = text[end + 3:].strip()
        except ValueError:
            pass
    return text


def call_llm(
    system_prompt: str,
    user_message: str,
    *,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> str:
    """Send a chat completion request and return the assistant's text.

    Raises ``LLMCallError`` if no API key is configured or the
    request fails.
    """
    api_key = (
        os.environ.get("MOONSHOT_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    if not api_key:
        raise LLMCallError(
            "no MOONSHOT_API_KEY or OPENAI_API_KEY in environment"
        )

    base_url = os.environ.get(
        "HEAVISIDE_LLM_BASE_URL",
        "https://api.moonshot.ai/v1",
    )
    model = os.environ.get("HEAVISIDE_LLM_MODEL", "kimi-k2.5")

    try:
        import httpx
    except ImportError as exc:
        raise LLMCallError("httpx not installed") from exc

    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": max_tokens,
    }
    # Reasoning models (k2.5+) only accept temperature=1; omit it.
    if "k2" not in model:
        body["temperature"] = temperature

    try:
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=600.0,
        )
    except httpx.HTTPError as exc:
        raise LLMCallError(f"HTTP error: {exc}") from exc

    if response.status_code != 200:
        raise LLMCallError(
            f"LLM API returned {response.status_code}: "
            f"{response.text[:300]}"
        )

    data = response.json()
    try:
        msg = data["choices"][0]["message"]
        content = msg.get("content") or ""
        # kimi-k2.5 reasoning models may put the real output in
        # reasoning_content with an empty content field
        if not content:
            content = msg.get("reasoning_content") or ""
    except (KeyError, IndexError) as exc:
        raise LLMCallError(
            f"unexpected response shape: {json.dumps(data)[:300]}"
        ) from exc
    if not content:
        raise LLMCallError(
            f"LLM returned empty content (finish_reason="
            f"{data['choices'][0].get('finish_reason', '?')})"
        )
    return content


def call_agent(
    agent_name: str,
    user_message: str,
    *,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> str:
    """Load a named agent prompt and call the LLM with it."""
    system_prompt = load_prompt(agent_name)
    return call_llm(
        system_prompt, user_message,
        temperature=temperature, max_tokens=max_tokens,
    )


def call_agent_json(
    agent_name: str,
    user_message: str,
    *,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    max_retries: int = 2,
) -> dict[str, Any]:
    """Call an agent and parse its JSON response, with retries.

    On parse failure (no JSON block or invalid JSON), retries up to
    ``max_retries`` times with a slightly higher temperature to
    encourage a different output format. Raises ``LLMCallError``
    if all attempts fail.
    """
    last_error: LLMCallError | None = None
    for attempt in range(1 + max_retries):
        try:
            t = temperature + (attempt * 0.1)
            raw = call_agent(
                agent_name, user_message,
                temperature=min(t, 1.0), max_tokens=max_tokens,
            )
            return extract_json_block(raw)
        except LLMCallError as exc:
            last_error = exc
            logger.warning(
                "call_agent_json(%s) attempt %d/%d failed: %s",
                agent_name, attempt + 1, 1 + max_retries, exc,
            )
    raise last_error  # type: ignore[misc]


def _repair_truncated_json(text: str) -> str:
    """Try to close truncated JSON by adding missing brackets/braces."""
    text = text.rstrip()
    # If we're mid-string, drop back to the last complete string
    if text.count('"') % 2 != 0:
        last_quote = text.rfind('"')
        text = text[:last_quote]
    # Drop back to the last complete array/object element
    # (remove trailing partial value after the last comma or bracket)
    text = text.rstrip()
    while text and text[-1] not in '",}]':
        text = text[:-1]
    # Remove trailing comma
    text = text.rstrip().rstrip(",")
    # Count open brackets/braces and close them
    opens = 0
    open_sq = 0
    for ch in text:
        if ch == "{":
            opens += 1
        elif ch == "}":
            opens -= 1
        elif ch == "[":
            open_sq += 1
        elif ch == "]":
            open_sq -= 1
    text += "]" * max(0, open_sq) + "}" * max(0, opens)
    return text


def extract_json_block(text: str) -> dict[str, Any]:
    """Extract the first fenced JSON block from LLM output.

    Handles truncated JSON (from max_tokens cutoff) by attempting
    to close open brackets/braces. Raises ``LLMCallError`` if no
    valid JSON block is found.
    """
    import re
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not match:
        match = re.search(r"(\{.*\})", text, re.DOTALL)
    if not match:
        # Try to find truncated JSON (starts with { but never closes)
        trunc = re.search(r"```(?:json)?\s*(\{.+)", text, re.DOTALL)
        if not trunc:
            trunc = re.search(r"(\{.+)", text, re.DOTALL)
        if trunc:
            repaired = _repair_truncated_json(trunc.group(1))
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass
        raise LLMCallError(
            f"no JSON block in LLM response: {text[:200]!r}"
        )
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        # Try repair on malformed but complete-looking JSON
        repaired = _repair_truncated_json(match.group(1))
        try:
            return json.loads(repaired)
        except json.JSONDecodeError as exc:
            raise LLMCallError(
                f"JSON parse failed: {exc}. Block: {match.group(1)[:200]!r}"
            ) from exc


__all__ = [
    "LLMCallError",
    "call_agent",
    "call_agent_json",
    "call_llm",
    "extract_json_block",
    "load_prompt",
]
