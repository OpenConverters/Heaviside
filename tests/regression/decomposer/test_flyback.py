"""End-to-end regression test for the MKF→TAS flyback decomposer.

Flyback is the proof-of-concept for the isolated single-switch family —
the first stencil to introduce the T1 multi-winding transformer and the
multi-stage TAS shape (switchingCell → isolation → outputRectifier → control).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from heaviside.decomposer import decompose_from_spec

GOLDEN_DIR = Path(__file__).parent / "golden"
SPICE_GOLDEN = GOLDEN_DIR / "flyback_48to12_2A.spice"
TAS_GOLDEN = GOLDEN_DIR / "flyback_48to12_2A.tas.json"

SPEC: dict[str, object] = {
    "inputVoltage": {"nominal": 48.0},
    "diodeVoltageDrop": 0.7,
    "maximumSwitchCurrent": 20.0,
    "currentRippleRatio": 0.4,
    "efficiency": 0.95,
    "desiredInductance": 22e-6,
    "maximumDrainSourceVoltage": 200.0,
    "maximumDutyCycle": 0.5,
    "operatingPoints": [
        {
            "switchingFrequency": 200000.0,
            "ambientTemperature": 25.0,
            "outputVoltages": [12.0],
            "outputCurrents": [2.0],
        }
    ],
}
MAGNETIZING_INDUCTANCE = 1e-3
TURNS_RATIOS = [2.0]


def _maybe_update(path: Path, content: str) -> None:
    if os.environ.get("HEAVISIDE_UPDATE_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def test_flyback_decompose_matches_golden() -> None:
    netlist, tas = decompose_from_spec(
        "flyback",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
    )
    tas_json = json.dumps(tas, indent=2) + "\n"

    _maybe_update(SPICE_GOLDEN, netlist)
    _maybe_update(TAS_GOLDEN, tas_json)

    if not SPICE_GOLDEN.exists() or not TAS_GOLDEN.exists():
        pytest.fail("Golden fixtures missing. Run with HEAVISIDE_UPDATE_GOLDENS=1 to create.")

    assert netlist == SPICE_GOLDEN.read_text(), (
        "MKF spice deck for flyback has drifted from the golden fixture."
    )
    assert tas_json == TAS_GOLDEN.read_text(), (
        "Decomposed TAS for flyback has drifted from the golden fixture."
    )


def test_flyback_tas_round_trip_shape() -> None:
    """Structural invariants: four stages (switchingCell, isolation,
    outputRectifier, control), T1 carries pri+sec0 windings, Vin enters
    at Q1.D, switch_node bridges switch and T1, Vout0 exits the rectifier."""
    _, tas = decompose_from_spec(
        "flyback",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
    )
    roles = [s["role"] for s in tas["topology"]["stages"]]
    assert roles == ["switchingCell", "isolation", "outputRectifier", "control"], roles

    # Primary switch stage holds Q1 only.
    sw_names = {
        c["name"]
        for c in tas["topology"]["stages"][0]["circuit"]["components"]
        if not c["name"].startswith("P_")
    }
    assert sw_names == {"Q1"}, sw_names

    # Isolation stage holds T1 only, with pri+sec0 pins.
    iso = tas["topology"]["stages"][1]
    iso_names = {c["name"] for c in iso["circuit"]["components"] if not c["name"].startswith("P_")}
    assert iso_names == {"T1"}, iso_names
    # Pins derived from observed connection endpoints (writer convention).
    t1_pins = {
        ep["pin"]
        for w in tas["topology"]["interStageCircuit"]
        for ep in w.get("endpoints", [])
        if ep["component"] == "T1"
    }
    assert t1_pins == {"pri.1", "pri.2", "sec0.1", "sec0.2"}, t1_pins

    # Output rectifier stage holds D_out0 and C_out0.
    rect_names = {
        c["name"]
        for c in tas["topology"]["stages"][2]["circuit"]["components"]
        if not c["name"].startswith("P_")
    }
    assert rect_names == {"D_out0", "C_out0"}, rect_names

    # interStageCircuit must wire switch_node and sec0_node, plus Vin/Vout0.
    ports = {p["name"]: p for p in tas["topology"]["interStageCircuit"]}
    assert set(ports) == {"Vin", "switch_node", "sec0_node", "Vout0", "GND"}

    vin_eps = {
        (e["component"], e["pin"])
        for e in ports["Vin"]["endpoints"]
        if not e["component"].startswith("P_")
    }
    assert vin_eps == {("Q1", "D")}, vin_eps

    sw_eps = {
        (e["component"], e["pin"])
        for e in ports["switch_node"]["endpoints"]
        if not e["component"].startswith("P_")
    }
    assert sw_eps == {("Q1", "S"), ("T1", "pri.1")}, sw_eps

    sec_eps = {
        (e["component"], e["pin"])
        for e in ports["sec0_node"]["endpoints"]
        if not e["component"].startswith("P_")
    }
    assert sec_eps == {("T1", "sec0.2"), ("D_out0", "A")}, sec_eps

    vout_eps = {
        (e["component"], e["pin"])
        for e in ports["Vout0"]["endpoints"]
        if not e["component"].startswith("P_")
    }
    assert vout_eps == {("D_out0", "K"), ("C_out0", "1")}, vout_eps

    # Controller drives Q1.
    drives = {d["component"] for d in tas["topology"]["stages"][3]["drives"]}
    assert drives == {"Q1"}, drives
