"""CR (Cross-Reference) pipeline orchestrator.

Takes a source BOM and a target manufacturer, then:
  1.   Prefetches TAS candidates per component, ranked by relevance
  1.5  Librarian: searches Digi-Key for gaps not covered by TAS
  2.   Pre-classifies keep_original (already target mfr) and not-fitted
  3.   LLM cross-referencer with constrained candidates
  4.   Engineering guardrails (deterministic)
  5.   Match scoring + sourcing annotation
  6.   Otto challenge: diagnoses feed back as hints to re-crossref
  7.   Review (Nicola quality mode) — rejects trigger correction loop
  7b.  Correction loop: fix objected components, re-run 4→5→6→7
  8.   Teacher: learn lessons from objections and Otto diagnoses

Launch:
  heaviside crossref bom.json --mfr "Wurth"
  POST /crossref {"source_bom": [...], "target_manufacturer": "Wurth"}
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import logging
import os
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from heaviside.pipeline.re_state import REState

from heaviside.agents.llm_call import (
    LLMCallError,
    call_agent,
    call_agent_json,
    extract_json_block,
    normalize_reviewer_verdict,
)
from heaviside.pipeline.crossref import (
    CrossRefOutcome,
    CrossRefState,
)

logger = logging.getLogger(__name__)


def _normalize_manufacturer(name: str) -> str:
    """Lowercase + strip accents for manufacturer name matching.

    Handles the Würth/Wurth, Murata/murata, etc. mismatch between
    user input and TAS records.
    """
    nfkd = unicodedata.normalize("NFKD", name.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


class CrossRefPipelineError(RuntimeError):
    """Raised on unrecoverable CR pipeline failures."""


# ---------------------------------------------------------------------------
# Stage 1: Prefetch TAS candidates (deterministic)
# ---------------------------------------------------------------------------


def _stage1_prefetch(state: CrossRefState) -> CrossRefState:
    """For each BOM row, query TAS for candidates from the target manufacturer.

    Scans each NDJSON file at most once, building a per-category
    manufacturer cache. This avoids the O(n_components × n_rows)
    trap when multiple BOM rows share a category.
    """
    from heaviside.catalogue._reader import CatalogueReadError, iter_envelopes

    tas_dir = Path(
        os.environ.get(
            "HEAVISIDE_TAS_DATA_DIR",
            str(Path(__file__).resolve().parents[2] / "TAS" / "data"),
        )
    )

    category_files = {
        "mosfet": "mosfets.ndjson",
        "diode": "diodes.ndjson",
        "capacitor": "capacitors.ndjson",
        "resistor": "resistors.ndjson",
        "magnetic": "magnetics.ndjson",
        "chipBead": "magnetics.ndjson",  # subtype-filtered at scan time
        "varistor": "varistors.ndjson",
        "connector": "connectors.ndjson",
        "analog": "analog_ics.ndjson",
        "timeBase": "timing_devices.ndjson",
    }

    target_mfr_lower = _normalize_manufacturer(state.target_manufacturer)

    # Which categories does the BOM actually need?
    needed_cats: set[str] = set()
    for comp in state.source_bom:
        cat = comp.get("component_type", comp.get("category", ""))
        if cat in category_files:
            needed_cats.add(cat)

    # Source MPNs whose physical dimensions we want to resolve (any
    # manufacturer), so the footprint-fit ranking knows the board space the
    # substitute must fit into. Keyed (cat, normalized-mpn).
    source_mpn_by_cat: dict[str, set[str]] = {}
    for comp in state.source_bom:
        cat = comp.get("component_type", comp.get("category", ""))
        mpn = str(comp.get("original_mpn", "")).strip().lower()
        if cat in category_files and mpn:
            source_mpn_by_cat.setdefault(cat, set()).add(mpn)
    source_env_by_key: dict[tuple[str, str], dict[str, Any]] = {}

    # Scan each needed NDJSON file ONCE, collect all target-mfr rows
    mfr_cache: dict[str, list[dict[str, Any]]] = {}
    for cat in needed_cats:
        fname = category_files[cat]
        path = tas_dir / fname
        if not path.exists():
            mfr_cache[cat] = []
            continue
        rows: list[dict[str, Any]] = []
        want_source = source_mpn_by_cat.get(cat, set())
        try:
            for _lineno, env in iter_envelopes(path):
                if cat == "chipBead" and not _is_chip_bead_env(env):
                    continue
                # Capture the source part's own envelope (for its dimensions),
                # regardless of manufacturer.
                if want_source:
                    ref = str(_envelope_reference(env, cat) or "").strip().lower()
                    if ref and ref in want_source:
                        source_env_by_key[(cat, ref)] = env
                mfr_name = _extract_manufacturer(env, cat)
                if mfr_name and target_mfr_lower in _normalize_manufacturer(mfr_name):
                    rows.append(env)
        except CatalogueReadError:
            pass
        mfr_cache[cat] = rows
        logger.info(
            "CR prefetch: %s has %d %s candidates", state.target_manufacturer, len(rows), cat
        )

    # Resolve each source row's physical footprint: prefer its catalogue
    # envelope's mechanical drawing, fall back to the BOM-declared case code.
    # When nothing is known, surface it (CLAUDE.md: no silent fallback) — the
    # fit ranking simply can't be enforced for that row.
    for comp in state.source_bom:
        cat = comp.get("component_type", comp.get("category", ""))
        mpn = str(comp.get("original_mpn", "")).strip().lower()
        dims = None
        src_env = source_env_by_key.get((cat, mpn)) if mpn else None
        if src_env is not None:
            dims = _extract_dimensions(src_env, cat)
            # Attribute-matched categories (connectors: family/positions/
            # pitch/gender; analog: function/channels/supply) rank against
            # the ORIGINAL's catalogue record, not a parsed value string —
            # keep the source envelope on the row for the ranker.
            comp["_source_env"] = src_env
        if dims is None:
            eia = _eia_dims_from_case(comp.get("package"))
            if eia:
                dims = (eia[0], eia[1], None)
        comp["_source_dims_m"] = dims

    # Surface missing source dimensions once, aggregated — one diagnostic per
    # row floods the report on large BOMs. Footprint-fit is simply not enforced
    # for rows whose physical size couldn't be resolved. Identity-matched
    # categories (connector/analog/crystal) are matched on function/pins/pitch,
    # NOT a board-space footprint, so a missing dimension isn't a gap for them —
    # exclude them from the diagnostic (they were flooding it with crystals).
    no_dims = [
        c.get("original_mpn") or c.get("ref_des", "?")
        for c in state.source_bom
        if c.get("component_type")
        and c.get("component_type") not in _IDENTITY_MATCHED_CATEGORIES
        and c.get("_source_dims_m") is None
    ]
    if no_dims:
        shown = ", ".join(str(m) for m in no_dims[:8])
        more = f" (+{len(no_dims) - 8} more)" if len(no_dims) > 8 else ""
        state.diagnostics.append(
            f"footprint-fit not enforced for {len(no_dims)} row(s) with no "
            f"resolvable dimensions: {shown}{more}"
        )

    # Assign candidates per BOM row, ranked by relevance
    for comp in state.source_bom:
        ref = comp.get("ref_des", comp.get("name", "?"))
        cat = comp.get("component_type", comp.get("category", ""))
        all_candidates = mfr_cache.get(cat, [])
        stress = state.stress_by_ref.get(ref)
        state.candidates_by_ref[ref] = _rank_candidates(
            comp,
            cat,
            all_candidates,
            max_results=50,
            stress=stress,
        )

    total = sum(len(v) for v in state.candidates_by_ref.values())
    logger.info(
        "CR stage 1: prefetched %d candidates across %d components",
        total,
        len(state.candidates_by_ref),
    )
    return state


# ---------------------------------------------------------------------------
# Stage 1.5: Librarian fetch for gaps (distributor API)
# ---------------------------------------------------------------------------


def _stage1_5_librarian(state: CrossRefState) -> CrossRefState:
    """For components with 0 relevant candidates, search Digi-Key for the
    target manufacturer and inject results into the candidate list.

    This is a best-effort stage — credential or API errors are logged
    but don't abort the pipeline.
    """
    try:
        from heaviside.librarian.fetcher import (
            DigiKeyClient,
            load_credentials,
        )
        from heaviside.librarian.fetcher.convert import (
            convert_digikey_to_tas_capacitor,
            convert_digikey_to_tas_resistor,
        )
    except ImportError:
        logger.info("CR stage 1.5: librarian not available — skipping")
        return state

    from heaviside.pipeline.value_parse import (
        parse_capacitance,
        parse_resistance,
    )

    def _has_close_match(comp: dict[str, Any], cat: str, cands: list[dict[str, Any]]) -> bool:
        """Check if any candidate is within 2× of the target value."""
        value_str = str(comp.get("value", ""))
        if not value_str:
            return False
        target = 0.0
        if cat == "capacitor":
            target = parse_capacitance(value_str)
        elif cat == "resistor":
            target = parse_resistance(value_str)
        if target == 0.0:
            # 0Ω: check if any candidate has 0Ω
            if cat == "resistor":
                return any((_extract_value(c, cat) or -1) == 0.0 for c in cands[:20])
            return len(cands) > 0
        for c in cands[:20]:
            cv = _extract_value(c, cat)
            if cv and cv > 0:
                ratio = cv / target
                if 0.5 <= ratio <= 2.0:
                    return True
        return False

    gaps: list[tuple[str, dict[str, Any]]] = []
    for comp in state.source_bom:
        ref = comp.get("ref_des", comp.get("name", "?"))
        cat = comp.get("component_type", "")
        if cat not in ("capacitor", "resistor"):
            continue
        value_str = comp.get("value", "")
        if not value_str or value_str == "NC":
            continue
        cands = state.candidates_by_ref.get(ref, [])
        if _has_close_match(comp, cat, cands):
            continue
        gaps.append((ref, comp))

    if not gaps:
        logger.info("CR stage 1.5: no gaps to fill")
        return state

    try:
        creds = load_credentials()
        dk = DigiKeyClient(creds.digikey)
    except Exception as exc:
        # Optional best-effort stage: with no Digi-Key credentials configured we
        # simply skip distributor gap-filling (the internal DB still drives the
        # crossref). Phrase it as a skipped optional step, not an error.
        logger.info("CR stage 1.5: Digi-Key gap-fill unavailable (%s) — skipping", exc)
        state.diagnostics.append(f"librarian gap-fill skipped (Digi-Key not configured): {exc}")
        return state

    target_mfr = state.target_manufacturer
    fetched = 0
    converters = {
        "capacitor": convert_digikey_to_tas_capacitor,
        "resistor": convert_digikey_to_tas_resistor,
    }

    # Deduplicate searches by (category, value, package)
    searched: dict[tuple[str, str, str], list[dict[str, Any]]] = {}

    for ref, comp in gaps:
        cat = comp.get("component_type", "")
        value_str = comp.get("value", "")
        package = comp.get("package", "")
        cache_key = (cat, value_str, package)

        if cache_key in searched:
            state.candidates_by_ref[ref] = searched[cache_key]
            continue

        keywords = f"{target_mfr} {value_str} {package} {cat}".strip()
        try:
            result = dk.search(keywords, limit=20)
        except Exception as exc:
            logger.warning("CR stage 1.5: Digi-Key search failed for %s: %s", ref, exc)
            searched[cache_key] = []
            continue

        products = result.get("Products", [])
        convert_fn = converters.get(cat)
        if not convert_fn:
            searched[cache_key] = []
            continue

        envelopes: list[dict[str, Any]] = []
        for prod in products:
            mfr_name = prod.get("Manufacturer", {}).get("Value", "")
            if not mfr_name:
                continue
            mfr_norm = _normalize_manufacturer(mfr_name)
            target_norm = _normalize_manufacturer(target_mfr)
            if target_norm not in mfr_norm and mfr_norm not in target_norm:
                continue
            try:
                env = convert_fn(prod)
                envelopes.append(env)
                _persist_digikey_product(prod, cat)
            except Exception:
                continue

        ranked = _rank_candidates(comp, cat, envelopes, max_results=50)
        searched[cache_key] = ranked
        state.candidates_by_ref[ref] = ranked
        fetched += len(ranked)
        logger.info(
            "CR stage 1.5: fetched %d candidates for %s (%s %s)",
            len(ranked),
            ref,
            value_str,
            package,
        )

    logger.info(
        "CR stage 1.5: librarian fetched %d total candidates for %d gaps", fetched, len(gaps)
    )
    return state


_FETCH_ORIGINALS_CAP = 40  # DK rate-limit + runtime guard: max originals fetched per run


def _stage1_6_fetch_originals(state: CrossRefState) -> CrossRefState:
    """Fetch each BOM component's ORIGINAL from Digi-Key when it is not in the
    internal DB, convert + schema-validate + persist it, so the cross-reference
    can VERIFY the part instead of returning no_substitute just because our
    catalogue lacks it (user: "the librarian should fetch everything").

    Best-effort: no Digi-Key credentials, an unconvertible category, or a part
    Digi-Key doesn't have simply leaves the original unresolved (the identity
    no_substitute rule then applies, as before). Never persists an original that
    fails schema validation.

    A row that carries NO category (a bare pasted MPN not in the internal
    catalogue and with no description keyword — the very case that reads as
    "connector 1707654 not found") is classified here FROM Digi-Key's own product
    taxonomy: the librarian looks the MPN up, sets the row's ``component_type``,
    and then sources the original. Otherwise such rows would be skipped and the
    LLM would guess the category too late for the original to ever be fetched."""
    from heaviside.pipeline.guardrails import lookup_part_fields

    try:
        from heaviside.librarian.fetcher import DigiKeyClient, load_credentials
        from heaviside.librarian.fetcher.original import (
            CR_CATEGORY_BY_DB,
            classify_dk_product,
            fetch_dk_product,
            fetch_original_envelope,
        )
        from heaviside.librarian.tas import DuplicateComponentError, add_component
    except ImportError as exc:
        logger.info("CR stage 1.6: original-fetch unavailable (%s) — skipping", exc)
        return state

    def _row_mpn(comp: dict[str, Any]) -> str:
        return str(comp.get("original_mpn") or comp.get("part") or "").strip()

    _SKIP = ("nc", "dnp", "n/a", "no_substitute")

    # Anything to do? Either an uncategorised real MPN (needs classifying) or a
    # categorised MPN missing from the internal DB (needs sourcing).
    has_uncategorised = any(
        not comp.get("component_type", comp.get("category", ""))
        and _row_mpn(comp)
        and _row_mpn(comp).lower() not in _SKIP
        for comp in state.source_bom
    )
    # Build the categorised "wanted" set up front (cheap DB lookups only).
    wanted: dict[tuple[str, str], str] = {}  # (cat, mpn_lower) -> original mpn
    for comp in state.source_bom:
        cat = comp.get("component_type", comp.get("category", ""))
        mpn = _row_mpn(comp)
        if not cat or not mpn or mpn.lower() in _SKIP:
            continue
        key = (cat, mpn.lower())
        if key in wanted:
            continue
        try:
            if lookup_part_fields(mpn, cat) is not None:
                continue  # already resolvable — nothing to fetch
        except Exception:
            pass
        wanted[key] = mpn

    if not wanted and not has_uncategorised:
        return state

    try:
        creds = load_credentials()
        dk = DigiKeyClient(creds.digikey)
    except Exception as exc:
        logger.info("CR stage 1.6: Digi-Key not configured (%s) — skipping original fetch", exc)
        state.diagnostics.append(f"original fetch skipped (Digi-Key not configured): {exc}")
        return state

    # Products fetched here, keyed by mpn_lower, so the classify pass and the
    # source pass don't hit Digi-Key twice for the same part.
    product_cache: dict[str, dict[str, Any] | None] = {}

    def _get_product(mpn: str) -> dict[str, Any] | None:
        key = mpn.lower()
        if key not in product_cache:
            product_cache[key] = fetch_dk_product(dk, mpn)
        return product_cache[key]

    fetched = 0
    touched_files = False
    calls_left = _FETCH_ORIGINALS_CAP

    # Pass A: classify uncategorised bare-MPN rows from Digi-Key's taxonomy and
    # set their component_type + enqueue them for sourcing.
    if has_uncategorised:
        seen_uncat: set[str] = set()
        for comp in state.source_bom:
            if comp.get("component_type", comp.get("category", "")):
                continue
            mpn = _row_mpn(comp)
            if not mpn or mpn.lower() in _SKIP or mpn.lower() in seen_uncat:
                continue
            seen_uncat.add(mpn.lower())
            if calls_left <= 0:
                logger.info("CR stage 1.6: classify cap reached; %s left uncategorised", mpn)
                continue
            calls_left -= 1
            product = _get_product(mpn)
            cat = classify_dk_product(product) if product else None
            if not cat:
                logger.info("CR stage 1.6: could not classify bare MPN %s from Digi-Key", mpn)
                continue
            # Stamp the category on every row that shares this MPN.
            for c in state.source_bom:
                if _row_mpn(c).lower() == mpn.lower() and not c.get("component_type"):
                    c["component_type"] = cat
            # Not already in the internal DB (uncategorised → definitionally not
            # resolvable), so queue for sourcing.
            wanted[(cat, mpn.lower())] = mpn

    # Pass B: source + persist each wanted original (reusing cached products).
    for (cat, _mpn_l), mpn in list(wanted.items()):
        if calls_left <= 0:
            logger.info("CR stage 1.6: fetch cap reached at %d originals", fetched)
            break
        calls_left -= 1
        product = _get_product(mpn)
        try:
            env, db_cat_or_reason = fetch_original_envelope(dk, mpn, cat, product=product)
        except Exception as exc:
            logger.debug("CR stage 1.6: fetch %s (%s) errored: %s", mpn, cat, exc)
            continue
        if env is None:
            logger.info("CR stage 1.6: could not source original %s (%s): %s", mpn, cat, db_cat_or_reason)
            continue
        try:
            add_component(db_cat_or_reason, env)
            fetched += 1
            touched_files = True
            logger.info("CR stage 1.6: fetched + persisted original %s (%s)", mpn, cat)
        except DuplicateComponentError:
            touched_files = True  # already there now — still refresh caches
        except Exception as exc:
            logger.warning("CR stage 1.6: persist of original %s failed: %s", mpn, exc)

    if touched_files:
        # The newly-appended originals must be visible to the param-check /
        # guardrail lookups this run: drop the stale per-file indexes so they
        # rebuild from the updated NDJSON.
        try:
            from heaviside.pipeline import guardrails as _g
            from heaviside.pipeline import match_score as _ms

            _g._TAS_INDEX_CACHE.clear()
            _g._TAS_LOOKUP_CACHE.clear()
            _ms._MPN_ENV_INDEX_CACHE.clear()
        except Exception:
            pass

    if wanted:
        logger.info(
            "CR stage 1.6: sourced %d/%d unknown originals from Digi-Key", fetched, len(wanted)
        )
    return state


_RESOLVE_PARTS_CAP = 200  # bound LLM cost on huge BOMs
_RESOLVE_BATCH = 40


def _needs_part_resolution(row: dict[str, Any]) -> bool:
    """A row whose manufacturer/MPN is mashed together, prefixed with a
    separator, or carries the manufacturer inside the MPN — the messy pasted
    shapes an LLM should clean (e.g. 'Phoenix C  1707654', 'VISHAY /IHLP…')."""
    mpn = str(row.get("original_mpn") or row.get("part") or "").strip()
    if not mpn:
        return False
    if re.search(r"\s", mpn):  # whitespace inside an MPN → mfr+code mashed
        return True
    if mpn[0] in "/\\|,;:" or mpn[-1] in "/\\|,;:":  # leading/trailing separator
        return True
    mfr = str(row.get("manufacturer") or "").strip()
    if mfr and len(mfr) >= 3 and mfr.split()[0].lower() in mpn.lower():
        return True
    return False


def _stage0_resolve_parts(state: CrossRefState) -> CrossRefState:
    """LLM-clean messy BOM rows into {manufacturer, mpn} BEFORE lookup.

    Engineers paste rows like 'Phoenix C  1707654' or 'VISHAY /IHLP1616ABER1R5M11'
    where the manufacturer and part are mashed together or prefixed with junk.
    Those never resolve against the catalogue as-is. This stage sends only the
    messy rows to the ``part-resolver`` agent, which SEPARATES + CLEANS them
    (never invents an MPN — the cleaned MPN is still verified downstream by the
    catalogue / Digi-Key fetch). Best-effort: no LLM key or an agent error
    leaves the rows untouched."""
    messy = [r for r in state.source_bom if _needs_part_resolution(r)]
    if not messy:
        return state
    messy = messy[:_RESOLVE_PARTS_CAP]

    try:
        from heaviside.agents.llm_call import LLMCallError, call_agent_json
    except ImportError:
        return state

    def _raw(r: dict[str, Any]) -> str:
        return " ".join(
            str(v)
            for k, v in r.items()
            if v and not k.startswith("_") and isinstance(v, (str, int, float))
        )[:300]

    resolved_count = 0
    by_ref = {r.get("ref_des"): r for r in state.source_bom}
    for i in range(0, len(messy), _RESOLVE_BATCH):
        batch = messy[i : i + _RESOLVE_BATCH]
        payload = {
            "rows": [
                {
                    "ref_des": r.get("ref_des"),
                    "raw": _raw(r),
                    "manufacturer": r.get("manufacturer", ""),
                    "mpn": r.get("original_mpn", r.get("part", "")),
                }
                for r in batch
            ]
        }
        try:
            data = call_agent_json("part-resolver", json.dumps(payload), max_tokens=4096)
        except LLMCallError as exc:
            logger.info("CR stage 0: part-resolver unavailable (%s) — leaving rows as-is", exc)
            return state
        for item in data.get("resolved", []) if isinstance(data, dict) else []:
            ref = item.get("ref_des")
            row = by_ref.get(ref)
            if row is None:
                continue
            new_mpn = str(item.get("mpn") or "").strip()
            new_mfr = str(item.get("manufacturer") or "").strip()
            # Only apply a non-empty, actually-cleaner MPN (no whitespace).
            if new_mpn and not re.search(r"\s", new_mpn):
                old = str(row.get("original_mpn") or "")
                if new_mpn != old:
                    row["original_mpn"] = new_mpn
                    resolved_count += 1
            if new_mfr:
                row["manufacturer"] = new_mfr

    if resolved_count:
        logger.info("CR stage 0: resolved %d messy BOM part(s) via LLM", resolved_count)
        state.diagnostics.append(f"part-resolver cleaned {resolved_count} messy BOM row(s)")
    return state


def _is_chip_bead_env(env: dict[str, Any]) -> bool:
    """Return True when a magnetics.ndjson envelope is a chip bead (not an inductor)."""
    try:
        el = env["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"]
        return bool(el) and el[0].get("subtype") == "chipBead"
    except (KeyError, TypeError, IndexError):
        return False


def _chip_bead_impedance_at_100mhz(env: dict[str, Any]) -> float | None:
    """Return the impedance magnitude (Ω) at 100 MHz from a chip bead envelope.

    Searches impedancePoints for the point whose frequency is closest to 100e6 Hz.
    Falls back to None when no impedance curve is present."""
    try:
        el = env["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"]
        elec = el[0] if el else {}
        points = elec.get("impedancePoints")
        if not points:
            return None
        target_f = 100e6
        best = min(points, key=lambda p: abs(p.get("frequency", 0) - target_f))
        mag = (best.get("impedance") or {}).get("magnitude")
        return float(mag) if mag is not None else None
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def _analog_subtype_block(analog_block: Any) -> tuple[str | None, dict[str, Any] | None]:
    """(subtype, record) from an AAS analog block ``{"<subtype>": {...}}``.

    The analog envelope nests one level deeper than the other categories and
    the inner key is the FUNCTION (operationalAmplifier / comparator / adc /
    …), which varies per row — so the fixed-path descent the other categories
    use can't reach it. TBAS time-base documents share the shape one level
    down ({"timeBase": {"inputs": …, "oscillator": {...}}}): the family key
    (oscillator/timer/latch) is found the same way — it is the sibling of
    inputs/outputs that carries a manufacturerInfo."""
    if isinstance(analog_block, dict):
        for key, record in analog_block.items():
            if isinstance(record, dict) and "manufacturerInfo" in record:
                return key, record
    return None, None


def _category_record(env: dict[str, Any], category: str) -> dict[str, Any] | None:
    """The record dict holding ``manufacturerInfo`` for a TAS envelope,
    descending the category's base path (and the analog/timeBase subtype
    level)."""
    cur: Any = env
    for k in _BASE_PATHS.get(category, ()):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    if category in ("analog", "timeBase"):
        _, cur = _analog_subtype_block(cur)
    return cur if isinstance(cur, dict) else None


def _extract_manufacturer(env: dict[str, Any], category: str) -> str | None:
    """Extract manufacturer name from a TAS envelope."""
    record = _category_record(env, category)
    if record is None:
        return None
    mi = record.get("manufacturerInfo")
    name = mi.get("name") if isinstance(mi, dict) else None
    return name if isinstance(name, str) else None


def _envelope_reference(env: dict[str, Any], category: str) -> str | None:
    """Extract the part reference (MPN) from a TAS envelope."""
    record = _category_record(env, category)
    if record is None:
        return None
    mi = record.get("manufacturerInfo")
    if isinstance(mi, dict):
        ref = mi.get("reference")
        return ref if isinstance(ref, str) else None
    return None


def _extract_value(env: dict[str, Any], category: str) -> float | None:
    """Extract the primary electrical value from a TAS envelope (SI base units)."""
    try:
        if category == "capacitor":
            elec = env["capacitor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]
            cap = elec.get("capacitance")
            v = cap.get("nominal") if isinstance(cap, dict) else cap
            return float(v) if v is not None else None
        elif category == "resistor":
            elec = env["resistor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]
            res = elec.get("resistance")
            v = res.get("nominal") if isinstance(res, dict) else res
            return float(v) if v is not None else None
        elif category == "magnetic":
            elec = _magnetic_elec(
                env["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"]
            )
            ind = elec.get("inductance")
            v = ind.get("nominal") if isinstance(ind, dict) else ind
            return float(v) if v is not None else None
        elif category == "chipBead":
            return _chip_bead_impedance_at_100mhz(env)
        elif category == "varistor":
            vv = env["varistor"]["manufacturerInfo"]["datasheetInfo"]["electrical"].get(
                "varistorVoltage"
            )
            v = vv.get("nominal") if isinstance(vv, dict) else vv
            return float(v) if v is not None else None
        elif category == "connector":
            # Primary sorting value: rated current per contact
            v = env["connector"]["manufacturerInfo"]["datasheetInfo"]["electrical"].get(
                "ratedCurrentPerContact"
            )
            return float(v) if v is not None else None
    except (KeyError, TypeError, ValueError):
        pass
    return None


def _extract_package(env: dict[str, Any], category: str) -> str:
    """Extract package/case string from a TAS envelope."""
    try:
        record = _category_record(env, category)
        if record is None:
            return ""
        part = record["manufacturerInfo"]["datasheetInfo"]["part"]
        # Magnetics carry the size under `caseCode` (Würth WE-PD "1260" etc.),
        # chip passives under `case` (EIA "0402"…), analog ICs under `package`
        # (SOIC/TSSOP…). Accept any so the package signal isn't silently empty
        # for one family.
        return part.get("case") or part.get("caseCode") or part.get("package") or ""
    except (KeyError, TypeError):
        return ""


# Category → path to the manufacturerInfo block inside a TAS envelope.
# `analog` nests ONE MORE level (the subtype key) below its base path —
# always descend via _category_record, never by this path alone.
_BASE_PATHS: dict[str, tuple[str, ...]] = {
    "capacitor": ("capacitor",),
    "resistor": ("resistor",),
    "magnetic": ("magnetic",),
    "chipBead": ("magnetic",),
    "varistor": ("varistor",),
    "connector": ("connector",),
    "mosfet": ("semiconductor", "mosfet"),
    "diode": ("semiconductor", "diode"),
    "analog": ("analog",),
    "timeBase": ("timeBase",),
}

# Categories whose substitution is defined by the ORIGINAL's identity, not a
# parsed value — a connector either mates/fits or it doesn't; an analog IC's
# function/channels must match; a crystal's frequency/technology/load-C IS the
# part. For these, a substitute can only be justified when the original is
# resolvable (in the internal DB) so its identity is known. When it isn't, the
# pipeline must return no_substitute rather than force a value/chemistry match
# (they don't get the value-based deterministic rescue, and the param-check
# stage demotes an unverifiable original to no_substitute).
_IDENTITY_MATCHED_CATEGORIES: frozenset[str] = frozenset({"connector", "analog", "timeBase"})


# Standard EIA/IPC chip footprint dimensions: imperial case code → (L, W) in
# metres. Used to derive a physical body footprint for chip passives that carry
# only a case code and no explicit mechanical drawing. Heights are not
# standardised per case code (they depend on the value/dielectric), so only the
# L×W footprint — what governs whether a substitute fits the original's board
# space — is mapped. Source: IPC-7351 / EIA chip-size standard. This is a
# documented standard table, not a fabricated value.
_EIA_CHIP_DIMENSIONS_M: dict[str, tuple[float, float]] = {
    "01005": (0.0004, 0.0002),
    "0201": (0.0006, 0.0003),
    "0402": (0.0010, 0.0005),
    "0603": (0.0016, 0.0008),
    "0805": (0.0020, 0.00125),
    "1206": (0.0032, 0.0016),
    "1210": (0.0032, 0.0025),
    "1812": (0.0045, 0.0032),
    "2010": (0.0050, 0.0025),
    "2220": (0.0057, 0.0050),
    "2225": (0.0057, 0.0064),
}
_EIA_CODE_RE = re.compile(r"(?<!\d)(01005|0201|0402|0603|0805|1206|1210|1812|2010|2220|2225)(?!\d)")


def _dim_value(d: Any) -> float | None:
    """Pull a scalar length from a TAS dimension field (a {nominal/...} dict or
    a bare number). Returns None when absent — never invents a value."""
    if isinstance(d, dict):
        v = d.get("nominal")
        if v is None:
            v = d.get("maximum")
        if v is None:
            v = d.get("typical")
    else:
        v = d
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _eia_dims_from_case(case: str | None) -> tuple[float, float] | None:
    """Map a chip case-code string (e.g. "0402", "C0805") to its standard
    footprint (L, W) in metres, or None if it isn't a recognised EIA code."""
    if not case:
        return None
    m = _EIA_CODE_RE.search(str(case))
    if m:
        return _EIA_CHIP_DIMENSIONS_M.get(m.group(1))
    return None


