"""End-to-end regression test for the MKF→TAS buck decomposer.

The test pins:

1. The full SPICE netlist MKF emits for a fixed buck spec
   (``golden/buck_48to12_5A.spice``).
2. The TAS topology dict our stencil derives from that netlist
   (``golden/buck_48to12_5A.tas.json``).

If either drifts, the test fails. To regenerate after an intentional
change, run with ``HEAVISIDE_UPDATE_GOLDENS=1``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from heaviside.decomposer import decompose_from_spec

GOLDEN_DIR = Path(__file__).parent / "golden"
SPICE_GOLDEN = GOLDEN_DIR / "buck_48to12_5A.spice"
TAS_GOLDEN = GOLDEN_DIR / "buck_48to12_5A.tas.json"

BUCK_SPEC: dict[str, object] = {
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
MAGNETIZING_INDUCTANCE = 22e-6


def _maybe_update(path: Path, content: str) -> None:
    if os.environ.get("HEAVISIDE_UPDATE_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def test_buck_decompose_matches_golden() -> None:
    netlist, tas = decompose_from_spec(
        "buck",
        BUCK_SPEC,
        turns_ratios=[],
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
    )
    tas_json = json.dumps(tas, indent=2) + "\n"

    _maybe_update(SPICE_GOLDEN, netlist)
    _maybe_update(TAS_GOLDEN, tas_json)

    if not SPICE_GOLDEN.exists() or not TAS_GOLDEN.exists():
        pytest.fail(
            "Golden fixtures missing. Run "
            "`HEAVISIDE_UPDATE_GOLDENS=1 pytest tests/regression/decomposer/` "
            "to create them, then commit."
        )

    assert netlist == SPICE_GOLDEN.read_text(), (
        "MKF spice deck for buck has drifted from the golden fixture. "
        "Inspect the diff; if intentional, regenerate with HEAVISIDE_UPDATE_GOLDENS=1."
    )
    assert tas_json == TAS_GOLDEN.read_text(), (
        "Decomposed TAS for buck has drifted from the golden fixture. "
        "Inspect the diff; if intentional, regenerate with HEAVISIDE_UPDATE_GOLDENS=1."
    )


def test_buck_tas_round_trip_shape() -> None:
    """The decomposed TAS must satisfy the structural invariants we rely on:

    * Two stages: ``switchingCell`` then ``control``.
    * Switching cell carries Q1/D1/L1/C_out.
    * External Vin/Vout ports in ``interStageCircuit``.
    """
    _, tas = decompose_from_spec(
        "buck",
        BUCK_SPEC,
        turns_ratios=[],
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
    )
    roles = [s["role"] for s in tas["stages"]]
    assert roles == ["switchingCell", "control"], roles

    sc = tas["stages"][0]
    names = {c["name"] for c in sc["circuit"]["components"]}
    assert names == {"Q1", "D1", "L1", "C_out"}, names

    port_names = {p["name"] for p in tas["interStageCircuit"]}
    # Power-flow wires + GND (all grounded pins) + per-switch gate net.
    assert port_names == {"Vin", "Vout", "GND", "Q1_gate"}, port_names
