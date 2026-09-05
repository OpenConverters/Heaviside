"""A suppressor is not a rectifier, and must not be asked a rectifier's questions.

SMAJ58A-13-F failed to source with

    conversion failed: digikey payload for 'SMAJ58A-13-F' is missing required
    field 'electrical.forwardCurrent' (no Digi-Key parameter matched any of:
    'Current - Average Rectified (Io)', ...)

Digi-Key holds that part in full. It publishes no average rectified forward
current because a transient-voltage-suppression diode has none — it conducts
nothing until it avalanches. SAS/schemas/diode.json has always known this: its
requirements are conditional on part.subType, and a tvs is asked for
standoffVoltage, clampingVoltage and a pulse rating instead. The converter
mirrored none of that and demanded the rectifier set from every diode, so no
TVS and no zener could ever be sourced from a distributor.

The payloads here are REAL — captured from the live Digi-Key feed on
2026-09-05, not written from memory, because the parameter labels carry their
own punctuation ("Current - Peak Pulse (10/1000us)") and that is exactly the
kind of detail a remembered fixture gets wrong and a test then locks in.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from heaviside.librarian.fetcher.base import IncompleteSourceError
from heaviside.librarian.fetcher.convert import (
    _build_diode_envelope,
    _resolve_diode_subtype,
)
from heaviside.librarian.tas import validate_component

_PAYLOADS = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "digikey" / "diode_payloads.json")
    .read_text(encoding="utf-8")
)


def _build(mpn: str) -> dict:
    p = _PAYLOADS[mpn]
    params = {x["Parameter"]: x["Value"]
              for x in p["Parameters"] if x["Value"] not in (None, "-")}
    return _build_diode_envelope(
        source="digikey", mpn=mpn, manufacturer="Diodes Incorporated",
        description=f"{p['ProductDescription']} {p['DetailedDescription']}",
        params=params,
        distributor_block={"name": "DigiKey", "reference": mpn},
        datasheet_url="https://example.invalid/x.pdf", status="production",
    )


def _electrical(env: dict) -> dict:
    return env["semiconductor"]["diode"]["manufacturerInfo"]["datasheetInfo"]["electrical"]


def _subtype(env: dict) -> str:
    return env["semiconductor"]["diode"]["manufacturerInfo"]["datasheetInfo"]["part"]["subType"]


@pytest.mark.parametrize("mpn", sorted(_PAYLOADS))
def test_every_real_payload_builds_and_validates(mpn):
    env = _build(mpn)
    di = env["semiconductor"]["diode"]["manufacturerInfo"]["datasheetInfo"]
    di["provenance"] = [{"source": "distributor", "sourceName": "DigiKey",
                         "retrievedDate": datetime.date.today().isoformat()}]
    validate_component("diodes", env)          # raises on failure


def test_a_tvs_carries_what_a_tvs_is_characterised_by():
    e = _electrical(_build("SMAJ58A-13-F"))
    assert _subtype(_build("SMAJ58A-13-F")) == "tvs"
    assert e["standoffVoltage"] == 58.0
    assert e["clampingVoltage"] == 93.6
    assert e["peakPulseCurrent"] == 4.3
    assert e["peakPulsePower"] == 400.0
    # and nothing invented a forward current it does not have
    assert "forwardCurrent" not in e


def test_a_zener_is_asked_for_a_zener_voltage_and_a_power_rating():
    env = _build("BZT52C5V1-7-F")
    assert _subtype(env) == "zener"
    e = _electrical(env)
    assert e["breakdownVoltage"] == {"nominal": 5.1}
    assert e["powerDissipation"] == 0.5
    assert "forwardCurrent" not in e


def test_a_rectifier_still_must_have_its_forward_current():
    env = _build("B1100-13-F")
    assert _subtype(env) == "schottky"
    e = _electrical(env)
    assert e["forwardCurrent"] == 1.0
    assert e["reverseVoltage"] == 100.0


def test_a_rectifier_missing_its_forward_current_is_still_refused():
    """The counter-check: the rectifier path must not have gone soft."""
    p = _PAYLOADS["B1100-13-F"]
    params = {x["Parameter"]: x["Value"]
              for x in p["Parameters"] if x["Value"] not in (None, "-")}
    params.pop("Current - Average Rectified (Io)", None)
    params.pop("Current - Average Rectified (Io) (per Diode)", None)
    with pytest.raises(IncompleteSourceError) as exc:
        _build_diode_envelope(
            source="digikey", mpn="B1100-13-F", manufacturer="Diodes Incorporated",
            description=f"{p['ProductDescription']} {p['DetailedDescription']}",
            params=params,
            distributor_block={"name": "DigiKey", "reference": "B1100-13-F"},
            datasheet_url="https://example.invalid/x.pdf", status="production")
    assert "forwardCurrent" in str(exc.value)


def test_a_tvs_with_no_pulse_rating_at_all_is_refused():
    """The schema wants one of peakPulseCurrent/peakPulsePower; so does this."""
    p = _PAYLOADS["SMAJ58A-13-F"]
    params = {x["Parameter"]: x["Value"]
              for x in p["Parameters"] if x["Value"] not in (None, "-")}
    params.pop("Current - Peak Pulse (10/1000µs)", None)
    params.pop("Power - Peak Pulse", None)
    with pytest.raises(IncompleteSourceError) as exc:
        _build_diode_envelope(
            source="digikey", mpn="SMAJ58A-13-F", manufacturer="Diodes Incorporated",
            description=f"{p['ProductDescription']} {p['DetailedDescription']}",
            params=params,
            distributor_block={"name": "DigiKey", "reference": "SMAJ58A-13-F"},
            datasheet_url="https://example.invalid/x.pdf", status="production")
    assert "peakPulseCurrent" in str(exc.value)


def test_tvs_is_resolved_before_zener():
    # Digi-Key files every TVS under Type = "Zener" while describing it as a
    # TVS. The other order made a suppressor a zener, and then demanded the
    # steady-state power dissipation a suppressor does not publish.
    assert _resolve_diode_subtype("TVS DIODE 58VWM 93.6VC SMA Zener") == "tvs"
    assert _resolve_diode_subtype("DIODE ZENER 5.1V 500MW SOD123") == "zener"
