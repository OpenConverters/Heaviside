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
    convert_digikey_to_tas_capacitor,
    convert_digikey_to_tas_diode,
    convert_digikey_to_tas_igbt,
    convert_digikey_to_tas_mosfet,
    convert_digikey_to_tas_resistor,
    convert_mouser_to_tas_capacitor,
    convert_mouser_to_tas_diode,
    convert_mouser_to_tas_igbt,
    convert_mouser_to_tas_mosfet,
    convert_mouser_to_tas_resistor,
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

    # Top-level shape: {"semiconductor": {"mosfet": {...}}}.
    assert set(envelope.keys()) == {"semiconductor"}
    mosfet = envelope["semiconductor"]["mosfet"]
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
    dist = envelope["semiconductor"]["mosfet"]["distributorsInfo"]
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
    mpn_hint: str,
    description: str,
    expected: str,
) -> None:
    payload = _wolfspeed_digikey_payload(
        ManufacturerPartNumber=mpn_hint,
        Description={"ProductDescription": description},
    )
    envelope = convert_digikey_to_tas_mosfet(payload)
    assert (
        envelope["semiconductor"]["mosfet"]["manufacturerInfo"]["datasheetInfo"]["part"][
            "technology"
        ]
        == expected
    )


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
    param_to_drop: str,
    expected_field: str,
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
    assert envelope["semiconductor"]["mosfet"]["manufacturerInfo"]["status"] == "discontinued"


