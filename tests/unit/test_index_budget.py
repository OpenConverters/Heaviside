"""The crossref index memory guard evicts all registered index caches when the
process crosses the RSS budget, so a large crossref can't OOM a shared host."""

from __future__ import annotations

import heaviside.pipeline.index_budget as ib


def test_all_index_caches_registered():
    # match_score + guardrails register their caches at import.
    import heaviside.pipeline.guardrails as g
    import heaviside.pipeline.match_score as ms

    reg = ib._REGISTERED_CACHES
    assert any(ms._MPN_ENV_INDEX_CACHE is c for c in reg)
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
    import heaviside.pipeline.match_score as ms

    ms._MPN_ENV_INDEX_CACHE["p"] = {"a": 1}
    g._TAS_INDEX_CACHE["q"] = {"b": 2}
    monkeypatch.setattr(ib, "_rss_mb", lambda: 9999.0)
    monkeypatch.setenv("HEAVISIDE_MAX_INDEX_RSS_MB", "2500")
    assert ib.evict_if_over_budget() is True
    assert not ms._MPN_ENV_INDEX_CACHE and not g._TAS_INDEX_CACHE


def test_no_evict_under_budget(monkeypatch):
    import heaviside.pipeline.match_score as ms

    ms._MPN_ENV_INDEX_CACHE["p"] = {"a": 1}
    monkeypatch.setattr(ib, "_rss_mb", lambda: 500.0)
    monkeypatch.setenv("HEAVISIDE_MAX_INDEX_RSS_MB", "2500")
    assert ib.evict_if_over_budget() is False
    assert ms._MPN_ENV_INDEX_CACHE  # untouched
    ms._MPN_ENV_INDEX_CACHE.clear()


def test_rss_mb_reads_something():
    # On Linux prod this reads /proc/self/statm; must be a non-negative float.
    assert ib._rss_mb() >= 0.0
