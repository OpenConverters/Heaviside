"""End-to-end regression test for MKF→TAS single-switch forward decomposer.

MKF emits primary excitation only (S1 + Lpri + Ldemag + Kpri_demag +
Ddemag). The stencil augments this with a synthetic output stage —
3rd winding ``sec0`` on T1, forward + freewheel diodes, output choke,
output cap, and a ``Vout0`` external port — so the resulting TAS is a
complete simulatable converter that round-trips through SPICE↔TAS.
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
TURNS_RATIOS = [2.0]


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
    roles = [s["role"] for s in tas["stages"]]
    assert roles == [
        "switchingCell", "isolation", "outputRectifier", "control",
    ], roles

    sw_names = {c["name"] for c in tas["stages"][0]["circuit"]["components"]}
    assert sw_names == {"Q1", "D_demag"}, sw_names

    # T1 is 3-winding: pri (excitation) + demag (reset) + sec0 (forward).
    t1 = tas["stages"][1]["circuit"]["components"][0]
    assert t1["name"] == "T1"
    assert set(t1["pins"]) == {
        "pri.1", "pri.2", "demag.1", "demag.2", "sec0.1", "sec0.2",
    }, t1["pins"]

    # Injected output stage: 2 diodes (D_fwd, D_fw) + L_out0 + C_out0.
    rect_names = {
        c["name"] for c in tas["stages"][2]["circuit"]["components"]
    }
    assert rect_names == {"D_fwd", "D_fw", "L_out0", "C_out0"}, rect_names

    ports = {p["name"]: p for p in tas["interStageCircuit"]}
    assert set(ports) == {
        "Vin", "switch_node", "demag_node", "sec0_node",
        "Vout0", "GND", "Q1_gate",
    }, set(ports)

    # Vin must reach both Q1.D and D_demag.K (demag reset returns to Vin).
    vin_eps = {(e["component"], e["pin"]) for e in ports["Vin"]["endpoints"]}
    assert vin_eps == {("Q1", "D"), ("D_demag", "K")}, vin_eps

    # Vout0 is the LC filter port.
    vout_eps = {(e["component"], e["pin"]) for e in ports["Vout0"]["endpoints"]}
    assert vout_eps == {("L_out0", "2"), ("C_out0", "1")}, vout_eps
