"""End-to-end regression test for the MKF→TAS active-clamp forward decomposer.

Active-clamp forward exercises the full isolated TAS shape: primary
switch + active clamp (Q_clamp + C_clamp), T1 transformer, two-diode
forward output rectifier with output choke, and a controller that drives
both primary switches.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from heaviside.decomposer import decompose_from_spec

GOLDEN_DIR = Path(__file__).parent / "golden"
SPICE_GOLDEN = GOLDEN_DIR / "acf_48to12_5A.spice"
TAS_GOLDEN = GOLDEN_DIR / "acf_48to12_5A.tas.json"

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
TURNS_RATIOS = [2.0]


def _maybe_update(path: Path, content: str) -> None:
    if os.environ.get("HEAVISIDE_UPDATE_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def test_acf_decompose_matches_golden() -> None:
    netlist, tas = decompose_from_spec(
        "active_clamp_forward",
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


def test_acf_tas_round_trip_shape() -> None:
    _, tas = decompose_from_spec(
        "active_clamp_forward",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
    )
    roles = [s["role"] for s in tas["topology"]["stages"]]
    assert roles == ["switchingCell", "isolation", "outputRectifier", "control"], roles

    sw_names = {
        c["name"]
        for c in tas["topology"]["stages"][0]["circuit"]["components"]
        if not c["name"].startswith("P_")
    }
    assert sw_names == {"Q1", "Q_clamp", "C_clamp"}, sw_names

    rect_names = {
        c["name"]
        for c in tas["topology"]["stages"][2]["circuit"]["components"]
        if not c["name"].startswith("P_")
    }
    assert rect_names == {"D_fwd0", "D_fw0", "L_out0", "C_out0"}, rect_names

    ports = {p["name"]: p for p in tas["topology"]["interStageConnections"]}
    # v2: GND/gate wires live inside stage circuits
    assert set(ports) == {"Vin", "switch_node", "sec0_node", "Vout0"}, set(ports)

    # v2 endpoints use {stage, port}
    sw_eps = {(e["stage"], e["port"]) for e in ports["switch_node"]["endpoints"]}
    assert sw_eps == {("primary_switch", "sw"), ("isolation", "in")}, sw_eps

    vout_eps = {(e["stage"], e["port"]) for e in ports["Vout0"]["endpoints"]}
    assert vout_eps == {("output_0", "out")}, vout_eps

    # Controller drives both primary switches.
    drives = {d["component"] for d in tas["topology"]["stages"][3]["drives"]}
    assert drives == {"Q1", "Q_clamp"}, drives
