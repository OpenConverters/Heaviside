"""Env-gated live LLM smoke test against real Kimi (Moonshot).

This test is **opt-in**.  It runs only when *all* of the following
are true:

* ``MOONSHOT_API_KEY`` is set in the environment.
* The ``openai`` Python SDK is installed (``pip install openai``).
* The ``evals`` pytest marker is selected (``pytest -m evals``).

Otherwise it skips with a clear reason.  This means default CI / dev
``pytest`` runs see zero impact: the test contributes to neither
runtime nor flakiness budgets.

What this exercises
-------------------

* :func:`heaviside.llm.load_kimi_credentials` against real env.
* :func:`heaviside.llm.build_kimi_model` against the real
  ``strands.models.openai.OpenAIModel`` constructor (i.e. validates
  that our kwargs shape matches what Strands actually wants).
* One round-trip to Moonshot's chat-completions endpoint via a
  minimal :class:`strands.Agent` invocation — the cheapest possible
  prompt that still proves the credential, the base URL, and the
  model id are all coherent.

Cost
----

A single ``kimi-k2.5`` chat-completions call with ~20 input tokens
and ``max_tokens=512`` (generous bound that accommodates the model's
reasoning trace; actual completion is typically <100 tokens).
Order-of-magnitude under USD 0.01 per run at May 2026 Moonshot
pricing.

How to run
----------

::

    pip install openai
    export MOONSHOT_API_KEY=sk-...
    pytest -m evals tests/evals/test_kimi_smoke.py -v
"""

from __future__ import annotations

import os

import pytest


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


# Skip the entire module when the openai SDK is missing.  Use
# ``importorskip`` so the skip message is informative without
# requiring us to construct it manually.
pytest.importorskip(
    "openai",
    reason=(
        "openai SDK not installed; install with `pip install openai` to "
        "enable live Kimi smoke tests"
    ),
)


pytestmark = [
    pytest.mark.evals,
    pytest.mark.skipif(
        not os.environ.get("MOONSHOT_API_KEY", "").strip(),
        reason="MOONSHOT_API_KEY not set; live Kimi smoke skipped",
    ),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_load_real_kimi_credentials() -> None:
    """Sanity: env carries a usable Moonshot key shape."""
    from heaviside.llm import load_kimi_credentials

    creds = load_kimi_credentials()
    # Moonshot keys conventionally begin with ``sk-``; not a hard
    # requirement (Moonshot has rotated the prefix scheme once
    # already), but useful to flag pasted-junk credentials.
    assert creds.api_key, "MOONSHOT_API_KEY decoded to empty string"
    assert creds.base_url.startswith("https://"), (
        f"resolved base_url {creds.base_url!r} is not https — refusing "
        f"to send a key over cleartext"
    )


def test_build_real_kimi_model_constructs() -> None:
    """The real Strands OpenAIModel accepts our kwargs without error.

    This exercises the kwargs *shape* but does not yet make an HTTP
    call — that's :func:`test_kimi_agent_round_trip`.
    """
    from heaviside.llm import build_kimi_model

    # ``kimi-k2.5`` rejects ``temperature != 1`` with HTTP 400
    # (``only 1 is allowed for this model``).  Use the only accepted
    # value; determinism is enforced by the system prompt instead.
    # ``kimi-k2.5`` is also a reasoning model: it emits an internal
    # thought trace before the user-visible answer, so ``max_tokens``
    # must accommodate both.  512 is generous for a one-word reply.
    model = build_kimi_model(params={"temperature": 1.0, "max_tokens": 512})
    # No state checked beyond "construction succeeded" — the next
    # test exercises behaviour.
    assert model is not None


def test_kimi_agent_round_trip() -> None:
    """One real Moonshot chat-completions call via a Strands Agent.

    The agent is constructed inline (not via ``load_agent``) so this
    test stays independent of the prompt corpus — a regression in
    the agent factory should not mask a working LLM path.

    The prompt is engineered to elicit a deterministic single-word
    response so flakiness budget stays tight.
    """
    from strands import Agent

    from heaviside.llm import build_kimi_model

    model = build_kimi_model(params={"temperature": 1.0, "max_tokens": 512})
    agent = Agent(
        model=model,
        system_prompt=(
            "You are a smoke-test echo.  When asked to reply with a "
            "single word, output exactly that word with no "
            "punctuation, no preamble, and no formatting."
        ),
        name="kimi-smoke",
        description="Heaviside slice-G live smoke check.",
    )
    result = agent("Reply with the single word: PROTEUS")
    # The Strands Agent return type varies by version; coerce to a
    # plain string before asserting.
    text = str(result).strip().upper()
    assert text, f"empty response from Kimi: {result!r}"
    # We don't insist on an exact match — model behaviour drift over
    # time is normal — but the marker token must appear.
    assert "PROTEUS" in text, (
        f"Kimi response {text!r} does not contain the expected token; "
        f"likely a prompt / model regression."
    )
