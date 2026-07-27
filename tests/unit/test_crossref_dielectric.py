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


def test_codes_are_sourced_from_cas() -> None:
    # The taxonomy is owned by CAS (CAS/data/eia_dielectric_codes.json), not
    # hardcoded in crossref — the loader must read it and include X7T.
    from heaviside.pipeline.crossref_pipeline import _eia_dielectric_codes

    codes = _eia_dielectric_codes()
    assert "X7T" in codes and "X7R" in codes and "C0G" in codes
    assert len(codes) >= 20


def test_literal_dielectric_decoded_from_mpn_fills_the_param(monkeypatch=None) -> None:
    """ABT #148 item 2: _decode_cap_mpn already reads a literal X7R out of the
    MPN, but only its voltage was consumed — so a catalogue record missing
    dielectricCode still rendered UNVERIFIED and an X7R->X5R change hid."""
    from heaviside.pipeline.crossref_pipeline import _fill_decoded_dielectric

    params = {"capacitance": 8.2e-9, "voltage": 50.0, "dielectric_code": None}
    _fill_decoded_dielectric(params, "C0402X7R500822KNP")
    assert params["dielectric_code"] == "X7R"


def test_decoded_dielectric_never_overwrites_the_catalogue_value() -> None:
    from heaviside.pipeline.crossref_pipeline import _fill_decoded_dielectric

    params = {"dielectric_code": "X5R"}
    _fill_decoded_dielectric(params, "C0402X7R500822KNP")
    assert params["dielectric_code"] == "X5R"


def test_missing_record_is_never_synthesised_from_an_mpn_decode() -> None:
    """A None params dict must stay None: fabricating one would make the
    no-original-data gates believe an unidentified original was resolved."""
    from heaviside.pipeline.crossref_pipeline import _fill_decoded_dielectric

    assert _fill_decoded_dielectric(None, "C0402X7R500822KNP") is None


def test_internal_severity_tags_never_reach_customer_prose() -> None:
    """ABT #136 item 2 residual: the severity belongs to the deterministic
    verdict/badge, not to the customer-facing note."""
    from heaviside.pipeline.crossref_pipeline import _sanitize_internal_names

    out = _sanitize_internal_names("CRITICAL: voltage-rating downgrade on C19")
    assert out == "voltage-rating downgrade on C19"
    assert "critical" not in out.lower()