def _extract_dimensions(
    env: dict[str, Any], category: str
) -> tuple[float, float, float | None] | None:
    """Return the part's physical body size as (length, width, height) in metres.

    Tries an explicit mechanical drawing first (``mechanical.length/width/height``
    for magnetics, ``mechanical.dimensions.*`` for caps/connectors), then falls
    back to the standard EIA footprint for a recognised chip case code. Height
    may be None when only a footprint is known. Returns None when nothing is
    available — no fabrication (CLAUDE.md: surface the gap, don't guess).
    """
    length = width = height = None
    try:
        record = _category_record(env, category)
        mech = (record or {})["manufacturerInfo"]["datasheetInfo"].get("mechanical") or {}
    except (KeyError, TypeError):
        mech = {}
    if isinstance(mech, dict):
        length = _dim_value(mech.get("length"))
        width = _dim_value(mech.get("width"))
        height = _dim_value(mech.get("height"))
        nested = mech.get("dimensions")
        if isinstance(nested, dict):
            if length is None:
                length = _dim_value(nested.get("length"))
            if width is None:
                width = _dim_value(nested.get("width"))
            if height is None:
                height = _dim_value(nested.get("height"))
    if length is not None and width is not None:
        return (length, width, height)
    eia = _eia_dims_from_case(_extract_package(env, category))
    if eia:
        return (eia[0], eia[1], None)
    return None


# Footprint-fit penalty weights. A substitute must fit the board space the
# original occupies; smaller is better, oversize is heavily penalised but still
# selectable when nothing else exists (per product spec). val_dist/pkg/stress
# terms sit in the 0–5 range, so OVERSIZE_BASE dominates them: any candidate
# that fits always outranks any candidate that doesn't, yet an oversize part
# stays finite-scored so it can win when it's the only option.
_FIT_AREA_WEIGHT = 0.5  # fitting parts: penalty = weight × area ratio
_OVERSIZE_BASE = 10.0  # flat floor applied to any part that overflows
_OVERSIZE_SCALE = 8.0  # extra penalty per unit of worst linear overflow
_UNKNOWN_DIM_PENALTY = 2.0  # candidate size unknown → can't confirm it fits
_DIM_TOLERANCE = 1.02  # 2 % slack for rounding / termination spread
# A substitute up to ~one EIA case size larger (e.g. 0402→0603, ≈0.6 linear
# overflow) is an ACCEPTABLE PARTIAL substitution for bypass/decoupling parts —
# offered with a "verify board fit" caveat rather than dropped. Its penalty
# stays below _OVERSIZE_BASE so the fit filter keeps it and the matcher sees it;
# ≥2 sizes up remains a heavily-penalised last resort.
_SLIGHTLY_OVERSIZE_OVERFLOW = 0.65  # worst-linear overflow that counts as one size up
# Kept small (penalty ≈1–2.6) so an EXACT-value part one size up still outranks a
# near-value fitting part — otherwise the value-match the user actually needs is
# pushed out of the top-N by closer-fitting wrong-value parts. Still above any
# true fit (≤_FIT_AREA_WEIGHT≈0.5) so a same-size match always wins when it exists.
_SLIGHTLY_OVERSIZE_BASE = 1.0  # partial floor: above any true fit, below _OVERSIZE_BASE
_SLIGHTLY_OVERSIZE_SCALE = 2.5  # gentle per-overflow slope (one size up stays < value-match weight)

# Weight applied to the value-distance term so the primary electrical value
# (resistance/inductance/capacitance) dominates package/footprint when ranking
# passives. Sized so even a small value error outranks the largest fitting-
# footprint penalty (_FIT_AREA_WEIGHT) — i.e. an exact-value part always beats a
# near-value part regardless of package.
_VALUE_MATCH_WEIGHT = 4.0


def _footprint_penalty(
    source_dims: tuple[float, float, float | None] | None,
    cand_dims: tuple[float, float, float | None] | None,
) -> float:
    """Score how well a candidate fits the original's board space.

    Orientation-agnostic on the footprint (a rotated part that still fits is not
    penalised). Returns 0.0 when the source size is unknown (cannot enforce —
    surfaced separately as a diagnostic), a small area-proportional penalty when
    the candidate fits (smaller → lower), and a large finite penalty when it
    overflows in any dimension.
    """
    if not source_dims:
        return 0.0
    if not cand_dims:
        return _UNKNOWN_DIM_PENALTY
    s_l, s_w, s_h = source_dims
    c_l, c_w, c_h = cand_dims
    if s_l is None or s_w is None or c_l is None or c_w is None:
        return _UNKNOWN_DIM_PENALTY
    s_long, s_short = max(s_l, s_w), min(s_l, s_w)
    c_long, c_short = max(c_l, c_w), min(c_l, c_w)
    if s_long <= 0 or s_short <= 0:
        return _UNKNOWN_DIM_PENALTY
    fits = c_long <= s_long * _DIM_TOLERANCE and c_short <= s_short * _DIM_TOLERANCE
    if s_h is not None and c_h is not None:
        fits = fits and c_h <= s_h * _DIM_TOLERANCE
    if fits:
        area_ratio = (c_long * c_short) / (s_long * s_short)
        return _FIT_AREA_WEIGHT * area_ratio
    overflow = (
        max(
            c_long / s_long,
            c_short / s_short,
            (c_h / s_h) if (s_h and c_h) else 1.0,
        )
        - 1.0
    )
    overflow = max(overflow, 0.0)
    if overflow <= _SLIGHTLY_OVERSIZE_OVERFLOW:
        # ~one EIA case size larger: an acceptable PARTIAL (verify board fit).
        # Penalty stays < _OVERSIZE_BASE so it is NOT dropped by the fit filter
        # and reaches the matcher; gentle so an exact-value part one size up still
        # outranks a near-value fitting part, but above any true fit.
        return _SLIGHTLY_OVERSIZE_BASE + _SLIGHTLY_OVERSIZE_SCALE * overflow
    return _OVERSIZE_BASE + _OVERSIZE_SCALE * overflow


def _footprint_tier(
    source_dims: tuple[float, float, float | None] | None,
    cand_dims: tuple[float, float, float | None] | None,
) -> bool | str:
    """Categorise the fit: ``True`` (fits), ``"one_size_larger"`` (acceptable
    partial, verify board fit), ``False`` (overflows by ≥ ~2 sizes), or
    ``"unknown"``. Derived from :func:`_footprint_penalty` so the thresholds stay
    in one place."""
    if not source_dims:
        return "unknown"
    if not cand_dims or cand_dims[0] is None or cand_dims[1] is None:
        return "unknown"
    pen = _footprint_penalty(source_dims, cand_dims)
    if pen >= _OVERSIZE_BASE:
        return False
    if pen >= _SLIGHTLY_OVERSIZE_BASE:
        return "one_size_larger"
    return True


VOLTAGE_DERATING_FACTOR = 1.25
DIODE_VOLTAGE_DERATING = 1.50
SATURATION_MARGIN = 0.90
CURRENT_DERATING_FACTOR = 1.25


@functools.lru_cache(maxsize=1)
def _eia_dielectric_codes() -> tuple[str, ...]:
    """EIA/MIL ceramic dielectric codes (C0G, X7R, X7T, …), uppercase.

    Loaded from the CANONICAL CAS map (CAS/data/eia_dielectric_codes.json) —
    CAS owns the dielectric taxonomy (its `technology` enum is the chemistry
    family; this file maps each EIA code to a ceramic class). We do NOT hardcode
    the list here; downstream chemistry-family logic stays in sync with CAS.
    A missing/empty CAS map is a loud error (CLAUDE.md: no silent fallback)."""
    path = Path(__file__).resolve().parents[2] / "CAS" / "data" / "eia_dielectric_codes.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"cannot load canonical CAS dielectric codes from {path}: {exc}"
        ) from exc
    codes = tuple((data.get("codeToTechnology") or {}).keys())
    if not codes:
        raise RuntimeError(f"CAS dielectric code map at {path} is empty")
    return codes


# Manufacturer ceramic-MLCC series prefixes (the dielectric is positional
# in these MPNs, not a literal substring, so a prefix map is how we tell a
# Murata GRM / TDK CGA / Samsung CL etc. is a ceramic).
_CERAMIC_MPN_PREFIXES = (
    "GRM",
    "GCM",
    "GRT",
    "GJM",
    "GMD",
    "GA3",
    "GCJ",
    "GQM",  # Murata
    "CGA",
    "CKG",
    "CGJ",  # TDK (+ bare C#### handled below)
    "CL03",
    "CL05",
    "CL10",
    "CL21",
    "CL31",
    "CL32",
    "CL05",
    "CL",  # Samsung
    "AC0",
    "CC0",
    "CC1",
    "CC2",  # Yageo
    "WCAP-CSGP",
    "WCAP-CSMH",
    "WCAP-CSSA",
    "WCAP-CSST",  # Würth
    "C0402",
    "C0603",
    "C0805",
    "C1206",  # Kemet/generic
)


def _is_polymer_cap(technology: str | None) -> bool:
    """A conductive-polymer capacitor (polymer tantalum / polymer aluminum /
    SP-Cap / POSCAP / OS-CON). Cross-substitutable within the polymer group for
    low-ESR bulk decoupling even across the anode metal (Ta ↔ Al), unlike a wet
    electrolytic or an MnO2 tantalum."""
    t = (technology or "").lower()
    return any(k in t for k in ("polymer", "os-con", "oscon", "poscap", "sp-cap", "sp cap"))


def _capacitor_technology_family(technology: str | None) -> str | None:
    """Collapse a CAS technology string (or loose intent like 'ceramic' /
    'X7R' / 'MLCC') to a chemistry FAMILY, so a crossref stays in-kind.
    Returns None when nothing is given. This is what stops a ceramic
    query from ranking 2.7V supercaps and aluminium-electrolytics
    alongside the MLCCs."""
    if not technology:
        return None
    t = technology.strip().lower()
    if not t:
        return None
    if "thin-film" in t or "thin film" in t:
        return "thin-film-silicon"
    if (
        "ceramic" in t
        or "mlcc" in t
        or any(len(c) >= 3 and c.lower() in t for c in _eia_dielectric_codes())
    ):
        return "ceramic"
    if "tantal" in t:
        return "tantalum"
    if "niobium" in t:
        return "niobium"
    if "alum" in t or "electrolytic" in t or "polymer" in t or "hybrid" in t:
        return "aluminum"
    if any(
        k in t
        for k in ("film", "polyprop", "polyest", "paper", "pps", "polyphenylene", "mkt", "mkp")
    ):
        return "film"
    if "supercap" in t or "edlc" in t or "super cap" in t:
        return "supercapacitor"
    if "mica" in t:
        return "mica"
    return t


def _infer_source_cap_technology(comp: dict[str, Any]) -> str | None:
    """Infer the ORIGINAL capacitor's chemistry family from the BOM row.
    Uses an explicit dielectric/technology field if present, else an EIA
    code or a known ceramic-MLCC series prefix in the part number. Returns
    None when it cannot be determined confidently (no penalty is then
    applied — we never guess a family)."""
    for field in ("dielectric", "technology", "temperature_coefficient", "tempco"):
        v = comp.get(field)
        if v:
            fam = _capacitor_technology_family(str(v))
            if fam:
                return fam
    blob = " ".join(
        str(comp.get(k, "")) for k in ("part", "mpn", "value", "description", "series")
    ).upper()
    if any(code in blob for code in _eia_dielectric_codes()):
        return "ceramic"
    part = str(comp.get("part") or comp.get("mpn") or "").upper().strip()
    if part.startswith(_CERAMIC_MPN_PREFIXES):
        return "ceramic"
    # bare TDK ceramic chip: C + 4 digits (C1608, C2012, C3216)
    if len(part) >= 5 and part[0] == "C" and part[1:5].isdigit():
        return "ceramic"
    return None


def _magnetic_elec(elec_raw: Any) -> dict[str, Any]:
    """Return the inductor electrical dict from a TAS magnetic electrical field.

    TAS v2 stores magnetics electrical as a list of subtype items; v1 used a plain
    dict. Both shapes are supported: for list, return the first item that has
    inductance or saturationCurrentPeak (i.e. the inductor item), falling back to
    the first item. For dict, return as-is."""
    if isinstance(elec_raw, list):
        for item in elec_raw:
            if isinstance(item, dict) and ("inductance" in item or "saturationCurrentPeak" in item):
                return item
        return elec_raw[0] if elec_raw and isinstance(elec_raw[0], dict) else {}
    return elec_raw if isinstance(elec_raw, dict) else {}


def _effective_saturation_current(cand_elec: dict[str, Any]) -> float | None:
    """Saturation current for a magnetic candidate's electrical block.

    ABT #6 stopgap (manufacturer-agnostic): many catalog magnetics that lack a
    published saturation current are chokes/beads/transformers with no Isat by
    design; for the few genuine inductors that still lack one, the rated current
    is a usable lower-confidence current bound. So when a part has no
    ``saturationCurrentPeak`` we fall back to ``ratedCurrent`` rather than
    skipping the saturation check entirely. A real ``saturationCurrentPeak``
    always wins. Remove once a real-Isat source lands — see memory
    ``wurth-isat-redexpert-exhausted``.
    """
    isat = cand_elec.get("saturationCurrentPeak")
    if isat is None:
        isat = cand_elec.get("ratedCurrent")
    return isat


def _ident_norm(v: Any) -> str:
    """Normalize an identity attribute for comparison: case/space/dash/underscore
    insensitive. Strips formatting only — never meaning."""
    return str(v).strip().lower().replace("-", "").replace(" ", "").replace("_", "")


def _attr_mismatch_str(a: dict[str, Any], b: dict[str, Any], key: str) -> bool:
    """True when BOTH sides carry the attribute and they differ (drop the
    candidate). A missing side is NOT a mismatch here — it is surfaced later
    as an unverified parameter by the param-check stage."""
    av, bv = a.get(key), b.get(key)
    if av is None or bv is None:
        return False
    return _ident_norm(av) != _ident_norm(bv)


def _attr_mismatch_num(a: dict[str, Any], b: dict[str, Any], key: str, rel_tol: float) -> bool:
    av, bv = a.get(key), b.get(key)
    if av is None or bv is None:
        return False
    try:
        af, bf = float(av), float(bv)
    except (TypeError, ValueError):
        return False
    if af == bf:
        return False
    denom = max(abs(af), abs(bf))
    return denom == 0 or abs(af - bf) / denom > rel_tol


def _connector_attrs(env: dict[str, Any]) -> dict[str, Any]:
    """Substitution-relevant attributes of a connector envelope (absent → key
    omitted, never a fabricated default). Sparse structured fields (pitch,
    gender, mounting) are backfilled from the vendor's OWN catalogue
    description text — grounded in the record, not invented."""
    try:
        mi = env["connector"]["manufacturerInfo"]
        ds = mi["datasheetInfo"]
    except (KeyError, TypeError):
        return {}
    mech = ds.get("mechanical") or {}
    elec = ds.get("electrical") or {}
    part = ds.get("part") or {}
    fd = ds.get("familyDetails") or {}
    temp = (ds.get("environmental") or {}).get("operatingTemperature") or {}
    out = {
        "family": fd.get("family"),
        "interface_standard": fd.get("interfaceStandard"),
        "series": part.get("series"),
        "positions": mech.get("positions"),
        "pitch": mech.get("pitch"),  # SI metres
        "polarity": part.get("matingPolarity"),
        "mounting": mech.get("mountingStyle"),
        "current": elec.get("ratedCurrentPerContact"),
        "voltage": elec.get("ratedVoltage"),
        "temp_min": temp.get("minimum"),
        "temp_max": temp.get("maximum"),
        "cycles": mech.get("matingCycles"),
    }
    out = {k: v for k, v in out.items() if v is not None}
    missing = {"pitch", "polarity", "mounting"} - set(out)
    if missing:
        desc = " ".join(
            str(x) for x in (mi.get("description"), part.get("description")) if x
        )
        if desc:
            signals = _connector_signals_from_text(desc)
            for key in missing:
                if key in signals:
                    out[key] = signals[key]
    return out


# Free-text fallbacks for originals that are not in the internal catalogue —
# a BOM description like "Header, 2.54mm pitch, 10POS, vertical, receptacle"
# still yields hard gates. Conservative: only unambiguous signals are used.
_CONN_POSITIONS_RE = re.compile(
    r"(\d+)\s*(?:pos(?:ition)?s?|way|ckt|circuits?|contacts?|pins?|p)\b", re.I
)
_CONN_PITCH_LABELLED_RE = re.compile(
    r"(?:pitch[^0-9]{0,6}(\d+(?:\.\d+)?)\s*mm)|(?:(\d+(?:\.\d+)?)\s*mm\s*pitch)", re.I
)
_CONN_MM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*mm\b", re.I)
_CONN_FAMILY_WORDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"terminal\s*block|screw\s*terminal", re.I), "terminalBlock"),
    (re.compile(r"card\s*edge", re.I), "cardEdge"),
    (re.compile(r"ffc|fpc|flat\s*flex", re.I), "fpcFfc"),
    (re.compile(r"usb|rj45|rj-45|modular\s*jack|d-?sub|db\d{1,2}|pcie|hdmi|sata|sfp", re.I), "dataInterface"),
    (re.compile(r"\bsma\b|\bbnc\b|\bmcx\b|\bmmcx\b|fakra|coax", re.I), "rf"),
    (re.compile(r"wire[\s-]*to[\s-]*board|crimp", re.I), "wireToBoard"),
    (re.compile(r"board[\s-]*to[\s-]*board|mezzanine", re.I), "boardToBoard"),
    (re.compile(r"\bm8\b|\bm12\b|circular", re.I), "circular"),
    (re.compile(r"header|socket\s*strip|pin\s*strip", re.I), "pinHeaderSocket"),
]
_CONN_STANDARD_WORDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"usb[\s-]*c|type[\s-]*c", re.I), "USB-C"),
    (re.compile(r"\busb\b", re.I), "USB"),
    (re.compile(r"rj-?45|modular\s*jack", re.I), "RJ45"),
    (re.compile(r"d-?sub|db\d{1,2}", re.I), "D-Sub"),
    (re.compile(r"pcie|pci\s*express", re.I), "PCIe"),
    (re.compile(r"fakra", re.I), "FAKRA"),
    (re.compile(r"sfp\+", re.I), "SFP+"),
]


