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

_TOTAL_TOKENS: dict[str, int] = {"input": 0, "output": 0, "calls": 0}


def get_token_usage() -> dict[str, int]:
    """Return cumulative token usage since process start."""
    return dict(_TOTAL_TOKENS)


def reset_token_usage() -> None:
    """Reset token counters."""
    _TOTAL_TOKENS["input"] = 0
    _TOTAL_TOKENS["output"] = 0
    _TOTAL_TOKENS["calls"] = 0


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
            text = text[end + 3 :].strip()
        except ValueError:
            pass
    return text


def call_llm(
    system_prompt: str,
    user_message: str,
    *,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    json_mode: bool = False,
    model: str | None = None,
) -> str:
    """Send a chat completion request and return the assistant's text.

    ``model`` overrides the ``HEAVISIDE_LLM_MODEL`` env / ``kimi-k2.5``
    default (used by :func:`call_agent` to honour prompt-frontmatter
    model pins). Raises ``LLMCallError`` if no API key is configured or
    the request fails.
    """
    api_key = os.environ.get("MOONSHOT_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise LLMCallError("no MOONSHOT_API_KEY or OPENAI_API_KEY in environment")

    base_url = os.environ.get(
        "HEAVISIDE_LLM_BASE_URL",
        "https://api.moonshot.ai/v1",
    )
    if model is None:
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
    # Force a valid JSON object response (Moonshot/OpenAI-compatible). Needed
    # for reviewer agents: kimi-k2.5 otherwise emits <scratchpad> reasoning with
    # no parseable JSON block. The prompt must mention JSON (the reviewers do).
    if json_mode:
        body["response_format"] = {"type": "json_object"}

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
        raise LLMCallError(f"LLM API returned {response.status_code}: {response.text[:300]}")

    data = response.json()

    # Track token usage for cost reporting
    usage = data.get("usage", {})
    _TOTAL_TOKENS["input"] += usage.get("prompt_tokens", 0)
    _TOTAL_TOKENS["output"] += usage.get("completion_tokens", 0)
    _TOTAL_TOKENS["calls"] += 1

    try:
        msg = data["choices"][0]["message"]
        content = msg.get("content") or ""
        # kimi-k2.5 reasoning models may put the real output in
        # reasoning_content with an empty content field
        if not content:
            content = msg.get("reasoning_content") or ""
    except (KeyError, IndexError) as exc:
        raise LLMCallError(f"unexpected response shape: {json.dumps(data)[:300]}") from exc
    if not content:
        raise LLMCallError(
            f"LLM returned empty content (finish_reason="
            f"{data['choices'][0].get('finish_reason', '?')})"
        )
    return content


#: Agents whose verdicts gate the pipeline (Ray / Nicola / the CRE
#: reviewer). Their model must pass the tier policy's review-role gate —
#: an un-vetted model silently rubber-stamping designs is worse than a
#: loud refusal.
_REVIEW_ROLE_AGENTS: frozenset[str] = frozenset({"ray", "nicola", "reviewer"})


def _resolve_model_id(definition: Any) -> str:
    """Model precedence: env override > prompt frontmatter > project default."""
    from heaviside.agents.factory import DEFAULT_MODEL

    return os.environ.get("HEAVISIDE_LLM_MODEL") or definition.model or DEFAULT_MODEL


def _run_strands_agent(
    definition: Any,
    user_message: str,
    *,
    model_id: str,
    temperature: float,
    max_tokens: int,
    json_mode: bool,
) -> str:
    """Run a tool-using agent through Strands and return its final text.

    The agent gets the tools its prompt declares in ``allowed_tools`` and
    may take multiple tool-call turns before answering. Construction or
    run failures surface as :class:`LLMCallError` (chained) so callers
    keep a single failure type — there is no fallback to a tool-less
    single-shot call.

    Request params (``max_tokens`` / ``temperature`` / ``json_mode``) are
    applied on the Kimi/Moonshot route via the model builder. Non-Kimi
    model ids go to Strands' own provider routing, which manages its own
    request defaults.
    """
    from heaviside.agents.factory import load_agent
    from heaviside.llm import build_kimi_model, is_kimi_model

    params: dict[str, Any] = {"max_tokens": max_tokens}
    # Reasoning models (k2.5+) only accept temperature=1; omit it.
    if "k2" not in model_id:
        params["temperature"] = temperature
    if json_mode:
        params["response_format"] = {"type": "json_object"}

    try:
        if is_kimi_model(model_id):
            agent = load_agent(
                definition.name,
                model=model_id,
                kimi_model_builder=lambda *, model_id: build_kimi_model(
                    model_id=model_id, params=params
                ),
            )
        else:
            agent = load_agent(definition.name, model=model_id)
        result = agent(user_message)
    except LLMCallError:
        raise
    except Exception as exc:
        raise LLMCallError(f"strands agent {definition.name!r} failed: {exc}") from exc

    # Token accounting (Strands accumulates across tool-call turns).
    usage = getattr(getattr(result, "metrics", None), "accumulated_usage", None)
    if isinstance(usage, dict):
        _TOTAL_TOKENS["input"] += int(usage.get("inputTokens", 0))
        _TOTAL_TOKENS["output"] += int(usage.get("outputTokens", 0))
    _TOTAL_TOKENS["calls"] += 1

    text = str(result).strip()
    if not text:
        raise LLMCallError(f"strands agent {definition.name!r} returned empty final text")
    return text


def call_agent(
    agent_name: str,
    user_message: str,
    *,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    json_mode: bool = False,
) -> str:
    """Run a named agent prompt and return its final text.

    The prompt's YAML frontmatter decides the execution path:

    * ``allowed_tools`` non-empty → a Strands agent with those tools
      wired in (multi-turn tool calling, model-agnostic provider
      routing).
    * ``allowed_tools: []`` → a single-shot :func:`call_llm` chat
      completion (cheap; the agent works only from pre-fed context).

    In both paths the model id resolves env override
    (``HEAVISIDE_LLM_MODEL``) > frontmatter ``model:`` > project
    default, and review-role agents (Ray, Nicola, reviewer) are refused
    on models that fail :func:`heaviside.llm.is_review_role_allowed`.
    """
    from heaviside.agents.factory import AgentLoadError, load_agent_definition

    try:
        definition = load_agent_definition(agent_name)
    except AgentLoadError as exc:
        raise LLMCallError(f"agent prompt {agent_name!r} failed to load: {exc}") from exc

    model_id = _resolve_model_id(definition)

    if agent_name in _REVIEW_ROLE_AGENTS:
        from heaviside.llm import is_review_role_allowed

        if not is_review_role_allowed(model_id):
            raise LLMCallError(
                f"model {model_id!r} is not allowed in review role {agent_name!r} "
                "(heaviside/llm/model_tiers.json review_roles_allowed)"
            )

    if definition.allowed_tools:
        return _run_strands_agent(
            definition,
            user_message,
            model_id=model_id,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )

    return call_llm(
        definition.system_prompt,
        user_message,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=json_mode,
        model=model_id,
    )


def call_agent_json(
    agent_name: str,
    user_message: str,
    *,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    max_retries: int = 2,
    json_mode: bool = False,
) -> dict[str, Any]:
    """Call an agent and parse its JSON response, with retries.

    On parse failure (no JSON block or invalid JSON), retries up to
    ``max_retries`` times with a slightly higher temperature to
    encourage a different output format. ``json_mode`` forces the API to
    return a JSON object (needed for reasoning models that otherwise emit
    un-parseable scratchpad prose). Raises ``LLMCallError`` if all attempts
    fail.
    """
    last_error: LLMCallError | None = None
    for attempt in range(1 + max_retries):
        try:
            t = temperature + (attempt * 0.1)
            raw = call_agent(
                agent_name,
                user_message,
                temperature=min(t, 1.0),
                max_tokens=max_tokens,
                json_mode=json_mode,
            )
            return extract_json_block(raw)
        except LLMCallError as exc:
            last_error = exc
            logger.warning(
                "call_agent_json(%s) attempt %d/%d failed: %s",
                agent_name,
                attempt + 1,
                1 + max_retries,
                exc,
            )
    raise last_error  # type: ignore[misc]


_VALID_REVIEWER_VERDICTS = {"APPROVED", "REJECTED", "INCOMPLETE"}


def normalize_reviewer_verdict(data: dict[str, Any], reviewer_name: str) -> dict[str, Any]:
    """Validate + normalize a Ray/Nicola reviewer JSON verdict in place.

    The reviewer output contract is a JSON object with a string ``verdict``
    in {APPROVED, REJECTED, INCOMPLETE} plus an ``objections`` array. Under
    ``json_mode`` a reviewer that ignores the contract still returns *valid*
    JSON — it echoes the input dict, or dumps reasoning into ``{"scratchpad":
    ...}`` — which would otherwise sail through ``extract_json_block`` and be
    recorded as a real review. This raises ``LLMCallError`` on any
    contract violation so the caller's fail-loud path converts it into a
    pipeline error instead of a silent fake "review" (CLAUDE.md: no silent
    fallbacks).

    The reviewers' personas have strong native verdict vocabulary — Ray says
    "PROCEED WITH CAUTION" for grudging approval and "NOT ACCEPTABLE" for
    rejection; Nicola says "NOT_APPROVED". These ARE valid reviews, so they are
    mapped onto the canonical enum rather than aborting the pipeline. Only a
    genuinely missing/unparseable verdict (echo, scratchpad blob, or wording
    that maps to nothing) raises.
    """
    if not isinstance(data, dict):
        raise LLMCallError(
            f"{reviewer_name} verdict is {type(data).__name__}, expected a JSON object"
        )
    raw = data.get("verdict")
    if not isinstance(raw, str) or not raw.strip():
        raise LLMCallError(
            f"{reviewer_name} response has no string 'verdict' field "
            f"(keys={sorted(data)[:8]}) — reviewer did not follow the JSON "
            f"output contract (likely a json_mode echo/scratchpad blob)"
        )
    vu = raw.strip().upper()
    # Canonicalise the reviewers' natural verdict phrasing onto the enum.
    # Order matters: INCOMPLETE and the NOT-* rejections are checked before
    # the approval synonyms so "NOT APPROVED"/"NOT ACCEPTABLE" don't fall
    # through to an approval match.
    if vu in _VALID_REVIEWER_VERDICTS:
        v = vu
    elif "INCOMPLETE" in vu:
        v = "INCOMPLETE"
    elif (
        "NOT ACCEPTABLE" in vu
        or "NOT_ACCEPTABLE" in vu
        or "NOT APPROVED" in vu
        or "NOT_APPROVED" in vu
        or vu.startswith("REJECT")
        or vu in ("FAIL", "BLOCK", "BLOCKED", "NO")
    ):
        v = "REJECTED"
    elif "PROCEED" in vu or vu.startswith("APPROV") or vu in ("PASS", "ACCEPT", "ACCEPTED", "OK"):
        v = "APPROVED"
    else:
        v = vu  # unknown wording → fails the enum check below (fail-loud)
    if v not in _VALID_REVIEWER_VERDICTS:
        raise LLMCallError(
            f"{reviewer_name} verdict {raw!r} maps to nothing in {sorted(_VALID_REVIEWER_VERDICTS)}"
        )
    data["verdict"] = v
    objections = data.get("objections")
    if objections is None:
        data["objections"] = objections = []
    if not isinstance(objections, list):
        raise LLMCallError(
            f"{reviewer_name} 'objections' is {type(objections).__name__}, expected a JSON array"
        )
    return data


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
        raise LLMCallError(f"no JSON block in LLM response: {text[:200]!r}")
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
