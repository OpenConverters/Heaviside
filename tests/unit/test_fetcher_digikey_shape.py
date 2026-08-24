"""Conversion against Digi-Key's REAL payload shape, and the chip-bead path.

Two defects the existing converter tests could not catch, both found while
diagnosing a 0/7-coverage cross-reference (job 1b73eec81dbd):

* Digi-Key publishes ``Category`` / ``Family`` / ``Series`` as TOP-LEVEL objects,
  not as entries in ``Parameters``. Every fixture in ``test_fetcher_convert``
  puts them in ``Parameters``, so the converters passed in tests while failing on
  every real payload: sourcing ANY capacitor from Digi-Key raised "distributor
  payload lacks Family/Series parameter" (ABT #876). These fixtures carry the
  shape the API actually returns.

* A ferrite bead has no inductance, so routing it through the inductor converter
  always failed on a required ``electrical.inductance``. Its defining spec is
  impedance at a stated frequency (ABT #874/#876).
"""

from __future__ import annotations

from typing import Any

import pytest

from heaviside.librarian import validate_component
from heaviside.librarian.fetcher.base import IncompleteSourceError
from heaviside.librarian.fetcher.convert import (
    convert_digikey_to_tas_capacitor,
    convert_digikey_to_tas_chip_bead,
)
from heaviside.librarian.fetcher.original import classify_dk_product

# ---------------------------------------------------------------------------
# Real Digi-Key payload shapes (fields verified against api.digikey.com)
# ---------------------------------------------------------------------------


def _taiyo_yuden_mlcc(**overrides: Any) -> dict[str, Any]:
    """EMK105BJ105KV-F — 1 µF 16 V X5R 0402, as Digi-Key returns it."""
    base: dict[str, Any] = {
        "ManufacturerPartNumber": "EMK105BJ105KV-F",
        "Manufacturer": {"Value": "Taiyo Yuden"},
        "DigiKeyPartNumber": "587-1291-1-ND",
        "ProductStatus": "Active",
        "UnitPrice": 0.05,
        "QuantityAvailable": 50000,
        "PrimaryDatasheet": "https://www.yuden.co.jp/...",
        "ProductUrl": "https://www.digikey.com/...",
        # NOT in Parameters — this is the shape that broke every conversion.
        "Category": {"Value": "Capacitors"},
        "Family": {"Value": "Ceramic Capacitors"},
        "Series": {"Value": "M"},
        "Description": {"ProductDescription": "CAP CER 1UF 16V X5R 0402"},
        "Parameters": [
            {"Parameter": "Capacitance", "Value": "1 µF"},
            {"Parameter": "Voltage - Rated", "Value": "16V"},
            {"Parameter": "Temperature Coefficient", "Value": "X5R"},
            {"Parameter": "Package / Case", "Value": "0402 (1005 Metric)"},
            {"Parameter": "Mounting Type", "Value": "Surface Mount, MLCC"},
        ],
    }
    base.update(overrides)
    return base


def _murata_ferrite_bead(**overrides: Any) -> dict[str, Any]:
    """BLM21AG601SN1D — 600 Ω @ 100 MHz, 0805, as Digi-Key returns it."""
    base: dict[str, Any] = {
        "ManufacturerPartNumber": "BLM21AG601SN1D",
        "Manufacturer": {"Value": "Murata Electronics"},
        "DigiKeyPartNumber": "490-1050-1-ND",
        "ProductStatus": "Active",
        "UnitPrice": 0.10,
        "QuantityAvailable": 20000,
        "PrimaryDatasheet": "https://pim.murata.com/...",
        "ProductUrl": "https://www.digikey.com/...",
        "Category": {"Value": "Filters"},
        "Family": {"Value": "Ferrite Beads and Chips"},
        "Series": {"Value": "EMIFIL®, BLM21"},
        "Description": {"ProductDescription": "FERRITE BEAD 600 OHM 0805 1LN"},
        "Parameters": [
            {"Parameter": "Impedance @ Frequency", "Value": "600 Ohms @ 100 MHz"},
            {"Parameter": "DC Resistance (DCR) (Max)", "Value": "210mOhm"},
            {"Parameter": "Current Rating (Max)", "Value": "600mA"},
            {"Parameter": "Package / Case", "Value": "0805 (2012 Metric)"},
            {"Parameter": "Number of Lines", "Value": "1"},
            {"Parameter": "Mounting Type", "Value": "Surface Mount"},
        ],
    }
    base.update(overrides)
    return base