def _connector_signals_from_text(text: str) -> dict[str, Any]:
    """Connector attributes recoverable from free text (a BOM row's columns or
    a vendor's catalogue description). Only unambiguous signals are extracted;
    anything else stays unknown."""
    out: dict[str, Any] = {}
    m = _CONN_POSITIONS_RE.search(text)
    if m:
        out["positions"] = int(m.group(1))
    m = _CONN_PITCH_LABELLED_RE.search(text)
    if m:
        out["pitch"] = float(m.group(1) or m.group(2)) * 1e-3
    else:
        mm = _CONN_MM_RE.findall(text)
        # A single mm figure in a connector row is (almost always) the pitch;
        # several mm figures are dimensions — too ambiguous, leave unknown.
        if len(mm) == 1 and 0.2 <= float(mm[0]) <= 12.0:
            out["pitch"] = float(mm[0]) * 1e-3
    low = text.lower()
    if re.search(r"\breceptacle\b|\bfemale\b|\bsocket\b|\bjack\b", low):
        out["polarity"] = "female"
    elif re.search(r"\bplug\b|\bmale\b|\bheader\b", low):
        out["polarity"] = "male"
    for pat, fam in _CONN_FAMILY_WORDS:
        if pat.search(text):
            out["family"] = fam
            break
    for pat, std in _CONN_STANDARD_WORDS:
        if pat.search(text):
            out["interface_standard"] = std
            break
    if re.search(r"\bsmt\b|\bsmd\b|surface\s*mount", low):
        out["mounting"] = "smt"
    elif re.search(r"\btht\b|through[\s-]*hole", low):
        out["mounting"] = "tht"
    return out


def _connector_attrs_from_text(comp: dict[str, Any]) -> dict[str, Any]:
    """Best-effort connector attributes from the BOM row's free text — the
    fallback when the original part is not in the internal catalogue."""
    text = " ".join(
        str(v)
        for k, v in comp.items()
        if v and not k.startswith("_") and isinstance(v, (str, int, float))
    )
    return _connector_signals_from_text(text) if text.strip() else {}


def _rank_connector_candidates(
    comp: dict[str, Any],
    all_candidates: list[dict[str, Any]],
    max_results: int,
) -> list[dict[str, Any]]:
    """Connector-specific ranking: identity attributes are HARD GATES.

    A connector substitute must match the original's family, contact count,
    gender, pitch, interface standard and mounting style exactly — there is no
    "nearby" connector the way there is a nearby E-series resistor. Ratings
    (current/voltage/temperature/mating cycles) then rank the survivors.
    Knows the original from its catalogue envelope (preferred) or from
    unambiguous BOM text. When NOTHING is known about the original, returns []
    — offering 50 arbitrary connectors to the cross-referencer would invite a
    plausible-looking wrong pick (fail loud, not fabricate).
    """
    src_env = comp.get("_source_env")
    attrs = _connector_attrs(src_env) if isinstance(src_env, dict) else {}
    if not attrs:
        attrs = _connector_attrs_from_text(comp)
    if not attrs:
        return []

    # Commodity pin headers straddle the pinHeaderSocket/boardToBoard boundary
    # across vendor taxonomies — gate on the GROUP, not the raw label.
    _header_group = {"pinheadersocket": "headerlike", "boardtoboard": "headerlike"}

    def _family_group(v: Any) -> str:
        n = _ident_norm(v)
        return _header_group.get(n, n)

    scored: list[tuple[float, dict[str, Any]]] = []
    for cand in all_candidates:
        c = _connector_attrs(cand)
        if not c:
            continue
        # Identity hard gates — mismatch on any known-both attribute drops it.
        if (
            attrs.get("family") is not None
            and c.get("family") is not None
            and _family_group(attrs["family"]) != _family_group(c["family"])
        ):
            continue
        if _attr_mismatch_num(attrs, c, "positions", 0.0):
            continue
        if _attr_mismatch_str(attrs, c, "polarity"):
            continue
        if _attr_mismatch_num(attrs, c, "pitch", 0.02):
            continue
        if _attr_mismatch_str(attrs, c, "interface_standard"):
            continue
        if _attr_mismatch_str(attrs, c, "mounting"):
            continue
        score = 0.0
        # Ratings: substitute must meet or beat the original.
        oc, cc = attrs.get("current"), c.get("current")
        if oc is not None and cc is not None and float(cc) < float(oc):
            score += 5.0
        ov, cv = attrs.get("voltage"), c.get("voltage")
        if ov is not None and cv is not None and float(cv) < float(ov):
            score += 3.0
        if (
            attrs.get("temp_min") is not None
            and c.get("temp_min") is not None
            and float(c["temp_min"]) > float(attrs["temp_min"])
        ):
            score += 1.0
        if (
            attrs.get("temp_max") is not None
            and c.get("temp_max") is not None
            and float(c["temp_max"]) < float(attrs["temp_max"])
        ):
            score += 1.0
        ocy, ccy = attrs.get("cycles"), c.get("cycles")
        if ocy is not None and ccy is not None and float(ccy) < 0.5 * float(ocy):
            score += 1.0
        # Prefer candidates whose identity attributes are VERIFIABLE: an
        # unknown pitch/positions can't be gated above and would surface as
        # an unverified (→ partial) later.
        for key, pen in (("positions", 1.0), ("pitch", 0.75), ("polarity", 0.5), ("family", 0.5)):
            if attrs.get(key) is not None and c.get(key) is None:
                score += pen
        # Same mating system (series) is the ideal substitution.
        if (
            attrs.get("series")
            and c.get("series")
            and _ident_norm(attrs["series"]) == _ident_norm(c["series"])
        ):
            score -= 1.0
        scored.append((score, cand))
    scored.sort(key=lambda x: x[0])
    return [c for _, c in scored[:max_results]]


def _analog_attrs(env: dict[str, Any]) -> dict[str, Any]:
    """Substitution-relevant attributes of an AAS analog envelope."""
    subtype, record = _analog_subtype_block((env or {}).get("analog"))
    if record is None:
        return {}
    ds = (record.get("manufacturerInfo") or {}).get("datasheetInfo") or {}
    elec = ds.get("electrical") or {}
    supply = elec.get("supply") or {}
    out = {
        "subtype": subtype,
        "channels": elec.get("numberOfChannels"),
        "supply_min": supply.get("minimumSupplyVoltage"),
        "supply_max": supply.get("maximumSupplyVoltage"),
        "gbw": elec.get("gainBandwidthProduct"),
        "slew": elec.get("slewRate"),
        "vos": elec.get("inputOffsetVoltage"),
        "package": (ds.get("part") or {}).get("package"),
        "output_stage": elec.get("outputStage"),
        "resolution": elec.get("resolution"),
        "sample_rate": elec.get("sampleRate"),
        "tpd": elec.get("propagationDelay"),
    }
    return {k: v for k, v in out.items() if v is not None}


_ANALOG_TEXT_SUBTYPE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"instrumentation\s*amp", re.I), "instrumentationAmplifier"),
    (re.compile(r"difference\s*amp", re.I), "differenceAmplifier"),
    (re.compile(r"programmable\s*gain", re.I), "programmableGainAmplifier"),
    (re.compile(r"op[\s-]*amp|operational\s*amp", re.I), "operationalAmplifier"),
    (re.compile(r"comparator", re.I), "comparator"),
    (re.compile(r"\badc\b|analog[\s-]*to[\s-]*digital", re.I), "adc"),
    (re.compile(r"\bdac\b|digital[\s-]*to[\s-]*analog", re.I), "dac"),
    (re.compile(r"multiplexer|\bmux\b", re.I), "multiplexer"),
    (re.compile(r"analog\s*switch", re.I), "analogSwitch"),
]


def _analog_attrs_from_text(comp: dict[str, Any]) -> dict[str, Any]:
    """Best-effort analog-IC attributes (function, channel count) from the BOM
    row's free text."""
    text = " ".join(
        str(v)
        for k, v in comp.items()
        if v and not k.startswith("_") and isinstance(v, (str, int, float))
    )
    out: dict[str, Any] = {}
    for pat, subtype in _ANALOG_TEXT_SUBTYPE:
        if pat.search(text):
            out["subtype"] = subtype
            break
    low = text.lower()
    if "quad" in low:
        out["channels"] = 4
    elif "dual" in low:
        out["channels"] = 2
    elif re.search(r"\bsingle\b", low):
        out["channels"] = 1
    return out


def _rank_analog_candidates(
    comp: dict[str, Any],
    all_candidates: list[dict[str, Any]],
    max_results: int,
) -> list[dict[str, Any]]:
    """Analog-IC ranking: function and channel count are HARD GATES (a
    comparator never substitutes an op-amp; a quad never substitutes a dual);
    the supply window must cover the original's; dynamic specs (GBW, slew,
    Vos, sample rate) rank the survivors by log-distance so a 10× slower part
    ranks behind a 1.2× one. Returns [] when nothing is known about the
    original — no honest ranking exists."""
    import math

    src_env = comp.get("_source_env")
    attrs = _analog_attrs(src_env) if isinstance(src_env, dict) else {}
    if not attrs:
        attrs = _analog_attrs_from_text(comp)
    if not attrs:
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    for cand in all_candidates:
        c = _analog_attrs(cand)
        if not c:
            continue
        if _attr_mismatch_str(attrs, c, "subtype"):
            continue
        if _attr_mismatch_num(attrs, c, "channels", 0.0):
            continue
        if _attr_mismatch_str(attrs, c, "output_stage"):
            continue
        score = 0.0
        # Supply window must cover the original's operating window.
        if (
            attrs.get("supply_min") is not None
            and c.get("supply_min") is not None
            and float(c["supply_min"]) > float(attrs["supply_min"]) + 0.3
        ):
            score += 3.0
        if (
            attrs.get("supply_max") is not None
            and c.get("supply_max") is not None
            and float(c["supply_max"]) < float(attrs["supply_max"]) * 0.9
        ):
            score += 3.0
        # Dynamic specs: log-distance keeps decades comparable.
        for key in ("gbw", "slew", "sample_rate", "tpd"):
            o, cv = attrs.get(key), c.get(key)
            if o and cv and float(o) > 0 and float(cv) > 0:
                score += abs(math.log10(float(cv) / float(o)))
            elif o and not cv:
                score += 1.0  # unverifiable dynamic spec
        # Precision: only a WORSE offset costs (a better one is free).
        o, cv = attrs.get("vos"), c.get("vos")
        if o and cv and float(o) > 0 and float(cv) > float(o):
            score += min(math.log10(float(cv) / float(o)) + 0.5, 2.5)
        # Data converters: resolution is a floor.
        if attrs.get("resolution") and c.get("resolution"):
            if int(c["resolution"]) < int(attrs["resolution"]):
                score += 5.0
            else:
                score += 0.2 * (int(c["resolution"]) - int(attrs["resolution"]))
        if (
            attrs.get("package")
            and c.get("package")
            and _ident_norm(attrs["package"]) != _ident_norm(c["package"])
        ):
            score += 0.3
        if attrs.get("channels") is not None and c.get("channels") is None:
            score += 1.0
        scored.append((score, cand))
    scored.sort(key=lambda x: x[0])
    return [c for _, c in scored[:max_results]]


def _timebase_attrs(env: dict[str, Any]) -> dict[str, Any]:
    """Substitution-relevant attributes of a TBAS time-base envelope."""
    subtype, record = _analog_subtype_block((env or {}).get("timeBase"))
    if record is None:
        return {}
    ds = (record.get("manufacturerInfo") or {}).get("datasheetInfo") or {}
    elec = ds.get("electrical") or {}
    supply = elec.get("supply") or {}
    temp = (ds.get("thermal") or {}).get("operatingTemperature") or {}
    out = {
        "subtype": subtype,  # oscillator / timer / latch
        "technology": elec.get("technology"),  # quartzCrystal / mems / tcxo / …
        "frequency": elec.get("frequency"),
        "mode": elec.get("mode"),
        "output_type": elec.get("outputType"),
        "tolerance": elec.get("frequencyTolerance"),  # fractional (2e-05 = 20 ppm)
        "stability": elec.get("frequencyStability"),
        "load_capacitance": elec.get("loadCapacitance"),
        "esr": elec.get("equivalentSeriesResistance"),
        "supply_min": supply.get("minimumSupplyVoltage"),
        "supply_max": supply.get("maximumSupplyVoltage"),
        "temp_min": temp.get("minimum"),
        "temp_max": temp.get("maximum"),
        "package": (ds.get("part") or {}).get("case") or (ds.get("part") or {}).get("package"),
    }
    return {k: v for k, v in out.items() if v is not None}


_TB_FREQ_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(k|m|g)?hz\b", re.I)
_TB_CL_RE = re.compile(r"(\d+(?:\.\d+)?)\s*pf\b", re.I)
_TB_TECH_WORDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\btcxo\b", re.I), "tcxo"),
    (re.compile(r"\bvcxo\b", re.I), "vcxo"),
    (re.compile(r"\bocxo\b", re.I), "ocxo"),
    (re.compile(r"\bmems\b", re.I), "mems"),
    (re.compile(r"ceramic\s*resonator", re.I), "ceramicResonator"),
    # "crystal oscillator" (an ACTIVE clock) must win over bare "crystal".
    (re.compile(r"crystal\s*(?:clock\s*)?oscillator|\bxo\b", re.I), "crystalOscillator"),
    (re.compile(r"crystal|\bxtal\b|quartz", re.I), "quartzCrystal"),
    (re.compile(r"\boscillator\b|\bosc\b", re.I), "crystalOscillator"),
]


def _timebase_attrs_from_text(comp: dict[str, Any]) -> dict[str, Any]:
    """Best-effort time-base attributes (technology, frequency, CL) from the
    BOM row's free text — the fallback when the original is not catalogued."""
    text = " ".join(
        str(v)
        for k, v in comp.items()
        if v and not k.startswith("_") and isinstance(v, (str, int, float))
    )
    out: dict[str, Any] = {}
    m = _TB_FREQ_RE.search(text)
    if m:
        mult = {"k": 1e3, "m": 1e6, "g": 1e9}.get((m.group(2) or "").lower(), 1.0)
        out["frequency"] = float(m.group(1)) * mult
    m = _TB_CL_RE.search(text)
    if m:
        out["load_capacitance"] = float(m.group(1)) * 1e-12
    for pat, tech in _TB_TECH_WORDS:
        if pat.search(text):
            out["technology"] = tech
            break
    if out:
        out["subtype"] = "oscillator"
    return out


def _rank_timebase_candidates(
    comp: dict[str, Any],
    all_candidates: list[dict[str, Any]],
    max_results: int,
) -> list[dict[str, Any]]:
    """Time-base (crystal / oscillator) ranking: identity attributes are HARD
    GATES. Frequency must match exactly (it IS the part); technology must
    match (a MEMS clock is not a quartz crystal, an active XO is not a
    passive crystal); a crystal's load capacitance must match (wrong CL pulls
    the oscillator off frequency); an active oscillator's output type must
    match (CMOS vs LVDS are different interfaces). Tolerance/stability/ESR
    and temperature coverage rank the survivors. Returns [] when nothing is
    known about the original."""
    src_env = comp.get("_source_env")
    attrs = _timebase_attrs(src_env) if isinstance(src_env, dict) else {}
    if not attrs:
        attrs = _timebase_attrs_from_text(comp)
    if not attrs:
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    for cand in all_candidates:
        c = _timebase_attrs(cand)
        if not c:
            continue
        if _attr_mismatch_str(attrs, c, "subtype"):
            continue
        if _attr_mismatch_str(attrs, c, "technology"):
            continue
        # Frequency is the identity of a time base — 1e-4 relative window
        # absorbs catalogue rounding only.
        if _attr_mismatch_num(attrs, c, "frequency", 1e-4):
            continue
        if _attr_mismatch_str(attrs, c, "output_type"):
            continue
        if _attr_mismatch_str(attrs, c, "mode"):
            continue
        # Crystal load capacitance: standardized steps (8/10/12.5/18/20 pF)
        # are NOT interchangeable without re-deriving the load caps.
        if _attr_mismatch_num(attrs, c, "load_capacitance", 0.05):
            continue
        score = 0.0
        for key, worse_penalty in (("tolerance", 2.0), ("stability", 2.0), ("esr", 1.5)):
            o, cv = attrs.get(key), c.get(key)
            if o is not None and cv is not None and float(o) > 0 and float(cv) > float(o):
                score += min(worse_penalty, float(cv) / float(o) - 1.0)
            elif o is not None and cv is None:
                score += 0.75
        if (
            attrs.get("temp_min") is not None
            and c.get("temp_min") is not None
            and float(c["temp_min"]) > float(attrs["temp_min"])
        ):
            score += 1.0
        if (
            attrs.get("temp_max") is not None
            and c.get("temp_max") is not None
            and float(c["temp_max"]) < float(attrs["temp_max"])
        ):
            score += 1.0
        if (
            attrs.get("supply_min") is not None
            and c.get("supply_min") is not None
            and float(c["supply_min"]) > float(attrs["supply_min"]) + 0.3
        ):
            score += 3.0
        if (
            attrs.get("supply_max") is not None
            and c.get("supply_max") is not None
            and float(c["supply_max"]) < float(attrs["supply_max"]) * 0.9
        ):
            score += 3.0
        if (
            attrs.get("package")
            and c.get("package")
            and _ident_norm(attrs["package"]) != _ident_norm(c["package"])
        ):
            score += 0.3
        # Prefer candidates whose identity attributes are verifiable.
        for key, pen in (("load_capacitance", 0.75), ("technology", 0.5)):
            if attrs.get(key) is not None and c.get(key) is None:
                score += pen
        scored.append((score, cand))
    scored.sort(key=lambda x: x[0])
    return [c for _, c in scored[:max_results]]


