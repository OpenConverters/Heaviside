"""End-to-end regression test for MKF→TAS two-switch forward decomposer.

Two-switch forward adds a low-side switch Q2 and a pair of reset diodes
D1/D2 to the single-switch forward. Both ends of the primary winding are
active nets; the reset path commutates primary current back to Vin via
D1+D2 when both switches turn OFF.

Output stage matches active-clamp forward: D_fwd + D_fw + L_out0 + C_out0.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from heaviside.decomposer import decompose_from_spec

GOLDEN_DIR = Path(__file__).parent / "golden"
SPICE_GOLDEN = GOLDEN_DIR / "2sforward_48to5_2A.spice"
TAS_GOLDEN = GOLDEN_DIR / "2sforward_48to5_2A.tas.json"

# Two-switch forward enforces D ≤ 0.45 (transformer reset constraint).
# With Vin=48 and Ns:Np=1:2 (turnsRatios=[2.0]), Vout=5V → D≈23.75%, safe.
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
            "outputVoltages": [5.0],
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


def test_2sforward_decompose_matches_golden() -> None:
    netlist, tas = decompose_from_spec(
        "two_switch_forward",
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


def test_2sforward_tas_round_trip_shape() -> None:
    _, tas = decompose_from_spec(
        "two_switch_forward",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
    )
    roles = [s["role"] for s in tas["topology"]["stages"]]
    assert roles == [
        "switchingCell",
        "isolation",
        "outputRectifier",
        "control",
    ], roles

    sw_names = {c["name"] for c in tas["topology"]["stages"][0]["circuit"]["components"]}
    assert sw_names == {"Q1", "Q2", "D1", "D2"}, sw_names

    t1 = tas["topology"]["stages"][1]["circuit"]["components"][0]
    assert t1["name"] == "T1"
    assert set(t1["pins"]) == {"pri.1", "pri.2", "sec0.1", "sec0.2"}, t1["pins"]

    rect_names = {c["name"] for c in tas["topology"]["stages"][2]["circuit"]["components"]}
    assert rect_names == {"D_fwd", "D_fw", "L_out0", "C_out0"}, rect_names

    ports = {p["name"]: p for p in tas["topology"]["interStageCircuit"]}
    assert set(ports) == {
        "Vin",
        "switch_node",
        "pri_gnd_node",
        "sec0_node",
        "Vout0",
        "GND",
        "Q1_gate",
        "Q2_gate",
    }, set(ports)

    # Vin must reach Q1.D (source) AND D2.K (reset return).
    vin_eps = {(e["component"], e["pin"]) for e in ports["Vin"]["endpoints"]}
    assert vin_eps == {("Q1", "D"), ("D2", "K")}, vin_eps

    # pri_gnd_node must bridge Q2.D, D2.A, and T1.pri.2.
    pgn_eps = {(e["component"], e["pin"]) for e in ports["pri_gnd_node"]["endpoints"]}
    assert pgn_eps == {("Q2", "D"), ("D2", "A"), ("T1", "pri.2")}, pgn_eps

    # switch_node must bridge Q1.S, D1.K, and T1.pri.1.
    swn_eps = {(e["component"], e["pin"]) for e in ports["switch_node"]["endpoints"]}
    assert swn_eps == {("Q1", "S"), ("D1", "K"), ("T1", "pri.1")}, swn_eps

    # Controller drives both Q1 and Q2.
    drives = {d["component"] for d in tas["topology"]["stages"][3]["drives"]}
    assert drives == {"Q1", "Q2"}, drives
