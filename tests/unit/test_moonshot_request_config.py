"""Single-source-of-truth contract for the Kimi/Moonshot request config.

The Kimi *non-reasoning* invariant (thinking off by default + the temperature
rule Moonshot enforces) lives in one helper,
:func:`heaviside.llm.moonshot_request_config`. These tests pin its behaviour
*and* assert that both call sites — the single-shot httpx path
(:func:`heaviside.agents.llm_call.call_llm`) and the Strands model path
(:func:`heaviside.llm.build_kimi_model`) — apply exactly that config.

No network, no ``openai`` SDK, no real Moonshot key.
"""

from __future__ import annotations

from typing import Any

import pytest

from heaviside.llm.kimi import (
    MOONSHOT_BASE_URL_INTL,
    KimiCredentials,
    build_kimi_model,
    moonshot_request_config,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# moonshot_request_config — the helper itself
# ---------------------------------------------------------------------------


def test_helper_k2_default_disables_thinking_and_pins_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Default (env unset) = thinking DISABLED — the standing directive.
    monkeypatch.delenv("HEAVISIDE_KIMI_DISABLE_THINKING", raising=False)
    cfg = moonshot_request_config("kimi-k2.5", temperature=0.3)
    assert cfg["thinking"] == {"type": "disabled"}
    # The passed temperature is IGNORED — Moonshot requires 0.6 with thinking off.
    assert cfg["temperature"] == 0.6


def test_helper_explicit_disable_flag_is_identical_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HEAVISIDE_KIMI_DISABLE_THINKING", "1")
    cfg = moonshot_request_config("kimi-k2.5", temperature=0.9)
    assert cfg == {"thinking": {"type": "disabled"}, "temperature": 0.6}


def test_helper_non_k2_passes_temperature_and_no_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HEAVISIDE_KIMI_DISABLE_THINKING", raising=False)
    cfg = moonshot_request_config("moonshot-v1-128k", temperature=0.42)
    assert "thinking" not in cfg
    assert cfg["temperature"] == 0.42


def test_helper_k2_thinking_on_omits_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Opt back into thinking → k2.5 only accepts temperature == 1, so the
    # helper omits temperature entirely (and emits no thinking key).
    monkeypatch.setenv("HEAVISIDE_KIMI_DISABLE_THINKING", "0")
    cfg = moonshot_request_config("kimi-k2.5", temperature=0.3)
    assert "thinking" not in cfg
    assert "temperature" not in cfg


# ---------------------------------------------------------------------------
# call_llm — the single-shot httpx call site applies the same config
# ---------------------------------------------------------------------------


class _FakeResp:
    status_code = 200
    text = ""

    def json(self) -> dict[str, Any]:
        return {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {},
        }


def _capture_call_llm_body(
    monkeypatch: pytest.MonkeyPatch,
    *,
    model: str,
    temperature: float,
) -> dict[str, Any]:
    """Run call_llm with httpx.post stubbed and return the request body."""
    import httpx

    from heaviside.agents import llm_call

    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-test")
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> _FakeResp:
        captured["body"] = kwargs["json"]
        return _FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    out = llm_call.call_llm("sys", "user", temperature=temperature, model=model)
    assert out == "ok"
    return captured["body"]


def test_call_llm_applies_helper_config_for_k2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEAVISIDE_KIMI_DISABLE_THINKING", raising=False)
    body = _capture_call_llm_body(monkeypatch, model="kimi-k2.5", temperature=0.3)
    assert body["thinking"] == {"type": "disabled"}
    assert body["temperature"] == 0.6
    # Parity: every key the helper resolves is present, verbatim, in the body.
    for key, value in moonshot_request_config("kimi-k2.5", temperature=0.3).items():
        assert body[key] == value


def test_call_llm_applies_helper_config_for_non_k2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEAVISIDE_KIMI_DISABLE_THINKING", raising=False)
    body = _capture_call_llm_body(monkeypatch, model="moonshot-v1-128k", temperature=0.42)
    assert "thinking" not in body
    assert body["temperature"] == 0.42


# ---------------------------------------------------------------------------
# build_kimi_model — the Strands model call site applies the same config
# ---------------------------------------------------------------------------


class _FakeBaseModel:
    """Stand-in OpenAIModel exposing a minimal ``format_request``."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def format_request(self, *args: Any, **kw: Any) -> dict[str, Any]:
        return {"model_id": self.kwargs.get("model_id"), "messages": []}


_CREDS = KimiCredentials(api_key="sk-1", base_url=MOONSHOT_BASE_URL_INTL)


def test_build_kimi_model_applies_thinking_disabled_for_k2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HEAVISIDE_KIMI_DISABLE_THINKING", raising=False)
    model = build_kimi_model(
        model_id="kimi-k2.5", credentials=_CREDS, model_cls=_FakeBaseModel
    )
    request = model.format_request()
    assert request["extra_body"]["thinking"] == {"type": "disabled"}
    assert request["temperature"] == 0.6
    # Parity with the helper.
    cfg = moonshot_request_config("kimi-k2.5", temperature=0.3)
    assert request["extra_body"]["thinking"] == cfg["thinking"]
    assert request["temperature"] == cfg["temperature"]


def test_build_kimi_model_no_override_for_non_k2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEAVISIDE_KIMI_DISABLE_THINKING", raising=False)
    model = build_kimi_model(
        model_id="moonshot-v1-128k", credentials=_CREDS, model_cls=_FakeBaseModel
    )
    # Non-k2 → no thinking key in the helper config → no format_request wrapper.
    assert type(model) is _FakeBaseModel
    request = model.format_request()
    assert "extra_body" not in request


def test_build_kimi_model_no_override_when_thinking_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HEAVISIDE_KIMI_DISABLE_THINKING", "0")
    model = build_kimi_model(
        model_id="kimi-k2.5", credentials=_CREDS, model_cls=_FakeBaseModel
    )
    assert type(model) is _FakeBaseModel
    request = model.format_request()
    assert "extra_body" not in request
    assert "temperature" not in request
