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
from typing import Any

# ── Direction semantics ──────────────────────────────────────────────────────
# lower_better : a valid substitute should be ≤ original (ESR, Rds_on, DCR, Vf…)
# higher_better: a valid substitute should be ≥ original (ripple, Isat, Irms…)
# class_match  : categorical; substitute class must be equal-or-better (dielectric)
# exact_match  : identity parameter; substitute must EQUAL the original
#                (connector pitch/positions/gender, analog function/channels…).
#                Different is a FAIL, never a WARN — there is no "close enough"
#                for a 9-position housing replacing a 10-position one.
LOWER_BETTER = "lower_better"
HIGHER_BETTER = "higher_better"
CLASS_MATCH = "class_match"
EXACT_MATCH = "exact_match"

# Verdicts a single parameter comparison can yield.
PASS = "pass"  # meets or beats the original
WARN = "warn"  # worse than original but within the allowed margin
FAIL = "fail"  # outside the allowed margin
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
    # Compare |value| instead of the signed value. TCR is signed physics
    # (carbon film is genuinely negative) but its QUALITY is the magnitude:
    # a −500 ppm/K substitute must not beat a +100 ppm/K original.
    magnitude: bool = False
    # Additive WARN margin (same unit as the value) used INSTEAD of the
    # multiplicative tol_factor when set. Needed for parameters where a
    # ratio is meaningless: temperatures cross zero (−40 °C × 1.5 is
    # nonsense) and dB figures are already logarithmic.
    abs_tol: float | None = None
    # An UNVERIFIED verdict on this parameter demotes a `recommended`
    # substitute to `partial` — for identity parameters (connector
    # positions/gender/family, analog function) that a senior engineer
    # would never leave unchecked. FAIL always demotes regardless.
    unverified_demotes: bool = False


