"""call_llm retries rate limits (429) and transient 5xx with backoff, honours
Retry-After, and fails fast on non-retryable statuses (400/401)."""

from __future__ import annotations

import httpx
import pytest

import heaviside.agents.llm_call as L


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    # Keep the test instant: tiny backoff, dummy key.
    monkeypatch.setenv("HEAVISIDE_LLM_RETRY_BASE_S", "0.001")
    monkeypatch.setenv("HEAVISIDE_LLM_RETRY_MAX_S", "0.01")
    monkeypatch.setenv("HEAVISIDE_LLM_MAX_RETRIES", "5")
    monkeypatch.setenv("MOONSHOT_API_KEY", "dummy")


class _Resp:
    def __init__(self, code, text="", data=None, headers=None):
        self.status_code = code
        self.text = text
        self._d = data or {}
        self.headers = headers or {}

    def json(self):
        return self._d


_OK = {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}], "usage": {}}


def test_retries_429_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def post(url, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            return _Resp(429, "rl", headers={"Retry-After": "0"})
        return _Resp(200, data=_OK)

    monkeypatch.setattr(httpx, "post", post)
    assert L.call_llm("s", "u") == "hi"
    assert calls["n"] == 3


def test_exhausted_rate_limit_raises_ratelimit_error(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, **kw: _Resp(429, "rl", headers={"Retry-After": "0"}))
    with pytest.raises(L.LLMRateLimitError):
        L.call_llm("s", "u")


def test_transient_5xx_retried(monkeypatch):
    calls = {"n": 0}

    def post(url, **kw):
        calls["n"] += 1
        return _Resp(200, data=_OK) if calls["n"] >= 2 else _Resp(503, "busy")

    monkeypatch.setattr(httpx, "post", post)
    assert L.call_llm("s", "u") == "hi"
    assert calls["n"] == 2


def test_400_fails_immediately_no_retry(monkeypatch):
    calls = {"n": 0}

    def post(url, **kw):
        calls["n"] += 1
        return _Resp(400, "bad request")

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(L.LLMCallError) as ei:
        L.call_llm("s", "u")
    assert not isinstance(ei.value, L.LLMRateLimitError)
    assert calls["n"] == 1  # never retried


def test_transport_error_retried_then_raises(monkeypatch):
    def post(url, **kw):
        raise httpx.ConnectError("reset")

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(L.LLMCallError):
        L.call_llm("s", "u")
