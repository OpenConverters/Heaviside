"""Crossref decisions made by Jev (closed choices), with Kimi kept for prose.

Three decisions in the crossref pipeline are closed choices and go to Jev
(:mod:`heaviside.llm.jev`):

* **Stage 3 pick** — which of the ≤10 catalogue candidates replaces the
  original (or none), then which status (exact / recommended / partial).
  Rows without catalogue candidates still go to the Kimi cross-referencer:
  Jev can only choose, never propose a part.
* **Otto triage** — which ``no_substitute`` rows are worth Otto's challenge.
  Otto himself (writing the challenge) stays on Kimi.
* **Stage 7 review gate** — one yes/no question per compared parameter;
  a row where no parameter deviates (P ≤ threshold) is cleared, everything
  else still goes to Ray on Kimi. The threshold (0.25) was picked on a
  labelled training split of 79 real crossref rows and missed no bad part on
  the 48-row held-out split.

``HEAVISIDE_JEV=0`` switches every Jev decision off (the pre-Jev behaviour),
for A/B comparisons. With it on (the default) a Jev failure raises — there is
no silent fall-through to Kimi.
"""

from __future__ import annotations

import json
import logging
import math
import os
from typing import Any

from heaviside.llm.jev import JevError, choice_question, decide, noul_question, noul_values

logger = logging.getLogger(__name__)

#: P(parameter deviates) above which a row is sent to Ray (tuned, see module doc).
REVIEW_GATE_THRESHOLD: float = 0.25

_STATUS_OPTIONS: dict[str, str] = {
    "exact": ("The chosen substitute IS the original part: the same manufacturer part number, "
              "or the original is already made by the target manufacturer."),
    "recommended": ("The chosen substitute meets or exceeds every requirement of the original: same "
                    "value within tolerance, equal or higher voltage/current/temperature ratings, "
                    "equal or lower losses, and it fits the original's footprint."),
    "partial": ("The chosen substitute meets the critical requirements but has a minor gap the "
                "engineer must check: a slightly different value, a marginal rating, a larger "
                "footprint that needs verification, or a missing datasheet parameter."),
}

_UNITS = {
    "capacitance": "F", "inductance": "H", "resistance": "Ω", "impedance_100mhz": "Ω",
    "vds": "V", "vrrm": "V", "voltage": "V", "rated_voltage": "V", "vf": "V",
    "vgs_threshold_max": "V", "rds_on": "Ω", "dcr": "Ω", "esr": "Ω", "id": "A",
    "if_avg": "A", "isat": "A", "irms": "A", "rated_current": "A", "current": "A",
    "qg": "C", "qrr": "C", "coss": "F", "trr": "s", "power": "W", "frequency": "Hz",
}
_PREFIX = [(1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""), (1e-3, "m"), (1e-6, "µ"),
           (1e-9, "n"), (1e-12, "p")]


def jev_enabled() -> bool:
    return os.environ.get("HEAVISIDE_JEV", "1") != "0"


def _eng(x: float, unit: str) -> str:
    if x == 0 or not math.isfinite(x):
        return f"{x:g}{unit}"
    for scale, p in _PREFIX:
        if abs(x) >= scale:
            return f"{x / scale:.4g}{p}{unit}"
    return f"{x:.3g}{unit}"


