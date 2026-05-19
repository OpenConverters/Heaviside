"""End-to-end regression test for MKF→TAS Weinberg decomposer.

Weinberg V1 (classic push-pull primary + CT-FW diode rectifier) is the
first stencil with TWO coupled-magnetic systems (input coupled inductor
L1 with two windings + main 4-winding push-pull transformer T1) and
the first that binds an extras-role magnetic on the INPUT side
(``inputCoupledInductor``).

TAS-only golden — bridge MKF decks emit non-deterministic uninitialised
memory in testbench scaffolding (``Vpwm`` PULSE / ``Lmag`` header), so
the netlist isn't byte-stable. Structural validation lives in
``test_weinberg_spice_realset``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from heaviside.decomposer import decompose_from_spec

GOLDEN_DIR = Path(__file__).parent / "golden"
TAS_GOLDEN = GOLDEN_DIR / "wbg_48to150_5A.tas.json"

SPEC: dict[str, object] = {
    "inputVoltage": {"nominal": 48.0},
    "diodeVoltageDrop": 0.7,
    "currentRippleRatio": 0.30,
    "efficiency": 0.85,
    "maximumSwitchCurrent": 50.0,
    "operatingPoints": [
        {
            "outputVoltages": [150.0],
            "outputCurrents": [5.0],
            "switchingFrequency": 50000.0,
            "ambientTemperature": 25.0,
        }
    ],
}
MAGNETIZING_INDUCTANCE = 100e-6
TURNS_RATIOS = [1.0 / 3.0]


def _maybe_update(path: Path, content: str) -> None:
    if os.environ.get("HEAVISIDE_UPDATE_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def test_weinberg_decompose_matches_golden() -> None:
    _netlist, tas = decompose_from_spec(
        "weinberg",
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


def test_weinberg_spice_realset() -> None:
    from heaviside.decomposer.spice_parser import parse_spice
    from heaviside.decomposer.stencils import _WEINBERG_REAL_KINDS

    netlist, _ = decompose_from_spec(
        "weinberg",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
        bridge_simulation_mode="switch",
    )
    deck = parse_spice(netlist)
    refdeses = {el.refdes for el in deck.elements}
    for r in _WEINBERG_REAL_KINDS:
        assert r in refdeses, f"MKF Weinberg deck missing expected refdes {r!r}"


def test_weinberg_tas_shape() -> None:
    _, tas = decompose_from_spec(
        "weinberg",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
        bridge_simulation_mode="switch",
    )
    roles = [s["role"] for s in tas["topology"]["stages"]]
    assert roles == [
        "inputFilter",
        "switchingCell",
        "isolation",
        "outputRectifier",
        "control",
    ], roles

    input_names = {c["name"] for c in tas["topology"]["stages"][0]["circuit"]["components"]}
    assert input_names == {"L1"}, input_names

    sc_names = {c["name"] for c in tas["topology"]["stages"][1]["circuit"]["components"]}
    assert sc_names == {"Q1", "Q2"}, sc_names

    iso_names = {c["name"] for c in tas["topology"]["stages"][2]["circuit"]["components"]}
    assert iso_names == {"T1"}, iso_names

    rect_names = {c["name"] for c in tas["topology"]["stages"][3]["circuit"]["components"]}
    assert rect_names == {"D1", "D2", "C_out0"}, rect_names

    drives = {d["component"] for d in tas["topology"]["stages"][4]["drives"]}
    assert drives == {"Q1", "Q2"}, drives

    # Vin enters at L1.a.1 and L1.b.1 (NOT at any switch drain).
    ports = {p["name"]: p for p in tas["topology"]["interStageCircuit"]}
    vin_eps = {(e["component"], e["pin"]) for e in ports["Vin"]["endpoints"]}
    assert vin_eps == {("L1", "a.1"), ("L1", "b.1")}, vin_eps
