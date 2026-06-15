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
    }

    target_mfr_lower = _normalize_manufacturer(state.target_manufacturer)

    # Which categories does the BOM actually need?
    needed_cats: set[str] = set()
    for comp in state.source_bom:
        cat = comp.get("component_type", comp.get("category", ""))
        if cat in category_files:
            needed_cats.add(cat)

    # Scan each needed NDJSON file ONCE, collect all target-mfr rows
    mfr_cache: dict[str, list[dict[str, Any]]] = {}
    for cat in needed_cats:
        fname = category_files[cat]
        path = tas_dir / fname
        if not path.exists():
            mfr_cache[cat] = []
            continue
        rows: list[dict[str, Any]] = []
        try:
            for _lineno, env in iter_envelopes(path):
                mfr_name = _extract_manufacturer(env, cat)
                if mfr_name and target_mfr_lower in _normalize_manufacturer(mfr_name):
                    rows.append(env)
        except CatalogueReadError:
            pass
        mfr_cache[cat] = rows
        logger.info(
            "CR prefetch: %s has %d %s candidates", state.target_manufacturer, len(rows), cat
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
        state.diagnostics.append(f"librarian: cannot init Digi-Key client: {exc}")
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


def _extract_manufacturer(env: dict[str, Any], category: str) -> str | None:
    """Extract manufacturer name from a TAS envelope."""
    paths = {
        "mosfet": ("semiconductor", "mosfet", "manufacturerInfo", "name"),
        "diode": ("semiconductor", "diode", "manufacturerInfo", "name"),
        "capacitor": ("capacitor", "manufacturerInfo", "name"),
        "resistor": ("resistor", "manufacturerInfo", "name"),
        "magnetic": ("magnetic", "manufacturerInfo", "name"),
    }
    keys = paths.get(category, ())
    cur: Any = env
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur if isinstance(cur, str) else None


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
            elec = env["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"]
            ind = elec.get("inductance")
            v = ind.get("nominal") if isinstance(ind, dict) else ind
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
            "mosfet": ("semiconductor", "mosfet"),
            "diode": ("semiconductor", "diode"),
        }
        cur: Any = env
        for k in base_paths.get(category, ()):
            cur = cur[k]
        return cur["manufacturerInfo"]["datasheetInfo"]["part"].get("case", "")
    except (KeyError, TypeError):
        return ""


VOLTAGE_DERATING_FACTOR = 1.25
DIODE_VOLTAGE_DERATING = 1.50
SATURATION_MARGIN = 0.90
CURRENT_DERATING_FACTOR = 1.25


_EIA_DIELECTRIC_CODES = (
    # Class I
    "C0G", "NP0", "U2J",
    # Class II/III — [X/Y/Z low-temp][4-8 high-temp][R/S/T/U/V tolerance]. All
    # are ceramic; the T-tolerance variants (e.g. X7T = 125C, +22/-33%) were
    # missing, so X7T caps fell out of the 'ceramic' family and got the
    # cross-chemistry penalty vs Würth X7R candidates (the um3491 regression).
    "X5R", "X5S", "X5T",
    "X6R", "X6S", "X6T",
    "X7R", "X7S", "X7T",
    "X8R", "X8S", "X8L", "X8G", "X8M",
    "Y5V", "Y5U", "Z5U",
)
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
        c.lower() in t for c in _EIA_DIELECTRIC_CODES):
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
    if any(code in blob for code in _EIA_DIELECTRIC_CODES):
        return "ceramic"
    part = str(comp.get("part") or comp.get("mpn") or "").upper().strip()
    if part.startswith(_CERAMIC_MPN_PREFIXES):
        return "ceramic"
    # bare TDK ceramic chip: C + 4 digits (C1608, C2012, C3216)
    if len(part) >= 5 and part[0] == "C" and part[1:5].isdigit():
        return "ceramic"
    return None


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

    target_val: float | None = None
    if category == "capacitor" and value_str:
        target_val = parse_capacitance(value_str)
    elif category == "resistor" and value_str:
        target_val = parse_resistance(value_str)
    elif category == "magnetic" and value_str:
        target_val = parse_inductance(value_str)

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
        # For capacitors: higher is OK (bypass/bulk), lower is bad
        if category == "capacitor" and ratio >= 1.0:
            val_dist = (ratio - 1.0) * 0.3  # mild penalty for overcap
        else:
            val_dist = abs(1.0 - ratio)
        # Package: same=0.0, one size up=0.3, two sizes up=0.8
        cand_pkg = _extract_package(cand, category).lower()
        if package and package in cand_pkg:
            pkg_penalty = 0.0
        elif _is_one_size_up(package, cand_pkg):
            pkg_penalty = 0.3
        else:
            pkg_penalty = 0.8
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
        # Stress-based penalties (from CRE simulation)
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
                    cand_elec = (
                        cand.get("magnetic", {})
                        .get("manufacturerInfo", {})
                        .get("datasheetInfo", {})
                        .get("electrical", {})
                    )
                    isat = cand_elec.get("saturationCurrentPeak")
                    if isat and stress.i_peak and float(isat) < stress.i_peak:
                        stress_penalty += 5.0
                    i_rated = cand_elec.get("ratedCurrent")
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

        score = val_dist + pkg_penalty + voltage_penalty + stress_penalty + tech_penalty
        scored.append((score, cand))

    scored.sort(key=lambda x: x[0])
    return [c for _, c in scored[:max_results]]


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


