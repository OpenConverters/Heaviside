"""PyOpenMagnetics result cache.

Wraps the three PyOM call sites used by :mod:`heaviside.bridge` (whose
single-call latency ranges from seconds to minutes) with a
content-addressed JSON cache.  Re-running an unchanged
``(topology, spec, pyom_binary)`` triple becomes a JSON read — turning
the integration suite from a coffee-break into a sub-second loop.

Cache key derivation
--------------------

``sha256(canonical_json({"fn": fn_name, "args": args, "pyom_sha": ...}))``

where ``args`` is a tuple of positional arguments rendered through a
strict canonicaliser:

  * ``dict`` → key-sorted ``dict`` (recursive).
  * ``list``/``tuple`` → ``list`` (recursive).
  * ``str | int | float | bool | None`` → identity.
  * anything else → :class:`PyomCacheError` (no silent ``str(x)``
    coercion: per CLAUDE.md, an unrecognised type is a programmer
    error in the caller, not data to be guessed at).

``pyom_sha`` is the SHA-256 of the compiled PyOpenMagnetics extension
``.so`` on disk; rebuilding PyOM transparently invalidates every cache
entry.  Computed once per process and memoised.

No fallbacks
------------

Per ``CLAUDE.md`` ("no fallbacks, no defaults, no silent shortcuts —
throw"):

  * A cache file that exists but fails to parse as JSON raises
    :class:`PyomCacheError` rather than re-running PyOM.  Corruption
    is a bug to investigate, not to paper over.
  * The cache can be globally disabled by setting
    ``HEAVISIDE_PYOM_CACHE=0`` in the environment — in which case the
    helpers degrade to a passthrough that just calls the wrapped
    function.  This is the only sanctioned "skip the cache" path.
  * Set ``HEAVISIDE_PYOM_CACHE_DIR`` to override the default location
    (``<repo>/.heaviside/pyom-cache``).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any


__all__ = [
    "PyomCacheError",
    "cache_dir",
    "cache_enabled",
    "cached_call",
    "pyom_fingerprint",
]


class PyomCacheError(RuntimeError):
    """Raised on cache I/O / serialisation problems.

    Distinct from :class:`heaviside.bridge.BridgeError` because the
    failure mode is in the cache infrastructure, not in PyOM itself —
    surfacing it as a separate exception type avoids masking real PyOM
    misbehaviour behind a cache bug.
    """


# ---------------------------------------------------------------------------
# Environment / configuration
# ---------------------------------------------------------------------------


_DEFAULT_DIR = Path(__file__).resolve().parent.parent / ".heaviside" / "pyom-cache"


def cache_dir() -> Path:
    """Resolve the cache directory.

    Honours ``HEAVISIDE_PYOM_CACHE_DIR`` if set; falls back to
    ``<repo_root>/.heaviside/pyom-cache``.  Resolved fresh on every
    call so tests can monkeypatch ``os.environ`` between invocations.
    """
    override = os.environ.get("HEAVISIDE_PYOM_CACHE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return _DEFAULT_DIR


def cache_enabled() -> bool:
    """Return ``False`` when ``HEAVISIDE_PYOM_CACHE`` is set to ``"0"``.

    Any other value (including unset / empty / ``"1"`` / ``"true"``)
    keeps caching on.  Resolved on every call so tests can flip the
    switch mid-suite.
    """
    return os.environ.get("HEAVISIDE_PYOM_CACHE", "1") != "0"


# ---------------------------------------------------------------------------
# PyOpenMagnetics fingerprint
# ---------------------------------------------------------------------------


_FINGERPRINT_CACHE: str | None = None


def pyom_fingerprint() -> str:
    """SHA-256 of the compiled PyOpenMagnetics extension on disk.

    Memoised for the lifetime of the process — PyOM cannot be reloaded
    without a fresh Python invocation, so any change to the ``.so``
    file requires re-importing this module too.

    Raises :class:`PyomCacheError` if PyOpenMagnetics is not importable
    (we cannot stamp a cache key without knowing which binary will
    serve the miss).
    """
    global _FINGERPRINT_CACHE
    if _FINGERPRINT_CACHE is not None:
        return _FINGERPRINT_CACHE

    try:
        import PyOpenMagnetics  # noqa: F401 — used for __path__ side effect
    except ImportError as exc:
        raise PyomCacheError(
            "pyom_fingerprint: PyOpenMagnetics is not importable — cannot "
            "derive a cache key.  Disable the cache with "
            "HEAVISIDE_PYOM_CACHE=0 if you are intentionally running "
            "without PyOM."
        ) from exc

    pkg_dir = Path(next(iter(PyOpenMagnetics.__path__))).resolve()
    candidates = sorted(pkg_dir.glob("PyOpenMagnetics.*.so"))
    if not candidates:
        raise PyomCacheError(
            f"pyom_fingerprint: no PyOpenMagnetics.*.so found in {pkg_dir} "
            "— installation looks broken."
        )
    # In practice exactly one .so ships per platform tag; hash all of
    # them in deterministic order if more appear so a multi-ABI install
    # still yields a stable key.
    h = hashlib.sha256()
    for so in candidates:
        h.update(so.name.encode("utf-8"))
        h.update(b"\0")
        with so.open("rb") as fh:
            for chunk in iter(lambda fh=fh: fh.read(1 << 20), b""):
                h.update(chunk)
    _FINGERPRINT_CACHE = h.hexdigest()
    return _FINGERPRINT_CACHE


# ---------------------------------------------------------------------------
# Canonicalisation
# ---------------------------------------------------------------------------


def _canonicalise(value: Any, path: str = "<root>") -> Any:
    """Recursively normalise ``value`` into a JSON-safe shape.

    Throws :class:`PyomCacheError` on any unsupported type — see module
    docstring for rationale.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        # bool is a subclass of int; isinstance ordering doesn't matter
        # since both serialise identically through json.
        return value
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise PyomCacheError(
                    f"_canonicalise: non-string mapping key at {path}: "
                    f"{type(k).__name__}={k!r}"
                )
            out[k] = _canonicalise(v, f"{path}.{k}")
        return dict(sorted(out.items()))
    if isinstance(value, (list, tuple)):
        return [_canonicalise(v, f"{path}[{i}]") for i, v in enumerate(value)]
    raise PyomCacheError(
        f"_canonicalise: unsupported type at {path}: "
        f"{type(value).__name__}={value!r} — extend the canonicaliser "
        "explicitly rather than relying on str() coercion."
    )


