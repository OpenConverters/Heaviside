"""End-to-end regression test for the MKF→TAS four-switch buck-boost decomposer.

Pins both the MKF spice deck and the decomposed TAS dict to golden files;
regenerate with ``HEAVISIDE_UPDATE_GOLDENS=1``.

4SBB is the first family member to introduce a real input capacitor (C_in)
in the BOM — MKF actually emits it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from heaviside.decomposer import decompose_from_spec

GOLDEN_DIR = Path(__file__).parent / "golden"
SPICE_GOLDEN = GOLDEN_DIR / "4sbb_48to12_5A.spice"
TAS_GOLDEN = GOLDEN_DIR / "4sbb_48to12_5A.tas.json"

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
            "outputVoltages": [12.0],
            "outputCurrents": [5.0],
        }
    ],
}
MAGNETIZING_INDUCTANCE = 1e-3


def _maybe_update(path: Path, content: str) -> None:
    if os.environ.get("HEAVISIDE_UPDATE_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def test_4sbb_decompose_matches_golden() -> None:
    netlist, tas = decompose_from_spec(
        "four_switch_buck_boost",
        SPEC,
        turns_ratios=[],
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
    )
    tas_json = json.dumps(tas, indent=2) + "\n"

    _maybe_update(SPICE_GOLDEN, netlist)
    _maybe_update(TAS_GOLDEN, tas_json)

    if not SPICE_GOLDEN.exists() or not TAS_GOLDEN.exists():
        pytest.fail(
            "Golden fixtures missing. Run with HEAVISIDE_UPDATE_GOLDENS=1 to create."
        )

    assert netlist == SPICE_GOLDEN.read_text(), (
        "MKF spice deck for 4SBB has drifted from the golden fixture."
    )
    assert tas_json == TAS_GOLDEN.read_text(), (
        "Decomposed TAS for 4SBB has drifted from the golden fixture."
    )


def test_4sbb_tas_round_trip_shape() -> None:
    """Structural invariants: 4-switch H-bridge around a single inductor,
    real input cap, controller drives all four gates."""
    _, tas = decompose_from_spec(
        "four_switch_buck_boost",
        SPEC,
        turns_ratios=[],
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
    )
    roles = [s["role"] for s in tas["topology"]["stages"]]
    assert roles == ["switchingCell", "control"], roles

    sc = tas["topology"]["stages"][0]
    names = {c["name"] for c in sc["circuit"]["components"]}
    assert names == {"Q1", "Q2", "Q3", "Q4", "L1", "C_in", "C_out"}, names

    conn_names = {c["name"] for c in sc["circuit"]["connections"]}
    assert conn_names == {"sw1", "sw2"}, conn_names

    ports = {p["name"]: p for p in tas["topology"]["interStageCircuit"]}
    assert set(ports) == {"Vin", "Vout", "GND",
                          "Q1_gate", "Q2_gate", "Q3_gate", "Q4_gate"}
    vin_eps = {(e["component"], e["pin"]) for e in ports["Vin"]["endpoints"]}
    assert vin_eps == {("Q1", "D"), ("C_in", "1")}, vin_eps
    vout_eps = {(e["component"], e["pin"]) for e in ports["Vout"]["endpoints"]}
    assert vout_eps == {("Q3", "S"), ("C_out", "1")}, vout_eps

    # Controller must drive all four switches.
    drives = {d["component"] for d in tas["topology"]["stages"][1]["drives"]}
    assert drives == {"Q1", "Q2", "Q3", "Q4"}, drives