def _readable(obj: Any, key: str = "") -> Any:
    """Engineering notation for known SI quantities (presentation only)."""
    if isinstance(obj, dict):
        if set(obj) & {"nominal", "minimum", "maximum"} and key in _UNITS:
            return {k: _readable(v, key) for k, v in obj.items()}
        return {k: _readable(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_readable(v, key) for v in obj]
    if isinstance(obj, (int, float)) and not isinstance(obj, bool) and key in _UNITS:
        return _eng(float(obj), _UNITS[key])
    return obj


def _original_view(entry: dict[str, Any]) -> dict[str, Any]:
    """The original part as Jev sees it: the BOM row minus the candidate list."""
    view = {k: v for k, v in entry.items() if k != "_tas_candidates" and v not in (None, "", [], {})}
    return _readable(view)


# ---------------------------------------------------------------------------
# Stage 3: pick among catalogue candidates, then the status
# ---------------------------------------------------------------------------

def jev_crossref_row(entry: dict[str, Any], target_manufacturer: str,
                     circuit_context: Any = None) -> dict[str, Any]:
    """Build one crossref row for ``entry`` from its ``_tas_candidates`` via Jev."""
    cands = entry.get("_tas_candidates") or []
    if not cands:
        raise JevError(f"{entry.get('ref_des')}: no catalogue candidates for Jev to choose from")
    original = _original_view(entry)
    options = {f"c{i}": "Candidate " + json.dumps(_readable(c), ensure_ascii=False, default=str)
               for i, c in enumerate(cands)}
    options["none"] = ("None of the listed candidates can replace the original: each one has a "
                       "different primary value beyond tolerance, a lower voltage or current rating, "
                       "a different function or identity, or does not fit the footprint "
                       "(`fits_original` false).")
    state = {"original": original, "target_manufacturer": target_manufacturer}
    if circuit_context:
        state["circuit_context"] = circuit_context
    pick = decide(state, {"pick": choice_question(
        "Choose the catalogue candidate that best replaces the `original` part as a drop-in: "
        "same primary value (capacitance, resistance, inductance, or function for ICs/connectors), "
        "equal or higher voltage/current ratings (and at least `_sim_stress.V_rated_min` when given), "
        "same or better dielectric/tolerance, and `fits_original` true when present. Prefer an "
        "exact match over a better-rated one.", options)})["pick"]
    key = pick.get("choice")
    if key not in options:
        raise JevError(f"{entry.get('ref_des')}: Jev chose {key!r}, not a listed candidate")
    p_pick = float((pick.get("probabilities") or {}).get(key, 0.0))
    base = {
        "ref_des": entry.get("ref_des", entry.get("name")),
        "component_type": entry.get("component_type", ""),
        "original_pn": entry.get("original_mpn") or entry.get("mpn") or "",
        "original_value": entry.get("value", ""),
        "original_voltage": entry.get("voltage", ""),
        "original_package": entry.get("package", ""),
    }
    if key == "none":
        return {**base, "substitute_pn": None, "substitute_value": "", "substitute_voltage": "",
                "substitute_package": "", "status": "no_substitute",
                "notes": f"None of the {len(cands)} catalogue candidates is an acceptable substitute "
                         f"(decision model, P={p_pick:.2f})."}
    chosen = cands[int(key[1:])]
    mpn = chosen.get("mpn")
    if not mpn or mpn == "?":
        raise JevError(f"{base['ref_des']}: chosen candidate {key} has no MPN")
    st = decide({**state, "substitute": _readable(chosen)}, {"status": choice_question(
        "The `substitute` was chosen to replace the `original`. Classify how well it replaces it.",
        _STATUS_OPTIONS)})["status"]
    status = st.get("choice")
    if status not in _STATUS_OPTIONS:
        raise JevError(f"{base['ref_des']}: Jev status {status!r} is not a valid status")
    return {**base, "substitute_pn": mpn,
            "substitute_value": "", "substitute_voltage": "",
            "substitute_package": chosen.get("package", ""),
            "status": status,
            "notes": f"Chosen from {len(cands)} catalogue candidates by the decision model "
                     f"(P={p_pick:.2f}); status {status} "
                     f"(P={float((st.get('probabilities') or {}).get(status, 0.0)):.2f})."}


# ---------------------------------------------------------------------------
# Stage 6: which no_substitute rows are worth Otto's challenge
# ---------------------------------------------------------------------------

def jev_otto_triage(rows: list[dict[str, Any]], target_manufacturer: str) -> list[dict[str, Any]]:
    """Return the subset of ``no_substitute`` rows worth sending to Otto."""
    keep: list[dict[str, Any]] = []
    for row in rows:
        a = decide({"row": _readable(row), "target_manufacturer": target_manufacturer},
                   {"worth": noul_question(
                       "This BOM part was left without a substitute from the target manufacturer. "
                       "Is it worth a second, broader catalogue search?",
                       true=("Yes: it is a common passive or discrete part (capacitor, resistor, "
                             "inductor, ferrite, diode, MOSFET, connector) that the target "
                             "manufacturer plausibly makes, and the notes suggest the search was "
                             "too narrow (value, package or rating filter)."),
                       false=("No: the part is not fitted, is an IC or module the target does not "
                              "make, has no identifiable original, or the notes show the target "
                              "genuinely has no such part."))})
        if noul_values(a, ["worth"])["worth"] > 0.5:
            keep.append(row)
    return keep


# ---------------------------------------------------------------------------
# Stage 7: per-parameter review gate in front of Ray
# ---------------------------------------------------------------------------

def jev_review_gate(row: dict[str, Any],
                    params: list[dict[str, Any]]) -> tuple[bool, dict[str, float]]:
    """``(cleared, P(deviates) per parameter)`` for one substituted row.

    ``params`` are ``build_match_detail`` entries (name/original/substitute);
    their deterministic verdicts are deliberately NOT shown to Jev. The state
    has exactly the shape the threshold was tuned on — change it and retune.
    """
    if not params:
        return False, {}
    view = [{"name": p["name"], "original": p.get("original", ""),
             "substitute": p.get("substitute", "")} for p in params]
    qs = {f"p{i}": noul_question(
        f"Compare the parameter `{p['name']}`: original `{p['original']}`, "
        f"substitute `{p['substitute']}`.",
        true=("The substitute deviates on this parameter: a different value beyond normal "
              "tolerance, a lower rating, a higher DCR/ESR, a worse dielectric, or a different "
              "package size."),
        false=("The substitute matches or beats the original on this parameter: same value within "
               "tolerance, equal-or-higher rating, equal-or-lower DCR/ESR, same or better "
               "dielectric, or the same package size under another name."))
        for i, p in enumerate(view)}
    ans = decide({"ref_des": row.get("ref_des"), "component_type": row.get("component_type"),
                  "original_pn": row.get("original_pn") or row.get("original_mpn"),
                  "substitute_pn": row.get("substitute_pn"), "parameters": view}, qs)
    probs = noul_values(ans, list(qs))
    by_name = {view[int(k[1:])]["name"]: v for k, v in probs.items()}
    return all(v <= REVIEW_GATE_THRESHOLD for v in by_name.values()), by_name


# ---------------------------------------------------------------------------
# Stage 6: a broadened catalogue search + Jev pick, before Kimi's Otto
# ---------------------------------------------------------------------------

#: How far the broadened search relaxes the original's primary value. A
#: resistor stays tight (a divider cannot drift); caps and inductors get the
#: ±20 % Otto's own diagnoses kept asking for. The primary-value gate and the
#: parameter check still judge whatever is picked.
BROADEN_VALUE_TOLERANCE_PCT: dict[str, float] = {"capacitor": 20.0, "magnetic": 20.0,
                                                 "resistor": 2.0}


def jev_broadened_rescue(row: dict[str, Any], target_manufacturer: str,
                         circuit_context: Any = None) -> dict[str, Any] | None:
    """Search the catalogue with relaxed filters and let Jev pick one or none.

    Returns the replacement row (status ``partial``, the relaxation named in
    the notes) or ``None`` when the row is not searchable this way or Jev
    finds nothing acceptable.
    """
    from heaviside.agents.tools import _crossref_search_impl
    from heaviside.pipeline.crossref_pipeline import _parse_value_si, _to_volts

    cat = row.get("component_type", "")
    tol = BROADEN_VALUE_TOLERANCE_PCT.get(cat)
    value = _parse_value_si(row.get("original_value"), cat) if tol is not None else None
    if tol is None or value is None:
        return None
    kwargs: dict[str, Any] = {}
    if cat == "capacitor":
        v = _to_volts(row.get("original_voltage"))
        if v is not None:
            kwargs["min_voltage"] = v
    found = json.loads(_crossref_search_impl(cat, target_manufacturer, value=value,
                                             value_tolerance_pct=tol, max_results=10, **kwargs))
    cands = found.get("candidates") or []
    if not cands:
        return None
    entry = {
        "ref_des": row.get("ref_des"), "component_type": cat,
        "original_mpn": row.get("original_pn") or row.get("original_mpn") or "",
        "value": row.get("original_value", ""), "voltage": row.get("original_voltage", ""),
        "package": row.get("original_package", ""), "_tas_candidates": cands,
    }
    picked = jev_crossref_row(entry, target_manufacturer, circuit_context)
    if picked["status"] == "no_substitute":
        return None
    relax = f"value within ±{tol:g}%" + (", voltage ≥ original" if "min_voltage" in kwargs else "")
    return {**row, **{k: picked[k] for k in ("substitute_pn", "substitute_value",
                                             "substitute_voltage", "substitute_package")},
            "status": "partial",
            "notes": f"Found by a broadened catalogue search ({relax}) after the first search "
                     f"returned nothing; {picked['notes']}"}
