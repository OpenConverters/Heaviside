"""Strands-Agent factory: prompt loader → fully-wired :class:`strands.Agent`.

Agent definitions are Markdown files with YAML frontmatter under
:data:`PROMPTS_DIR`.  This module is the only place that knows how
to translate that on-disk format into a Strands ``Agent`` instance,
which keeps the prompt files completely model-agnostic — switching
provider, swapping tools, or relocating the prompts is a one-file
change.

Frontmatter contract::

    ---
    name: component-librarian
    description: One-line summary used in agent listings.
    allowed_tools: [add_component, component_exists, ...]
    model: kimi-k2.5           # optional; AGENTS.md §8 default
    tier_required: tier_1      # optional; defaults to no constraint
    ---
    <system prompt body — passed verbatim to strands.Agent.system_prompt>

Unknown frontmatter keys raise :class:`AgentLoadError` rather than
being silently ignored — typos in agent prompts have caused real
production bugs in Proteus (see *Lessons learned* in the librarian
prompt).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from heaviside.agents import tools as _tools_module
from heaviside.agents.tools import resolve_tools
from heaviside.llm import ModelTier, classify_model, is_kimi_model

__all__ = [
    "DEFAULT_MODEL",
    "PROMPTS_DIR",
    "AgentDefinition",
    "AgentLoadError",
    "available_agents",
    "load_agent",
    "load_agent_definition",
]


#: Directory holding ``<name>.md`` prompt files.
PROMPTS_DIR: Path = Path(__file__).resolve().parent / "prompts"

#: Default model id per ``AGENTS.md`` §8 ("Default for v0.1: Kimi
#: (Moonshot)").  Override via the ``model`` arg to :func:`load_agent`
#: or the ``model:`` key in prompt frontmatter.
DEFAULT_MODEL: str = "kimi-k2.5"


_KNOWN_FRONTMATTER_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "allowed_tools",
        "model",
        "tier_required",
    }
)


_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<fm>.*?\n)---\s*\n(?P<body>.*)\Z",
    re.DOTALL,
)


class AgentLoadError(Exception):
    """Raised on any structural problem in an agent prompt file."""


@dataclass(frozen=True)
class AgentDefinition:
    """Parsed contents of an agent prompt file."""

    name: str
    description: str
    allowed_tools: tuple[str, ...]
    system_prompt: str
    model: str | None = None
    tier_required: str | None = None
    source_path: Path = field(default_factory=lambda: Path())


def available_agents(prompts_dir: Path | None = None) -> list[str]:
    """Return the sorted list of agent names with a prompt on disk."""
    root = prompts_dir or PROMPTS_DIR
    if not root.exists():
        return []
    return sorted(p.stem for p in root.glob("*.md"))


def load_agent_definition(
    name: str,
    *,
    prompts_dir: Path | None = None,
) -> AgentDefinition:
    """Read and parse ``<prompts_dir>/<name>.md`` without constructing an Agent.

    Useful for tests, agent-listing UIs, or static analysis of the
    prompt corpus.  Raises :class:`AgentLoadError` on any of:

    * file missing,
    * missing frontmatter delimiters,
    * unknown frontmatter keys,
    * missing required keys (``name``, ``description``,
      ``allowed_tools``),
    * frontmatter ``name`` not matching the filename stem.
    """
    root = prompts_dir or PROMPTS_DIR
    path = root / f"{name}.md"
    if not path.exists():
        raise AgentLoadError(
            f"load_agent_definition({name!r}): no prompt at {path}.  "
            f"Available: {available_agents(root)}"
        )

    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise AgentLoadError(
            f"{path}: missing YAML frontmatter.  Expected a block "
            "delimited by '---' on its own line at the top of the file."
        )

    try:
        fm = yaml.safe_load(match.group("fm")) or {}
    except yaml.YAMLError as exc:
        raise AgentLoadError(f"{path}: invalid YAML frontmatter: {exc}") from exc

    if not isinstance(fm, dict):
        raise AgentLoadError(
            f"{path}: frontmatter must decode to a mapping, got {type(fm).__name__}"
        )

    unknown = set(fm) - _KNOWN_FRONTMATTER_KEYS
    if unknown:
        raise AgentLoadError(
            f"{path}: unknown frontmatter keys {sorted(unknown)}.  "
            f"Known: {sorted(_KNOWN_FRONTMATTER_KEYS)}"
        )

    for required in ("name", "description", "allowed_tools"):
        if required not in fm:
            raise AgentLoadError(f"{path}: frontmatter missing required key {required!r}")

    if fm["name"] != name:
        raise AgentLoadError(
            f"{path}: frontmatter name {fm['name']!r} does not match filename stem {name!r}"
        )

    allowed_tools = fm["allowed_tools"]
    if not isinstance(allowed_tools, list) or not all(isinstance(t, str) for t in allowed_tools):
        raise AgentLoadError(
            f"{path}: allowed_tools must be a list[str], got {type(allowed_tools).__name__}"
        )

    model = fm.get("model")
    if model is not None and not isinstance(model, str):
        raise AgentLoadError(f"{path}: model must be a string, got {type(model).__name__}")

    tier_required = fm.get("tier_required")
    if tier_required is not None and not isinstance(tier_required, str):
        raise AgentLoadError(
            f"{path}: tier_required must be a string, got {type(tier_required).__name__}"
        )

    return AgentDefinition(
        name=fm["name"],
        description=fm["description"],
        allowed_tools=tuple(allowed_tools),
        system_prompt=match.group("body").strip(),
        model=model,
        tier_required=tier_required,
        source_path=path,
    )


def _check_tier(model_id: str, tier_required: str | None) -> None:
    """Raise :class:`AgentLoadError` if model classification fails policy."""
    tier = classify_model(model_id)
    if tier is ModelTier.BLOCKED:
        raise AgentLoadError(
            f"model {model_id!r} is classified BLOCKED in "
            "heaviside/llm/model_tiers.json; refusing to construct agent."
        )
    if tier_required is None:
        return
    # Coerce both sides to canonical short forms for comparison.
    want = tier_required.strip().lower()
    have = tier.value
    if want not in have and have != want:
        raise AgentLoadError(
            f"tier_required={tier_required!r} not satisfied: model "
            f"{model_id!r} classifies as {have!r}."
        )


def load_agent(
    name: str,
    *,
    model: str | None = None,
    prompts_dir: Path | None = None,
    agent_cls: Any = None,
    kimi_model_builder: Any = None,
) -> Any:
    """Construct a Strands ``Agent`` for the named prompt.

    Args:
        name: Stem of ``<prompts_dir>/<name>.md``.
        model: Override the prompt's declared model (and the
            :data:`DEFAULT_MODEL` fallback).
        prompts_dir: Override :data:`PROMPTS_DIR` (test hook).
        agent_cls: Inject a fake ``Agent`` class — used by the unit
            tests to avoid network calls.  Default is the real
            :class:`strands.Agent`.
        kimi_model_builder: Inject a fake substitute for
            :func:`heaviside.llm.build_kimi_model` — used by unit
            tests so neither the ``openai`` SDK nor a real
            ``MOONSHOT_API_KEY`` need be present.  Receives the
            keyword ``model_id`` and must return a value that the
            Strands ``Agent`` accepts as ``model=``.  Default is the
            real :func:`build_kimi_model`.

    The constructed agent has:

    * ``system_prompt`` = the prompt body,
    * ``tools`` = the resolved callables from
      :data:`heaviside.agents.tools.TOOL_REGISTRY`,
    * ``model`` = a fully constructed Strands ``Model`` object when
      the resolved model id matches a Moonshot prefix (``kimi-*`` or
      ``moonshot-*``); otherwise the raw model-id string passed
      through to Strands so its built-in provider routing applies.

    Tier policy: a model classified ``blocked`` is refused
    unconditionally.  A prompt with ``tier_required:`` enforces a
    minimum tier; unknown-tier models satisfy no ``tier_required``.

    Raises:
        AgentLoadError: tier policy violation or prompt-file
            structural problem.
        heaviside.llm.KimiCredentialError: Kimi-family model resolved
            but ``MOONSHOT_API_KEY`` is unset.  Surfaced from the
            builder verbatim — Heaviside refuses to silently fall
            back to a string model id that Strands cannot route.
        heaviside.llm.KimiDependencyError: Kimi-family model resolved
            but the ``openai`` SDK is not installed.
    """
    definition = load_agent_definition(name, prompts_dir=prompts_dir)

    chosen_model = model or definition.model or DEFAULT_MODEL
    _check_tier(chosen_model, definition.tier_required)

    tools = resolve_tools(list(definition.allowed_tools))

    # Route Kimi-family ids through the Moonshot builder so Strands
    # receives a fully constructed ``OpenAIModel`` pointed at
    # ``api.moonshot.ai``.  For non-Kimi ids the bare string is
    # handed to Strands, which selects its own provider adapter.
    if is_kimi_model(chosen_model):
        if kimi_model_builder is None:
            from heaviside.llm import build_kimi_model as kimi_model_builder
        model_arg: Any = kimi_model_builder(model_id=chosen_model)
    else:
        model_arg = chosen_model

    if agent_cls is None:
        # Deferred import keeps strands optional for code paths that
        # never construct a live Agent (e.g. dataclass-only tests).
        from strands import Agent as _Agent

        agent_cls = _Agent

    return agent_cls(
        model=model_arg,
        tools=tools,
        system_prompt=definition.system_prompt,
        name=definition.name,
        description=definition.description,
    )


# Silence the "imported but unused" warning — kept for the public
# re-export from heaviside.agents.
_ = _tools_module
