"""Deterministic crossref guardrail system.

Post-parse checks applied to structured crossref JSON to catch
systematic LLM failure modes:

  - G0: Already-target-manufacturer parts misclassified as no_substitute.
  - G1: Capacitor value mismatch (substitute vs. original > 2x ratio).
  - G2: Resistor value drift (> 5% shift on feedback-dividers).
  - G3: Capacitor voltage downrate (substitute rated below original).
  - G4: Inductor over-rejection on footprint-only grounds.
  - G5: Substitute MPN does not exist in TAS catalogue.
  - G6: Voltage inadequacy admitted in the LLM's own notes.
  - GAECQ: Automotive grade propagation.
  - GFoot: Footprint class incompatibility (SMD vs. leaded, >3 size jump).
  - GStack: Multiple concurrent caveats on a single row.

Each guardrail:
  - Has a clear docstring explaining what it catches.
  - Returns structured fire-log entries when it fires.
  - Is independently testable.

The main entry point ``apply_guardrails`` returns ``(corrected_json,
fire_log_entries)`` and never silently substitutes defaults. If a
guardrail cannot run (e.g. TAS data unavailable), it emits a
diagnostic skip entry instead.

Ported from ``proteus.pipelines.crossref_strands._apply_crossref_guardrails``.
Adapted to use ``heaviside.catalogue._reader`` and
``heaviside.pipeline.value_parse`` for TAS lookups and SI parsing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from heaviside.pipeline import mpn_packaging
from heaviside.pipeline.value_parse import parse_si_value

# ---------------------------------------------------------------------------
# TAS data directory
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TAS_DATA_DEFAULT = _REPO_ROOT / "TAS" / "data"


# ---------------------------------------------------------------------------
# TAS MPN lookup (linear scan with caching)
# ---------------------------------------------------------------------------

# Keyed by (data_dir, kind, mpn) — the dir must be part of the key or a test
# fixture directory poisons lookups against the real catalogue (and vice versa).
_TAS_LOOKUP_CACHE: dict[tuple[str, str, str], dict | None] = {}
# Per-file MPN index: full path -> {mpn_lower: flat_record}. Built once on first
# access and reused, so validating N substitutes is O(file) total instead of
# O(N × file) — the latter made a large BOM (hundreds of magnetic substitutes,
# each previously scanning the whole 50 MB magnetics.ndjson) take ~20 min.
_TAS_INDEX_CACHE: dict[str, dict[str, dict]] = {}
_TAS_BASE_INDEX_CACHE: dict[str, dict[str, dict]] = {}
_TAS_SQUASHED_INDEX_CACHE: dict[str, dict[str, dict]] = {}
# LIGHT category index: mpn_lower -> electrical subtype ("" when the record has
# none). Answers "which category is this MPN, and is it a chip bead?" without
# retaining a single envelope. The heavy index above costs GB — a part number
# that resolves NOWHERE used to index the entire catalogue looking for it, which
# OOM-killed prod (ABT #886). Measured over the shipped catalogue: magnetics
# 82 479 keys in 2 s, capacitors 253 844 in 4 s, connectors 391 073 in 3 s, all
# three under 85 MB together.
_TAS_KIND_INDEX_CACHE: dict[str, dict[str, str]] = {}
_TAS_KIND_BASE_CACHE: dict[str, dict[str, str]] = {}
_TAS_KIND_SQUASHED_CACHE: dict[str, dict[str, str]] = {}

# Register every cache with the shared memory guard so a large crossref can't
# grow them past the RSS budget and OOM a shared host (index_budget).
try:
    from heaviside.pipeline.index_budget import register_cache as _register_cache

    _register_cache(_TAS_INDEX_CACHE)
    _register_cache(_TAS_BASE_INDEX_CACHE)
    _register_cache(_TAS_SQUASHED_INDEX_CACHE)
    _register_cache(_TAS_KIND_INDEX_CACHE)
    _register_cache(_TAS_KIND_BASE_CACHE)
    _register_cache(_TAS_KIND_SQUASHED_CACHE)
    _register_cache(_TAS_LOOKUP_CACHE)
except Exception:  # pragma: no cover - guard is best-effort
    pass

_TAS_KIND_TO_FILES = {
    "capacitor": ["capacitors.ndjson"],
    "resistor": ["resistors.ndjson"],
    "inductor": ["magnetics.ndjson"],
    "magnetic": ["magnetics.ndjson"],
    "chipBead": ["magnetics.ndjson"],
    "mosfet": ["mosfets.ndjson"],
    "diode": ["diodes.ndjson"],
    "connector": ["connectors.ndjson"],
    "analog": ["analog_ics.ndjson"],
    "timeBase": ["timing_devices.ndjson"],
}

# Catalogue file → CR canonical category, for the reverse question: "which
# category is this MPN?". Ordered by corpus size ascending so the cheapest
# indexes are consulted first.
_TAS_FILE_TO_KIND = {
    "timing_devices.ndjson": "timeBase",
    "analog_ics.ndjson": "analog",
    "mosfets.ndjson": "mosfet",
    "diodes.ndjson": "diode",
    "magnetics.ndjson": "magnetic",
    "connectors.ndjson": "connector",
    "resistors.ndjson": "resistor",
    "capacitors.ndjson": "capacitor",
}


def lookup_part_fields(
    part_number: str,
    component_kind: str,
    *,
    tas_data_dir: Path | None = None,
) -> dict | None:
    """Public catalogue lookup: flat record (capacitance, voltage, resistance,
    inductance, package, manufacturer, …) for an MPN, or ``None`` when the part
    is not catalogued. Used to ground report fields in real data instead of
    LLM-echoed values."""
    return _lookup_tas_part(part_number, component_kind, tas_data_dir=tas_data_dir)


def lookup_part_fields_bulk(
    wanted: dict[str, set[str]],
    *,
    tas_data_dir: Path | None = None,
) -> dict[tuple[str, str], dict]:
    """Flat records for a SMALL set of MPNs, without indexing whole files.

    ``wanted`` maps a component kind to the MPNs needed from it; the result is
    keyed ``(kind, mpn_lower)`` using the MPN the caller asked for, whichever
    spelling the catalogue actually stores.

    The per-file index is the right shape for validating hundreds of substitutes
    later in a run, and the wrong shape for the handful of rows a BOM starts
    with: building it to answer seven questions costs GB and, on prod, an
    eviction storm (ABT #886). This streams instead — one pass per needed file,
    memory proportional to the HITS.
    """
    from heaviside.catalogue._reader import iter_envelopes

    root = tas_data_dir or _TAS_DATA_DEFAULT
    out: dict[tuple[str, str], dict] = {}
    for kind, mpns in wanted.items():
        # variant spelling -> every asked MPN that reduces to it. A SET, not one
        # value: a BOM can spell the same part several ways (BLM21AG601SN1D,
        # BLM-21A-G601SN1D, …) and they all reduce to one catalogue key, so
        # mapping to a single MPN let one row claim the record and starved the
        # rest — which then fell back to building the whole-file index.
        keys: dict[str, set[str]] = {}
        for mpn in mpns:
            asked = str(mpn or "").strip().lower()
            if not asked:
                continue
            for variant in _mpn_match_keys(asked):
                keys.setdefault(variant, set()).add(asked)
        if not keys:
            continue
        # Counted down rather than re-tested per line: an `all(...)` over the
        # wanted MPNs would be O(rows) on every one of a quarter-million lines.
        remaining = {str(m or "").strip().lower() for m in mpns} - {""}
        for fname in _TAS_KIND_TO_FILES.get(kind, []):
            if not remaining:
                break
            path = root / fname
            if not path.is_file():
                continue
            for _lineno, env in iter_envelopes(path):
                for mi in _iter_part_records(env):
                    part = (mi.get("datasheetInfo") or {}).get("part") or {}
                    for ref in (mi.get("reference"), part.get("partNumber")):
                        if not isinstance(ref, str) or not ref.strip():
                            continue
                        matched = {
                            asked
                            for variant in _mpn_match_keys(ref.strip().lower())
                            for asked in keys.get(variant, ())
                        } & remaining
                        if not matched:
                            continue
                        flat = _flat_record_from_env(env, mi)
                        flat["mpn"] = ref.strip()
                        for asked in matched:
                            out[(kind, asked)] = flat
                        remaining -= matched
                        break
                if not remaining:
                    break  # every MPN this kind wanted is accounted for
    return out


def _mpn_match_keys(mpn: str) -> set[str]:
    """Every spelling under which an MPN should be considered the same part."""
    key = mpn.strip().lower()
    if not key:
        return set()
    keys = {key}
    squashed = mpn_packaging.squashed(key).lower()
    if squashed:
        keys.add(squashed)
    for form in (key, squashed):
        base = mpn_packaging.packaging_base(form) if form else None
        if base:
            keys.add(base.lower())
    return keys


def lookup_mpn_category(
    part_number: str,
    *,
    tas_data_dir: Path | None = None,
    only_kinds: set[str] | None = None,
) -> str | None:
    """Authoritative CR category for an MPN, from the internal catalogue.

    Returns the canonical category of the catalogue file the MPN is indexed
    in, or ``None`` when the part is not catalogued. Used to classify BOM rows
    that carry only a part number — the catalogue answers instead of an LLM
    guessing from the MPN's shape.

    The magnetics file holds inductors, transformers AND chip beads, so the
    file alone is not the answer for it: a bead reported as ``magnetic`` gets
    cross-referenced against power inductors (ABT #874). The record's electrical
    subtype refines it.

    Reads the LIGHT index, not the record index: this question needs a category
    and a subtype, and an MPN that resolves NOWHERE consults every catalogue file
    before it can answer ``None``. Doing that over whole envelopes cost ~10 GB
    and OOM-killed the prod box (ABT #886); over the light index the same sweep
    is seconds and tens of MB.

    ``only_kinds`` narrows it further, for a caller that already knows which
    files could possibly have gained the part (the librarian just wrote one).
    """
    if not part_number or not part_number.strip():
        return None
    root = tas_data_dir or _TAS_DATA_DEFAULT
    mpn_l = part_number.strip().lower()
    for fname, kind in _TAS_FILE_TO_KIND.items():
        if only_kinds is not None and kind not in only_kinds:
            continue
        path = root / fname
        if not path.is_file():
            continue
        exact = _tas_kind_index(path)
        # "" is a real value here (a record with no subtype), so test for None.
        subtype = exact.get(mpn_l)
        if subtype is None:
            base, squashed = _tas_kind_variant_indexes(path)
            if _has_variant_spelling(mpn_l):
                subtype = mpn_packaging.resolve(mpn_l, exact, base, squashed)
            else:
                subtype = squashed.get(mpn_l)
        if subtype is None:
            continue
        if kind == "magnetic" and subtype == "chipBead":
            return "chipBead"
        return kind
    return None


def catalogue_records_with_prefix(
    stem: str, *, limit: int = 8, tas_data_dir: Path | None = None
) -> list[dict]:
    """Catalogue records whose MPN begins with ``stem``.

    Answers "is this a truncated part number?" for a row that resolved to
    nothing — ``BLM21AG601S`` is a real stem shared by BLM21AG601SH1 / SN1 /
    SZ1, and saying so beats an unexplained no_substitute (ABT #878). Records,
    not just MPNs, so the caller can also say what actually DIFFERS between the
    completions.

    STREAMS the catalogue rather than indexing it. The index-based version of
    this OOM-killed prod: an unresolvable stem is by definition not in any file,
    so populating the indexes to search them meant building a full MPN index for
    every catalogue file — including a 486 MB connectors table — to phrase a
    diagnostic. Reading line by line is O(1) memory, and the cheap substring
    pre-filter means only candidate lines are ever parsed.
    """
    key = mpn_packaging.squashed(stem).lower()
    if not key or len(key) < 4:
        return []
    root = tas_data_dir or _TAS_DATA_DEFAULT
    needle = key.encode()
    found: dict[str, dict] = {}
    for fname in _TAS_FILE_TO_KIND:
        path = root / fname
        if not path.is_file():
            continue
        try:
            with open(path, "rb") as fh:
                for raw in fh:
                    # Pre-filter on the raw bytes: the overwhelming majority of
                    # lines cannot match, and json.loads is what costs.
                    if needle not in raw.lower():
                        continue
                    try:
                        env = json.loads(raw.decode("utf-8", "replace"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    for record in _flat_records_in(env):
                        mpn = str(record.get("mpn") or "")
                        low = mpn_packaging.squashed(mpn).lower()
                        if low == key or low in found or not low.startswith(key):
                            continue
                        found[low] = record
                        if len(found) > limit:
                            return [found[k] for k in sorted(found)]
        except OSError:
            continue
    return [found[k] for k in sorted(found)]


def _flat_records_in(env: dict):
    """Every (mpn-carrying) flat record in one catalogue envelope."""
    for top_key in _ENVELOPE_TOP_KEYS:
        sub = env.get(top_key)
        if not isinstance(sub, dict):
            continue
        inner_keys = (
            tuple(sub.keys()) if top_key in ("analog", "timeBase") else (None, "mosfet", "diode", "igbt")
        )
        for inner_key in inner_keys:
            record = sub if inner_key is None else sub.get(inner_key)
            if not isinstance(record, dict):
                continue
            mi = record.get("manufacturerInfo")
            if not isinstance(mi, dict):
                continue
            part = (mi.get("datasheetInfo") or {}).get("part") or {}
            ref = mi.get("reference") or part.get("partNumber")
            if isinstance(ref, str) and ref.strip():
                flat = _flat_record_from_env(env, mi)
                flat["mpn"] = ref.strip()
                yield flat


def _flat_record_from_env(env: dict, mi: dict) -> dict:
    """Extract the commonly-needed fields from an envelope's manufacturerInfo.
    Handles ``electrical`` as either a dict (caps/resistors) or a LIST (magnetics
    v2) — reading it as a bare dict used to throw and abort the whole lookup."""
    di = mi.get("datasheetInfo") or {}
    elec_raw = di.get("electrical")
    if isinstance(elec_raw, list):
        elec = next((x for x in elec_raw if isinstance(x, dict)), {})
    elif isinstance(elec_raw, dict):
        elec = elec_raw
    else:
        elec = {}
    part_info = di.get("part") or {}
    cap_obj = elec.get("capacitance")
    cap_val = cap_obj.get("nominal") if isinstance(cap_obj, dict) else cap_obj
    res_obj = elec.get("resistance")
    res_val = res_obj.get("nominal") if isinstance(res_obj, dict) else res_obj
    ind_obj = elec.get("inductance")
    ind_val = ind_obj.get("nominal") if isinstance(ind_obj, dict) else ind_obj
    return {
        "capacitance": cap_val,
        "voltage": elec.get("ratedVoltage"),
        "resistance_Ohm": res_val,
        "inductance": ind_val,
        # Magnetics share one catalogue file across inductors, transformers,
        # chip beads and cable cores; the subtype is what tells them apart.
        "subtype": elec.get("subtype"),
        "package": part_info.get("caseCode") or part_info.get("case") or part_info.get("package"),
        "manufacturer": mi.get("name"),
        "family": mi.get("family") or part_info.get("series"),
        "status": mi.get("status"),
        "raw_envelope": env,
    }


def _tas_file_index(path: Path) -> dict[str, dict]:
    """Return (building+caching once) an mpn_lower -> flat_record index for an
    NDJSON catalogue file.

    A read error (e.g. a corrupt NDJSON line) PROPAGATES — it must never be
    swallowed into a partial index that is then cached for the process
    lifetime. A truncated index silently shrinks the catalogue, making G5
    demote valid substitutes as "hallucinations". The cache is populated ONLY
    after a complete, successful scan; callers pass only existing files.
    """
    cached = _TAS_INDEX_CACHE.get(str(path))
    if cached is not None:
        return cached

    # Bound memory before building another full-file index (see index_budget):
    # keeps a large crossref from exhausting RAM on a shared host.
    from heaviside.pipeline.index_budget import evict_if_over_budget

    evict_if_over_budget()

    from heaviside.catalogue._reader import iter_envelopes

    index: dict[str, dict] = {}
    for _lineno, env in iter_envelopes(path):
        for top_key in (
            "capacitor",
            "semiconductor",
            "resistor",
            "magnetics",
            "magnetic",
            "connector",
            "analog",
            "timeBase",
        ):
            sub = env.get(top_key)
            if not isinstance(sub, dict):
                continue
            # `analog`/`timeBase` nest the record under a per-row FAMILY key
            # (operationalAmplifier / oscillator / …) — descend every child;
            # the fixed inner keys cover the semiconductor split.
            inner_keys: tuple = (
                tuple(sub.keys())
                if top_key in ("analog", "timeBase")
                else (None, "mosfet", "diode", "igbt")
            )
            for inner_key in inner_keys:
                record = sub if inner_key is None else sub.get(inner_key)
                if not isinstance(record, dict):
                    continue
                mi = record.get("manufacturerInfo")
                if not isinstance(mi, dict):
                    continue
                # Key by reference AND part.partNumber: capacitors/resistors
                # carry the MPN only in part.partNumber (no reference), and
                # were previously invisible to every index-based lookup.
                part = (mi.get("datasheetInfo") or {}).get("part") or {}
                for ref in (mi.get("reference"), part.get("partNumber")):
                    if isinstance(ref, str) and ref.strip():
                        key = ref.strip().lower()
                        if key not in index:
                            flat = _flat_record_from_env(env, mi)
                            # The catalogue's own spelling of this MPN. The key is
                            # lower-cased for matching, so anything that shows an
                            # MPN to a user must read it from here instead.
                            flat["mpn"] = ref.strip()
                            index[key] = flat
    # Only reached after the FULL scan succeeds — never cache a partial index.
    _TAS_INDEX_CACHE[str(path)] = index
    return index


def _tas_base_index(path: Path) -> dict[str, dict]:
    """Packaging-base → record index for a catalogue file (ABT #137), cached
    alongside the exact index it is derived from."""
    exact = _tas_file_index(path)   # first: an eviction here must not be masked
    cached = _TAS_BASE_INDEX_CACHE.get(str(path))
    if cached is None:
        cached = mpn_packaging.build_base_index(exact)
        _TAS_BASE_INDEX_CACHE[str(path)] = cached
    return cached


# The top-level keys a catalogue envelope can carry, and how to descend to the
# per-part records inside them. Shared by the heavy and light index builders so
# the two can never disagree about what counts as a part.
_ENVELOPE_TOP_KEYS = (
    "capacitor",
    "semiconductor",
    "resistor",
    "magnetics",
    "magnetic",
    "connector",
    "analog",
    "timeBase",
)


def _iter_part_records(env: dict):
    """Yield each ``manufacturerInfo`` an envelope carries for a PART.

    Deliberately not a regex over the raw line: that is 3× faster but wrong.
    Measured against this traversal it over-collects 14 729 MPNs in magnetics
    alone — distributor SKUs such as ``994-XFL2010-472MEC`` sitting in
    ``distributorsInfo`` — which would index a Mouser order code as though it
    were the manufacturer's part number.
    """
    for top_key in _ENVELOPE_TOP_KEYS:
        sub = env.get(top_key)
        if not isinstance(sub, dict):
            continue
        inner_keys = (
            tuple(sub.keys())
            if top_key in ("analog", "timeBase")
            else (None, "mosfet", "diode", "igbt")
        )
        for inner_key in inner_keys:
            record = sub if inner_key is None else sub.get(inner_key)
            if not isinstance(record, dict):
                continue
            mi = record.get("manufacturerInfo")
            if isinstance(mi, dict):
                yield mi


def _electrical_subtype(mi: dict) -> str:
    """The record's electrical subtype ("inductor" / "chipBead" / …), or ""."""
    electrical = (mi.get("datasheetInfo") or {}).get("electrical")
    if isinstance(electrical, list):
        first = next((x for x in electrical if isinstance(x, dict)), {})
    elif isinstance(electrical, dict):
        first = electrical
    else:
        return ""
    subtype = first.get("subtype")
    return subtype if isinstance(subtype, str) else ""


def _tas_kind_index(path: Path) -> dict[str, str]:
    """``mpn_lower -> electrical subtype`` for a catalogue file, no envelopes.

    Same traversal and same keys as :func:`_tas_file_index` — so an MPN resolves
    identically through both — but it keeps a short string per part instead of
    the whole record.
    """
    cached = _TAS_KIND_INDEX_CACHE.get(str(path))
    if cached is not None:
        return cached

    from heaviside.catalogue._reader import iter_envelopes

    index: dict[str, str] = {}
    for _lineno, env in iter_envelopes(path):
        for mi in _iter_part_records(env):
            part = (mi.get("datasheetInfo") or {}).get("part") or {}
            subtype = _electrical_subtype(mi)
            for ref in (mi.get("reference"), part.get("partNumber")):
                if isinstance(ref, str) and ref.strip():
                    index.setdefault(ref.strip().lower(), subtype)
    # Only after a COMPLETE scan — a partial index silently shrinks the
    # catalogue and would misreport parts as uncatalogued.
    _TAS_KIND_INDEX_CACHE[str(path)] = index
    return index


def _tas_kind_variant_indexes(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """(packaging-base, separator-squashed) views of the light index."""
    exact = _tas_kind_index(path)
    key = str(path)
    base = _TAS_KIND_BASE_CACHE.get(key)
    if base is None:
        base = mpn_packaging.build_base_index(exact)
        _TAS_KIND_BASE_CACHE[key] = base
    squashed = _TAS_KIND_SQUASHED_CACHE.get(key)
    if squashed is None:
        squashed = mpn_packaging.build_squashed_index(exact)
        _TAS_KIND_SQUASHED_CACHE[key] = squashed
    return base, squashed


def _has_variant_spelling(mpn: str) -> bool:
    """Whether an MPN could match a catalogue entry under a DIFFERENT spelling.

    The derived (packaging-base / separator-squashed) indexes hold an entry per
    catalogue MPN, so building them costs real memory on a 600 MB file — and for
    an MPN with no packaging code and no punctuation there is nothing they could
    resolve that the exact index did not. Prod is memory-tight enough to OOM on a
    catalogue-wide sweep, so the pathological case (a bare unresolvable stem
    consulting every file) must not pay for indexes that cannot help it."""
    key = str(mpn or "").strip().lower()
    if not key:
        return False
    return mpn_packaging.packaging_base(key) is not None or mpn_packaging.squashed(key).lower() != key


def _resolve_with_variants(mpn: str, path: Path, index: dict[str, dict]) -> dict | None:
    """Exact hit, else a packaging/separator variant — building the derived
    indexes only when one could actually match."""
    hit = index.get(mpn)
    if hit is not None:
        return hit
    if not _has_variant_spelling(mpn):
        # The catalogue could still hold a punctuated spelling of a bare query,
        # which only the squashed index sees; that is one derived index, not two.
        return _tas_squashed_index(path).get(mpn)
    return mpn_packaging.resolve(mpn, index, _tas_base_index(path), _tas_squashed_index(path))


def _tas_squashed_index(path: Path) -> dict[str, dict]:
    """Separator-squashed MPN → record index for a catalogue file (ABT #878),
    cached alongside the exact index it is derived from."""
    exact = _tas_file_index(path)   # first: an eviction here must not be masked
    cached = _TAS_SQUASHED_INDEX_CACHE.get(str(path))
    if cached is None:
        cached = mpn_packaging.build_squashed_index(exact)
        _TAS_SQUASHED_INDEX_CACHE[str(path)] = cached
    return cached


def _lookup_tas_part(
    part_number: str,
    component_kind: str,
    *,
    tas_data_dir: Path | None = None,
) -> dict | None:
    """Look up a part's parsed specs in TAS NDJSON files.

    Returns a flat dict of commonly-needed fields (capacitance, voltage,
    resistance, etc.), or ``None`` if the part is not found. Uses a per-file
    MPN index (built once) so repeated lookups don't re-scan multi-megabyte
    NDJSON files.
    """
    if not part_number or part_number == "no_substitute":
        return None
    root = tas_data_dir or _TAS_DATA_DEFAULT
    key = (str(root), component_kind, part_number.strip().lower())
    if key in _TAS_LOOKUP_CACHE:
        return _TAS_LOOKUP_CACHE[key]
    filenames = _TAS_KIND_TO_FILES.get(component_kind)
    # Fall back to all NDJSON files if kind is unknown.
    if not filenames:
        filenames = [f.name for f in root.glob("*.ndjson")]

    mpn_l = part_number.strip().lower()
    result: dict | None = None
    for fname in filenames:
        path = root / fname
        if not path.is_file():
            continue
        # Packaging-suffix aware (ABT #137): the BOM lists the base orderable
        # MPN while the catalogue stores the reeled variant (XGL5050-153ME vs
        # -153MEC). Exact hits still win, so no MPN that resolves today moves.
        hit = _resolve_with_variants(mpn_l, path, _tas_file_index(path))
        if hit is not None:
            result = hit
            break

    _TAS_LOOKUP_CACHE[key] = result
    return result


def _mpn_exists_in_tas(
    mpn: str,
    *,
    tas_data_dir: Path | None = None,
) -> bool:
    """Return True if *mpn* appears in any TAS NDJSON file.

    Checks all component kinds (capacitor, resistor, magnetics, etc.), so a
    valid substitute of a kind without its own lookup table — an IGBT, a
    thermistor — is not mistaken for a hallucination.

    Reads the LIGHT index. This is a boolean question, and answering it from the
    record index meant loading the full envelope of every part in every file the
    glob reaches — which includes circuits.ndjson at 1.2 GB and
    quarantine.ndjson, neither of them a part catalogue. The result exceeded the
    index memory budget, so the guard evicted and the next check rebuilt it, and
    G5 runs once per substitute: three correction rounds of that turned a review
    into 59 minutes of thrash on prod (ABT #886). Same files, same answer, a
    fraction of the memory.
    """
    root = tas_data_dir or _TAS_DATA_DEFAULT
    if not root.is_dir():
        return False
    if not mpn or not mpn.strip():
        return False

    mpn_l = mpn.strip().lower()
    return any(mpn_l in _tas_kind_index(f) for f in sorted(root.glob("*.ndjson")))


# ---------------------------------------------------------------------------
# Fire log helpers
# ---------------------------------------------------------------------------


def _make_fire(
    guardrail_id: str,
    ref_des: str,
    before: str | None,
    after: str,
    reason: str,
) -> dict:
    """Create a structured fire-log entry."""
    return {
        "guardrail_id": guardrail_id,
        "ref_des": ref_des or "?",
        "before": before,
        "after": after,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# BOM lookup helper
# ---------------------------------------------------------------------------


def _row_kind(*dicts: dict) -> str:
    """Lowered component kind from the first dict that carries one.

    Normalized BOM rows and crossref rows carry ``component_type``; legacy /
    Proteus-style rows carry ``type``. Guards that only read ``type`` silently
    skipped every normalized row (a 1000×-wrong resistor once shipped as
    "partial" because G2 never saw a kind for it).
    """
    for d in dicts:
        for key in ("component_type", "type"):
            v = d.get(key)
            if v:
                return str(v).lower()
    return ""


def _build_bom_by_ref(source_bom: list[dict]) -> dict[str, dict]:
    """Build a ``{ref_des: bom_row}`` mapping, expanding grouped refs."""
    bom_by_ref: dict[str, dict] = {}
    for c in source_bom:
        rd = str(c.get("ref_des", "") or "")
        for sub in re.split(r"[,\s]+", rd):
            sub = sub.strip()
            if sub:
                bom_by_ref[sub] = c
    return bom_by_ref


# ---------------------------------------------------------------------------
# Individual guardrails
# ---------------------------------------------------------------------------


def _normalize_manufacturer_name(name: str) -> str:
    """Lowercase, drop non-alphanumerics and common suffixes for matching.

    'Würth Elektronik' / 'Wurth Elektronik eiSos' / 'WE' all collapse so a
    BOM-extracted manufacturer can be compared to the target regardless of
    spelling/casing/legal-suffix noise."""
    import unicodedata

    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = n.lower()
    for suffix in (
        "elektronik",
        "electronics",
        "electronic",
        "eisos",
        "technologies",
        "technology",
        "semiconductor",
        "semiconductors",
        "incorporated",
        "inc",
        "corporation",
        "corp",
        "gmbh",
        "ltd",
        "llc",
        "co",
        "limited",
    ):
        n = n.replace(suffix, " ")
    return "".join(ch for ch in n if ch.isalnum())


def _manufacturer_matches(a: str, b: str) -> bool:
    """True if two manufacturer names refer to the same maker (either
    normalized form contains the other; guards against empty/too-short)."""
    na, nb = _normalize_manufacturer_name(a), _normalize_manufacturer_name(b)
    if len(na) < 3 or len(nb) < 3:
        return False
    return na in nb or nb in na


def _g0_already_target_manufacturer(
    comps: list[dict],
    target_manufacturer: str,
    fires: list[dict],
    *,
    tas_data_dir: Path | None = None,
) -> None:
    """G0: If the original_pn is already the target manufacturer's part
    AND exists in TAS, force status='exact' with substitute_pn=original_pn.

    Manufacturer-AGNOSTIC: looks the original MPN up in TAS and compares
    the part's catalogued manufacturer to the target — no per-manufacturer
    MPN-pattern regex. Pre-empts the LLM hallucinating 'no_substitute' for
    parts that ARE already the target manufacturer (e.g. 74437349100 is
    Würth WE-MAPI; LM5146 is TI; etc.).
    """
    for comp in comps:
        orig_pn = (comp.get("original_pn") or "").strip()
        if not orig_pn or orig_pn == "no_substitute":
            continue
        prev_status = comp.get("status")
        if prev_status in ("exact", "already_target"):
            continue
        # Authoritative check: is this MPN in TAS, and is its catalogued
        # manufacturer the target? Works for ANY manufacturer.
        part = _lookup_tas_part(
            orig_pn,
            comp.get("component_type", ""),
            tas_data_dir=tas_data_dir,
        )
        part_mfr = (part or {}).get("manufacturer")
        if not part or not isinstance(part_mfr, str):
            continue
        if not _manufacturer_matches(part_mfr, target_manufacturer):
            continue
        comp["status"] = "exact"
        comp["substitute_pn"] = orig_pn
        comp["value_check"] = "pass"
        comp["footprint_check"] = "pass"
        comp["derating_check"] = "pass"
        comp["notes"] = f"Already {target_manufacturer} part ({orig_pn}); verified in catalogue."
        ref = comp.get("ref_des", "?")
        fires.append(
            _make_fire(
                "0",
                ref,
                prev_status,
                "exact",
                f"original_pn {orig_pn} catalogued as {part_mfr} (matches target) "
                f"+ present in catalogue",
            )
        )


def _g1_capacitor_value_mismatch(
    comps: list[dict],
    bom_by_ref: dict[str, dict],
    fires: list[dict],
    *,
    tas_data_dir: Path | None = None,
) -> None:
    """G1: Per-component capacitance check.

    For every capacitor substitute, look up its TAS-stored capacitance
    and compare to the original's parsed capacitance. >2x mismatch
    means the LLM picked a wrong-value part; downgrade to no_substitute.
    """
    for comp in comps:
        ref = str(comp.get("ref_des", "") or "").split(",")[0].strip()
        bom_entry = bom_by_ref.get(ref, {})
        kind_cap = _row_kind(bom_entry, comp) == "capacitor"
        if not kind_cap:
            continue
        pn = (comp.get("substitute_pn") or "").strip()
        if not pn or pn == "no_substitute":
            continue
        if comp.get("status") not in ("recommended", "partial"):
            continue

        sub_spec = _lookup_tas_part(pn, "capacitor", tas_data_dir=tas_data_dir)
        if sub_spec is None or sub_spec.get("capacitance") is None:
            continue
        sub_cap = sub_spec["capacitance"]
        if sub_cap <= 0:
            continue

        orig_val_str = bom_entry.get("value") or comp.get("original_pn") or ""
        orig_cap = parse_si_value(orig_val_str)
        if orig_cap is None or not (1e-13 <= orig_cap <= 1e-1):
            continue

        ratio = sub_cap / orig_cap
        if ratio < 0.5 or ratio > 2.0:
            prev_status = comp.get("status", "?")
            comp["status"] = "no_substitute"
            comp["substitute_pn"] = "no_substitute"
            comp["notes"] = (
                f"GUARDRAIL G1: {ref} substitute {pn} has C={sub_cap:.2e}F, "
                f"original needs {orig_cap:.2e}F (ratio {ratio:.2f}x). "
                f"Downgraded {prev_status} -> no_substitute.\n" + (comp.get("notes") or "")
            )
            fires.append(
                _make_fire(
                    "1",
                    ref,
                    prev_status,
                    "no_substitute",
                    f"capacitance ratio {ratio:.2f}x out of [0.5, 2.0]",
                )
            )


def _g2_resistor_value_drift(
    comps: list[dict],
    bom_by_ref: dict[str, dict],
    fires: list[dict],
    *,
    tas_data_dir: Path | None = None,
) -> None:
    """G2: Resistor value tolerance check.

    Catches two tiers:
      - >50% deviation or >2x ratio: wrong-value part (LLM matched on
        package only). Escalate to no_substitute.
      - >5% deviation: marginal drift that risks feedback-divider
        accuracy. Downgrade to partial.
    """
    for comp in comps:
        ref = str(comp.get("ref_des", "") or "").split(",")[0].strip()
        bom_entry = bom_by_ref.get(ref, {})
        kind = _row_kind(bom_entry, comp)
        if kind != "resistor":
            continue
        if comp.get("status") not in ("recommended", "partial"):
            continue
        pn = (comp.get("substitute_pn") or "").strip()
        if not pn or pn == "no_substitute":
            continue

        orig_val = parse_si_value(bom_entry.get("value"))
        sub_spec = _lookup_tas_part(pn, "resistor", tas_data_dir=tas_data_dir)
        sub_val = (sub_spec or {}).get("resistance_Ohm")

        if orig_val is None or not (1e-3 <= orig_val <= 1e9):
            continue
        if sub_val is None or sub_val <= 0:
            continue

        dev = abs(sub_val - orig_val) / orig_val

        if dev > 0.50 or sub_val / orig_val > 2.0 or orig_val / sub_val > 2.0:
            prev_status = comp.get("status", "?")
            comp["status"] = "no_substitute"
            comp["substitute_pn"] = "no_substitute"
            comp["notes"] = (
                f"GUARDRAIL G2: {ref} resistor substitute {pn} = {sub_val} Ohm "
                f"is a wrong-value part for original {orig_val} Ohm "
                f"(deviation {dev * 100:.0f}%). Marked no_substitute.\n" + (comp.get("notes") or "")
            )
            fires.append(
                _make_fire(
                    "2a",
                    ref,
                    prev_status,
                    "no_substitute",
                    f"resistor wrong-value: {sub_val} Ohm vs {orig_val} Ohm (deviation {dev * 100:.0f}%)",
                )
            )
        elif dev > 0.05:
            prev_status = comp.get("status")
            comp["status"] = "partial"
            comp["notes"] = (
                f"GUARDRAIL G2: {ref} resistor substitute {pn} = {sub_val} Ohm "
                f"differs from original {orig_val} Ohm by {dev * 100:.1f}%. "
                f"Downgraded to partial (>5% shift risks feedback dividers).\n"
                + (comp.get("notes") or "")
            )
            fires.append(
                _make_fire(
                    "2b",
                    ref,
                    prev_status,
                    "partial",
                    f"resistor tolerance: {sub_val} Ohm vs {orig_val} Ohm (delta {dev * 100:.1f}%)",
                )
            )


def _g3_capacitor_voltage_downrate(
    comps: list[dict],
    bom_by_ref: dict[str, dict],
    fires: list[dict],
    *,
    tas_data_dir: Path | None = None,
) -> None:
    """G3: Capacitor voltage downrate check.

    If the substitute's rated voltage is below the original's rated
    voltage AND the entry is tagged 'recommended', downgrade to
    'partial'.
    """
    for comp in comps:
        ref = str(comp.get("ref_des", "") or "").split(",")[0].strip()
        bom_entry = bom_by_ref.get(ref, {})
        kind_cap = _row_kind(bom_entry, comp) == "capacitor"
        if not kind_cap:
            continue
        if comp.get("status") != "recommended":
            continue
        pn = (comp.get("substitute_pn") or "").strip()
        if not pn or pn == "no_substitute":
            continue

        sub_spec = _lookup_tas_part(pn, "capacitor", tas_data_dir=tas_data_dir)
        sub_v = (sub_spec or {}).get("voltage")
        orig_v = parse_si_value(bom_entry.get("voltage"))

        if sub_v is None or orig_v is None or orig_v <= 0:
            continue
        if sub_v < orig_v * 0.99:  # 1% slack for rounding
            prev_status = comp.get("status")
            comp["status"] = "partial"
            comp["notes"] = (
                f"GUARDRAIL G3: {ref} cap substitute {pn} rated {sub_v}V, "
                f"original needs {orig_v}V. Downgraded recommended -> partial.\n"
                + (comp.get("notes") or "")
            )
            fires.append(
                _make_fire(
                    "3",
                    ref,
                    prev_status,
                    "partial",
                    f"cap voltage downrate: {sub_v}V vs {orig_v}V required",
                )
            )


def _g4_inductor_footprint_overrejection(
    comps: list[dict],
    bom_by_ref: dict[str, dict],
    fires: list[dict],
) -> None:
    """G4: Inductor over-rejection on footprint-only grounds.

    Per standing rule: inductors don't need exact footprint match;
    reject only on Isat / DCR / value / extreme size. If a substitute
    was marked 'not_recommended' solely because of footprint mismatch,
    soften to 'partial'.
    """
    _FOOTPRINT_ONLY = (
        "package mismatch",
        "footprint mismatch",
        "not a drop-in",
        "requires pcb redesign",
        "package change",
        "footprint change",
    )
    _HARD_REJECTION = (
        r"isat",
        r"saturation",
        r"dcr.{0,30}exceed",
        r"value mismatch",
        r"extreme size",
        r"10x",
        r"20x",
    )

    for comp in comps:
        ref = str(comp.get("ref_des", "") or "").split(",")[0].strip()
        bom_entry = bom_by_ref.get(ref, {})
        kind = _row_kind(bom_entry)
        comp_type = _row_kind(comp)
        _magnetic_kinds = ("inductor", "transformer", "common_mode_choke", "magnetic")
        if kind not in _magnetic_kinds and comp_type not in _magnetic_kinds:
            continue
        if comp.get("status") != "not_recommended":
            continue

        notes = (comp.get("notes") or "").lower()
        is_footprint_issue = any(tok in notes for tok in _FOOTPRINT_ONLY)
        is_hard_reject = any(re.search(tok, notes) for tok in _HARD_REJECTION)

        if is_footprint_issue and not is_hard_reject:
            comp["status"] = "partial"
            comp["notes"] = (
                f"GUARDRAIL G4: {ref} inductor substitute "
                f"{comp.get('substitute_pn')} was marked not_recommended on "
                f"footprint grounds only. Downgraded to partial.\n" + (comp.get("notes") or "")
            )
            fires.append(
                _make_fire(
                    "4",
                    ref,
                    "not_recommended",
                    "partial",
                    "inductor over-rejected on footprint-only grounds",
                )
            )


# Row component_type → catalogue file-kind, for the G5c category check.
# Types with no single catalogue file (semiconductor, controller, varistor)
# are not checkable and are skipped.
# A row's component_type, in the SAME vocabulary lookup_mpn_category returns.
# These two must agree or G5c compares apples to oranges: chipBead used to map
# to "magnetic" here, because beads live in the magnetics FILE, and that was
# right until lookup_mpn_category learned to distinguish a bead from an inductor
# by subtype (ABT #874). After that a correct bead-for-bead substitution read as
# row=magnetic vs substitute=chipBead and G5c demoted it — every round, on every
# correction loop, reported as a hallucinated MPN that stage 4b then "recovered"
# by picking the very same part again.
_ROW_TYPE_TO_CATEGORY = {
    "capacitor": "capacitor",
    "resistor": "resistor",
    "magnetic": "magnetic",
    "inductor": "magnetic",
    "chipBead": "chipBead",
    "mosfet": "mosfet",
    "diode": "diode",
    "connector": "connector",
    "analog": "analog",
    "timeBase": "timeBase",
}


def _g5_substitute_existence(
    comps: list[dict],
    fires: list[dict],
    *,
    target_manufacturer: str = "",
    tas_data_dir: Path | None = None,
) -> None:
    """G5: Substitute MPN must exist in the TAS catalogue — as the right kind
    of part, from the target manufacturer.

    Catches LLM hallucinations where a plausible-looking MPN does not
    actually exist in TAS (5/5b), is a real part of a DIFFERENT category
    (5c — e.g. a connector MPN offered as a capacitor substitute), or is a
    real part of the WRONG manufacturer (5d — a substitution must come from
    the target's catalogue). Also catches product-family descriptions
    masquerading as MPNs (e.g. 'WCAP-MLCC-4700nF-630V').
    """
    for comp in comps:
        if comp.get("status") not in ("recommended", "partial", "exact"):
            continue
        pn = (comp.get("substitute_pn") or "").strip()
        if not pn or pn == "no_substitute":
            continue
        ref = str(comp.get("ref_des", "") or "").split(",")[0].strip()

        # 5a: Format check — reject obvious non-MPN strings (product-family
        # descriptions like 'WCAP-MLCC-4700nF-160V'). Manufacturer-agnostic
        # carve-out: if the description-looking string is actually a real
        # catalogued MPN (present in TAS), keep it — don't reject on format.
        looks_like_description = "-" in pn and any(unit in pn for unit in ("nF", "uF", "V", "Ohm"))
        if looks_like_description and not _mpn_exists_in_tas(pn, tas_data_dir=tas_data_dir):
            prev_status = comp.get("status")
            comp["status"] = "no_substitute"
            comp["substitute_pn"] = "no_substitute"
            comp["notes"] = (
                f"GUARDRAIL G5b: {ref} substitute '{pn}' is a product family "
                f"description, not a catalogue MPN. Demoted to no_substitute.\n"
                + (comp.get("notes") or "")
            )
            fires.append(
                _make_fire(
                    "5b",
                    ref,
                    prev_status,
                    "no_substitute",
                    f"substitute '{pn}' is a product-family description, not an MPN",
                )
            )
            continue

        # 5b: Existence check.
        if not _mpn_exists_in_tas(pn, tas_data_dir=tas_data_dir):
            prev_status = comp.get("status")
            comp["status"] = "no_substitute"
            comp["substitute_pn"] = "no_substitute"
            comp["notes"] = (
                f"GUARDRAIL G5: {ref} substitute '{pn}' does not exist in the "
                f"catalogue (TAS lookup returned no record). "
                f"Demoted to no_substitute — likely LLM hallucination.\n"
                + (comp.get("notes") or "")
            )
            fires.append(
                _make_fire(
                    "5",
                    ref,
                    prev_status,
                    "no_substitute",
                    f"substitute '{pn}' not present in catalogue (LLM hallucination)",
                )
            )
            continue

        # 5c: Category check — the substitute must be the same KIND of part.
        # A real-but-wrong-category MPN (a connector offered for a capacitor
        # row) passes the existence check yet is never a valid substitute.
        row_kind = _ROW_TYPE_TO_CATEGORY.get(str(comp.get("component_type") or ""))
        if row_kind:
            sub_kind = lookup_mpn_category(pn, tas_data_dir=tas_data_dir)
            if sub_kind is not None and sub_kind != row_kind:
                prev_status = comp.get("status")
                comp["status"] = "no_substitute"
                comp["substitute_pn"] = "no_substitute"
                comp["notes"] = (
                    f"GUARDRAIL G5c: {ref} substitute '{pn}' is catalogued as a "
                    f"{sub_kind}, but this row is a {comp.get('component_type')}. "
                    f"Demoted to no_substitute.\n" + (comp.get("notes") or "")
                )
                fires.append(
                    _make_fire(
                        "5c",
                        ref,
                        prev_status,
                        "no_substitute",
                        f"substitute '{pn}' is a {sub_kind}, row is a "
                        f"{comp.get('component_type')} (wrong category)",
                    )
                )
                continue

        # 5d: Manufacturer check — a substitution must come from the target
        # manufacturer's catalogue. A real part of another manufacturer offered
        # as the substitute is a hallucinated cross-reference.
        if target_manufacturer:
            part = _lookup_tas_part(
                pn, str(comp.get("component_type") or ""), tas_data_dir=tas_data_dir
            )
            part_mfr = (part or {}).get("manufacturer")
            if isinstance(part_mfr, str) and not _manufacturer_matches(
                part_mfr, target_manufacturer
            ):
                prev_status = comp.get("status")
                comp["status"] = "no_substitute"
                comp["substitute_pn"] = "no_substitute"
                comp["notes"] = (
                    f"GUARDRAIL G5d: {ref} substitute '{pn}' is catalogued as "
                    f"{part_mfr}, not {target_manufacturer}. "
                    f"Demoted to no_substitute.\n" + (comp.get("notes") or "")
                )
                fires.append(
                    _make_fire(
                        "5d",
                        ref,
                        prev_status,
                        "no_substitute",
                        f"substitute '{pn}' is a {part_mfr} part, target is {target_manufacturer}",
                    )
                )


def _g6_voltage_inadequacy_in_notes(
    comps: list[dict],
    fires: list[dict],
) -> None:
    """G6: LLM admits voltage inadequacy in its own notes.

    Catches the case where the LLM emits status=recommended but its own
    notes describe a voltage mismatch (e.g. 'No Wurth 4.7uF/1206
    capacitor meets original 100V rating').
    """
    _VOLTAGE_INADEQUATE = [
        # Manufacturer-agnostic: "no <any manufacturer> ... meets original
        # ... rating" (the LLM names whatever target it was given).
        re.compile(r"\bno\b.{0,40}meets? original.{0,40}rating", re.I | re.S),
        re.compile(r"highest available.{0,30}\d+\.?\d*\s*v.{0,40}orig", re.I | re.S),
        re.compile(r"voltage rating fail", re.I),
        re.compile(r"voltage.{0,30}inadequate", re.I),
        re.compile(r"derating fail", re.I),
        re.compile(r"requirement.{0,20}not met", re.I),
    ]

    for comp in comps:
        if comp.get("status") != "recommended":
            continue
        notes = comp.get("notes") or ""
        if not any(p.search(notes) for p in _VOLTAGE_INADEQUATE):
            continue
        ref = str(comp.get("ref_des", "") or "").split(",")[0].strip()
        comp["status"] = "partial"
        comp["notes"] = (
            f"GUARDRAIL G6: {ref} marked recommended but notes describe a "
            f"voltage inadequacy. Demoted to partial.\n" + (comp.get("notes") or "")
        )
        fires.append(
            _make_fire(
                "6",
                ref,
                "recommended",
                "partial",
                "notes describe voltage inadequacy",
            )
        )


_AUTOMOTIVE_PREFIXES = ("NCV", "NCD", "NCH", "TJA", "TLE", "AUIRG", "AUIRF")
_AUTOMOTIVE_SUFFIXES = ("-AEC", "-Q100", "-Q101", "-Q200")


def _looks_automotive(mpn: str) -> bool:
    if not mpn:
        return False
    u = mpn.upper()
    if any(u.startswith(p) for p in _AUTOMOTIVE_PREFIXES):
        return True
    return bool(any(s in u for s in _AUTOMOTIVE_SUFFIXES))


def _gaecq_automotive_grade(
    comps: list[dict],
    source_bom: list[dict],
    fires: list[dict],
    *,
    tas_data_dir: Path | None = None,
) -> None:
    """GAECQ: Automotive grade propagation.

    When the source BOM contains automotive-qualified parts (NCV*, TJA*,
    AEC-Q suffixed), all substitute parts must also carry AEC-Q
    qualification. Parts without Q-grade are demoted to partial.
    """
    auto_context = any(_looks_automotive(s.get("original_pn") or "") for s in (source_bom or []))
    if not auto_context:
        return

    for comp in comps:
        if comp.get("status") not in ("recommended", "partial"):
            continue
        pn = (comp.get("substitute_pn") or "").strip()
        if not pn or pn == "no_substitute":
            continue
        ref = str(comp.get("ref_des", "") or "").split(",")[0].strip()

        # Check if the substitute's TAS record mentions AEC-Q.
        spec = _lookup_tas_part(pn, "", tas_data_dir=tas_data_dir)
        if spec is None:
            continue
        env = spec.get("raw_envelope") or {}
        # Walk the envelope looking for qualification fields.
        quals_text = ""
        for top_key in ("capacitor", "semiconductor", "resistor", "magnetics"):
            sub = env.get(top_key)
            if not isinstance(sub, dict):
                continue
            for inner_key in (None, "mosfet", "diode", "igbt"):
                record = sub if inner_key is None else sub.get(inner_key)
                if not isinstance(record, dict):
                    continue
                mi = record.get("manufacturerInfo") or {}
                di = mi.get("datasheetInfo") or {}
                for path in (di, mi, di.get("part") or {}, di.get("compliance") or {}):
                    q = path.get("qualifications") or path.get("aecq") or path.get("aec_q")
                    if isinstance(q, list):
                        quals_text += " " + " ".join(str(x).upper() for x in q)
                    elif isinstance(q, str):
                        quals_text += " " + q.upper()

        is_q = any(tok in quals_text for tok in ("AEC-Q", "AECQ", "Q200", "Q100", "Q101"))
        if not is_q:
            prev = comp.get("status")
            comp["status"] = "partial" if prev == "recommended" else prev
            comp["notes"] = (
                f"GUARDRAIL GAECQ: automotive design but substitute {pn} "
                f"carries no Q-grade in TAS. Verify qualification. " + (comp.get("notes") or "")
            )
            fires.append(
                _make_fire(
                    "AECQ",
                    ref,
                    prev,
                    "partial",
                    f"automotive design; substitute {pn} not Q-graded in TAS",
                )
            )


_SMD_SIZES = [
    "0201",
    "0402",
    "0603",
    "0805",
    "1206",
    "1210",
    "1812",
    "2010",
    "2220",
    "2512",
    "2920",
]
_LEADED_PKG_TOKENS = (
    "DIP",
    "PDIP",
    "TO-220",
    "TO-247",
    "TO-218",
    "TO-3P",
    "TO-126",
    "TO-92",
    "SIP",
    "RADIAL",
    "AXIAL",
    "THRU-HOLE",
    "THROUGH",
    "THT",
    "SNAP-IN",
    "SCREW-TERMINAL",
)
_SMD_PKG_TOKENS = (
    "0201",
    "0402",
    "0603",
    "0805",
    "1206",
    "1210",
    "1812",
    "2010",
    "2512",
    "2920",
    "DFN",
    "SON",
    "TDFN",
    "CSP",
    "SOT",
    "SOIC",
    "SOP",
    "DPAK",
    "D2PAK",
    "QFN",
    "BGA",
    "LGA",
    "SMA",
    "SMB",
    "SMC",
)


def _is_smd(pkg: str) -> bool:
    p = pkg.upper()
    return any(tok in p for tok in _SMD_PKG_TOKENS)


def _is_leaded(pkg: str) -> bool:
    p = pkg.upper()
    return any(tok in p for tok in _LEADED_PKG_TOKENS)


def _smd_class_idx(pkg: str) -> int | None:
    if not pkg:
        return None
    p = pkg.upper()
    for i, s in enumerate(_SMD_SIZES):
        if s in p:
            return i
    return None


def _gfoot_footprint_compatibility(
    comps: list[dict],
    bom_by_ref: dict[str, dict],
    fires: list[dict],
    *,
    tas_data_dir: Path | None = None,
) -> None:
    """GFoot: Footprint class compatibility check.

    - SMD <-> leaded mounting type: hard reject.
    - Class jump >= 4 sizes: hard reject (redesign required).
    - Class jump >= 3 sizes: demote to partial.

    Skipped for inductors/transformers/MOSFETs/diodes — their footprints
    vary by series and generic rules produce too many false positives.
    """
    for comp in comps:
        if comp.get("status") not in ("recommended", "partial"):
            continue
        pn = (comp.get("substitute_pn") or "").strip()
        if not pn or pn == "no_substitute":
            continue
        ref = str(comp.get("ref_des", "") or "").split(",")[0].strip()
        src = bom_by_ref.get(ref) or {}

        # Skip inductor/semiconductor types.
        ctype = _row_kind(comp, src)
        if ctype in (
            "inductor",
            "transformer",
            "common_mode_choke",
            "ferrite_bead",
            "magnetic",
            "mosfet",
            "diode",
            "igbt",
        ):
            continue

        src_pkg = str(src.get("package") or "").strip()
        if not src_pkg:
            continue

        # Get substitute package from TAS.
        sub_spec = _lookup_tas_part(pn, "", tas_data_dir=tas_data_dir)
        sub_pkg = str((sub_spec or {}).get("package") or "").strip()
        if not sub_pkg:
            continue

        # SMD <-> leaded check.
        if (_is_smd(src_pkg) and _is_leaded(sub_pkg)) or (_is_leaded(src_pkg) and _is_smd(sub_pkg)):
            prev = comp.get("status")
            comp["status"] = "no_substitute"
            comp["substitute_pn"] = "no_substitute"
            comp["notes"] = (
                f"GUARDRAIL GFoot: {src_pkg} mount-type incompatible "
                f"with substitute {sub_pkg}. " + (comp.get("notes") or "")
            )
            fires.append(
                _make_fire(
                    "Foot",
                    ref,
                    prev,
                    "no_substitute",
                    f"mount-type incompatible: {src_pkg} -> {sub_pkg}",
                )
            )
            continue

        # SMD class jump check. A larger-package substitute is a REAL part that
        # exists — it is a partial substitution (works electrically, needs a
        # footprint/board-space check), NOT a no_substitute. Reserving
        # no_substitute for "no electrically-valid part exists" keeps the label
        # honest and avoids discarding a usable Würth equivalent just because it
        # is a size or more bigger (the engineer decides if the board has room).
        si, ti = _smd_class_idx(src_pkg), _smd_class_idx(sub_pkg)
        if si is not None and ti is not None:
            jump = abs(ti - si)
            if jump >= 4:
                prev = comp.get("status")
                comp["status"] = "partial" if prev == "recommended" else prev
                comp["notes"] = (
                    f"GUARDRAIL GFoot: {src_pkg} -> {sub_pkg} "
                    f"({jump} size classes — board redesign required for footprint). "
                    + (comp.get("notes") or "")
                )
                fires.append(
                    _make_fire(
                        "Foot",
                        ref,
                        prev,
                        comp["status"],
                        f"footprint redesign (>=4 classes): {src_pkg} -> {sub_pkg}",
                    )
                )
                continue
            if jump >= 3:
                prev = comp.get("status")
                comp["status"] = "partial" if prev == "recommended" else prev
                comp["notes"] = (
                    f"GUARDRAIL GFoot: large footprint jump {src_pkg} -> {sub_pkg} "
                    f"({jump} size classes). " + (comp.get("notes") or "")
                )
                fires.append(
                    _make_fire(
                        "Foot",
                        ref,
                        prev,
                        "partial",
                        f"footprint jump >=3 classes: {src_pkg} -> {sub_pkg}",
                    )
                )


def _gstack_multiple_caveats(
    comps: list[dict],
    fires: list[dict],
) -> None:
    """GStack: Multiple concurrent caveats on a single row.

    When a row has accumulated >= 2 independent guardrail warnings it is no
    longer a clean drop-in — but a real part still EXISTS, so it is a *partial*
    substitution flagged "MULTIPLE COMPROMISES" for the engineer to weigh, NOT a
    no_substitute. no_substitute must mean "no electrically-valid part exists";
    relabeling a found-but-caveated part as no_substitute hides a usable option
    and is the wrong signal (it also wrongly tanked coverage vs Proteus).
    """
    for comp in comps:
        if comp.get("status") not in ("recommended", "partial"):
            continue
        notes = comp.get("notes") or ""
        # Count guardrail prefix occurrences.
        guardrail_hits = len(re.findall(r"GUARDRAIL G", notes))
        already_stacked = "MULTIPLE COMPROMISES" in notes.upper()
        if already_stacked:
            continue
        if guardrail_hits >= 2:
            ref = str(comp.get("ref_des", "") or "").split(",")[0].strip()
            prev = comp.get("status")
            comp["status"] = "partial"
            comp["notes"] = (
                f"GUARDRAIL GStack: MULTIPLE COMPROMISES — {guardrail_hits} "
                f"concurrent caveats; verify carefully before use. " + (comp.get("notes") or "")
            )
            fires.append(
                _make_fire(
                    "Stack",
                    ref,
                    prev,
                    "partial",
                    f"{guardrail_hits} concurrent caveats stacked",
                )
            )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

VOLTAGE_DERATING = 1.20
CURRENT_DERATING = 1.25
DIODE_VOLTAGE_DERATING = 1.50
SATURATION_MARGIN = 0.90


def _g7_voltage_stress(
    comps: list[dict],
    stress_by_ref: dict,
    fires: list[dict],
    *,
    tas_data_dir: Path | None = None,
) -> None:
    """G7: substitute voltage rating insufficient for simulated peak stress."""
    for comp in comps:
        ref = comp.get("ref_des", "")
        stress = stress_by_ref.get(ref)
        if not stress or not stress.v_peak:
            continue
        sub_pn = comp.get("substitute_pn")
        if not sub_pn or comp.get("status") in ("keep_original", "no_substitute"):
            continue
        cat = comp.get("component_type", "")
        rated_v = _lookup_substitute_voltage(comp, cat, tas_data_dir)
        if rated_v is None:
            continue
        derating = DIODE_VOLTAGE_DERATING if cat == "diode" else VOLTAGE_DERATING
        if rated_v < stress.v_peak * derating:
            before = comp.get("status", "recommended")
            if rated_v < stress.v_peak:
                comp["status"] = "no_substitute"
            else:
                comp["status"] = "partial"
            fires.append(
                {
                    "guardrail_id": "G7_VoltageStress",
                    "ref_des": ref,
                    "before": before,
                    "after": comp["status"],
                    "reason": (
                        f"sim V_peak={stress.v_peak:.1f}V, rated={rated_v:.0f}V, "
                        f"derating {derating}× requires ≥{stress.v_peak * derating:.1f}V"
                    ),
                }
            )


def _g8_current_stress(
    comps: list[dict],
    stress_by_ref: dict,
    fires: list[dict],
    *,
    tas_data_dir: Path | None = None,
) -> None:
    """G8: substitute current rating insufficient for simulated peak stress."""
    for comp in comps:
        ref = comp.get("ref_des", "")
        stress = stress_by_ref.get(ref)
        if not stress or not stress.i_peak:
            continue
        sub_pn = comp.get("substitute_pn")
        if not sub_pn or comp.get("status") in ("keep_original", "no_substitute"):
            continue
        cat = comp.get("component_type", "")
        rated_i = _lookup_substitute_current(comp, cat, tas_data_dir)
        if rated_i is None:
            continue
        if rated_i < stress.i_peak:
            before = comp.get("status", "recommended")
            comp["status"] = "partial"
            fires.append(
                {
                    "guardrail_id": "G8_CurrentStress",
                    "ref_des": ref,
                    "before": before,
                    "after": "partial",
                    "reason": (f"sim I_peak={stress.i_peak:.2f}A, rated={rated_i:.1f}A"),
                }
            )


def _g9_saturation_margin(
    comps: list[dict],
    stress_by_ref: dict,
    fires: list[dict],
    *,
    tas_data_dir: Path | None = None,
) -> None:
    """G9: inductor operating too close to saturation current."""
    for comp in comps:
        ref = comp.get("ref_des", "")
        stress = stress_by_ref.get(ref)
        if not stress or not stress.i_peak:
            continue
        cat = comp.get("component_type", "")
        if cat not in ("inductor", "magnetic"):
            continue
        sub_pn = comp.get("substitute_pn")
        if not sub_pn or comp.get("status") in ("keep_original", "no_substitute"):
            continue
        isat = _lookup_substitute_isat(comp, cat, tas_data_dir)
        if isat is None:
            continue
        if stress.i_peak > isat * SATURATION_MARGIN:
            before = comp.get("status", "recommended")
            comp["status"] = "partial"
            fires.append(
                {
                    "guardrail_id": "G9_SaturationMargin",
                    "ref_des": ref,
                    "before": before,
                    "after": "partial",
                    "reason": (
                        f"sim I_peak={stress.i_peak:.2f}A > "
                        f"{SATURATION_MARGIN}×Isat={isat * SATURATION_MARGIN:.2f}A"
                    ),
                }
            )


def _lookup_substitute_voltage(comp: dict, cat: str, tas_data_dir: Path | None) -> float | None:
    """Look up the substitute's voltage rating from the crossref result."""
    v_str = comp.get("substitute_voltage", "")
    if v_str:
        try:
            return float(str(v_str).replace("V", "").strip())
        except (ValueError, TypeError):
            pass
    return None


def _envelope_electrical(env: dict) -> Any:
    """Return the ``datasheetInfo.electrical`` block of a TAS envelope (a dict
    for caps/semis/resistors, a LIST of subtype items for magnetics v2), or
    ``None``. Navigates the top/inner keys the same way the MPN index does."""
    for top_key in ("capacitor", "semiconductor", "resistor", "magnetics", "magnetic"):
        sub = env.get(top_key)
        if not isinstance(sub, dict):
            continue
        for inner_key in (None, "mosfet", "diode", "igbt"):
            rec = sub if inner_key is None else sub.get(inner_key)
            if not isinstance(rec, dict):
                continue
            di = (rec.get("manufacturerInfo") or {}).get("datasheetInfo") or {}
            elec = di.get("electrical")
            if elec is not None:
                return elec
    return None


def _read_electrical_field(elec: Any, field: str) -> float | None:
    """Read a scalar ``field`` from an electrical block (dict or magnetics list),
    resolving a ``dimensionWithTolerance`` to a scalar (nominal → max → min)."""
    val: Any = None
    if isinstance(elec, list):
        for item in elec:
            if isinstance(item, dict) and field in item:
                val = item[field]
                break
    elif isinstance(elec, dict):
        val = elec.get(field)
    if isinstance(val, dict):  # dimensionWithTolerance
        for k in ("nominal", "maximum", "minimum"):
            if isinstance(val.get(k), (int, float)):
                return float(val[k])
        return None
    return float(val) if isinstance(val, (int, float)) else None


def _substitute_electrical_scalar(
    comp: dict, cat: str, field: str, tas_data_dir: Path | None
) -> float | None:
    """Resolve the substitute MPN in TAS and read one electrical scalar from it."""
    pn = comp.get("substitute_pn")
    if not pn or pn == "no_substitute":
        return None
    rec = _lookup_tas_part(pn, cat, tas_data_dir=tas_data_dir)
    if not rec:
        return None
    env = rec.get("raw_envelope")
    if not isinstance(env, dict):
        return None
    return _read_electrical_field(_envelope_electrical(env), field)


# Per-category continuous current-rating field. Absent categories (e.g. caps in
# G8) legitimately have no comparable rating → that row is skipped, but the gate
# still fires for the categories that DO carry a rating (not a blanket disable).
_CURRENT_RATING_FIELD = {
    "mosfet": "continuousDrainCurrent",
    "diode": "forwardCurrent",
}


def _lookup_substitute_current(comp: dict, cat: str, tas_data_dir: Path | None) -> float | None:
    """Continuous current rating of the substitute, resolved from its TAS
    envelope (mosfet continuousDrainCurrent / diode forwardCurrent)."""
    field = _CURRENT_RATING_FIELD.get(cat)
    if field is None:
        return None
    return _substitute_electrical_scalar(comp, cat, field, tas_data_dir)


def _lookup_substitute_isat(comp: dict, cat: str, tas_data_dir: Path | None) -> float | None:
    """Saturation-current peak of the substitute magnetic, from its TAS
    envelope (magnetics electrical.saturationCurrentPeak)."""
    return _substitute_electrical_scalar(comp, cat, "saturationCurrentPeak", tas_data_dir)


def apply_guardrails(
    crossref_json: dict,
    source_bom: list[dict],
    target_manufacturer: str,
    *,
    stress_by_ref: dict | None = None,
    tas_data_dir: Path | None = None,
) -> tuple[dict, list[dict]]:
    """Apply all deterministic guardrails to a structured crossref result.

    Parameters
    ----------
    crossref_json : dict
        Structured crossref output with a ``"components"`` list.
    source_bom : list[dict]
        The original BOM used as input to the crossref pipeline.
    target_manufacturer : str
        Name of the target manufacturer (e.g. ``"Wurth Elektronik"``).
    tas_data_dir : Path | None
        Override for the TAS data directory (for testing).

    Returns
    -------
    tuple[dict, list[dict]]
        ``(corrected_crossref_json, fire_log_entries)`` where
        *fire_log_entries* is a list of structured dicts recording each
        guardrail that fired and what it changed.
    """
    comps = crossref_json.get("crossref") or crossref_json.get("components") or []
    if not isinstance(comps, list) or not comps:
        return crossref_json, []

    fires: list[dict] = []
    bom_by_ref = _build_bom_by_ref(source_bom)

    # Run guardrails in order. G0 runs first so subsequent guardrails
    # see corrected statuses.
    _g0_already_target_manufacturer(comps, target_manufacturer, fires, tas_data_dir=tas_data_dir)

    _g1_capacitor_value_mismatch(comps, bom_by_ref, fires, tas_data_dir=tas_data_dir)

    _g2_resistor_value_drift(comps, bom_by_ref, fires, tas_data_dir=tas_data_dir)

    _g3_capacitor_voltage_downrate(comps, bom_by_ref, fires, tas_data_dir=tas_data_dir)

    _g4_inductor_footprint_overrejection(comps, bom_by_ref, fires)

    _g5_substitute_existence(
        comps, fires, target_manufacturer=target_manufacturer, tas_data_dir=tas_data_dir
    )

    _g6_voltage_inadequacy_in_notes(comps, fires)

    _gaecq_automotive_grade(comps, source_bom, fires, tas_data_dir=tas_data_dir)

    _gfoot_footprint_compatibility(comps, bom_by_ref, fires, tas_data_dir=tas_data_dir)

    # Stress-based guardrails (from RE simulation)
    if stress_by_ref:
        _g7_voltage_stress(comps, stress_by_ref, fires, tas_data_dir=tas_data_dir)
        _g8_current_stress(comps, stress_by_ref, fires, tas_data_dir=tas_data_dir)
        _g9_saturation_margin(comps, stress_by_ref, fires, tas_data_dir=tas_data_dir)

    # GStack runs last — it counts caveats from all prior guardrails.
    _gstack_multiple_caveats(comps, fires)

    return crossref_json, fires


__all__ = [
    "apply_guardrails",
]