def _rank_candidates(
    comp: dict[str, Any],
    category: str,
    all_candidates: list[dict[str, Any]],
    max_results: int = 50,
    stress: Any | None = None,
) -> list[dict[str, Any]]:
    """Rank and filter TAS candidates by relevance to the BOM component."""
    from heaviside.pipeline.value_parse import (
        parse_capacitance,
        parse_inductance,
        parse_resistance,
    )

    # Connectors, analog ICs and time bases are matched on identity
    # attributes, not a parsed value string — they have their own rankers
    # with hard gates.
    if category == "connector":
        return _rank_connector_candidates(comp, all_candidates, max_results)
    if category == "analog":
        return _rank_analog_candidates(comp, all_candidates, max_results)
    if category == "timeBase":
        return _rank_timebase_candidates(comp, all_candidates, max_results)

    value_str = str(comp.get("value", ""))
    package = str(comp.get("package", "")).lower()
    # Physical footprint the substitute must fit into (the original's board
    # space). Resolved in stage 1 from the source envelope / BOM case code.
    source_dims = comp.get("_source_dims_m")

    target_val: float | None = None
    if category == "capacitor" and value_str:
        target_val = parse_capacitance(value_str)
    elif category == "resistor" and value_str:
        target_val = parse_resistance(value_str)
    elif category == "magnetic" and value_str:
        target_val = parse_inductance(value_str)
    elif category == "chipBead" and value_str:
        target_val = parse_resistance(value_str)  # impedance in Ω
    elif category == "varistor" and value_str:
        from heaviside.pipeline.value_parse import parse_voltage

        target_val = parse_voltage(value_str)

    if target_val is None:
        # For a value-matched passive, ranking without a parseable value is not
        # value-based — the returned order is arbitrary. Log it so a downstream
        # consumer (rescue, review) knows not to trust position as a value proxy;
        # the primary-value gate in _stage_param_check is the real backstop.
        if category in ("capacitor", "resistor", "magnetic", "chipBead", "varistor") and value_str:
            logger.warning(
                "CR ranking: could not parse %s value %r — returning unranked "
                "candidates (order is not value-based)",
                category,
                value_str,
            )
        return all_candidates[:max_results]

    # 0Ω resistors: find candidates with 0Ω (jumpers)
    if target_val == 0.0 and category == "resistor":
        zero_ohm = [c for c in all_candidates if (_extract_value(c, category) or -1) == 0.0]
        if zero_ohm:
            return zero_ohm[:max_results]
        return all_candidates[:max_results]

    if target_val == 0.0:
        return all_candidates[:max_results]

    # For capacitors, also consider rated voltage from the BOM
    target_voltage: float | None = None
    source_cap_family: str | None = None
    if category == "capacitor":
        from heaviside.pipeline.value_parse import parse_voltage

        v_str = str(comp.get("rated_voltage", comp.get("voltage", "")))
        if v_str:
            target_voltage = parse_voltage(v_str) if isinstance(v_str, str) else float(v_str)
        source_cap_family = _infer_source_cap_technology(comp)

    scored: list[tuple[float, dict[str, Any]]] = []
    for cand in all_candidates:
        cand_val = _extract_value(cand, category)
        if cand_val is None or cand_val == 0.0:
            continue
        ratio = cand_val / target_val if target_val else 0.0
        # Value is the DEFINING spec for a passive — a 47Ω part must beat a 39Ω
        # one regardless of package. Weight the value distance so it dominates
        # the package/footprint terms (those decide only between same-value
        # parts). Without this, a same-package wrong-value part used to outrank
        # an exact-value part in a smaller footprint (CAY16470J4LF 47Ω → 39Ω bug).
        # Capacitors: a HIGHER value is usually fine (bypass/bulk), so keep
        # overcap a mild, unweighted penalty.
        if category == "capacitor" and ratio >= 1.0:
            val_dist = (ratio - 1.0) * 0.3  # mild penalty for overcap
        else:
            val_dist = abs(1.0 - ratio) * _VALUE_MATCH_WEIGHT
        # Package string match is now only a minor tie-breaker between equally
        # good candidates: the real physical fit is handled by footprint_penalty
        # below (orientation-aware, dimension-based). Keep these small so they
        # never override a value difference.
        cand_pkg = _extract_package(cand, category).lower()
        if package and package in cand_pkg:
            pkg_penalty = 0.0
        elif _is_one_size_up(package, cand_pkg):
            pkg_penalty = 0.05
        else:
            pkg_penalty = 0.15
        # Voltage: for capacitors, reject candidates below target voltage
        voltage_penalty = 0.0
        if target_voltage and target_voltage > 0 and category == "capacitor":
            try:
                cand_v = cand["capacitor"]["manufacturerInfo"]["datasheetInfo"]["electrical"].get(
                    "ratedVoltage"
                )
                if cand_v is not None:
                    cand_v = float(cand_v)
                    if cand_v < target_voltage:
                        voltage_penalty = 5.0  # strongly penalize insufficient voltage
                    elif cand_v > target_voltage * 2:
                        voltage_penalty = 0.2  # slight penalty for overkill
            except (KeyError, TypeError, ValueError):
                pass
        # Stress-based penalties (from RE simulation)
        stress_penalty = 0.0
        if stress:
            try:
                if category == "capacitor":
                    cand_elec = (
                        cand.get("capacitor", {})
                        .get("manufacturerInfo", {})
                        .get("datasheetInfo", {})
                        .get("electrical", {})
                    )
                    v_rated = cand_elec.get("ratedVoltage")
                    if (
                        v_rated
                        and stress.v_peak
                        and float(v_rated) < stress.v_peak * VOLTAGE_DERATING_FACTOR
                    ):
                        stress_penalty += 5.0
                    i_ripple = cand_elec.get("rippleCurrent")
                    if i_ripple and stress.i_rms and float(i_ripple) < stress.i_rms:
                        stress_penalty += 3.0
                elif category == "magnetic":
                    cand_elec = _magnetic_elec(
                        cand.get("magnetic", {})
                        .get("manufacturerInfo", {})
                        .get("datasheetInfo", {})
                        .get("electrical")
                    )
                    isat = _effective_saturation_current(cand_elec)
                    i_rated = cand_elec.get("ratedCurrent")
                    if isat and stress.i_peak and float(isat) < stress.i_peak:
                        stress_penalty += 5.0
                    if i_rated and stress.i_rms and float(i_rated) < stress.i_rms:
                        stress_penalty += 3.0
                elif category == "mosfet":
                    cand_elec = (
                        cand.get("semiconductor", {})
                        .get("mosfet", {})
                        .get("manufacturerInfo", {})
                        .get("datasheetInfo", {})
                        .get("electrical", {})
                    )
                    vds = cand_elec.get("drainSourceVoltage")
                    if (
                        vds
                        and stress.v_peak
                        and float(vds) < stress.v_peak * VOLTAGE_DERATING_FACTOR
                    ):
                        stress_penalty += 5.0
                    id_cont = cand_elec.get("continuousDrainCurrent")
                    if (
                        id_cont
                        and stress.i_peak
                        and float(id_cont) < stress.i_peak * CURRENT_DERATING_FACTOR
                    ):
                        stress_penalty += 3.0
                elif category == "diode":
                    cand_elec = (
                        cand.get("semiconductor", {})
                        .get("diode", {})
                        .get("manufacturerInfo", {})
                        .get("datasheetInfo", {})
                        .get("electrical", {})
                    )
                    vrrm = cand_elec.get("reverseVoltage")
                    if (
                        vrrm
                        and stress.v_peak
                        and float(vrrm) < stress.v_peak * DIODE_VOLTAGE_DERATING
                    ):
                        stress_penalty += 5.0
                    if_avg = cand_elec.get("forwardCurrent")
                    if if_avg and stress.i_avg and float(if_avg) < stress.i_avg:
                        stress_penalty += 3.0
                elif category == "varistor":
                    cand_elec = (
                        cand.get("varistor", {})
                        .get("manufacturerInfo", {})
                        .get("datasheetInfo", {})
                        .get("electrical", {})
                    )
                    i_surge = cand_elec.get("peakSurgeCurrent")
                    if i_surge and stress.i_peak and float(i_surge) < stress.i_peak:
                        stress_penalty += 5.0
                    cv = cand_elec.get("clampingVoltage")
                    if cv and stress.v_peak and float(cv) < stress.v_peak:
                        stress_penalty += 3.0
                elif category == "chipBead":
                    cand_elec = _magnetic_elec(
                        cand.get("magnetic", {})
                        .get("manufacturerInfo", {})
                        .get("datasheetInfo", {})
                        .get("electrical")
                    )
                    rc = cand_elec.get("ratedCurrents")
                    rated_i = rc[0] if isinstance(rc, list) and rc else None
                    if rated_i and stress.i_rms and float(rated_i) < stress.i_rms:
                        stress_penalty += 3.0
            except (KeyError, TypeError, ValueError):
                pass

        # Technology penalty: when the original capacitor's chemistry
        # family is known, push different-family candidates down so the
        # top results stay in-kind (ceramic original -> ceramic subs, not
        # supercaps / electrolytics). Same-family and unreadable-family
        # candidates are not penalised.
        tech_penalty = 0.0
        if source_cap_family and category == "capacitor":
            cand_tech = (
                cand.get("capacitor", {})
                .get("manufacturerInfo", {})
                .get("datasheetInfo", {})
                .get("part", {})
                .get("technology")
            )
            cand_fam = _capacitor_technology_family(cand_tech)
            if cand_fam is not None and cand_fam != source_cap_family:
                # Both conductive-polymer (tantalum-polymer ↔ aluminum-polymer) is
                # an acceptable cross-chemistry for low-ESR BULK caps — a small
                # penalty (a verify-ESR/ripple partial), not the full family gate
                # that keeps ceramic↔tantalum↔wet-electrolytic apart.
                src_tech = str(comp.get("technology") or comp.get("dielectric") or "")
                tech_penalty = (
                    1.5 if (_is_polymer_cap(src_tech) and _is_polymer_cap(cand_tech)) else 6.0
                )

        # Footprint fit (all categories): the substitute must occupy no more
        # board space than the original. Smaller is better; oversize is heavily
        # penalised but still finite-scored so it can win when nothing fits.
        footprint_penalty = _footprint_penalty(source_dims, _extract_dimensions(cand, category))
        # A candidate that overflows the original's board space (footprint
        # penalty hit the oversize floor) is a LAST RESORT — see filter below.
        is_oversize = footprint_penalty >= _OVERSIZE_BASE

        score = (
            val_dist
            + pkg_penalty
            + voltage_penalty
            + stress_penalty
            + tech_penalty
            + footprint_penalty
        )
        scored.append((score, cand, is_oversize))

    scored.sort(key=lambda x: x[0])
    # Larger-package parts are considered ONLY when no fitting part was found:
    # if any candidate fits the original's board space, drop the oversize ones
    # entirely so they neither pre-empt a real drop-in nor churn the downstream
    # LLM/Otto/scoring stages. When NOTHING fits, keep the oversize candidates
    # (they're the only option — surfaced later as a `partial` with a footprint
    # caveat, not silently dropped).
    fitting = [(s, c) for s, c, oversize in scored if not oversize]
    ranked = fitting if fitting else [(s, c) for s, c, _ in scored]
    return [c for _, c in ranked[:max_results]]


_PKG_ORDER = ["0201", "0402", "0603", "0805", "1206", "1210", "1812", "2010", "2220"]


def _is_one_size_up(original: str, candidate: str) -> bool:
    """Check if candidate package is exactly one standard size up."""
    if not original or not candidate:
        return False
    o_idx = next((i for i, p in enumerate(_PKG_ORDER) if p in original), -1)
    c_idx = next((i for i, p in enumerate(_PKG_ORDER) if p in candidate), -1)
    return o_idx >= 0 and c_idx == o_idx + 1


# ---------------------------------------------------------------------------
# Stage 2: Pre-classify (deterministic)
# ---------------------------------------------------------------------------


def _stage2_preclassify(state: CrossRefState) -> CrossRefState:
    """Pre-classify components that don't need crossref:
    - Already from the target manufacturer → keep_original
    - Empty value / NC / not fitted → keep_original (not fitted)
    """
    _NC_VALUES = {"", "nc", "n/c", "dnp", "not fitted", "open", "none"}

    target_norm = _normalize_manufacturer(state.target_manufacturer)
    for comp in state.source_bom:
        ref = comp.get("ref_des", comp.get("name", "?"))

        # Not-fitted components: only when value key is present but empty/NC
        has_value_key = "value" in comp
        has_mpn_key = "original_mpn" in comp or "part" in comp
        value = str(comp.get("value", "")).strip().lower()
        mpn = str(comp.get("original_mpn", comp.get("part", ""))).strip().lower()
        if has_value_key and value in _NC_VALUES and (not has_mpn_key or mpn in _NC_VALUES):
            state.preclassified[ref] = {
                "status": "keep_original",
                "reason": "not fitted (empty value)",
            }
            continue

        # Already target manufacturer
        mfr = comp.get("manufacturer", "")
        mfr_norm = _normalize_manufacturer(mfr) if isinstance(mfr, str) else ""
        if mfr_norm and (target_norm in mfr_norm or mfr_norm in target_norm):
            state.preclassified[ref] = {
                "status": "keep_original",
                "reason": f"already {state.target_manufacturer}",
            }

    logger.info(
        "CR stage 2: %d components pre-classified as keep_original", len(state.preclassified)
    )
    return state


# ---------------------------------------------------------------------------
# Stage 3: LLM cross-reference
# ---------------------------------------------------------------------------


def _merge_preclassified(state: CrossRefState) -> None:
    """Append the pre-classified (keep_original) rows to ``crossref_result`` so
    they appear in the final output. Idempotent — skips refs already present (the
    correction loop re-runs stage 3, and the empty-BOM path also calls this)."""
    present = {r.get("ref_des") for r in state.crossref_result}
    for ref, info in state.preclassified.items():
        if ref in present:
            continue
        comp = next((c for c in state.source_bom if c.get("ref_des", c.get("name")) == ref), None)
        if comp:
            state.crossref_result.append(
                {
                    "ref_des": ref,
                    "component_type": comp.get("component_type", ""),
                    "original_pn": comp.get(
                        "original_mpn", comp.get("mpn", comp.get("original_pn", ""))
                    ),
                    "original_value": comp.get("value", ""),
                    "original_voltage": comp.get("voltage", comp.get("rated_voltage", "")),
                    "original_package": comp.get("package", ""),
                    "substitute_pn": comp.get("original_mpn", comp.get("mpn", "")),
                    "substitute_value": comp.get("value", ""),
                    "substitute_voltage": comp.get("voltage", ""),
                    "substitute_package": comp.get("package", ""),
                    "status": "keep_original",
                    "notes": info["reason"],
                }
            )


# Cross-referencer batching. The binding constraint is the RESPONSE, not the
# request: the model context is 262144 tokens (so a big INPUT batch fits fine),
# but the reply is capped at 16k tokens (see _run_crossref_batches). Each output
# row (original+substitute mpn/value/voltage/package + a match_detail rationale +
# notes) runs ~150-250 tokens, so a batch of ~120 parts OVERFLOWS 16k and the
# model silently truncates its JSON array — dropping the overflow rows with no
# error (this caused an observed 386/2101 loss). Size the part cap so the worst-
# case response fits the reply budget with margin: 60 parts × ~250 tok ≈ 15k <
# 16k. _reconcile_crossref_coverage is the backstop that retries (and ultimately
# raises on) any rows that still don't come back. The char cap stays a request-
# side safety net.
_CROSSREF_BATCH_MAX_PARTS = 60  # sized to the 16k REPLY budget, not the request
_CROSSREF_RETRY_MAX_PARTS = 20  # retry dropped rows in tiny batches that can't truncate
_CROSSREF_BATCH_CHARS = 400_000  # request-side safety net: ~175k tokens, under limit
_CROSSREF_MAX_CONCURRENCY = 12  # batches run in parallel (I/O-bound, cost-neutral)
# Review (Ray/Nicola) batching: the rows are TRIMMED (small), so larger batches
# are fine; the reviewer verdict is aggregated across batches. Keep well under
# the 262k limit incl. the per-batch guardrail fires + reviewer system prompt.
_REVIEW_BATCH_MAX_PARTS = 200
_REVIEW_BATCH_CHARS = 200_000


def _batch_for_llm(
    entries: list[dict[str, Any]],
    max_chars: int = _CROSSREF_BATCH_CHARS,
    max_parts: int = _CROSSREF_BATCH_MAX_PARTS,
) -> list[list[dict[str, Any]]]:
    """Split BOM entries into batches capped by BOTH a component count and a
    serialized-size budget (whichever is hit first), so no single LLM request
    exceeds the model token limit and each call stays small/fast. A single entry
    larger than the char budget still goes out alone (better an oversized request
    that may fail than dropping the component silently)."""
    batches: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    cur_len = 0
    for e in entries:
        elen = len(json.dumps(e))
        if cur and (len(cur) >= max_parts or cur_len + elen > max_chars):
            batches.append(cur)
            cur, cur_len = [], 0
        cur.append(e)
        cur_len += elen
    if cur:
        batches.append(cur)
    return batches


