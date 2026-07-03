"""Memory budget guard for the crossref MPN→envelope indexes.

The scoring (``match_score``) and guardrail (``guardrails``) stages build
per-file ``mpn -> envelope`` indexes so bulk cross-reference doesn't re-scan
multi-megabyte NDJSON files per part. Those indexes hold the FULL envelope for
every part and are cached for the process lifetime — across categories that is
the whole ~600k-part catalogue, several GB. On a small shared host (Heaviside
co-resident with OpenMagnetics + umami) an unbounded index can exhaust RAM and
swap, hanging every service on the box.

This module bounds that: index builders register their cache dicts here and, at
the top of every build, call :func:`evict_if_over_budget`. When the process RSS
crosses ``HEAVISIDE_MAX_INDEX_RSS_MB`` (default 2500 MB), ALL registered index
caches are cleared and the garbage collector run, freeing the memory back to
the OS (so OpenMagnetics keeps working). The next lookup transparently rebuilds
whatever it needs — bounded memory in exchange for an occasional re-scan, which
is exactly the trade a memory-constrained box wants.
"""

from __future__ import annotations

import gc
import logging
import os

logger = logging.getLogger(__name__)

# Cache dicts registered by the index builders. Cleared as a group when the
# process crosses the RSS budget.
_REGISTERED_CACHES: list[dict] = []


def register_cache(cache: dict) -> None:
    """Register an index cache dict so the budget guard can evict it. Dedup by
    IDENTITY, not equality — every empty dict is ``==`` to every other, so an
    equality check would register only the first cache and silently drop the
    rest (leaving them un-evictable)."""
    if not any(cache is c for c in _REGISTERED_CACHES):
        _REGISTERED_CACHES.append(cache)


def _rss_mb() -> float:
    """Resident set size of THIS process, in MB. 0.0 if it can't be read (the
    guard then never fires — fail-open, never crash a run over a stat error)."""
    try:
        with open("/proc/self/statm") as fh:
            resident_pages = int(fh.read().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except Exception:
        try:
            import resource

            # ru_maxrss is KB on Linux, bytes on macOS — assume Linux (prod).
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
        except Exception:
            return 0.0


def _total_ram_mb() -> float:
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024.0
    except Exception:
        pass
    return 0.0


def _budget_mb() -> float:
    """RSS budget for the index caches, in MB.

    An explicit ``HEAVISIDE_MAX_INDEX_RSS_MB`` wins. Otherwise the budget SCALES
    with the machine: 45 % of total RAM (floor 2.5 GB). A fixed low cap was the
    bug — on a 64 GB dev box a 2.5 GB cap made every multi-category crossref
    thrash (evict → rebuild → evict), so nothing progressed; on the 7.7 GB prod
    host 45 % ≈ 3.5 GB still leaves room for the co-resident OpenMagnetics."""
    env = os.environ.get("HEAVISIDE_MAX_INDEX_RSS_MB")
    if env:
        try:
            return max(256.0, float(env))
        except (TypeError, ValueError):
            pass
    total = _total_ram_mb()
    if total > 0:
        return max(2500.0, 0.45 * total)
    return 2500.0


def _cooldown_s() -> float:
    try:
        return max(0.0, float(os.environ.get("HEAVISIDE_INDEX_EVICT_COOLDOWN_S", "30")))
    except (TypeError, ValueError):
        return 30.0


# monotonic timestamp of the last eviction — a cooldown between evictions stops
# a tight evict→rebuild→evict thrash loop when a single run's working set sits
# near the budget: after an eviction we let the caches rebuild and be USED for a
# while before the guard is allowed to fire again.
_LAST_EVICT_AT: list[float] = [0.0]


def evict_if_over_budget() -> bool:
    """If process RSS is over the budget (and past the eviction cooldown), clear
    every registered index cache and run GC. Returns True if it evicted."""
    if _rss_mb() <= _budget_mb():
        return False
    import time

    now = time.monotonic()
    if now - _LAST_EVICT_AT[0] < _cooldown_s():
        # Recently evicted — let the working set rebuild and do its work rather
        # than thrash. Memory may briefly exceed the budget; it's reclaimed at
        # the next eviction. (Bounded: a run touches a fixed set of categories.)
        return False
    _LAST_EVICT_AT[0] = now
    rss = _rss_mb()
    freed_entries = sum(len(c) for c in _REGISTERED_CACHES)
    for cache in _REGISTERED_CACHES:
        cache.clear()
    gc.collect()
    logger.warning(
        "index memory guard: RSS %.0f MB > budget %.0f MB — evicted %d cached "
        "index entries (rebuild on demand; next eviction ≥ %.0fs away)",
        rss,
        _budget_mb(),
        freed_entries,
        _cooldown_s(),
    )
    return True


__all__ = ["evict_if_over_budget", "register_cache"]
