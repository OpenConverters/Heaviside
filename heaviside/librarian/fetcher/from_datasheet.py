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
SUPPORTED = ("mosfet", "diode")

_MIN_TEXT = 400          # a PDF yielding less than this is a scan or a stub
_MAX_TEXT = 12000        # what the seeker prompt itself truncates to


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
}


# SI prefixes a datasheet prints and a reading may forget to apply. Only these
# steps are considered, so a "correction" can never be an arbitrary rescale.
_PREFIXES = ((1e-12, "p"), (1e-9, "n"), (1e-6, "u"), (1e-3, "m"),
             (1.0, ""), (1e3, "k"), (1e6, "M"))
# "u" is also written µ (U+00B5) and μ (U+03BC) in real PDFs.
_PREFIX_ALIASES = {"u": ("u", "\u00b5", "\u03bc")}


def _mantissas(value: float) -> tuple[str, ...]:
    """How a datasheet might print this number: 4.5, 4.50, 45 …"""
    out = []
    for text in (f"{value:g}", f"{value:.1f}", f"{value:.2f}", f"{value:.0f}"):
        if text not in out and text not in ("0", "-0"):
            out.append(text)
    return tuple(out)


def _printed_in(text: str, mantissa: str, prefix: str) -> bool:
    """Is ``mantissa`` printed in the datasheet, followed by ``prefix``?

    Matched on a NUMBER boundary. A bare substring search let "1" corroborate
    itself out of the "100" in "100 V", which would accept a 100 V part as a
    1 V one — the single worst thing this check exists to prevent.

    The trailing window is short on purpose: "117 nC" corroborates a gate
    charge; "117" alone (a page number, an order code, an axis label) does not.
    """
    forms = _PREFIX_ALIASES.get(prefix, (prefix,)) if prefix else ("",)
    for m in re.finditer(r"(?<![0-9.])" + re.escape(mantissa) + r"(?![0-9])", text):
        after = text[m.end(): m.end() + 6]
        for form in forms:
            if not form:
                return True
            if form in after or form.upper() in after:
                return True
    return False


def _forms_of(value: float):
    """Every (mantissa, prefix) a datasheet could print ``value`` as.

    0.0045 ohm is printed "4.5 m", never "0.0045", so a value that is already
    correct in SI has to be looked for in the units the document really uses.
    """
    for factor, prefix in _PREFIXES:
        m = value / factor
        if 0.05 <= abs(m) < 100000:
            for mantissa in _mantissas(m):
                yield mantissa, prefix


def _is_printed(value: float, text: str) -> bool:
    return any(_printed_in(text, mantissa, prefix)
               for mantissa, prefix in _forms_of(value))


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
    if _plausible(field, raw) and _is_printed(raw, text):
        return raw, ""
    # 2. otherwise the reading may have dropped an SI prefix. Only a scale the
    #    document itself prints is accepted.
    for factor, prefix in _PREFIXES:
        if factor == 1.0:
            continue
        scaled = raw * factor
        if not _plausible(field, scaled):
            continue
        for mantissa in _mantissas(raw):
            if _printed_in(text, mantissa, prefix):
                return scaled, f"{field} read as {mantissa} {prefix} and converted to SI"
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
        "onResistance": "on-state resistance RDS(on), the MAX",
        # RDS(on) is meaningless without the gate drive it was measured at: a
        # logic-level part specified at 4.5 V and a standard one at 10 V are not
        # the same number, and the catalogue has fields for both conditions.
        # Capturing them also shows a reviewer WHICH row of the table was read.
        "onResistanceVgs": "the VGS that RDS(on) max is specified at",
        "onResistanceId": "the ID that RDS(on) max is specified at",
        "capacitanceMeasurementVds": "the VDS the capacitances are specified at",
        "totalGateCharge": "total gate charge QG",
        "outputCapacitance": "output capacitance Coss",
        "gateThresholdVoltage": "gate threshold voltage VGS(th), the MAX",
        "junctionTemperatureMax": "maximum junction temperature Tj",
    },
    "diode": {
        "reverseVoltage": "repetitive peak reverse voltage VRRM",
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


# Manufacturer sites whose hostname is not their name.
_HOST_MANUFACTURER = {
    "ti.com": "Texas Instruments", "st.com": "STMicroelectronics",
    "nxp.com": "NXP Semiconductors", "onsemi.com": "onsemi",
    "diodes.com": "Diodes Incorporated", "vishay.com": "Vishay",
    "rohm.com": "ROHM", "toshiba.semicon-storage.com": "Toshiba",
    "infineon.com": "Infineon Technologies", "microchip.com": "Microchip",
    "analog.com": "Analog Devices", "renesas.com": "Renesas",
    "epc-co.com": "EPC", "wolfspeed.com": "Wolfspeed",
    "we-online.com": "Wurth Elektronik", "murata.com": "Murata",
    "nexperia.com": "Nexperia", "littelfuse.com": "Littelfuse",
    "mccsemi.com": "Micro Commercial Components", "alphaandomega.com": "Alpha & Omega",
    "aosmd.com": "Alpha & Omega Semiconductor", "semtech.com": "Semtech",
}


def _manufacturer_from_host(host: str) -> str:
    """Who publishes documents at this hostname.

    When nothing else names the manufacturer, the site the datasheet was
    fetched FROM is real evidence rather than a guess — a PDF served by
    infineon.com is Infineon's. It is only trusted for a host that is not a
    reseller or an aggregator, because those republish everyone's.
    """
    from heaviside.librarian.fetcher.websearch import _AGGREGATORS, _DISTRIBUTORS

    host = (host or "").lower()
    if not host or any(a in host for a in _AGGREGATORS) or any(d in host for d in _DISTRIBUTORS):
        return ""
    bare = host[4:] if host.startswith("www.") else host
    for domain, name in _HOST_MANUFACTURER.items():
        if bare == domain or bare.endswith("." + domain):
            return name
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


_BUILDERS = {"mosfet": _mosfet, "diode": _diode}
_CATEGORY_TO_DB = {"mosfet": "mosfets", "diode": "diodes"}


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
        urls = [c.url for c in cands if c.is_pdf][:3] or [c.url for c in cands][:2]
    if not urls:
        return None, "no datasheet could be found on the web for this part number", detail

    # 2. read the first one that yields real text
    text = ""
    used = ""
    for url in urls:
        detail["tried"].append(url)
        try:
            doc = fetch(url, timeout=60)
        except Exception as exc:
            logger.debug("datasheet fetch failed for %s: %s", url, exc)
            continue
        try:
            text = _text_of(doc)
        except Exception as exc:
            logger.debug("datasheet parse failed for %s: %s", url, exc)
            continue
        if len(text) >= _MIN_TEXT:
            used = doc.final_url or url
            break
        text = ""
    if not used:
        return None, ("a datasheet was found but no readable text could be taken from it "
                      "(a scanned image, most likely)"), detail
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