def _crossref_llm_batched(
    items: list[dict[str, Any]],
    build_payload: Any,
    *,
    max_tokens: int = 8192,
    max_retries: int = 1,
    concurrent: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Send a per-component ``items`` list to the cross-referencer in token-safe
    batches and collect the returned crossref rows. ``build_payload(batch)`` -> a
    dict (the user message for that batch). Returns ``(rows, errors)`` — rows are
    the concatenated ``crossref``/``components`` from every successful batch;
    errors lists per-batch failures. Used by every stage that re-sends scaling
    per-component data to the LLM (retry, Otto re-crossref, correction) so none
    can blow the 262k token limit."""
    batches = _batch_for_llm(items)

    def _run(batch: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None]:
        try:
            data = call_agent_json(
                "cross-referencer",
                json.dumps(build_payload(batch), indent=2),
                max_tokens=max_tokens,
                max_retries=max_retries,
            )
            r = data.get("crossref", data.get("components", []))
            return (r if isinstance(r, list) else []), None
        except LLMCallError as exc:
            return [], str(exc)

    import time

    t0 = time.monotonic()
    if concurrent and len(batches) > 1:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(_CROSSREF_MAX_CONCURRENCY, len(batches))) as pool:
            outcomes = list(pool.map(_run, batches))
    else:
        outcomes = [_run(b) for b in batches]

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for bi, (r, err) in enumerate(outcomes, 1):
        rows.extend(r)
        if err:
            errors.append(f"batch {bi}/{len(batches)}: {err}")
    logger.info(
        "cross-referencer: %d item(s) in %d batch(es) %s -> %d rows, %d error(s) in %.0fs",
        len(items),
        len(batches),
        "concurrent" if (concurrent and len(batches) > 1) else "sequential",
        len(rows),
        len(errors),
        time.monotonic() - t0,
    )
    return rows, errors


def _build_bom_for_llm(state: CrossRefState) -> list[dict[str, Any]]:
    """Assemble the per-component entries (with candidate summaries, source
    dimensions and sim stress) that get sent to the cross-referencer. Pulled out
    of :func:`_stage3_crossref` so the batching can be exercised in tests against
    a real BOM without invoking the LLM."""
    bom_for_llm: list[dict[str, Any]] = []
    for comp in state.source_bom:
        ref = comp.get("ref_des", comp.get("name", "?"))
        if ref in state.preclassified:
            continue
        entry = dict(comp)
        cat = comp.get("component_type", "")
        source_dims = comp.get("_source_dims_m")
        src_mm = _source_dims_mm(source_dims)
        if src_mm:
            entry["_source_dimensions_mm"] = src_mm
        candidates = state.candidates_by_ref.get(ref, [])
        if candidates:
            entry["_tas_candidates"] = _candidate_summaries_for_llm(
                candidates, cat, source_dims, limit=10
            )
        # Connector/analog rows: the BOM carries no value/voltage that could
        # describe the part, so hand the LLM the ORIGINAL's catalogue summary
        # (family, positions, pitch, gender / function, channels, GBW, …) to
        # compare candidates against — real data, not an LLM recollection.
        src_env = comp.get("_source_env")
        if cat in ("connector", "analog") and isinstance(src_env, dict):
            orig_specs = _summarize_candidate(src_env, cat)
            if orig_specs:
                entry["_original_specs"] = orig_specs
        entry.pop("_source_dims_m", None)  # internal-only: don't leak to the LLM
        entry.pop("_source_env", None)  # raw catalogue envelope: internal-only
        # Inject simulation stress data into the LLM prompt
        stress = state.stress_by_ref.get(ref)
        if stress:
            stress_info: dict[str, Any] = {}
            if stress.v_peak is not None:
                stress_info["V_peak"] = f"{stress.v_peak:.1f}V"
                stress_info["V_rated_min"] = f"{stress.v_peak * VOLTAGE_DERATING_FACTOR:.1f}V"
            if stress.i_peak is not None:
                stress_info["I_peak"] = f"{stress.i_peak:.2f}A"
            if stress.i_rms is not None:
                stress_info["I_rms"] = f"{stress.i_rms:.2f}A"
            if stress.i_avg is not None:
                stress_info["I_avg"] = f"{stress.i_avg:.2f}A"
            if stress_info:
                entry["_sim_stress"] = stress_info

        bom_for_llm.append(entry)
    return bom_for_llm


def _restore_component_types(state: CrossRefState, bom_for_llm: list[dict[str, Any]]) -> None:
    """The LLM must not relabel parts: component_type in its output is an echo.

    Restore the engine-derived category (type column / description keywords /
    catalogue lookup) on every returned row — an LLM guessing "connector" from
    a numeric MPN would otherwise stick to the row through every downstream
    check and the report.
    """
    types_by_ref = {
        c["ref_des"]: c["component_type"]
        for c in bom_for_llm
        if c.get("ref_des") and c.get("component_type")
    }
    for row in state.crossref_result:
        known = types_by_ref.get(row.get("ref_des"))
        if known and row.get("component_type") != known:
            row["component_type"] = known


def _stage3_crossref(state: CrossRefState) -> CrossRefState:
    """Call the cross-referencer agent with constrained candidates."""
    bom_for_llm = _build_bom_for_llm(state)

    if not bom_for_llm:
        # Every component was pre-classified (already target-mfr / not-fitted).
        # There's nothing for the LLM, but the kept-original rows MUST still
        # reach the output — otherwise a BOM that needs no substitutions comes
        # back with 0 components and looks like a failure.
        state.diagnostics.append("all components pre-classified, nothing to crossref")
        _merge_preclassified(state)
        return state

    results, _failed = _run_crossref_batches(state, bom_for_llm)
    state.crossref_result = results

    # Merge pre-classified rows back in
    _merge_preclassified(state)

    # The cross-referencer must return one row per component it was handed. It can
    # silently OMIT rows when its JSON reply hits the output-token cap (truncated
    # array) — trusting the returned count then drops components with no error.
    # Detect any missing refs, retry them in tiny batches, and raise if they still
    # don't come back (no silent data loss).
    _reconcile_crossref_coverage(state, bom_for_llm)

    _restore_component_types(state, bom_for_llm)

    logger.info("CR stage 3: crossref produced %d rows", len(state.crossref_result))
    return state


def _ref_of(entry: dict[str, Any]) -> Any:
    """The unique ref designator a BOM entry / crossref row is keyed on."""
    return entry.get("ref_des", entry.get("name", "?"))


def _run_crossref_batches(
    state: CrossRefState,
    entries: list[dict[str, Any]],
    *,
    max_parts: int = _CROSSREF_BATCH_MAX_PARTS,
) -> tuple[list[dict[str, Any]], int]:
    """Cross-reference ``entries`` via the LLM, batched and run concurrently.

    Returns ``(rows, failed_batch_count)``. ``rows`` is the raw concatenation of
    each batch's output — it is NOT guaranteed to be one row per input (the model
    can omit rows on output truncation), so callers MUST reconcile against the
    input via :func:`_reconcile_crossref_coverage`. Batching keeps each request
    under the model context window AND each reply under the output-token cap; one
    failed batch only loses its own rows, surfaced as a diagnostic.
    """
    batches = _batch_for_llm(entries, max_parts=max_parts)
    if len(batches) > 1:
        logger.info(
            "CR stage 3: %d components split into %d batches (≤%d each) run %d-way concurrently",
            len(entries),
            len(batches),
            max_parts,
            _CROSSREF_MAX_CONCURRENCY,
        )

    def _run_batch(batch: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None]:
        user_msg = json.dumps(
            {
                "source_bom": batch,
                "target_manufacturer": state.target_manufacturer,
                "circuit_context": state.circuit_context,
            },
            indent=2,
        )
        try:
            data = call_agent_json("cross-referencer", user_msg, max_tokens=16384, max_retries=2)
            return data.get("crossref", []), None
        except LLMCallError as exc:
            return [], str(exc)

    # Batches are independent network-bound calls — run them concurrently so a
    # large BOM finishes in roughly one batch's time rather than the sum. Order
    # is preserved for a stable report.
    results: list[dict[str, Any]] = []
    failed_batches = 0
    if len(batches) == 1:
        rows, err = _run_batch(batches[0])
        results.extend(rows)
        if err:
            failed_batches = 1
            state.diagnostics.append(f"cross-referencer failed after retries: {err}")
    else:
        from concurrent.futures import ThreadPoolExecutor

        workers = min(_CROSSREF_MAX_CONCURRENCY, len(batches))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = list(pool.map(_run_batch, batches))
        for bi, (rows, err) in enumerate(outcomes, 1):
            results.extend(rows)
            if err:
                failed_batches += 1
                state.diagnostics.append(
                    f"cross-referencer batch {bi}/{len(batches)} failed after retries: {err}"
                )
    if batches and failed_batches == len(batches):
        # Nothing came back at all — surface it as a single clear diagnostic.
        state.diagnostics.append("cross-referencer produced no results (all batches failed)")
    return results, failed_batches


def _reconcile_crossref_coverage(state: CrossRefState, bom_for_llm: list[dict[str, Any]]) -> None:
    """Guarantee every component sent to the cross-referencer comes back.

    The LLM can silently omit rows when its reply hits the output-token cap, so
    the raw result may have fewer rows than the input. Find the missing refs,
    retry them in tiny batches (which can't truncate), append whatever returns,
    and raise :class:`CrossRefPipelineError` if any are STILL missing — a
    cross-reference with dropped components is not a valid result (no silent
    fallback, per the fail-loud policy)."""
    expected = {_ref_of(e): e for e in bom_for_llm}
    present = {r.get("ref_des") for r in state.crossref_result}
    missing = [e for ref, e in expected.items() if ref not in present]
    if not missing:
        return

    logger.warning(
        "CR stage 3: cross-referencer omitted %d/%d component row(s) "
        "(likely output truncation) — retrying in small batches",
        len(missing),
        len(bom_for_llm),
    )
    state.diagnostics.append(
        f"cross-referencer omitted {len(missing)} row(s); retried dropped components"
    )
    retry_rows, _ = _run_crossref_batches(state, missing, max_parts=_CROSSREF_RETRY_MAX_PARTS)
    retry_by_ref = {r.get("ref_des"): r for r in retry_rows}
    for entry in missing:
        ref = _ref_of(entry)
        row = retry_by_ref.get(ref)
        if row is not None and ref not in present:
            state.crossref_result.append(row)
            present.add(ref)

    still_missing = [ref for ref in expected if ref not in present]
    if still_missing:
        sample = ", ".join(str(r) for r in still_missing[:10])
        more = f" (+{len(still_missing) - 10} more)" if len(still_missing) > 10 else ""
        raise CrossRefPipelineError(
            f"CR stage 3: cross-referencer dropped {len(still_missing)} component(s) "
            f"that never came back even after a small-batch retry: {sample}{more}. "
            f"A cross-reference missing components is not a valid result — aborting "
            f"(no silent data loss)."
        )


def _summarize_candidate(env: dict[str, Any], category: str) -> dict[str, Any]:
    """Extract a brief summary from a TAS envelope for the LLM."""
    summary: dict[str, Any] = {}
    if category == "mosfet":
        try:
            m = env["semiconductor"]["mosfet"]
            mi = m["manufacturerInfo"]
            elec = mi["datasheetInfo"]["electrical"]
            gth = elec.get("gateThresholdVoltage")
            vgs_th_max = gth.get("maximum") if isinstance(gth, dict) else gth
            summary = {
                "mpn": mi.get("reference", "?"),
                "vds": elec.get("drainSourceVoltage"),
                "rds_on": elec.get("onResistance"),
                "id": elec.get("continuousDrainCurrent"),
                "qg": elec.get("totalGateCharge"),
                "coss": elec.get("outputCapacitance"),
                "vgs_threshold_max": vgs_th_max,
                "rth_jc": elec.get("thermalResistanceJunctionCase"),
                "package": mi.get("datasheetInfo", {}).get("part", {}).get("case", ""),
            }
        except (KeyError, TypeError):
            pass
    elif category == "diode":
        try:
            d = env["semiconductor"]["diode"]
            mi = d["manufacturerInfo"]
            elec = mi["datasheetInfo"]["electrical"]
            summary = {
                "mpn": mi.get("reference", "?"),
                "vrrm": elec.get("reverseVoltage"),
                "vf": elec.get("forwardVoltage"),
                "if_avg": elec.get("forwardCurrent"),
                "qrr": elec.get("reverseRecoveryCharge"),
                "trr": elec.get("reverseRecoveryTime"),
                "package": mi.get("datasheetInfo", {}).get("part", {}).get("case", ""),
            }
        except (KeyError, TypeError):
            pass
    elif category == "capacitor":
        try:
            c = env["capacitor"]
            mi = c["manufacturerInfo"]
            elec = mi["datasheetInfo"]["electrical"]
            cap = elec.get("capacitance")
            cap_val = cap.get("nominal") if isinstance(cap, dict) else cap
            part = mi.get("datasheetInfo", {}).get("part", {})
            summary = {
                "mpn": mi.get("reference") or part.get("partNumber", "?"),
                "capacitance": cap_val,
                "voltage": elec.get("ratedVoltage"),
                "technology": part.get("technology"),
                "esr": elec.get("esr"),
                "ripple_current": elec.get("rippleCurrent"),
                # MLCC DC-bias model anchors (nullable for non-MLCC) — used to
                # compare effective capacitance at the operating voltage.
                "capacitance_saturation_mlcc": elec.get("capacitanceSaturationMLCC"),
                "vth_mlcc": elec.get("vthMLCC"),
                "package": part.get("case", ""),
            }
        except (KeyError, TypeError):
            pass
    elif category == "resistor":
        try:
            r = env["resistor"]
            mi = r["manufacturerInfo"]
            elec = mi["datasheetInfo"]["electrical"]
            res = elec.get("resistance")
            res_val = res.get("nominal") if isinstance(res, dict) else res
            summary = {
                "mpn": mi.get("reference", "?"),
                "resistance": res_val,
                "tolerance": elec.get("tolerance"),
                "power_rating": elec.get("powerRating"),
                "tcr": elec.get("temperatureCoefficient"),
                "package": mi.get("datasheetInfo", {}).get("part", {}).get("case", ""),
            }
        except (KeyError, TypeError):
            pass
    elif category == "magnetic":
        try:
            m = env["magnetic"]
            mi = m["manufacturerInfo"]
            elec = _magnetic_elec(mi["datasheetInfo"]["electrical"])
            ind = elec.get("inductance")
            ind_val = ind.get("nominal") if isinstance(ind, dict) else ind
            # Field is saturationCurrentPeak (not saturationCurrent); reading the
            # wrong name made every candidate show null Isat to the reviewers.
            # Use the shared effective-Isat helper (ABT #6 rated-current fallback)
            # and tell the reviewer which basis the number came from.
            real_isat = elec.get("saturationCurrentPeak")
            isat = _effective_saturation_current(elec)
            dcr = elec.get("dcResistance")
            dcr_val = (dcr.get("typical") or dcr.get("maximum")) if isinstance(dcr, dict) else dcr
            rc = elec.get("ratedCurrents")
            rated_current = rc[0] if isinstance(rc, list) and rc else None
            summary = {
                "mpn": mi.get("reference", "?"),
                "inductance": ind_val,
                "saturation_current": isat,
                "saturation_current_basis": (
                    "datasheet"
                    if real_isat is not None
                    else "rated_current_fallback"
                    if isat is not None
                    else "unavailable"
                ),
                "rated_current": rated_current,
                "dcr": dcr_val,
                "srf": elec.get("selfResonantFrequency"),
                "package": mi.get("datasheetInfo", {}).get("part", {}).get("case", ""),
            }
        except (KeyError, TypeError):
            pass
    elif category == "chipBead":
        try:
            m = env["magnetic"]
            mi = m["manufacturerInfo"]
            elec = (mi["datasheetInfo"]["electrical"] or [{}])[0]
            dcr = elec.get("dcResistance")
            dcr_val = dcr.get("nominal") if isinstance(dcr, dict) else dcr
            rated = elec.get("ratedCurrents")
            rated_val = rated[0] if isinstance(rated, list) and rated else rated
            summary = {
                "mpn": mi.get("reference", "?"),
                "impedance_100mhz": _chip_bead_impedance_at_100mhz(env),
                "srf": elec.get("selfResonantFrequency"),
                "dcr": dcr_val,
                "rated_current": rated_val,
                "package": mi.get("datasheetInfo", {}).get("part", {}).get("case", ""),
            }
        except (KeyError, TypeError):
            pass
    elif category == "varistor":
        try:
            v = env["varistor"]
            mi = v["manufacturerInfo"]
            elec = mi["datasheetInfo"]["electrical"]
            vv = elec.get("varistorVoltage")
            vv_nom = vv.get("nominal") if isinstance(vv, dict) else vv
            summary = {
                "mpn": mi.get("reference", "?"),
                "varistor_voltage": vv_nom,
                "clamping_voltage": elec.get("clampingVoltage"),
                "peak_surge_current": elec.get("peakSurgeCurrent"),
                "energy_absorption": elec.get("energyAbsorption"),
                "surge_waveform": elec.get("surgeWaveform"),
                "max_ac_voltage": elec.get("maxContinuousAcVoltage"),
            }
        except (KeyError, TypeError):
            pass
    elif category == "connector":
        try:
            mi = env["connector"]["manufacturerInfo"]
            elec = mi["datasheetInfo"]["electrical"]
            # Same attribute extractor as the ranker (incl. the description-
            # text backfill for sparse pitch/gender/mounting) so the ranking
            # gates and the param-check verdicts never disagree.
            attrs = _connector_attrs(env)
            cr = elec.get("contactResistance")
            cr_val = (cr.get("maximum") or cr.get("nominal")) if isinstance(cr, dict) else cr
            summary = {
                "mpn": mi.get("reference", "?"),
                "family": attrs.get("family"),
                "series": attrs.get("series"),
                "interface_standard": attrs.get("interface_standard"),
                "positions": attrs.get("positions"),
                "pitch_mm": round(attrs["pitch"] * 1e3, 3) if attrs.get("pitch") else None,
                "rated_current_A": attrs.get("current"),
                "rated_voltage_V": attrs.get("voltage"),
                "mounting": attrs.get("mounting"),
                "polarity": attrs.get("polarity"),
                "temp_min_C": attrs.get("temp_min"),
                "temp_max_C": attrs.get("temp_max"),
                "mating_cycles": attrs.get("cycles"),
                "contact_resistance": cr_val,
            }
        except (KeyError, TypeError):
            pass
    elif category == "analog":
        try:
            subtype, record = _analog_subtype_block(env.get("analog"))
            if record is None:
                raise KeyError("analog")
            mi = record["manufacturerInfo"]
            ds = mi.get("datasheetInfo") or {}
            elec = ds.get("electrical") or {}
            supply = elec.get("supply") or {}

            def _yn(v: Any) -> str | None:
                # bools become "yes"/"no" strings so the class comparator can
                # rank them (False would normalise to "" and vanish).
                if isinstance(v, bool):
                    return "yes" if v else "no"
                return None

            summary = {
                "mpn": mi.get("reference", "?"),
                "subtype": subtype,
                "channels": elec.get("numberOfChannels"),
                "supply_min_V": supply.get("minimumSupplyVoltage"),
                "supply_max_V": supply.get("maximumSupplyVoltage"),
                "gbw": elec.get("gainBandwidthProduct"),
                "slew_rate": elec.get("slewRate"),
                "input_offset_voltage": elec.get("inputOffsetVoltage"),
                "offset_drift": elec.get("inputOffsetVoltageDrift"),
                "input_bias_current": elec.get("inputBiasCurrent"),
                "cmrr_db": elec.get("commonModeRejectionRatio"),
                "quiescent_current": supply.get("quiescentCurrentPerChannel"),
                "rail_to_rail_input": _yn(elec.get("railToRailInput")),
                "rail_to_rail_output": _yn(elec.get("railToRailOutput")),
                "propagation_delay": elec.get("propagationDelay"),
                "output_stage": elec.get("outputStage"),
                "resolution": elec.get("resolution"),
                "sample_rate": elec.get("sampleRate"),
                "package": (ds.get("part") or {}).get("package", ""),
            }
        except (KeyError, TypeError):
            pass
    elif category == "timeBase":
        try:
            subtype, record = _analog_subtype_block(env.get("timeBase"))
            if record is None:
                raise KeyError("timeBase")
            mi = record["manufacturerInfo"]
            attrs = _timebase_attrs(env)

            def _ppm(v: Any) -> float | None:
                # TBAS stores fractional ratios (2e-05); engineers read ppm.
                return round(v * 1e6, 3) if isinstance(v, (int, float)) else None

            ds = (mi.get("datasheetInfo") or {})
            elec = ds.get("electrical") or {}
            supply = elec.get("supply") or {}
            summary = {
                "mpn": mi.get("reference", "?"),
                "subtype": subtype,
                "technology": attrs.get("technology"),
                "frequency": attrs.get("frequency"),
                "mode": attrs.get("mode"),
                "output_type": attrs.get("output_type"),
                "tolerance_ppm": _ppm(attrs.get("tolerance")),
                "stability_ppm": _ppm(attrs.get("stability")),
                "aging_ppm_y": _ppm(elec.get("agingPerYear")),
                "load_capacitance_pF": (
                    round(attrs["load_capacitance"] * 1e12, 3)
                    if attrs.get("load_capacitance") is not None
                    else None
                ),
                "esr": attrs.get("esr"),
                "supply_min_V": attrs.get("supply_min"),
                "supply_max_V": attrs.get("supply_max"),
                "current_consumption": supply.get("currentConsumption"),
                "temp_min_C": attrs.get("temp_min"),
                "temp_max_C": attrs.get("temp_max"),
                "package": attrs.get("package", ""),
            }
        except (KeyError, TypeError):
            pass
    # Physical body size for every category, so the LLM (and the reader) can
    # see whether a substitute fits the original's board space.
    if summary:
        dims = _extract_dimensions(env, category)
        if dims and dims[0] and dims[1]:
            summary["dimensions_mm"] = {
                "length": round(dims[0] * 1e3, 2),
                "width": round(dims[1] * 1e3, 2),
                **({"height": round(dims[2] * 1e3, 2)} if dims[2] else {}),
            }
    return summary


def _source_dims_mm(
    source_dims: tuple[float, float, float | None] | None,
) -> dict[str, float] | None:
    """Render resolved source dimensions (metres) as an mm dict for the LLM."""
    if source_dims and source_dims[0] and source_dims[1]:
        out = {
            "length": round(source_dims[0] * 1e3, 2),
            "width": round(source_dims[1] * 1e3, 2),
        }
        if source_dims[2]:
            out["height"] = round(source_dims[2] * 1e3, 2)
        return out
    return None


def _candidate_summaries_for_llm(
    candidates: list[dict[str, Any]],
    category: str,
    source_dims: tuple[float, float, float | None] | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Summarise candidates for the LLM, tagging each with an explicit
    ``fits_original`` verdict (True / False / "unknown") so the cross-referencer
    doesn't have to compare dimensions by hand. The substitute must occupy no
    more board space than the original."""
    out: list[dict[str, Any]] = []
    for c in candidates[:limit]:
        summ = _summarize_candidate(c, category)
        if source_dims:
            # 3-state: True (fits) / "one_size_larger" (partial, verify fit) /
            # False (≥2 sizes over) / "unknown".
            summ["fits_original"] = _footprint_tier(source_dims, _extract_dimensions(c, category))
        out.append(summ)
    return out


# ---------------------------------------------------------------------------
# Stage 4: Guardrails (deterministic)
# ---------------------------------------------------------------------------


def _stage4_guardrails(state: CrossRefState) -> CrossRefState:
    """Apply engineering guardrails to the crossref result.

    Fail loud: a crash inside the guardrail stage must NOT ship an unguarded
    crossref with only a diagnostics string — that silently disables the
    anti-hallucination (G5) and physics gates. The only tolerated skip is the
    guardrails module being genuinely absent.
    """
    try:
        from heaviside.pipeline.guardrails import apply_guardrails
    except ImportError:
        state.diagnostics.append("guardrails module not available — skipping")
        return state

    try:
        corrected, fire_log = apply_guardrails(
            {"crossref": state.crossref_result},
            state.source_bom,
            state.target_manufacturer,
            stress_by_ref=state.stress_by_ref or None,
        )
    except Exception as exc:
        raise CrossRefPipelineError(
            f"CR stage 4: the guardrail stage failed ({type(exc).__name__}: {exc}). A "
            f"crossref shipped without its anti-hallucination/physics guardrails is not a "
            f"valid result — aborting (no silent fallback)."
        ) from exc

    state.crossref_result = corrected.get("crossref", state.crossref_result)
    state.guardrail_log.extend(fire_log)
    logger.info("CR stage 4: %d guardrail fires", len(fire_log))

    # Retry hallucinated MPNs caught by G5/G5b. fire_log is always bound here
    # (apply_guardrails returned), so no UnboundLocalError on the error path.
    g5_fires = [f for f in fire_log if f.get("guardrail_id", "").startswith("5")]
    if g5_fires:
        state = _stage4b_retry_hallucinations(state, g5_fires)
    return state


def _stage4b_retry_hallucinations(
    state: CrossRefState,
    g5_fires: list[dict[str, Any]],
) -> CrossRefState:
    """Retry crossref for components where G5/G5b caught hallucinated MPNs.

    Instead of giving up, re-ask the LLM with explicit instructions to
    pick ONLY from the pre-ranked TAS candidates.
    """
    failed_refs = {f["ref_des"] for f in g5_fires}
    if not failed_refs:
        return state

    retry_bom = []
    for comp in state.source_bom:
        ref = comp.get("ref_des", comp.get("name", "?"))
        if ref not in failed_refs:
            continue
        entry = dict(comp)
        cat = comp.get("component_type", "")
        source_dims = comp.get("_source_dims_m")
        src_mm = _source_dims_mm(source_dims)
        if src_mm:
            entry["_source_dimensions_mm"] = src_mm
        candidates = state.candidates_by_ref.get(ref, [])
        if candidates:
            entry["_tas_candidates"] = _candidate_summaries_for_llm(
                candidates, cat, source_dims, limit=10
            )
        src_env = comp.get("_source_env")
        if cat in ("connector", "analog") and isinstance(src_env, dict):
            orig_specs = _summarize_candidate(src_env, cat)
            if orig_specs:
                entry["_original_specs"] = orig_specs
        entry.pop("_source_dims_m", None)  # internal-only: don't leak to the LLM
        entry.pop("_source_env", None)  # raw catalogue envelope: internal-only
        retry_bom.append(entry)

    if not retry_bom:
        return state

    retried, errors = _crossref_llm_batched(
        retry_bom,
        lambda batch: {
            "source_bom": batch,
            "target_manufacturer": state.target_manufacturer,
            "circuit_context": state.circuit_context,
            "IMPORTANT": (
                "Your previous response contained hallucinated MPNs (product "
                "family descriptions like 'WCAP-MLCC-4700nF-160V' instead of "
                "real catalogue MPNs). You MUST pick substitute MPNs ONLY from "
                "the _tas_candidates list provided for each component. If no "
                "candidate fits, set status to 'no_substitute'. Do NOT invent "
                "or construct MPN strings."
            ),
        },
        concurrent=True,
    )
    for e in errors:
        state.diagnostics.append(f"G5 retry {e}")
    logger.info(
        "CR stage 4b: retried %d G5-failed components, got %d results",
        len(failed_refs),
        len(retried),
    )

    # Merge retried results back into crossref_result
    retried_by_ref = {r.get("ref_des"): r for r in retried}
    for i, row in enumerate(state.crossref_result):
        ref = row.get("ref_des")
        if ref in retried_by_ref:
            retry_row = retried_by_ref[ref]
            pn = (retry_row.get("substitute_pn") or "").strip()
            status = retry_row.get("status", "no_substitute")
            if pn and pn != "no_substitute" and status in ("recommended", "partial"):
                state.crossref_result[i] = retry_row
                state.crossref_result[i]["notes"] = (
                    "(G5 retry: replaced hallucinated MPN) " + retry_row.get("notes", "")
                )
                logger.info("CR stage 4b: %s recovered → %s (%s)", ref, pn, status)

    return state


# ---------------------------------------------------------------------------
# Stage 5: Match scoring + sourcing (deterministic)
# ---------------------------------------------------------------------------


def _stage5_score(state: CrossRefState) -> CrossRefState:
    """Score matches and annotate sourcing data."""
    try:
        from heaviside.pipeline.match_score import annotate_match_scores

        annotate_match_scores(
            state.crossref_result,
            state.source_bom,
            stress_by_ref=state.stress_by_ref or None,
        )
    except ImportError:
        state.diagnostics.append("match_score module not available — skipping")
    except Exception as exc:
        state.diagnostics.append(f"match scoring failed: {exc}")
    return state


# ---------------------------------------------------------------------------
# Stage 6: Otto challenge (LLM, optional)
# ---------------------------------------------------------------------------


def _guess_category_from_product(product: dict[str, Any]) -> str | None:
    """Guess the TAS category from a Digi-Key product's family."""
    family = (
        product.get("Category", {}).get("Value", "")
        + " "
        + product.get("Family", {}).get("Value", "")
    ).lower()
    if "capacitor" in family:
        return "capacitor"
    elif "resistor" in family or "sense" in family:
        return "resistor"
    elif "inductor" in family or "choke" in family or "ferrite" in family:
        return "magnetic"
    elif "diode" in family or "rectifier" in family or "schottky" in family:
        return "diode"
    elif "mosfet" in family or "transistor" in family or "fet" in family:
        return "mosfet"
    return None


def _persist_digikey_product(product: dict[str, Any], hint_type: str = "") -> None:
    """Convert a Digi-Key product to TAS format and persist to TAS/data/.

    Best-effort — failures are logged but don't abort the pipeline.
    """
    try:
        from heaviside.librarian.fetcher.convert import (
            convert_digikey_to_tas_capacitor,
            convert_digikey_to_tas_diode,
            convert_digikey_to_tas_mosfet,
            convert_digikey_to_tas_resistor,
        )
        from heaviside.librarian.tas import DuplicateComponentError, add_component
    except ImportError:
        return

    cat = _guess_category_from_product(product)
    if not cat and hint_type:
        for keyword, c in [
            ("capacitor", "capacitor"),
            ("resistor", "resistor"),
            ("diode", "diode"),
            ("mosfet", "mosfet"),
            ("magnetic", "magnetic"),
            ("inductor", "magnetic"),
            ("ferrite", "magnetic"),
        ]:
            if keyword in hint_type.lower():
                cat = c
                break
    if not cat:
        logger.debug(
            "persist: cannot determine category for %s", product.get("ManufacturerPartNumber", "?")
        )
        return

    converters = {
        "capacitor": convert_digikey_to_tas_capacitor,
        "resistor": convert_digikey_to_tas_resistor,
        "diode": convert_digikey_to_tas_diode,
        "mosfet": convert_digikey_to_tas_mosfet,
    }
    convert_fn = converters.get(cat)
    if not convert_fn:
        return

    mpn = product.get("ManufacturerPartNumber", "?")
    try:
        envelope = convert_fn(product)
        # Map TAS category names to NDJSON file categories
        ndjson_cat = {
            "capacitor": "capacitors",
            "resistor": "resistors",
            "diode": "diodes",
            "mosfet": "mosfets",
            "magnetic": "magnetics",
        }.get(cat, cat)
        add_component(ndjson_cat, envelope)
        logger.info("persist: added %s to TAS/%s", mpn, ndjson_cat)
    except DuplicateComponentError:
        logger.debug("persist: %s already in TAS", mpn)
    except Exception as exc:
        logger.warning("persist: failed to add %s to TAS: %s", mpn, exc)


def _stage6_otto(state: CrossRefState) -> CrossRefState:
    """Run Otto to challenge no_substitute items (manufacturer-agnostic).

    Otto is a pushy field-sales engineer for the TARGET manufacturer — his
    diagnoses are valuable (why the search missed parts: too-narrow
    footprint/value filtering) but his MPNs are often hallucinated. We
    collect his diagnoses and feed them back to the cross-referencer as
    hints for a broader search, rather than using his MPNs directly. The
    target manufacturer is passed into his prompt so he challenges for
    whichever maker is the target (Würth, TI, Vishay, …).
    """
    no_subs = [row for row in state.crossref_result if row.get("status") == "no_substitute"]
    if not no_subs:
        return state

    # Trim to essential fields to keep payload small for the reasoning model
    trimmed = [
        {
            k: row[k]
            for k in (
                "ref_des",
                "component_type",
                "original_pn",
                "original_value",
                "original_package",
                "notes",
            )
            if k in row
        }
        for row in no_subs
    ]
    try:
        # Batch the no_substitute list so a large BOM (hundreds of no-subs)
        # doesn't blow the model context. Otto is best-effort: a failed batch
        # just yields no challenges for its items.
        challenges: list[dict[str, Any]] = []
        raws: list[str] = []
        for batch in _batch_for_llm(trimmed):
            otto_tokens = min(8192 + len(batch) * 256, 16384)
            try:
                raw = call_agent(
                    "otto",
                    json.dumps(
                        {
                            "target_manufacturer": state.target_manufacturer,
                            "no_substitute_items": batch,
                        },
                        indent=2,
                    ),
                    max_tokens=otto_tokens,
                )
                raws.append(raw)
                challenges.extend(extract_json_block(raw).get("challenges", []))
            except Exception as exc:
                state.diagnostics.append(f"otto challenge batch failed: {exc}")

        state.otto_log = {
            "raw_response": "\n---\n".join(raws),
            "challenges": challenges,
            "summary": {},
        }

        # Collect Otto's diagnoses as hints for the cross-referencer
        overturned = [c for c in challenges if c.get("verdict") == "OVERTURNED"]
        confirmed = [c for c in challenges if c.get("verdict") == "CONFIRMED"]

        state.otto_log["confirmed"] = [
            {"ref_des": c["ref_des"], "diagnosis": c.get("diagnosis", "")} for c in confirmed
        ]

        if not overturned:
            logger.info("CR stage 6: Otto confirmed all %d no_substitute items", len(no_subs))
            return state

        # Build hints from Otto's diagnoses and feed back to cross-referencer
        hints: list[dict[str, Any]] = []
        for ov in overturned:
            ref = ov.get("ref_des", "")
            comp = next((c for c in state.source_bom if c.get("ref_des") == ref), {})
            entry = {
                "ref_des": ref,
                "component_type": comp.get("component_type", ""),
                "original_pn": comp.get("original_mpn", comp.get("mpn", "")),
                "original_value": comp.get("value", ""),
                "original_package": comp.get("package", ""),
                "otto_diagnosis": ov.get("diagnosis", ""),
                "otto_suggestion": ov.get("counter_proposal", ""),
            }
            source_dims = comp.get("_source_dims_m")
            src_mm = _source_dims_mm(source_dims)
            if src_mm:
                entry["_source_dimensions_mm"] = src_mm
            candidates = state.candidates_by_ref.get(ref, [])
            if candidates:
                entry["_tas_candidates"] = _candidate_summaries_for_llm(
                    candidates, comp.get("component_type", ""), source_dims, limit=15
                )
            hints.append(entry)

        retried, errors = _crossref_llm_batched(
            hints,
            lambda batch: {
                "task": "OTTO CHALLENGE — re-search with broader criteria",
                "instructions": (
                    "Otto (Würth sales agent) challenged these no_substitute verdicts. "
                    "His diagnoses explain why the original search was too narrow. "
                    "Use his hints to broaden your search in _tas_candidates. "
                    "Do NOT use Otto's suggested part numbers — they may be hallucinated. "
                    "Only use real parts from _tas_candidates. "
                    "If no suitable candidate exists even with broader criteria, "
                    "keep status as no_substitute."
                ),
                "components_to_retry": batch,
                "target_manufacturer": state.target_manufacturer,
            },
            concurrent=True,
        )
        for e in errors:
            state.diagnostics.append(f"Otto re-crossref {e}")

        applied = 0
        for fix in retried:
            ref = fix.get("ref_des", "")
            new_pn = fix.get("substitute_pn", fix.get("wurth_pn"))
            new_status = fix.get("status", "")
            if not ref or not new_pn or new_status == "no_substitute":
                continue
            for row in state.crossref_result:
                if row.get("ref_des") == ref:
                    row["substitute_pn"] = new_pn
                    row["status"] = new_status if new_status else "recommended"
                    row["notes"] = f"Otto-prompted re-search: {fix.get('notes', '')}"
                    # Carry the substitute's own fields when the LLM supplied
                    # them, so param-check/footprint don't read the stale
                    # no_substitute placeholders. Never fabricate — only copy
                    # what the retry row actually provided.
                    for field_name in (
                        "substitute_value",
                        "substitute_voltage",
                        "substitute_package",
                    ):
                        if fix.get(field_name):
                            row[field_name] = fix[field_name]
                    applied += 1
                    break

        state.otto_log["re_crossref_applied"] = applied
        state.otto_log["re_crossref_total"] = len(overturned)
        logger.info(
            "CR stage 6: Otto challenged %d, re-crossref found %d new substitutes",
            len(overturned),
            applied,
        )
        # Re-gate the applied Otto substitutions — critically G5 (hallucination).
        # Stage 4's guardrails ran BEFORE this stage, so without re-running them
        # an invented-but-plausible Otto MPN would reach review/report unchecked
        # (G5 demotes any substitute MPN absent from the catalogue, and G5b
        # re-searches it). This covers the correction-loop Otto call too.
        if applied:
            state = _stage4_guardrails(state)
    except LLMCallError as exc:
        state.diagnostics.append(f"otto challenge skipped: {exc}")
    return state


# ---------------------------------------------------------------------------
# Stage 7: Review (LLM)
# ---------------------------------------------------------------------------


def _stage7_review(state: CrossRefState, *, max_attempts: int = 2) -> CrossRefState:
    """Run Ray (engineering) and Nicola (quality) reviews sequentially.

    Both must approve for the pipeline to pass. Ray reviews first
    (physics/derating), then Nicola (completeness/quality/process).
    """
    _REVIEW_KEYS = (
        "ref_des",
        "component_type",
        "original_pn",
        "substitute_pn",
        "status",
        "notes",
        "guardrail_fires",
    )
    trimmed_xref = [{k: row[k] for k in _REVIEW_KEYS if k in row} for row in state.crossref_result]
    # Batch the rows so a large BOM doesn't exceed the model context (the prod
    # 400 "exceeded model token limit" on Ray's review). Each batch is reviewed
    # independently and the verdicts are aggregated: a reviewer APPROVES overall
    # only if it approves EVERY batch; objections are merged. guardrail fires are
    # filtered to each batch's components.
    fires_by_ref: dict[str, list[Any]] = {}
    for f in state.guardrail_log or []:
        fires_by_ref.setdefault(f.get("ref_des"), []).append(f)
    batches = _batch_for_llm(
        trimmed_xref, max_chars=_REVIEW_BATCH_CHARS, max_parts=_REVIEW_BATCH_MAX_PARTS
    )
    if len(batches) > 1:
        logger.info(
            "CR stage 7: review split into %d batches (%d rows)", len(batches), len(trimmed_xref)
        )

    _SCOPE = (
        "[SCOPE: CROSS-REFERENCE — component substitution validity only "
        "(electrical/thermal equivalence, footprint, ratings of the proposed "
        "replacements vs the originals). Full converter design phases — control "
        "loop, gate drive, protection, EMI, PCB — are OUT OF SCOPE.]\n\n"
    )

    # Run both reviewers. Per CLAUDE.md "no silent fallbacks": a reviewer that
    # cannot produce a verdict (LLM unreachable/timeout/unparseable even after
    # retries) is a HARD failure — a cross-reference without its Ray+Nicola
    # review is not a valid result, so raise rather than appending a diagnostic.
    _batch_list = batches or [[]]

    def _review_one(reviewer_name: str, bi: int, batch: list[dict[str, Any]]):
        refs = {r.get("ref_des") for r in batch}
        review_input = {
            "crossref": batch,
            "target_manufacturer": state.target_manufacturer,
            "total_components": len(state.source_bom),
            "batch": f"{bi}/{len(_batch_list)}" if len(_batch_list) > 1 else "1/1",
            "guardrail_fires": [f for r in refs for f in fires_by_ref.get(r, [])],
        }
        review_tokens = min(8192 + len(batch) * 128, 16384)
        try:
            vd = call_agent_json(
                reviewer_name,
                f"{_SCOPE}CROSS-REFERENCE REVIEW\n\n{json.dumps(review_input, indent=2)}",
                max_tokens=review_tokens,
                max_retries=max_attempts,
                json_mode=True,
            )
            return normalize_reviewer_verdict(vd, reviewer_name), None
        except LLMCallError as exc:
            return None, exc

    for reviewer_name in ("ray", "nicola"):
        # Review batches run concurrently (independent I/O-bound calls) so a
        # large BOM's review finishes in ~one batch's time, not the sum.
        if len(_batch_list) > 1:
            from concurrent.futures import ThreadPoolExecutor

            workers = min(_CROSSREF_MAX_CONCURRENCY, len(_batch_list))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                outcomes = list(
                    pool.map(
                        lambda a, rn=reviewer_name: _review_one(rn, a[0], a[1]),
                        list(enumerate(_batch_list, 1)),
                    )
                )
        else:
            outcomes = [_review_one(reviewer_name, 1, _batch_list[0])]
        chunk_verdicts: list[dict[str, Any]] = []
        for bi, (vd, err) in enumerate(outcomes, 1):
            if err is not None:
                raise CrossRefPipelineError(
                    f"CR stage 7: reviewer {reviewer_name!r} could not produce a "
                    f"valid verdict for batch {bi}/{len(_batch_list)} ({err}). A "
                    f"cross-reference without its Ray+Nicola review is not a valid "
                    f"result — aborting (no silent fallback)."
                ) from err
            chunk_verdicts.append(vd)
        # Aggregate the per-batch verdicts into one reviewer verdict.
        approved = all(
            cv.get("verdict", "").upper() in ("APPROVED", "PROCEED") for cv in chunk_verdicts
        )
        merged_obj: list[Any] = []
        for cv in chunk_verdicts:
            merged_obj.extend(cv.get("objections") or [])
        verdict_data = {
            "reviewer": reviewer_name,
            "verdict": "APPROVED" if approved else "REJECTED",
            "objections": merged_obj,
            "batches": len(batches),
        }
        state.review_verdicts.append(verdict_data)
        state.reviewer_log += f"\n--- {reviewer_name.upper()} ---\n{json.dumps(verdict_data)}\n"
        logger.info(
            "CR stage 7: %s %s (%d batches)", reviewer_name, verdict_data["verdict"], len(batches)
        )

    # Pass/fail is decided by the MOST RECENT Ray verdict (Nicola advisory for
    # now). `any` over the whole history would let an early approval mask a
    # later correction-loop rejection.
    latest_ray = _latest_ray_verdict(state.review_verdicts)
    ray_approved = latest_ray.get("verdict", "").upper() in ("APPROVED", "PROCEED")
    state.passed = ray_approved  # Ray must approve; Nicola is advisory for now
    return state


# ---------------------------------------------------------------------------
# Public orchestrator
# ---------------------------------------------------------------------------


def _latest_ray_verdict(verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    """The most recent Ray verdict — the gating reviewer — or ``{}`` if none.

    Ray gates the pipeline (Nicola is advisory for now), so BOTH the pass/fail
    decision and the correction loop's objection list must key off Ray, and off
    the LATEST review round: a stale ``any``-over-history read would let an
    early approval mask a later correction-loop rejection (and reading
    ``review_verdicts[-1]`` returned Nicola's objections, not Ray's).
    """
    for v in reversed(verdicts):
        if v.get("reviewer") == "ray":
            return v
    return {}


_CATEGORY_ALIASES = {
    "inductor": "magnetic",
    "ferrite_bead": "magnetic",
    "transformer": "magnetic",
    "opamp": "analog",
    "op_amp": "analog",
    "op-amp": "analog",
    "operational amplifier": "analog",
    "comparator": "analog",
    "adc": "analog",
    "dac": "analog",
    "analog_ic": "analog",
    "conn": "connector",
    "header": "connector",
    "receptacle": "connector",
    "terminal_block": "connector",
    "terminal block": "connector",
    "crystal": "timeBase",
    "xtal": "timeBase",
    "oscillator": "timeBase",
    "resonator": "timeBase",
    "timebase": "timeBase",
    "time_base": "timeBase",
    "tcxo": "timeBase",
    "vcxo": "timeBase",
}

# Categories the pipeline recognises as-is. A component_type that is neither one
# of these nor an alias above (e.g. a JEDEC/package code like "c0402h32" wrongly
# mapped into the type column) is treated as unknown → re-inferred from the
# description rather than trusted.
_CR_CANONICAL_CATEGORIES = frozenset(
    {
        "capacitor",
        "resistor",
        "magnetic",
        "mosfet",
        "diode",
        "semiconductor",
        "controller",
        "connector",
        "chipBead",
        "varistor",
        "analog",
        "timeBase",
    }
)


def _humanize_value(value: str, category: str) -> str:
    """Convert raw SI values like '10e-6' to human-readable '10µF'.

    This prevents the LLM from misinterpreting scientific notation.
    """
    if not value or not value.strip():
        return value
    try:
        v = float(value)
    except ValueError:
        return value
    if v == 0:
        return value

    units = {
        "capacitor": "F",
        "resistor": "Ω",
        "magnetic": "H",
        "inductor": "H",
        "chipBead": "Ω",
        "varistor": "V",
        "connector": "A",
    }
    unit = units.get(category, "")
    if not unit:
        return value

    prefixes = [
        (1e12, "T"),
        (1e9, "G"),
        (1e6, "M"),
        (1e3, "k"),
        (1, ""),
        (1e-3, "m"),
        (1e-6, "µ"),
        (1e-9, "n"),
        (1e-12, "p"),
    ]
    for scale, prefix in prefixes:
        if abs(v) >= scale:
            scaled = v / scale
            if scaled == int(scaled):
                return f"{int(scaled)}{prefix}{unit}"
            return f"{scaled:.1f}{prefix}{unit}"
    return f"{v}{unit}"


def _infer_component_type(row: dict[str, Any]) -> str:
    """Infer a CR category from a row's free text when no category column exists
    (PLM/eval-board exports give the type only in the description, e.g.
    'CAP CER 10UF', 'RES 10K', 'IND 4.7UH'). Returns "" when it isn't a
    substitutable passive — those rows are correctly left for keep_original
    rather than mis-classified. Manufacturer-agnostic, keyword-based."""
    # Read ALL free-text columns (any "desc"-named column, plus jedec/value), so
    # the type signal isn't missed when it lives in a secondary column the main
    # description doesn't carry (e.g. LumiQuote's "Description (IPN)").
    text = " ".join(
        str(v)
        for k, v in row.items()
        if v and ("desc" in k.lower() or k in ("jedec_type", "value"))
    ).upper()
    # Does the part declare an inductance (henries)? Used to disambiguate a
    # ferrite *bead* (rated in ohms at 100 MHz) from a ferrite-core *inductor*.
    _has_inductance = any(
        w in text for w in ("INDUCTOR", "CHOKE", "COIL", "UH", "MH ", "NH", " H ")
    )
    # Order matters: check the more specific magnetic/resistor words before the
    # capacitor 'CAP' (which also appears in unrelated words rarely).
    if any(w in text for w in ("FERRITE BEAD", "CHIP BEAD", "BEAD", "EMI FILTER")):
        return "chipBead"
    # "Ferrite chip" / "chip ferrite" with NO inductance spec is a ferrite bead
    # (e.g. Murata BLM "Ferrite Chip … 100MHz 3A"), not an inductor — the bare
    # "FERRITE" keyword below would otherwise mis-file it as magnetic.
    if any(w in text for w in ("FERRITE CHIP", "CHIP FERRITE")) and not _has_inductance:
        return "chipBead"
    if any(w in text for w in ("VARISTOR", "MOV ", " MOV", "VDR ")):
        return "varistor"
    if any(
        w in text
        for w in (
            "CONNECTOR",
            "CONN ",
            "TERMINAL BLOCK",
            "SCREW TERMINAL",
            "PIN HEADER",
            "SOCKET HEADER",
            "WIRE-TO-BOARD",
            "BOARD-TO-BOARD",
            "CRIMP",
            "SKEDD",
            "WR-TBL",
            "WR-PHD",
            "WR-WTB",
            "WR-BTB",
            "WR-MPC",
            "SMA CONN",
            "BNC CONN",
            "M12 CONN",
        )
    ):
        return "connector"
    # Time-base parts. The keywords also appear inside IC descriptions
    # ("crystal oscillator driver", "controller with internal oscillator") —
    # only classify as a time base when no IC word co-occurs.
    if any(
        w in text
        for w in ("CRYSTAL", "XTAL", "RESONATOR", "TCXO", "VCXO", "OCXO", "OSCILLATOR")
    ) and not any(w in text for w in ("REG", "CONTROLLER", "CONVERTER", "PWM", "DRIVER")):
        return "timeBase"
    if any(
        w in text
        for w in (
            "OPAMP",
            "OP AMP",
            "OP-AMP",
            "OPERATIONAL AMPLIFIER",
            "COMPARATOR",
            "INSTRUMENTATION AMP",
            "DIFFERENCE AMP",
            "PROGRAMMABLE GAIN",
            "ANALOG SWITCH",
            "MULTIPLEXER",
        )
    ) or re.search(r"\b(ADC|DAC|MUX)\b", text):
        return "analog"
    if any(
        w in text
        for w in (
            "INDUCTOR",
            "IND ",
            "CHOKE",
            "FERRITE",
            "TRANSFORMER",
            "XFMR",
            "COIL",
            "UH ",
            "MH ",
        )
    ):
        return "magnetic"
    if any(w in text for w in ("RESISTOR", "RES ", "RES-", "OHM", "KOHM")):
        return "resistor"
    if any(
        w in text for w in ("CAP CER", "CAPACITOR", "MLCC", "CER CAP", "CAP ", "UF", "NF", "PF")
    ):
        return "capacitor"
    return ""  # IC / diode / etc. — not a substitutable passive


# Primary electrical value per category, parsed out of a free-text part
# description when the BOM has no dedicated value column (LumiQuote / distributor
# exports bury it, e.g. "Inductor … 15uH 20% … 0.027Ohm DCR"). Category-scoped so
# an inductor's "0.027Ohm" DCR or "1KHz" isn't mistaken for its value. These read
# a value the part already declares — not a fabricated one.
_VALUE_FROM_DESC_RE = {
    # number + optional SI prefix + H, but NOT "Hz" (\b stops at the z boundary).
    "magnetic": re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*([pnuµm]?)H\b", re.I),
    # number + optional SI prefix + F (farads).
    "capacitor": re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*([pnuµm]?)F\b", re.I),
}
# Resistance is written two ways in free text: ohm-terminated ("0.027Ohm",
# "10kΩ") or metric-shorthand ("10k", "1M", "4R7"). Try the explicit-ohm form
# first (unambiguous), then the shorthand.
_RES_OHM_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*([kKmMgG]?)\s*(?:Ω|ohms?)\b", re.I)
_RES_SHORT_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*([kKMGR])\b")
# Chip-bead impedance (at 100 MHz). Written as "600 Ohm", "1KOhm", or in terse
# IPN shorthand "50H" / "50R". Accept Ω/ohm and the R/H suffixes, but the H must
# NOT be the "H" of "MHz" — so it can't be followed by a letter (the (?![A-Za-z])
# guard rejects "100MHz" while allowing "50H ").
_BEAD_Z_RE = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*([kKmMgG]?)\s*(?:Ω|ohms?|[RH])(?![A-Za-z])", re.I
)


def _value_from_description(desc: str, category: str) -> str | None:
    """Recover the primary electrical value string (e.g. "15uH", "10uF", "10k")
    from a part description for a given category. Returns None if not found."""
    if not desc:
        return None
    if category == "chipBead":
        # chipBead "value" is its impedance at 100 MHz, in ohms — so ranking can
        # value-match the closest bead instead of returning the catalogue in
        # arbitrary order. Prefer the explicit-ohm form, then the R/H shorthand.
        m = _RES_OHM_RE.search(desc) or _BEAD_Z_RE.search(desc)
        if not m:
            return None
        return f"{m.group(1)}{(m.group(2) or '').strip()}".strip()
    if category == "resistor":
        m = _RES_OHM_RE.search(desc) or _RES_SHORT_RE.search(desc)
        if not m:
            return None
        return f"{m.group(1)}{(m.group(2) or '').strip()}".strip()
    rx = _VALUE_FROM_DESC_RE.get(category)
    if not rx:
        return None
    m = rx.search(desc)
    if not m:
        return None
    num, prefix = m.group(1), (m.group(2) or "")
    unit = {"magnetic": "H", "capacitor": "F"}[category]
    return f"{num}{prefix.lower()}{unit}"


def _package_from_description(desc: str) -> str | None:
    """Recover a chip case code (EIA size, e.g. "0402") from a description, so
    footprint-fit has a source size when there is no package column."""
    if not desc:
        return None
    m = _EIA_CODE_RE.search(desc)
    return m.group(1) if m else None


def _normalize_bom(bom: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Canonicalise BOM field names so the pipeline works with both
    Heaviside-native and Proteus-style BOMs."""
    _FIELD_MAP = {
        "type": "component_type",
        "category": "component_type",
        "part": "original_mpn",
        "mpn": "original_mpn",
    }
    import re as _re

    out: list[dict[str, Any]] = []
    _seen_refs: set[str] = set()
    for idx, comp in enumerate(bom):
        row = dict(comp)
        for old_key, new_key in _FIELD_MAP.items():
            if old_key in row and new_key not in row:
                row[new_key] = row[old_key]

        # ref_des: a missing/blank designator must NOT default to a shared "?"
        # key — that collapses every such row onto one identity and makes the
        # whole BOM look pre-classified (the ADAQ7767-1 bug). Fall back to the
        # ref-des column some exports name differently (location/designator),
        # then to a synthetic unique id. Also de-dupe grouped/repeated refs.
        ref = str(row.get("ref_des") or "").strip()
        if not ref or ref in ("?", "n/a"):
            for alt in ("location", "designator", "refdes", "ref", "item#"):
                v = str(row.get(alt) or "").strip()
                if v and v not in ("?", "n/a"):
                    ref = v
                    break
        if not ref or ref in _seen_refs:
            ref = f"{ref or 'CMP'}#{idx}"  # guarantee uniqueness
        _seen_refs.add(ref)
        row["ref_des"] = ref

        # component_type: normalize through the alias table; if the provided
        # value isn't a recognised CR category — absent, OR a JEDEC/package code
        # that got mapped into the type column (e.g. "C0402H32", "SOT23_6-2") —
        # infer it from the description so passive rows still reach the
        # cross-referencer (and prefetch can find candidates by category). A
        # non-passive (IC/connector/diode) infers to "" and is left unclassified.
        raw_ct = str(row.get("component_type", "")).strip().lower()
        cat = _CATEGORY_ALIASES.get(raw_ct) or (
            raw_ct if raw_ct in _CR_CANONICAL_CATEGORIES else ""
        )
        if not cat:
            cat = _infer_component_type(row)
        if not cat:
            # No type column and no description signal (e.g. a pasted bare
            # part number) — ask the internal catalogue, which is authoritative,
            # instead of leaving the row for the LLM to guess a category from
            # the MPN's shape.
            from heaviside.pipeline.guardrails import lookup_mpn_category

            cat = lookup_mpn_category(str(row.get("original_mpn") or "")) or ""
        if cat:
            row["component_type"] = cat
        else:
            row.pop("component_type", None)  # drop a bogus package-code value
        # Backfill missing value/package/voltage from the catalogue record of
        # the original MPN. A bare-MPN row otherwise reaches candidate ranking
        # with no value — the value filter can't run, the LLM picks from
        # unranked parts, and the G1/G2 value guardrails have nothing to check
        # against (a 10 Ω part once shipped as "partial" for a 10 kΩ original).
        if cat and not (row.get("value") and row.get("package")):
            db = _fields_from_catalogue(str(row.get("original_mpn") or ""), cat)
            if db is not None:
                if not row.get("value") and db["value"]:
                    row["value"] = db["value"]
                if not row.get("package") and db["package"]:
                    row["package"] = db["package"]
                if not row.get("rated_voltage") and db["voltage"]:
                    row["rated_voltage"] = db["voltage"]
        # All free-text columns combined, so value/package recovery doesn't miss
        # data that lives in a secondary description column (e.g. LumiQuote's
        # "Description (IPN)" carries "50H 100MHZ" when the main "Description
        # (Part)" only says "Ferrite Chip, 3A, 2 Pin"). Any column whose name
        # contains "desc" (plus notes) contributes.
        desc_text = " ".join(
            str(v)
            for k, v in row.items()
            if v and ("desc" in k.lower() or k in ("notes", "jedec_type"))
        )
        # Convert raw SI values to human-readable for the LLM
        val = row.get("value", "")
        if val and cat:
            row["value"] = _humanize_value(val, cat)
        elif not val and cat:
            # No value column — recover the part's declared value from its
            # description so ranking can value-filter candidates (else the LLM
            # sees the first 50 unranked parts and matches nothing).
            ev = _value_from_description(desc_text, cat)
            if ev:
                row["value"] = ev
        # No package column — recover a chip case code from the description so
        # footprint-fit has a source size (and stops warning on every row).
        if not row.get("package"):
            pkg = _package_from_description(desc_text)
            if pkg:
                row["package"] = pkg
        # Extract rated_voltage from notes if not explicitly set
        if not row.get("rated_voltage") and not row.get("voltage"):
            notes = str(row.get("notes", ""))
            v_match = _re.search(r"(\d+\.?\d*)\s*V\b", notes)
            if v_match:
                row["rated_voltage"] = v_match.group(0)
        out.append(row)
    return out


def _stage8_learn(state: CrossRefState) -> None:
    """Extract lessons from the CR run and persist them for future runs."""
    try:
        from heaviside.pipeline.teacher import Lesson, store_lessons
    except ImportError:
        return

    now = datetime.now(UTC).isoformat()
    fingerprint = hashlib.sha256(
        f"{state.target_manufacturer}:{len(state.source_bom)}".encode()
    ).hexdigest()[:12]

    lessons: list[Lesson] = []

    # Learn from reviewer objections
    for verdict in state.review_verdicts:
        for obj in verdict.get("objections", []):
            lessons.append(
                Lesson(
                    id=hashlib.sha256(f"cr-objection:{obj}".encode()).hexdigest()[:16],
                    timestamp=now,
                    topology="crossref",
                    category="crossref_objection",
                    severity="high",
                    detail=obj,
                    spec_fingerprint=fingerprint,
                    suggestion="Address in correction loop or improve candidate ranking",
                )
            )

    # Learn from Otto's confirmed gaps (genuine TAS holes)
    for ch in state.otto_log.get("confirmed", state.otto_log.get("challenges", [])):
        if isinstance(ch, dict) and ch.get("verdict") == "CONFIRMED":
            lessons.append(
                Lesson(
                    id=hashlib.sha256(
                        f"cr-otto-gap:{ch.get('ref_des', '')}:{ch.get('diagnosis', '')}".encode()
                    ).hexdigest()[:16],
                    timestamp=now,
                    topology="crossref",
                    category="component_unavailable",
                    severity="medium",
                    detail=f"{ch.get('ref_des', '?')}: {ch.get('diagnosis', '')}",
                    spec_fingerprint=fingerprint,
                    suggestion=ch.get("librarian_request", "File librarian request"),
                )
            )

    # Learn from Otto's overturned diagnoses (search was too narrow)
    for ch in state.otto_log.get("challenges", []):
        if isinstance(ch, dict) and ch.get("verdict") == "OVERTURNED":
            lessons.append(
                Lesson(
                    id=hashlib.sha256(
                        f"cr-otto-hint:{ch.get('ref_des', '')}:{ch.get('diagnosis', '')}".encode()
                    ).hexdigest()[:16],
                    timestamp=now,
                    topology="crossref",
                    category="crossref_objection",
                    severity="medium",
                    detail=f"Otto: {ch.get('ref_des', '?')}: {ch.get('diagnosis', '')}",
                    spec_fingerprint=fingerprint,
                    suggestion="Broaden search criteria for this component type",
                )
            )

    if lessons:
        written = store_lessons(lessons)
        logger.info("CR stage 8: learned %d lessons (%d new)", len(lessons), written)


_MAX_REVIEW_LOOPS = 3


def _objection_refs(objections: list[Any], known_refs: set[str]) -> set[str]:
    """Which known ref_des a reviewer's objections cite.

    Matches against the ACTUAL ref_des present in the result — not a bare
    ``[A-Z]+\\d+`` regex — so it catches synthetic refs like ``CMP#25`` (assigned
    to pasted rows with no designator), lowercase/odd designators, and an explicit
    ``ref_des`` field on a structured (dict) objection. The regex is kept only as
    an extra pass, intersected with ``known_refs`` so it never invents a ref."""
    cited: set[str] = set()
    for obj in objections:
        # 1. explicit ref_des field(s) on a structured objection ("C1" or "C1, C3")
        if isinstance(obj, dict):
            for key in ("ref_des", "ref", "component", "designator"):
                val = obj.get(key)
                if isinstance(val, str):
                    cited.update(p.strip() for p in val.split(",") if p.strip())
        text = json.dumps(obj) if isinstance(obj, dict) else str(obj)
        # 2. any KNOWN ref_des appearing verbatim in the objection text. Guard the
        #    trailing edge so "C1" doesn't match inside "C10" (but "CMP#25" is fine).
        for ref in known_refs:
            if ref and re.search(r"(?<!\w)" + re.escape(ref) + r"(?!\d)", text):
                cited.add(ref)
        # 3. conventional-form fallback, intersected with known below
        cited.update(re.findall(r"\b([A-Z]+\d+)\b", text))
    return cited & known_refs


def _stage3b_correct(state: CrossRefState, objections: list[str]) -> CrossRefState:
    """Re-run the crossref LLM for components cited in reviewer objections."""
    known_refs = {r.get("ref_des", "") for r in state.crossref_result if r.get("ref_des")}
    cited_refs = _objection_refs(objections, known_refs)

    if not cited_refs:
        state.diagnostics.append("correction loop: no ref_des found in objections")
        return state

    # Build a targeted correction prompt with just the objected components
    to_fix = []
    for row in state.crossref_result:
        ref = row.get("ref_des", "")
        if ref in cited_refs:
            comp = next((c for c in state.source_bom if c.get("ref_des") == ref), {})
            entry = {
                "ref_des": ref,
                "component_type": row.get("component_type", ""),
                "original_pn": row.get("original_pn", ""),
                "current_substitute": row.get("substitute_pn"),
                "current_status": row.get("status"),
                "current_notes": row.get("notes", ""),
                "original_value": comp.get("value", ""),
                "original_package": comp.get("package", ""),
                "original_voltage": comp.get("rated_voltage", ""),
            }
            source_dims = comp.get("_source_dims_m")
            src_mm = _source_dims_mm(source_dims)
            if src_mm:
                entry["_source_dimensions_mm"] = src_mm
            candidates = state.candidates_by_ref.get(ref, [])
            if candidates:
                entry["_tas_candidates"] = _candidate_summaries_for_llm(
                    candidates, row.get("component_type", ""), source_dims, limit=15
                )
            to_fix.append(entry)

    if not to_fix:
        return state

    corrections, errors = _crossref_llm_batched(
        to_fix,
        lambda batch: {
            "task": "CORRECTION — fix reviewer objections",
            "objections": objections,
            "components_to_fix": batch,
            "target_manufacturer": state.target_manufacturer,
            "instructions": (
                "The reviewer rejected these substitutions. For each component, "
                "either find a better substitute from _tas_candidates that "
                "addresses the objection, or change status to no_substitute "
                "with a note explaining why no fix is possible. "
                "Respond with the same JSON crossref format."
            ),
        },
        concurrent=True,
    )
    for e in errors:
        state.diagnostics.append(f"correction crossref {e}")
    if not corrections:
        return state

    # Apply corrections back into crossref_result
    corrected_refs: set[str] = set()
    for fix in corrections:
        ref = fix.get("ref_des", "")
        if not ref:
            continue
        for row in state.crossref_result:
            if row.get("ref_des") == ref:
                new_pn = fix.get("substitute_pn", fix.get("wurth_pn"))
                new_status = fix.get("status", row.get("status"))
                if new_pn:
                    row["substitute_pn"] = new_pn
                if new_status:
                    row["status"] = new_status
                row["notes"] = fix.get("notes", row.get("notes", ""))
                corrected_refs.add(ref)
                break

    logger.info(
        "CR stage 3b: corrected %d / %d objected components", len(corrected_refs), len(cited_refs)
    )
    return state


def _to_volts(v: Any) -> float | None:
    """Coerce a rated-voltage field ('10V' / '10' / 10.0) to volts."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    from heaviside.pipeline.value_parse import parse_voltage

    try:
        return parse_voltage(str(v))
    except Exception:
        return None


_VALUE_PARSERS = {
    "capacitor": "parse_capacitance",
    "resistor": "parse_resistance",
    "magnetic": "parse_inductance",
}


def _parse_value_si(value: Any, category: str) -> float | None:
    """Parse a value string to SI base units using the category-specific parser
    (F / Ω / H), falling back to the generic SI parser."""
    from heaviside.pipeline import value_parse

    if value in (None, "", "?"):
        return None
    fn = _VALUE_PARSERS.get(category)
    try:
        if fn:
            return float(getattr(value_parse, fn)(str(value)))
        return value_parse.parse_si_value(str(value))
    except Exception:
        return None


def _param_verdict(orig: float | None, sub: float | None, *, higher_is_ok: bool) -> str:
    """Compare two numeric parameters → exact / exceeds / lower / differs / n-a.

    ``higher_is_ok`` (voltage rating, bypass capacitance): a larger substitute is
    fine ('exceeds'); a smaller one is a real downgrade ('lower'). For value-type
    params where exactness matters, the caller passes higher_is_ok=False."""
    if orig is None or sub is None:
        return "n/a"
    if abs(sub - orig) <= 0.02 * abs(orig) if orig else sub == orig:
        return "exact"
    if sub > orig:
        return "exceeds" if higher_is_ok else "differs"
    return "lower" if higher_is_ok else "differs"


# Which flat-record field is a category's "value" (for the report's value row).
_VALUE_FIELD_BY_CAT = {
    "capacitor": "capacitance",
    "resistor": "resistance_Ohm",
    "magnetic": "inductance",
    "inductor": "inductance",
    "chipBead": "inductance",
}


def _fields_from_catalogue(mpn: str, cat: str) -> dict[str, str] | None:
    """value/voltage/package display strings for an MPN from the internal DB,
    or ``None`` when the part is not catalogued. A field the catalogue lacks
    comes back as "" (honest unknown)."""
    from heaviside.pipeline.guardrails import lookup_part_fields

    rec = lookup_part_fields(mpn, cat)
    if not rec:
        return None
    out = {"value": "", "voltage": "", "package": ""}
    value_field = _VALUE_FIELD_BY_CAT.get(cat)
    raw = rec.get(value_field) if value_field else None
    if isinstance(raw, (int, float)):
        out["value"] = _humanize_value(str(raw), cat)
    volts = rec.get("voltage")
    if isinstance(volts, (int, float)):
        out["voltage"] = f"{volts:g}V"
    pkg = rec.get("package")
    if isinstance(pkg, str):
        out["package"] = pkg.strip()
    return out


def _ground_row_fields_in_catalogue(state: CrossRefState) -> None:
    """Replace LLM-echoed original_*/substitute_* value/voltage/package fields
    with authoritative data before they are rendered or compared.

    The cross-referencer echoes these fields back and, for rows it was handed
    with only a part number, it INVENTS them (a 5 F / 2.7 V supercapacitor once
    shipped as "100nF / 630V" with two different fabricated packages). Sources,
    in order of authority: the user's BOM row (their stated requirement) for the
    original side; the internal catalogue for both sides; otherwise "" — an
    honest unknown, never an LLM guess. Runs after the review/correction loops
    so it grounds the FINAL substitute picks.
    """
    bom_by_ref = {r.get("ref_des"): r for r in state.source_bom}
    for row in state.crossref_result:
        cat = row.get("component_type", "")
        ref = row.get("ref_des")
        bom = bom_by_ref.get(ref) or {}
        orig_pn = str(row.get("original_pn") or row.get("original_mpn") or "").strip()
        db_orig = _fields_from_catalogue(orig_pn, cat) if orig_pn else None
        for field in ("value", "voltage", "package"):
            bom_val = str(bom.get(field) or "").strip()
            if bom_val:
                row[f"original_{field}"] = bom_val
            elif db_orig is not None:
                row[f"original_{field}"] = db_orig[field]
            elif orig_pn:
                row[f"original_{field}"] = ""  # neither BOM nor catalogue knows
        sub_pn = str(row.get("substitute_pn") or "").strip()
        if sub_pn and sub_pn != "no_substitute":
            db_sub = _fields_from_catalogue(sub_pn, cat)
            if db_sub is not None:
                for field in ("value", "voltage", "package"):
                    row[f"substitute_{field}"] = db_sub[field]


def build_match_detail(row: dict[str, Any]) -> dict[str, Any]:
    """Deterministic per-parameter rationale for ONE cross-reference row.

    Compares the original vs substitute value / voltage / package — each with a
    verdict (exact / exceeds / lower / differs / same / n-a) and the raw pair —
    then derives a human ``why`` line explaining the row's status. The data is
    already on the row (no LLM); this just makes 'why exact/recommended/partial'
    explicit and auditable instead of buried in the LLM's free-text notes."""
    cat = row.get("component_type", "")
    status = row.get("status", "")
    params: list[dict[str, Any]] = []

    # value (capacitance/resistance/inductance) — exactness matters
    o_val, s_val = row.get("original_value", ""), row.get("substitute_value", "")
    if o_val or s_val:
        v = _param_verdict(
            _parse_value_si(o_val, cat),
            _parse_value_si(s_val, cat),
            higher_is_ok=(cat == "capacitor"),  # bypass/bulk caps tolerate higher
        )
        params.append(
            {"name": "value", "original": str(o_val), "substitute": str(s_val), "verdict": v}
        )

    # voltage rating — higher is good, lower is a downgrade
    o_v, s_v = row.get("original_voltage", ""), row.get("substitute_voltage", "")
    if o_v or s_v:
        params.append(
            {
                "name": "voltage",
                "original": str(o_v),
                "substitute": str(s_v),
                "verdict": _param_verdict(_to_volts(o_v), _to_volts(s_v), higher_is_ok=True),
            }
        )

    # package — string equality (case/space-insensitive)
    o_p = str(row.get("original_package", "")).strip().lower()
    s_p = str(row.get("substitute_package", "")).strip().lower()
    if o_p or s_p:
        verdict = "same" if (o_p and o_p == s_p) else ("differs" if (o_p and s_p) else "n/a")
        params.append(
            {
                "name": "package",
                "original": row.get("original_package", ""),
                "substitute": row.get("substitute_package", ""),
                "verdict": verdict,
            }
        )

    # Spec-driven electrical parameters (ESR, ripple, dielectric, Rds_on, Qg,
    # Coss, Vf, Qrr, TCR, Isat, DCR, SRF, …) resolved from the internal DB by
    # _stage_param_check. The report renders these generically alongside the
    # core value/voltage/package params above.
    for pr in row.get("_param_results", []):
        params.append(
            {
                "name": pr.get("label") or pr["name"],
                "original": pr.get("original", ""),
                "substitute": pr.get("substitute", ""),
                "verdict": pr.get("verdict", ""),
                "note": pr.get("note", ""),
            }
        )

    # derive a one-line "why" from the parameter verdicts + status
    deviations = [p.get("name") for p in params if p["verdict"] in ("differs", "lower", "fail")]
    warns = [p.get("name") for p in params if p["verdict"] == "warn"]
    exceeds = [p["name"] for p in params if p["verdict"] == "exceeds"]
    exacts = [p["name"] for p in params if p["verdict"] in ("exact", "same", "pass")]
    if status == "exact":
        why = "identical part from the target manufacturer"
    elif status == "keep_original":
        why = row.get("notes") or "kept as-is (already target manufacturer / not fitted)"
    elif status == "no_substitute":
        why = row.get("notes") or "no qualifying part found in the target catalogue"
    else:
        bits = []
        if deviations:
            bits.append("deviates on " + ", ".join(deviations))
        if warns:
            bits.append("marginal on " + ", ".join(warns))
        if exceeds:
            bits.append("exceeds on " + ", ".join(exceeds))
        if exacts:
            bits.append("matches " + ", ".join(exacts))
        why = f"{status}: " + ("; ".join(bits) if bits else "meets constraints")
    return {"params": params, "why": why}


def _annotate_match_detail(state: CrossRefState) -> None:
    """Attach a deterministic per-parameter match_detail to every result row."""
    for row in state.crossref_result:
        row["match_detail"] = build_match_detail(row)


# Which _summarize_candidate key holds a category's primary electrical value
# (SI base units). Used to read the substitute's authoritative value straight
# from its catalogue envelope for the primary-value gate.
_PRIMARY_SUMMARY_KEY = {
    "capacitor": "capacitance",
    "resistor": "resistance",
    "magnetic": "inductance",
    "chipBead": "impedance_100mhz",
}


def _force_no_substitute(row: dict[str, Any], reason: str, *, fire: str = "NO_ORIGINAL_DATA") -> None:
    """Reject a proposed substitute in place: clear it, set no_substitute, append
    a reason to the notes, and record a guardrail fire. Shared by the
    original-has-no-data gates and the primary-value gate so a rejected row is
    always cleared consistently (no stale substitute_* fields left rendering)."""
    row["status"] = "no_substitute"
    row["substitute_pn"] = None
    for f in ("substitute_value", "substitute_voltage", "substitute_package"):
        row[f] = ""
    row.pop("_param_results", None)
    prior = (row.get("notes") or "").strip()
    row["notes"] = (prior + " | " if prior else "") + reason
    fires = row.setdefault("guardrail_fires", [])
    if fire not in fires:
        fires.append(fire)


def _primary_value_si(
    row: dict[str, Any],
    cat: str,
    orig_params: dict[str, Any] | None,
    sub_params: dict[str, Any] | None,
) -> tuple[float | None, float | None]:
    """Resolve (original, substitute) primary values in SI base units for the
    primary-value gate. Original prefers the BOM-grounded ``original_value``
    (the user's stated requirement), falling back to the catalogue record;
    substitute prefers the catalogue envelope value (authoritative SI), falling
    back to the grounded ``substitute_value`` string. Returns None for a side
    that cannot be resolved — never a guessed value."""
    key = _PRIMARY_SUMMARY_KEY.get(cat)

    def _from_params(p: dict[str, Any] | None) -> float | None:
        if not p or not key:
            return None
        v = p.get(key)
        return float(v) if isinstance(v, (int, float)) else None

    orig_si = _parse_value_si(row.get("original_value", ""), cat)
    if orig_si is None:
        orig_si = _from_params(orig_params)
    sub_si = _from_params(sub_params)
    if sub_si is None:
        sub_si = _parse_value_si(row.get("substitute_value", ""), cat)
    return orig_si, sub_si


def _stage_param_check(state: CrossRefState) -> None:
    """Resolve original + substitute *electrical* parameters from the internal DB
    and attach a per-parameter verdict (ESR, ripple, dielectric, Rds(on), Qg,
    Coss, Vf, Qrr, TCR, Isat, DCR, SRF, …) to every row.

    These parameters are never in the source BOM, so both sides are looked up by
    MPN. A substitute that FAILs a critical parameter is demoted from
    ``recommended`` to ``partial`` and the reason is surfaced (fail loud — a
    silently-worse ESR/ripple/Isat part is not a clean equivalent). Missing data
    is reported as ``unverified``, never silently treated as a pass.
    """
    from heaviside.catalogue._reader import CatalogueReadError, iter_envelopes
    from heaviside.catalogue.selector import _tas_data_dir
    from heaviside.pipeline.param_check import (
        FAIL,
        PARAM_SPECS,
        UNVERIFIED,
        connector_mating_check,
        evaluate_params,
        mlcc_bias_param,
    )

    category_files = {
        "mosfet": "mosfets.ndjson",
        "diode": "diodes.ndjson",
        "capacitor": "capacitors.ndjson",
        "resistor": "resistors.ndjson",
        "magnetic": "magnetics.ndjson",
        "chipBead": "magnetics.ndjson",
        "connector": "connectors.ndjson",
        "analog": "analog_ics.ndjson",
        "timeBase": "timing_devices.ndjson",
    }
    rows = state.crossref_result

    # Collect the (category, mpn) pairs to resolve — original and substitute,
    # for spec'd categories only.
    needed: dict[str, set[str]] = {}
    for row in rows:
        cat = row.get("component_type", "")
        if cat not in PARAM_SPECS or cat not in category_files:
            continue
        for key in ("original_pn", "substitute_pn"):
            mpn = str(row.get(key) or "").strip().lower()
            if mpn:
                needed.setdefault(cat, set()).add(mpn)
    if not needed:
        return

    tas_dir = _tas_data_dir()
    # Scan each NDJSON once, summarising only the parts we need. magnetics.ndjson
    # serves both magnetic + chipBead, so group categories by file.
    cats_by_file: dict[str, list[str]] = {}
    for cat in needed:
        cats_by_file.setdefault(category_files[cat], []).append(cat)

    params_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for fname, cats in cats_by_file.items():
        path = tas_dir / fname
        if not path.exists():
            continue
        try:
            for _lineno, env in iter_envelopes(path):
                for cat in cats:
                    refs = needed.get(cat, set())
                    if not refs:
                        continue
                    ref = str(_envelope_reference(env, cat) or "").strip().lower()
                    if ref and ref in refs and (cat, ref) not in params_by_key:
                        summ = _summarize_candidate(env, cat)
                        if summ:
                            params_by_key[(cat, ref)] = summ
        except CatalogueReadError:
            continue

    for row in rows:
        cat = row.get("component_type", "")
        if cat not in PARAM_SPECS:
            continue
        o_mpn = str(row.get("original_pn") or "").strip().lower()
        s_mpn = str(row.get("substitute_pn") or "").strip().lower()
        orig_params = params_by_key.get((cat, o_mpn))
        sub_params = params_by_key.get((cat, s_mpn))

        # IDENTITY-MATCHED categories (connector / analog / crystal): the match
        # is defined by the ORIGINAL's identity (connector family+positions+
        # pitch+gender; analog function+channels; crystal frequency+technology+
        # load-C). If the original is NOT in the internal DB, that identity is
        # unknown and unverifiable — so a substitute cannot be justified, no
        # matter what an earlier stage (LLM guess or deterministic rescue)
        # proposed. Per policy: no data on the original + can't verify it →
        # DON'T cross-reference it. Force no_substitute with a clear reason.
        has_sub = s_mpn not in ("", "no_substitute")
        if cat in _IDENTITY_MATCHED_CATEGORIES and has_sub and orig_params is None:
            _label = {"connector": "connector", "analog": "analog IC", "timeBase": "crystal/oscillator"}
            row["status"] = "no_substitute"
            row["substitute_pn"] = None
            for f in ("substitute_value", "substitute_voltage", "substitute_package"):
                row[f] = ""
            row.pop("_param_results", None)
            prior = (row.get("notes") or "").strip()
            reason = (
                f"original {row.get('original_pn') or o_mpn!r} is not in the internal DB, so its "
                f"{_label.get(cat, cat)} identity cannot be verified — a substitute cannot be "
                "justified. Not cross-referenced (need the original's real specs from a datasheet/"
                "distributor first)."
            )
            row["notes"] = (prior + " | " if prior else "") + reason
            fires = row.setdefault("guardrail_fires", [])
            if "NO_ORIGINAL_DATA" not in fires:
                fires.append("NO_ORIGINAL_DATA")
            continue

        # VALUE-MATCHED categories (mosfet/diode/capacitor/resistor/magnetic/…):
        # the match is defined by the ORIGINAL's value/electrical specs. If the
        # original has NO resolvable specs at all — not in the internal DB
        # (orig_params is None) AND no usable value in the BOM — then a proposed
        # substitute was matched against NOTHING and is an ungrounded guess.
        # Same principle as identity-matched: no data on the original + can't
        # verify → don't cross-reference. This is the defence-in-depth that stops
        # a fabricated "partial" when a part can't be sourced from any distributor.
        # (A sourced original — the normal case — has orig_params, so this never
        # fires for it; a BOM that carries the value keeps its own value match.)
        _bom_value = str(row.get("original_value") or "").strip().lower()
        _no_bom_value = _bom_value in ("", "-", "–", "n/a", "na", "none", "nan", "?")
        _genuine_sub = has_sub and s_mpn != o_mpn
        if (
            cat not in _IDENTITY_MATCHED_CATEGORIES
            and _genuine_sub
            and orig_params is None
            and _no_bom_value
            and row.get("status") in ("recommended", "partial", "exact")
        ):
            row["status"] = "no_substitute"
            row["substitute_pn"] = None
            for f in ("substitute_value", "substitute_voltage", "substitute_package"):
                row[f] = ""
            row.pop("_param_results", None)
            prior = (row.get("notes") or "").strip()
            reason = (
                f"original {row.get('original_pn') or o_mpn!r} has no resolvable specs — not in "
                "the internal DB and no value in the BOM — so a substitute cannot be verified "
                "against it. Not cross-referenced (need the original's real specs from a "
                "datasheet/distributor first)."
            )
            row["notes"] = (prior + " | " if prior else "") + reason
            fires = row.setdefault("guardrail_fires", [])
            if "NO_ORIGINAL_DATA" not in fires:
                fires.append("NO_ORIGINAL_DATA")
            continue

        results = evaluate_params(cat, orig_params, sub_params)

        # PRIMARY VALUE GATE (R / L / C / Z): the defining electrical spec of a
        # passive is NOT in evaluate_params — historically it was only described
        # in prose and could never reject a row, which is how a 330 nH part was
        # accepted as a "partial" substitute for a 1.5 µH original. Compare it
        # here with the proximity/utility engine and gate on it:
        #   FAIL (value out of the accept window) → the substitute is a
        #       DIFFERENT value, not this part → no_substitute.
        #   WARN (in-window but off nominal)      → a defensible deviation →
        #       cap a 'recommended' at 'partial' and say so.
        # Only value-matched passives have a primary-value spec; mosfet/diode/
        # connector/analog/timeBase return None and are unaffected.
        if has_sub:
            from heaviside.pipeline.scoring import FAIL as _S_FAIL
            from heaviside.pipeline.scoring import WARN as _S_WARN
            from heaviside.pipeline.scoring import score_primary_value

            orig_si, sub_si = _primary_value_si(row, cat, orig_params, sub_params)
            pv = score_primary_value(cat, orig_si, sub_si)
            if pv is not None and pv.verdict == _S_FAIL:
                _force_no_substitute(
                    row,
                    f"primary value out of range: substitute {pv.note} — a different "
                    "value is not an in-kind substitute for this part.",
                    fire="PRIMARY_VALUE",
                )
                continue
            if pv is not None and pv.verdict == _S_WARN:
                if row.get("status") == "recommended":
                    row["status"] = "partial"
                prior = (row.get("notes") or "").strip()
                row["notes"] = (
                    (prior + " | " if prior else "")
                    + f"primary value deviates: {pv.note} (verify it suits the circuit)."
                )
                fires = row.setdefault("guardrail_fires", [])
                if "PRIMARY_VALUE:warn" not in fires:
                    fires.append("PRIMARY_VALUE:warn")

        # MLCC DC-bias: compare effective capacitance at the component's
        # operating voltage (from sim stress, when available). Only meaningful
        # for capacitors with a known bias and class-2 model anchors.
        if cat == "capacitor":
            ref = row.get("ref_des", "")
            stress = state.stress_by_ref.get(ref)
            v_op = stress.v_peak if stress is not None else None
            bias = mlcc_bias_param(orig_params or {}, sub_params or {}, v_op)
            if bias is not None:
                results.append(bias)

        # Connectors: mating-system compatibility — a connector is half of a
        # mated PAIR; a cross-vendor series swap is only a drop-in when the
        # interface is standardized (USB/RJ45/D-Sub…), the part has no discrete
        # mating half (terminal block / card edge), or it's a commodity pin
        # header at matching pitch. Skipped for parts that stay in place
        # (no_substitute / keep_original).
        if cat == "connector" and str(row.get("substitute_pn") or "") not in (
            "",
            "no_substitute",
        ):
            mate = connector_mating_check(orig_params, sub_params)
            if mate is not None:
                results.append(mate)

        if not results:
            continue
        row["_param_results"] = results

        fails = [r for r in results if r["verdict"] == FAIL]
        # Identity parameters (connector positions/gender/family/pitch, analog
        # function/channels) that could NOT be verified also block a clean
        # 'recommended' — a senior engineer never ships an unverified mate.
        unverified_critical = [
            r for r in results if r["verdict"] == UNVERIFIED and r.get("critical")
        ]
        # Only demote an actively-recommended substitute; 'exact' (identical
        # part) and 'partial'/'no_substitute' don't move. This keeps the
        # parameter check a tightening, never a loosening, of the verdict.
        if (fails or unverified_critical) and row.get("status") == "recommended":
            row["status"] = "partial"
        if fails or unverified_critical:
            note = "; ".join(r["note"] for r in fails + unverified_critical)
            existing = row.get("notes") or ""
            row["notes"] = (existing + " | " if existing else "") + f"parameter check: {note}"
            fires = row.setdefault("guardrail_fires", [])
            for r in fails:
                tag = f"PARAM:{r['name']}"
                if tag not in fires:
                    fires.append(tag)
            for r in unverified_critical:
                tag = f"PARAM:{r['name']}:unverified"
                if tag not in fires:
                    fires.append(tag)


def _best_inkind_candidate(
    comp: dict[str, Any], cat: str, candidates: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Deterministic in-kind gate: the best prefetched candidate that PROVABLY
    satisfies the cross-referencer's own substitution criteria (same chemistry
    family + voltage >= original + value in range). Candidates are pre-ranked,
    so the first that passes is the best. Returns a substitute row patch, or
    None when no candidate qualifies (a genuine no_substitute)."""
    from heaviside.pipeline.scoring import (
        FAIL as _S_FAIL,
        PASS as _S_PASS,
        PRIMARY_VALUE_SPECS,
        score_primary_value,
    )

    # The normalized BOM row stores the value under "value" (humanized); older
    # code read "value_si"/"original_value", which do not exist on these rows —
    # that silent None is exactly what let a 330 nH part rescue a 1.5 µH original
    # with the value check no-oped. Read the real key, parse with the
    # category-specific parser.
    orig_vsi = _parse_value_si(
        comp.get("value") or comp.get("original_value") or comp.get("value_si"), cat
    )
    # No-fallbacks: a value-matched category with no parseable original value
    # cannot have its defining spec verified, so we must NOT rescue a part
    # against nothing. Refuse (a genuine no_substitute), don't guess.
    if cat in PRIMARY_VALUE_SPECS and orig_vsi is None:
        return None

    orig_v = _to_volts(comp.get("rated_voltage") or comp.get("original_voltage"))
    orig_fam = _capacitor_technology_family(comp.get("technology")) if cat == "capacitor" else None

    for env in candidates:
        cand_vsi = _extract_value(env, cat)
        s = _summarize_candidate(env, cat)
        cand_v = _to_volts(s.get("voltage"))
        # voltage floor: if both known, candidate must meet the original rating
        if orig_v is not None and cand_v is not None and cand_v < orig_v:
            continue
        # chemistry family must match for caps (stops ceramic<->tantalum<->alu drift)
        if (
            cat == "capacitor"
            and orig_fam is not None
            and _capacitor_technology_family(s.get("technology")) != orig_fam
        ):
            continue
        # Primary value window via the shared proximity engine (same rule the
        # param-check gate applies, so rescue can never propose a value the gate
        # would then reject). FAIL → skip this candidate; PASS (tight) →
        # recommended; WARN (in-window, off nominal) → partial.
        pv = score_primary_value(cat, orig_vsi, cand_vsi)
        if pv is not None:
            if pv.verdict == _S_FAIL:
                continue
            within_tight = pv.verdict == _S_PASS
        else:
            within_tight = False
        status = (
            "recommended"
            if (within_tight and (orig_v is None or cand_v is not None))
            else "partial"
        )
        return {
            "substitute_pn": s.get("mpn"),
            "substitute_value": s.get("value", ""),
            "substitute_voltage": str(cand_v) if cand_v is not None else "",
            "substitute_package": s.get("package", ""),
            "status": status,
        }
    return None


def _ondemand_candidates(
    target_manufacturer: str,
    category: str,
    comp: dict[str, Any],
    cache: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Load + rank target-manufacturer candidates for one component straight
    from TAS, used by the deterministic rescue when prefetch left none. The
    per-category manufacturer rows are cached across the rescue call so each
    NDJSON is scanned at most once."""
    files = {
        "capacitor": "capacitors.ndjson",
        "resistor": "resistors.ndjson",
        "magnetic": "magnetics.ndjson",
        "chipBead": "magnetics.ndjson",
        "varistor": "varistors.ndjson",
        "connector": "connectors.ndjson",
        "analog": "analog_ics.ndjson",
        "timeBase": "timing_devices.ndjson",
    }
    if category not in files:
        return []
    if category not in cache:
        from heaviside.catalogue._reader import CatalogueReadError, iter_envelopes

        tas_dir = Path(
            os.environ.get(
                "HEAVISIDE_TAS_DATA_DIR",
                str(Path(__file__).resolve().parents[2] / "TAS" / "data"),
            )
        )
        path = tas_dir / files[category]
        rows: list[dict[str, Any]] = []
        target = _normalize_manufacturer(target_manufacturer)
        if path.exists():
            try:
                for _lineno, env in iter_envelopes(path):
                    if category == "chipBead" and not _is_chip_bead_env(env):
                        continue
                    mfr = _extract_manufacturer(env, category)
                    if mfr and target in _normalize_manufacturer(mfr):
                        rows.append(env)
            except CatalogueReadError:
                pass
        cache[category] = rows
    return _rank_candidates(comp, category, cache[category], max_results=50)


def _stage6_5_deterministic_rescue(state: CrossRefState) -> CrossRefState:
    """Deterministic floor under the two stochastic LLM stages (stage3 crossref
    + stage6 otto): for any remaining no_substitute, if a prefetched candidate
    PROVABLY meets the in-kind criteria, promote it. After this, no_substitute
    means 'no qualifying candidate exists in TAS', not 'the LLM dropped it' —
    removing the run-to-run variance (e.g. um3491's X7T caps)."""
    by_ref = {c.get("ref_des", c.get("name", "?")): c for c in state.source_bom}
    ondemand_cache: dict[str, list[dict[str, Any]]] = {}
    rescued = 0
    for row in state.crossref_result:
        if row.get("status") != "no_substitute":
            continue
        ref = row.get("ref_des")
        cat = row.get("component_type", "")
        # Identity-matched categories (connector / analog / crystal) are NEVER
        # rescued: the rescue's value/voltage/chemistry logic is meaningless for
        # a connector (it can't tell a 26-way board-to-board from a matable
        # counterpart) and would force a substitute onto an original we can't
        # verify. For these, the LLM's + strict-ranker's no_substitute stands.
        if cat in _IDENTITY_MATCHED_CATEGORIES:
            continue
        comp = by_ref.get(ref, row)
        cands = state.candidates_by_ref.get(ref, [])
        if not cands:
            # Prefetch left no candidates for this row — usually a ref-des /
            # category mismatch between the source BOM (prefetch keys on it) and
            # the cross-referenced result. The deterministic floor must not skip
            # a rescuable part on that account, so fetch from TAS on demand.
            cands = _ondemand_candidates(state.target_manufacturer, cat, comp, ondemand_cache)
            if cands:
                logger.info(
                    "CR stage 6.5: prefetch had 0 candidates for %s (%s); fetched %d on-demand",
                    ref,
                    cat,
                    len(cands),
                )
        if not cands:
            continue
        # If the prior stage already determined there's no suitable substitute
        # (e.g. "No suitable ferrite bead substitute available" — the internal DB
        # has the wrong component sub-type), the rescue must not override that
        # verdict by substituting a structurally different part.
        prior_notes = (row.get("notes") or "").lower()
        if "no suitable" in prior_notes and "substitute available" in prior_notes:
            continue
        patch = _best_inkind_candidate(comp, cat, cands)
        if patch is None:
            continue
        row.update(patch)
        prior = (row.get("notes") or "").strip()
        # Name only what the rescue actually verified: the primary value is
        # checked for every value-matched category; voltage floor and chemistry
        # family additionally for capacitors. (The old note claimed
        # "voltage/value/chemistry" unconditionally even when those checks had
        # no-oped — the honesty regression this rework removes.)
        basis = "value" + ("/voltage/chemistry" if cat == "capacitor" else "")
        row["notes"] = (
            f"{prior} | deterministic in-kind rescue ({patch.get('status', 'partial')}): "
            f"{patch['substitute_pn']} verified on {basis} (LLM stages dropped it)."
        ).strip(" |")
        rescued += 1
    if rescued:
        logger.info("CR stage 6.5: deterministically rescued %d no_substitute rows", rescued)
    return state


_SUBSTITUTED_STATUSES = ("exact", "recommended", "partial")


def _stage_footprint_caveat(state: CrossRefState) -> CrossRefState:
    """Mark any substitution whose part is ~one EIA size larger than the
    original as a PARTIAL, with an explicit "old → new size, verify board fit"
    caveat. Deterministic backstop so a larger substitute is always honestly
    flagged (and downgraded from 'recommended') regardless of how the LLM
    scored it — and so the report can show the size change."""
    for row in state.crossref_result:
        if row.get("status") not in _SUBSTITUTED_STATUSES:
            continue
        op = row.get("original_package")
        sp = row.get("substitute_package")
        src = _eia_dims_from_case(op)
        sub = _eia_dims_from_case(sp)
        if not src or not sub:
            continue
        tier = _footprint_tier((src[0], src[1], None), (sub[0], sub[1], None))
        if tier == "one_size_larger":
            if row.get("status") in ("exact", "recommended"):
                row["status"] = "partial"
            row["footprint_caveat"] = {"original_package": op, "substitute_package": sp}
            note = f"Footprint one size larger: {op} → {sp} — verify board fit."
            existing = str(row.get("notes") or "").strip()
            if note not in existing:
                row["notes"] = f"{existing} | {note}" if existing else note
    return state


def run_crossref_pipeline(
    source_bom: list[dict[str, Any]],
    target_manufacturer: str,
    *,
    circuit_context: str | None = None,
    stress_by_ref: dict[str, Any] | None = None,
    verbose: bool = False,
    progress: Any = None,
) -> CrossRefOutcome:
    """Run the full CR pipeline end-to-end.

    When ``stress_by_ref`` is provided (from RE simulation), candidates
    are ranked and guardrails are applied using actual per-component
    voltage and current stress instead of static BOM specs.

    ``progress`` (optional) is called as ``progress(message, pct)`` before
    each stage so a caller (the Jobs UI) can render granular per-stage state.
    """

    def _say(msg: str, pct: int) -> None:
        if progress is not None:
            with contextlib.suppress(Exception):
                progress(msg, pct)

    n = len(source_bom)
    # Emit the first stage BEFORE _normalize_bom so the "Resolve part numbers"
    # stage is shown active during it. _normalize_bom categorises each row —
    # for a bare/unknown MPN that scans the internal DB — which can take a while;
    # doing it before any progress message left the bar stuck at 0/N with no
    # active stage (the "running design stays at position 0" bug).
    _say("Resolving messy BOM part numbers", 3)
    state = CrossRefState(
        source_bom=_normalize_bom(source_bom),
        target_manufacturer=target_manufacturer,
        circuit_context=circuit_context,
        stress_by_ref=stress_by_ref or {},
    )
    state = _stage0_resolve_parts(state)
    _say(f"Prefetching TAS candidates for {n} components", 5)
    state = _stage1_prefetch(state)
    _say("Librarian: sourcing any missing components from datasheets/distributors", 15)
    state = _stage1_5_librarian(state)
    _say("Librarian: fetching unknown originals from Digi-Key", 22)
    state = _stage1_6_fetch_originals(state)
    _say("Pre-classifying each component by category", 28)
    state = _stage2_preclassify(state)
    _say(f"Cross-referencing to {target_manufacturer} (LLM picks equivalents)", 38)
    state = _stage3_crossref(state)
    _say("Applying guardrails (voltage/current/package physics checks)", 55)
    state = _stage4_guardrails(state)
    _say("Scoring the substitute candidates", 64)
    state = _stage5_score(state)
    _say("Otto challenge (field-sales rebuttal of every no-substitute)", 70)
    state = _stage6_otto(state)
    _say("Deterministic in-kind rescue for residual gaps", 78)
    state = _stage6_5_deterministic_rescue(state)
    state = _stage_footprint_caveat(state)
    _say("Adversarial review (Ray + Nicola)", 84)
    state = _stage7_review(state)

    # Correction loop: if reviewer rejects, fix objected components and re-review.
    # Re-runs stay under the "review" stage (no backward stage bounce in the UI).
    for loop_i in range(1, _MAX_REVIEW_LOOPS + 1):
        if state.passed:
            break
        # Correct the objections from the GATING reviewer (Ray), not
        # review_verdicts[-1] which is always Nicola (appended last).
        objections = _latest_ray_verdict(state.review_verdicts).get("objections", [])
        if not objections:
            break

        # If the objections don't cite any component we can act on, re-running the
        # LLM + review won't change anything — break instead of spinning through
        # the remaining iterations (each is a full re-review round).
        known_refs = {r.get("ref_des", "") for r in state.crossref_result if r.get("ref_des")}
        if not _objection_refs(objections, known_refs):
            logger.info(
                "CR correction loop %d: %d objection(s) cite no actionable ref_des — "
                "stopping the loop (not re-reviewing)",
                loop_i,
                len(objections),
            )
            state.diagnostics.append("correction loop: no ref_des found in objections")
            break

        logger.info("CR correction loop %d: addressing %d objections", loop_i, len(objections))
        _say(f"Correction loop {loop_i}: addressing {len(objections)} reviewer objections", 88)
        state = _stage3b_correct(state, objections)
        state = _stage4_guardrails(state)
        state = _stage5_score(state)
        state = _stage6_otto(state)
        state = _stage6_5_deterministic_rescue(state)
        state = _stage_footprint_caveat(state)
        state = _stage7_review(state)

    # Stage 8: Learn from this run
    _say("Learning from this run (persisting accepted substitutions)", 95)
    _stage8_learn(state)

    # Ground the report's value/voltage/package fields in the BOM + internal DB
    # (the LLM's echoes are unreliable and, for bare-MPN rows, invented). Runs
    # after the review/correction loops so the FINAL picks are grounded, before
    # param check + match_detail so comparisons and the report use real data.
    _ground_row_fields_in_catalogue(state)
    # Electrical-parameter check (ESR, ripple, Rds_on, Qrr, Isat, …): resolve
    # original + substitute values from the internal DB, flag/demote substitutes
    # that fall outside the allowed margin. Runs before match_detail so its
    # verdicts are rendered, and after the review loop so it sees final picks.
    _stage_param_check(state)
    # Deterministic per-parameter rationale (why exact/recommended/partial) for
    # every row, computed from the original-vs-substitute fields already present.
    _annotate_match_detail(state)
    outcome = CrossRefOutcome.from_state(state)
    logger.info(
        "CR pipeline %s: %d components, %s → %s",
        "PASSED" if outcome.passed else "FAILED",
        len(outcome.components),
        "mixed" if not outcome.passed else "all",
        target_manufacturer,
    )
    return outcome


def _run_re_enrichment_stages(re_state: REState) -> REState:
    """Fan out the INDEPENDENT RE enrichment stages onto the shared ``re_state``.

    Field audit (reads / writes per stage) drives what may run concurrently:

    * ``_stage2_5_verify_mpns`` — reads ``ref_bom`` (each row's role/mpn/category);
      writes only each row's ``in_tas`` flag (+ best-effort Digi-Key magnetic fetch)
      and appends to ``diagnostics``. It neither reads nor writes ``ref_spec`` /
      ``ref_claims`` / the testbench outputs.
    * ``_stage2_65_extract_rdson`` — reads ``ref_spec`` + ``ref_bom`` + ``pdf_text``;
      wholesale-REASSIGNS ``state.ref_spec`` (adds the extracted Rds_on).
    * ``_stage2_7_extract_claims`` — reads ``ref_spec`` + ``pdf_text``; reassigns
      ``state.ref_claims`` and, gated on 2.65 NOT having found it, also reassigns
      ``state.ref_spec``.
    * ``_stage2_8_testbench`` — reads the Rds_on-enriched ``ref_spec`` AND the
      populated ``ref_claims`` (load points + Rds_on selection); writes
      ``role_map`` / ``comparisons`` / ``sim_result`` / ``tas`` / ``netlist`` /
      ``passed`` / ``lessons``.

    So 2.65 → 2.7 → 2.8 is a hard SEQUENTIAL chain: 2.65 and 2.7 both do a
    wholesale ``state.ref_spec = ReferenceSpec(...)`` reassignment (the spec is a
    frozen dataclass), 2.7 reads the spec 2.65 wrote, and 2.8 reads both the spec
    and the claims the first two wrote. Reassigning those shared containers
    concurrently would race and change the result — they stay ordered.

    Only ``_stage2_5_verify_mpns`` is independent (disjoint write-set: ``in_tas`` +
    ``diagnostics``; reads no field the chain writes), so it runs CONCURRENTLY with
    the whole chain on the same shared state — disjoint-field in-place mutation is
    GIL-safe (``diagnostics`` appends are atomic; only their interleave order, not
    their content, is non-deterministic). This overlaps the DB-lookup / distributor
    fetch (network-bound) with the LLM + ngspice chain.

    Exceptions from any stage PROPAGATE (``Future.result()`` re-raises) — no
    swallowing (CLAUDE.md: surface problems, fail loud).
    """
    from concurrent.futures import ThreadPoolExecutor

    # Reference the stages through the module so test monkeypatches take effect.
    from heaviside.pipeline import re_pipeline as _re

    def _rdson_claims_testbench(s: REState) -> REState:
        s = _re._stage2_65_extract_rdson(s)
        s = _re._stage2_7_extract_claims(s)
        s = _re._stage2_8_testbench(s)
        return s

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="re-enrich") as pool:
        f_verify = pool.submit(_re._stage2_5_verify_mpns, re_state)
        f_chain = pool.submit(_rdson_claims_testbench, re_state)
        # Surface either branch's exception (no swallow). Block on both so the
        # shared state is fully populated before the bridge reads it.
        f_verify.result()
        return f_chain.result()


