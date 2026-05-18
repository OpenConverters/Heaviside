"""End-to-end regression test for MKF→TAS single-switch forward decomposer.

MKF emits primary excitation only for this topology — no output rectifier
stage appears in the deck. The decomposer stays faithful to MKF and
produces a 3-stage TAS (switchingCell + isolation + control) with no
Vout port. Downstream BOM-augmentation is required to complete it.
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
    # No outputRectifier — primary excitation only.
    assert roles == ["switchingCell", "isolation", "control"], roles

    sw_names = {c["name"] for c in tas["stages"][0]["circuit"]["components"]}
    assert sw_names == {"Q1", "D_demag"}, sw_names

    t1 = tas["stages"][1]["circuit"]["components"][0]
    assert t1["name"] == "T1"
    assert set(t1["pins"]) == {"pri.1", "pri.2", "demag.1", "demag.2"}, t1["pins"]

    ports = {p["name"]: p for p in tas["interStageCircuit"]}
    # No Vout port — only Vin + 2 internal stage-bridging wires.
    assert set(ports) == {"Vin", "switch_node", "demag_node",
                          "GND", "Q1_gate"}, set(ports)

    # Vin must reach both Q1.D and D_demag.K (demag reset returns to Vin).
    vin_eps = {(e["component"], e["pin"]) for e in ports["Vin"]["endpoints"]}
    assert vin_eps == {("Q1", "D"), ("D_demag", "K")}, vin_eps
