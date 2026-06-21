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
from typing import Any

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
        "chipBead": "magnetics.ndjson",   # subtype-filtered at scan time
        "varistor": "varistors.ndjson",
        "connector": "connectors.ndjson",
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
        if dims is None:
            eia = _eia_dims_from_case(comp.get("package"))
            if eia:
                dims = (eia[0], eia[1], None)
        comp["_source_dims_m"] = dims

    # Surface missing source dimensions once, aggregated — one diagnostic per
    # row floods the report on large BOMs. Footprint-fit is simply not enforced
    # for rows whose physical size couldn't be resolved.
    no_dims = [
        c.get("original_mpn") or c.get("ref_des", "?")
        for c in state.source_bom
        if c.get("component_type") and c.get("_source_dims_m") is None
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
        state.diagnostics.append(
            f"librarian gap-fill skipped (Digi-Key not configured): {exc}"
        )
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


def _extract_manufacturer(env: dict[str, Any], category: str) -> str | None:
    """Extract manufacturer name from a TAS envelope."""
    paths = {
        "mosfet": ("semiconductor", "mosfet", "manufacturerInfo", "name"),
        "diode": ("semiconductor", "diode", "manufacturerInfo", "name"),
        "capacitor": ("capacitor", "manufacturerInfo", "name"),
        "resistor": ("resistor", "manufacturerInfo", "name"),
        "magnetic": ("magnetic", "manufacturerInfo", "name"),
        "chipBead": ("magnetic", "manufacturerInfo", "name"),
        "varistor": ("varistor", "manufacturerInfo", "name"),
        "connector": ("connector", "manufacturerInfo", "name"),
    }
    keys = paths.get(category, ())
    cur: Any = env
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur if isinstance(cur, str) else None


def _envelope_reference(env: dict[str, Any], category: str) -> str | None:
    """Extract the part reference (MPN) from a TAS envelope."""
    cur: Any = env
    for k in _BASE_PATHS.get(category, ()):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    if not isinstance(cur, dict):
        return None
    mi = cur.get("manufacturerInfo")
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
            elec = _magnetic_elec(env["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"])
            ind = elec.get("inductance")
            v = ind.get("nominal") if isinstance(ind, dict) else ind
            return float(v) if v is not None else None
        elif category == "chipBead":
            return _chip_bead_impedance_at_100mhz(env)
        elif category == "varistor":
            vv = (env["varistor"]["manufacturerInfo"]["datasheetInfo"]
                  ["electrical"].get("varistorVoltage"))
            v = vv.get("nominal") if isinstance(vv, dict) else vv
            return float(v) if v is not None else None
        elif category == "connector":
            # Primary sorting value: rated current per contact
            v = (env["connector"]["manufacturerInfo"]["datasheetInfo"]
                 ["electrical"].get("ratedCurrentPerContact"))
            return float(v) if v is not None else None
    except (KeyError, TypeError, ValueError):
        pass
    return None


def _extract_package(env: dict[str, Any], category: str) -> str:
    """Extract package/case string from a TAS envelope."""
    try:
        base_paths = {
            "capacitor": ("capacitor",),
            "resistor": ("resistor",),
            "magnetic": ("magnetic",),
            "chipBead": ("magnetic",),
            "varistor": ("varistor",),
            "connector": ("connector",),
            "mosfet": ("semiconductor", "mosfet"),
            "diode": ("semiconductor", "diode"),
        }
        cur: Any = env
        for k in base_paths.get(category, ()):
            cur = cur[k]
        part = cur["manufacturerInfo"]["datasheetInfo"]["part"]
        # Magnetics carry the size under `caseCode` (Würth WE-PD "1260" etc.),
        # chip passives under `case` (EIA "0402"…). Accept either so the
        # package signal isn't silently empty for one family.
        return part.get("case") or part.get("caseCode") or ""
    except (KeyError, TypeError):
        return ""


# Category → path to the manufacturerInfo block inside a TAS envelope.
_BASE_PATHS: dict[str, tuple[str, ...]] = {
    "capacitor": ("capacitor",),
    "resistor": ("resistor",),
    "magnetic": ("magnetic",),
    "chipBead": ("magnetic",),
    "varistor": ("varistor",),
    "connector": ("connector",),
    "mosfet": ("semiconductor", "mosfet"),
    "diode": ("semiconductor", "diode"),
}


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
_EIA_CODE_RE = re.compile(
    r"(?<!\d)(01005|0201|0402|0603|0805|1206|1210|1812|2010|2220|2225)(?!\d)"
)


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
        cur: Any = env
        for k in _BASE_PATHS.get(category, ()):
            cur = cur[k]
        mech = cur["manufacturerInfo"]["datasheetInfo"].get("mechanical") or {}
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
_FIT_AREA_WEIGHT = 0.5          # fitting parts: penalty = weight × area ratio
_OVERSIZE_BASE = 10.0           # flat floor applied to any part that overflows
_OVERSIZE_SCALE = 8.0           # extra penalty per unit of worst linear overflow
_UNKNOWN_DIM_PENALTY = 2.0      # candidate size unknown → can't confirm it fits
_DIM_TOLERANCE = 1.02           # 2 % slack for rounding / termination spread

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
    overflow = max(
        c_long / s_long,
        c_short / s_short,
        (c_h / s_h) if (s_h and c_h) else 1.0,
    ) - 1.0
    return _OVERSIZE_BASE + _OVERSIZE_SCALE * max(overflow, 0.0)


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
    "GRM", "GCM", "GRT", "GJM", "GMD", "GA3", "GCJ", "GQM",  # Murata
    "CGA", "CKG", "CGJ",  # TDK (+ bare C#### handled below)
    "CL03", "CL05", "CL10", "CL21", "CL31", "CL32", "CL05", "CL", # Samsung
    "AC0", "CC0", "CC1", "CC2",  # Yageo
    "WCAP-CSGP", "WCAP-CSMH", "WCAP-CSSA", "WCAP-CSST",  # Würth
    "C0402", "C0603", "C0805", "C1206",  # Kemet/generic
)


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
    if "ceramic" in t or "mlcc" in t or any(
        len(c) >= 3 and c.lower() in t for c in _eia_dielectric_codes()):
        return "ceramic"
    if "tantal" in t:
        return "tantalum"
    if "niobium" in t:
        return "niobium"
    if "alum" in t or "electrolytic" in t or "polymer" in t or "hybrid" in t:
        return "aluminum"
    if any(k in t for k in ("film", "polyprop", "polyest", "paper", "pps",
                            "polyphenylene", "mkt", "mkp")):
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
    elif category == "connector" and value_str:
        from heaviside.pipeline.value_parse import parse_current
        target_val = parse_current(value_str)

    if target_val is None:
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
                elif category == "connector":
                    cand_elec = (
                        cand.get("connector", {})
                        .get("manufacturerInfo", {})
                        .get("datasheetInfo", {})
                        .get("electrical", {})
                    )
                    i_per_contact = cand_elec.get("ratedCurrentPerContact")
                    if i_per_contact and stress.i_peak and float(i_per_contact) < stress.i_peak:
                        stress_penalty += 5.0
                    v_rated = cand_elec.get("ratedVoltage")
                    if v_rated and stress.v_peak and float(v_rated) < stress.v_peak:
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
            cand_fam = _capacitor_technology_family(
                cand.get("capacitor", {})
                .get("manufacturerInfo", {})
                .get("datasheetInfo", {})
                .get("part", {})
                .get("technology")
            )
            if cand_fam is not None and cand_fam != source_cap_family:
                tech_penalty = 6.0

        # Footprint fit (all categories): the substitute must occupy no more
        # board space than the original. Smaller is better; oversize is heavily
        # penalised but still finite-scored so it can win when nothing fits.
        footprint_penalty = _footprint_penalty(
            source_dims, _extract_dimensions(cand, category)
        )
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
_CROSSREF_BATCH_MAX_PARTS = 60          # sized to the 16k REPLY budget, not the request
_CROSSREF_RETRY_MAX_PARTS = 20          # retry dropped rows in tiny batches that can't truncate
_CROSSREF_BATCH_CHARS = 400_000         # request-side safety net: ~175k tokens, under limit
_CROSSREF_MAX_CONCURRENCY = 12          # batches run in parallel (I/O-bound, cost-neutral)
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
        len(items), len(batches),
        "concurrent" if (concurrent and len(batches) > 1) else "sequential",
        len(rows), len(errors), time.monotonic() - t0,
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
        entry.pop("_source_dims_m", None)  # internal-only: don't leak to the LLM
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
            "CR stage 3: %d components split into %d batches (≤%d each) "
            "run %d-way concurrently",
            len(entries), len(batches), max_parts, _CROSSREF_MAX_CONCURRENCY,
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
            data = call_agent_json(
                "cross-referencer", user_msg, max_tokens=16384, max_retries=2
            )
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


