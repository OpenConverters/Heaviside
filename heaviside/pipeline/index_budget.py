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


def _budget_mb() -> float:
    try:
        return max(256.0, float(os.environ.get("HEAVISIDE_MAX_INDEX_RSS_MB", "2500")))
    except (TypeError, ValueError):
        return 2500.0


def evict_if_over_budget() -> bool:
    """If process RSS is over the budget, clear every registered index cache and
    run GC. Returns True if it evicted. Call at the top of an index build so a
    large new index can't push the box into swap."""
    rss = _rss_mb()
    budget = _budget_mb()
    if rss <= budget:
        return False
    freed_entries = sum(len(c) for c in _REGISTERED_CACHES)
    for cache in _REGISTERED_CACHES:
        cache.clear()
    gc.collect()
    logger.warning(
        "index memory guard: RSS %.0f MB > budget %.0f MB — evicted %d cached "
        "index entries (they will rebuild on demand)",
        rss,
        budget,
        freed_entries,
    )
    return True


__all__ = ["evict_if_over_budget", "register_cache"]
