"""End-to-end regression test for the MKF→TAS isolated-buck-boost decomposer.

Isolated buck-boost has two outputs and an unusual primary-side topology:
a single switch + transformer + a primary diode (D_pri) whose CATHODE
taps the switch node and whose ANODE feeds C_pri at Vout_pri. Plus the
standard isolated secondary (D_out0 + C_out0 → Vout0).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from heaviside.decomposer import decompose_from_spec

GOLDEN_DIR = Path(__file__).parent / "golden"
SPICE_GOLDEN = GOLDEN_DIR / "isobb_48to12_5A.spice"
TAS_GOLDEN = GOLDEN_DIR / "isobb_48to12_5A.tas.json"

SPEC: dict[str, object] = {
    "inputVoltage": {"nominal": 48.0},
    "diodeVoltageDrop": 0.7,
    "maximumSwitchCurrent": 20.0,
    "currentRippleRatio": 0.4,
    "efficiency": 0.95,
    "desiredInductance": 22e-6,
    "operatingPoints": [
        {
            "switchingFrequency": 200000.0,
            "ambientTemperature": 25.0,
            "outputVoltages": [12.0, 5.0],
            "outputCurrents": [5.0, 1.0],
        }
    ],
}
MAGNETIZING_INDUCTANCE = 1e-3
TURNS_RATIOS = [2.0]


def _maybe_update(path: Path, content: str) -> None:
    if os.environ.get("HEAVISIDE_UPDATE_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def test_isobb_decompose_matches_golden() -> None:
    netlist, tas = decompose_from_spec(
        "isolated_buck_boost",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
    )
    tas_json = json.dumps(tas, indent=2) + "\n"

    _maybe_update(SPICE_GOLDEN, netlist)
    _maybe_update(TAS_GOLDEN, tas_json)

    if not SPICE_GOLDEN.exists() or not TAS_GOLDEN.exists():
        pytest.fail(
            "Golden fixtures missing. Run with HEAVISIDE_UPDATE_GOLDENS=1 to create."
        )

    assert netlist == SPICE_GOLDEN.read_text()
    assert tas_json == TAS_GOLDEN.read_text()


def test_isobb_tas_round_trip_shape() -> None:
    _, tas = decompose_from_spec(
        "isolated_buck_boost",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
    )
    roles = [s["role"] for s in tas["stages"]]
    assert roles == [
        "switchingCell", "isolation", "outputRectifier",
        "outputRectifier", "control"
    ], roles

    # 1 switch (no synch rectifier).
    sw_names = {c["name"] for c in tas["stages"][0]["circuit"]["components"]}
    assert sw_names == {"Q1"}, sw_names

    # Primary output rectifier has D_pri + C_pri.
    pri_names = {c["name"] for c in tas["stages"][2]["circuit"]["components"]}
    assert pri_names == {"D_pri", "C_pri"}, pri_names

    ports = {p["name"]: p for p in tas["interStageCircuit"]}
    assert set(ports) == {"Vin", "switch_node", "Vout_pri",
                          "sec0_node", "Vout0",
                          "GND", "Q1_gate"}, set(ports)

    # Switch node taps Q1.S + T1.pri.1 + D_pri.K (the inverting-BB signature).
    sw_eps = {(e["component"], e["pin"]) for e in ports["switch_node"]["endpoints"]}
    assert sw_eps == {("Q1", "S"), ("T1", "pri.1"), ("D_pri", "K")}, sw_eps

    # Controller regulates Vout_pri.
    sense = tas["stages"][4]["senses"][0]["wire"]
    assert sense == "Vout_pri", sense
