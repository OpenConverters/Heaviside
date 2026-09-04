"""Build a catalogue record by READING a part's datasheet.

The last resort in the librarian's chain, and the one that needs the most care.
A distributor payload is a table somebody curated; this is a PDF read by a
language model, and this project has already shipped 177 invented magnetics to
production once (ABT #247) and 1,232 more later (#316). So the rules here are
narrow on purpose:

* **A real document, every time.** ``kimi_seek`` will answer from the model's
  own memory of a part number when given no text, and that is exactly the
  failure mode that produced those fabricated parts. This module never calls it
  that way — no fetched datasheet, no record.
* **The same schema gate as every other source.** The envelope goes through
  ``validate_component`` before anyone sees it. A half-read PDF is worse than
  no answer, because it arrives wearing a datasheet's authority.
* **Provenance names the PDF.** Every record says which document it was read
  from and that a model did the reading, so a reviewer can open the same page
  and check the numbers.
* **Staged, never applied.** Like every other source: a human decides whether a
  machine-read part belongs in the catalogue.

What it is good for: parts no distributor API carries (IPA045N10N3G is an
Infineon MOSFET Digi-Key does not list), and parts a distributor DOES carry but
cannot describe completely — Digi-Key rarely publishes Coss, so the MOSFET
converter refuses perfectly real parts for a field the datasheet states plainly
on page 2.
"""

from __future__ import annotations

import datetime
import logging
import re
from typing import Any

from heaviside.librarian.fetcher.base import FetcherError

logger = logging.getLogger(__name__)

__all__ = ["DatasheetSourceError", "envelope_from_datasheet", "SUPPORTED"]


class DatasheetSourceError(FetcherError):
    """The datasheet route could not produce a record, with the reason."""


# Categories whose mapping from the seeker's fields to the TAS envelope is
# unambiguous. Anything else is refused BY NAME rather than guessed at.
#
# Capacitors and resistors are deliberately absent even though the electrical
# fields would map cleanly, because their schemas require a `technology` from a
# specific taxonomy — 'ceramic-class-2' vs 'film-polypropylene',
# 'thinFilm' vs 'thickFilm' — that a datasheet states obliquely if at all. The
# distributor converters resolve it from a curated Family/Series field and
# REFUSE the part when that is missing; a model asked to pick from the enum
# would always return something, and a wrong technology silently changes what
# the part is. Those two categories are also the best covered by distributors,
# so the gap this route exists to close is not there. MOSFETs are: Digi-Key
# rarely publishes Coss, and the part that prompted all this — IPA045N10N3G —
# is a MOSFET Digi-Key does not list at all.
# BJT is absent on purpose: heaviside.librarian.safe_access has no "bjts"
# category, so the librarian cannot write one to TAS even if it read one
# perfectly. Adding a category is a data-governance change, not this module's
# to make.
SUPPORTED = ("mosfet", "diode", "capacitor", "resistor", "igbt")


# ---------------------------------------------------------------------------
# technology: read off the page, never chosen by the model
# ---------------------------------------------------------------------------
# Capacitor and resistor schemas require a `technology` from a fixed taxonomy,
# and that is why these families were left out at first: a model asked to pick
# from an enum always returns something, and a wrong technology silently
# changes what the part IS.
#
# But a datasheet SAYS which it is — "X7R", "Thick Film", "Aluminum
# electrolytic" — so the mapping is a lookup, done here, in code, against words
# that must actually appear in the document. Most specific first, because
# "tantalum polymer" is not "tantalum wet" and "bulk metal foil" is not "metal
# foil". Nothing matching means the datasheet did not say, and the part is
# refused rather than assigned a plausible technology.
_TECHNOLOGY_WORDS = {
    "capacitor": (
        ("c0g", "ceramic-class-1"), ("np0", "ceramic-class-1"),
        ("npo", "ceramic-class-1"), ("class 1 ceramic", "ceramic-class-1"),
        ("x7r", "ceramic-class-2"), ("x5r", "ceramic-class-2"),
        ("x6s", "ceramic-class-2"), ("x7s", "ceramic-class-2"),
        ("x8r", "ceramic-class-2"), ("x8l", "ceramic-class-2"),
        ("y5v", "ceramic-class-3"), ("z5u", "ceramic-class-3"),
        ("aluminum polymer", "aluminum-electrolytic-polymer"),
        ("aluminium polymer", "aluminum-electrolytic-polymer"),
        ("hybrid polymer", "aluminum-hybrid-polymer"),
        ("conductive polymer aluminum", "aluminum-electrolytic-polymer"),
        ("polymer tantalum", "tantalum-polymer"),
        ("tantalum polymer", "tantalum-polymer"),
        ("tantalum mno2", "tantalum-mno2"), ("manganese dioxide", "tantalum-mno2"),
        ("niobium oxide", "niobium-oxide"),
        ("aluminum electrolytic", "aluminum-electrolytic-wet"),
        ("aluminium electrolytic", "aluminum-electrolytic-wet"),
        ("tantalum", "tantalum-wet"),
        ("polypropylene", "film-polypropylene"),
        ("polyphenylene sulfide", "film-polyphenylene-sulfide"),
        ("polyester", "film-polyester"), ("metallized pet", "film-polyester"),
        ("paper", "film-paper"), ("mica", "mica"),
        ("edlc", "supercapacitor-edlc"), ("electric double layer", "supercapacitor-edlc"),
    ),
    "resistor": (
        ("bulk metal foil", "bulkMetalFoil"), ("metal foil", "metalFoil"),
        ("thick film", "thickFilm"), ("thin film", "thinFilm"),
        ("metal oxide", "metalOxide"), ("metal film", "metalFilm"),
        ("wirewound", "wirewound"), ("wire wound", "wirewound"),
        ("carbon composition", "carbonComposition"), ("carbon film", "carbonFilm"),
        ("current sense", "currentSenseShunt"), ("shunt", "currentSenseShunt"),
        ("melf", "melf"),
    ),
}


# The body a capacitor has, in the words a datasheet uses. CAS wants a
# shapeType beside the assembly, and a datasheet always says which it is.
_SHAPE_WORDS = (
    ("radial cylindrical", "Radial Cylindrical"), ("snap-in", "Radial Cylindrical"),
    ("radial", "Radial Cylindrical"), ("axial", "Axial Cylindrical"),
    ("can", "Radial Cylindrical"), ("screw", "Screw Terminal"),
    ("rectangular", "Rectangular"), ("chip", "Rectangular"),
    ("mlcc", "Rectangular"), ("box", "Rectangular"),
)


def shape_from_text(text: str) -> str:
    low = text.lower()
    for word, value in _SHAPE_WORDS:
        if word in low:
            return value
    return ""


