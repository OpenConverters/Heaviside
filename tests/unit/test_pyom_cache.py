"""Tests for :mod:`heaviside._pyom_cache`.

Covers:

  * key stability — same args yield same key, different args yield
    different keys, dict-key ordering doesn't matter.
  * miss-then-hit — first call invokes ``call``, second call reads the
    JSON without invoking again.
  * fingerprint propagates into the key (we monkeypatch
    ``pyom_fingerprint`` so this works without a PyOM rebuild).
  * env-disable — ``HEAVISIDE_PYOM_CACHE=0`` makes ``cached_call`` a
    passthrough that never touches disk.
  * env-dir-override — ``HEAVISIDE_PYOM_CACHE_DIR`` reroutes writes.
  * canonicaliser throws on unsupported types (per CLAUDE.md no
    fallbacks) and on non-string mapping keys.
  * corrupted cache file → :class:`PyomCacheError` (no silent regen).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from heaviside import _pyom_cache as cache


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch, tmp_path):
    """Isolate every test from sibling state.

    Routes the cache dir to a fresh ``tmp_path`` and stubs out the
    fingerprint so we never touch the real PyOM ``.so`` (the unit
    suite must run without PyOM installed).
    """
    monkeypatch.setenv("HEAVISIDE_PYOM_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("HEAVISIDE_PYOM_CACHE", raising=False)
    # Clear the memoised fingerprint so the monkeypatched stub is used.
    monkeypatch.setattr(cache, "_FINGERPRINT_CACHE", None, raising=False)
    monkeypatch.setattr(cache, "pyom_fingerprint", lambda: "deadbeef" * 8)
    yield


# ---------------------------------------------------------------------------
# Configuration knobs
# ---------------------------------------------------------------------------


class TestConfiguration:

    def test_cache_dir_honours_env_override(self, monkeypatch, tmp_path):
        target = tmp_path / "custom"
        monkeypatch.setenv("HEAVISIDE_PYOM_CACHE_DIR", str(target))
        assert cache.cache_dir() == target.resolve()

    def test_cache_dir_default_under_repo(self, monkeypatch):
        monkeypatch.delenv("HEAVISIDE_PYOM_CACHE_DIR", raising=False)
        got = cache.cache_dir()
        # Default lives inside .heaviside/ which is .gitignored.
        assert ".heaviside" in got.parts
        assert got.name == "pyom-cache"

    def test_cache_enabled_default(self, monkeypatch):
        monkeypatch.delenv("HEAVISIDE_PYOM_CACHE", raising=False)
        assert cache.cache_enabled() is True

    def test_cache_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("HEAVISIDE_PYOM_CACHE", "0")
        assert cache.cache_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "yes", ""])
    def test_cache_enabled_for_any_non_zero_value(self, monkeypatch, val):
        monkeypatch.setenv("HEAVISIDE_PYOM_CACHE", val)
        assert cache.cache_enabled() is True


# ---------------------------------------------------------------------------
# Canonicalisation
# ---------------------------------------------------------------------------


class TestCanonicalise:

    def test_dict_keys_sorted_recursively(self):
        a = cache._canonicalise({"b": 1, "a": {"y": 2, "x": 1}})
        b = cache._canonicalise({"a": {"x": 1, "y": 2}, "b": 1})
        assert list(a.keys()) == ["a", "b"]
        assert list(a["a"].keys()) == ["x", "y"]
        assert a == b

    def test_tuple_becomes_list(self):
        assert cache._canonicalise((1, 2, 3)) == [1, 2, 3]

    def test_primitives_passthrough(self):
        for v in (None, True, False, 0, 3.14, "hello"):
            assert cache._canonicalise(v) == v

    def test_unsupported_type_throws(self):
        with pytest.raises(cache.PyomCacheError, match="unsupported type"):
            cache._canonicalise(object())

    def test_non_string_dict_key_throws(self):
        with pytest.raises(cache.PyomCacheError, match="non-string mapping key"):
            cache._canonicalise({1: "one"})


# ---------------------------------------------------------------------------
# Cache key stability
# ---------------------------------------------------------------------------


class TestCacheKey:

    def test_same_args_same_key(self):
        k1 = cache._cache_key("f", ("a", {"x": 1, "y": 2}))
        k2 = cache._cache_key("f", ("a", {"y": 2, "x": 1}))
        assert k1 == k2

    def test_different_args_different_key(self):
        k1 = cache._cache_key("f", ("a", 1))
        k2 = cache._cache_key("f", ("a", 2))
        assert k1 != k2

    def test_different_fn_name_different_key(self):
        k1 = cache._cache_key("f", ("a",))
        k2 = cache._cache_key("g", ("a",))
        assert k1 != k2

    def test_fingerprint_invalidates_key(self, monkeypatch):
        k1 = cache._cache_key("f", ("a",))
        monkeypatch.setattr(cache, "pyom_fingerprint", lambda: "0" * 64)
        k2 = cache._cache_key("f", ("a",))
        assert k1 != k2


# ---------------------------------------------------------------------------
# Miss → hit
# ---------------------------------------------------------------------------


class TestCachedCall:

    def test_miss_invokes_thunk_then_hit_does_not(self, tmp_path):
        calls = {"n": 0}

        def thunk():
            calls["n"] += 1
            return {"data": [1, 2, 3], "scoring": 0.7}

        r1 = cache.cached_call("design_X", ("buck", {"v": 12}), call=thunk)
        r2 = cache.cached_call("design_X", ("buck", {"v": 12}), call=thunk)
        assert r1 == r2
        assert calls["n"] == 1

    def test_different_args_miss_separately(self):
        calls = {"n": 0}

        def thunk():
            calls["n"] += 1
            return calls["n"]

        cache.cached_call("f", ("a",), call=thunk)
        cache.cached_call("f", ("b",), call=thunk)
        cache.cached_call("f", ("a",), call=thunk)   # hit
        assert calls["n"] == 2

    def test_passthrough_when_disabled(self, monkeypatch):
        monkeypatch.setenv("HEAVISIDE_PYOM_CACHE", "0")
        calls = {"n": 0}

        def thunk():
            calls["n"] += 1
            return {"x": 1}

        cache.cached_call("f", ("a",), call=thunk)
        cache.cached_call("f", ("a",), call=thunk)
        assert calls["n"] == 2  # no caching → both invoke

    def test_disabled_does_not_create_files(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HEAVISIDE_PYOM_CACHE", "0")
        cache.cached_call("f", ("a",), call=lambda: {"x": 1})
        # tmp_path is the override dir; nothing should be written.
        assert list(tmp_path.iterdir()) == []

    def test_writes_canonicalised_json(self, tmp_path):
        result = cache.cached_call(
            "f", ("a",), call=lambda: {"b": 2, "a": 1}
        )
        # Return value is already canonical (key-sorted).
        assert list(result.keys()) == ["a", "b"]
        # Find the single produced file.
        files = list(tmp_path.rglob("*.json"))
        assert len(files) == 1
        on_disk = json.loads(files[0].read_text())
        assert list(on_disk.keys()) == ["a", "b"]

    def test_thunk_returning_unserialisable_throws(self):
        with pytest.raises(cache.PyomCacheError, match="unsupported type"):
            cache.cached_call("f", ("a",), call=lambda: object())

    def test_unserialisable_args_throws_before_thunk(self):
        calls = {"n": 0}

        def thunk():
            calls["n"] += 1
            return {"ok": True}

        with pytest.raises(cache.PyomCacheError, match="unsupported type"):
            cache.cached_call("f", (object(),), call=thunk)
        # Thunk must NOT have been invoked — key derivation happened first.
        assert calls["n"] == 0

    def test_corrupted_cache_throws(self, tmp_path):
        # Prime the cache with a real call.
        cache.cached_call("f", ("a",), call=lambda: {"x": 1})
        cache_file = next(tmp_path.rglob("*.json"))
        cache_file.write_text("{not valid json")

        with pytest.raises(cache.PyomCacheError, match="corrupted cache"):
            cache.cached_call("f", ("a",), call=lambda: {"x": 1})

    def test_thunk_exception_propagates(self):
        class Boom(RuntimeError):
            pass

        def thunk():
            raise Boom("nope")

        with pytest.raises(Boom, match="nope"):
            cache.cached_call("f", ("a",), call=thunk)

    def test_partial_write_uses_atomic_rename(self, tmp_path):
        cache.cached_call("f", ("a",), call=lambda: {"x": 1})
        # No leftover .tmp file should remain after a successful write.
        leftovers = list(tmp_path.rglob("*.tmp"))
        assert leftovers == []


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------


class TestFingerprint:

    def test_missing_pyom_throws(self, monkeypatch):
        # Undo the autouse stub so we exercise the real path.
        monkeypatch.setattr(cache, "pyom_fingerprint",
                            cache.__dict__["pyom_fingerprint"].__wrapped__
                            if hasattr(cache.pyom_fingerprint, "__wrapped__")
                            else _real_pyom_fingerprint())
        # Force ImportError by hiding PyOpenMagnetics from sys.modules
        # and from the import system.
        import sys
        monkeypatch.setitem(sys.modules, "PyOpenMagnetics", None)
        monkeypatch.setattr(cache, "_FINGERPRINT_CACHE", None,
                            raising=False)
        with pytest.raises(cache.PyomCacheError, match="not importable"):
            cache.pyom_fingerprint()


def _real_pyom_fingerprint():
    """Reload the real function out of the module's source.

    The autouse fixture replaces ``cache.pyom_fingerprint`` with a
    stub — to test the real implementation we need to bypass the
    monkeypatch.  Pulling it from the module via ``__dict__`` after a
    reload is the cleanest dodge.
    """
    import importlib
    fresh = importlib.reload(cache)
    return fresh.pyom_fingerprint
