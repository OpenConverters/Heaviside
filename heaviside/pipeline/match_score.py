"""Match-strictness scorecard for crossref components.

Replaces the bare {recommended, partial, exact, no_substitute} enum with
a structured object so downstream consumers (reports, dashboards, review
agents) can see calibrated confidence per substitution.

The scorecard is computed AFTER all guardrails have run, from the
guardrail-adjusted state. It does not mutate status or notes — it
augments each component with a ``match_score`` dict::

    {
      "value_pct_delta":   float | None,   # signed, 0 if exact
      "voltage":           "match" | "upgrade" | "downrate" | "unknown",
      "footprint":         "identical" | "one_size_up" | "one_size_down"
                           | "two_or_more_up" | "two_or_more_down"
                           | "different_class" | "different_mount" | "unknown",
      "technology":        "match" | "compatible" | "regression" | "unknown",
      "overall":           float (0..1),   # weighted heuristic scalar
    }

``overall`` is a soft confidence the customer report can use to assign a
traffic-light icon. Heuristics — tune as needed.

Ported from ``proteus.pipelines.match_score``, adapted to use
Heaviside's TAS reader (``heaviside.catalogue._reader.iter_envelopes``)
for MPN lookups instead of ``proteus.catalogue.index``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from heaviside.pipeline.value_parse import parse_si_value

# ---------------------------------------------------------------------------
# EIA SMD size ordering for footprint classification
# ---------------------------------------------------------------------------

_SMD_SIZES = [
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
]


def _smd_idx(pkg: str) -> int | None:
    """Return the position of *pkg* in the standard EIA SMD ordering."""
    if not pkg:
        return None
    p = pkg.upper()
    for i, s in enumerate(_SMD_SIZES):
        if s in p:
            return i
    return None


def _classify_footprint(src_pkg: str, sub_pkg: str) -> str:
    """Classify the footprint relationship between source and substitute."""
    if not src_pkg or not sub_pkg:
        return "unknown"
    if src_pkg.upper() == sub_pkg.upper():
        return "identical"
    si, ti = _smd_idx(src_pkg), _smd_idx(sub_pkg)
    if si is None and ti is None:
        return "different_class"
    if si is None or ti is None:
        return "different_mount"
    delta = ti - si
    if delta == 0:
        return "identical"
    if delta == 1:
        return "one_size_up"
    if delta == -1:
        return "one_size_down"
    if delta >= 2:
        return "two_or_more_up"
    return "two_or_more_down"


def _value_delta_pct(orig: float | None, sub: float | None) -> float | None:
    """Signed percentage delta between original and substitute values."""
    if orig is None or sub is None or orig == 0:
        return None
    return (sub - orig) / orig * 100.0


# ---------------------------------------------------------------------------
# TAS MPN lookup (linear scan — acceptable for crossref-sized batches)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TAS_DATA_DEFAULT = _REPO_ROOT / "TAS" / "data"


def _lookup_mpn(mpn: str, tas_data_dir: Path | None = None) -> dict[str, Any] | None:
    """Find the raw TAS envelope for *mpn* across all category NDJSON files.

    Returns the first matching envelope dict, or ``None``.  This is a
    brute-force linear scan — fine for the ~dozen parts in a single
    crossref batch, not for bulk queries.
    """
    if not mpn:
        return None
    root = tas_data_dir or _TAS_DATA_DEFAULT
    if not root.is_dir():
        return None

    from heaviside.catalogue._reader import iter_envelopes

    for ndjson_file in root.glob("*.ndjson"):
        try:
            for _lineno, env in iter_envelopes(ndjson_file):
                # Walk common TAS envelope shapes to find the MPN.
                for top_key in ("capacitor", "semiconductor", "resistor", "magnetics", "magnetic"):
                    sub = env.get(top_key)
                    if not isinstance(sub, dict):
                        continue
                    # Semiconductors nest one level deeper.
                    for inner_key in (None, "mosfet", "diode", "igbt"):
                        record = sub if inner_key is None else sub.get(inner_key)
                        if not isinstance(record, dict):
                            continue
                        mi = record.get("manufacturerInfo")
                        if isinstance(mi, dict) and mi.get("reference") == mpn:
                            return env
        except Exception:
            continue
    return None


def _normalize_electrical(elec_raw: Any) -> dict[str, Any]:
    """Coerce a TAS ``electrical`` field to a single dict.

    TAS v2 stores magnetics electrical as a *list* of subtype items (inductor,
    bead, …); v1 and all other categories use a plain dict. For a list, return
    the first item carrying inductance / saturationCurrentPeak (the inductor
    item), else the first dict. This mirrors ``crossref_pipeline._magnetic_elec``
    — without it, ``elec.get(...)`` blows up with
    "'list' object has no attribute 'get'" on every magnetic match score."""
    if isinstance(elec_raw, list):
        for item in elec_raw:
            if isinstance(item, dict) and ("inductance" in item or "saturationCurrentPeak" in item):
                return item
        return elec_raw[0] if elec_raw and isinstance(elec_raw[0], dict) else {}
    return elec_raw if isinstance(elec_raw, dict) else {}


def _extract_electrical(env: dict[str, Any]) -> dict[str, Any]:
    """Drill into a TAS envelope and return the ``electrical`` sub-dict."""
    for top_key in ("capacitor", "semiconductor", "resistor", "magnetics", "magnetic"):
        sub = env.get(top_key)
        if not isinstance(sub, dict):
            continue
        for inner_key in (None, "mosfet", "diode", "igbt"):
            record = sub if inner_key is None else sub.get(inner_key)
            if not isinstance(record, dict):
                continue
            mi = record.get("manufacturerInfo")
            if isinstance(mi, dict):
                di = mi.get("datasheetInfo") or {}
                return _normalize_electrical(di.get("electrical"))
    return {}


def _extract_part(env: dict[str, Any]) -> dict[str, Any]:
    """Drill into a TAS envelope and return the ``part`` sub-dict."""
    for top_key in ("capacitor", "semiconductor", "resistor", "magnetics", "magnetic"):
        sub = env.get(top_key)
        if not isinstance(sub, dict):
            continue
        for inner_key in (None, "mosfet", "diode", "igbt"):
            record = sub if inner_key is None else sub.get(inner_key)
            if not isinstance(record, dict):
                continue
            mi = record.get("manufacturerInfo")
            if isinstance(mi, dict):
                di = mi.get("datasheetInfo") or {}
                return di.get("part") or {}
    return {}


def _coerce_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip().split()[0])
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# Score computation
# ---------------------------------------------------------------------------


def compute_match_score(
    comp: dict,
    src_row: dict,
    sub_envelope: dict[str, Any] | None,
    stress: Any | None = None,
) -> dict:
    """Build a structured match_score for one crossref row.

    Parameters
    ----------
    comp : dict
        The crossref component row (has ``type``, ``substitute_pn``, etc.).
    src_row : dict
        The source BOM row (``value``, ``voltage``, ``package``, etc.).
    sub_envelope : dict | None
        Raw TAS envelope for the substitute part (from ``_lookup_mpn``).
    """
    score: dict = {
        "value_pct_delta": None,
        "voltage": "unknown",
        "footprint": "unknown",
        "technology": "unknown",
        "overall": 0.0,
    }

    src_val = _coerce_float(src_row.get("value"))
    src_volt = _coerce_float(src_row.get("voltage"))
    src_pkg = str(src_row.get("package") or "").strip()

    if not sub_envelope:
        return score

    elec = _extract_electrical(sub_envelope)
    part = _extract_part(sub_envelope)
    sub_pkg = str(part.get("caseCode") or part.get("case") or "").strip()

    # --- Primary value comparison ---
    ctype = (comp.get("type") or src_row.get("type") or "").lower()
    sub_val: float | None = None
    if "cap" in ctype:
        v = elec.get("capacitance")
        sub_val = (v.get("nominal") if isinstance(v, dict) else v) if v is not None else None
    elif "res" in ctype or ctype == "resistor":
        v = elec.get("resistance")
        sub_val = (v.get("nominal") if isinstance(v, dict) else v) if v is not None else None
    elif any(t in ctype for t in ("ind", "transformer", "choke", "ferrite", "magnetic")):
        v = elec.get("inductance")
        sub_val = (v.get("nominal") if isinstance(v, dict) else v) if v is not None else None

    # Source value may be a string like "22uF" — parse to SI.
    if src_val is None:
        src_val = parse_si_value(src_row.get("value"))
    sub_val = _coerce_float(sub_val)

    score["value_pct_delta"] = _value_delta_pct(src_val, sub_val)

    # --- Voltage comparison ---
    sub_volt = _coerce_float(elec.get("ratedVoltage") or elec.get("vdsMax") or elec.get("vrrm"))
    if src_volt is not None and sub_volt is not None and src_volt > 0:
        if abs(sub_volt - src_volt) / src_volt < 0.02:
            score["voltage"] = "match"
        elif sub_volt > src_volt:
            score["voltage"] = "upgrade"
        else:
            score["voltage"] = "downrate"

    # --- Footprint ---
    score["footprint"] = _classify_footprint(src_pkg, sub_pkg)

    # --- Technology (capacitors only for now) ---
    if "cap" in ctype:
        src_tech = (src_row.get("technology") or "").lower()
        sub_tech = (part.get("family") or part.get("subType") or "").lower()
        if src_tech and sub_tech:
            if src_tech == sub_tech:
                score["technology"] = "match"
            elif {src_tech, sub_tech} <= {"mlcc", "ceramic", "polymer"}:
                score["technology"] = "compatible"
            else:
                score["technology"] = "regression"

    # --- Overall weighted scalar ---
    overall = 1.0

    vd = score["value_pct_delta"]
    if vd is not None:
        overall *= max(0.0, 1.0 - min(abs(vd), 30.0) / 30.0)

    voltage_w = {"match": 1.0, "upgrade": 1.0, "downrate": 0.4, "unknown": 0.85}
    overall *= voltage_w.get(score["voltage"], 0.85)

    fp_w = {
        "identical": 1.0,
        "one_size_up": 0.95,
        "one_size_down": 0.95,
        "two_or_more_up": 0.7,
        "two_or_more_down": 0.7,
        "different_class": 0.5,
        "different_mount": 0.2,
        "unknown": 0.85,
    }
    overall *= fp_w.get(score["footprint"], 0.85)

    tech_w = {"match": 1.0, "compatible": 0.9, "regression": 0.4, "unknown": 0.95}
    overall *= tech_w.get(score["technology"], 0.95)

    # Stress margin weighting (from RE simulation)
    if stress:
        stress_margin = 1.0
        sub_volt = _coerce_float(comp.get("substitute_voltage"))
        if sub_volt and stress.v_peak and stress.v_peak > 0:
            margin = sub_volt / stress.v_peak
            if margin < 1.0:
                stress_margin = min(stress_margin, 0.1)
            elif margin < 1.2:
                stress_margin = min(stress_margin, 0.6)
        overall *= stress_margin
        score["stress_voltage_margin"] = (
            round(sub_volt / stress.v_peak, 2) if sub_volt and stress.v_peak else None
        )

    score["overall"] = round(overall, 3)
    return score


def annotate_match_scores(
    crossref_components: list[dict],
    source_bom: list[dict],
    *,
    stress_by_ref: dict | None = None,
    tas_data_dir: Path | None = None,
) -> None:
    """Mutate *crossref_components* in place: add ``match_score`` to each row."""
    src_by_first = {str(s.get("ref_des", "")).split(",")[0].strip(): s for s in (source_bom or [])}
    for c in crossref_components:
        ref = str(c.get("ref_des", "")).split(",")[0].strip()
        src = src_by_first.get(ref) or {}
        pn = (c.get("substitute_pn") or "").strip()
        if not pn or pn == "no_substitute":
            c["match_score"] = {
                "overall": 0.0,
                "voltage": "n/a",
                "footprint": "n/a",
                "technology": "n/a",
                "value_pct_delta": None,
            }
            continue
        env = _lookup_mpn(pn, tas_data_dir=tas_data_dir)
        stress = (stress_by_ref or {}).get(ref)
        c["match_score"] = compute_match_score(c, src, env, stress=stress)


__all__ = [
    "annotate_match_scores",
    "compute_match_score",
]
