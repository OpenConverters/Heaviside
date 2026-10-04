"""The crossref index memory guard evicts all registered index caches when the
process crosses the RSS budget, so a large crossref can't OOM a shared host."""

from __future__ import annotations

import pytest
import heaviside.pipeline.index_budget as ib


def test_all_index_caches_registered():
    # guardrails registers its caches at import (match_score reads through them).
    import heaviside.pipeline.guardrails as g

    reg = ib._REGISTERED_CACHES
    assert any(g._TAS_INDEX_CACHE is c for c in reg)
    assert any(g._TAS_LOOKUP_CACHE is c for c in reg)


def test_register_dedups_by_identity_not_equality():
    # Two distinct empty dicts are == but must both register (equality would
    # drop the second, leaving it un-evictable).
    before = len(ib._REGISTERED_CACHES)
    a: dict = {}
    b: dict = {}
    ib.register_cache(a)
    ib.register_cache(b)
    ib.register_cache(a)  # same identity — not added twice
    assert len(ib._REGISTERED_CACHES) == before + 2


def test_evicts_over_budget(monkeypatch):
    import heaviside.pipeline.guardrails as g

    g._TAS_KIND_INDEX_CACHE["p"] = {"a": "x"}
    g._TAS_INDEX_CACHE["q"] = {"b": 2}
    monkeypatch.setattr(ib, "_rss_mb", lambda: 9999.0)
    monkeypatch.setenv("HEAVISIDE_MAX_INDEX_RSS_MB", "2500")
    monkeypatch.setenv("HEAVISIDE_INDEX_EVICT_COOLDOWN_S", "0")  # no cooldown for the test
    assert ib.evict_if_over_budget() is True
    assert not g._TAS_KIND_INDEX_CACHE and not g._TAS_INDEX_CACHE


def test_budget_scales_with_ram(monkeypatch):
    # No explicit override -> budget scales with total RAM (45%, floor 2500).
    monkeypatch.delenv("HEAVISIDE_MAX_INDEX_RSS_MB", raising=False)
    monkeypatch.setattr(ib, "_total_ram_mb", lambda: 64000.0)
    assert ib._budget_mb() == pytest.approx(0.45 * 64000.0)
    monkeypatch.setattr(ib, "_total_ram_mb", lambda: 2000.0)  # tiny box -> floor
    assert ib._budget_mb() == 2500.0


def test_cooldown_prevents_thrash(monkeypatch):
    import heaviside.pipeline.guardrails as g

    monkeypatch.setattr(ib, "_rss_mb", lambda: 9999.0)
    monkeypatch.setenv("HEAVISIDE_MAX_INDEX_RSS_MB", "2500")
    monkeypatch.setenv("HEAVISIDE_INDEX_EVICT_COOLDOWN_S", "999")
    ib._LAST_EVICT_AT[0] = 0.0
    g._TAS_KIND_INDEX_CACHE["p"] = {"a": 1}
    assert ib.evict_if_over_budget() is True  # first eviction fires
    g._TAS_KIND_INDEX_CACHE["p"] = {"a": 1}
    assert ib.evict_if_over_budget() is False  # within cooldown -> no re-evict (no thrash)
    assert g._TAS_KIND_INDEX_CACHE  # left intact
    g._TAS_KIND_INDEX_CACHE.clear()


def test_no_evict_under_budget(monkeypatch):
    import heaviside.pipeline.guardrails as g

    g._TAS_KIND_INDEX_CACHE["p"] = {"a": 1}
    monkeypatch.setattr(ib, "_rss_mb", lambda: 500.0)
    monkeypatch.setenv("HEAVISIDE_MAX_INDEX_RSS_MB", "2500")
    assert ib.evict_if_over_budget() is False
    assert g._TAS_KIND_INDEX_CACHE  # untouched
    g._TAS_KIND_INDEX_CACHE.clear()


def test_rss_mb_reads_something():
    # On Linux prod this reads /proc/self/statm; must be a non-negative float.
    assert ib._rss_mb() >= 0.0


def test_swapped_out_memory_counts_against_the_budget(monkeypatch, tmp_path):
    """Once the host is short of RAM the kernel swaps the indexes out and RSS
    alone stays under budget — the guard must see resident + swap."""
    status = tmp_path / "status"
    status.write_text("Name:\tpython\nVmRSS:\t 3000000 kB\nVmSwap:\t 2000000 kB\n")
    real_open = open
    monkeypatch.setattr("builtins.open",
                        lambda f, *a, **k: real_open(status if f == "/proc/self/status" else f, *a, **k))
    assert ib._rss_mb() == pytest.approx((3000000 + 2000000) / 1024.0)