# How the part mounts, in the words a datasheet uses. Same rule as technology:
# read off the page, never assumed.
_ASSEMBLY_WORDS = (
    ("snap-in", "Snap-In"), ("snap in", "Snap-In"),
    ("screw terminal", "Screw Type"), ("screw type", "Screw Type"),
    ("surface mount", "SMT"), ("surface-mount", "SMT"), ("smd", "SMT"),
    ("smt", "SMT"), ("chip capacitor", "SMT"), ("chip resistor", "SMT"),
    ("mlcc", "SMT"), ("chip", "SMT"),   # a "chip" passive is a surface-mount one
    ("through hole", "THT"), ("through-hole", "THT"), ("radial", "THT"),
    ("axial", "THT"), ("tht", "THT"),
)


def assembly_from_text(text: str) -> str:
    low = text.lower()
    for word, value in _ASSEMBLY_WORDS:
        if word in low:
            return value
    return ""


def _toleranced(value: float, tol_fraction: float | None) -> dict[str, float]:
    """A value as the schema wants it: nominal, and the tolerance band when the
    datasheet states one. A capacitance is a dimensionWithTolerance, not a
    number, because a 100 nF +/-10 % part is not the same as a 100 nF +/-1 %
    one and the catalogue has to be able to tell them apart."""
    out = {"nominal": value}
    if tol_fraction:
        out["minimum"] = value * (1.0 - tol_fraction)
        out["maximum"] = value * (1.0 + tol_fraction)
    return out


def technology_from_text(category: str, text: str) -> str:
    """The technology this datasheet states, or "" when it states none."""
    low = text.lower()
    for word, value in _TECHNOLOGY_WORDS.get(category, ()):
        if word in low:
            return value
    return ""

_MIN_TEXT = 400          # a PDF yielding less than this is a scan or a stub

# A datasheet has a characteristics table; a product landing page does not.
# Infineon's /part/ page names the part, reads cleanly, and carries none of
# its numbers — so it passed the "names the part" test and produced a reading
# the two passes could not agree on a single field of.
_DATASHEET_MARKERS = (
    "absolute maximum", "electrical characteristic", "electrical parameter",
    "thermal characteristic", "static characteristic", "dynamic characteristic",
    "characteristics", "parameter symbol", "test condition", "rating",
)
_MIN_MARKERS = 2
# The whole document, not its first pages. A datasheet puts absolute maximums
# up front and the dynamic characteristics — where the gate charge lives —
# several pages in: Nexperia's BUK98180 sheet is 20 000 characters and its Qg
# row sat past a 12 000-character cutoff, so the part was refused for a number
# that was in the file all along.
_MAX_TEXT = 45000


def _num(v: Any, *, prefer: str = "max") -> float | None:
    """A single number from whatever the reading produced.

    The seeker is asked for scalars and often returns a datasheet's own shape
    instead — ``{"typ": 0.0037, "max": 0.0045}`` for an Rds(on) that the
    datasheet prints as both. Discarding those (the first version did) threw
    away a correctly-read value; averaging them would invent one. The
    datasheet's GUARANTEED limit is the max, so that is what a catalogue
    carries, and ``prefer`` names the end for the rare field where the other
    one is the specification.
    """
    if isinstance(v, dict):
        for key in ((prefer, "typ", "nominal", "min") if prefer == "max"
                    else (prefer, "typ", "nominal", "max")):
            got = _num(v.get(key), prefer=prefer)
            if got is not None:
                return got
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if v != v or v in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return float(v)


# What a real part of this kind can be, in SI. The seeker is told to answer in
# SI and mostly does, but not always: one run returned a gate charge of 8.8
# (i.e. nanocoulombs, unconverted) and the next returned 1.17 for the same PDF.
# A number that far outside physics is not a value to rescue by guessing a
# prefix — that is how a plausible-looking wrong part gets built — so it is
# dropped when the field is optional, and refuses the record when it is not.
_PLAUSIBLE = {
    "drainSourceVoltage": (1.0, 20e3),        # V
    "continuousDrainCurrent": (1e-3, 5e3),    # A
    "onResistance": (1e-6, 1e4),              # ohm
    "onResistanceVgs": (1.0, 30.0),           # V
    "onResistanceId": (1e-3, 5e3),            # A
    "capacitanceMeasurementVds": (1.0, 20e3), # V
    "totalGateCharge": (1e-10, 1e-5),         # C  (0.1 nC … 10 µC)
    "outputCapacitance": (1e-15, 1e-6),       # F
    "gateThresholdVoltage": (0.1, 20.0),      # V
    "reverseVoltage": (0.1, 20e3),            # V
    "forwardVoltage": (0.05, 20.0),           # V
    "forwardCurrent": (1e-6, 5e3),            # A
    "reverseRecoveryCharge": (1e-12, 1e-3),   # C
    "reverseRecoveryTime": (1e-12, 1e-3),     # s
    "junctionTemperatureMax": (25.0, 400.0),  # degC
    "capacitance": (1e-15, 10.0),             # F
    "ratedVoltage": (0.5, 1e5),               # V
    "esr": (1e-6, 1e6),                       # ohm
    "ripplecurrent": (1e-6, 1e3),             # A
    # A datasheet prints a tolerance as a PERCENTAGE ("10 %"), and the record
    # carries a fraction. The band has to admit what the page says or the value
    # is dropped before it can be converted; the conversion happens after.
    "tolerance": (1e-3, 100.0),
    "resistance": (1e-6, 1e12),               # ohm
    "powerRating": (1e-4, 1e4),               # W
    "collectorEmitterVoltage": (1.0, 20e3),   # V
    "continuousCollectorCurrent": (1e-3, 5e3),
    "collectorCurrent": (1e-6, 5e3),          # A
    "collectorEmitterSaturation": (0.05, 20.0),
}


# SI prefixes a datasheet prints and a reading may forget to apply. Only these
# steps are considered, so a "correction" can never be an arbitrary rescale.
_PREFIXES = ((1e-12, "p"), (1e-9, "n"), (1e-6, "u"), (1e-3, "m"),
             (1.0, ""), (1e3, "k"), (1e6, "M"))
# "u" is also written µ (U+00B5) and μ (U+03BC) in real PDFs.
_PREFIX_ALIASES = {"u": ("u", "\u00b5", "\u03bc")}