# ── top-level Family/Series (ABT #876) ───────────────────────────────────────


def test_capacitor_converts_from_the_real_digikey_shape() -> None:
    """Family/Series live at the top level; the chemistry must still resolve."""
    envelope = convert_digikey_to_tas_capacitor(_taiyo_yuden_mlcc())
    part = envelope["capacitor"]["manufacturerInfo"]["datasheetInfo"]["part"]
    assert part["technology"] == "ceramic-class-2"
    assert part["dielectricCode"] == "X5R"
    assert part["series"] == "M"
    electrical = envelope["capacitor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]
    assert electrical["capacitance"] == {"nominal": pytest.approx(1e-6)}
    assert electrical["ratedVoltage"] == pytest.approx(16.0)
    validate_component("capacitors", envelope)


def test_a_parameters_entry_still_wins_over_the_top_level_field() -> None:
    """Surfacing the top-level fields must not shadow a real Parameters entry."""
    product = _taiyo_yuden_mlcc()
    product["Parameters"].append({"Parameter": "Series", "Value": "FROM-PARAMETERS"})
    envelope = convert_digikey_to_tas_capacitor(product)
    part = envelope["capacitor"]["manufacturerInfo"]["datasheetInfo"]["part"]
    assert part["series"] == "FROM-PARAMETERS"


def test_a_payload_with_no_family_anywhere_still_raises() -> None:
    """No silent fallback: an unresolvable chemistry is still an error."""
    product = _taiyo_yuden_mlcc()
    del product["Family"]
    with pytest.raises(IncompleteSourceError, match="technology"):
        convert_digikey_to_tas_capacitor(product)


# ── the chip-bead converter (ABT #874/#876) ──────────────────────────────────


def test_a_ferrite_bead_classifies_as_a_chip_bead_not_a_magnetic() -> None:
    """Filed as "magnetic" it was cross-referenced against power inductors."""
    assert classify_dk_product(_murata_ferrite_bead()) == "chipBead"


def test_chip_bead_converts_with_its_impedance_curve_point() -> None:
    envelope = convert_digikey_to_tas_chip_bead(_murata_ferrite_bead())
    electrical = envelope["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"]
    assert len(electrical) == 1
    item = electrical[0]
    assert item["subtype"] == "chipBead"
    assert item["impedancePoints"] == [
        {"frequency": pytest.approx(100e6), "impedance": {"magnitude": pytest.approx(600.0)}}
    ]
    assert item["dcResistance"] == {"maximum": pytest.approx(0.21)}
    assert item["ratedCurrents"] == [pytest.approx(0.6)]
    assert "inductance" not in item, "a bead has no inductance"
    validate_component("magnetics", envelope)


def test_an_impedance_without_its_frequency_is_refused() -> None:
    """600 Ω at 10 MHz and at 1 GHz are different parts — defaulting the
    frequency to 100 MHz would silently mis-rank every bead."""
    product = _murata_ferrite_bead()
    product["Parameters"][0] = {"Parameter": "Impedance @ Frequency", "Value": "600 Ohms"}
    with pytest.raises(IncompleteSourceError, match="frequency"):
        convert_digikey_to_tas_chip_bead(product)


def test_a_bead_with_no_impedance_is_refused() -> None:
    product = _murata_ferrite_bead()
    product["Parameters"] = [p for p in product["Parameters"] if "Impedance" not in p["Parameter"]]
    with pytest.raises(IncompleteSourceError, match="impedancePoints"):
        convert_digikey_to_tas_chip_bead(product)


def test_a_bead_array_is_refused_rather_than_modelled_as_one_bead() -> None:
    """A 4-line array in one package is a different part; one impedance/DCR pair
    cannot describe it, and pretending otherwise would let it substitute for a
    single bead."""
    product = _murata_ferrite_bead()
    product["Parameters"] = [
        {"Parameter": "Number of Lines", "Value": "4"} if p["Parameter"] == "Number of Lines" else p
        for p in product["Parameters"]
    ]
    with pytest.raises(IncompleteSourceError, match="array"):
        convert_digikey_to_tas_chip_bead(product)
