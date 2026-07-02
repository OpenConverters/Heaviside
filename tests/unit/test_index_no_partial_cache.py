"""A catalogue read error must never be swallowed into a partial index that is
then cached for the process lifetime.

Both guardrails._tas_file_index and match_score._mpn_env_index build a per-file
MPN index once and cache it. If iter_envelopes raised partway (a corrupt NDJSON
line), the old code cached the truncated index forever — silently shrinking the
catalogue so G5 demotes valid substitutes as "hallucinations" and bulk scoring
misses real parts. The error must propagate and nothing must be cached.
"""

from __future__ import annotations

import pytest

from heaviside.catalogue import _reader
from heaviside.pipeline import guardrails, match_score


def _iter_that_fails_midway(path):
    # One processable-but-unindexed envelope, then a read error on the next line.
    yield (1, {"unrelated": {}})
    raise RuntimeError("corrupt NDJSON line 2")


@pytest.mark.parametrize(
    ("module", "fn_name", "cache_name"),
    [
        (guardrails, "_tas_file_index", "_TAS_INDEX_CACHE"),
        (match_score, "_mpn_env_index", "_MPN_ENV_INDEX_CACHE"),
    ],
)
def test_read_error_propagates_and_nothing_cached(
    monkeypatch, tmp_path, module, fn_name, cache_name
) -> None:
    monkeypatch.setattr(_reader, "iter_envelopes", _iter_that_fails_midway)
    cache = getattr(module, cache_name)
    cache.clear()

    path = tmp_path / "capacitors.ndjson"
    path.write_text("{}\n")

    fn = getattr(module, fn_name)
    with pytest.raises(RuntimeError, match="corrupt NDJSON"):
        fn(path)

    # The partial index must NOT have been cached.
    assert path.name not in cache


@pytest.mark.parametrize(
    ("module", "fn_name", "cache_name"),
    [
        (guardrails, "_tas_file_index", "_TAS_INDEX_CACHE"),
        (match_score, "_mpn_env_index", "_MPN_ENV_INDEX_CACHE"),
    ],
)
def test_complete_scan_is_cached(monkeypatch, tmp_path, module, fn_name, cache_name) -> None:
    def _iter_ok(path):
        yield (1, {"capacitor": {"manufacturerInfo": {"reference": "GOODPART-1"}}})

    monkeypatch.setattr(_reader, "iter_envelopes", _iter_ok)
    cache = getattr(module, cache_name)
    cache.clear()

    path = tmp_path / "capacitors.ndjson"
    path.write_text("{}\n")

    fn = getattr(module, fn_name)
    index = fn(path)
    assert "goodpart-1" in index
    assert path.name in cache  # a complete scan IS cached
