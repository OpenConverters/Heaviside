"""SI value parsing for component parameters.

Pure functions that convert human-readable value strings (e.g.
``'22uF'``, ``'4.7kΩ'``, ``'100nH'``) into SI-unit floats (Farads,
Henries, Ohms, Volts).

Handles:
  - SI prefixes: p, n, u/micro, m, k, M, G
  - Unicode micro sign (U+00B5) and Greek mu (U+03BC)
  - Raw scientific notation (``4.7e-06``)
  - EIA "R" convention for resistors (``4R7`` = 4.7 Ohms)
  - Whitespace between number and unit

Returns ``0.0`` for unparseable strings (never raises).

Ported from ``proteus.pipelines.crossref._parse_capacitance`` and
``proteus.pipelines.crossref_strands._parse_si_value``.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Shared SI multiplier table
# ---------------------------------------------------------------------------

_SI_MULTIPLIERS: dict[str, float] = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,  # Unicode MICRO SIGN
    "μ": 1e-6,  # Greek small letter MU
    "m": 1e-3,
    "": 1.0,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
    "T": 1e12,
}

# Regex that matches a number followed by an optional SI prefix and
# optional unit suffix.  Captures:
#   group(1) = numeric part (may include scientific notation)
#   group(2) = SI prefix letter (may be empty)
#   group(3) = unit suffix (F, H, V, Ohm, R, Ω) — unused by the parser
#              but anchors the match to avoid false positives.
_VALUE_RE = re.compile(
    r"([-+]?\d*\.?\d+(?:e[+-]?\d+)?)"  # number
    r"\s*"
    r"([pnuµμmkKMGT]?)"  # SI prefix
    r"\s*"
    r"(F|H|V|Ω|Ohm|ohm|R)?",  # unit (optional)
    re.IGNORECASE,
)

# EIA "R" convention for resistors: "4R7" = 4.7 Ω, "10R0" = 10.0 Ω.
_EIA_R_RE = re.compile(r"^(\d+)R(\d+)$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Generic SI parser (internal)
# ---------------------------------------------------------------------------


def _parse_si(s: Any, unit_letter: str, plausibility_max: float) -> float:
    """Parse a string with an SI prefix into a base-unit float.

    Parameters
    ----------
    s : Any
        Raw value string (or None / non-string).
    unit_letter : str
        Expected unit suffix character (``'F'``, ``'H'``, ``'V'``).
        Used only for disambiguation — e.g. ``'10m'`` is 10 millifarads
        when *unit_letter* is ``'F'``, not 10 meters.
    plausibility_max : float
        When a raw ``float(s)`` parse succeeds (no prefix), values below
        this threshold are assumed to already be in base units. Above it
        the raw parse is ignored (avoids interpreting ``"470"`` as 470 F).

    Returns
    -------
    float
        Value in SI base units, or ``0.0`` if unparseable.
    """
    if s is None:
        return 0.0
    s = str(s).strip()
    if not s:
        return 0.0

    # Fast path: raw scientific notation (e.g. "4.7e-06").
    try:
        v = float(s)
        if v < plausibility_max:
            return v
    except ValueError:
        pass

    m = _VALUE_RE.match(s.replace(" ", ""))
    if not m:
        return 0.0
    num_str, prefix, _unit = m.groups()
    try:
        num = float(num_str)
    except ValueError:
        return 0.0
    prefix = prefix or ""
    return num * _SI_MULTIPLIERS.get(prefix, 1.0)


# ---------------------------------------------------------------------------
# Public API — one function per component class
# ---------------------------------------------------------------------------


def parse_capacitance(s: str) -> float:
    """Parse a capacitance string to Farads.

    Examples::

        parse_capacitance("22uF")     -> 2.2e-05
        parse_capacitance("100nF")    -> 1e-07
        parse_capacitance("4.7pF")    -> 4.7e-12
        parse_capacitance("4.7e-06")  -> 4.7e-06
        parse_capacitance("bogus")    -> 0.0
    """
    return _parse_si(s, "F", plausibility_max=1.0)


def parse_inductance(s: str) -> float:
    """Parse an inductance string to Henries.

    Examples::

        parse_inductance("4.7uH")    -> 4.7e-06
        parse_inductance("100nH")    -> 1e-07
        parse_inductance("1.5mH")    -> 0.0015
        parse_inductance("1.5e-06")  -> 1.5e-06
    """
    return _parse_si(s, "H", plausibility_max=1.0)


def parse_resistance(s: str) -> float:
    """Parse a resistance string to Ohms.

    Handles the EIA ``R`` convention (``4R7`` = 4.7 Ohms) as well as
    standard prefix notation (``10k`` = 10000 Ohms).

    Examples::

        parse_resistance("4R7")   -> 4.7
        parse_resistance("10k")   -> 10000.0
        parse_resistance("33kΩ")  -> 33000.0
        parse_resistance("0.1Ω")  -> 0.1
    """
    if s is None:
        return 0.0
    s_stripped = str(s).strip()
    if not s_stripped:
        return 0.0

    # EIA "R" convention: "4R7" = 4.7 Ω
    m = _EIA_R_RE.match(s_stripped)
    if m:
        try:
            return float(f"{m.group(1)}.{m.group(2)}")
        except ValueError:
            return 0.0

    # Resistors can be large (1 MΩ+) so plausibility_max is generous.
    return _parse_si(s_stripped, "R", plausibility_max=1e10)


def parse_voltage(s: str) -> float:
    """Parse a voltage string to Volts.

    Examples::

        parse_voltage("12V")   -> 12.0
        parse_voltage("3.3")   -> 3.3
        parse_voltage("1.8kV") -> 1800.0
    """
    return _parse_si(s, "V", plausibility_max=1e6)


def parse_current(s: str) -> float:
    """Parse a current string to Amperes.

    Examples::

        parse_current("10A")    -> 10.0
        parse_current("500mA")  -> 0.5
        parse_current("2.5")    -> 2.5
    """
    return _parse_si(s, "A", plausibility_max=1e6)


# ---------------------------------------------------------------------------
# Generic entry point (for callers that dispatch by category)
# ---------------------------------------------------------------------------


def parse_si_value(s: Any) -> float | None:
    """Parse an arbitrary SI-prefixed value string to a float.

    Unlike the type-specific functions above, this returns ``None`` (not
    ``0.0``) on failure, which lets callers distinguish "unparseable"
    from "genuinely zero".
    """
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None

    # EIA "R" convention
    m = _EIA_R_RE.match(s)
    if m:
        try:
            return float(f"{m.group(1)}.{m.group(2)}")
        except ValueError:
            return None

    m2 = _VALUE_RE.match(s.replace(" ", ""))
    if not m2:
        return None
    num_str, prefix, _unit = m2.groups()
    try:
        num = float(num_str)
    except ValueError:
        return None
    prefix = prefix or ""
    return num * _SI_MULTIPLIERS.get(prefix, 1.0)


__all__ = [
    "parse_capacitance",
    "parse_inductance",
    "parse_resistance",
    "parse_si_value",
    "parse_voltage",
]