def _mantissas(value: float) -> tuple[str, ...]:
    """How a datasheet might print this number: 4.5, 4.50, 0.00210, 45 …

    Trailing zeros are part of how a spec is written — Vishay prints an
    on-resistance as "0.00210", not "0.0021" — so padded forms are generated
    too. A form that has ROUNDED the value into a different number is not this
    value and is dropped, and so is a form that is all zeros, which every page
    contains and which would therefore corroborate anything.
    """
    out: list[str] = []
    for text in (f"{value:g}", f"{value:.0f}", f"{value:.1f}", f"{value:.2f}",
                 f"{value:.3f}", f"{value:.4f}", f"{value:.5f}"):
        if text in out or text.strip("-0.") == "":
            continue
        try:
            if abs(float(text) - value) > abs(value) * 1e-6:
                continue
        except ValueError:
            continue
        out.append(text)
    return tuple(out)


# How far a unit may sit from its number and still be that number's unit.
# A datasheet table is extracted as text with the unit COLUMN detached from the
# value: on the IRFP4668 sheet "241" reads as "161 241 I = 81A" and the "nC"
# that qualifies it is 62 characters away, in the column header. Requiring
# adjacency threw away correct readings of well-formed datasheets.
_UNIT_COLUMN_CHARS = 220

# The unit symbol each field is printed in, as a set of spellings, because a
# PDF's text layer does not render units the way the page shows them. The ohm
# sign is the worst: Infineon's sheets come out as "4.5 mW" (Symbol-font omega
# read as W) and as "9.7 m\uf057" (a private-use glyph). Insisting on the real
# Omega rejected almost every RDS(on) there is.
_FIELD_UNIT: dict[str, tuple[str, ...]] = {
    "drainSourceVoltage": ("V",), "gateThresholdVoltage": ("V",),
    "onResistanceVgs": ("V",), "capacitanceMeasurementVds": ("V",),
    "reverseVoltage": ("V",), "forwardVoltage": ("V",),
    "continuousDrainCurrent": ("A",), "onResistanceId": ("A",),
    "forwardCurrent": ("A",),
    "onResistance": ("\u03a9", "\u2126", "\uf057", "W", "ohm", "Ohm", "OHM"),
    "totalGateCharge": ("C",), "reverseRecoveryCharge": ("C",),
    "outputCapacitance": ("F",),
    "reverseRecoveryTime": ("s",),
    "junctionTemperatureMax": ("C", "\u00b0C"),
    "capacitance": ("F",), "ratedVoltage": ("V",),
    "esr": ("\u03a9", "\u2126", "\uf057", "W", "ohm", "Ohm", "OHM"),
    "ripplecurrent": ("A",), "tolerance": ("%",),
    "resistance": ("\u03a9", "\u2126", "\uf057", "W", "ohm", "Ohm", "OHM"),
    "powerRating": ("W",),
    "collectorEmitterVoltage": ("V",), "collectorEmitterSaturation": ("V",),
    "continuousCollectorCurrent": ("A",), "collectorCurrent": ("A",),
}


def _printed_in(text: str, mantissa: str, prefix: str, *, wide: bool = False,
                unit: tuple[str, ...] | str = ()) -> bool:
    """Is ``mantissa`` printed in the datasheet, qualified by ``prefix``?

    Matched on a NUMBER boundary, on BOTH sides and through the decimal point.
    A bare substring search let the "1" corroborate itself out of the "100" in
    "100 V"; excluding only a following digit still let it out of the "1.0" of
    an unrelated gate resistance. Accepting a 200 V part as a 1 V one is the
    single worst thing this check exists to prevent, so the boundary rejects a
    following digit, and a following decimal point that leads to one.

    ``wide`` looks for the prefix in the table row rather than right after the
    number. It is used ONLY for a value that needs an SI prefix to make sense,
    never for a bare one: a number that is already plausible as written must
    have its unit beside it, or "Figure 1 ... 200 V" would corroborate a 1 V
    part out of a caption.
    """
    # A table puts its unit in a column HEADER, which lands before the value
    # as often as after it: Vishay writes "RDS(on) max. (Ohm) at VGS = 10 V
    # 0.00210", with the unit 24 characters ahead of the number. Looking only
    # after it refused correct readings of well-formed sheets.
    #
    # The cost is real and worth naming: a unit found before a number is weaker
    # evidence than one found after it, so what stops a wrong reading here is
    # no longer this check alone. It is this check plus the physical bound plus
    # the two readings having to agree — and, in the end, the record being
    # staged for a human against the document it links.
    heads = _PREFIX_ALIASES.get(prefix, (prefix,)) if prefix else ("",)
    # The unit is required whenever we know it. Without that, an unprefixed
    # value matched on the mantissa alone and ANY number on the page
    # corroborated ANY field — the 1.0 of an internal gate resistance stood in
    # for a 1 V drain-source rating on a 200 V part.
    units = (unit,) if isinstance(unit, str) and unit else tuple(unit or ())
    # The unit is required only when this document actually spells it.
    #
    # Requiring it unconditionally scored 1 of 6 on real MOSFETs: a PDF's text
    # layer spells units however it likes, and an ohm sign in particular
    # arrives as "W", as a private-use glyph, or not at all. Dropping the
    # requirement outright would give up the check that stops a 200 V part
    # being read as a 1 V one, since "V" is always on the page.
    #
    # So: if none of the field's spellings appear ANYWHERE in the document, the
    # check cannot be applied to this field and the mantissa alone is taken. If
    # one does appear, the document can express that unit, and the value has to
    # be qualified by it.
    if units and not any(u in text or u.upper() in text for u in units):
        units = ()
    forms = [h + u for h in heads for u in units] if units else list(heads)
    span = _UNIT_COLUMN_CHARS if wide else 6
    # the header sits ahead of the value even in the narrow case
    back = _UNIT_COLUMN_CHARS if wide else 48
    for m in re.finditer(r"(?<![0-9.])" + re.escape(mantissa) + r"(?!\.?[0-9])", text):
        after = text[m.end(): m.end() + span]
        before = text[max(0, m.start() - back): m.start()]
        for form in forms:
            if not form:
                return True
            if form in after or form.upper() in after:
                return True
            if form in before or form.upper() in before:
                return True
    return False


def _forms_of(value: float):
    """Every (mantissa, prefix) a datasheet could print ``value`` as.

    0.0045 ohm is printed "4.5 m", never "0.0045", so a value that is already
    correct in SI has to be looked for in the units the document really uses.
    """
    for factor, prefix in _PREFIXES:
        m = value / factor
        # The unprefixed form is ALWAYS worth trying: a datasheet may print a
        # small quantity in base units ("0.00210 Ohm") rather than scale it,
        # and a lower bound of 0.05 skipped exactly those.
        lo = 1e-6 if factor == 1.0 else 0.05
        if lo <= abs(m) < 100000:
            for mantissa in _mantissas(m):
                yield mantissa, prefix


