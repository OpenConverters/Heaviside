"""End-to-end regression test for MKF→TAS Dual Active Bridge decomposer.

DAB is bidirectional: BOTH primary and secondary are full bridges of
real MOSFETs (4 + 4 = 8 switches). The secondary bridge IS the
rectifier (synchronous), so there are no diodes in the BOM. Phase
shift between primary and secondary controls power flow direction.

TAS-only golden — bridge MKF decks emit non-deterministic uninitialised
memory in testbench scaffolding (Vpwm PULSE / header). Structural
shape is validated in ``test_dab_spice_realset``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from heaviside.decomposer import decompose_from_spec

GOLDEN_DIR = Path(__file__).parent / "golden"
TAS_GOLDEN = GOLDEN_DIR / "dab_800to500_20A.tas.json"

# TI TIDA-010054 reference: V1=800V, V2=500V, P=10kW, Fs=100kHz, N=1.6, L=35µH
SPEC: dict[str, object] = {
    "inputVoltage": {"nominal": 800.0, "minimum": 700.0, "maximum": 800.0},
    "efficiency": 0.97,
    "seriesInductance": 35e-6,
    "useLeakageInductance": False,
    "operatingPoints": [
        {
            "outputVoltages": [500.0],
            "outputCurrents": [20.0],
            "innerPhaseShift3": 23.0,   # outer phase shift, degrees
            "switchingFrequency": 100000.0,
            "ambientTemperature": 25.0,
        }
    ],
}
MAGNETIZING_INDUCTANCE = 1e-3
TURNS_RATIOS = [1.6]


def _maybe_update(path: Path, content: str) -> None:
    if os.environ.get("HEAVISIDE_UPDATE_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def test_dab_decompose_matches_golden() -> None:
    _netlist, tas = decompose_from_spec(
        "dab",
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


def test_dab_spice_realset() -> None:
    from heaviside.decomposer.spice_parser import parse_spice
    from heaviside.decomposer.stencils import _DAB_REAL_KINDS

    netlist, _ = decompose_from_spec(
        "dab",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
        bridge_simulation_mode="switch",
    )
    deck = parse_spice(netlist)
    refdeses = {el.refdes for el in deck.elements}
    for r in _DAB_REAL_KINDS:
        assert r in refdeses, f"MKF DAB deck missing expected refdes {r!r}"


def test_dab_tas_shape() -> None:
    _, tas = decompose_from_spec(
        "dab",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
        bridge_simulation_mode="switch",
    )
    roles = [s["role"] for s in tas["topology"]["stages"]]
    assert roles == [
        "inverter",
        "isolation",
        "outputRectifier",
        "outputFilter",
        "control",
    ], roles

    pri_names = {c["name"] for c in tas["topology"]["stages"][0]["circuit"]["components"]}
    assert pri_names == {"Q1", "Q2", "Q3", "Q4"}, pri_names

    iso_names = {c["name"] for c in tas["topology"]["stages"][1]["circuit"]["components"]}
    assert iso_names == {"L_r", "T1"}, iso_names

    sec_names = {c["name"] for c in tas["topology"]["stages"][2]["circuit"]["components"]}
    assert sec_names == {"Q5", "Q6", "Q7", "Q8"}, sec_names

    of_names = {c["name"] for c in tas["topology"]["stages"][3]["circuit"]["components"]}
    assert of_names == {"C_out0"}, of_names

    drives = {d["component"] for d in tas["topology"]["stages"][4]["drives"]}
    assert drives == {"Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8"}, drives

    # Vin reaches Q1.D and Q3.D (and nothing else).
    ports = {p["name"]: p for p in tas["topology"]["interStageCircuit"]}
    vin_eps = {(e["component"], e["pin"]) for e in ports["Vin"]["endpoints"]}
    assert vin_eps == {("Q1", "D"), ("Q3", "D")}, vin_eps
