"""Kimi (Moonshot) provider wiring for Heaviside agents.

Per ``AGENTS.md`` §8, the v0.1 default LLM provider is **Kimi
(Moonshot)** — accessed via Moonshot's OpenAI-compatible REST API
through the :class:`strands.models.openai.OpenAIModel` adapter.

This module is the *only* place that knows about Moonshot's base
URL, environment-variable names, and credential shape.  Every other
component (the agent factory, the eval suite, the future MCP
server) constructs a Kimi-backed Strands model via
:func:`build_kimi_model`.

Strict-mode contract
--------------------

* :class:`KimiCredentialError` on missing ``MOONSHOT_API_KEY`` —
  never silently fall back to an anonymous client, never substitute
  a placeholder key.
* :class:`KimiDependencyError` when the ``openai`` Python SDK isn't
  installed — Strands's ``OpenAIModel`` requires it.  Heaviside's
  ``pyproject.toml`` does *not* list ``openai`` as a hard
  dependency (the unit test suite never needs it), so this is a
  deliberate runtime check rather than a build-time guarantee.
* No default API key in code, in tests, or in env-file fallbacks —
  the credential must come from the process environment or be
  passed explicitly to :func:`load_kimi_credentials`.

Why not a generic OpenAIClient wrapper?
---------------------------------------

Moonshot's API is OpenAI-shaped but Moonshot-flavoured: the base
URL differs (``api.moonshot.ai`` vs ``api.openai.com``), the model
ids differ (``kimi-k2.5`` / ``moonshot-v1-128k`` vs ``gpt-4o``),
context windows differ, and the rate-limit responses are reported
differently.  A dedicated module documents those facts and keeps
the agent factory model-agnostic.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "DEFAULT_KIMI_MODEL_ID",
    "KIMI_MODEL_PREFIXES",
    "MOONSHOT_BASE_URL_CN",
    "MOONSHOT_BASE_URL_INTL",
    "KimiCredentialError",
    "KimiCredentials",
    "KimiDependencyError",
    "KimiError",
    "build_kimi_model",
    "is_kimi_model",
    "load_kimi_credentials",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


#: Moonshot's OpenAI-compatible base URL outside mainland China.
MOONSHOT_BASE_URL_INTL: str = "https://api.moonshot.ai/v1"

#: Moonshot's mainland-China base URL.  Returned when
#: ``MOONSHOT_BASE_URL`` is set to ``"cn"`` (or any explicit URL).
MOONSHOT_BASE_URL_CN: str = "https://api.moonshot.cn/v1"

#: Default model id per ``heaviside/llm/model_tiers.json`` (Tier 1).
DEFAULT_KIMI_MODEL_ID: str = "kimi-k2.5"

#: Model-id prefixes that route through Moonshot.  ``kimi-k2.5`` and
#: any forthcoming ``kimi-*`` variants live on Moonshot; the
#: ``moonshot-v1-*`` family is the legacy line still served from the
#: same endpoint.  Used by :func:`is_kimi_model` to decide whether
#: :func:`heaviside.agents.load_agent` should build an
#: ``OpenAIModel`` object or pass the bare string through to Strands.
KIMI_MODEL_PREFIXES: tuple[str, ...] = ("kimi-", "moonshot-")

#: Environment variable names — kept in one place so renames are a
#: one-file change.
_ENV_API_KEY = "MOONSHOT_API_KEY"
_ENV_BASE_URL = "MOONSHOT_BASE_URL"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class KimiError(Exception):
    """Base class for every Kimi/Moonshot provider failure."""


class KimiCredentialError(KimiError):
    """The Moonshot API key is missing, blank, or otherwise unusable.

    Carries no key material in its message — callers may safely log
    this exception's ``str(exc)`` form.
    """


class KimiDependencyError(KimiError):
    """The ``openai`` Python package is not installed.

    Heaviside's strict-mode contract refuses to construct a
    "model placeholder" or to fall back to a different provider
    silently.  Install via ``pip install openai`` to enable the
    Kimi backend.
    """


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KimiCredentials:
    """Bundle of (api_key, base_url) required to talk to Moonshot."""

    api_key: str
    base_url: str

    def redacted(self) -> str:
        """Human-readable form with the API key masked.

        Useful in audit logs.  The first 4 and last 4 characters of
        the key are shown so two distinct keys can be told apart
        without leaking the full secret.
        """
        shown = "****" if len(self.api_key) <= 8 else f"{self.api_key[:4]}…{self.api_key[-4:]}"
        return f"KimiCredentials(api_key={shown!r}, base_url={self.base_url!r})"


def _resolve_base_url(raw: str | None) -> str:
    """Map ``MOONSHOT_BASE_URL`` shorthand to a concrete URL.

    Accepts ``None`` → :data:`MOONSHOT_BASE_URL_INTL`, ``"intl"`` →
    same, ``"cn"`` → :data:`MOONSHOT_BASE_URL_CN`, or any explicit
    URL starting with ``http``.  Anything else raises
    :class:`KimiCredentialError`.
    """
    if raw is None or raw.strip() == "" or raw.strip().lower() == "intl":
        return MOONSHOT_BASE_URL_INTL
    if raw.strip().lower() == "cn":
        return MOONSHOT_BASE_URL_CN
    url = raw.strip()
    if not url.startswith(("http://", "https://")):
        raise KimiCredentialError(
            f"MOONSHOT_BASE_URL={raw!r} is not a recognised shorthand "
            f"('intl', 'cn') and does not start with 'http://' or "
            f"'https://'"
        )
    return url


def load_kimi_credentials(
    *,
    env: Mapping[str, str] | None = None,
) -> KimiCredentials:
    """Read Moonshot credentials from the process environment.

    Args:
        env: Override ``os.environ`` (test hook).  When ``None`` the
            real process environment is consulted.

    Raises:
        KimiCredentialError: ``MOONSHOT_API_KEY`` is unset or blank,
            or ``MOONSHOT_BASE_URL`` carries an unrecognised value.
    """
    source = env if env is not None else os.environ
    api_key = source.get(_ENV_API_KEY, "")
    if not api_key or not api_key.strip():
        raise KimiCredentialError(
            f"environment variable {_ENV_API_KEY!r} is not set; "
            f"export your Moonshot key before constructing a Kimi "
            f"model.  No fallback is provided — Heaviside refuses to "
            f"construct anonymous LLM clients."
        )
    base_url = _resolve_base_url(source.get(_ENV_BASE_URL))
    return KimiCredentials(api_key=api_key.strip(), base_url=base_url)


# ---------------------------------------------------------------------------
# Model construction
# ---------------------------------------------------------------------------


def is_kimi_model(model_id: str) -> bool:
    """Return ``True`` when *model_id* should be routed through Moonshot.

    A non-string input returns ``False`` — defensive against callers
    that hand us an already-constructed Strands ``Model`` object
    (which must be passed through verbatim).

    The check is prefix-based against :data:`KIMI_MODEL_PREFIXES`,
    case-insensitive.  Anything not matching is some other provider's
    model id (Anthropic, OpenAI proper, Bedrock, …) and Strands is
    expected to handle the string itself.
    """
    if not isinstance(model_id, str):
        return False
    lowered = model_id.strip().lower()
    return any(lowered.startswith(prefix) for prefix in KIMI_MODEL_PREFIXES)


def build_kimi_model(
    *,
    model_id: str = DEFAULT_KIMI_MODEL_ID,
    credentials: KimiCredentials | None = None,
    params: dict[str, Any] | None = None,
    model_cls: Any = None,
) -> Any:
    """Construct a Strands ``OpenAIModel`` pointed at Moonshot.

    Args:
        model_id: One of ``"kimi-k2.5"`` (Tier 1, default),
            ``"moonshot-v1-128k"`` (Tier 2), or ``"moonshot-v1-32k"``
            (Tier 2).  Anything else is forwarded verbatim — Moonshot
            will reject unknown ids at first call.
        credentials: Pre-loaded credentials.  Defaults to
            :func:`load_kimi_credentials` (env-driven).
        params: Per-request parameter overrides forwarded as the
            ``params`` config of :class:`strands.models.openai.OpenAIModel`
            (e.g. ``{"temperature": 0.2, "max_tokens": 512}``).
        model_cls: Inject a fake ``OpenAIModel`` class — used by the
            unit tests to avoid requiring the ``openai`` SDK.  Default
            imports :class:`strands.models.openai.OpenAIModel`.

    Returns:
        A Strands ``Model`` instance ready to pass to ``strands.Agent``
        or to :func:`heaviside.agents.load_agent` via its ``model=`` kwarg.

    Raises:
        KimiCredentialError: ``credentials`` is ``None`` and the env
            doesn't carry a Moonshot key.
        KimiDependencyError: ``model_cls`` is ``None`` and the
            ``openai`` SDK isn't importable.
    """
    if credentials is None:
        credentials = load_kimi_credentials()

    if model_cls is None:
        try:
            from strands.models.openai import (
                OpenAIModel as model_cls,  # type: ignore[no-redef]
            )
        except ImportError as exc:
            raise KimiDependencyError(
                "the 'openai' package is required to construct a "
                "Kimi-backed Strands model; install with "
                "`pip install openai`"
            ) from exc

    # Thinking control (matches Proteus). HEAVISIDE_KIMI_DISABLE_THINKING=1
    # injects `thinking: {type: "disabled"}` via the OpenAI SDK extra_body
    # escape hatch and forces temperature=0.6 (Moonshot K2.5 rejects other
    # temps with thinking off). ~2-5x faster. Only wraps k2* (Moonshot) ids.
    import os as _os

    if (
        _os.environ.get("HEAVISIDE_KIMI_DISABLE_THINKING", "0") == "1"
        and "k2" in model_id.lower()
    ):
        _base = model_cls

        class _NoThinkKimiModel(_base):  # type: ignore[valid-type,misc]
            def format_request(self, *args: Any, **kw: Any) -> Any:
                request = super().format_request(*args, **kw)
                if isinstance(request, dict):
                    eb = request.get("extra_body")
                    eb = dict(eb) if isinstance(eb, dict) else {}
                    eb["thinking"] = {"type": "disabled"}
                    request["extra_body"] = eb
                    request["temperature"] = 0.6
                return request

        model_cls = _NoThinkKimiModel

    kwargs: dict[str, Any] = {
        "client_args": {
            "api_key": credentials.api_key,
            "base_url": credentials.base_url,
        },
        "model_id": model_id,
    }
    if params is not None:
        kwargs["params"] = dict(params)
    return model_cls(**kwargs)