def run_crossref_with_cre(
    reference: str,
    target_manufacturer: str,
    *,
    pdf_path: Path | None = None,
    pdf_text: str | None = None,
    source_bom_override: list[dict[str, Any]] | None = None,
    verbose: bool = False,
    progress: Any = None,
    review_llm: bool = False,
) -> CrossRefOutcome:
    """RE-fronted cross-reference: simulate first, then crossref with stress.

    Runs RE stages 0→2.8 to extract specs, BOM, and simulate the
    reference design. Then extracts per-component V/I stress from the
    simulation and feeds it into the CR pipeline for stress-informed
    ranking, guardrails, and scoring.

    When ``source_bom_override`` is provided (e.g. from a pre-extracted
    Proteus BOM), the CR pipeline uses that instead of the RE-extracted
    BOM. The RE BOM is still used for simulation (power-path components).
    """
    from heaviside.pipeline.re_pipeline import (
        _stage0_extract_pdf,
        _stage1_spec_extract,
        _stage2_reverse_engineer,
    )
    from heaviside.pipeline.re_state import REState
    from heaviside.pipeline.re_testbench import extract_component_stress

    def _say(msg: str, pct: int) -> None:
        if progress is not None:
            with contextlib.suppress(Exception):
                progress(msg, pct)

    # --- RE stages: extract and simulate ---
    # Carry the review flag + progress sink so the high-risk extraction stages
    # (competitor specs, reverse-engineered BOM) run under Ray+Nicola review.
    re_state = REState(
        reference=reference, pdf_path=pdf_path, review_llm=review_llm, progress=progress
    )
    if pdf_text is not None:
        # Pre-extracted text (e.g. an HTML app-note fetched from a URL):
        # seed it directly so stage 0 skips PDF extraction.
        re_state.pdf_text = pdf_text
    _say("Extracting the reference document (text + tables)", 2)
    re_state = _stage0_extract_pdf(re_state)
    _say("Spec extract: Vin/Vout/topology/fsw from the reference", 6)
    re_state = _stage1_spec_extract(re_state)
    _say("Reverse-engineering the schematic + topology", 10)
    re_state = _stage2_reverse_engineer(re_state)
    # RE enrichment fan-out: MPN verification runs CONCURRENTLY with the
    # rdson → claims → testbench chain (see `_run_re_enrichment_stages` for the
    # per-stage field audit and why only verify-mpns is independent).
    _say("Verify MPNs ∥ RDS(on)/claims/testbench (concurrent)", 14)
    re_state = _run_re_enrichment_stages(re_state)

    # --- Bridge: extract per-component stress ---
    _say("RE→CR bridge: extracting per-component V/I stress from the sim", 30)
    stress_by_ref = extract_component_stress(re_state)
    logger.info("RE→CR bridge: %d components have simulation stress data", len(stress_by_ref))

    # --- CR pipeline with stress data ---
    # Use pre-extracted BOM if provided (more complete than LLM extraction),
    # otherwise use the RE-extracted BOM.
    if source_bom_override:
        source_bom = _normalize_bom(source_bom_override)
        logger.info(
            "RE→CR: using provided BOM (%d components) instead of RE-extracted (%d)",
            len(source_bom),
            len(re_state.ref_bom),
        )
    else:
        source_bom = _normalize_bom(re_state.ref_bom)

    # Build circuit context from RE spec
    ctx = ""
    if re_state.ref_spec:
        s = re_state.ref_spec
        ctx = (
            f"Topology: {s.topology}, Vin={s.vin_nom}V, "
            f"Vout={s.vout}V, Iout={s.iout}A, fsw={s.fsw / 1e3:.0f}kHz"
        )

    # Forward progress to the CR core, scaling its 0–100 into the post-RE band
    # (30–100) so the percentage advances monotonically across both phases. The
    # stage names map by keyword, so they render correctly regardless of pct.
    cr_progress = None
    if progress is not None:

        def cr_progress(msg: str, pct: int) -> None:
            _say(msg, 30 + int(pct * 0.70))

    return run_crossref_pipeline(
        source_bom,
        target_manufacturer,
        circuit_context=ctx,
        stress_by_ref=stress_by_ref,
        verbose=verbose,
        progress=cr_progress,
    )


__all__ = [
    "CrossRefPipelineError",
    "run_crossref_pipeline",
    "run_crossref_with_cre",
]
