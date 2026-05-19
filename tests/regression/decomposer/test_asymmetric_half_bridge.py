"""End-to-end regression test for MKF→TAS asymmetric half-bridge decomposer.

AHB has a half-bridge inverter (Q1/Q2) with DC blocking cap C_b and
series leakage inductor L_lk in the primary loop, and a full-bridge
rectifier (D1..D4) on the secondary. Requires ``bridge_simulation_mode=
"switch"`` and ``rectifierType="fullBridge"``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from heaviside.decomposer import decompose_from_spec

GOLDEN_DIR = Path(__file__).parent / "golden"
TAS_GOLDEN = GOLDEN_DIR / "ahb_400to12_5A.tas.json"

SPEC: dict[str, object] = {
    "inputVoltage": {"nominal": 400.0},
    "diodeVoltageDrop": 0.7,
    "maximumSwitchCurrent": 20.0,
    "currentRippleRatio": 0.4,
    "efficiency": 0.95,
    "desiredInductance": 10e-6,
    "rectifierType": "fullBridge",
    "operatingPoints": [
        {
            "switchingFrequency": 100000.0,
            "ambientTemperature": 25.0,
            "outputVoltages": [12.0],
            "outputCurrents": [5.0],
            "dutyCycle": 0.5,
        }
    ],
}
MAGNETIZING_INDUCTANCE = 1e-3
TURNS_RATIOS = [16.0]


def _maybe_update(path: Path, content: str) -> None:
    if os.environ.get("HEAVISIDE_UPDATE_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def test_ahb_decompose_matches_golden() -> None:
    _netlist, tas = decompose_from_spec(
        "asymmetric_half_bridge",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
        bridge_simulation_mode="switch",
    )
    tas_json = json.dumps(tas, indent=2) + "\n"

    _maybe_update(TAS_GOLDEN, tas_json)

    if not TAS_GOLDEN.exists():
        pytest.fail(
            "Golden fixtures missing. Run with HEAVISIDE_UPDATE_GOLDENS=1 to create."
        )

    assert tas_json == TAS_GOLDEN.read_text()


def test_ahb_spice_realset() -> None:
    from heaviside.decomposer.spice_parser import parse_spice
    from heaviside.decomposer.stencils import _AHB_REAL_KINDS

    netlist, _ = decompose_from_spec(
        "asymmetric_half_bridge",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
        bridge_simulation_mode="switch",
    )
    deck = parse_spice(netlist)
    refdeses = {el.refdes for el in deck.elements}
    for r in _AHB_REAL_KINDS:
        assert r in refdeses, f"MKF AHB deck missing expected refdes {r!r}"


def test_ahb_tas_shape() -> None:
    _, tas = decompose_from_spec(
        "asymmetric_half_bridge",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
        bridge_simulation_mode="switch",
    )
    roles = [s["role"] for s in tas["topology"]["stages"]]
    assert roles == ["inverter", "isolation", "outputRectifier", "control"], roles

    inv_names = {c["name"] for c in tas["topology"]["stages"][0]["circuit"]["components"]}
    assert inv_names == {"Q1", "Q2", "C_b"}, inv_names

    rect_names = {c["name"] for c in tas["topology"]["stages"][2]["circuit"]["components"]}
    assert rect_names == {"D1", "D2", "D3", "D4", "L_out0", "C_out0"}, rect_names

    drives = {d["component"] for d in tas["topology"]["stages"][3]["drives"]}
    assert drives == {"Q1", "Q2"}, drives

    # Vin must reach Q1.D and C_b.1 (no others).
    ports = {p["name"]: p for p in tas["topology"]["interStageCircuit"]}
    vin_eps = {(e["component"], e["pin"]) for e in ports["Vin"]["endpoints"]}
    assert vin_eps == {("Q1", "D"), ("C_b", "1")}, vin_eps
