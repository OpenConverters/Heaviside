"""Tests for ``heaviside.llm.kimi``.

These run unconditionally — no network, no ``openai`` SDK, no real
Moonshot key required.  The corresponding env-gated live smoke
test lives in ``tests/evals/test_kimi_smoke.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from heaviside.llm.kimi import (
    DEFAULT_KIMI_MODEL_ID,
    MOONSHOT_BASE_URL_CN,
    MOONSHOT_BASE_URL_INTL,
    KimiCredentialError,
    KimiCredentials,
    KimiDependencyError,
    KimiError,
    build_kimi_model,
    load_kimi_credentials,
)


# ---------------------------------------------------------------------------
# load_kimi_credentials
# ---------------------------------------------------------------------------


def test_load_credentials_happy_path() -> None:
    env = {"MOONSHOT_API_KEY": "sk-test-12345"}
    creds = load_kimi_credentials(env=env)
    assert creds.api_key == "sk-test-12345"
    assert creds.base_url == MOONSHOT_BASE_URL_INTL


def test_load_credentials_strips_whitespace_in_key() -> None:
    env = {"MOONSHOT_API_KEY": "  sk-padded  "}
    creds = load_kimi_credentials(env=env)
    assert creds.api_key == "sk-padded"


def test_load_credentials_raises_on_missing_key() -> None:
    with pytest.raises(KimiCredentialError, match="MOONSHOT_API_KEY"):
        load_kimi_credentials(env={})


def test_load_credentials_raises_on_blank_key() -> None:
    with pytest.raises(KimiCredentialError, match="MOONSHOT_API_KEY"):
        load_kimi_credentials(env={"MOONSHOT_API_KEY": "   "})


def test_load_credentials_intl_shorthand() -> None:
    creds = load_kimi_credentials(env={
        "MOONSHOT_API_KEY": "sk-1",
        "MOONSHOT_BASE_URL": "intl",
    })
    assert creds.base_url == MOONSHOT_BASE_URL_INTL


def test_load_credentials_cn_shorthand() -> None:
    creds = load_kimi_credentials(env={
        "MOONSHOT_API_KEY": "sk-1",
        "MOONSHOT_BASE_URL": "cn",
    })
    assert creds.base_url == MOONSHOT_BASE_URL_CN


def test_load_credentials_explicit_https_url() -> None:
    creds = load_kimi_credentials(env={
        "MOONSHOT_API_KEY": "sk-1",
        "MOONSHOT_BASE_URL": "https://my.proxy/moonshot/v1",
    })
    assert creds.base_url == "https://my.proxy/moonshot/v1"


def test_load_credentials_rejects_unknown_base_url_shorthand() -> None:
    with pytest.raises(KimiCredentialError, match="MOONSHOT_BASE_URL"):
        load_kimi_credentials(env={
            "MOONSHOT_API_KEY": "sk-1",
            "MOONSHOT_BASE_URL": "europe",
        })


# ---------------------------------------------------------------------------
# KimiCredentials.redacted
# ---------------------------------------------------------------------------


def test_redacted_masks_long_key() -> None:
    creds = KimiCredentials(
        api_key="sk-abcdefghijklmnop",
        base_url=MOONSHOT_BASE_URL_INTL,
    )
    out = creds.redacted()
    assert "abcdefghij" not in out
    assert "sk-a" in out and "mnop" in out


def test_redacted_masks_short_key_entirely() -> None:
    creds = KimiCredentials(api_key="abc", base_url=MOONSHOT_BASE_URL_INTL)
    assert "abc" not in creds.redacted()
    assert "****" in creds.redacted()


# ---------------------------------------------------------------------------
# build_kimi_model
# ---------------------------------------------------------------------------


class _FakeOpenAIModel:
    """Stand-in for :class:`strands.models.openai.OpenAIModel`.

    Records constructor kwargs verbatim so tests can assert on the
    wiring without needing the real openai SDK installed.
    """

    last_kwargs: dict[str, Any] | None = None

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        type(self).last_kwargs = kwargs


def test_build_kimi_model_wires_credentials() -> None:
    creds = KimiCredentials(api_key="sk-1", base_url=MOONSHOT_BASE_URL_INTL)
    model = build_kimi_model(credentials=creds, model_cls=_FakeOpenAIModel)
    assert isinstance(model, _FakeOpenAIModel)
    assert model.kwargs["client_args"]["api_key"] == "sk-1"
    assert model.kwargs["client_args"]["base_url"] == MOONSHOT_BASE_URL_INTL
    assert model.kwargs["model_id"] == DEFAULT_KIMI_MODEL_ID


def test_build_kimi_model_loads_env_when_no_credentials_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-env")
    monkeypatch.delenv("MOONSHOT_BASE_URL", raising=False)
    model = build_kimi_model(model_cls=_FakeOpenAIModel)
    assert model.kwargs["client_args"]["api_key"] == "sk-env"
    assert model.kwargs["client_args"]["base_url"] == MOONSHOT_BASE_URL_INTL


def test_build_kimi_model_forwards_params() -> None:
    creds = KimiCredentials(api_key="sk-1", base_url=MOONSHOT_BASE_URL_INTL)
    model = build_kimi_model(
        credentials=creds,
        params={"temperature": 0.0, "max_tokens": 32},
        model_cls=_FakeOpenAIModel,
    )
    assert model.kwargs["params"] == {"temperature": 0.0, "max_tokens": 32}


def test_build_kimi_model_omits_params_when_unset() -> None:
    creds = KimiCredentials(api_key="sk-1", base_url=MOONSHOT_BASE_URL_INTL)
    model = build_kimi_model(credentials=creds, model_cls=_FakeOpenAIModel)
    assert "params" not in model.kwargs


def test_build_kimi_model_accepts_explicit_model_id() -> None:
    creds = KimiCredentials(api_key="sk-1", base_url=MOONSHOT_BASE_URL_INTL)
    model = build_kimi_model(
        model_id="moonshot-v1-128k",
        credentials=creds,
        model_cls=_FakeOpenAIModel,
    )
    assert model.kwargs["model_id"] == "moonshot-v1-128k"


def test_build_kimi_model_raises_credential_error_with_no_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    with pytest.raises(KimiCredentialError):
        build_kimi_model(model_cls=_FakeOpenAIModel)


def test_kimi_dependency_error_subclasses_kimi_error() -> None:
    # The eval suite catches KimiError as a single catch-all; both
    # specific exceptions must descend from it.
    assert issubclass(KimiCredentialError, KimiError)
    assert issubclass(KimiDependencyError, KimiError)


def test_build_kimi_model_raises_dependency_error_when_openai_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Simulate an environment where strands.models.openai cannot be
    # imported because the underlying ``openai`` package is missing.
    # We do this by inserting a sentinel that raises ImportError on
    # the protected import line inside build_kimi_model.
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "strands.models.openai":
            raise ImportError("simulated: openai not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    creds = KimiCredentials(api_key="sk-1", base_url=MOONSHOT_BASE_URL_INTL)
    with pytest.raises(KimiDependencyError, match="openai"):
        build_kimi_model(credentials=creds)


# ---------------------------------------------------------------------------
# is_kimi_model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_id",
    [
        "kimi-k2.5",
        "kimi-k2",
        "KIMI-K2.5",
        "moonshot-v1-128k",
        "moonshot-v1-32k",
        "Moonshot-v1-Auto",
        "  kimi-k2.5  ",  # whitespace tolerated
    ],
)
def test_is_kimi_model_recognises_moonshot_family(model_id: str) -> None:
    from heaviside.llm import is_kimi_model

    assert is_kimi_model(model_id) is True


@pytest.mark.parametrize(
    "model_id",
    [
        "claude-opus-4-6",
        "claude-sonnet-4-5",
        "gpt-4o",
        "gpt-5",
        "llama3:8b",
        "bedrock/anthropic.claude-3-5-sonnet",
        "",
        "kimi",  # missing dash → not a member of the family
        "moonshot",
    ],
)
def test_is_kimi_model_rejects_non_moonshot(model_id: str) -> None:
    from heaviside.llm import is_kimi_model

    assert is_kimi_model(model_id) is False


def test_is_kimi_model_rejects_non_string() -> None:
    """Already-constructed Model objects are passed through unchanged.

    The factory uses this to distinguish "needs the builder" from
    "Strands already has a Model object".
    """
    from heaviside.llm import is_kimi_model

    class FakeModel:
        pass

    assert is_kimi_model(FakeModel()) is False  # type: ignore[arg-type]
    assert is_kimi_model(None) is False  # type: ignore[arg-type]
    assert is_kimi_model(42) is False  # type: ignore[arg-type]