def _is_printed(value: float, text: str, field: str = "") -> bool:
    """Is this value printed anywhere, in any units the document uses?

    A prefixed form may take its unit from the table column; an unprefixed one
    may not, for the reason in :func:`_printed_in`.
    """
    unit = _FIELD_UNIT.get(field, ())
    forms = list(_forms_of(value))
    for mantissa, prefix in forms:
        if _printed_in(text, mantissa, prefix, unit=unit):
            return True
    for mantissa, prefix in forms:
        if prefix and _printed_in(text, mantissa, prefix, wide=True, unit=unit):
            return True
    return False


def _plausible(field: str, value: float | None) -> bool:
    if value is None:
        return False
    lo, hi = _PLAUSIBLE.get(field, (float("-inf"), float("inf")))
    return lo <= value <= hi


def _corroborated(field: str, raw: float | None, text: str) -> tuple[float | None, str]:
    """The value this reading supports, checked against the document itself.

    Extraction gets UNITS wrong in both directions and in both engines: asked
    for coulombs, one run returned 117 (nanocoulombs, unconverted); a stricter
    prompt turned a 100 V part into 1.0 V and 175 degC into 448.15 (kelvin);
    the deterministic table reader returned a Coss of 103. None of those is a
    value to salvage by picking whichever power of ten looks sensible — that
    is how a confident, wrong part gets built, and this project has shipped
    fabricated parts to production twice already.

    So a number is accepted only when it is BOTH physically possible AND
    actually printed in the datasheet at some scale the datasheet uses. 117
    becomes 1.17e-7 because "117 nC" is on the page; 1.0 for a drain-source
    voltage is dropped because nothing on the page says 1 V.

    Returns ``(value, note)`` — the note is empty when the value was taken as
    given, and says what happened otherwise.
    """
    if raw is None:
        return None, ""
    # 1. taken as given, when it is possible AND the document prints it (in
    #    whatever units the document prints it in: 0.0045 appears as "4.5 m").
    if _plausible(field, raw) and _is_printed(raw, text, field):
        return raw, ""
    # 2. otherwise the reading may have dropped an SI prefix. Only a scale the
    #    document itself prints is accepted.
    unit = _FIELD_UNIT.get(field, ())
    # Adjacency is the strong evidence, so EVERY prefix is tried that way
    # before any of them is allowed to take its unit from the table column.
    for wide in (False, True):
        for factor, prefix in _PREFIXES:
            if factor == 1.0:
                continue
            scaled = raw * factor
            if not _plausible(field, scaled):
                continue
            for mantissa in _mantissas(raw):
                if _printed_in(text, mantissa, prefix, wide=wide, unit=unit):
                    where = " (unit taken from the table column)" if wide else ""
                    return scaled, (f"{field} read as {mantissa} {prefix} and "
                                    f"converted to SI{where}")
    if _plausible(field, raw):
        return None, f"{field}={raw:g} is possible but is not printed in the datasheet"
    return None, f"{field}={raw:g} is not a possible value"


# ---------------------------------------------------------------------------
# Reading the numbers: the model finds the row, the code does the arithmetic
# ---------------------------------------------------------------------------
# Asking a model for SI floats was the mistake. Across five runs on one
# Infineon PDF the same gate charge came back as 88, 117, 8.8, 11.7 and 1.17 —
# every one of them a real number from the page with the exponent guessed
# differently — and a prompt demanding SI made it worse, turning a 100 V part
# into 1.0 V and 175 degC into 448.15 K.
#
# So the division of labour changes. The model does the part it is good at:
# finding the right row of the right table and copying what is printed there,
# VERBATIM, units and all. The code does the part it is good at: turning
# "88 nC" into 8.8e-8. Neither is asked to do the other's job.
_VERBATIM_SYSTEM = """You read one electronic component's datasheet and copy values out of it. You do NOT convert units and you do NOT do arithmetic.

For each field, return the value EXACTLY as the datasheet prints it, including its unit, as a string: "88 nC", "4.5 mOhm", "100 V", "175 °C". When the sheet gives typ and max, return the MAX. When a field is absent from the datasheet, return null. NEVER return a value that is not printed in the text you were given.

Return ONLY a JSON object with these keys and string-or-null values:
"""

_VERBATIM_FIELDS = {
    "mosfet": {
        "drainSourceVoltage": "drain-source breakdown voltage V(BR)DSS / VDS",
        "continuousDrainCurrent": "continuous drain current ID",
        # A datasheet lists RDS(on) at SEVERAL gate drives, and asking for
        # "the max" made the answer depend on which row the model happened to
        # read: two passes over the Vishay SUM60020E returned 2.47 mOhm at
        # VGS=7.5 V and 2.10 mOhm at VGS=10 V, disagreed, and the part was
        # refused. Naming the condition makes the question have one answer.
        "onResistance": ("on-state resistance RDS(on) — the MAXIMUM value at the "
                         "HIGHEST gate-source voltage the table lists"),
        # RDS(on) is meaningless without the gate drive it was measured at: a
        # logic-level part specified at 4.5 V and a standard one at 10 V are not
        # the same number, and the catalogue has fields for both conditions.
        # Capturing them also shows a reviewer WHICH row of the table was read.
        "onResistanceVgs": "the HIGHEST VGS that the RDS(on) table lists",
        "onResistanceId": "the ID in that same RDS(on) row",
        "capacitanceMeasurementVds": "the VDS the capacitances are specified at",
        # Named the way each vendor names it: Nexperia prints "Q G(tot)",
        # Infineon "Qg", Vishay "Total gate charge". Asking only for "QG"
        # got no answer from the Nexperia sheet and the part was refused for
        # a number printed on it.
        "totalGateCharge": ("total gate charge — QG, Qg, QG(tot), Qg(tot), "
                            "'Total gate charge' or 'gate charge total'"),
        "outputCapacitance": "output capacitance Coss or Co(ss)",
        "gateThresholdVoltage": "gate threshold voltage VGS(th), the MAX",
        "junctionTemperatureMax": "maximum junction temperature Tj",
    },
    "capacitor": {
        "capacitance": "nominal capacitance C",
        "ratedVoltage": "rated DC voltage",
        "esr": "equivalent series resistance ESR",
        "ripplecurrent": "rated RMS ripple current",
        "tolerance": "capacitance tolerance, as a percentage",
    },
    "resistor": {
        "resistance": "nominal resistance R",
        "powerRating": "rated power dissipation at 70 C",
        "tolerance": "resistance tolerance, as a percentage",
    },
    "igbt": {
        "collectorEmitterVoltage": "collector-emitter breakdown voltage VCES",
        "continuousCollectorCurrent": "continuous collector current IC",
        "collectorEmitterSaturation": "collector-emitter saturation voltage VCE(sat)",
        "junctionTemperatureMax": "maximum junction temperature Tj",
    },
    "bjt": {
        "collectorEmitterVoltage": "collector-emitter breakdown voltage VCEO",
        "collectorCurrent": "continuous collector current IC",
        "junctionTemperatureMax": "maximum junction temperature Tj",
    },
    "diode": {
        "reverseVoltage": "repetitive peak reverse voltage VRRM or VR",
        "forwardCurrent": "average forward current IF(AV)",
        "forwardVoltage": "forward voltage VF, the MAX",
        "reverseRecoveryCharge": "reverse recovery charge Qrr",
        "reverseRecoveryTime": "reverse recovery time trr",
        "junctionTemperatureMax": "maximum junction temperature Tj",
    },
}

