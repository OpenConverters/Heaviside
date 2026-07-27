"""Packaging-suffix aware MPN resolution (ABT #137).

A customer BOM lists the base orderable part number (Coilcraft
``XGL5050-153ME``) while the catalogue stores the reeled variant
(``XGL5050-153MEC``). Exact-match original resolution misses, the part's real
Isat/IR never reach the gates, and the row ships judged against nothing — the
lt80603 L2 CRITICAL the FAE caught.

A naive prefix/suffix-tolerant match is NOT an acceptable fix: it false-matches
a shorter BOM MPN onto a longer DIFFERENT part (Würth ``7440320015`` →
``74403200150``, where the trailing DIGIT changes the part). So this module
strips ONLY suffixes that a named vendor's documented packaging convention can
produce, and never a digit.

Two safety properties, both tested:

* **Exact wins.** Callers consult the base index only after an exact miss, so
  every MPN that resolves today keeps resolving to the same record.
* **Ambiguity poisons.** If two distinct catalogue MPNs reduce to the same base
  and their records are not the same part, the base is dropped rather than
  guessed. (Measured over the shipped catalogue — 84 642 magnetics + 257 460
  capacitors — the rules below produce 3 725 bases and zero such collisions;
  the guard is insurance against future data, not a live workaround.)
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# Per-vendor packaging conventions. Each entry matches a FULL part number whose
# LAST character is a packaging code, captured so it can be removed. Anchored,
# family-scoped and letter-only on purpose: an unknown vendor is left alone.
_PACKAGING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Coilcraft moulded/shielded inductor families: <FAMILY><size>-<code><spec>
    # then a reel letter — C (7" machine-ready reel) or D (13" reel). Verified
    # against the catalogue: XAL1010-102ME / -102MED / XGL5050-153MEC.
    (
        "coilcraft",
        re.compile(r"^(?:XAL|XGL|XFL|XEL|MSS|LPS|SER|SLC|MSD|MOS)[0-9A-Z]*-[0-9]{3}[A-Z]{2}[CD]$"),
    ),
    # Murata MLCC families: the trailing letter is the taping/packaging code
    # (D 180 mm paper, L 180 mm embossed, W/K/J/E/N …). The thickness code that
    # precedes it stays part of the base, so GRM188R61A475KE15D and
    # ...KE19L keep DISTINCT bases and can never be conflated.
    (
        "murata",
        re.compile(r"^(?:GRM|GCM|GRT|GCJ|GCG|GJ8|KRM|KCM)[0-9A-Z]{6,}[DLWKJEN]$"),
    ),
)


def packaging_base(mpn: str) -> str | None:
    """The part number with its vendor packaging code removed, or None.

    None means "no known packaging convention applies" — the caller must then
    treat the MPN as atomic. Never strips a digit, and never strips from a
    vendor/family this module has not been taught."""
    if not mpn:
        return None
    upper = str(mpn).strip().upper()
    for _vendor, pattern in _PACKAGING_PATTERNS:
        if pattern.match(upper):
            return upper[:-1]
    return None


def _same_part(a: Any, b: Any) -> bool:
    """True when two catalogue records describe the same part in different
    packaging. Compared on identity-bearing fields only; anything unequal (or
    uncomparable) counts as DIFFERENT, so the caller poisons the base."""
    if a is b:
        return True
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    for field in ("value", "voltage", "package", "case", "manufacturer", "technology"):
        if a.get(field) != b.get(field):
            return False
    return True


def build_base_index(exact_index: dict[str, Any]) -> dict[str, Any]:
    """Map packaging-base → record for every entry whose MPN carries a known
    packaging code. Bases reached by two records that are not the same part are
    dropped (never guessed)."""
    base_index: dict[str, Any] = {}
    poisoned: set[str] = set()
    for mpn, record in exact_index.items():
        base = packaging_base(mpn)
        if not base:
            continue
        key = base.lower()
        if key in poisoned:
            continue
        seen = base_index.get(key)
        if seen is None:
            base_index[key] = record
        elif not _same_part(seen, record):
            del base_index[key]
            poisoned.add(key)
    return base_index


def resolve(mpn: str, exact_index: dict[str, Any], base_index: dict[str, Any]) -> Any | None:
    """Look an MPN up allowing a packaging-suffix difference in EITHER direction.

    Order is deliberate: an exact hit always wins, so this can only ever add
    resolutions, never change an existing one."""
    if not mpn:
        return None
    key = str(mpn).strip().lower()
    hit = exact_index.get(key)
    if hit is not None:
        return hit
    # BOM carries the packaging code, catalogue stores the base.
    base_of_query = packaging_base(key)
    if base_of_query is not None:
        hit = exact_index.get(base_of_query.lower())
        if hit is not None:
            return hit
    # BOM carries the base, catalogue stores the reeled variant.
    return base_index.get(key)


def expand_wanted(mpns: Iterable[str]) -> dict[str, str]:
    """For a set of wanted MPNs, the extra catalogue keys that should also match,
    mapped back to the wanted MPN. Used by streaming lookups that cannot hold a
    whole index in memory: a record whose own base is a wanted MPN is a hit."""
    extra: dict[str, str] = {}
    for mpn in mpns:
        key = str(mpn or "").strip().lower()
        if not key:
            continue
        base = packaging_base(key)
        if base:
            extra.setdefault(base.lower(), key)
    return extra
