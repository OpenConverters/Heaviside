"""Tests for ``heaviside.librarian.fetcher.convert``.

Covers:

* :func:`parse_si_value` — happy path, SI prefixes, strict failure
  modes, blank handling.
* :func:`convert_digikey_to_tas_mosfet` — round-trip against a
  representative Wolfspeed SiC payload, technology resolution,
  required-field gap detection (the six SAS electrical fields plus
  manufacturer/MPN), and schema validation via
  :func:`heaviside.librarian.validate_component`.
* :func:`convert_mouser_to_tas_mosfet` — happy path against Mouser's
  ``ProductAttributes`` shape and strict failure on thin Mouser
  records.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from heaviside.librarian import validate_component
from heaviside.librarian.fetcher.base import IncompleteSourceError
from heaviside.librarian.fetcher.convert import (
    convert_digikey_to_tas_mosfet,
    convert_mouser_to_tas_mosfet,
    parse_si_value,
)


# ---------------------------------------------------------------------------
# parse_si_value
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("100 V", 100.0),
        ("20 mΩ", 0.020),
        ("230 pF", 230e-12),
        ("51 nC", 51e-9),
        ("1.5 µA", 1.5e-6),
        ("1.5 μA", 1.5e-6),  # GREEK SMALL LETTER MU
        ("4 kΩ", 4000.0),
        ("3.3", 3.3),
        ("1,000 V", 1000.0),  # comma thousands separator
        ("-0.5 V", -0.5),
        ("1e3", 1000.0),
    ],
)
def test_parse_si_value_happy(raw: str, expected: float) -> None:
    assert parse_si_value(raw) == pytest.approx(expected, rel=1e-9)


@pytest.mark.parametrize("raw", ["", "  ", None, "-", "—"])
def test_parse_si_value_blank_raises_by_default(raw: Any) -> None:
    with pytest.raises(ValueError, match="empty or sentinel"):
        parse_si_value(raw)


@pytest.mark.parametrize("raw", ["", "-", None])
def test_parse_si_value_blank_allowed(raw: Any) -> None:
    assert math.isnan(parse_si_value(raw, allow_blank=True))


@pytest.mark.parametrize("raw", ["abc", "V", "...", "no-number"])
def test_parse_si_value_unparseable_raises(raw: str) -> None:
    with pytest.raises(ValueError, match="cannot parse"):
        parse_si_value(raw)


# ---------------------------------------------------------------------------
# Digi-Key MOSFET converter
# ---------------------------------------------------------------------------


def _wolfspeed_digikey_payload(**overrides: Any) -> dict[str, Any]:
    """Synthetic Wolfspeed C3M0020075K payload mirroring Digi-Key v3 shape."""
    base: dict[str, Any] = {
        "ManufacturerPartNumber": "C3M0020075K",
        "Manufacturer": {"Value": "Wolfspeed"},
        "DigiKeyPartNumber": "C3M0020075K-ND",
        "ProductStatus": "Active",
        "UnitPrice": 22.50,
        "QuantityAvailable": 1450,
        "PrimaryDatasheet": "https://www.wolfspeed.com/.../C3M0020075K.pdf",
        "ProductUrl": "https://www.digikey.com/en/products/detail/wolfspeed/C3M0020075K/...",
        "Description": {
            "ProductDescription": "MOSFET N-CH 750V 90A TO247-4 SiC",
        },
        "Parameters": [
            {"Parameter": "Drain to Source Voltage (Vdss)", "Value": "750 V"},
            {"Parameter": "Rds On (Max) @ Id, Vgs", "Value": "20 mΩ"},
            {
                "Parameter": "Current - Continuous Drain (Id) @ 25°C",
                "Value": "90 A",
            },
            {"Parameter": "Vgs(th) (Max) @ Id", "Value": "2.5 V"},
            {
                "Parameter": "Output Capacitance (Coss) @ Vds, Vgs",
                "Value": "230 pF",
            },
            {"Parameter": "Gate Charge (Qg) @ Vgs", "Value": "51 nC"},
            {"Parameter": "Supplier Device Package", "Value": "TO-247-4"},
        ],
    }
    base.update(overrides)
    return base


def test_digikey_mosfet_happy_path_validates_against_schema() -> None:
    payload = _wolfspeed_digikey_payload()
    envelope = convert_digikey_to_tas_mosfet(payload)

    # Top-level shape: {"mosfet": {...}}.
    assert set(envelope.keys()) == {"mosfet"}
    mosfet = envelope["mosfet"]
    assert mosfet["manufacturerInfo"]["name"] == "Wolfspeed"
    assert mosfet["manufacturerInfo"]["reference"] == "C3M0020075K"
    assert mosfet["manufacturerInfo"]["status"] == "production"

    part = mosfet["manufacturerInfo"]["datasheetInfo"]["part"]
    assert part["partNumber"] == "C3M0020075K"
    assert part["technology"] == "SiC"
    assert part["subType"] == "nChannel"
    assert part["case"] == "TO-247-4"
    # Proteus put deviceType in part — SAS forbids it.
    assert "deviceType" not in part

    electrical = mosfet["manufacturerInfo"]["datasheetInfo"]["electrical"]
    assert electrical["drainSourceVoltage"] == pytest.approx(750.0)
    assert electrical["onResistance"] == pytest.approx(0.020)
    assert electrical["continuousDrainCurrent"] == pytest.approx(90.0)
    assert electrical["outputCapacitance"] == pytest.approx(230e-12)
    assert electrical["totalGateCharge"] == pytest.approx(51e-9)
    assert electrical["gateThresholdVoltage"] == {"maximum": pytest.approx(2.5)}

    # And it must satisfy the live SAS mosfet schema.
    validate_component("mosfets", envelope)


def test_digikey_distributor_block_populated() -> None:
    envelope = convert_digikey_to_tas_mosfet(_wolfspeed_digikey_payload())
    dist = envelope["mosfet"]["distributorsInfo"]
    assert len(dist) == 1
    assert dist[0]["name"] == "Digi-Key"
    assert dist[0]["reference"] == "C3M0020075K-ND"
    assert dist[0]["cost"] == pytest.approx(22.50)
    assert dist[0]["quantity"] == 1450


@pytest.mark.parametrize(
    "mpn_hint,description,expected",
    [
        ("C3M0020075K", "MOSFET N-CH 750V 90A SiC", "SiC"),
        ("EPC2052", "GaN HEMT 100V 22A", "GaN"),
        ("IPB017N10N5", "MOSFET N-CH 100V 180A", "Si"),
        ("RANDOM", "", "Si"),  # default
    ],
)
def test_digikey_technology_resolution(
    mpn_hint: str, description: str, expected: str,
) -> None:
    payload = _wolfspeed_digikey_payload(
        ManufacturerPartNumber=mpn_hint,
        Description={"ProductDescription": description},
    )
    envelope = convert_digikey_to_tas_mosfet(payload)
    assert envelope["mosfet"]["manufacturerInfo"]["datasheetInfo"]["part"][
        "technology"
    ] == expected


def _drop_param(payload: dict[str, Any], name: str) -> dict[str, Any]:
    payload["Parameters"] = [p for p in payload["Parameters"] if p["Parameter"] != name]
    return payload


@pytest.mark.parametrize(
    "param_to_drop,expected_field",
    [
        ("Drain to Source Voltage (Vdss)", "electrical.drainSourceVoltage"),
        ("Rds On (Max) @ Id, Vgs", "electrical.onResistance"),
        (
            "Current - Continuous Drain (Id) @ 25°C",
            "electrical.continuousDrainCurrent",
        ),
        ("Vgs(th) (Max) @ Id", "electrical.gateThresholdVoltage"),
        (
            "Output Capacitance (Coss) @ Vds, Vgs",
            "electrical.outputCapacitance",
        ),
        ("Gate Charge (Qg) @ Vgs", "electrical.totalGateCharge"),
    ],
)
def test_digikey_missing_required_param_raises(
    param_to_drop: str, expected_field: str,
) -> None:
    payload = _drop_param(_wolfspeed_digikey_payload(), param_to_drop)
    with pytest.raises(IncompleteSourceError) as excinfo:
        convert_digikey_to_tas_mosfet(payload)
    err = excinfo.value
    assert err.missing_field == expected_field
    assert err.source == "digikey"
    assert err.mpn == "C3M0020075K"


def test_digikey_unparseable_numeric_raises() -> None:
    payload = _wolfspeed_digikey_payload()
    # Inject a garbage Rds(on) value.
    for entry in payload["Parameters"]:
        if entry["Parameter"] == "Rds On (Max) @ Id, Vgs":
            entry["Value"] = "TBD"
    with pytest.raises(IncompleteSourceError) as excinfo:
        convert_digikey_to_tas_mosfet(payload)
    assert excinfo.value.missing_field == "electrical.onResistance"
    assert "unparseable" in str(excinfo.value)


def test_digikey_missing_manufacturer_raises() -> None:
    payload = _wolfspeed_digikey_payload(Manufacturer={})
    with pytest.raises(IncompleteSourceError) as excinfo:
        convert_digikey_to_tas_mosfet(payload)
    assert excinfo.value.missing_field == "manufacturerInfo.name"


def test_digikey_missing_mpn_raises() -> None:
    payload = _wolfspeed_digikey_payload()
    payload["ManufacturerPartNumber"] = ""
    with pytest.raises(IncompleteSourceError) as excinfo:
        convert_digikey_to_tas_mosfet(payload)
    assert excinfo.value.missing_field == "ManufacturerPartNumber"


def test_digikey_non_active_product_marks_discontinued() -> None:
    payload = _wolfspeed_digikey_payload(ProductStatus="Obsolete")
    envelope = convert_digikey_to_tas_mosfet(payload)
    assert envelope["mosfet"]["manufacturerInfo"]["status"] == "discontinued"


def test_digikey_alternate_param_label_accepted() -> None:
    """Digi-Key occasionally drops ``@ Id, Vgs`` from the Rds(on) label."""
    payload = _wolfspeed_digikey_payload()
    for entry in payload["Parameters"]:
        if entry["Parameter"] == "Rds On (Max) @ Id, Vgs":
            entry["Parameter"] = "Rds On (Max)"  # newer label variant
    envelope = convert_digikey_to_tas_mosfet(payload)
    assert envelope["mosfet"]["manufacturerInfo"]["datasheetInfo"]["electrical"][
        "onResistance"
    ] == pytest.approx(0.020)


# ---------------------------------------------------------------------------
# Mouser MOSFET converter
# ---------------------------------------------------------------------------


def _mouser_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ManufacturerPartNumber": "C3M0020075K",
        "Manufacturer": "Wolfspeed",
        "MouserPartNumber": "581-C3M0020075K",
        "Description": "750V 90A SiC MOSFET N-CH TO-247-4",
        "DataSheetUrl": "https://www.wolfspeed.com/.../C3M0020075K.pdf",
        "ProductDetailUrl": "https://www.mouser.com/...",
        "AvailabilityInStock": "150",
        "PriceBreaks": [{"Quantity": 1, "Price": "$22.50", "Currency": "USD"}],
        "ProductAttributes": [
            {"AttributeName": "Drain to Source Voltage (Vdss)", "AttributeValue": "750 V"},
            {"AttributeName": "Rds On (Max) @ Id, Vgs", "AttributeValue": "20 mΩ"},
            {
                "AttributeName": "Current - Continuous Drain (Id) @ 25°C",
                "AttributeValue": "90 A",
            },
            {"AttributeName": "Vgs(th) (Max) @ Id", "AttributeValue": "2.5 V"},
            {
                "AttributeName": "Output Capacitance (Coss) @ Vds, Vgs",
                "AttributeValue": "230 pF",
            },
            {"AttributeName": "Gate Charge (Qg) @ Vgs", "AttributeValue": "51 nC"},
        ],
    }
    base.update(overrides)
    return base


def test_mouser_mosfet_happy_path_validates() -> None:
    envelope = convert_mouser_to_tas_mosfet(_mouser_payload())
    validate_component("mosfets", envelope)
    mosfet = envelope["mosfet"]
    assert mosfet["distributorsInfo"][0]["name"] == "Mouser"
    assert mosfet["distributorsInfo"][0]["cost"] == pytest.approx(22.50)
    assert mosfet["distributorsInfo"][0]["quantity"] == 150


def test_mouser_thin_payload_raises_incomplete() -> None:
    """A real-world Mouser row often lacks at least one electrical field."""
    payload = _mouser_payload()
    payload["ProductAttributes"] = [
        p for p in payload["ProductAttributes"]
        if p["AttributeName"] != "Output Capacitance (Coss) @ Vds, Vgs"
    ]
    with pytest.raises(IncompleteSourceError) as excinfo:
        convert_mouser_to_tas_mosfet(payload)
    assert excinfo.value.source == "mouser"
    assert excinfo.value.missing_field == "electrical.outputCapacitance"


def test_mouser_unparseable_price_raises() -> None:
    payload = _mouser_payload(
        PriceBreaks=[{"Quantity": 1, "Price": "TBD", "Currency": "USD"}],
    )
    with pytest.raises(IncompleteSourceError) as excinfo:
        convert_mouser_to_tas_mosfet(payload)
    assert excinfo.value.missing_field == "distributorsInfo.cost"


def test_mouser_missing_manufacturer_raises() -> None:
    payload = _mouser_payload(Manufacturer="")
    with pytest.raises(IncompleteSourceError) as excinfo:
        convert_mouser_to_tas_mosfet(payload)
    assert excinfo.value.missing_field == "manufacturerInfo.name"