# Unit symbols, longest first so "mOhm" is not read as "m" then "Ohm".
_UNIT_SUFFIXES = ("ohms", "ohm", "degc", "\u00b0c", "c",
                  "v", "a", "f", "s", "w", "j", "hz")
_SI_PREFIX = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "\u00b5": 1e-6, "\u03bc": 1e-6,
              "m": 1e-3, "k": 1e3, "M": 1e6, "G": 1e9}


def parse_verbatim(printed: Any) -> float | None:
    """"88 nC" -> 8.8e-08. "4.5 mOhm" -> 0.0045. "-55 to 175 degC" -> 175.

    Case matters for exactly one prefix pair — 'm' is milli and 'M' is mega —
    so the prefix is read before anything is folded. The unit symbol is folded
    with a table rather than ``.lower()``, because lower-casing the ohm sign
    turns Omega into omega and the unit stops being recognised.
    """
    if isinstance(printed, (int, float)) and not isinstance(printed, bool):
        return float(printed)
    if not isinstance(printed, str):
        return None
    t = printed.strip().replace(",", "").replace("\u2212", "-").replace("\u2013", "-")
    numbers = list(re.finditer(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", t))
    if not numbers:
        return None
    # A datasheet cell is rarely just a number: "max 4.5 mOhm", "typ. 88 nC",
    # "-55 to 175 degC". Every field asked for here is a MAX, so a range takes
    # its upper end; anything else takes the first number, because "0.5 V at
    # 100 A" is a forward voltage, not a current.
    is_range = re.search(r"\d\s*(?:to|\.\.\.|\u2026|~|/|-)\s*[-+]?\d", t) is not None
    m = numbers[-1] if is_range else numbers[0]
    try:
        value = float(m.group(0))
    except ValueError:
        return None
    rest = t[m.end():].strip()
    if not rest:
        return value
    head = rest[0]
    tail = rest[1:].lstrip()
    # A prefix only counts when a unit follows it: the "m" of "mOhm" is milli,
    # the "m" of "m" alone is metres, and the "A" of "A" is amperes not atto.
    if head in _SI_PREFIX and tail and _is_unit(tail):
        return value * _SI_PREFIX[head]
    return value


def _is_unit(tail: str) -> bool:
    """Does ``tail`` begin with a unit symbol?

    Folded through an explicit table: ``"\u03a9".lower()`` is ``"\u03c9"``,
    a different character, so a case-insensitive compare silently stops
    recognising ohms — which is most of what this module reads.
    """
    folded = tail.replace("\u2126", "\u03a9").replace("\u03c9", "\u03a9")
    if folded.startswith("\u03a9"):
        return True
    return folded.lower().startswith(_UNIT_SUFFIXES)


def _read_verbatim(mpn: str, category: str, text: str, call=None,
                   passes: int = 2) -> tuple[dict[str, Any], list[str]]:
    """Ask for each field exactly as the datasheet prints it, twice, and keep
    only what both readings agree on.

    Corroboration proves a number is ON the page; it cannot prove it came from
    the RIGHT row. On IPA045N10N3G, RDS(on) came back as 4.5 mOhm on one run
    and 4.7 mOhm on the next — both printed, at different conditions. A field
    the two passes disagree about is dropped and named, because a value that
    changes when you ask again is not one the catalogue should carry.

    Returns ``(fields, disagreements)``; fields maps name -> (value, printed).
    """
    fields = _VERBATIM_FIELDS.get(category)
    if not fields:
        return {}, []
    if call is None:
        from heaviside.agents.llm_call import call_llm as call
    system = _VERBATIM_SYSTEM + "\n".join(f'  "{k}": {v}' for k, v in fields.items())
    user = (f"Component: MPN={mpn}, category={category}.\n\n"
            f"DATASHEET TEXT:\n{text[:_MAX_TEXT]}")

    from heaviside.librarian.datasheet.seeker import _parse_json_lenient

    reads: list[dict[str, Any]] = []
    for _ in range(max(1, passes)):
        raw = call(system, user, json_mode=True, max_tokens=900)
        obj = _parse_json_lenient(raw)
        reads.append(obj if isinstance(obj, dict) else {})

    out: dict[str, Any] = {}
    disagreed: list[str] = []
    for field in fields:
        parsed = [(parse_verbatim(r.get(field)), r.get(field)) for r in reads]
        values = [v for v, _ in parsed if v is not None]
        if not values:
            continue
        if len(values) < len(reads):
            # one pass found it and the other did not: that is not agreement
            disagreed.append(f"{field} (found in only {len(values)} of {len(reads)} readings)")
            continue
        first = values[0]
        if any(abs(v - first) > abs(first) * 1e-9 for v in values[1:]):
            disagreed.append(f"{field} ({' vs '.join(f'{v:g}' for v in values)})")
            continue
        printed = next(p for v, p in parsed if v is not None)
        out[field] = (first, str(printed))
    return out, disagreed


def _manufacturer_from_host(host: str) -> str:
    """Who publishes documents at this hostname.

    When nothing else names the manufacturer, the site the datasheet was
    fetched FROM is real evidence rather than a guess — a PDF served by
    infineon.com is Infineon's. It is only trusted for a host that is not a
    reseller or an aggregator, because those republish everyone's.
    """
    from heaviside.librarian.fetcher.websearch import (
        _AGGREGATORS, _DISTRIBUTORS, MANUFACTURER_DOMAINS, manufacturer_domain)

    host = (host or "").lower()
    if not host or any(a in host for a in _AGGREGATORS) or any(d in host for d in _DISTRIBUTORS):
        return ""
    domain = manufacturer_domain(host)
    if domain and MANUFACTURER_DOMAINS[domain]:
        return MANUFACTURER_DOMAINS[domain]
    bare = host[4:] if host.startswith("www.") else host
    # a plain corporate domain: "somevendor.com" -> "Somevendor"
    label = bare.split(".")[0]
    return label.capitalize() if len(label) >= 3 and label.isalpha() else ""


def _provenance(url: str, host: str) -> list[dict[str, Any]]:
    """Say plainly where this came from and that a model read it.

    A reviewer must be able to tell a machine-read record from a curated one at
    a glance, and open the same document to check the numbers.
    """
    return [{
        # The schema's enum for exactly this: a value read off the
        # manufacturer's own datasheet rather than a curated distributor feed.
        "source": "manufacturerDatasheet",
        "sourceName": f"manufacturer datasheet via {host}",
        "sourceUrl": url,
        "retrievedDate": datetime.date.today().isoformat(),
    }]


def _resolve(field: str, verbatim: dict, specs: dict, key: str, text: str):
    """One field's value: the verbatim reading first, the SI reading second.

    The verbatim pass carries the datasheet's own units, so its exponent is the
    document's rather than the model's arithmetic. It still goes through
    corroboration — a copied string can be copied from the wrong row.
    """
    got = verbatim.get(field)
    if got is not None:
        value, note = _corroborated(field, got[0], text)
        if value is not None:
            return value, (note or f"{field} read from the datasheet as {got[1]!r}")
    value, note = _corroborated(field, _num(specs.get(key)), text)
    return value, note


def _mosfet(specs: dict, mpn: str, mfr: str, url: str, host: str,
            text: str = "", verbatim: dict | None = None) -> tuple[dict | None, str]:
    verbatim = verbatim or {}
    e: dict[str, Any] = {}
    dropped: list[str] = []
    notes: list[str] = []
    for key, dst in (("vds_V", "drainSourceVoltage"), ("id_A", "continuousDrainCurrent"),
                     ("rds_on_ohm", "onResistance"), ("qg_C", "totalGateCharge"),
                     ("coss_F", "outputCapacitance")):
        v, note = _resolve(dst, verbatim, specs, key, text)
        if v is not None:
            e[dst] = v
            if note:
                notes.append(note)
        elif note:
            dropped.append(note)
    # the conditions the values were specified at, where the reading captured
    # them — an Rds(on) without its Vgs is not comparable to another part's
    for cond in ("onResistanceVgs", "onResistanceId", "capacitanceMeasurementVds"):
        got = verbatim.get(cond)
        if got is not None:
            v, _n = _corroborated(cond, got[0], text)
            if v is not None:
                e[cond] = v
    vth, note = _resolve("gateThresholdVoltage", verbatim, specs, "vgs_th_max_V", text)
    if vth is not None:
        e["gateThresholdVoltage"] = {"maximum": vth}
        if note:
            notes.append(note)
    elif note:
        dropped.append(note)
    missing = [k for k in ("drainSourceVoltage", "continuousDrainCurrent", "onResistance")
               if k not in e]
    if missing:
        why = f"the datasheet reading is missing {', '.join(missing)}"
        if dropped:
            why += f" — also dropped: {'; '.join(dropped)}"
        return None, why
    node: dict[str, Any] = {
        "manufacturerInfo": {
            "name": mfr, "reference": mpn, "status": "production",
            "datasheetUrl": url,
            "datasheetInfo": {
                "part": {"partNumber": mpn, "technology": specs.get("technology") or "Si",
                         "subType": "nChannel"},
                "electrical": e,
                "provenance": _provenance(url, host),
            },
        }
    }
    tmax, _ = _resolve("junctionTemperatureMax", verbatim, specs, "temp_max_C", text)
    if tmax is not None:
        node["manufacturerInfo"]["datasheetInfo"]["thermal"] = {"junctionTemperatureMax": tmax}
    return {"semiconductor": {"mosfet": node}}, "; ".join(notes)


# The schema's diode subtypes. A reading that names none of them gets the
# neutral 'rectifier' rather than a guess at something more specific.
_DIODE_SUBTYPES = ("rectifier", "schottky", "sicSchottky", "fastRecovery",
                   "ultrafast", "zener", "tvs", "esd")


def _diode_subtype(specs: dict) -> str:
    said = str(specs.get("subtype") or specs.get("diode_type") or "").strip()
    squashed = said.lower().replace(" ", "").replace("-", "")
    for known in _DIODE_SUBTYPES:
        if known.lower() == squashed:
            return known
    if "schottky" in squashed:
        return "sicSchottky" if "sic" in squashed else "schottky"
    if "zener" in squashed:
        return "zener"
    if "tvs" in squashed or "transient" in squashed:
        return "tvs"
    if "ultrafast" in squashed:
        return "ultrafast"
    if "fast" in squashed:
        return "fastRecovery"
    return "rectifier"


def _diode(specs: dict, mpn: str, mfr: str, url: str, host: str,
           text: str = "", verbatim: dict | None = None) -> tuple[dict | None, str]:
    verbatim = verbatim or {}
    e: dict[str, Any] = {}
    notes: list[str] = []
    for key, dst in (("vrrm_V", "reverseVoltage"), ("vf_V", "forwardVoltage"),
                     ("if_A", "forwardCurrent"), ("qrr_C", "reverseRecoveryCharge"),
                     ("trr_s", "reverseRecoveryTime")):
        v, note = _resolve(dst, verbatim, specs, key, text)
        if v is not None:
            e[dst] = v
            if note:
                notes.append(note)
    missing = [k for k in ("reverseVoltage", "forwardCurrent") if k not in e]
    if missing:
        return None, f"the datasheet reading is missing {', '.join(missing)}"
    node: dict[str, Any] = {
        "manufacturerInfo": {
            "name": mfr, "reference": mpn, "status": "production",
            "datasheetUrl": url,
            "datasheetInfo": {
                "part": {"partNumber": mpn, "technology": "Si",
                         "subType": _diode_subtype(specs)},
                "electrical": e,
                "provenance": _provenance(url, host),
            },
        }
    }
    tmax, _ = _resolve("junctionTemperatureMax", verbatim, specs, "temp_max_C", text)
    if tmax is not None:
        node["manufacturerInfo"]["datasheetInfo"]["thermal"] = {"junctionTemperatureMax": tmax}
    return {"semiconductor": {"diode": node}}, "; ".join(notes)


def _semi_node(mpn, mfr, url, host, part, e, tmax):
    node = {"manufacturerInfo": {
        "name": mfr, "reference": mpn, "status": "production", "datasheetUrl": url,
        "datasheetInfo": {"part": part, "electrical": e,
                          "provenance": _provenance(url, host)}}}
    if tmax is not None:
        node["manufacturerInfo"]["datasheetInfo"]["thermal"] = {"junctionTemperatureMax": tmax}
    return node


def _passive(kind, required, specs, mpn, mfr, url, host, text, verbatim):
    """Capacitor and resistor: same shape, different fields.

    `technology` is read off the page rather than chosen — see
    technology_from_text — and its absence refuses the part, because a
    capacitor whose dielectric nobody knows is not a catalogue capacitor.
    """
    verbatim = verbatim or {}
    e, notes = {}, []
    for field in _VERBATIM_FIELDS[kind]:
        v, note = _resolve(field, verbatim, specs, field, text)
        if v is not None:
            e[field] = v
            if note:
                notes.append(note)
    # "5 %" on the page is the fraction 0.05 in the record. A value already
    # below 1 is already a fraction and is left alone.
    if e.get("tolerance", 0) > 1.0:
        e["tolerance"] = e["tolerance"] / 100.0
    missing = [k for k in required if k not in e]
    if missing:
        return None, f"the datasheet reading is missing {', '.join(missing)}"
    technology = technology_from_text(kind, text)
    if not technology:
        return None, (f"the datasheet does not state a {kind} technology, and it is "
                      f"not a value to guess — the schema needs one of a fixed list")
    # the primary value carries its tolerance band, per the schema
    primary = "capacitance" if kind == "capacitor" else "resistance"
    e[primary] = _toleranced(e[primary], e.get("tolerance"))
    part = {"partNumber": mpn, "technology": technology}
    if kind == "capacitor":
        # CAS carries a capacitor's tolerance INSIDE the value's band, not as a
        # field of its own; RAS requires it as a field. Same reading, two
        # schemas, and the record has to match the one it claims to be.
        e.pop("tolerance", None)
    node = _semi_node(mpn, mfr, url, host, part, e, None)
    if kind == "capacitor":
        # CAS requires a mechanical block with a shape; RAS does not have one
        # at all, so this is added where it belongs and nowhere else.
        assembly = assembly_from_text(text)
        shape = shape_from_text(text)
        if not assembly or not shape:
            return None, ("the datasheet does not say how the part mounts and what "
                          "body it has, which the capacitor schema requires")
        node["manufacturerInfo"]["datasheetInfo"]["mechanical"] = {
            "shape": {"assembly": assembly, "shapeType": shape}}
    return {kind: node}, "; ".join(notes)


def _capacitor(specs, mpn, mfr, url, host, text="", verbatim=None):
    return _passive("capacitor", ("capacitance", "ratedVoltage"),
                    specs, mpn, mfr, url, host, text, verbatim)


def _resistor(specs, mpn, mfr, url, host, text="", verbatim=None):
    out, why = _passive("resistor", ("resistance", "tolerance", "powerRating"),
                        specs, mpn, mfr, url, host, text, verbatim)
    return out, why


def _bipolar(kind, required, subtype, specs, mpn, mfr, url, host, text, verbatim):
    verbatim = verbatim or {}
    e, notes = {}, []
    for field in _VERBATIM_FIELDS[kind]:
        if field == "junctionTemperatureMax":
            continue
        v, note = _resolve(field, verbatim, specs, field, text)
        if v is not None:
            e[field] = v
            if note:
                notes.append(note)
    missing = [k for k in required if k not in e]
    if missing:
        return None, f"the datasheet reading is missing {', '.join(missing)}"
    part = {"partNumber": mpn, "technology": "Si"}
    if subtype:
        part["subType"] = subtype
    tmax, _ = _resolve("junctionTemperatureMax", verbatim, specs, "temp_max_C", text)
    return {"semiconductor": {kind: _semi_node(mpn, mfr, url, host, part, e, tmax)}}, \
           "; ".join(notes)


def _igbt(specs, mpn, mfr, url, host, text="", verbatim=None):
    return _bipolar("igbt", ("collectorEmitterVoltage", "continuousCollectorCurrent",
                             "collectorEmitterSaturation"), "",
                    specs, mpn, mfr, url, host, text, verbatim)


def _bjt(specs, mpn, mfr, url, host, text="", verbatim=None):
    low = (text or "").lower()
    # a datasheet says which it is, in its title
    subtype = "pnp" if ("pnp" in low and "npn" not in low) else "npn" if "npn" in low else ""
    if not subtype:
        return None, "the datasheet does not say whether this is an NPN or a PNP"
    return _bipolar("bjt", ("collectorEmitterVoltage", "collectorCurrent"), subtype,
                    specs, mpn, mfr, url, host, text, verbatim)


_BUILDERS = {"mosfet": _mosfet, "diode": _diode, "capacitor": _capacitor,
             "resistor": _resistor, "igbt": _igbt}
_CATEGORY_TO_DB = {"mosfet": "mosfets", "diode": "diodes", "capacitor": "capacitors",
                   "resistor": "resistors", "igbt": "igbts"}


def envelope_from_datasheet(
    mpn: str,
    category: str,
    *,
    manufacturer: str = "",
    datasheet_url: str | None = None,
    search=None,
    fetch=None,
    seek=None,
) -> tuple[dict | None, str, dict[str, Any]]:
    """Read ``mpn``'s datasheet and build a validated catalogue envelope.

    Args:
        mpn: the exact part number.
        category: singular category ("mosfet", "diode", "capacitor", "resistor").
        manufacturer: sharpens the web search and names the record; when the
            datasheet reading reports one, that wins.
        datasheet_url: skip the search — pass the URL a distributor already
            gave us. This is the path that rescues a part the distributor
            FOUND but could not describe (Digi-Key rarely publishes Coss).
        search / fetch / seek: injection points (test hooks).

    Returns:
        ``(envelope, db_category, detail)`` on success, or ``(None, reason,
        detail)``. ``detail`` always carries what was tried — the URL read, the
        candidates considered — so a refusal is auditable.

    Raises:
        DatasheetSourceError: the route could not be attempted at all (no
            supported mapping for the category). Distinguished from a failed
            reading, which comes back as a reason.
    """
    detail: dict[str, Any] = {"mpn": mpn, "category": category, "tried": []}
    if category not in _BUILDERS:
        raise DatasheetSourceError(
            f"no datasheet mapping for category {category!r}; "
            f"supported: {', '.join(SUPPORTED)}")

    if fetch is None:
        from heaviside.pipeline.url_fetch import fetch_document as fetch
    if seek is None:
        from heaviside.librarian.datasheet.seeker import kimi_seek as seek

    # 1. which documents to read
    urls: list[str] = []
    if datasheet_url:
        urls.append(datasheet_url)
        detail["from"] = "the distributor's own datasheet link"
    else:
        if search is None:
            from heaviside.librarian.fetcher.websearch import find_datasheet_urls as search
        cands = search(mpn, manufacturer or None)
        detail["from"] = "a web search"
        detail["candidates"] = [{"url": c.url, "why": c.why} for c in cands]
        # PDFs first, then anything else — a maker's HTML product page often
        # carries the same table, and one bad PDF must not end the search.
        urls = [c.url for c in cands if c.is_pdf][:4]
        urls += [c.url for c in cands if not c.is_pdf][:2]
    if not urls:
        return None, "no datasheet could be found on the web for this part number", detail

    # 2. read the first candidate that is actually THIS part's datasheet.
    #
    # "yields text" was too weak a test. A search result can be a landing page,
    # a selection guide, or another part's sheet, and reading one of those
    # produced either nothing or — worse — a plausible record for the wrong
    # component. A datasheet for this part names this part, so that is the
    # test, and a candidate that fails it is skipped rather than accepted.
    text = ""
    used = ""
    rejected: list[str] = []
    squashed_mpn = re.sub(r"[^a-z0-9]", "", mpn.lower())
    for url in urls:
        detail["tried"].append(url)
        try:
            doc = fetch(url, timeout=60)
        except Exception as exc:
            rejected.append(f"{url[:60]}: could not be fetched ({str(exc)[:60]})")
            continue
        try:
            candidate_text = _text_of(doc)
        except Exception as exc:
            rejected.append(f"{url[:60]}: no text could be parsed ({str(exc)[:50]})")
            continue
        if len(candidate_text) < _MIN_TEXT:
            rejected.append(f"{url[:60]}: {len(candidate_text)} characters of text "
                            f"(a scanned image, most likely)")
            continue
        # the part number, however the sheet punctuates it
        if squashed_mpn not in re.sub(r"[^a-z0-9]", "", candidate_text.lower()):
            rejected.append(f"{url[:60]}: readable, but does not name {mpn}")
            continue
        low = candidate_text.lower()
        markers = sum(1 for m in _DATASHEET_MARKERS if m in low)
        if markers < _MIN_MARKERS:
            rejected.append(f"{url[:60]}: names {mpn} but reads like a product "
                            f"page, not a datasheet (no characteristics table)")
            continue
        text = candidate_text
        used = doc.final_url or url
        break
    if rejected:
        detail["rejected"] = rejected
    if not used:
        return None, ("no document was found that is this part's datasheet — "
                      + ("; ".join(rejected[:3]) if rejected else "nothing to read")), detail
    detail["read"] = used

    # 3. the model reads it — GROUNDED, never from memory.
    # raise_on_llm_error: "the model found nothing in this PDF" and "the model
    # could not be called" are different facts, and reporting the second as the
    # first is how a retired kimi-k2.5 looked like a page of unreadable
    # datasheets for as long as nobody checked.
    from heaviside.agents.llm_call import LLMCallError

    try:
        specs = seek(mpn, manufacturer or "", category,
                     datasheet_text=text[:_MAX_TEXT], raise_on_llm_error=True)
    except LLMCallError as exc:
        raise DatasheetSourceError(
            f"the datasheet was fetched but the extraction model could not be "
            f"called: {exc}") from exc
    except TypeError:
        # an injected test double that predates the keyword
        specs = seek(mpn, manufacturer or "", category, datasheet_text=text[:_MAX_TEXT])
    specs = specs if isinstance(specs, dict) else {}

    # 3b. and reads them AGAIN, verbatim, so the exponent comes from the page
    # rather than from the model's arithmetic. This pass is authoritative where
    # it answers; the pass above supplies the descriptive fields (manufacturer,
    # subtype) and covers anything it missed.
    try:
        verbatim, disagreed = _read_verbatim(mpn, category, text)
    except LLMCallError as exc:
        raise DatasheetSourceError(
            f"the datasheet was fetched but the extraction model could not be "
            f"called: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — the first pass may still carry it
        logger.debug("verbatim read failed for %s: %s", mpn, exc)
        verbatim, disagreed = {}, []
    if verbatim:
        detail["verbatim"] = {k: v[1] for k, v in verbatim.items()}
    if disagreed:
        # the fields two readings of the same PDF did not agree on: dropped,
        # and named, because that is exactly what a reviewer needs to look at
        detail["disagreed"] = disagreed
    if not specs and not verbatim:
        return None, "the datasheet was read but no specifications could be extracted from it", detail

    from urllib.parse import urlparse
    host = (urlparse(used).hostname or "").lower()
    mfr = str(specs.get("manufacturer") or manufacturer or "").strip()
    if not mfr:
        # Nothing named it, so fall back to who served the document. This is
        # the common case for a part no distributor carries: there is no
        # distributor record to take a manufacturer from, and the reading does
        # not always report one.
        mfr = _manufacturer_from_host(host)
        if mfr:
            detail["manufacturerFrom"] = f"the site the datasheet was served by ({host})"
    if not mfr:
        # A record with no manufacturer is not a catalogue record; the schema
        # wants one and a reviewer needs it.
        return None, ("the datasheet did not name a manufacturer, and it was not served "
                      f"by a site that identifies one ({host})"), detail

    envelope, why = _BUILDERS[category](specs, mpn, mfr, used, host, text, verbatim)
    if envelope is None:
        detail["extracted"] = {k: v for k, v in specs.items() if v is not None}
        return None, why, detail
    if why:
        # unit corrections the document itself corroborated, kept visible
        detail["conversions"] = why

    # 4. the strongest gate available — schema AND physics.
    # Every other source stops at the schema; this one is a PDF read by a
    # model, so it also goes through the C++ physics validator that judges
    # whether the numbers describe a part that could exist.
    db_cat = _CATEGORY_TO_DB[category]
    try:
        from heaviside.librarian.guards import guard_component
        from heaviside.librarian.tas import ValidationError

        guard_component(db_cat, envelope, validate_schema=True, validate_physics=True)
    except ValidationError as exc:
        detail["extracted"] = {k: v for k, v in specs.items() if v is not None}
        return None, f"the record read from the datasheet failed {db_cat} schema validation: {str(exc)[:200]}", detail
    except Exception as exc:
        return None, f"the record read from the datasheet could not be validated: {exc}", detail

    return envelope, db_cat, detail


def _text_of(doc: Any) -> str:
    """Text from a fetched document, PDF or HTML."""
    ctype = (getattr(doc, "content_type", "") or "").lower()
    content = doc.content
    if "pdf" in ctype or content[:5] == b"%PDF-":
        import tempfile
        from pathlib import Path

        from heaviside.pipeline.pdf_extract import extract_pdf_text

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(content)
            path = Path(tmp.name)
        try:
            return extract_pdf_text(path)
        finally:
            path.unlink(missing_ok=True)
    import re as _re

    html = content.decode("utf-8", "replace")
    html = _re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    return _re.sub(r"\s+", " ", _re.sub(r"(?s)<[^>]+>", " ", html))
