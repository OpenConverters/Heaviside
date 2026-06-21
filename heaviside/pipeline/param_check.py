"""Declarative electrical-parameter checking for cross-reference substitutes.

Why this module exists
----------------------
Substitution correctness for power-electronics parts is governed by parameters
that the source BOM never carries (ESR, ripple current, Rds(on), Qg, Qrr, TCR,
Isat, DCR, …). Historically each parameter that *was* checked lived hardcoded in
three disconnected places — ``build_match_detail`` (rationale), ``match_score``
(scoring) and ``guardrails`` (pass/fail) — so adding one meant editing all three,
and most parameters (ESR included) simply fell through and never reached the
report.

This module replaces that with ONE declarative spec per component category. Each
:class:`ParamSpec` row says how to read a parameter, which direction is "better",
and how much margin is allowed before a substitute is flagged. The same spec
drives extraction, the per-parameter verdict on the report, the guardrail, and
the score — a single source of truth, so adding a parameter is one table row.

Both the original and the substitute parameter values come from the internal DB
(resolved by MPN), reusing :func:`crossref_pipeline._summarize_candidate` so the
extraction logic is never duplicated. Missing data is represented as ``None``
(never a 0.0 sentinel — a real low-ESR MLCC is 0.0-ish), and handled per the
policies below.

Margin guidelines are engineering defaults drawn from manufacturer substitution
guides (TDK/Mouser electrolytic→MLCC, Z2Data/SpeCap cross-reference, Coilcraft
current ratings, TI/Nexperia MOSFET notes); they are centralised here so they can
be tuned in one place.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# ── Direction semantics ──────────────────────────────────────────────────────
# lower_better : a valid substitute should be ≤ original (ESR, Rds_on, DCR, Vf…)
# higher_better: a valid substitute should be ≥ original (ripple, Isat, Irms…)
# class_match  : categorical; substitute class must be equal-or-better (dielectric)
LOWER_BETTER = "lower_better"
HIGHER_BETTER = "higher_better"
CLASS_MATCH = "class_match"

# Verdicts a single parameter comparison can yield.
PASS = "pass"           # meets or beats the original
WARN = "warn"           # worse than original but within the allowed margin
FAIL = "fail"           # outside the allowed margin
UNVERIFIED = "unverified"  # cannot compare (a value is missing) — never silently a pass


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """One checkable parameter for a component category.

    ``key`` indexes into the dict returned by ``_summarize_candidate`` (so the
    original and substitute values come from the same None-aware extractor).
    ``tol_factor`` is the multiple of the original at which WARN becomes FAIL:
      * lower_better  : PASS if s ≤ o; WARN if o < s ≤ o·tol; FAIL if s > o·tol
      * higher_better : PASS if s ≥ o; WARN if o·tol ≤ s < o; FAIL if s < o·tol
        (here tol < 1, e.g. 0.9 = "may be up to 10% lower")
    ``missing_substitute`` / ``missing_original`` choose how a None is handled
    (see the comparator). ESR-style critical params exclude a substitute that
    lacks the value ("don't use it"); soft params merely mark it UNVERIFIED.
    """

    key: str
    label: str
    unit: str
    direction: str
    tol_factor: float
    # "exclude" → a substitute missing this value FAILs (it can't be verified);
    # "soft"    → marked UNVERIFIED, not failed.
    missing_substitute: str = "soft"
    # "minimize"/"maximize" → when the original value is unknown, prefer the
    # extreme (used by scoring/selection); here it only annotates the verdict.
    # "soft" → just UNVERIFIED.
    missing_original: str = "soft"
    # Optional categorical rank map for CLASS_MATCH (higher rank = better).
    class_rank: dict[str, int] | None = None


# ── Dielectric / temperature-characteristic ranking (capacitors) ─────────────
# Higher rank = more stable / wider temperature range. A substitute must be
# equal-or-better; e.g. C0G→X7R is a downgrade (bias/temp behaviour worsens),
# X5R→X7R is safe. Electrolytic/tantalum/film are distinct technologies — a
# cross-technology swap is flagged via the special-case in _compare_class.
_DIELECTRIC_RANK = {
    "c0g": 5, "np0": 5, "cog": 5,        # class-1: most stable
    "x8r": 4, "x8l": 4,
    "x7r": 3, "x7s": 3, "x7t": 3,
    "x6s": 2, "x6t": 2,
    "x5r": 1,
    "y5v": 0, "z5u": 0,                  # class-2/3: worst bias/temp behaviour
}


def _norm_class(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "").replace(" ", "")


# ── Per-category parameter specs ─────────────────────────────────────────────
# Margins (tol_factor) are documented engineering defaults; tune here.
PARAM_SPECS: dict[str, list[ParamSpec]] = {
    "capacitor": [
        # ESR must be ≤ original for low-ESR/regulator-output use; allow 1.5×
        # before failing. A candidate with no ESR data is excluded ("don't use
        # it") per the substitution policy.
        ParamSpec("esr", "ESR", "Ω", LOWER_BETTER, 1.5,
                  missing_substitute="exclude", missing_original="minimize"),
        # Ripple-current capability must be ≥ original; allow 10% shortfall.
        ParamSpec("ripple_current", "Ripple I", "A", HIGHER_BETTER, 0.9,
                  missing_substitute="exclude", missing_original="maximize"),
        # Dielectric / temperature characteristic must not downgrade.
        ParamSpec("technology", "Dielectric", "", CLASS_MATCH, 0.0,
                  class_rank=_DIELECTRIC_RANK),
    ],
    "mosfet": [
        ParamSpec("rds_on", "Rds(on)", "Ω", LOWER_BETTER, 1.5),
        ParamSpec("qg", "Qg", "C", LOWER_BETTER, 2.0),
        ParamSpec("coss", "Coss", "F", LOWER_BETTER, 2.0),
        # Gate-threshold class: logic-level vs standard-gate must match (handled
        # as a numeric lower_better-ish closeness — large jump flags).
        ParamSpec("vgs_threshold_max", "Vgs(th)", "V", LOWER_BETTER, 2.0),
    ],
    "diode": [
        ParamSpec("vf", "Vf", "V", LOWER_BETTER, 1.2),
        ParamSpec("qrr", "Qrr", "C", LOWER_BETTER, 2.0),
        ParamSpec("trr", "trr", "s", LOWER_BETTER, 2.0),
    ],
    "resistor": [
        # Power rating must be ≥ original; allow 10% shortfall.
        ParamSpec("power_rating", "Power", "W", HIGHER_BETTER, 0.9),
        # TCR (ppm/°C): lower is better for precision; allow 2×.
        ParamSpec("tcr", "TCR", "ppm/°C", LOWER_BETTER, 2.0),
    ],
    "magnetic": [
        ParamSpec("saturation_current", "Isat", "A", HIGHER_BETTER, 0.9,
                  missing_substitute="exclude"),
        ParamSpec("dcr", "DCR", "Ω", LOWER_BETTER, 1.3),
        ParamSpec("rated_current", "Irms", "A", HIGHER_BETTER, 0.9),
    ],
    "chipBead": [
        ParamSpec("impedance_100mhz", "Z@100MHz", "Ω", HIGHER_BETTER, 0.8),
        ParamSpec("srf", "SRF", "Hz", HIGHER_BETTER, 0.8),
        ParamSpec("dcr", "DCR", "Ω", LOWER_BETTER, 1.3),
        ParamSpec("rated_current", "Irms", "A", HIGHER_BETTER, 0.9),
    ],
}


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _compare_numeric(spec: ParamSpec, o: float | None, s: float | None) -> tuple[str, str]:
    """Return (verdict, note) for a numeric parameter."""
    if o is None and s is None:
        return UNVERIFIED, f"{spec.label}: not specified on either part"
    if s is None:
        # Substitute lacks the value.
        if spec.missing_substitute == "exclude":
            return FAIL, f"{spec.label}: substitute has no {spec.label} data — cannot verify"
        return UNVERIFIED, f"{spec.label}: substitute has no {spec.label} data"
    if o is None:
        hint = {"minimize": " (lowest available preferred)",
                "maximize": " (highest available preferred)"}.get(spec.missing_original, "")
        return UNVERIFIED, f"{spec.label}: original unknown; substitute = {s:g}{spec.unit}{hint}"

    if spec.direction == LOWER_BETTER:
        if s <= o:
            return PASS, f"{spec.label}: {s:g} ≤ {o:g}{spec.unit}"
        if s <= o * spec.tol_factor:
            return WARN, f"{spec.label}: {s:g} > {o:g}{spec.unit} (within {spec.tol_factor:g}× margin)"
        return FAIL, f"{spec.label}: {s:g} exceeds {o:g}{spec.unit} by >{spec.tol_factor:g}×"
    # HIGHER_BETTER (tol_factor < 1)
    if s >= o:
        return PASS, f"{spec.label}: {s:g} ≥ {o:g}{spec.unit}"
    if s >= o * spec.tol_factor:
        return WARN, f"{spec.label}: {s:g} < {o:g}{spec.unit} (within {spec.tol_factor:g}× margin)"
    return FAIL, f"{spec.label}: {s:g} below {o:g}{spec.unit} by >{(1 - spec.tol_factor) * 100:g}%"


def _compare_class(spec: ParamSpec, o: Any, s: Any) -> tuple[str, str]:
    """Categorical comparison (dielectric / temperature characteristic)."""
    on, sn = _norm_class(o), _norm_class(s)
    rank = spec.class_rank or {}
    o_rank = next((v for k, v in rank.items() if k in on), None)
    s_rank = next((v for k, v in rank.items() if k in sn), None)
    if o_rank is None and s_rank is None:
        # Neither is a recognised class-2 ceramic code — compare raw technology.
        if on and sn and on != sn:
            return WARN, f"{spec.label}: {o} → {s} (different technology)"
        return UNVERIFIED, f"{spec.label}: not a recognised dielectric code"
    if s_rank is None:
        return UNVERIFIED, f"{spec.label}: substitute dielectric '{s}' unrecognised"
    if o_rank is None:
        return UNVERIFIED, f"{spec.label}: original dielectric '{o}' unrecognised"
    if s_rank >= o_rank:
        return PASS, f"{spec.label}: {o} → {s} (equal-or-better stability)"
    return FAIL, f"{spec.label}: {o} → {s} is a dielectric downgrade"


def compare_param(spec: ParamSpec, orig: Any, sub: Any) -> dict[str, Any]:
    """Evaluate one parameter; returns a render-ready dict."""
    if spec.direction == CLASS_MATCH:
        verdict, note = _compare_class(spec, orig, sub)
        o_disp, s_disp = (str(orig) if orig is not None else ""), (str(sub) if sub is not None else "")
    else:
        of, sf = _as_float(orig), _as_float(sub)
        verdict, note = _compare_numeric(spec, of, sf)
        o_disp = f"{of:g}{spec.unit}" if of is not None else ""
        s_disp = f"{sf:g}{spec.unit}" if sf is not None else ""
    return {
        "name": spec.key,
        "label": spec.label,
        "original": o_disp,
        "substitute": s_disp,
        "verdict": verdict,
        "note": note,
    }


def evaluate_params(
    category: str,
    orig_params: dict[str, Any] | None,
    sub_params: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Evaluate every spec'd parameter for a category. Returns one result per
    parameter that has data on at least one side (skips fully-absent pairs to
    avoid noise). ``orig_params``/``sub_params`` are ``_summarize_candidate``
    outputs (or None when the MPN didn't resolve in the DB)."""
    specs = PARAM_SPECS.get(category, [])
    orig_params = orig_params or {}
    sub_params = sub_params or {}
    results: list[dict[str, Any]] = []
    for spec in specs:
        o = orig_params.get(spec.key)
        s = sub_params.get(spec.key)
        if o is None and s is None:
            continue  # neither side has it — nothing to say
        results.append(compare_param(spec, o, s))
    return results


def worst_verdict(results: list[dict[str, Any]]) -> str:
    """The most severe verdict across a parameter result list."""
    order = {PASS: 0, UNVERIFIED: 1, WARN: 2, FAIL: 3}
    worst = PASS
    for r in results:
        if order.get(r.get("verdict", PASS), 0) > order[worst]:
            worst = r["verdict"]
    return worst