def _stage3_crossref(state: CrossRefState) -> CrossRefState:
    """Call the cross-referencer agent with constrained candidates."""
    bom_for_llm = []
    for comp in state.source_bom:
        ref = comp.get("ref_des", comp.get("name", "?"))
        if ref in state.preclassified:
            continue
        entry = dict(comp)
        candidates = state.candidates_by_ref.get(ref, [])
        if candidates:
            entry["_tas_candidates"] = [
                _summarize_candidate(c, comp.get("component_type", "")) for c in candidates[:10]
            ]
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

    if not bom_for_llm:
        state.diagnostics.append("all components pre-classified, nothing to crossref")
        return state

    user_msg = json.dumps(
        {
            "source_bom": bom_for_llm,
            "target_manufacturer": state.target_manufacturer,
            "circuit_context": state.circuit_context,
        },
        indent=2,
    )

    try:
        data = call_agent_json("cross-referencer", user_msg, max_tokens=16384, max_retries=2)
    except LLMCallError as exc:
        state.diagnostics.append(f"cross-referencer agent failed after retries: {exc}")
        return state

    state.crossref_result = data.get("crossref", [])

    # Merge pre-classified rows back in
    for ref, info in state.preclassified.items():
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

    logger.info("CR stage 3: crossref produced %d rows", len(state.crossref_result))
    return state


