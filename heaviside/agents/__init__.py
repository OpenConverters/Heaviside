"""Strands-Agents layer for Heaviside.

Per ``AGENTS.md`` §8, every LLM-driven persona is constructed via
``strands.Agent`` so provider routing (Kimi / Moonshot default;
Claude / GPT / Bedrock / local via Strands adapters) stays outside
the agent prompt and the agent-tool layer.

Per the *Adding agents* section of ``AGENTS.md``, agent definitions
live as Markdown files under ``heaviside/agents/prompts/<name>.md``
with YAML frontmatter::

    ---
    name: component-librarian
    description: ...one-line summary...
    allowed_tools: [add_component, component_exists, ...]
    ---
    <system prompt body in Markdown>

The v0.1 target is 10–12 consolidated agents — fewer than Proteus's
30 — by collapsing overlapping reviewers and merging single-purpose
helpers into the persona that owns the workflow.

This package re-exports the two public entry points:

* :func:`load_agent` — read a prompt file, parse its frontmatter,
  resolve its tool list, and return a fully-wired
  :class:`strands.Agent`.
* :func:`available_agents` — enumerate the prompts on disk.

The tool registry lives in :mod:`heaviside.agents.tools`; adding a
new librarian/auditor surface there is what makes it callable from
agent prompts via ``allowed_tools``.
"""

from __future__ import annotations

from heaviside.agents.factory import (
    AgentDefinition,
    AgentLoadError,
    available_agents,
    load_agent,
    load_agent_definition,
)
from heaviside.agents.tools import (
    AGENT_TOOLS,
    TOOL_REGISTRY,
    resolve_tools,
)

__all__ = [
    "AGENT_TOOLS",
    "AgentDefinition",
    "AgentLoadError",
    "TOOL_REGISTRY",
    "available_agents",
    "load_agent",
    "load_agent_definition",
    "resolve_tools",
]
