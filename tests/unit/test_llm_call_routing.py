"""Routing contract for :func:`heaviside.agents.llm_call.call_agent`.

The prompt frontmatter decides the execution path:

* ``allowed_tools`` non-empty → Strands agent (tool-calling, multi-turn),
* ``allowed_tools: []``      → single-shot :func:`call_llm`,

and review-role agents (ray / nicola / reviewer) are refused on models
that fail the tier policy's review-role gate — *before* any network call.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from heaviside.agents import llm_call
from heaviside.agents.llm_call import LLMCallError, call_agent

pytestmark = pytest.mark.unit


def test_tooled_agent_routes_through_strands(monkeypatch: pytest.MonkeyPatch) -> None:
    """otto declares tools → call_agent must take the Strands path."""
    seen: dict[str, Any] = {}

    def fake_strands(definition: Any, user_message: str, **kwargs: Any) -> str:
        seen["name"] = definition.name
        seen["tools"] = tuple(definition.allowed_tools)
        seen["model_id"] = kwargs["model_id"]
        return '{"ok": true}'

    monkeypatch.setattr(llm_call, "_run_strands_agent", fake_strands)
    monkeypatch.delenv("HEAVISIDE_LLM_MODEL", raising=False)

    out = call_agent("otto", "challenge these no_substitutes")
    assert out == '{"ok": true}'
    assert seen["name"] == "otto"
    assert "crossref_capacitor" in seen["tools"]
    assert seen["model_id"] == "kimi-k2.5"


def test_toolless_agent_routes_through_call_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """cross-referencer declares no tools → single-shot call_llm path."""
    seen: dict[str, Any] = {}

    def fake_call_llm(system_prompt: str, user_message: str, **kwargs: Any) -> str:
        seen["system_prompt"] = system_prompt
        seen["model"] = kwargs.get("model")
        return "{}"

    def boom(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("tool-less agent must not construct a Strands agent")

    monkeypatch.setattr(llm_call, "call_llm", fake_call_llm)
    monkeypatch.setattr(llm_call, "_run_strands_agent", boom)
    monkeypatch.delenv("HEAVISIDE_LLM_MODEL", raising=False)

    call_agent("cross-referencer", "crossref this BOM")
    assert "Cross-Referencer" in seen["system_prompt"]
    assert seen["model"] == "kimi-k2.5"


def test_env_model_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        llm_call,
        "_run_strands_agent",
        lambda d, m, **kw: seen.update(model_id=kw["model_id"]) or "x",
    )
    monkeypatch.setenv("HEAVISIDE_LLM_MODEL", "gpt-4o")
    call_agent("otto", "msg")
    assert seen["model_id"] == "gpt-4o"


@pytest.mark.parametrize("reviewer", ["ray", "nicola", "reviewer"])
def test_review_role_refuses_unvetted_model(reviewer: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A model outside the review-role allowlist must be refused loudly
    before any LLM/agent construction happens."""
    monkeypatch.setenv("HEAVISIDE_LLM_MODEL", "llama3:70b")

    def boom(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("must refuse before reaching the LLM")

    monkeypatch.setattr(llm_call, "call_llm", boom)
    monkeypatch.setattr(llm_call, "_run_strands_agent", boom)

    with pytest.raises(LLMCallError, match="review role"):
        call_agent(reviewer, "review this design")


def test_review_role_accepts_certified_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEAVISIDE_LLM_MODEL", "kimi-k2.5")
    monkeypatch.setattr(llm_call, "call_llm", lambda *a, **kw: '{"verdict": "APPROVED"}')
    out = call_agent("ray", "review this design")
    assert json.loads(out)["verdict"] == "APPROVED"


def test_unknown_agent_raises_llm_call_error() -> None:
    with pytest.raises(LLMCallError, match="failed to load"):
        call_agent("agent-that-does-not-exist", "hi")


def test_topology_selector_uses_shared_call_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The selector has no private HTTP client any more — it must go
    through llm_call.call_agent."""
    from heaviside.agents import topology_selector_llm as ts

    seen: dict[str, Any] = {}

    def fake_call_agent(agent_name: str, user_message: str, **kwargs: Any) -> str:
        seen["agent"] = agent_name
        return '```json\n{"viable": ["buck"], "reasoning": "fits"}\n```'

    monkeypatch.setattr(llm_call, "call_agent", fake_call_agent)

    names, reasoning = ts.topology_selector_llm({"inputVoltage": {"nominal": 48.0}})
    assert seen["agent"] == "topology-selector"
    assert names == ["buck"]
    assert reasoning == "fits"


def test_topology_selector_wraps_llm_call_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from heaviside.agents import topology_selector_llm as ts

    def fail(*args: Any, **kwargs: Any) -> str:
        raise LLMCallError("no key")

    monkeypatch.setattr(llm_call, "call_agent", fail)
    with pytest.raises(ts.LLMUnavailableError, match="no key"):
        ts.topology_selector_llm({})