def _summarize_candidate(env: dict[str, Any], category: str) -> dict[str, Any]:
    """Extract a brief summary from a TAS envelope for the LLM."""
    summary: dict[str, Any] = {}
    if category == "mosfet":
        try:
            m = env["semiconductor"]["mosfet"]
            mi = m["manufacturerInfo"]
            elec = mi["datasheetInfo"]["electrical"]
            summary = {
                "mpn": mi.get("reference", "?"),
                "vds": elec.get("drainSourceVoltage"),
                "rds_on": elec.get("onResistance"),
                "id": elec.get("continuousDrainCurrent"),
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
                "package": mi.get("datasheetInfo", {}).get("part", {}).get("case", ""),
            }
        except (KeyError, TypeError):
            pass
    elif category == "magnetic":
        try:
            m = env["magnetic"]
            mi = m["manufacturerInfo"]
            elec = mi["datasheetInfo"]["electrical"]
            ind = elec.get("inductance")
            ind_val = ind.get("nominal") if isinstance(ind, dict) else ind
            summary = {
                "mpn": mi.get("reference", "?"),
                "inductance": ind_val,
                "saturation_current": elec.get("saturationCurrent"),
                "dcr": elec.get("dcResistance"),
                "package": mi.get("datasheetInfo", {}).get("part", {}).get("case", ""),
            }
        except (KeyError, TypeError):
            pass
    return summary


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
        candidates = state.candidates_by_ref.get(ref, [])
        if candidates:
            entry["_tas_candidates"] = [
                _summarize_candidate(c, comp.get("component_type", "")) for c in candidates[:10]
            ]
        retry_bom.append(entry)

    if not retry_bom:
        return state

    retry_msg = json.dumps(
        {
            "source_bom": retry_bom,
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
        indent=2,
    )

    try:
        data = call_agent_json(
            "cross-referencer",
            retry_msg,
            max_tokens=8192,
            max_retries=1,
        )
    except LLMCallError as exc:
        state.diagnostics.append(f"G5 retry failed: {exc}")
        return state

    retried = data.get("crossref", [])
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
        otto_tokens = 8192 + len(trimmed) * 256
        raw = call_agent(
            "otto",
            json.dumps(
                {
                    "target_manufacturer": state.target_manufacturer,
                    "no_substitute_items": trimmed,
                },
                indent=2,
            ),
            max_tokens=min(otto_tokens, 16384),
        )
        data = extract_json_block(raw)

        state.otto_log = {
            "raw_response": raw,
            "challenges": data.get("challenges", []),
            "summary": data.get("summary", {}),
        }

        # Collect Otto's diagnoses as hints for the cross-referencer
        overturned = [c for c in data.get("challenges", []) if c.get("verdict") == "OVERTURNED"]
        confirmed = [c for c in data.get("challenges", []) if c.get("verdict") == "CONFIRMED"]

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
            candidates = state.candidates_by_ref.get(ref, [])
            if candidates:
                entry["_tas_candidates"] = [
                    _summarize_candidate(c, comp.get("component_type", "")) for c in candidates[:15]
                ]
            hints.append(entry)

        hint_msg = json.dumps(
            {
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
                "components_to_retry": hints,
                "target_manufacturer": state.target_manufacturer,
            },
            indent=2,
        )

        try:
            retry_data = call_agent_json(
                "cross-referencer",
                hint_msg,
                max_tokens=8192,
                max_retries=1,
            )
        except LLMCallError as exc:
            state.diagnostics.append(f"Otto re-crossref failed: {exc}")
            logger.info(
                "CR stage 6: Otto challenged %d items but re-crossref failed", len(overturned)
            )
            return state

        # Apply successful re-crossrefs
        retried = retry_data.get("crossref", retry_data.get("components", []))
        if not isinstance(retried, list):
            retried = []

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
    review_input = {
        "crossref": trimmed_xref,
        "target_manufacturer": state.target_manufacturer,
        "total_components": len(state.source_bom),
        "guardrail_fires": state.guardrail_log,
    }

    # Run both reviewers. Per CLAUDE.md "no silent fallbacks": a reviewer that
    # cannot produce a verdict (LLM unreachable/timeout/unparseable even after
    # retries) is a HARD failure — a cross-reference without its Ray+Nicola
    # review is not a valid result, so raise rather than appending a diagnostic
    # and proceeding. A reviewer that runs and returns NOT_APPROVED is a valid
    # review (recorded below, drives state.passed), not a failure.
    review_tokens = min(8192 + len(trimmed_xref) * 128, 16384)
    for reviewer_name in ("ray", "nicola"):
        try:
            verdict_data = call_agent_json(
                reviewer_name,
                "[SCOPE: CROSS-REFERENCE — component substitution validity only "
                "(electrical/thermal equivalence, footprint, ratings of the "
                "proposed replacements vs the originals). Full converter design "
                "phases — control loop, gate drive, protection, EMI, PCB — are "
                "OUT OF SCOPE.]\n\n"
                f"CROSS-REFERENCE REVIEW\n\n{json.dumps(review_input, indent=2)}",
                max_tokens=review_tokens,
                max_retries=max_attempts,
                json_mode=True,
            )
            verdict_data = normalize_reviewer_verdict(verdict_data, reviewer_name)
        except LLMCallError as exc:
            raise CrossRefPipelineError(
                f"CR stage 7: reviewer {reviewer_name!r} could not produce a "
                f"valid verdict ({exc}). A cross-reference without its Ray+Nicola "
                f"review is not a valid result — aborting (no silent fallback)."
            ) from exc
        verdict_data["reviewer"] = reviewer_name
        state.review_verdicts.append(verdict_data)
        state.reviewer_log += f"\n--- {reviewer_name.upper()} ---\n{json.dumps(verdict_data)}\n"
        logger.info("CR stage 7: %s %s", reviewer_name, verdict_data.get("verdict", "?"))

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

    units = {"capacitor": "F", "resistor": "Ω", "magnetic": "H", "inductor": "H"}
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
    for comp in bom:
        row = dict(comp)
        for old_key, new_key in _FIELD_MAP.items():
            if old_key in row and new_key not in row:
                row[new_key] = row[old_key]
        cat = row.get("component_type", "")
        if cat in _CATEGORY_ALIASES:
            row["component_type"] = _CATEGORY_ALIASES[cat]
        # Convert raw SI values to human-readable for the LLM
        val = row.get("value", "")
        if val and cat:
            row["value"] = _humanize_value(val, cat)
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
            candidates = state.candidates_by_ref.get(ref, [])
            if candidates:
                entry["_tas_candidates"] = [
                    _summarize_candidate(c, row.get("component_type", "")) for c in candidates[:15]
                ]
            to_fix.append(entry)

    if not to_fix:
        return state

    user_msg = json.dumps(
        {
            "task": "CORRECTION — fix reviewer objections",
            "objections": objections,
            "components_to_fix": to_fix,
            "target_manufacturer": state.target_manufacturer,
            "instructions": (
                "The reviewer rejected these substitutions. For each component, "
                "either find a better substitute from _tas_candidates that "
                "addresses the objection, or change status to no_substitute "
                "with a note explaining why no fix is possible. "
                "Respond with the same JSON crossref format."
            ),
        },
        indent=2,
    )

    try:
        data = call_agent_json("cross-referencer", user_msg, max_tokens=8192, max_retries=1)
    except LLMCallError as exc:
        state.diagnostics.append(f"correction crossref failed: {exc}")
        return state

    corrections = data.get("crossref", data.get("components", []))
    if not isinstance(corrections, list):
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


def run_crossref_pipeline(
    source_bom: list[dict[str, Any]],
    target_manufacturer: str,
    *,
    circuit_context: str | None = None,
    stress_by_ref: dict[str, Any] | None = None,
    verbose: bool = False,
) -> CrossRefOutcome:
    """Run the full CR pipeline end-to-end.

    When ``stress_by_ref`` is provided (from CRE simulation), candidates
    are ranked and guardrails are applied using actual per-component
    voltage and current stress instead of static BOM specs.
    """
    state = CrossRefState(
        source_bom=_normalize_bom(source_bom),
        target_manufacturer=target_manufacturer,
        circuit_context=circuit_context,
        stress_by_ref=stress_by_ref or {},
    )

    state = _stage1_prefetch(state)
    state = _stage1_5_librarian(state)
    state = _stage2_preclassify(state)
    state = _stage3_crossref(state)
    state = _stage4_guardrails(state)
    state = _stage5_score(state)
    state = _stage6_otto(state)
    state = _stage7_review(state)

    # Correction loop: if reviewer rejects, fix objected components and re-review
    for loop_i in range(1, _MAX_REVIEW_LOOPS + 1):
        if state.passed:
            break
        last_verdict = state.review_verdicts[-1] if state.review_verdicts else {}
        objections = last_verdict.get("objections", [])
        if not objections:
            break

        logger.info("CR correction loop %d: addressing %d objections", loop_i, len(objections))
        state = _stage3b_correct(state, objections)
        state = _stage4_guardrails(state)
        state = _stage5_score(state)
        state = _stage6_otto(state)
        state = _stage7_review(state)

    # Stage 8: Learn from this run
    _stage8_learn(state)

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
) -> CrossRefOutcome:
    """CRE-fronted cross-reference: simulate first, then crossref with stress.

    Runs CRE stages 0→2.8 to extract specs, BOM, and simulate the
    reference design. Then extracts per-component V/I stress from the
    simulation and feeds it into the CR pipeline for stress-informed
    ranking, guardrails, and scoring.

    When ``source_bom_override`` is provided (e.g. from a pre-extracted
    Proteus BOM), the CR pipeline uses that instead of the CRE-extracted
    BOM. The CRE BOM is still used for simulation (power-path components).
    """
    from heaviside.pipeline.cre import CREState
    from heaviside.pipeline.cre_pipeline import (
        _stage0_extract_pdf,
        _stage1_competitor,
        _stage2_5_verify_mpns,
        _stage2_7_extract_claims,
        _stage2_8_testbench,
        _stage2_65_extract_rdson,
        _stage2_reverse_engineer,
    )
    from heaviside.pipeline.cre_testbench import extract_component_stress

    # --- CRE stages: extract and simulate ---
    cre_state = CREState(reference=reference, pdf_path=pdf_path)
    if pdf_text is not None:
        # Pre-extracted text (e.g. an HTML app-note fetched from a URL):
        # seed it directly so stage 0 skips PDF extraction.
        cre_state.pdf_text = pdf_text
    cre_state = _stage0_extract_pdf(cre_state)
    cre_state = _stage1_competitor(cre_state)
    cre_state = _stage2_reverse_engineer(cre_state)
    cre_state = _stage2_5_verify_mpns(cre_state)
    cre_state = _stage2_65_extract_rdson(cre_state)
    cre_state = _stage2_7_extract_claims(cre_state)
    cre_state = _stage2_8_testbench(cre_state)

    # --- Bridge: extract per-component stress ---
    stress_by_ref = extract_component_stress(cre_state)
    logger.info("CRE→CR bridge: %d components have simulation stress data", len(stress_by_ref))

    # --- CR pipeline with stress data ---
    # Use pre-extracted BOM if provided (more complete than LLM extraction),
    # otherwise use the CRE-extracted BOM.
    if source_bom_override:
        source_bom = _normalize_bom(source_bom_override)
        logger.info(
            "CRE→CR: using provided BOM (%d components) instead of CRE-extracted (%d)",
            len(source_bom),
            len(cre_state.ref_bom),
        )
    else:
        source_bom = _normalize_bom(cre_state.ref_bom)

    # Build circuit context from CRE spec
    ctx = ""
    if cre_state.ref_spec:
        s = cre_state.ref_spec
        ctx = (
            f"Topology: {s.topology}, Vin={s.vin_nom}V, "
            f"Vout={s.vout}V, Iout={s.iout}A, fsw={s.fsw / 1e3:.0f}kHz"
        )

    return run_crossref_pipeline(
        source_bom,
        target_manufacturer,
        circuit_context=ctx,
        stress_by_ref=stress_by_ref,
        verbose=verbose,
    )


__all__ = [
    "CrossRefPipelineError",
    "run_crossref_pipeline",
    "run_crossref_with_cre",
]
