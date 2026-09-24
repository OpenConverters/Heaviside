"""A tool-using agent cannot loop forever (ABT #1395).

Otto once called crossref_capacitor hundreds of times in one run and held the
server's only job worker for over an hour. The runner now caps tool calls and
raises instead of returning whatever the stopped loop last said.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("strands")

from strands import Agent, tool  # noqa: E402
from strands.models.model import Model  # noqa: E402

import heaviside.agents.llm_call as lc  # noqa: E402

_calls = {"n": 0}


@tool
def ping() -> str:
    """Answer pong."""
    _calls["n"] += 1
    return "pong"


class _AlwaysCallsTool(Model):
    """A model that requests the tool on every turn and never answers."""

    def update_config(self, **kwargs: Any) -> None: ...

    def get_config(self) -> dict[str, Any]:
        return {}

    async def structured_output(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise NotImplementedError

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": f"t{len(messages)}",
                                                           "name": "ping"}}}}
        yield {"contentBlockDelta": {"delta": {"toolUse": {"input": "{}"}}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}


def test_budget_stops_a_runaway_tool_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEAVISIDE_AGENT_TOOL_BUDGET", "5")
    _calls["n"] = 0
    agent = Agent(model=_AlwaysCallsTool(), tools=[ping], callback_handler=None)
    budget = lc._attach_tool_budget(agent)
    agent("go")
    assert _calls["n"] == 5
    assert budget["exceeded"]


def test_runner_raises_when_the_budget_is_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEAVISIDE_AGENT_TOOL_BUDGET", "3")
    _calls["n"] = 0

    def fake_load_agent(name: str, **kwargs: Any) -> Agent:
        return Agent(model=_AlwaysCallsTool(), tools=[ping], callback_handler=None)

    import heaviside.agents.factory as factory

    monkeypatch.setattr(factory, "load_agent", fake_load_agent)
    definition = type("D", (), {"name": "otto"})()
    with pytest.raises(lc.LLMCallError, match="tool-call budget"):
        lc._run_strands_agent(definition, "go", model_id="gpt-4o", temperature=0.3,
                              max_tokens=100, json_mode=False)
    assert _calls["n"] == 3


def test_a_caller_budget_applies_but_never_exceeds_the_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HEAVISIDE_AGENT_TOOL_BUDGET", "6")
    for asked, expected in ((2, 2), (50, 6)):
        _calls["n"] = 0
        agent = Agent(model=_AlwaysCallsTool(), tools=[ping], callback_handler=None)
        budget = lc._attach_tool_budget(agent, asked)
        agent("go")
        assert (_calls["n"], budget["limit"]) == (expected, expected)


def test_otto_budget_scales_with_the_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    from heaviside.pipeline import crossref_pipeline as cp
    from heaviside.pipeline.crossref import CrossRefState

    monkeypatch.setenv("HEAVISIDE_JEV", "0")  # no triage: every row reaches Otto
    seen: list[int | None] = []

    def fake_call_agent(name: str, msg: str, **kwargs: Any) -> str:
        seen.append(kwargs.get("tool_budget"))
        return '{"challenges": []}'

    monkeypatch.setattr(cp, "call_agent", fake_call_agent)
    rows = [{"ref_des": f"C{i}", "component_type": "capacitor", "status": "no_substitute"}
            for i in range(2)]
    state = CrossRefState(source_bom=[], target_manufacturer="Würth Elektronik",
                          crossref_result=rows)
    cp._stage6_otto(state)
    assert seen == [3 * 2 + 2]
