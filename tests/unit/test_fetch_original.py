"""The librarian fetches an unknown ORIGINAL from Digi-Key, converts it to the
category envelope, and schema-validates before trusting it — so an original
absent from the internal DB gets verified instead of an immediate no_substitute.
Bad/unconvertible originals are dropped (never persisted). No network: a fake
Digi-Key client returns synthetic products."""

from __future__ import annotations

from heaviside.librarian.fetcher.original import fetch_original_envelope


class _FakeDK:
    def __init__(self, product):
        self._p = product

    def get_product(self, mpn):  # detail endpoint absent in this fake
        raise RuntimeError("no detail endpoint")

    def search(self, mpn, limit=10):
        return {"Products": [self._p] if self._p else []}


def _crystal(mpn="ABLS-16.000MHZ-B4-T"):
    return {
        "ManufacturerPartNumber": mpn,
        "Manufacturer": {"Value": "Abracon LLC"},
        "Family": {"Value": "Crystals"},
        "Description": {"DetailedDescription": "CRYSTAL 16MHZ 18PF SMD"},
        "Parameters": [
            {"Parameter": "Frequency", "Value": "16 MHz"},
            {"Parameter": "Frequency Tolerance", "Value": "±30ppm"},
            {"Parameter": "Load Capacitance", "Value": "18 pF"},
            {"Parameter": "Operating Mode", "Value": "Fundamental"},
            {"Parameter": "Package / Case", "Value": "4-SMD"},
        ],
    }


def _connector(mpn="691216510002"):
    return {
        "ManufacturerPartNumber": mpn,
        "Manufacturer": {"Value": "Würth Elektronik"},
        "Family": {"Value": "Terminal Blocks - Headers, Plugs and Sockets"},
        "Description": {"DetailedDescription": "TERM BLOCK HDR 2POS 5.08MM"},
        "Parameters": [
            {"Parameter": "Current Rating (Amps)", "Value": "16 A"},
            {"Parameter": "Voltage Rating", "Value": "300 V"},
            {"Parameter": "Number of Positions", "Value": "2"},
            {"Parameter": "Pitch - Mating", "Value": "5.08 mm"},
            {"Parameter": "Mounting Type", "Value": "Through Hole"},
            {"Parameter": "Gender", "Value": "Male"},
            {"Parameter": "Operating Temperature", "Value": "-40°C ~ 130°C"},
        ],
    }


def test_fetch_crystal_original_valid():
    env, info = fetch_original_envelope(_FakeDK(_crystal()), "ABLS-16.000MHZ-B4-T", "timeBase")
    assert env is not None and info == "timebases"
    elec = env["timeBase"]["oscillator"]["manufacturerInfo"]["datasheetInfo"]["electrical"]
    assert elec["frequency"] == 16_000_000.0
    assert elec["technology"] == "quartzCrystal"


def test_fetch_connector_original_valid():
    env, info = fetch_original_envelope(_FakeDK(_connector()), "691216510002", "connector")
    assert env is not None and info == "connectors"
    assert env["connector"]["manufacturerInfo"]["reference"] == "691216510002"


def test_no_converter_category_returns_none():
    env, reason = fetch_original_envelope(_FakeDK(_crystal()), "OPA333", "analog")
    assert env is None and "no Digi-Key converter" in reason


def test_not_found_returns_none():
    env, reason = fetch_original_envelope(_FakeDK(None), "NOPE", "timeBase")
    assert env is None and "not found" in reason


def test_mpn_mismatch_not_accepted():
    # Search returns a different part — must not be accepted as the original.
    wrong = _crystal("SOME-OTHER-XTAL")
    env, reason = fetch_original_envelope(_FakeDK(wrong), "ABLS-16.000MHZ-B4-T", "timeBase")
    assert env is None
