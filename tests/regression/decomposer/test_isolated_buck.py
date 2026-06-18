"""End-to-end regression test for the MKF→TAS isolated-buck (flybuck) decomposer.

Flybuck is the first stencil with TWO external output ports: the primary
synchronous-buck output (Vout_pri) and the isolated secondary output (Vout0).
The controller regulates around Vout_pri; the secondary is open-loop.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from heaviside.decomposer import decompose_from_spec

GOLDEN_DIR = Path(__file__).parent / "golden"
SPICE_GOLDEN = GOLDEN_DIR / "isobuck_48to12_5A.spice"
TAS_GOLDEN = GOLDEN_DIR / "isobuck_48to12_5A.tas.json"

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


def test_isobuck_decompose_matches_golden() -> None:
    netlist, tas = decompose_from_spec(
        "isolated_buck",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
    )
    tas_json = json.dumps(tas, indent=2) + "\n"

    _maybe_update(SPICE_GOLDEN, netlist)
    _maybe_update(TAS_GOLDEN, tas_json)

    if not SPICE_GOLDEN.exists() or not TAS_GOLDEN.exists():
        pytest.fail("Golden fixtures missing. Run with HEAVISIDE_UPDATE_GOLDENS=1 to create.")

    assert netlist == SPICE_GOLDEN.read_text()
    assert tas_json == TAS_GOLDEN.read_text()


def test_isobuck_tas_round_trip_shape() -> None:
    _, tas = decompose_from_spec(
        "isolated_buck",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
    )
    roles = [s["role"] for s in tas["topology"]["stages"]]
    assert roles == ["switchingCell", "isolation", "outputFilter", "outputRectifier", "control"], (
        roles
    )

    sw_names = {
        c["name"]
        for c in tas["topology"]["stages"][0]["circuit"]["components"]
        if not c["name"].startswith("P_")
    }
    assert sw_names == {"Q1", "Q2"}, sw_names

    ports = {p["name"]: p for p in tas["topology"]["interStageConnections"]}
    # v2: GND/gate wires live inside stage circuits.
    # Flybuck: T1.pri.2, C_pri.1, and Vout_pri are the same node (no primary choke),
    # so isolation.pri_out, output_pri.in, and output_pri.out all collapse into Vout_pri.
    assert set(ports) == {"Vin", "switch_node", "Vout_pri", "sec0_node", "Vout0"}, set(ports)

    # v2 endpoints use {stage, port}
    sw_eps = {(e["stage"], e["port"]) for e in ports["switch_node"]["endpoints"]}
    assert sw_eps == {("primary_switch", "sw"), ("isolation", "in")}, sw_eps

    vp_eps = {(e["stage"], e["port"]) for e in ports["Vout_pri"]["endpoints"]}
    assert vp_eps == {("isolation", "pri_out"), ("output_pri", "in"), ("output_pri", "out")}, vp_eps

    # Controller regulates around Vout_pri (NOT Vout0) — flybuck signature (v2 uses 'net' not 'wire').
    sense = tas["topology"]["stages"][4]["senses"][0]["net"]
    assert sense == "Vout_pri", sense