def _cache_key(fn_name: str, args: Sequence[Any]) -> str:
    payload = {
        "fn": fn_name,
        "args": _canonicalise(list(args), f"{fn_name}.args"),
        "pyom_sha": pyom_fingerprint(),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Cached call
# ---------------------------------------------------------------------------


def cached_call(
    fn_name: str,
    args: Sequence[Any],
    *,
    call: Callable[[], Any],
) -> Any:
    """Look up ``(fn_name, args)`` in the cache; on miss invoke ``call``.

    ``call`` must be a zero-argument thunk that returns a JSON-safe
    result (PyOM dicts of plain Python types qualify).  The result is
    canonicalised on write — non-JSON-safe payloads throw rather than
    silently corrupting the cache.

    Parameters
    ----------
    fn_name :
        Stable label used in the cache key (typically the wrapped PyOM
        function name).  Caller-controlled, not introspected.
    args :
        Positional arguments mirror exactly the arguments passed to the
        wrapped call — they MUST canonicalise to a JSON-safe shape, see
        :func:`_canonicalise`.
    call :
        Thunk evaluated on miss.

    Returns
    -------
    Any
        The cached (or freshly computed) JSON-deserialised result.

    Raises
    ------
    PyomCacheError
        On serialisation failure, fingerprint failure, or corrupted
        cache file.  Bubbles up the underlying exception from ``call``
        unchanged.
    """
    if not cache_enabled():
        return call()

    key = _cache_key(fn_name, args)
    cdir = cache_dir()
    # Two-level fan-out keeps any single directory under ~256 entries
    # even for very large suites.
    path = cdir / key[:2] / f"{key}.json"

    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except json.JSONDecodeError as exc:
            raise PyomCacheError(
                f"cached_call: corrupted cache file {path} — JSON parse "
                f"failed ({exc.msg}).  Delete it and re-run to refresh, "
                "or set HEAVISIDE_PYOM_CACHE=0 to bypass."
            ) from exc

    result = call()
    # Canonicalise once for storage so we can't write a value we
    # couldn't later read back through json.load.
    canonical = _canonicalise(result, f"{fn_name}.result")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(canonical, fh, sort_keys=True, separators=(",", ":"),
                  ensure_ascii=False)
    os.replace(tmp, path)
    return canonical
