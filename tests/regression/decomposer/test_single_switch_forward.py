"""End-to-end regression test for MKF→TAS single-switch forward decomposer.

MKF emits a complete forward converter: S1 + 3-winding-class transformer
(Lpri + Ldemag reset winding + one Lsec{i} per output rail) + Kpri_demag /
Kpri_sec{i} / Kdemag_sec{i} couplings + demag reset diode (Ddemag) + one
forward-rectifier output stage per secondary (Dfwd{i} + Dfw{i} + Lout{i} +
Cout{i}). The stencil maps each secondary into a ``Vout{i}`` rail with the
same forward-rectifier output stage as the two-switch / active-clamp forward.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from heaviside.decomposer import decompose_from_spec

GOLDEN_DIR = Path(__file__).parent / "golden"
SPICE_GOLDEN = GOLDEN_DIR / "ssforward_48to12_5A.spice"
TAS_GOLDEN = GOLDEN_DIR / "ssforward_48to12_5A.tas.json"

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
# turnsRatios[0] = demag winding (1:1), turnsRatios[1] = sec0 (N_pri/N_sec0).
# n_sec0 = 1.6 keeps the regulated-rail duty under the half-period reset limit.
TURNS_RATIOS = [1.0, 1.6]


def _maybe_update(path: Path, content: str) -> None:
    if os.environ.get("HEAVISIDE_UPDATE_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def test_ssforward_decompose_matches_golden() -> None:
    netlist, tas = decompose_from_spec(
        "single_switch_forward",
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


def test_ssforward_tas_round_trip_shape() -> None:
    _, tas = decompose_from_spec(
        "single_switch_forward",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
    )
    roles = [s["role"] for s in tas["topology"]["stages"]]
    assert roles == [
        "switchingCell", "isolation", "outputRectifier", "control",
    ], roles

    sw_names = {c["name"] for c in tas["topology"]["stages"][0]["circuit"]["components"] if not c["name"].startswith("P_")}
    assert sw_names == {"Q1", "D_demag"}, sw_names

    # T1 is 3-winding: pri (excitation) + demag (reset) + sec0 (forward).
    t1 = tas["topology"]["stages"][1]["circuit"]["components"][0]
    assert t1["name"] == "T1"
    t1_pins = {ep["pin"] for w in tas["topology"]["interStageCircuit"] for ep in w.get("endpoints", []) if ep["component"] == "T1"}
    assert t1_pins == {
        "pri.1", "pri.2", "demag.1", "demag.2", "sec0.1", "sec0.2",
    }, t1_pins

    # Output stage (rail 0): 2 diodes (D_fwd0, D_fw0) + L_out0 + C_out0.
    rect_names = {
        c["name"] for c in tas["topology"]["stages"][2]["circuit"]["components"]
        if not c["name"].startswith("P_")
    }
    assert rect_names == {"D_fwd0", "D_fw0", "L_out0", "C_out0"}, rect_names

    ports = {p["name"]: p for p in tas["topology"]["interStageCircuit"]}
    assert set(ports) == {
        "Vin", "switch_node", "demag_node", "sec0_node",
        "Vout0", "GND",
    }, set(ports)

    # Vin must reach both Q1.D and D_demag.K (demag reset returns to Vin).
    vin_eps = {(e["component"], e["pin"]) for e in ports["Vin"]["endpoints"] if not e["component"].startswith("P_")}
    assert vin_eps == {("Q1", "D"), ("D_demag", "K")}, vin_eps

    # Vout0 is the LC filter port.
    vout_eps = {(e["component"], e["pin"]) for e in ports["Vout0"]["endpoints"] if not e["component"].startswith("P_")}
    assert vout_eps == {("L_out0", "2"), ("C_out0", "1")}, vout_eps
