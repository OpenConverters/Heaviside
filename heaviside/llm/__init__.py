"""LLM layer: model tiers, factory, cache, context guard.

Model-agnostic by design — providers are wired through Strands. The default
provider for v0.1 is Kimi (Moonshot); Anthropic / OpenAI / Bedrock / local
slot in via Strands provider adapters without touching agent code.
"""

from __future__ import annotations

from heaviside.llm.kimi import (
    DEFAULT_KIMI_MODEL_ID,
    KIMI_MODEL_PREFIXES,
    MOONSHOT_BASE_URL_CN,
    MOONSHOT_BASE_URL_INTL,
    KimiCredentialError,
    KimiCredentials,
    KimiDependencyError,
    KimiError,
    build_kimi_model,
    is_kimi_model,
    load_kimi_credentials,
)
from heaviside.llm.model_tiers import (
    ModelTier,
    classify_model,
    context_window,
    is_review_role_allowed,
)

__all__ = [
    "DEFAULT_KIMI_MODEL_ID",
    "KIMI_MODEL_PREFIXES",
    "MOONSHOT_BASE_URL_CN",
    "MOONSHOT_BASE_URL_INTL",
    "KimiCredentialError",
    "KimiCredentials",
    "KimiDependencyError",
    "KimiError",
    "ModelTier",
    "build_kimi_model",
    "classify_model",
    "context_window",
    "is_kimi_model",
    "is_review_role_allowed",
    "load_kimi_credentials",
]
