"""Deterministic regression for capacitor dielectric -> chemistry family.

Pins the um3491 fix: X7T (and the other valid Class II/III EIA codes) must
collapse to the ``ceramic`` family, so an X7T MLCC is cross-referenced against
Würth ceramics instead of being penalised out of family. Also guards that the
distinct chemistries stay separate (the supercap-vs-ceramic invariant)."""
from __future__ import annotations

import pytest

from heaviside.pipeline.crossref_pipeline import _capacitor_technology_family as fam


@pytest.mark.parametrize(
    "code",
    ["C0G", "NP0", "U2J",
     "X5R", "X5S", "X5T", "X6R", "X6S", "X6T", "X7R", "X7S", "X7T",
     "X8R", "X8S", "X8L", "Y5V", "Z5U",
     "ceramic", "MLCC", "Ceramic, X7R"],
)
def test_ceramic_dielectrics_map_to_ceramic(code: str) -> None:
    assert fam(code) == "ceramic", f"{code!r} should be ceramic, got {fam(code)!r}"


def test_x7t_specifically_is_ceramic() -> None:
    # The regression: X7T was absent from the EIA code list -> mapped to 'x7t'
    # -> cross-chemistry penalty vs Würth X7R candidates (um3491 C1/C2/C24).
    assert fam("X7T") == "ceramic"
    assert fam("22uF X7T 10V") == "ceramic"


@pytest.mark.parametrize(
    ("tech", "expected"),
    [
        ("tantalum", "tantalum"),
        ("Polymer Tantalum", "tantalum"),
        ("aluminum electrolytic", "aluminum"),
        ("aluminum polymer", "aluminum"),
        ("supercapacitor", "supercapacitor"),
        ("EDLC", "supercapacitor"),
        ("film polypropylene", "film"),
        ("niobium", "niobium"),
        ("mica", "mica"),
    ],
)
def test_non_ceramic_families_stay_distinct(tech: str, expected: str) -> None:
    assert fam(tech) == expected


def test_none_and_blank() -> None:
    assert fam(None) is None
    assert fam("   ") is None
