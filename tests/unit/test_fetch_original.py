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


def _phoenix_terminal_block(mpn="1707654"):
    # Real Digi-Key shape for a Phoenix Contact terminal-block header: current /
    # voltage under the IEC/UL aliases, gender only in the free-text Type field,
    # dual-unit pitch. None of this matches the Würth series fetcher.
    return {
        "ManufacturerPartNumber": mpn,
        "Manufacturer": {"Value": "Phoenix Contact"},
        "Category": {"Value": "Connectors, Interconnects"},
        "Family": {"Value": "Terminal Blocks - Headers, Plugs and Sockets"},
        "Description": {},
        "DatasheetUrl": "https://www.phoenixcontact.com/us/products/1707654/pdf",
        "Parameters": [
            {"Parameter": "Mounting Type", "Value": "Through Hole"},
            {"Parameter": "Number of Positions", "Value": "4"},
            {"Parameter": "Pitch", "Value": '0.150" (3.81mm)'},
            {"Parameter": "Type", "Value": "Header, Male Pins, Shrouded (4 Side)"},
            {"Parameter": "Current - IEC", "Value": "8A"},
            {"Parameter": "Current - UL", "Value": "8 A"},
            {"Parameter": "Voltage - IEC", "Value": "250 V"},
            {"Parameter": "Voltage - UL", "Value": "300 V"},
        ],
    }


def test_generic_connector_converter_non_wurth():
    # A non-Würth connector (Phoenix Contact terminal block) must still convert to
    # a valid CONAS envelope: family from the Digi-Key taxonomy, current/voltage
    # from the IEC/UL aliases, gender inferred from the Type field, mm pitch.
    env, info = fetch_original_envelope(_FakeDK(_phoenix_terminal_block()), "1707654", "connector")
    assert env is not None and info == "connectors"
    mi = env["connector"]["manufacturerInfo"]
    di = mi["datasheetInfo"]
    assert mi["name"] == "Phoenix Contact"
    assert di["familyDetails"]["family"] == "terminalBlock"
    assert di["electrical"]["ratedCurrentPerContact"] == 8.0
    assert di["electrical"]["ratedVoltage"] == 300.0
    assert di["mechanical"]["positions"] == 4
    assert abs(di["mechanical"]["pitch"] - 0.00381) < 1e-6
    assert di["part"]["matingPolarity"] == "male"


def test_fetch_original_auto_classifies_bare_mpn():
    # category="" -> the category is inferred from the Digi-Key product taxonomy
    # (the bare-pasted-MPN case: "1707654" with no type column), then converted
    # and validated. This is what lets the librarian source such a row at all.
    env, info = fetch_original_envelope(_FakeDK(_phoenix_terminal_block()), "1707654", "")
    assert env is not None and info == "connectors"


def test_classify_dk_product_taxonomy():
    from heaviside.librarian.fetcher.original import classify_dk_product

    assert classify_dk_product(_phoenix_terminal_block()) == "connector"
    assert classify_dk_product(_connector()) == "connector"
    cap = {"Category": {"Value": "Capacitors"}, "Family": {"Value": "Ceramic"}}
    assert classify_dk_product(cap) == "capacitor"
    ic = {"Category": {"Value": "Integrated Circuits (ICs)"}, "Family": {"Value": "Linear"}}
    assert classify_dk_product(ic) is None  # not a sourceable category → None
