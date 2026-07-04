"""Text-based datasheet parsers for cross-reference-critical fields that
the Electrical-Characteristics *table* extractor cannot cleanly capture.

Why these live outside :mod:`extract`
------------------------------------

Three fields dominate a "is this a safe substitute?" decision but almost
never sit in the parametric Electrical-Characteristics table that
:func:`heaviside.librarian.datasheet.extract.extract_params` walks:

* **Max operating temperature** — the part's temperature ceiling. It lives
  in "Absolute Maximum Ratings" / "Operating Temperature Range" prose as a
  RANGE (``-55 °C to +125 °C``), so a table row parser that grabs the first
  numeric cell would return the *minimum* (``-55``), not the ceiling. This
  is the exact spec behind the motivating bug: an X5R cap (+85 °C) shipped
  as a "recommended" replacement for an X7R cap (+125 °C).
* **Dielectric / temperature characteristic** (X7R / X5R / C0G / NP0 …) —
  an EIA code, never a numeric table row. C0G→X5R looks identical on
  capacitance and voltage yet is a real downgrade in bias/temperature
  stability.
* **AEC-Q qualification** (AEC-Q200 passives, AEC-Q101 discretes,
  AEC-Q100 ICs) — an automotive-grade marker; a non-qualified part is not a
  drop-in for a qualified one.

Every parser here reads ONLY what is literally in the text. A field that
is not present is absent from the result (``None``) — never a "typical"
default, per the no-fallbacks contract.
"""

from __future__ import annotations

import re

__all__ = [
    "DIELECTRIC_CODES",
    "parse_aec_qualification",
    "parse_dielectric_code",
    "parse_operating_temp_max_C",
]


# ---------------------------------------------------------------------------
# Max operating temperature
# ---------------------------------------------------------------------------

# A line must mention one of these to be considered an *operating*
# temperature spec. Storage/soldering/reflow ranges are deliberately
# excluded (a part rated to +150 °C storage but +105 °C operating must
# report 105, not 150).
_OPERATING_TEMP_KEYWORDS: tuple[str, ...] = (
    "operating temperature",
    "operating free-air",
    "operating free air",
    "operating ambient",
    "operating junction",
    "junction temperature",
    "category temperature",
    "ambient temperature range",
    "operating temp",
    "rated temperature",  # electrolytics quote a single "rated temperature 105°C"
)

# Numeric temperature immediately followed by a °C / degC / C unit marker.
# In a range like "-55 °C to +125 °C" only the endpoints carrying the unit
# match; we take the maximum, which is the ceiling we want. A bare "-55"
# with no unit (as in "-55 to +125 °C") does NOT match — but the +125 does,
# and the ceiling is all we need.
_TEMP_WITH_UNIT = re.compile(
    r"([+-]?\d+(?:\.\d+)?)\s*(?:°|deg(?:rees)?\.?\s*)?\s*C\b",
    re.IGNORECASE,
)

# Plausible operating-temperature ceiling window (°C). Drops stray matches
# like "2000C" (hours) or a mangled "1000C".
_TEMP_MIN_PLAUSIBLE = -100.0
_TEMP_MAX_PLAUSIBLE = 400.0


def parse_operating_temp_max_C(text: str) -> float | None:
    """Return the maximum *operating* temperature in °C, or ``None``.

    Scans line by line for an operating-temperature spec and returns the
    highest unit-bearing temperature on any such line. Storage/soldering
    lines are skipped. Returns ``None`` when no operating-temperature spec
    is present (never a default).

    Units: value is in **°C** (matching ``_summarize_candidate``'s
    ``temp_max_C`` convention — NOT kelvin).
    """
    best: float | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if not any(k in low for k in _OPERATING_TEMP_KEYWORDS):
            continue
        # A storage/soldering-only line is not an operating spec. Keep it
        # only if it ALSO names an operating/junction/category range.
        if any(w in low for w in ("storage", "soldering", "reflow", "solder")) and not any(
            w in low for w in ("operating", "junction", "category", "rated")
        ):
            continue
        # A "temperature coefficient … ppm/°C" line is TCR, not a range.
        if "coefficient" in low or "ppm" in low:
            continue
        temps = [float(m.group(1)) for m in _TEMP_WITH_UNIT.finditer(line)]
        temps = [t for t in temps if _TEMP_MIN_PLAUSIBLE <= t <= _TEMP_MAX_PLAUSIBLE]
        if not temps:
            continue
        line_max = max(temps)
        if best is None or line_max > best:
            best = line_max
    return best


