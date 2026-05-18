"""Model tier classification — ported from Proteus `proteus/model_tiers.py`.

Source of truth: `heaviside/llm/model_tiers.json`. Kept verbatim from Proteus
so existing tier judgements carry over. Update the JSON, not this file, to
add or reclassify a model.
"""

from __future__ import annotations

import json
from enum import StrEnum
from functools import lru_cache
from importlib import resources
from typing import TypedDict, cast


class ModelTier(StrEnum):
    """Model classification tiers."""

    TIER_1_CERTIFIED = "tier_1_certified"
    TIER_2_SUPPORTED = "tier_2_supported"
    TIER_3_EXPERIMENTAL = "tier_3_experimental"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class _TierEntry(TypedDict, total=False):
    description: str
    models: list[str]
    allowed_roles: list[str]
    min_context_tokens: int
    review_roles_allowed: bool
    review_warning: str
    complex_topology_warning: str
    requires_deterministic_pipeline: bool
    reason: str


class _Meta(TypedDict, total=False):
    version: str
    context_window_table: dict[str, int]
    review_roles: list[str]
    complex_topologies: list[str]


class _TiersFile(TypedDict, total=False):
    tier_1_certified: _TierEntry
    tier_2_supported: _TierEntry
    tier_3_experimental: _TierEntry
    blocked: _TierEntry
    _meta: _Meta


@lru_cache(maxsize=1)
def _load() -> _TiersFile:
    text = resources.files("heaviside.llm").joinpath("model_tiers.json").read_text()
    return cast(_TiersFile, json.loads(text))


def classify_model(model_id: str) -> ModelTier:
    """Return the tier for a given model identifier.

    Matching is exact on the strings listed under each tier's ``models`` key.
    """
    data = _load()
    for tier in (
        ModelTier.TIER_1_CERTIFIED,
        ModelTier.TIER_2_SUPPORTED,
        ModelTier.TIER_3_EXPERIMENTAL,
        ModelTier.BLOCKED,
    ):
        entry = cast(_TierEntry, data.get(tier.value, {}))
        if model_id in entry.get("models", []):
            return tier
    return ModelTier.UNKNOWN


def context_window(model_id: str) -> int | None:
    """Return the published context window for a model, if known."""
    meta = _load().get("_meta") or _Meta()
    return meta.get("context_window_table", {}).get(model_id)


def is_review_role_allowed(model_id: str) -> bool:
    """Whether a model is allowed in a review role (Ray / Nicola)."""
    tier = classify_model(model_id)
    if tier in (ModelTier.BLOCKED, ModelTier.UNKNOWN):
        return False
    data = _load()
    entry = cast(_TierEntry, data.get(tier.value, {}))
    return bool(entry.get("review_roles_allowed", False))
