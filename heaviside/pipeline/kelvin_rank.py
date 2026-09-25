"""Candidate ranking through Kelvin — the one crossref ranker.

Kelvin's ``cross_reference`` ranks a category's candidates against the
original, applies its honesty gates (primary value, critical ratings,
technology family, safety class, mounting, footprint…) and the circuit's
operating-point requirements, and returns a verdict per candidate: status,
grade, per-parameter verdicts, notes. This module is the glue: it projects
TAS envelopes into the flat SI spec dicts Kelvin reads, turns simulation
stress into Kelvin ``requirements`` (margins owned here, as before), drops
rejected candidates, and keeps oversize parts only when nothing fits.

The TAS envelopes themselves are never modified: Kelvin's verdicts come back
as a separate ``{mpn: verdict}`` map for the pipeline state, and are merged
only into the flat summaries the decision model reads.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Kelvin categories whose original is known only by identity (no value).
IDENTITY_CATEGORIES = frozenset({"connector", "analog", "timeBase"})

#: Summary keys whose value is the primary value, per category.
_VALUE_KEY = {"capacitor": "capacitance", "resistor": "resistance", "magnetic": "inductance",
              "chipBead": "impedance_100mhz", "varistor": "varistor_voltage"}

#: Text-derived identity attributes whose name differs from Kelvin's key.
_TEXT_ATTR_TO_KELVIN = {"pitch": "pitch_mm"}

#: Verdict fields carried from Kelvin to the pipeline (and the decision model).
VERDICT_FIELDS = ("status", "grade", "penalty", "params", "notes", "reason", "footprint",
                  "direction")


def _scalar(v: Any) -> Any:
    """A spec value as Kelvin reads it: dimension dicts resolved to nominal."""
    if isinstance(v, dict) and set(v) & {"nominal", "minimum", "maximum"}:
        from heaviside.pipeline.value_parse import resolve_dimensional_value

        return resolve_dimensional_value(v)
    return v


def kelvin_spec(env: dict[str, Any], category: str, key: str | None = None) -> dict[str, Any]:
    """Flat SI spec dict for one TAS envelope (Kelvin's candidate/original shape)."""
    from heaviside.pipeline.crossref_pipeline import (
        _extract_dimensions,
        _extract_value,
        _summarize_candidate,
    )

    summary = _summarize_candidate(env, category)
    spec: dict[str, Any] = {}
    for k, v in summary.items():
        if k == "dimensions_mm" or v is None or v == "":
            continue
        v = _scalar(v)
        if isinstance(v, (str, int, float, bool)):
            spec[k] = v
    val = _extract_value(env, category)
    if val is not None:
        spec["value_si"] = val
    dims = _extract_dimensions(env, category)
    if dims:
        spec["length_m"], spec["width_m"] = dims[0], dims[1]
        if dims[2]:
            spec["height_m"] = dims[2]
    if key is not None:
        spec["_key"] = key
    return spec


def original_spec(comp: dict[str, Any], category: str) -> tuple[dict[str, Any] | None, bool]:
    """``(spec, verified)`` for the BOM row's original part.

    The catalogue envelope, when the original was identified, is the base; the
    user's BOM fields (value, voltage, package) override it, because they are
    the requirement the user stated. ``None`` for an identity-matched part of
    which nothing is known — no candidate can be shown to be that part.
    """
    from heaviside.pipeline.crossref_pipeline import (
        _analog_attrs_from_text,
        _connector_attrs_from_text,
        _parse_value_si,
        _timebase_attrs_from_text,
        _to_volts,
    )

    src_env = comp.get("_source_env")
    verified = isinstance(src_env, dict)
    spec = kelvin_spec(src_env, category) if verified else {}
    if category in IDENTITY_CATEGORIES and not spec:
        text = {"connector": _connector_attrs_from_text, "analog": _analog_attrs_from_text,
                "timeBase": _timebase_attrs_from_text}[category](comp)
        spec = {_TEXT_ATTR_TO_KELVIN.get(k, k): v for k, v in (text or {}).items()
                if v not in (None, "")}
        if not spec:
            return None, False
    value = comp.get("value")
    if isinstance(comp.get("value_si"), (int, float)) and value in (None, ""):
        value = None
        spec["value_si"] = float(comp["value_si"])
    if category == "varistor":
        v = _to_volts(value)
    else:
        v = _parse_value_si(value, category) if value not in (None, "") else None
    if v is not None:
        spec["value_si"] = v
    volts = _to_volts(comp.get("rated_voltage") or comp.get("voltage"))
    if volts is not None and category == "capacitor":
        spec["voltage"] = volts
    if category == "capacitor" and not spec.get("technology"):
        # The construction family gates the candidates (a ceramic is not
        # replaced by a wet electrolytic); infer it the way the BOM states it.
        from heaviside.pipeline.crossref_pipeline import (
            _eia_dielectric_codes,
            _infer_source_cap_technology,
        )

        fam = _infer_source_cap_technology(comp)
        if fam:
            spec["technology"] = fam
        stated = str(comp.get("dielectric") or comp.get("technology") or "").upper().strip()
        if stated in _eia_dielectric_codes():
            spec["dielectric_code"] = stated
    if comp.get("package"):
        spec["package"] = str(comp["package"])
    dims = comp.get("_source_dims_m")
    if dims:
        spec["length_m"], spec["width_m"] = dims[0], dims[1]
        if len(dims) > 2 and dims[2]:
            spec["height_m"] = dims[2]
    spec.setdefault("mpn", str(comp.get("original_mpn") or comp.get("mpn") or ""))
    return spec, verified


def stress_requirements(category: str, stress: Any) -> dict[str, float]:
    """The circuit's minimum ratings at the operating point (the old ranker's margins)."""
    from heaviside.pipeline.crossref_pipeline import (
        CURRENT_DERATING_FACTOR,
        DIODE_VOLTAGE_DERATING,
        VOLTAGE_DERATING_FACTOR,
    )

    if stress is None:
        return {}
    v_peak, i_peak = getattr(stress, "v_peak", None), getattr(stress, "i_peak", None)
    i_rms, i_avg = getattr(stress, "i_rms", None), getattr(stress, "i_avg", None)
    req: dict[str, float | None] = {}
    if category == "capacitor":
        req = {"voltage": v_peak and v_peak * VOLTAGE_DERATING_FACTOR, "ripple_current": i_rms}
    elif category == "magnetic":
        req = {"saturation_current": i_peak, "rated_current": i_rms}
    elif category == "mosfet":
        req = {"vds": v_peak and v_peak * VOLTAGE_DERATING_FACTOR,
               "id": i_peak and i_peak * CURRENT_DERATING_FACTOR}
    elif category == "diode":
        req = {"vrrm": v_peak and v_peak * DIODE_VOLTAGE_DERATING, "if_avg": i_avg}
    elif category == "chipBead":
        req = {"rated_current": i_rms}
    elif category == "varistor":
        req = {"peak_surge_current": i_peak}
    return {k: float(v) for k, v in req.items() if v}


def rank(comp: dict[str, Any], category: str, envelopes: list[dict[str, Any]], *,
         max_results: int = 50, stress: Any = None
         ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Kelvin-ranked envelopes (best first) and their verdicts by MPN.

    Rejected candidates are dropped; parts that overflow the original's
    footprint are kept only when no candidate fits.
    """
    from heaviside.catalogue import kelvin_adapter

    if not envelopes:
        return [], {}
    original, verified = original_spec(comp, category)
    if original is None:
        return [], {}
    specs = [kelvin_spec(env, category, str(i)) for i, env in enumerate(envelopes)]
    specs = [s for s in specs if s.get("mpn")]
    options: dict[str, Any] = {"original_verified": verified, "max_results": len(specs)}
    req = stress_requirements(category, stress)
    if req:
        options["requirements"] = req
    result = kelvin_adapter.cross_reference_options(category, original, specs, options)
    kept = [c for c in result.get("candidates", []) if c.get("status") != "no_substitute"]
    if any(c.get("footprint") != "overflows" for c in kept):
        kept = [c for c in kept if c.get("footprint") != "overflows"]
    kept = kept[:max_results]
    ranked = [envelopes[int(c["_key"])] for c in kept]
    verdicts = {str(c.get("mpn")): {f: c[f] for f in VERDICT_FIELDS if f in c} for c in kept}
    return ranked, verdicts
