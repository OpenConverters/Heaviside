"""LLM layer: model tiers, factory, cache, context guard.

Model-agnostic by design — providers are wired through Strands. The default
provider for v0.1 is Kimi (Moonshot); Anthropic / OpenAI / Bedrock / local
slot in via Strands provider adapters without touching agent code.
"""

from __future__ import annotations

from heaviside.llm.model_tiers import (
    ModelTier,
    classify_model,
    context_window,
    is_review_role_allowed,
)

__all__ = [
    "ModelTier",
    "classify_model",
    "context_window",
    "is_review_role_allowed",
]
