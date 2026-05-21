"""Unit tests for :mod:`heaviside.agents.factory`.

The factory wires prompt-file → :class:`strands.Agent`.  Tests use a
``FakeAgent`` class injected via the ``agent_cls`` parameter so no
provider connection is ever attempted.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from heaviside.agents import (
    AgentDefinition,
    AgentLoadError,
    available_agents,
    load_agent,
    load_agent_definition,
)
from heaviside.agents.factory import DEFAULT_MODEL, PROMPTS_DIR


@dataclass
class FakeAgent:
    """Minimal stand-in for :class:`strands.Agent` used in tests."""

    model: Any
    tools: Any
    system_prompt: str
    name: str
    description: str

    def __init__(self, **kwargs: Any) -> None:
        # Strands ``Agent`` accepts a wide signature; we record whatever
        # the factory passed so tests can assert against it.
        for k, v in kwargs.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_available_agents_lists_ported_prompts() -> None:
    names = available_agents()
    assert "component-librarian" in names
    assert "component-auditor" in names


def test_prompts_dir_exists() -> None:
    assert PROMPTS_DIR.is_dir()


# ---------------------------------------------------------------------------
# load_agent_definition
# ---------------------------------------------------------------------------


def test_load_definition_parses_librarian_prompt() -> None:
    d = load_agent_definition("component-librarian")
    assert isinstance(d, AgentDefinition)
    assert d.name == "component-librarian"
    assert d.description
    assert "add_component" in d.allowed_tools
    assert "component_exists" in d.allowed_tools
    assert d.system_prompt.startswith("# Component Librarian")
    assert d.source_path.name == "component-librarian.md"


def test_load_definition_parses_auditor_prompt() -> None:
    d = load_agent_definition("component-auditor")
    assert d.name == "component-auditor"
    assert "audit_category" in d.allowed_tools
    assert "read_knowledge" in d.allowed_tools


def test_missing_prompt_raises(tmp_path: Path) -> None:
    with pytest.raises(AgentLoadError, match="no prompt at"):
        load_agent_definition("not-a-real-agent", prompts_dir=tmp_path)


def test_missing_frontmatter_raises(tmp_path: Path) -> None:
    (tmp_path / "broken.md").write_text("# Just a markdown file with no frontmatter\n")
    with pytest.raises(AgentLoadError, match="missing YAML frontmatter"):
        load_agent_definition("broken", prompts_dir=tmp_path)


def test_unknown_frontmatter_key_raises(tmp_path: Path) -> None:
    (tmp_path / "evil.md").write_text(
        "---\n"
        "name: evil\n"
        "description: x\n"
        "allowed_tools: [add_component]\n"
        "rogue_field: nope\n"
        "---\n\nbody\n"
    )
    with pytest.raises(AgentLoadError, match="unknown frontmatter keys"):
        load_agent_definition("evil", prompts_dir=tmp_path)


def test_missing_required_key_raises(tmp_path: Path) -> None:
    (tmp_path / "incomplete.md").write_text(
        "---\nname: incomplete\ndescription: x\n---\n\nbody\n"
    )
    with pytest.raises(AgentLoadError, match="missing required key"):
        load_agent_definition("incomplete", prompts_dir=tmp_path)


def test_name_mismatch_raises(tmp_path: Path) -> None:
    (tmp_path / "filename.md").write_text(
        "---\nname: different\ndescription: x\nallowed_tools: []\n---\n\nbody\n"
    )
    with pytest.raises(AgentLoadError, match="does not match filename"):
        load_agent_definition("filename", prompts_dir=tmp_path)


def test_allowed_tools_must_be_list_of_str(tmp_path: Path) -> None:
    (tmp_path / "bad.md").write_text(
        "---\nname: bad\ndescription: x\nallowed_tools: add_component\n---\n\nbody\n"
    )
    with pytest.raises(AgentLoadError, match="allowed_tools must be a list"):
        load_agent_definition("bad", prompts_dir=tmp_path)


# ---------------------------------------------------------------------------
# load_agent (with fake Agent class)
# ---------------------------------------------------------------------------


def test_load_agent_default_model_routes_to_kimi_builder() -> None:
    """Default model id ``kimi-k2.5`` flows through the Kimi builder.

    The factory must hand Strands a fully constructed ``OpenAIModel``
    object — never the bare string ``"kimi-k2.5"`` — because the
    Strands string router has no entry for Moonshot model ids.  The
    test injects a stub builder so no network or openai SDK is needed.
    """
    seen: dict[str, Any] = {}

    def fake_builder(*, model_id: str) -> str:
        seen["model_id"] = model_id
        return f"FAKE_KIMI_MODEL({model_id})"

    agent = load_agent(
        "component-librarian",
        agent_cls=FakeAgent,
        kimi_model_builder=fake_builder,
    )
    assert DEFAULT_MODEL == "kimi-k2.5"
    assert seen == {"model_id": "kimi-k2.5"}
    assert agent.model == "FAKE_KIMI_MODEL(kimi-k2.5)"
    assert agent.name == "component-librarian"


def test_load_agent_resolves_tools() -> None:
    # Use a non-Kimi model id so the factory passes the bare string
    # through; this test exercises tool resolution, not model wiring.
    agent = load_agent(
        "component-auditor",
        model="claude-opus-4-6",
        agent_cls=FakeAgent,
    )
    tool_names = [t.tool_spec["name"] for t in agent.tools]
    assert "audit_category" in tool_names
    assert "read_knowledge" in tool_names
    # Auditor must NOT receive the writer surface
    assert "add_component" not in tool_names


def test_model_arg_overrides_default() -> None:
    agent = load_agent(
        "component-librarian",
        model="claude-opus-4-6",
        agent_cls=FakeAgent,
    )
    assert agent.model == "claude-opus-4-6"


def test_blocked_model_is_refused(tmp_path: Path) -> None:
    # llama3:8b is in the blocked tier per the shipped tiers JSON.
    (tmp_path / "x.md").write_text(
        "---\nname: x\ndescription: x\nallowed_tools: [read_knowledge]\n---\n\nbody\n"
    )
    with pytest.raises(AgentLoadError, match="BLOCKED"):
        load_agent(
            "x",
            model="llama3:8b",
            prompts_dir=tmp_path,
            agent_cls=FakeAgent,
        )


def test_load_agent_passes_system_prompt(tmp_path: Path) -> None:
    (tmp_path / "hello.md").write_text(
        "---\nname: hello\ndescription: x\nallowed_tools: [read_knowledge]\n---\n\n# Hello world\n\nbody text\n"
    )
    agent = load_agent(
        "hello",
        model="claude-opus-4-6",
        prompts_dir=tmp_path,
        agent_cls=FakeAgent,
    )
    assert agent.system_prompt.startswith("# Hello world")
    assert "body text" in agent.system_prompt


def test_unknown_tool_in_prompt_raises(tmp_path: Path) -> None:
    (tmp_path / "typo.md").write_text(
        "---\nname: typo\ndescription: x\n"
        "allowed_tools: [read_knowledge, not_a_real_tool]\n---\n\nbody\n"
    )
    with pytest.raises(KeyError, match="unknown tool name"):
        load_agent(
            "typo",
            model="claude-opus-4-6",
            prompts_dir=tmp_path,
            agent_cls=FakeAgent,
        )


# ---------------------------------------------------------------------------
# Real Strands Agent construction (no LLM call — just instantiation)
# ---------------------------------------------------------------------------


def test_load_agent_constructs_real_strands_agent_object() -> None:
    """Construct via the real strands.Agent class; do not invoke.

    Uses a non-Kimi model id so the factory passes the bare string
    through to Strands and the test does not require a Moonshot key
    or the ``openai`` SDK.  The Kimi-routing path is covered by
    ``test_load_agent_default_model_routes_to_kimi_builder`` and the
    live eval at ``tests/evals/test_kimi_smoke.py``.
    """
    from strands import Agent
    agent = load_agent("component-librarian", model="claude-opus-4-6")
    assert isinstance(agent, Agent)
    # Strands keeps the tool list under .tool_registry — not asserting
    # the exact internal API beyond "constructor accepted our payload".
    assert agent is not None


# ---------------------------------------------------------------------------
# Kimi-routing behaviour
# ---------------------------------------------------------------------------


def test_explicit_kimi_model_id_routes_through_builder() -> None:
    """A ``model=`` override that is a Kimi id still routes correctly."""
    seen: dict[str, Any] = {}

    def fake_builder(*, model_id: str) -> str:
        seen["model_id"] = model_id
        return f"FAKE({model_id})"

    agent = load_agent(
        "component-librarian",
        model="moonshot-v1-128k",
        agent_cls=FakeAgent,
        kimi_model_builder=fake_builder,
    )
    assert seen == {"model_id": "moonshot-v1-128k"}
    assert agent.model == "FAKE(moonshot-v1-128k)"


def test_non_kimi_model_id_bypasses_builder() -> None:
    """Non-Kimi ids are handed to Strands as bare strings.

    The builder must not be called — Strands has its own routing for
    anthropic, openai, bedrock, …
    """
    builder_called = False

    def fake_builder(*, model_id: str) -> str:
        nonlocal builder_called
        builder_called = True
        return "should-not-be-used"

    agent = load_agent(
        "component-librarian",
        model="claude-opus-4-6",
        agent_cls=FakeAgent,
        kimi_model_builder=fake_builder,
    )
    assert builder_called is False
    assert agent.model == "claude-opus-4-6"


def test_kimi_credential_error_propagates() -> None:
    """A builder that raises ``KimiCredentialError`` is not swallowed.

    Strict-mode contract: when the default model is Kimi but no key
    is available, ``load_agent`` must raise — never fall back to a
    bare string that Strands cannot route.
    """
    from heaviside.llm import KimiCredentialError

    def angry_builder(*, model_id: str) -> Any:
        raise KimiCredentialError("no key in env (test)")

    with pytest.raises(KimiCredentialError, match="no key in env"):
        load_agent(
            "component-librarian",
            agent_cls=FakeAgent,
            kimi_model_builder=angry_builder,
        )


def test_kimi_model_id_case_insensitive() -> None:
    """``KIMI-K2.5`` (upper-case) still routes through the builder."""
    seen: dict[str, Any] = {}

    def fake_builder(*, model_id: str) -> str:
        seen["model_id"] = model_id
        return "FAKE"

    load_agent(
        "component-librarian",
        model="KIMI-K2.5",
        agent_cls=FakeAgent,
        kimi_model_builder=fake_builder,
    )
    # The factory forwards the *original* id (preserving case) — only
    # the routing decision is case-insensitive.
    assert seen == {"model_id": "KIMI-K2.5"}