# ── Dielectric / temperature-characteristic ranking (capacitors) ─────────────
# Higher rank = more stable / wider temperature range. A substitute must be
# equal-or-better; e.g. C0G→X7R is a downgrade (bias/temp behaviour worsens),
# X5R→X7R is safe. Electrolytic/tantalum/film are distinct technologies — a
# cross-technology swap is flagged via the special-case in _compare_class.
_DIELECTRIC_RANK = {
    "c0g": 5,
    "np0": 5,
    "cog": 5,  # class-1: most stable
    "x8r": 4,
    "x8l": 4,
    "x7r": 3,
    "x7s": 3,
    "x7t": 3,
    "x6s": 2,
    "x6t": 2,
    "x5r": 1,
    "y5v": 0,
    "z5u": 0,  # class-2/3: worst bias/temp behaviour
    # Coarse CAS chemistry buckets (many records store only these, not the EIA
    # code). Recognising them stops the "not a recognised class code" noise and
    # catches class-1→class-2 downgrades; the finer X7R-vs-X5R distinction is
    # handled by the specific dielectric_code + max-temp checks. Keys are the
    # _norm_class form (dashes stripped).
    "ceramicclass1": 5,
    "ceramicclass2": 2,
    "ceramicclass3": 0,
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
        ParamSpec(
            "esr",
            "ESR",
            "Ω",
            LOWER_BETTER,
            1.5,
            missing_substitute="exclude",
            missing_original="minimize",
        ),
        # Ripple-current capability must be ≥ original; allow 10% shortfall.
        ParamSpec(
            "ripple_current",
            "Ripple I",
            "A",
            HIGHER_BETTER,
            0.9,
            missing_substitute="exclude",
            missing_original="maximize",
        ),
        # Dielectric / temperature characteristic must not downgrade.
        ParamSpec("technology", "Dielectric", "", CLASS_MATCH, 0.0, class_rank=_DIELECTRIC_RANK),
        # Specific EIA dielectric code (X7R/X5R/C0G…): a finer check than the
        # class-1/class-2 bucket above — X7R→X5R is a real downgrade the coarse
        # bucket cannot see. Only bites when both records carry dielectricCode.
        ParamSpec("dielectric_code", "Dielectric code", "", CLASS_MATCH, 0.0, class_rank=_DIELECTRIC_RANK),
        # Max operating temperature: the substitute must reach at least as high
        # as the original (X5R 85 °C must NOT replace X7R 125 °C). Additive 5 °C
        # WARN band absorbs datasheet rounding; a real class drop (125→85) fails.
        ParamSpec("temp_max_C", "T max", "°C", HIGHER_BETTER, 0.0, abs_tol=5.0),
        # Tolerance (percent): tighter-or-equal preferred; WARN up to 2× looser,
        # FAIL beyond. Only bites when the catalogue records tolerance on both
        # sides (many MLCC records don't → unverified, honestly).
        ParamSpec("tolerance_pct", "Tolerance", "%", LOWER_BETTER, 2.0),
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
        # Tolerance (percent): tighter-or-equal is required; a looser part is a
        # real downgrade (a 1% feedback/sense resistor is chosen deliberately).
        # WARN up to 2× looser, FAIL beyond — catches a 5%-for-1% swap the LLM
        # prose called "tighter".
        ParamSpec("tolerance_pct", "Tolerance", "%", LOWER_BETTER, 2.0),
        # TCR (ppm/°C): lower |TCR| is better for precision; allow 2×. Signed
        # values are real (carbon film is negative) — compare magnitudes.
        ParamSpec("tcr", "TCR", "ppm/°C", LOWER_BETTER, 2.0, magnitude=True),
    ],
    "magnetic": [
        ParamSpec(
            "saturation_current", "Isat", "A", HIGHER_BETTER, 0.9, missing_substitute="exclude"
        ),
        ParamSpec("dcr", "DCR", "Ω", LOWER_BETTER, 1.3),
        ParamSpec("rated_current", "Irms", "A", HIGHER_BETTER, 0.9),
    ],
    "chipBead": [
        ParamSpec("impedance_100mhz", "Z@100MHz", "Ω", HIGHER_BETTER, 0.8),
        ParamSpec("srf", "SRF", "Hz", HIGHER_BETTER, 0.8),
        ParamSpec("dcr", "DCR", "Ω", LOWER_BETTER, 1.3),
        ParamSpec("rated_current", "Irms", "A", HIGHER_BETTER, 0.9),
    ],
    # Connectors: substitution is dominated by IDENTITY parameters, not
    # ratings — a connector either mates/fits or it does not. Family, contact
    # count, gender and pitch are exact-match hard gates (industry crossing
    # guides: pitch must match exactly, a 0.04 mm error accumulates to a full
    # misalignment over a row; both halves must share gender/positions).
    # Ratings (current per contact, voltage, temperature, durability cycles)
    # follow the usual ≥/≤ rules. Mounting style (SMT vs THT) is exact-match
    # but sparse in the catalogue, so it stays soft-unverified rather than
    # demoting every row. The mating-system check (same series / standardized
    # interface) is a separate helper: connector_mating_check().
    "connector": [
        # pinHeaderSocket and boardToBoard share a group: commodity pin
        # headers straddle that boundary across vendor taxonomies (the mating
        # check still separates true headers from mezzanine systems).
        ParamSpec(
            "family",
            "Family",
            "",
            EXACT_MATCH,
            0.0,
            missing_substitute="exclude",
            unverified_demotes=True,
            class_rank={"pinheadersocket": 1, "boardtoboard": 1},
        ),
        ParamSpec(
            "positions",
            "Positions",
            "",
            EXACT_MATCH,
            0.0,
            missing_substitute="exclude",
            unverified_demotes=True,
        ),
        ParamSpec("polarity", "Gender", "", EXACT_MATCH, 0.0, unverified_demotes=True),
        # 2% relative window absorbs unit rounding (2.54 vs 2.5399 mm) while
        # still splitting real pitch families (2.00 vs 2.54 mm = 27%).
        ParamSpec("pitch_mm", "Pitch", "mm", EXACT_MATCH, 0.02, unverified_demotes=True),
        ParamSpec("interface_standard", "Interface std", "", EXACT_MATCH, 0.0),
        ParamSpec("mounting", "Mounting", "", EXACT_MATCH, 0.0),
        ParamSpec(
            "rated_current_A",
            "I/contact",
            "A",
            HIGHER_BETTER,
            0.9,
            missing_substitute="exclude",
        ),
        ParamSpec("rated_voltage_V", "Rated V", "V", HIGHER_BETTER, 0.9),
        # Temperature range must COVER the original's; additive margin because
        # ratios are meaningless across 0 °C (−40 × factor is nonsense).
        ParamSpec("temp_min_C", "T min", "°C", LOWER_BETTER, 0.0, abs_tol=15.0),
        ParamSpec("temp_max_C", "T max", "°C", HIGHER_BETTER, 0.0, abs_tol=15.0),
        # Durability: half the mating cycles is a different durability class.
        ParamSpec("mating_cycles", "Mating cycles", "", HIGHER_BETTER, 0.5),
        ParamSpec("contact_resistance", "Contact R", "Ω", LOWER_BETTER, 2.0),
    ],
    # Analog ICs (op-amps, comparators, ADC/DAC, switches/muxes). One list
    # serves every subtype: evaluate_params() skips parameters absent on both
    # sides, so ADC rows never show GBW and op-amp rows never show sample
    # rate. Function (subtype) and channel count are identity gates — a
    # comparator is not an op-amp, a quad is not a dual. Package is
    # exact-match because pin-compatibility is the point of a drop-in
    # (SOIC-8 → TSSOP-8 is a footprint change → FAIL → demoted to partial).
    "analog": [
        ParamSpec(
            "subtype",
            "Function",
            "",
            EXACT_MATCH,
            0.0,
            missing_substitute="exclude",
            unverified_demotes=True,
        ),
        ParamSpec(
            "channels",
            "Channels",
            "",
            EXACT_MATCH,
            0.0,
            missing_substitute="exclude",
            unverified_demotes=True,
        ),
        ParamSpec("package", "Package", "", EXACT_MATCH, 0.0),
        # Supply window must cover the original's. Additive 0.3 V WARN band on
        # the low side (1.8 V-min part replaced by a 2.0 V-min part may still
        # work on a 3.3 V rail — flag, don't kill); ≥90 % on the high side.
        ParamSpec("supply_min_V", "Vsupply min", "V", LOWER_BETTER, 0.0, abs_tol=0.3),
        ParamSpec("supply_max_V", "Vsupply max", "V", HIGHER_BETTER, 0.9),
        ParamSpec("gbw", "GBW", "Hz", HIGHER_BETTER, 0.7),
        ParamSpec("slew_rate", "Slew rate", "V/s", HIGHER_BETTER, 0.7),
        ParamSpec("input_offset_voltage", "Vos", "V", LOWER_BETTER, 2.0, magnitude=True),
        ParamSpec("offset_drift", "Vos drift", "V/°C", LOWER_BETTER, 2.0, magnitude=True),
        # Bias current spans decades (pA CMOS → µA bipolar); a 10× increase is
        # the point where high-impedance sources start to notice.
        ParamSpec("input_bias_current", "Ib", "A", LOWER_BETTER, 10.0, magnitude=True),
        ParamSpec("cmrr_db", "CMRR", "dB", HIGHER_BETTER, 0.0, abs_tol=6.0),
        ParamSpec("quiescent_current", "Iq/ch", "A", LOWER_BETTER, 3.0),
        ParamSpec(
            "rail_to_rail_input",
            "Rail-to-rail in",
            "",
            CLASS_MATCH,
            0.0,
            class_rank={"yes": 1, "no": 0},
        ),
        ParamSpec(
            "rail_to_rail_output",
            "Rail-to-rail out",
            "",
            CLASS_MATCH,
            0.0,
            class_rank={"yes": 1, "no": 0},
        ),
        # Comparators: response time and output topology (push-pull vs
        # open-drain are NOT interchangeable — one needs a pull-up).
        ParamSpec("propagation_delay", "tpd", "s", LOWER_BETTER, 2.0),
        ParamSpec("output_stage", "Output stage", "", EXACT_MATCH, 0.0),
        # Data converters: resolution is a hard floor, throughput ≥90 %.
        ParamSpec("resolution", "Resolution", "bit", HIGHER_BETTER, 1.0),
        ParamSpec("sample_rate", "Sample rate", "S/s", HIGHER_BETTER, 0.9),
    ],
    # Time bases (TBAS: crystals, XOs, TCXO/VCXO/OCXO, MEMS). Frequency IS
    # the part — exact match only. Technology is identity (a MEMS clock is
    # not a quartz crystal; an active XO is not a passive crystal). A
    # crystal's load capacitance sets the oscillation frequency in-circuit:
    # a different CL pulls the clock off frequency, so it is exact-match
    # (5% window absorbs rounding, splits the 8/10/12.5/18/20 pF steps).
    # Tolerance/stability/aging are read in ppm; ESR must be low enough for
    # the oscillator circuit's negative resistance to start the crystal.
    "timeBase": [
        ParamSpec(
            "subtype",
            "Family",
            "",
            EXACT_MATCH,
            0.0,
            missing_substitute="exclude",
            unverified_demotes=True,
        ),
        ParamSpec(
            "technology",
            "Technology",
            "",
            EXACT_MATCH,
            0.0,
            missing_substitute="exclude",
            unverified_demotes=True,
        ),
        ParamSpec(
            "frequency",
            "Frequency",
            "Hz",
            EXACT_MATCH,
            1e-4,
            missing_substitute="exclude",
            unverified_demotes=True,
        ),
        ParamSpec("load_capacitance_pF", "Load C", "pF", EXACT_MATCH, 0.05, unverified_demotes=True),
        ParamSpec("output_type", "Output", "", EXACT_MATCH, 0.0),
        ParamSpec("mode", "Mode", "", EXACT_MATCH, 0.0),
        ParamSpec("tolerance_ppm", "Tolerance", "ppm", LOWER_BETTER, 2.0),
        ParamSpec("stability_ppm", "Stability", "ppm", LOWER_BETTER, 2.0),
        ParamSpec("aging_ppm_y", "Aging/yr", "ppm", LOWER_BETTER, 2.0),
        ParamSpec("esr", "ESR", "Ω", LOWER_BETTER, 1.5),
        ParamSpec("supply_min_V", "Vsupply min", "V", LOWER_BETTER, 0.0, abs_tol=0.3),
        ParamSpec("supply_max_V", "Vsupply max", "V", HIGHER_BETTER, 0.9),
        ParamSpec("current_consumption", "Isupply", "A", LOWER_BETTER, 3.0),
        ParamSpec("temp_min_C", "T min", "°C", LOWER_BETTER, 0.0, abs_tol=15.0),
        ParamSpec("temp_max_C", "T max", "°C", HIGHER_BETTER, 0.0, abs_tol=15.0),
        ParamSpec("package", "Package", "", EXACT_MATCH, 0.0),
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
        hint = {
            "minimize": " (lowest available preferred)",
            "maximize": " (highest available preferred)",
        }.get(spec.missing_original, "")
        return UNVERIFIED, f"{spec.label}: original unknown; substitute = {s:g}{spec.unit}{hint}"

    if spec.direction == LOWER_BETTER:
        if s <= o:
            return PASS, f"{spec.label}: {s:g} ≤ {o:g}{spec.unit}"
        # Additive margin when abs_tol is set (temperatures, dB); else
        # the usual multiplicative tol_factor.
        warn_ceiling = (o + spec.abs_tol) if spec.abs_tol is not None else (o * spec.tol_factor)
        if s <= warn_ceiling:
            margin = (
                f"within +{spec.abs_tol:g}{spec.unit} margin"
                if spec.abs_tol is not None
                else f"within {spec.tol_factor:g}× margin"
            )
            return WARN, f"{spec.label}: {s:g} > {o:g}{spec.unit} ({margin})"
        return FAIL, f"{spec.label}: {s:g} exceeds {o:g}{spec.unit} beyond the allowed margin"
    # HIGHER_BETTER (tol_factor < 1, or abs_tol as an additive shortfall band)
    if s >= o:
        return PASS, f"{spec.label}: {s:g} ≥ {o:g}{spec.unit}"
    warn_floor = (o - spec.abs_tol) if spec.abs_tol is not None else (o * spec.tol_factor)
    if s >= warn_floor:
        margin = (
            f"within −{spec.abs_tol:g}{spec.unit} margin"
            if spec.abs_tol is not None
            else f"within {spec.tol_factor:g}× margin"
        )
        return WARN, f"{spec.label}: {s:g} < {o:g}{spec.unit} ({margin})"
    return FAIL, f"{spec.label}: {s:g} below {o:g}{spec.unit} beyond the allowed margin"


def _compare_exact(spec: ParamSpec, o: Any, s: Any) -> tuple[str, str]:
    """Identity comparison: the substitute must EQUAL the original.

    Numbers compare within a relative window of ``tol_factor`` (0.0 = strict;
    pitch uses 0.02 to absorb metric/imperial rounding). Strings compare
    case/space/dash-insensitively ("Board-to-Board" == "boardToBoard" is NOT
    assumed — normalization only strips formatting, never meaning). A mismatch
    is always FAIL: identity parameters have no 'close enough'.
    """
    if o is None and s is None:
        return UNVERIFIED, f"{spec.label}: not specified on either part"
    if s is None:
        if spec.missing_substitute == "exclude":
            return FAIL, f"{spec.label}: substitute has no {spec.label} data — cannot verify"
        return UNVERIFIED, f"{spec.label}: substitute has no {spec.label} data"
    if o is None:
        return UNVERIFIED, f"{spec.label}: original unknown; substitute = {s}{spec.unit}"
    # Equivalence groups (via class_rank): values mapping to the same group id
    # compare equal — used for connector families where vendor taxonomies
    # drift (a 2.54 mm header is 'pinHeaderSocket' at one vendor and
    # 'boardToBoard' at another).
    if spec.class_rank:
        og = spec.class_rank.get(_norm_class(o), _norm_class(o))
        sg = spec.class_rank.get(_norm_class(s), _norm_class(s))
        if og == sg:
            return PASS, f"{spec.label}: {o} ≈ {s} (same class group)"
        return FAIL, f"{spec.label}: '{s}' ≠ original '{o}'"
    of, sf = _as_float(o), _as_float(s)
    if of is not None and sf is not None:
        if of == sf:
            return PASS, f"{spec.label}: {sf:g}{spec.unit} (exact)"
        denom = max(abs(of), abs(sf))
        if denom > 0 and abs(of - sf) / denom <= spec.tol_factor:
            return PASS, f"{spec.label}: {sf:g} ≈ {of:g}{spec.unit}"
        return FAIL, f"{spec.label}: {sf:g}{spec.unit} ≠ original {of:g}{spec.unit}"
    if _norm_class(o) == _norm_class(s) and _norm_class(o):
        return PASS, f"{spec.label}: {s} (match)"
    return FAIL, f"{spec.label}: '{s}' ≠ original '{o}'"


def _compare_class(spec: ParamSpec, o: Any, s: Any) -> tuple[str, str]:
    """Categorical equal-or-better comparison (dielectric class, rail-to-rail
    capability, …) driven by the spec's class_rank map."""
    on, sn = _norm_class(o), _norm_class(s)
    rank = spec.class_rank or {}
    o_rank = next((v for k, v in rank.items() if k in on), None)
    s_rank = next((v for k, v in rank.items() if k in sn), None)
    if o_rank is None and s_rank is None:
        # Neither value is a recognised class code — compare raw strings.
        if on and sn and on != sn:
            return WARN, f"{spec.label}: {o} → {s} (different class)"
        if on and sn:
            return PASS, f"{spec.label}: same class ({o})"
        return UNVERIFIED, f"{spec.label}: not a recognised class code"
    if s_rank is None:
        return UNVERIFIED, f"{spec.label}: substitute class '{s}' unrecognised"
    if o_rank is None:
        return UNVERIFIED, f"{spec.label}: original class '{o}' unrecognised"
    if s_rank >= o_rank:
        return PASS, f"{spec.label}: {o} → {s} (equal-or-better)"
    return FAIL, f"{spec.label}: {o} → {s} is a class downgrade"


def compare_param(spec: ParamSpec, orig: Any, sub: Any) -> dict[str, Any]:
    """Evaluate one parameter; returns a render-ready dict."""
    if spec.direction == CLASS_MATCH:
        verdict, note = _compare_class(spec, orig, sub)
        o_disp, s_disp = (
            (str(orig) if orig is not None else ""),
            (str(sub) if sub is not None else ""),
        )
    elif spec.direction == EXACT_MATCH:
        verdict, note = _compare_exact(spec, orig, sub)
        of, sf = _as_float(orig), _as_float(sub)
        o_disp = f"{of:g}{spec.unit}" if of is not None else (str(orig) if orig is not None else "")
        s_disp = f"{sf:g}{spec.unit}" if sf is not None else (str(sub) if sub is not None else "")
    else:
        of, sf = _as_float(orig), _as_float(sub)
        # magnitude specs judge |value| but display the signed datasheet value.
        cmp_o = abs(of) if (spec.magnitude and of is not None) else of
        cmp_s = abs(sf) if (spec.magnitude and sf is not None) else sf
        verdict, note = _compare_numeric(spec, cmp_o, cmp_s)
        o_disp = f"{of:g}{spec.unit}" if of is not None else ""
        s_disp = f"{sf:g}{spec.unit}" if sf is not None else ""
    return {
        "name": spec.key,
        "label": spec.label,
        "original": o_disp,
        "substitute": s_disp,
        "verdict": verdict,
        "note": note,
        # An UNVERIFIED here must block a clean 'recommended' (identity params).
        "critical": spec.unverified_demotes,
    }


def _render_param(spec: ParamSpec, orig: Any, sub: Any, verdict: str) -> dict[str, Any]:
    """Build the render-ready dict for one parameter. The VERDICT is Kelvin's
    decision; the display strings + note are Python glue."""
    if spec.direction == CLASS_MATCH:
        o_disp = str(orig) if orig is not None else ""
        s_disp = str(sub) if sub is not None else ""
    elif spec.direction == EXACT_MATCH:
        of, sf = _as_float(orig), _as_float(sub)
        o_disp = f"{of:g}{spec.unit}" if of is not None else (str(orig) if orig is not None else "")
        s_disp = f"{sf:g}{spec.unit}" if sf is not None else (str(sub) if sub is not None else "")
    else:
        of, sf = _as_float(orig), _as_float(sub)
        o_disp = f"{of:g}{spec.unit}" if of is not None else ""
        s_disp = f"{sf:g}{spec.unit}" if sf is not None else ""
    if sub is None and orig is None:
        note = f"{spec.label}: not specified on either part"
    elif sub is None:
        note = (
            f"{spec.label}: substitute has no {spec.label} data — cannot verify"
            if spec.missing_substitute == "exclude"
            else f"{spec.label}: substitute has no {spec.label} data"
        )
    elif orig is None:
        hint = {"minimize": " (lowest available preferred)", "maximize": " (highest available preferred)"}.get(
            spec.missing_original, ""
        )
        note = f"{spec.label}: original unknown; substitute = {s_disp}{hint}"
    else:
        note = f"{spec.label}: {s_disp} vs {o_disp}"
    return {
        "name": spec.key,
        "label": spec.label,
        "original": o_disp,
        "substitute": s_disp,
        "verdict": verdict,
        "note": note,
        "critical": spec.unverified_demotes,
    }


def evaluate_params(
    category: str,
    orig_params: dict[str, Any] | None,
    sub_params: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Evaluate every spec'd parameter for a category. Returns one result per
    parameter that has data on at least one side (skips fully-absent pairs to
    avoid noise). ``orig_params``/``sub_params`` are ``_summarize_candidate``
    outputs (or None when the MPN didn't resolve in the DB).

    The per-parameter VERDICT is computed by Kelvin (the deterministic engine,
    golden-parity-locked); Python builds the display dict + note around it."""
    from heaviside.pipeline._kelvin_primitives import evaluate_params as _kv

    orig_params = orig_params or {}
    sub_params = sub_params or {}
    verdicts = {r["name"]: r["verdict"] for r in _kv(category, orig_params, sub_params)}
    results: list[dict[str, Any]] = []
    for spec in PARAM_SPECS.get(category, []):
        if spec.key not in verdicts:  # Kelvin only reports params with data (same filter)
            continue
        results.append(
            _render_param(spec, orig_params.get(spec.key), sub_params.get(spec.key), verdicts[spec.key])
        )
    return results


def effective_capacitance_at_bias(
    c_nom: float | None,
    v_rated: float | None,
    sat_ratio: float | None,
    vth: float | None,
    v_op: float,
) -> float | None:
    """Effective capacitance of a class-2 MLCC at DC operating voltage ``v_op``.

    Class-2 ceramics lose capacitance under DC bias — a "10 µF" X5R can measure
    3-4 µF at its operating voltage — so the nameplate value is misleading for a
    substitution. We model the bias curve as ``C(V) = C_nom / (1 + (V/vth)^k)``
    and fit ``k`` from the two REAL anchors in the DB (no estimation):

      * 50% loss at ``vthMLCC``          → C(vth)   = C_nom/2 (automatic)
      * ``capacitanceSaturationMLCC``    → C(v_rated) = sat·C_nom

    Returns None (→ "unverified", never an estimate) if any anchor is missing or
    out of range, or for class-1 (C0G/NP0) parts which don't bias-derate.
    """
    import math

    if c_nom is None or c_nom <= 0 or v_op < 0:
        return None
    if not (isinstance(vth, (int, float)) and vth > 0):
        return None
    if not (isinstance(sat_ratio, (int, float)) and 0 < sat_ratio < 1):
        return None
    if not (isinstance(v_rated, (int, float)) and v_rated > 0) or v_rated == vth:
        return None
    # Fit k from the rated-voltage anchor: sat = 1/(1+(v_rated/vth)^k).
    ratio = (1.0 - sat_ratio) / sat_ratio  # = (v_rated/vth)^k, must be > 0
    if ratio <= 0:
        return None
    k = math.log(ratio) / math.log(v_rated / vth)
    if k <= 0:
        return None
    return c_nom / (1.0 + (v_op / vth) ** k)


def mlcc_bias_param(
    orig: dict[str, Any], sub: dict[str, Any], v_op: float | None
) -> dict[str, Any] | None:
    """Compare original vs substitute EFFECTIVE capacitance at the operating
    voltage. Returns a render-ready param dict (higher_better, 10% margin) or
    None when it can't be computed (no operating voltage / not class-2 MLCC /
    model anchors absent — surfaced elsewhere as the nominal-value check)."""
    if v_op is None or v_op <= 0:
        return None
    oc = effective_capacitance_at_bias(
        _as_float(orig.get("capacitance")),
        _as_float(orig.get("voltage")),
        _as_float(orig.get("capacitance_saturation_mlcc")),
        _as_float(orig.get("vth_mlcc")),
        v_op,
    )
    sc = effective_capacitance_at_bias(
        _as_float(sub.get("capacitance")),
        _as_float(sub.get("voltage")),
        _as_float(sub.get("capacitance_saturation_mlcc")),
        _as_float(sub.get("vth_mlcc")),
        v_op,
    )
    if oc is None and sc is None:
        return None
    spec = ParamSpec("c_bias", f"C @ {v_op:g}V", "F", HIGHER_BETTER, 0.9)
    return compare_param(spec, oc, sc)


# Connector families whose parts terminate a wire or a PCB edge directly —
# there is no discrete counterpart connector to strand, so a cross-vendor swap
# doesn't break a mated pair.
_NO_MATE_FAMILIES = {"terminalblock", "cardedge"}


def connector_mating_check(
    orig_params: dict[str, Any] | None,
    sub_params: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Mating-system compatibility verdict for a connector substitution.

    The one thing a parametric table can't capture: a connector is half of a
    mated PAIR. Substituting one half with another vendor's series breaks the
    pair unless the interface is standardized (USB, RJ45, D-Sub, …) or the
    part is a commodity pin header at the same pitch. Military/interface
    standards exist precisely so cross-vendor halves intermate; proprietary
    series (Micro-Fit, PicoBlade, MTA, WR-WTB…) do NOT intermate across
    vendors even at identical pitch. Returns a render-ready param dict
    (same shape as compare_param) or None when there is no substitute side.
    """

    def _n(v: Any) -> str:
        return _norm_class(v)

    if not sub_params:
        return None
    row = {
        "name": "mating_interface",
        "label": "Mating system",
        "original": str(orig_params.get("series") or "") if orig_params else "",
        "substitute": str(sub_params.get("series") or ""),
        "critical": True,
    }
    if not orig_params:
        row["verdict"] = UNVERIFIED
        row["note"] = (
            "Mating system: original connector is not in the internal DB — "
            "intermateability cannot be verified. Identify the original's mating "
            "counterpart before substituting."
        )
        return row

    o_series, s_series = _n(orig_params.get("series")), _n(sub_params.get("series"))
    o_std = _n(orig_params.get("interface_standard"))
    s_std = _n(sub_params.get("interface_standard"))
    fam = _n(sub_params.get("family")) or _n(orig_params.get("family"))

    if fam in _NO_MATE_FAMILIES:
        row["verdict"] = PASS
        row["note"] = (
            "Mating system: terminal-block / card-edge style — no discrete mating "
            "half; verify wire gauge range / board thickness instead."
        )
        return row
    if o_series and s_series and o_series == s_series:
        row["verdict"] = PASS
        row["note"] = f"Mating system: same series ({sub_params.get('series')})"
        return row
    if o_std and s_std and o_std == s_std:
        row["verdict"] = PASS
        row["note"] = (
            f"Mating system: standardized interface ({sub_params.get('interface_standard')}) "
            "— intermateable across vendors by standard."
        )
        return row
    fam_o, fam_s = _n(orig_params.get("family")), _n(sub_params.get("family"))
    header_fams = {"pinheadersocket", "boardtoboard"}
    if "pinheadersocket" in (fam_o, fam_s) and {fam_o, fam_s} <= header_fams:
        op, sp = orig_params.get("pitch_mm"), sub_params.get("pitch_mm")
        if (
            isinstance(op, (int, float))
            and isinstance(sp, (int, float))
            and max(op, sp) > 0
            and abs(op - sp) / max(op, sp) <= 0.02
        ):
            if fam_o == fam_s:
                row["verdict"] = PASS
                row["note"] = (
                    "Mating system: commodity pin header/socket at matching pitch — "
                    "verify mated stack height, pin length and keying."
                )
            else:
                # One vendor files its headers under boardToBoard — likely a
                # commodity header, but it could be a mezzanine SYSTEM, which
                # does not intermate with plain headers.
                row["verdict"] = WARN
                row["note"] = (
                    "Mating system: likely a commodity pin header at matching pitch, "
                    "but the substitute is classed board-to-board — confirm it is a "
                    "plain header/socket strip (not a mezzanine system) and verify "
                    "mated stack height and keying."
                )
            return row
    row["verdict"] = FAIL
    row["note"] = (
        f"Mating system: different mating systems "
        f"({orig_params.get('series') or 'unknown series'} → "
        f"{sub_params.get('series') or 'unknown series'}) — the counterpart "
        "housing/terminals must be replaced as a matched set from the substitute's "
        "series; verify intermateability, latch geometry and contact plating "
        "compatibility (never mate tin to gold)."
    )
    return row


def worst_verdict(results: list[dict[str, Any]]) -> str:
    """The most severe verdict across a parameter result list."""
    order = {PASS: 0, UNVERIFIED: 1, WARN: 2, FAIL: 3}
    worst = PASS
    for r in results:
        if order.get(r.get("verdict", PASS), 0) > order[worst]:
            worst = r["verdict"]
    return worst
