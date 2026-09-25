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
                     circuit_context: Any = None,
                     extra_state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build one crossref row for ``entry`` from its ``_tas_candidates`` via Jev.

    ``extra_state`` adds context for Jev to weigh (e.g. the reviewer's
    objections and the pick they rejected, in the correction pass).
    """
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
    if extra_state:
        state.update(extra_state)
    pick = decide(state, {"pick": choice_question(
        "Choose the catalogue candidate that best replaces the `original` part. Each candidate "
        "carries the ranker's verdict in `kelvin`: `status` (recommended beats partial), `grade` "
        "(drop_in beats minor_review beats major_review beats redesign), `footprint`, and "
        "per-parameter `params` verdicts with `notes`. Prefer the candidate the verdicts rate "
        "highest; weigh the notes for anything that matters to this circuit.", options)})["pick"]
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
    kelvin = chosen.get("kelvin")
    if not kelvin or kelvin.get("status") not in ("recommended", "partial"):
        raise JevError(f"{base['ref_des']}: chosen candidate {mpn} carries no Kelvin verdict — "
                       "candidates must be ranked by Kelvin before the choice")
    # Kelvin judged the chosen part; the same MPN as the original is exact.
    status = "exact" if mpn == base["original_pn"] else kelvin["status"]
    why = "; ".join(kelvin.get("notes") or [])
    return {**base, "substitute_pn": mpn,
            "substitute_value": "", "substitute_voltage": "",
            "substitute_package": chosen.get("package", ""),
            "status": status,
            "notes": f"Chosen from {len(cands)} Kelvin-ranked candidates by the decision model "
                     f"(P={p_pick:.2f}); Kelvin grades it {kelvin.get('grade', '?')}"
                     + (f": {why}" if why else ".")}


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

def jev_broadened_rescue(row: dict[str, Any], state: Any,
                         cache: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    """Rank the target's whole category for a no_substitute row through Kelvin
    (with the circuit's stress), and let Jev pick one or none.

    The first pass only saw the prefetch's value-nearest 50; this looks at every
    part of the category Kelvin will accept. Returns the replacement row
    (Kelvin's status, noted as found by the wider search) or ``None``.
    ``cache`` holds each category's target-manufacturer rows across one stage
    call, so a catalogue file is read once, not once per row.
    """
    from heaviside.pipeline import kelvin_rank
    from heaviside.pipeline.crossref_pipeline import (
        _candidate_summaries_for_llm,
        _target_manufacturer_envelopes,
    )

    cat = row.get("component_type", "")
    ref = row.get("ref_des")
    comp = next((c for c in state.source_bom if c.get("ref_des") == ref), None) or {
        "ref_des": ref, "component_type": cat, "value": row.get("original_value", ""),
        "voltage": row.get("original_voltage", ""), "package": row.get("original_package", ""),
        "original_mpn": row.get("original_pn", "")}
    envs = _target_manufacturer_envelopes(state.target_manufacturer, cat, cache)
    ranked, verdicts = kelvin_rank.rank(comp, cat, envs, max_results=10,
                                        stress=state.stress_by_ref.get(ref))
    if not ranked:
        return None
    state.kelvin_verdicts.setdefault(str(ref), {}).update(verdicts)
    entry = {**comp, "original_mpn": row.get("original_pn") or comp.get("original_mpn", ""),
             "_tas_candidates": _candidate_summaries_for_llm(ranked, cat, comp.get("_source_dims_m"),
                                                            limit=10, kelvin_verdicts=verdicts)}
    entry.pop("_source_env", None)
    entry.pop("_source_dims_m", None)
    picked = jev_crossref_row(entry, state.target_manufacturer, state.circuit_context)
    if picked["status"] == "no_substitute":
        return None
    return {**row, **{k: picked[k] for k in ("substitute_pn", "substitute_value",
                                             "substitute_voltage", "substitute_package",
                                             "status")},
            "notes": "Found by ranking the target's whole catalogue category after the first "
                     f"search returned nothing; {picked['notes']}"}