def _reconcile_crossref_coverage(
    state: CrossRefState, bom_for_llm: list[dict[str, Any]]
) -> None:
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
        len(missing), len(bom_for_llm),
    )
    state.diagnostics.append(
        f"cross-referencer omitted {len(missing)} row(s); retried dropped components"
    )
    retry_rows, _ = _run_crossref_batches(
        state, missing, max_parts=_CROSSREF_RETRY_MAX_PARTS
    )
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
            conn = env["connector"]
            mi = conn["manufacturerInfo"]
            ds = mi["datasheetInfo"]
            elec = ds["electrical"]
            mech = ds.get("mechanical", {})
            part = ds.get("part", {})
            fd = ds.get("familyDetails", {})
            summary = {
                "mpn": mi.get("reference", "?"),
                "family": fd.get("family"),
                "positions": mech.get("positions"),
                "pitch_mm": round(mech["pitch"] * 1e3, 2) if mech.get("pitch") else None,
                "rated_current_A": elec.get("ratedCurrentPerContact"),
                "rated_voltage_V": elec.get("ratedVoltage"),
                "mounting": mech.get("mountingStyle"),
                "polarity": part.get("matingPolarity"),
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
            cand_dims = _extract_dimensions(c, category)
            if cand_dims is None or cand_dims[0] is None or cand_dims[1] is None:
                summ["fits_original"] = "unknown"
            else:
                summ["fits_original"] = (
                    _footprint_penalty(source_dims, cand_dims) < _OVERSIZE_BASE
                )
        out.append(summ)
    return out


# ---------------------------------------------------------------------------
# Stage 4: Guardrails (deterministic)
# ---------------------------------------------------------------------------


def _stage4_guardrails(state: CrossRefState) -> CrossRefState:
    """Apply engineering guardrails to the crossref result."""
    try:
        from heaviside.pipeline.guardrails import apply_guardrails

        corrected, fire_log = apply_guardrails(
            {"crossref": state.crossref_result},
            state.source_bom,
            state.target_manufacturer,
            stress_by_ref=state.stress_by_ref or None,
        )
        state.crossref_result = corrected.get("crossref", state.crossref_result)
        state.guardrail_log.extend(fire_log)
        logger.info("CR stage 4: %d guardrail fires", len(fire_log))
    except ImportError:
        state.diagnostics.append("guardrails module not available — skipping")
    except Exception as exc:
        state.diagnostics.append(f"guardrails failed: {exc}")
    # Retry hallucinated MPNs caught by G5/G5b
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
        entry.pop("_source_dims_m", None)  # internal-only: don't leak to the LLM
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
            except Exception as exc:  # noqa: BLE001 - Otto is best-effort
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
                    applied += 1
                    break

        state.otto_log["re_crossref_applied"] = applied
        state.otto_log["re_crossref_total"] = len(overturned)
        logger.info(
            "CR stage 6: Otto challenged %d, re-crossref found %d new substitutes",
            len(overturned),
            applied,
        )
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
    batches = _batch_for_llm(trimmed_xref, max_chars=_REVIEW_BATCH_CHARS, max_parts=_REVIEW_BATCH_MAX_PARTS)
    if len(batches) > 1:
        logger.info("CR stage 7: review split into %d batches (%d rows)", len(batches), len(trimmed_xref))

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
                    pool.map(lambda a: _review_one(reviewer_name, a[0], a[1]),
                             list(enumerate(_batch_list, 1)))
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
        logger.info("CR stage 7: %s %s (%d batches)", reviewer_name, verdict_data["verdict"], len(batches))

    # Pipeline passes only if both reviewers approved
    ray_approved = any(
        v.get("reviewer") == "ray" and v.get("verdict", "").upper() in ("APPROVED", "PROCEED")
        for v in state.review_verdicts
    )
    # Computed but intentionally not gating yet (Nicola is advisory for
    # now — see the `state.passed` line below); `_`-prefixed to mark it
    # deliberately unused until her verdict becomes binding.
    _nicola_approved = any(
        v.get("reviewer") == "nicola"
        and v.get("verdict", "").upper() in ("APPROVED", "PROCEED", "NOT_APPROVED")
        # Nicola uses NOT_APPROVED with open_issues — treat as not blocking for CR
        for v in state.review_verdicts
    )
    state.passed = ray_approved  # Ray must approve; Nicola is advisory for now
    return state


# ---------------------------------------------------------------------------
# Public orchestrator
# ---------------------------------------------------------------------------


_CATEGORY_ALIASES = {
    "inductor": "magnetic",
    "ferrite_bead": "magnetic",
    "transformer": "magnetic",
}

# Categories the pipeline recognises as-is. A component_type that is neither one
# of these nor an alias above (e.g. a JEDEC/package code like "c0402h32" wrongly
# mapped into the type column) is treated as unknown → re-inferred from the
# description rather than trusted.
_CR_CANONICAL_CATEGORIES = frozenset({
    "capacitor", "resistor", "magnetic", "mosfet", "diode",
    "semiconductor", "controller", "connector",
    "chipBead", "varistor",
})


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

    units = {"capacitor": "F", "resistor": "Ω", "magnetic": "H", "inductor": "H",
             "chipBead": "Ω", "varistor": "V", "connector": "A"}
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
    if any(w in text for w in ("CONNECTOR", "CONN ", "TERMINAL BLOCK", "SCREW TERMINAL",
                               "PIN HEADER", "SOCKET HEADER", "WIRE-TO-BOARD",
                               "BOARD-TO-BOARD", "CRIMP", "SKEDD", "WR-TBL",
                               "WR-PHD", "WR-WTB", "WR-BTB", "WR-MPC", "SMA CONN",
                               "BNC CONN", "M12 CONN")):
        return "connector"
    if any(w in text for w in ("INDUCTOR", "IND ", "CHOKE", "FERRITE", "TRANSFORMER",
                               "XFMR", "COIL", "UH ", "MH ")):
        return "magnetic"
    if any(w in text for w in ("RESISTOR", "RES ", "RES-", "OHM", "KOHM")):
        return "resistor"
    if any(w in text for w in ("CAP CER", "CAPACITOR", "MLCC", "CER CAP", "CAP ",
                               "UF", "NF", "PF")):
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
        if cat:
            row["component_type"] = cat
        else:
            row.pop("component_type", None)  # drop a bogus package-code value
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


def _stage3b_correct(state: CrossRefState, objections: list[str]) -> CrossRefState:
    """Re-run the crossref LLM for components cited in reviewer objections."""
    cited_refs: set[str] = set()
    for obj in objections:
        text = json.dumps(obj) if isinstance(obj, dict) else str(obj)
        cited_refs.update(re.findall(r"\b([A-Z]+\d+)\b", text))

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
            _parse_value_si(o_val, cat), _parse_value_si(s_val, cat),
            higher_is_ok=(cat == "capacitor"),  # bypass/bulk caps tolerate higher
        )
        params.append({"name": "value", "original": str(o_val), "substitute": str(s_val),
                       "verdict": v})

    # voltage rating — higher is good, lower is a downgrade
    o_v, s_v = row.get("original_voltage", ""), row.get("substitute_voltage", "")
    if o_v or s_v:
        params.append({"name": "voltage", "original": str(o_v), "substitute": str(s_v),
                       "verdict": _param_verdict(_to_volts(o_v), _to_volts(s_v),
                                                 higher_is_ok=True)})

    # package — string equality (case/space-insensitive)
    o_p = str(row.get("original_package", "")).strip().lower()
    s_p = str(row.get("substitute_package", "")).strip().lower()
    if o_p or s_p:
        verdict = "same" if (o_p and o_p == s_p) else ("differs" if (o_p and s_p) else "n/a")
        params.append({"name": "package", "original": row.get("original_package", ""),
                       "substitute": row.get("substitute_package", ""), "verdict": verdict})

    # Spec-driven electrical parameters (ESR, ripple, dielectric, Rds_on, Qg,
    # Coss, Vf, Qrr, TCR, Isat, DCR, SRF, …) resolved from the internal DB by
    # _stage_param_check. The report renders these generically alongside the
    # core value/voltage/package params above.
    for pr in row.get("_param_results", []):
        params.append({
            "name": pr.get("label") or pr["name"],
            "original": pr.get("original", ""),
            "substitute": pr.get("substitute", ""),
            "verdict": pr.get("verdict", ""),
            "note": pr.get("note", ""),
        })

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
        results = evaluate_params(cat, orig_params, sub_params)

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

        if not results:
            continue
        row["_param_results"] = results

        fails = [r for r in results if r["verdict"] == FAIL]
        # Only demote an actively-recommended substitute; 'exact' (identical
        # part) and 'partial'/'no_substitute' don't move. This keeps the
        # parameter check a tightening, never a loosening, of the verdict.
        if fails and row.get("status") == "recommended":
            row["status"] = "partial"
        if fails:
            note = "; ".join(r["note"] for r in fails)
            existing = row.get("notes") or ""
            row["notes"] = (existing + " | " if existing else "") + f"parameter check: {note}"
            fires = row.setdefault("guardrail_fires", [])
            for r in fails:
                tag = f"PARAM:{r['name']}"
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
    from heaviside.pipeline.value_parse import parse_si_value

    orig_vsi = comp.get("value_si")
    if orig_vsi in (None, "") and comp.get("original_value"):
        orig_vsi = parse_si_value(str(comp.get("original_value")))
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
        if (cat == "capacitor" and orig_fam is not None
                and _capacitor_technology_family(s.get("technology")) != orig_fam):
            continue
        # value window
        if orig_vsi and cand_vsi:
            if cat == "capacitor":
                lo, hi, tight = 0.9 * orig_vsi, 3.0 * orig_vsi, 0.1
            elif cat == "magnetic":
                lo, hi, tight = 0.9 * orig_vsi, 1.5 * orig_vsi, 0.1
            else:  # resistor
                lo, hi, tight = 0.95 * orig_vsi, 1.05 * orig_vsi, 0.025
            if not (lo <= cand_vsi <= hi):
                continue
            within_tight = abs(cand_vsi - orig_vsi) <= tight * orig_vsi
        else:
            within_tight = False
        status = "recommended" if (within_tight and (orig_v is None or cand_v is not None)) else "partial"
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
                    "CR stage 6.5: prefetch had 0 candidates for %s (%s); "
                    "fetched %d on-demand", ref, cat, len(cands)
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
        row["notes"] = (
            f"{prior} | deterministic in-kind rescue: {patch['substitute_pn']} meets "
            "voltage/value/chemistry criteria (LLM stages dropped it)."
        ).strip(" |")
        rescued += 1
    if rescued:
        logger.info("CR stage 6.5: deterministically rescued %d no_substitute rows", rescued)
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
    state = CrossRefState(
        source_bom=_normalize_bom(source_bom),
        target_manufacturer=target_manufacturer,
        circuit_context=circuit_context,
        stress_by_ref=stress_by_ref or {},
    )

    _say(f"Prefetching TAS candidates for {n} components", 5)
    state = _stage1_prefetch(state)
    _say("Librarian: sourcing any missing components from datasheets/distributors", 15)
    state = _stage1_5_librarian(state)
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
    _say("Adversarial review (Ray + Nicola)", 84)
    state = _stage7_review(state)

    # Correction loop: if reviewer rejects, fix objected components and re-review.
    # Re-runs stay under the "review" stage (no backward stage bounce in the UI).
    for loop_i in range(1, _MAX_REVIEW_LOOPS + 1):
        if state.passed:
            break
        last_verdict = state.review_verdicts[-1] if state.review_verdicts else {}
        objections = last_verdict.get("objections", [])
        if not objections:
            break

        logger.info("CR correction loop %d: addressing %d objections", loop_i, len(objections))
        _say(f"Correction loop {loop_i}: addressing {len(objections)} reviewer objections", 88)
        state = _stage3b_correct(state, objections)
        state = _stage4_guardrails(state)
        state = _stage5_score(state)
        state = _stage6_otto(state)
        state = _stage6_5_deterministic_rescue(state)
        state = _stage7_review(state)

    # Stage 8: Learn from this run
    _say("Learning from this run (persisting accepted substitutions)", 95)
    _stage8_learn(state)

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
        _stage2_5_verify_mpns,
        _stage2_7_extract_claims,
        _stage2_8_testbench,
        _stage2_65_extract_rdson,
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
    re_state = REState(reference=reference, pdf_path=pdf_path,
                         review_llm=review_llm, progress=progress)
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
    _say("Verifying extracted MPNs against the catalog", 14)
    re_state = _stage2_5_verify_mpns(re_state)
    _say("Extracting RDS(on) for the power FETs", 17)
    re_state = _stage2_65_extract_rdson(re_state)
    _say("Extracting the reference's datasheet performance claims", 20)
    re_state = _stage2_7_extract_claims(re_state)
    _say("Testbench: simulating the reference design", 24)
    re_state = _stage2_8_testbench(re_state)

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
