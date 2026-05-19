"""End-to-end regression test for MKF→TAS phase-shifted full bridge decomposer.

PSFB is the first 4-switch bridge stencil. MKF emits both legs (A=SA/SB,
C=SC/SD) with one synthetic body diode per switch (DA/DB/DC/DD — dropped
as testbench), a resonant/leakage series inductor (L_series → L_r), and a
center-tapped rectifier on the secondary. The 1µΩ "center-tap stub"
``Rct_o1`` is dropped (testbench scaffolding).

Requires ``bridge_simulation_mode="switch"``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from heaviside.decomposer import decompose_from_spec

GOLDEN_DIR = Path(__file__).parent / "golden"
TAS_GOLDEN = GOLDEN_DIR / "psfb_400to12_5A.tas.json"

# 400 → 12 V / 5 A PSFB, 100 kHz, 16:1 step-down, centerTapped rectifier.
SPEC: dict[str, object] = {
    "inputVoltage": {"nominal": 400.0},
    "diodeVoltageDrop": 0.7,
    "maximumSwitchCurrent": 20.0,
    "currentRippleRatio": 0.4,
    "efficiency": 0.95,
    "desiredInductance": 10e-6,
    "rectifierType": "centerTapped",
    "operatingPoints": [
        {
            "switchingFrequency": 100000.0,
            "ambientTemperature": 25.0,
            "outputVoltages": [12.0],
            "outputCurrents": [5.0],
            "phaseShift": 126.0,
        }
    ],
}
MAGNETIZING_INDUCTANCE = 1e-3
TURNS_RATIOS = [16.0]


def _maybe_update(path: Path, content: str) -> None:
    if os.environ.get("HEAVISIDE_UPDATE_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def test_psfb_decompose_matches_golden() -> None:
    """TAS golden comparison only — MKF bridge decks frequently contain
    non-deterministic uninitialised memory in testbench scaffolding (see
    push_pull). The stencil discards all such scaffolding, so the TAS
    output is fully deterministic."""
    _netlist, tas = decompose_from_spec(
        "phase_shifted_full_bridge",
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


def test_psfb_spice_realset() -> None:
    """The MKF deck must contain exactly the refdeses the stencil expects."""
    from heaviside.decomposer.spice_parser import parse_spice
    from heaviside.decomposer.stencils import _PSFB_REAL_KINDS

    netlist, _ = decompose_from_spec(
        "phase_shifted_full_bridge",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
        bridge_simulation_mode="switch",
    )
    deck = parse_spice(netlist)
    refdeses = {el.refdes for el in deck.elements}
    for r in _PSFB_REAL_KINDS:
        assert r in refdeses, f"MKF PSFB deck missing expected refdes {r!r}"


def test_psfb_tas_shape() -> None:
    _, tas = decompose_from_spec(
        "phase_shifted_full_bridge",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
        bridge_simulation_mode="switch",
    )
    roles = [s["role"] for s in tas["stages"]]
    assert roles == ["inverter", "isolation", "outputRectifier", "control"], roles

    inv_names = {c["name"] for c in tas["stages"][0]["circuit"]["components"]}
    assert inv_names == {"Q_A", "Q_B", "Q_C", "Q_D", "L_r"}, inv_names

    t1 = tas["stages"][1]["circuit"]["components"][0]
    assert t1["name"] == "T1"
    assert set(t1["pins"]) == {"pri.1", "pri.2", "sec0.1", "sec0.2"}, t1["pins"]

    rect_names = {c["name"] for c in tas["stages"][2]["circuit"]["components"]}
    assert rect_names == {"D1", "D2", "L_out0", "C_out0"}, rect_names

    ports = {p["name"]: p for p in tas["interStageCircuit"]}
    assert {
        "Vin", "pri_top", "mid_C", "sec_a", "sec_b", "Vout0",
        "GND", "Q_A_gate", "Q_B_gate", "Q_C_gate", "Q_D_gate",
    } <= set(ports), set(ports)

    # Vin reaches both leg high-sides (Q_A.D, Q_C.D), no others.
    vin_eps = {(e["component"], e["pin"]) for e in ports["Vin"]["endpoints"]}
    assert vin_eps == {("Q_A", "D"), ("Q_C", "D")}, vin_eps

    drives = {d["component"] for d in tas["stages"][3]["drives"]}
    assert drives == {"Q_A", "Q_B", "Q_C", "Q_D"}, drives
