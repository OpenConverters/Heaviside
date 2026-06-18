"""End-to-end regression test for MKF→TAS CLLLC decomposer.

CLLLC is the largest bidirectional symmetric resonant converter MKF
emits: TWO full bridges (HV + LV = 8 real MOSFETs), TWO resonant tanks
(Cr1+Lr1 on HV, Cr2+Lr2 on LV), and the main transformer T1. The LV
bridge IS the synchronous rectifier; phase shift controls power-flow
direction.

TAS-only golden — bridge MKF decks emit non-deterministic uninitialised
memory in testbench scaffolding (Vpwm PULSE / header). Structural shape
is validated in ``test_clllc_spice_realset``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from heaviside.decomposer import decompose_from_spec

GOLDEN_DIR = Path(__file__).parent / "golden"
TAS_GOLDEN = GOLDEN_DIR / "clllc_400to12_5A.tas.json"

# 400 V HV bus → 12 V / 5 A LV. fr1≈173 kHz; pick fsw=200 kHz (above-resonance
# operation, ZVS region for CLLLC). Turns ratio n=8.333 (400/48 = 8.33 around
# the resonance, with the LV bus stepped down further to 12 V by phase shift).
# build_tas_inputs needs ``inputVoltage`` (HV bus value); MKF ignores it for
# CLLLC and uses ``highVoltageBusVoltage`` instead.
SPEC: dict[str, object] = {
    "inputVoltage": {"nominal": 400.0, "minimum": 380.0, "maximum": 420.0},
    "highVoltageBusVoltage": {"nominal": 400.0},
    "lowVoltageBusVoltage": {"nominal": 48.0},
    "powerFlow": "forward",
    "efficiency": 0.96,
    "currentRippleRatio": 0.4,
    "diodeVoltageDrop": 0.7,
    "maximumSwitchCurrent": 20.0,
    "desiredInductance": 22e-6,
    "desiredMagnetizingInductance": 1e-3,
    "minSwitchingFrequency": 100_000.0,
    "maxSwitchingFrequency": 300_000.0,
    "operatingPoints": [
        {
            "outputVoltages": [12.0],
            "outputCurrents": [5.0],
            "switchingFrequency": 200_000.0,
            "ambientTemperature": 25.0,
        }
    ],
}
MAGNETIZING_INDUCTANCE = 1e-3
TURNS_RATIOS = [8.0, 1.0]  # CLLLC takes [n_pri_sec, n_pri_pri] for the symmetric tank


def _maybe_update(path: Path, content: str) -> None:
    if os.environ.get("HEAVISIDE_UPDATE_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def test_clllc_decompose_matches_golden() -> None:
    _netlist, tas = decompose_from_spec(
        "clllc",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
        bridge_simulation_mode="switch",
    )
    tas_json = json.dumps(tas, indent=2) + "\n"

    _maybe_update(TAS_GOLDEN, tas_json)

    if not TAS_GOLDEN.exists():
        pytest.fail("Golden fixtures missing. Run with HEAVISIDE_UPDATE_GOLDENS=1 to create.")

    assert tas_json == TAS_GOLDEN.read_text()


def test_clllc_spice_realset() -> None:
    from heaviside.decomposer.spice_parser import parse_spice
    from heaviside.decomposer.stencils import _CLLLC_REAL_KINDS

    netlist, _ = decompose_from_spec(
        "clllc",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
        bridge_simulation_mode="switch",
    )
    deck = parse_spice(netlist)
    refdeses = {el.refdes for el in deck.elements}
    for r in _CLLLC_REAL_KINDS:
        assert r in refdeses, f"MKF CLLLC deck missing expected refdes {r!r}"


def test_clllc_tas_shape() -> None:
    _, tas = decompose_from_spec(
        "clllc",
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

    pri_names = {
        c["name"]
        for c in tas["topology"]["stages"][0]["circuit"]["components"]
        if not c["name"].startswith("P_")
    }
    assert pri_names == {"Q1", "Q2", "Q3", "Q4", "C_bus_HV"}, pri_names

    iso_names = {
        c["name"]
        for c in tas["topology"]["stages"][1]["circuit"]["components"]
        if not c["name"].startswith("P_")
    }
    assert iso_names == {"C_r1", "L_r1", "T1", "L_r2", "C_r2"}, iso_names

    sec_names = {
        c["name"]
        for c in tas["topology"]["stages"][2]["circuit"]["components"]
        if not c["name"].startswith("P_")
    }
    assert sec_names == {"Q5", "Q6", "Q7", "Q8"}, sec_names

    of_names = {
        c["name"]
        for c in tas["topology"]["stages"][3]["circuit"]["components"]
        if not c["name"].startswith("P_")
    }
    assert of_names == {"C_bus_LV"}, of_names

    drives = {d["component"] for d in tas["topology"]["stages"][4]["drives"]}
    assert drives == {"Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8"}, drives

    # v2 endpoints use {stage, port}
    ports = {p["name"]: p for p in tas["topology"]["interStageConnections"]}
    vin_eps = {(e["stage"], e["port"]) for e in ports["Vin"]["endpoints"]}
    assert vin_eps == {("primary_bridge", "in")}, vin_eps


def test_clllc_registry_binding_matches_extras_roles() -> None:
    """Smoke-check: registry entry's extras-role names match the
    PyOM-emitted role names for CLLLC. Both magnetic_binding (Lr1/Lr2)
    and capacitor_binding (Cr1/Cr2) must round-trip through the bridge."""
    from heaviside.topologies import registry

    entry = registry.get("clllc")
    assert set(entry.magnetic_binding) == {"T1", "L_r1", "L_r2"}
    assert entry.magnetic_binding["T1"] is None
    assert entry.magnetic_binding["L_r1"] == "Lr1_HV_seriesInductor"
    assert entry.magnetic_binding["L_r2"] == "Lr2_LV_seriesInductor"

    assert set(entry.capacitor_binding) == {"C_r1", "C_r2"}
    assert entry.capacitor_binding["C_r1"] == "Cr1_HV_resonantCapacitor"
    assert entry.capacitor_binding["C_r2"] == "Cr2_LV_resonantCapacitor"
