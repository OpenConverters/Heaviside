"""End-to-end regression test for MKF→TAS LLC decomposer.

LLC is the first *bridge* topology in the regression suite. It requires
``bridge_simulation_mode="switch"`` so MKF emits real SHI/SLO switches
(and DHI/DLO body diodes, which we drop as testbench since real MOSFETs
have integrated body diodes).

Distinguishing features vs. the forward family:
  * ``inverter`` role (not ``switchingCell``) — per Maksimović, the
    resonant tank Cr+Lr belongs to the inverter stage that emits hfAc.
  * Half-bridge bus split (Cbus_hi, Cbus_lo, Rbal_hi, Rbal_lo) is real
    BOM inside the inverter stage.
  * Center-tapped secondary (T1.sec1 + T1.sec2) — the CT node is GND
    and lives in interStage as ``sec_ct``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from heaviside.decomposer import decompose_from_spec

GOLDEN_DIR = Path(__file__).parent / "golden"
SPICE_GOLDEN = GOLDEN_DIR / "llc_48to12_5A.spice"
TAS_GOLDEN = GOLDEN_DIR / "llc_48to12_5A.tas.json"

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


def _maybe_update(path: Path, content: str) -> None:
    if os.environ.get("HEAVISIDE_UPDATE_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def test_llc_decompose_matches_golden() -> None:
    netlist, tas = decompose_from_spec(
        "llc",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
        bridge_simulation_mode=BRIDGE_MODE,
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


def test_llc_tas_round_trip_shape() -> None:
    _, tas = decompose_from_spec(
        "llc",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
        bridge_simulation_mode=BRIDGE_MODE,
    )

    roles = [s["role"] for s in tas["topology"]["stages"]]
    assert roles == [
        "inverter",
        "isolation",
        "outputRectifier",
        "control",
    ], roles

    # Inverter has both half-bridge MOSFETs, bus split + balancing, and
    # the resonant tank (Cr + Lr).
    inv_names = {c["name"] for c in tas["topology"]["stages"][0]["circuit"]["components"] if not c["name"].startswith("P_")}
    assert inv_names == {
        "Q_HI", "Q_LO",
        "C_bus_hi", "C_bus_lo",
        "R_bal_hi", "R_bal_lo",
        "C_r", "L_r",
    }, inv_names

    # T1 has three windings (primary + CT secondary modelled as two
    # half-windings sec1/sec2).
    t1 = tas["topology"]["stages"][1]["circuit"]["components"][0]
    assert t1["name"] == "T1"
    t1_pins = {ep["pin"] for w in tas["topology"]["interStageCircuit"] for ep in w.get("endpoints", []) if ep["component"] == "T1"}
    assert t1_pins == {
        "pri.1", "pri.2",
        "sec1.1", "sec1.2",
        "sec2.1", "sec2.2",
    }, t1_pins

    # Output rectifier: just the CT pair + Cout. No output choke (LLC).
    rect_names = {c["name"] for c in tas["topology"]["stages"][2]["circuit"]["components"] if not c["name"].startswith("P_")}
    assert rect_names == {"D1", "D2", "C_out0"}, rect_names

    ports = {p["name"]: p for p in tas["topology"]["interStageCircuit"]}
    assert set(ports) == {
        "Vin", "mid_point", "pri_top",
        "sec_top", "sec_bot", "sec_ct",
        "Vout0",
        "GND",
    }, set(ports)

    # mid_point must touch both bus caps, both balancing resistors,
    # AND T1.pri.2 (primary return through the capacitive divider).
    mid_eps = {(e["component"], e["pin"]) for e in ports["mid_point"]["endpoints"] if not e["component"].startswith("P_")}
    assert mid_eps == {
        ("C_bus_hi", "2"), ("C_bus_lo", "1"),
        ("R_bal_hi", "2"), ("R_bal_lo", "1"),
        ("T1", "pri.2"),
    }, mid_eps

    # sec_ct must short T1.sec1.2 and T1.sec2.1 (CT node) to C_out0.2.
    ct_eps = {(e["component"], e["pin"]) for e in ports["sec_ct"]["endpoints"] if not e["component"].startswith("P_")}
    assert ct_eps == {
        ("T1", "sec1.2"), ("T1", "sec2.1"),
        ("C_out0", "2"),
    }, ct_eps

    # Controller drives both bridge MOSFETs.
    drives = {d["component"] for d in tas["topology"]["stages"][3]["drives"]}
    assert drives == {"Q_HI", "Q_LO"}, drives
