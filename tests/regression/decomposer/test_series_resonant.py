"""End-to-end regression test for MKF→TAS series-resonant (SRC) decomposer.

SRC mirrors the LLC inverter shape (half-bridge with bus split + the
series tank C_r + L_r) but rectifies with a full diode bridge instead of
a center-tapped pair — no output choke, no CT node.

Run with ``HEAVISIDE_UPDATE_GOLDENS=1`` to (re)create the goldens.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from heaviside.decomposer import decompose_from_spec

GOLDEN_DIR = Path(__file__).parent / "golden"
SPICE_GOLDEN = GOLDEN_DIR / "src_48to12_5A.spice"
TAS_GOLDEN = GOLDEN_DIR / "src_48to12_5A.tas.json"

SPEC: dict[str, object] = {
    "inputVoltage": {"nominal": 48.0},
    "diodeVoltageDrop": 0.7,
    "maximumSwitchCurrent": 20.0,
    "currentRippleRatio": 0.4,
    "efficiency": 0.95,
    "desiredInductance": 2.2e-5,
    "operatingPoints": [
        {
            "switchingFrequency": 200000.0,
            "ambientTemperature": 25.0,
            "outputVoltages": [12.0],
            "outputCurrents": [5.0],
        }
    ],
    "minSwitchingFrequency": 100000.0,
    "maxSwitchingFrequency": 300000.0,
}
MAGNETIZING_INDUCTANCE = 1e-3
TURNS_RATIOS = [2.0]
BRIDGE_MODE = "switch"


def _decompose() -> tuple[str, dict]:
    return decompose_from_spec(
        "series_resonant",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
        bridge_simulation_mode=BRIDGE_MODE,
    )


def _maybe_update(path: Path, content: str) -> None:
    if os.environ.get("HEAVISIDE_UPDATE_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def test_series_resonant_decompose_matches_golden() -> None:
    netlist, tas = _decompose()
    tas_json = json.dumps(tas, indent=2) + "\n"

    _maybe_update(SPICE_GOLDEN, netlist)
    _maybe_update(TAS_GOLDEN, tas_json)

    if not SPICE_GOLDEN.exists() or not TAS_GOLDEN.exists():
        pytest.fail("Golden fixtures missing. Run with HEAVISIDE_UPDATE_GOLDENS=1 to create.")

    assert netlist == SPICE_GOLDEN.read_text()
    assert tas_json == TAS_GOLDEN.read_text()


def test_series_resonant_tas_round_trip_shape() -> None:
    _, tas = _decompose()

    roles = [s["role"] for s in tas["topology"]["stages"]]
    assert roles == [
        "inverter",
        "isolation",
        "outputRectifier",
        "control",
    ], roles

    # Inverter: half-bridge pair, bus split + balancing, series tank.
    inv_names = {
        c["name"]
        for c in tas["topology"]["stages"][0]["circuit"]["components"]
        if not c["name"].startswith("P_")
    }
    assert inv_names == {
        "Q_HI",
        "Q_LO",
        "C_bus_hi",
        "C_bus_lo",
        "R_bal_hi",
        "R_bal_lo",
        "C_r",
        "L_r",
    }, inv_names

    # Single transformer, plain (non-CT) secondary.
    t1 = tas["topology"]["stages"][1]["circuit"]["components"][0]
    assert t1["name"] == "T1"

    # Output rectifier: full diode bridge + Cout — no choke (SRC).
    rect_names = {
        c["name"]
        for c in tas["topology"]["stages"][2]["circuit"]["components"]
        if not c["name"].startswith("P_")
    }
    assert rect_names == {"D_h1_0", "D_h2_0", "D_l1_0", "D_l2_0", "C_out0"}, rect_names

    # Controller drives both bridge MOSFETs.
    drives = {d["component"] for d in tas["topology"]["stages"][3]["drives"]}
    assert drives == {"Q_HI", "Q_LO"}, drives
