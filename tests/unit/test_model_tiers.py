"""Unit tests for heaviside.llm.model_tiers."""

from __future__ import annotations

import pytest

from heaviside.llm import ModelTier, classify_model, context_window, is_review_role_allowed


@pytest.mark.unit
class TestModelTiers:
    def test_kimi_is_tier_1(self) -> None:
        assert classify_model("kimi-k2.5") is ModelTier.TIER_1_CERTIFIED

    def test_haiku_is_tier_2(self) -> None:
        assert classify_model("claude-haiku-4-5") is ModelTier.TIER_2_SUPPORTED

    def test_small_llama_is_blocked(self) -> None:
        assert classify_model("llama3:8b") is ModelTier.BLOCKED

    def test_unknown_model_is_unknown(self) -> None:
        assert classify_model("nonexistent-model-9000") is ModelTier.UNKNOWN

    def test_context_window_known(self) -> None:
        assert context_window("kimi-k2.5") == 262144

    def test_context_window_unknown(self) -> None:
        assert context_window("nonexistent-model-9000") is None

    def test_review_allowed_for_tier_1(self) -> None:
        assert is_review_role_allowed("kimi-k2.5") is True

    def test_review_blocked_for_tier_3(self) -> None:
        assert is_review_role_allowed("llama3:70b") is False

    def test_review_blocked_for_unknown(self) -> None:
        assert is_review_role_allowed("nonexistent-model-9000") is False
