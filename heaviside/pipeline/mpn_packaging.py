"""Packaging-suffix and separator aware MPN resolution (ABT #137, #878).

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
  guessed. (Re-measured over the shipped catalogue — 82 507 magnetics + 253 830
  capacitors — the rules below produce 563 bases and zero such collisions; the
  guard is insurance against future data, not a live workaround. Squashing over
  the same corpus drops 249 of ~336 000 keys as ambiguous, i.e. 0.07 % refused
  rather than guessed.)

The same two properties extend to SEPARATORS. Engineers retype and re-wrap part
numbers, so one BOM carried the same Murata bead as ``BLM21AG601SN1D``,
``BLM-21A-G601SN1D``, ``BLM21/AG6/01SN1D`` and ``BLM21-AG601/SN1D`` — four
spellings of one part, none of which matched the catalogue. Punctuation in an
MPN is presentational (``EMK105BJ105KV-F`` and ``EMK105BJ105KVF`` are the same
orderable part), so a squashed form is tried after the exact and packaging
forms. Squashing NEVER removes an alphanumeric character, and a squashed key
reached by two catalogue records that are not the same part is dropped — the
identical poisoning rule. This is deliberately deterministic: the catalogue
answers, rather than an LLM being asked to guess what the engineer meant.
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
    # Murata EMI-suppression chip beads (BLM/BLE/BLA): the BOM carries the
    # orderable number BLM21AG601SN1D, the catalogue the base BLM21AG601SN1 —
    # the trailing letter is the taping code. The character before it must be a
    # DIGIT: every base in these families ends in the internal-code digit
    # (…SN1 / …SZ1 / …SH1 / …SN4), so requiring a digit keeps families whose
    # base genuinely ends in a letter (BLF02GD162GNE) out of reach.
    (
        "murata-chip-bead",
        re.compile(r"^(?:BLM|BLE|BLA)[0-9A-Z]{6,}[0-9][DLWKJEN]$"),
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


# Punctuation an engineer's transcription can introduce or drop without naming a
# different part. Alphanumerics are never removed.
_SEPARATORS = re.compile(r"[-/\\.,_ |()\[\]]+")


def squashed(mpn: str) -> str:
    """The MPN with presentational punctuation removed, upper-cased.

    ``BLM-21A-G601SN1D`` and ``BLM21/AG6/01SN1D`` both squash to
    ``BLM21AG601SN1D``. Returns "" when nothing alphanumeric is left."""
    if not mpn:
        return ""
    return _SEPARATORS.sub("", str(mpn).strip().upper())


def _same_part(a: Any, b: Any) -> bool:
    """True when two catalogue records describe the same part in different
    packaging. Compared on identity-bearing fields only; anything unequal (or
    uncomparable) counts as DIFFERENT, so the caller poisons the base.

    A non-dict "record" is compared by equality. The indexes here hold whole
    records, but the lightweight category index holds only a subtype string per
    MPN — treating two equal strings as different would poison every base it
    builds and quietly undo the resolution these functions exist to provide."""
    if a is b:
        return True
    if not isinstance(a, dict) or not isinstance(b, dict):
        return type(a) is type(b) and a == b
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


def build_squashed_index(exact_index: dict[str, Any]) -> dict[str, Any]:
    """Map separator-squashed MPN → record, for every catalogue entry whose MPN
    changes shape when punctuation is removed OR that could be reached by a
    differently-punctuated BOM spelling. Keys reached by two records that are
    not the same part are dropped (never guessed)."""
    squashed_index: dict[str, Any] = {}
    poisoned: set[str] = set()
    for mpn, record in exact_index.items():
        key = squashed(mpn).lower()
        if not key or key in poisoned:
            continue
        seen = squashed_index.get(key)
        if seen is None:
            squashed_index[key] = record
        elif not _same_part(seen, record):
            del squashed_index[key]
            poisoned.add(key)
    return squashed_index


def resolve(
    mpn: str,
    exact_index: dict[str, Any],
    base_index: dict[str, Any],
    squashed_index: dict[str, Any] | None = None,
) -> Any | None:
    """Look an MPN up allowing a packaging-suffix or separator difference.

    Order is deliberate: an exact hit always wins, then a packaging-suffix one,
    and a squashed (punctuation-insensitive) match is the last resort — so this
    can only ever add resolutions, never change an existing one."""
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
    hit = base_index.get(key)
    if hit is not None:
        return hit
    if squashed_index is None:
        return None
    # Punctuation differs (BLM-21A-G601SN1D vs BLM21AG601SN1D). Try the squashed
    # spelling, then the squashed spelling minus its packaging code.
    squashed_key = squashed(key).lower()
    if squashed_key and squashed_key != key:
        hit = exact_index.get(squashed_key) or squashed_index.get(squashed_key)
        if hit is not None:
            return hit
    else:
        hit = squashed_index.get(squashed_key)
        if hit is not None:
            return hit
    squashed_base = packaging_base(squashed_key)
    if squashed_base is not None:
        return exact_index.get(squashed_base.lower()) or squashed_index.get(squashed_base.lower())
    return None


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