# ---------------------------------------------------------------------------
# Dielectric / temperature characteristic (capacitors)
# ---------------------------------------------------------------------------

# EIA class-1 (C0G/NP0/U2J) and class-2/3 temperature-characteristic codes.
# Ordered so more specific / longer codes are tried first and the two
# zero-vs-letter-O spellings of the class-1 codes are both recognised.
DIELECTRIC_CODES: tuple[str, ...] = (
    "C0G",
    "COG",  # letter-O spelling → normalised to C0G
    "NP0",
    "NPO",  # letter-O spelling → normalised to NP0
    "U2J",
    "U2K",
    "X8R",
    "X8L",
    "X7R",
    "X7S",
    "X7T",
    "X7U",
    "X6S",
    "X6T",
    "X5R",
    "Y5V",
    "Z5U",
)

# Codes normalised to their canonical zero-bearing spelling.
_DIELECTRIC_CANON = {"COG": "C0G", "NPO": "NP0"}

# Lines mentioning any of these are the authoritative place a dielectric
# code is declared; a code found on such a line wins over a stray token.
_DIELECTRIC_CONTEXT = (
    "dielectric",
    "temperature characteristic",
    "temperature characteristics",
    "temperature coefficient",  # some vendors: "Temperature Coefficient: X7R"
    "class 1",
    "class 2",
    "class ii",
    "class i",
    "eia",
)


def _find_code(segment: str) -> str | None:
    for code in DIELECTRIC_CODES:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(code)}(?![A-Za-z0-9])", segment, re.IGNORECASE):
            up = code.upper()
            return _DIELECTRIC_CANON.get(up, up)
    return None


def parse_dielectric_code(text: str) -> str | None:
    """Return the EIA dielectric/temperature-characteristic code (e.g.
    ``"X7R"``, ``"C0G"``) or ``None``.

    Prefers a code that appears on a line naming a dielectric context
    ("Temperature Characteristic", "Dielectric", "EIA", "Class 2"); falls
    back to any standalone code token in the text. C0G/NP0 are returned in
    their canonical zero-bearing spelling regardless of source spelling.
    """
    # First pass: context lines (authoritative).
    for raw in text.splitlines():
        low = raw.lower()
        if any(ctx in low for ctx in _DIELECTRIC_CONTEXT):
            code = _find_code(raw)
            if code:
                return code
    # Second pass: anywhere in the document.
    return _find_code(text)


# ---------------------------------------------------------------------------
# AEC-Q automotive qualification
# ---------------------------------------------------------------------------

_AEC_Q = re.compile(r"AEC\s*[-‐‑]?\s*Q\s*[-‐‑]?\s*(\d{3})", re.IGNORECASE)


def parse_aec_qualification(text: str) -> str | None:
    """Return the AEC-Q qualification string (e.g. ``"AEC-Q200"``) or ``None``.

    Recognises AEC-Q100 (ICs), AEC-Q101 (discretes) and AEC-Q200
    (passives) in their various hyphen/space spellings. Returns the
    canonical ``AEC-Q<nnn>`` form. Absent → ``None`` (a part is not assumed
    qualified).
    """
    m = _AEC_Q.search(text)
    if not m:
        return None
    return f"AEC-Q{m.group(1)}"
