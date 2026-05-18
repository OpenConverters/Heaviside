"""End-to-end regression test for the MKF→TAS Zeta decomposer.

Pins both the MKF spice deck and the decomposed TAS dict to golden files;
regenerate with ``HEAVISIDE_UPDATE_GOLDENS=1``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from heaviside.decomposer import decompose_from_spec

GOLDEN_DIR = Path(__file__).parent / "golden"
SPICE_GOLDEN = GOLDEN_DIR / "zeta_48to12_5A.spice"
TAS_GOLDEN = GOLDEN_DIR / "zeta_48to12_5A.tas.json"

ZETA_SPEC: dict[str, object] = {
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


def test_zeta_decompose_matches_golden() -> None:
    netlist, tas = decompose_from_spec(
        "zeta",
        ZETA_SPEC,
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
        "MKF spice deck for Zeta has drifted from the golden fixture."
    )
    assert tas_json == TAS_GOLDEN.read_text(), (
        "Decomposed TAS for Zeta has drifted from the golden fixture."
    )


def test_zeta_tas_round_trip_shape() -> None:
    """Structural invariants: Zeta uses a high-side switch, so the Vin
    port lands on Q1.D (not L1, unlike cuk/sepic). Two internal nodes
    node_SW and node_X; Vout exits at L2.2 + C_out.1."""
    _, tas = decompose_from_spec(
        "zeta",
        ZETA_SPEC,
        turns_ratios=[],
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
    )
    roles = [s["role"] for s in tas["stages"]]
    assert roles == ["switchingCell", "control"], roles

    sc = tas["stages"][0]
    names = {c["name"] for c in sc["circuit"]["components"]}
    assert names == {"Q1", "D1", "L1", "L2", "C_flying", "C_out"}, names

    conn_names = {c["name"] for c in sc["circuit"]["connections"]}
    assert conn_names == {"node_SW", "node_X"}, conn_names

    ports = {p["name"]: p for p in tas["interStageCircuit"]}
    assert set(ports) == {"Vin", "Vout", "GND", "Q1_gate"}
    # Zeta signature: Vin enters at Q1.D (high-side switch)
    vin_eps = {(e["component"], e["pin"]) for e in ports["Vin"]["endpoints"]}
    assert vin_eps == {("Q1", "D")}, vin_eps
    vout_eps = {(e["component"], e["pin"]) for e in ports["Vout"]["endpoints"]}
    assert vout_eps == {("L2", "2"), ("C_out", "1")}, vout_eps
