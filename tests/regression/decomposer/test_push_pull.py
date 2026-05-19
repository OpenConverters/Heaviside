"""End-to-end regression test for MKF→TAS push-pull decomposer.

Push-pull is the first stencil where the input bus (Vin) does NOT
terminate on a switch drain — it lands on the *center tap* of the
primary winding. Q1 and Q2 are both low-side switches that pull each
end of the primary down to ground alternately.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from heaviside.decomposer import decompose_from_spec

GOLDEN_DIR = Path(__file__).parent / "golden"
SPICE_GOLDEN = GOLDEN_DIR / "pushpull_48to5_5A.spice"
TAS_GOLDEN = GOLDEN_DIR / "pushpull_48to5_5A.tas.json"

# 48 → 5 V / 5 A push-pull at 200 kHz, 4:1 step-down.
# MKF push-pull duty solver rejects Vout near 12V at these specs — keep
# Vout low so D per switch stays well below 0.5.
SPEC: dict[str, object] = {
    "inputVoltage": {"nominal": 48.0},
    "diodeVoltageDrop": 0.7,
    "maximumSwitchCurrent": 20.0,
    "currentRippleRatio": 0.4,
    "efficiency": 0.95,
    "desiredInductance": 10e-6,
    "operatingPoints": [
        {
            "switchingFrequency": 200000.0,
            "ambientTemperature": 25.0,
            "outputVoltages": [5.0],
            "outputCurrents": [5.0],
        }
    ],
}
MAGNETIZING_INDUCTANCE = 1e-3
TURNS_RATIOS = [4.0]


def _maybe_update(path: Path, content: str) -> None:
    if os.environ.get("HEAVISIDE_UPDATE_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def test_pushpull_decompose_matches_golden() -> None:
    """TAS golden comparison only.

    MKF's push-pull SPICE emitter writes non-deterministic uninitialised
    memory into the ``Lmag=…, N=…`` header comment and the ``Vpwm2``
    PULSE pulse-duration field (different value per process). Both live
    in testbench scaffolding that the stencil discards, so the produced
    TAS is fully deterministic — we lock that down here. The SPICE deck
    is still inspected structurally below (see ``test_pushpull_tas_shape``
    and ``test_pushpull_spice_realset``), just not byte-compared.
    """
    _netlist, tas = decompose_from_spec(
        "push_pull",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
    )
    tas_json = json.dumps(tas, indent=2) + "\n"

    _maybe_update(TAS_GOLDEN, tas_json)

    if not TAS_GOLDEN.exists():
        pytest.fail(
            "Golden fixtures missing. Run with HEAVISIDE_UPDATE_GOLDENS=1 to create."
        )

    assert tas_json == TAS_GOLDEN.read_text()


def test_pushpull_spice_realset() -> None:
    """The MKF deck must contain exactly the refdeses the stencil expects.

    This catches drift in MKF's emitter shape without requiring byte
    stability of the comment/scaffolding sections.
    """
    from heaviside.decomposer.spice_parser import parse_spice
    from heaviside.decomposer.stencils import _PUSH_PULL_REAL_KINDS

    netlist, _ = decompose_from_spec(
        "push_pull",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
    )
    deck = parse_spice(netlist)
    refdeses = {el.refdes for el in deck.elements}
    for r in _PUSH_PULL_REAL_KINDS:
        assert r in refdeses, f"MKF push-pull deck missing expected refdes {r!r}"


def test_pushpull_tas_shape() -> None:
    _, tas = decompose_from_spec(
        "push_pull",
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
    assert sw_names == {"Q1", "Q2"}, sw_names

    t1 = tas["topology"]["stages"][1]["circuit"]["components"][0]
    assert t1["name"] == "T1"
    assert set(t1["pins"]) == {
        "pri_top.1", "pri_top.2",
        "pri_bot.1", "pri_bot.2",
        "sec_top.1", "sec_top.2",
        "sec_bot.1", "sec_bot.2",
    }, t1["pins"]

    rect_names = {c["name"] for c in tas["topology"]["stages"][2]["circuit"]["components"]}
    assert rect_names == {"D1", "D2", "L_out0", "C_out0"}, rect_names

    ports = {p["name"]: p for p in tas["topology"]["interStageCircuit"]}
    assert set(ports) == {
        "Vin",
        "sw_top_node",
        "sw_bot_node",
        "sec_top_node",
        "sec_bot_node",
        "Vout0",
        "GND",
        "Q1_gate",
        "Q2_gate",
    }, set(ports)

    # Vin lands on BOTH primary center-tap pins, NOT on any switch drain.
    vin_eps = {(e["component"], e["pin"]) for e in ports["Vin"]["endpoints"]}
    assert vin_eps == {
        ("T1", "pri_top.2"),
        ("T1", "pri_bot.1"),
    }, vin_eps

    # Each switch drain bridges to the outer end of its primary half-winding.
    swt = {(e["component"], e["pin"]) for e in ports["sw_top_node"]["endpoints"]}
    assert swt == {("Q1", "D"), ("T1", "pri_top.1")}, swt
    swb = {(e["component"], e["pin"]) for e in ports["sw_bot_node"]["endpoints"]}
    assert swb == {("Q2", "D"), ("T1", "pri_bot.2")}, swb

    # Both secondary center-tap pins must land on GND.
    gnd = {(e["component"], e["pin"]) for e in ports["GND"]["endpoints"]}
    assert ("T1", "sec_top.2") in gnd and ("T1", "sec_bot.1") in gnd
    assert ("Q1", "S") in gnd and ("Q2", "S") in gnd

    # Controller drives both Q1 and Q2.
    drives = {d["component"] for d in tas["topology"]["stages"][3]["drives"]}
    assert drives == {"Q1", "Q2"}, drives