def test_digikey_alternate_param_label_accepted() -> None:
    """Digi-Key occasionally drops ``@ Id, Vgs`` from the Rds(on) label."""
    payload = _wolfspeed_digikey_payload()
    for entry in payload["Parameters"]:
        if entry["Parameter"] == "Rds On (Max) @ Id, Vgs":
            entry["Parameter"] = "Rds On (Max)"  # newer label variant
    envelope = convert_digikey_to_tas_mosfet(payload)
    assert envelope["semiconductor"]["mosfet"]["manufacturerInfo"]["datasheetInfo"]["electrical"][
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
    mosfet = envelope["semiconductor"]["mosfet"]
    assert mosfet["distributorsInfo"][0]["name"] == "Mouser"
    assert mosfet["distributorsInfo"][0]["cost"] == pytest.approx(22.50)
    assert mosfet["distributorsInfo"][0]["quantity"] == 150


def test_mouser_thin_payload_raises_incomplete() -> None:
    """A real-world Mouser row often lacks at least one electrical field."""
    payload = _mouser_payload()
    payload["ProductAttributes"] = [
        p
        for p in payload["ProductAttributes"]
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


# ===========================================================================
# Diode converters
# ===========================================================================


def _wolfspeed_diode_digikey(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ManufacturerPartNumber": "C3D04060A",
        "Manufacturer": {"Value": "Wolfspeed"},
        "DigiKeyPartNumber": "C3D04060A-ND",
        "ProductStatus": "Active",
        "UnitPrice": 3.50,
        "QuantityAvailable": 500,
        "PrimaryDatasheet": "https://wolfspeed.com/.../C3D04060A.pdf",
        "ProductUrl": "https://www.digikey.com/...",
        "Description": {
            "ProductDescription": "DIODE SCHOTTKY 600V 4A TO220AC SiC",
        },
        "Parameters": [
            {"Parameter": "Voltage - DC Reverse (Vr) (Max)", "Value": "600 V"},
            {"Parameter": "Voltage - Forward (Vf) (Max) @ If", "Value": "1.5 V"},
            {"Parameter": "Current - Average Rectified (Io)", "Value": "4 A"},
            {"Parameter": "Reverse Recovery Charge (Qrr) (Typ)", "Value": "13 nC"},
            {"Parameter": "Supplier Device Package", "Value": "TO-220AC"},
        ],
    }
    base.update(overrides)
    return base


def test_digikey_diode_happy_path_validates() -> None:
    envelope = convert_digikey_to_tas_diode(_wolfspeed_diode_digikey())
    assert set(envelope.keys()) == {"semiconductor"}
    assert set(envelope["semiconductor"].keys()) == {"diode"}
    diode = envelope["semiconductor"]["diode"]
    part = diode["manufacturerInfo"]["datasheetInfo"]["part"]
    assert part["technology"] == "SiC"
    assert part["subType"] == "sicSchottky"
    assert part["case"] == "TO-220AC"
    assert "deviceType" not in part
    electrical = diode["manufacturerInfo"]["datasheetInfo"]["electrical"]
    assert electrical["reverseVoltage"] == pytest.approx(600.0)
    assert electrical["forwardVoltage"] == pytest.approx(1.5)
    assert electrical["forwardCurrent"] == pytest.approx(4.0)
    assert electrical["reverseRecoveryCharge"] == pytest.approx(13e-9)
    validate_component("diodes", envelope)


@pytest.mark.parametrize(
    "description,expected_subtype",
    [
        ("DIODE SCHOTTKY 100V 5A", "schottky"),
        ("DIODE SCHOTTKY SiC 1200V 10A", "sicSchottky"),
        ("DIODE ULTRAFAST 600V 8A", "ultrafast"),
        ("DIODE FAST RECOVERY 200V 3A", "fastRecovery"),
        ("DIODE TVS 33V UNIDIR", "tvs"),
        ("DIODE ZENER 12V 500MW", "zener"),
        ("DIODE GP RECTIFIER 1000V 1A", "standard"),
    ],
)
def test_digikey_diode_subtype_resolution(
    description: str,
    expected_subtype: str,
) -> None:
    payload = _wolfspeed_diode_digikey(
        Description={"ProductDescription": description},
    )
    env = convert_digikey_to_tas_diode(payload)
    assert (
        env["semiconductor"]["diode"]["manufacturerInfo"]["datasheetInfo"]["part"]["subType"]
        == expected_subtype
    )


@pytest.mark.parametrize(
    "param_to_drop,expected_field",
    [
        # Only the schema-required electrical fields raise on absence.
        # forwardVoltage and reverseRecoveryCharge are OPTIONAL at fetch
        # time (the distributor payload often omits them — Schottky have
        # negligible Qrr; the component-librarian enriches the rest from
        # the datasheet later). Their absence is covered by the positive
        # test below, NOT here.
        ("Voltage - DC Reverse (Vr) (Max)", "electrical.reverseVoltage"),
        ("Current - Average Rectified (Io)", "electrical.forwardCurrent"),
    ],
)
def test_digikey_diode_missing_required_param_raises(
    param_to_drop: str,
    expected_field: str,
) -> None:
    payload = _wolfspeed_diode_digikey()
    payload["Parameters"] = [p for p in payload["Parameters"] if p["Parameter"] != param_to_drop]
    with pytest.raises(IncompleteSourceError) as excinfo:
        convert_digikey_to_tas_diode(payload)
    assert excinfo.value.missing_field == expected_field
    assert excinfo.value.source == "digikey"


@pytest.mark.parametrize(
    "param_to_drop,absent_field",
    [
        ("Voltage - Forward (Vf) (Max) @ If", "forwardVoltage"),
        ("Reverse Recovery Charge (Qrr) (Typ)", "reverseRecoveryCharge"),
    ],
)
def test_digikey_diode_optional_param_omitted_not_defaulted(
    param_to_drop: str,
    absent_field: str,
) -> None:
    """An optional electrical field absent from the payload must be
    OMITTED from the converted envelope — never fabricated with a default
    (no-fallback rule)."""
    payload = _wolfspeed_diode_digikey()
    payload["Parameters"] = [p for p in payload["Parameters"] if p["Parameter"] != param_to_drop]
    envelope = convert_digikey_to_tas_diode(payload)
    electrical = envelope["semiconductor"]["diode"]["manufacturerInfo"]["datasheetInfo"][
        "electrical"
    ]
    assert absent_field not in electrical


def _wolfspeed_diode_mouser(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ManufacturerPartNumber": "C3D04060A",
        "Manufacturer": "Wolfspeed",
        "MouserPartNumber": "581-C3D04060A",
        "Description": "DIODE SCHOTTKY 600V 4A SiC TO-220AC",
        "DataSheetUrl": "https://wolfspeed.com/.../C3D04060A.pdf",
        "ProductDetailUrl": "https://www.mouser.com/...",
        "AvailabilityInStock": "75",
        "PriceBreaks": [{"Quantity": 1, "Price": "$3.50", "Currency": "USD"}],
        "ProductAttributes": [
            {"AttributeName": "Voltage - DC Reverse (Vr) (Max)", "AttributeValue": "600 V"},
            {"AttributeName": "Voltage - Forward (Vf) (Max) @ If", "AttributeValue": "1.5 V"},
            {"AttributeName": "Current - Average Rectified (Io)", "AttributeValue": "4 A"},
            {"AttributeName": "Reverse Recovery Charge (Qrr) (Typ)", "AttributeValue": "13 nC"},
        ],
    }
    base.update(overrides)
    return base


def test_mouser_diode_happy_path_validates() -> None:
    envelope = convert_mouser_to_tas_diode(_wolfspeed_diode_mouser())
    validate_component("diodes", envelope)
    diode = envelope["semiconductor"]["diode"]
    assert diode["distributorsInfo"][0]["name"] == "Mouser"
    assert diode["distributorsInfo"][0]["cost"] == pytest.approx(3.50)


def test_mouser_diode_thin_payload_raises() -> None:
    """A required electrical field (reverseVoltage) absent from a thin
    Mouser payload must raise."""
    payload = _wolfspeed_diode_mouser()
    payload["ProductAttributes"] = [
        p
        for p in payload["ProductAttributes"]
        if p["AttributeName"] != "Voltage - DC Reverse (Vr) (Max)"
    ]
    with pytest.raises(IncompleteSourceError) as excinfo:
        convert_mouser_to_tas_diode(payload)
    assert excinfo.value.missing_field == "electrical.reverseVoltage"


def test_mouser_diode_optional_qrr_omitted_not_defaulted() -> None:
    """Mouser payload lacking the optional Qrr converts successfully with
    reverseRecoveryCharge omitted — not fabricated."""
    payload = _wolfspeed_diode_mouser()
    payload["ProductAttributes"] = [
        p
        for p in payload["ProductAttributes"]
        if p["AttributeName"] != "Reverse Recovery Charge (Qrr) (Typ)"
    ]
    envelope = convert_mouser_to_tas_diode(payload)
    electrical = envelope["semiconductor"]["diode"]["manufacturerInfo"]["datasheetInfo"][
        "electrical"
    ]
    assert "reverseRecoveryCharge" not in electrical


# ===========================================================================
# IGBT converters
# ===========================================================================


def _infineon_igbt_digikey(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ManufacturerPartNumber": "IKW40N120H3",
        "Manufacturer": {"Value": "Infineon"},
        "DigiKeyPartNumber": "IKW40N120H3-ND",
        "ProductStatus": "Active",
        "UnitPrice": 12.0,
        "QuantityAvailable": 100,
        "PrimaryDatasheet": "https://infineon.com/...",
        "ProductUrl": "https://www.digikey.com/...",
        "Description": {
            "ProductDescription": "IGBT 1200V 40A 250W TO247",
        },
        "Parameters": [
            {"Parameter": "Voltage - Collector Emitter Breakdown (Max)", "Value": "1200 V"},
            {"Parameter": "Vce(on) (Max) @ Vge, Ic", "Value": "2.05 V"},
            {"Parameter": "Current - Collector (Ic) @ 25°C", "Value": "40 A"},
            {"Parameter": "Supplier Device Package", "Value": "TO-247"},
        ],
    }
    base.update(overrides)
    return base


def test_digikey_igbt_happy_path_validates() -> None:
    envelope = convert_digikey_to_tas_igbt(_infineon_igbt_digikey())
    igbt = envelope["semiconductor"]["igbt"]
    part = igbt["manufacturerInfo"]["datasheetInfo"]["part"]
    assert part["partNumber"] == "IKW40N120H3"
    assert part["technology"] == "Si"
    assert part["subType"] == "nChannel"
    electrical = igbt["manufacturerInfo"]["datasheetInfo"]["electrical"]
    assert electrical["collectorEmitterVoltage"] == pytest.approx(1200.0)
    assert electrical["collectorEmitterSaturation"] == pytest.approx(2.05)
    assert electrical["continuousCollectorCurrent"] == pytest.approx(40.0)
    validate_component("igbts", envelope)


@pytest.mark.parametrize(
    "param_to_drop,expected_field",
    [
        (
            "Voltage - Collector Emitter Breakdown (Max)",
            "electrical.collectorEmitterVoltage",
        ),
        ("Vce(on) (Max) @ Vge, Ic", "electrical.collectorEmitterSaturation"),
        (
            "Current - Collector (Ic) @ 25°C",
            "electrical.continuousCollectorCurrent",
        ),
    ],
)
def test_digikey_igbt_missing_required_param_raises(
    param_to_drop: str,
    expected_field: str,
) -> None:
    payload = _infineon_igbt_digikey()
    payload["Parameters"] = [p for p in payload["Parameters"] if p["Parameter"] != param_to_drop]
    with pytest.raises(IncompleteSourceError) as excinfo:
        convert_digikey_to_tas_igbt(payload)
    assert excinfo.value.missing_field == expected_field


@pytest.mark.parametrize(
    "description",
    [
        "IGBT MODULE 1200V 100A HALF BRIDGE",
        "IGBT 6-PACK 600V 30A",
        "IGBT DUAL 1200V 100A",
        "IGBT H-BRIDGE 600V 50A",
    ],
)
def test_digikey_igbt_rejects_modules(description: str) -> None:
    payload = _infineon_igbt_digikey(
        Description={"ProductDescription": description},
    )
    with pytest.raises(IncompleteSourceError) as excinfo:
        convert_digikey_to_tas_igbt(payload)
    assert excinfo.value.missing_field == "semiconductor.igbt"


def test_mouser_igbt_happy_path_validates() -> None:
    payload = {
        "ManufacturerPartNumber": "IKW40N120H3",
        "Manufacturer": "Infineon",
        "MouserPartNumber": "726-IKW40N120H3",
        "Description": "IGBT 1200V 40A 250W TO247",
        "DataSheetUrl": "http://x",
        "ProductDetailUrl": "http://y",
        "AvailabilityInStock": "30",
        "PriceBreaks": [{"Quantity": 1, "Price": "$12.00", "Currency": "USD"}],
        "ProductAttributes": [
            {
                "AttributeName": "Voltage - Collector Emitter Breakdown (Max)",
                "AttributeValue": "1200 V",
            },
            {"AttributeName": "Vce(on) (Max) @ Vge, Ic", "AttributeValue": "2.05 V"},
            {"AttributeName": "Current - Collector (Ic) @ 25°C", "AttributeValue": "40 A"},
        ],
    }
    envelope = convert_mouser_to_tas_igbt(payload)
    validate_component("igbts", envelope)


# ===========================================================================
# Resistor converters
# ===========================================================================


def _vishay_resistor_digikey(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ManufacturerPartNumber": "CRCW08051K00FKEA",
        "Manufacturer": {"Value": "Vishay Dale"},
        "DigiKeyPartNumber": "541-1.00KCCT-ND",
        "ProductStatus": "Active",
        "UnitPrice": 0.10,
        "QuantityAvailable": 100000,
        "PrimaryDatasheet": "http://vishay.com/...",
        "ProductUrl": "http://digikey.com/...",
        "Description": {
            "ProductDescription": "RES SMD 1K OHM 1% 1/8W 0805",
        },
        "Parameters": [
            {"Parameter": "Resistance", "Value": "1 kΩ"},
            {"Parameter": "Tolerance", "Value": "±1%"},
            {"Parameter": "Power (Watts)", "Value": "0.125 W"},
            {"Parameter": "Composition", "Value": "Thick Film"},
            {"Parameter": "Supplier Device Package", "Value": "0805 (2012 Metric)"},
        ],
    }
    base.update(overrides)
    return base


def test_digikey_resistor_happy_path_validates() -> None:
    envelope = convert_digikey_to_tas_resistor(_vishay_resistor_digikey())
    res = envelope["resistor"]
    part = res["manufacturerInfo"]["datasheetInfo"]["part"]
    assert part["technology"] == "thickFilm"
    assert part["case"] == "0805 (2012 Metric)"
    electrical = res["manufacturerInfo"]["datasheetInfo"]["electrical"]
    assert electrical["resistance"] == {"nominal": pytest.approx(1000.0)}
    assert electrical["tolerance"] == pytest.approx(0.01)
    assert electrical["powerRating"] == pytest.approx(0.125)
    validate_component("resistors", envelope)


@pytest.mark.parametrize(
    "raw,expected_fraction",
    [
        ("±1%", 0.01),
        ("5%", 0.05),
        ("0.1%", 0.001),
        ("±0.5%", 0.005),
    ],
)
def test_digikey_resistor_tolerance_parsing(
    raw: str,
    expected_fraction: float,
) -> None:
    payload = _vishay_resistor_digikey()
    for p in payload["Parameters"]:
        if p["Parameter"] == "Tolerance":
            p["Value"] = raw
    envelope = convert_digikey_to_tas_resistor(payload)
    assert envelope["resistor"]["manufacturerInfo"]["datasheetInfo"]["electrical"][
        "tolerance"
    ] == pytest.approx(expected_fraction)


@pytest.mark.parametrize(
    "composition,expected_enum",
    [
        ("Thick Film", "thickFilm"),
        ("Thin Film", "thinFilm"),
        ("Metal Film", "metalFilm"),
        ("Wirewound", "wirewound"),
        ("Wire Wound", "wirewound"),
        ("Carbon Composition", "carbonComposition"),
        ("Current Sense", "currentSenseShunt"),
    ],
)
def test_digikey_resistor_technology_mapping(
    composition: str,
    expected_enum: str,
) -> None:
    payload = _vishay_resistor_digikey()
    for p in payload["Parameters"]:
        if p["Parameter"] == "Composition":
            p["Value"] = composition
    envelope = convert_digikey_to_tas_resistor(payload)
    assert (
        envelope["resistor"]["manufacturerInfo"]["datasheetInfo"]["part"]["technology"]
        == expected_enum
    )


@pytest.mark.parametrize(
    "param_to_drop,expected_field",
    [
        ("Resistance", "electrical.resistance"),
        ("Power (Watts)", "electrical.powerRating"),
        ("Tolerance", "electrical.tolerance"),
        ("Composition", "datasheetInfo.part.technology"),
        ("Supplier Device Package", "datasheetInfo.part.case"),
    ],
)
def test_digikey_resistor_missing_required_param_raises(
    param_to_drop: str,
    expected_field: str,
) -> None:
    payload = _vishay_resistor_digikey()
    payload["Parameters"] = [p for p in payload["Parameters"] if p["Parameter"] != param_to_drop]
    with pytest.raises(IncompleteSourceError) as excinfo:
        convert_digikey_to_tas_resistor(payload)
    assert excinfo.value.missing_field == expected_field


def test_digikey_resistor_unknown_composition_raises() -> None:
    payload = _vishay_resistor_digikey()
    for p in payload["Parameters"]:
        if p["Parameter"] == "Composition":
            p["Value"] = "Quantum Vortex Resistor"
    with pytest.raises(IncompleteSourceError) as excinfo:
        convert_digikey_to_tas_resistor(payload)
    assert excinfo.value.missing_field == "datasheetInfo.part.technology"
    assert "unknown" in str(excinfo.value)


def test_digikey_resistor_unparseable_tolerance_raises() -> None:
    payload = _vishay_resistor_digikey()
    for p in payload["Parameters"]:
        if p["Parameter"] == "Tolerance":
            p["Value"] = "TBD%"
    with pytest.raises(IncompleteSourceError) as excinfo:
        convert_digikey_to_tas_resistor(payload)
    assert excinfo.value.missing_field == "electrical.tolerance"


def test_mouser_resistor_happy_path_validates() -> None:
    payload = {
        "ManufacturerPartNumber": "CRCW08051K00FKEA",
        "Manufacturer": "Vishay Dale",
        "MouserPartNumber": "71-CRCW08051K00FKEA",
        "Description": "RES SMD 1K OHM 1% 1/8W 0805",
        "DataSheetUrl": "http://x",
        "ProductDetailUrl": "http://y",
        "AvailabilityInStock": "50000",
        "PriceBreaks": [{"Quantity": 1, "Price": "$0.10", "Currency": "USD"}],
        "ProductAttributes": [
            {"AttributeName": "Resistance", "AttributeValue": "1 kΩ"},
            {"AttributeName": "Tolerance", "AttributeValue": "±1%"},
            {"AttributeName": "Power (Watts)", "AttributeValue": "0.125 W"},
            {"AttributeName": "Composition", "AttributeValue": "Thick Film"},
            {"AttributeName": "Supplier Device Package", "AttributeValue": "0805"},
        ],
    }
    envelope = convert_mouser_to_tas_resistor(payload)
    validate_component("resistors", envelope)


# ===========================================================================
# Capacitor converters
# ===========================================================================


def _murata_capacitor_digikey(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ManufacturerPartNumber": "GRM21BR71H104KA01L",
        "Manufacturer": {"Value": "Murata"},
        "DigiKeyPartNumber": "490-3296-1-ND",
        "ProductStatus": "Active",
        "UnitPrice": 0.10,
        "QuantityAvailable": 100000,
        "PrimaryDatasheet": "http://murata.com/...",
        "ProductUrl": "http://digikey.com/...",
        "Description": {
            "ProductDescription": "CAP CER 0.1UF 50V X7R 0805",
        },
        "Parameters": [
            {"Parameter": "Capacitance", "Value": "0.1 µF"},
            {"Parameter": "Voltage - Rated", "Value": "50 V"},
            {"Parameter": "ESR (Equivalent Series Resistance)", "Value": "25 mΩ"},
            {"Parameter": "Ripple Current @ Low Frequency", "Value": "2 A"},
            {"Parameter": "Package / Case", "Value": "0805 (2012 Metric)"},
            {"Parameter": "Family", "Value": "Ceramic Capacitors"},
            {"Parameter": "Series", "Value": "GRM"},
            {"Parameter": "Mounting Type", "Value": "Surface Mount, MLCC"},
        ],
    }
    base.update(overrides)
    return base


def test_digikey_capacitor_happy_path_validates() -> None:
    envelope = convert_digikey_to_tas_capacitor(_murata_capacitor_digikey())
    cap = envelope["capacitor"]
    part = cap["manufacturerInfo"]["datasheetInfo"]["part"]
    assert part["technology"] == "MLCC"
    assert part["series"] == "GRM"
    assert part["case"] == "0805 (2012 Metric)"
    electrical = cap["manufacturerInfo"]["datasheetInfo"]["electrical"]
    assert electrical["capacitance"] == {"nominal": pytest.approx(0.1e-6)}
    assert electrical["ratedVoltage"] == pytest.approx(50.0)
    assert electrical["esr"] == pytest.approx(0.025)
    assert electrical["rippleCurrent"] == pytest.approx(2.0)
    mech = cap["manufacturerInfo"]["datasheetInfo"]["mechanical"]
    assert mech["shape"] == {"assembly": "SMT", "shapeType": "rectangular"}
    # We do NOT invent dimensions when the distributor doesn't publish them.
    assert mech["dimensions"] == {}
    validate_component("capacitors", envelope)


@pytest.mark.parametrize(
    "family,expected_tech",
    [
        ("Ceramic Capacitors", "MLCC"),
        ("Aluminum Electrolytic Capacitors", "AluminumElectrolytic"),
        ("Aluminum Polymer Capacitors", "AluminumPolymer"),
        ("Tantalum Capacitors", "Tantalum"),
        ("Tantalum Polymer Capacitors", "TantalumPolymer"),
        ("Film Capacitors", "Film"),
    ],
)
def test_digikey_capacitor_technology_mapping(
    family: str,
    expected_tech: str,
) -> None:
    payload = _murata_capacitor_digikey()
    for p in payload["Parameters"]:
        if p["Parameter"] == "Family":
            p["Value"] = family
    # Aluminum/Tantalum bulk caps don't validate as SMT MLCCs — adjust
    # mounting so the shape resolver doesn't trip while we test only
    # the technology mapping.
    if expected_tech in {
        "AluminumElectrolytic",
        "AluminumPolymer",
        "Supercapacitor",
    }:
        for p in payload["Parameters"]:
            if p["Parameter"] == "Mounting Type":
                p["Value"] = "Through Hole"
    envelope = convert_digikey_to_tas_capacitor(payload)
    assert (
        envelope["capacitor"]["manufacturerInfo"]["datasheetInfo"]["part"]["technology"]
        == expected_tech
    )


@pytest.mark.parametrize(
    "param_to_drop,expected_field",
    [
        # esr and rippleCurrent are OPTIONAL (MLCCs commonly omit them in
        # distributor data; not every cap chemistry specs them the same
        # way). Their absence is covered by the positive test below.
        ("Capacitance", "electrical.capacitance"),
        ("Voltage - Rated", "electrical.ratedVoltage"),
        ("Package / Case", "datasheetInfo.part.case"),
        ("Family", "datasheetInfo.part.technology"),
        ("Series", "datasheetInfo.part.series"),
        ("Mounting Type", "datasheetInfo.mechanical.shape.assembly"),
    ],
)
def test_digikey_capacitor_missing_required_param_raises(
    param_to_drop: str,
    expected_field: str,
) -> None:
    payload = _murata_capacitor_digikey()
    payload["Parameters"] = [p for p in payload["Parameters"] if p["Parameter"] != param_to_drop]
    with pytest.raises(IncompleteSourceError) as excinfo:
        convert_digikey_to_tas_capacitor(payload)
    assert excinfo.value.missing_field == expected_field


@pytest.mark.parametrize(
    "param_to_drop,absent_field",
    [
        ("ESR (Equivalent Series Resistance)", "esr"),
        ("Ripple Current @ Low Frequency", "rippleCurrent"),
    ],
)
def test_digikey_capacitor_optional_param_omitted_not_defaulted(
    param_to_drop: str,
    absent_field: str,
) -> None:
    """An optional capacitor electrical field absent from the payload is
    OMITTED from the envelope, never fabricated (no-fallback rule)."""
    payload = _murata_capacitor_digikey()
    payload["Parameters"] = [p for p in payload["Parameters"] if p["Parameter"] != param_to_drop]
    envelope = convert_digikey_to_tas_capacitor(payload)
    electrical = envelope["capacitor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]
    assert absent_field not in electrical


def test_digikey_capacitor_unrecognised_assembly_raises() -> None:
    payload = _murata_capacitor_digikey()
    for p in payload["Parameters"]:
        if p["Parameter"] == "Mounting Type":
            p["Value"] = "Magnetic Levitation"
    with pytest.raises(IncompleteSourceError) as excinfo:
        convert_digikey_to_tas_capacitor(payload)
    assert excinfo.value.missing_field == "datasheetInfo.mechanical.shape.assembly"


def test_digikey_capacitor_smt_aluminum_refuses_to_guess_shape() -> None:
    """SMT + AluminumElectrolytic is not in the safe-mapping table —
    rather than guessing 'rectangular' (which would be wrong for SMT
    can caps) the converter raises."""
    payload = _murata_capacitor_digikey()
    for p in payload["Parameters"]:
        if p["Parameter"] == "Family":
            p["Value"] = "Aluminum Electrolytic Capacitors"
    with pytest.raises(IncompleteSourceError) as excinfo:
        convert_digikey_to_tas_capacitor(payload)
    assert excinfo.value.missing_field == "datasheetInfo.mechanical.shape.shapeType"


def test_mouser_capacitor_happy_path_validates() -> None:
    payload = {
        "ManufacturerPartNumber": "GRM21BR71H104KA01L",
        "Manufacturer": "Murata",
        "MouserPartNumber": "81-GRM21BR71H104KA01L",
        "Description": "CAP CER 0.1UF 50V X7R 0805",
        "DataSheetUrl": "http://x",
        "ProductDetailUrl": "http://y",
        "AvailabilityInStock": "20000",
        "PriceBreaks": [{"Quantity": 1, "Price": "$0.10", "Currency": "USD"}],
        "ProductAttributes": [
            {"AttributeName": "Capacitance", "AttributeValue": "0.1 µF"},
            {"AttributeName": "Voltage - Rated", "AttributeValue": "50 V"},
            {"AttributeName": "ESR (Equivalent Series Resistance)", "AttributeValue": "25 mΩ"},
            {"AttributeName": "Ripple Current @ Low Frequency", "AttributeValue": "2 A"},
            {"AttributeName": "Package / Case", "AttributeValue": "0805"},
            {"AttributeName": "Family", "AttributeValue": "Ceramic Capacitors"},
            {"AttributeName": "Series", "AttributeValue": "GRM"},
            {"AttributeName": "Mounting Type", "AttributeValue": "Surface Mount, MLCC"},
        ],
    }
    envelope = convert_mouser_to_tas_capacitor(payload)
    validate_component("capacitors", envelope)
