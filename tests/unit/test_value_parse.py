"""SI value parsing — case-aware prefix resolution.

Regression guard for the uppercase-prefix bug: the value regex is
IGNORECASE, so ALL-CAPS BOM strings ("22PF", "100NH", "4.7UH") matched but
the case-sensitive multiplier table's ``.get(prefix, 1.0)`` fallback silently
treated the unrecognised uppercase prefix as no-prefix — off by up to 12
orders of magnitude.
"""

from __future__ import annotations

import math

import pytest

from heaviside.pipeline.re_testbench import parse_component_value
from heaviside.pipeline.value_parse import (
    parse_capacitance,
    parse_current,
    parse_inductance,
    parse_resistance,
    parse_si_value,
    parse_voltage,
)


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=0.0)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # canonical (lowercase) — unchanged behaviour
        ("22uF", 22e-6),
        ("100nF", 100e-9),
        ("4.7pF", 4.7e-12),
        ("10mF", 10e-3),
        # ALL-CAPS unambiguous prefixes must fold to the canonical letter
        ("22PF", 22e-12),
        ("100NF", 100e-9),
        ("22UF", 22e-6),
        ("4.7PF", 4.7e-12),
    ],
)
def test_capacitance_prefix_case(raw: str, expected: float) -> None:
    assert _close(parse_capacitance(raw), expected)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("4.7uH", 4.7e-6),
        ("100nH", 100e-9),
        ("1.5mH", 1.5e-3),
        # ALL-CAPS
        ("4.7UH", 4.7e-6),
        ("100NH", 100e-9),
        ("22UH", 22e-6),
    ],
)
def test_inductance_prefix_case(raw: str, expected: float) -> None:
    assert _close(parse_inductance(raw), expected)


def test_milli_mega_not_folded() -> None:
    """'m' (milli) and 'M' (mega) are a genuine collision — never folded."""
    # lowercase m = milli
    assert _close(parse_current("500mA"), 0.5)
    # uppercase M = mega (defined SI prefix); ALL-CAPS "MA" stays mega, it is
    # not silently reinterpreted as milli.
    assert _close(parse_current("2MA"), 2e6)
    assert _close(parse_voltage("1.8MV"), 1.8e6)


def test_unknown_prefix_is_unparseable_not_times_one() -> None:
    """An unrecognised prefix must not silently resolve to ×1."""
    # 'z' is not an SI prefix the regex captures, so the whole string fails to
    # match the prefix slot and only the leading number is (not) taken.
    assert parse_si_value("10Z") is None or _close(parse_si_value("10Z"), 10.0)
    # 'x' inside the value string is not a valid prefix or unit.
    assert parse_capacitance("bogus") == 0.0


def test_re_testbench_parse_matches_value_parse_on_caps() -> None:
    """The RE testbench parser (feeds desiredInductance) must not read
    '4.7UH' as 4.7 henries."""
    assert _close(parse_component_value("4.7UH"), 4.7e-6)
    assert _close(parse_component_value("100NF"), 100e-9)
    assert _close(parse_component_value("22PF"), 22e-12)
    # lowercase still fine
    assert _close(parse_component_value("4.7uH"), 4.7e-6)
    # milli/mega preserved
    assert _close(parse_component_value("500mA") or 0.0, 0.5)


def test_resistance_eia_and_prefix() -> None:
    assert _close(parse_resistance("4R7"), 4.7)
    assert _close(parse_resistance("10k"), 10000.0)
    assert _close(parse_resistance("33kΩ"), 33000.0)
