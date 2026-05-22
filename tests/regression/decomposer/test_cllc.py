"""End-to-end regression test for MKF→TAS CLLC decomposer.

CLLC is the asymmetric bidirectional resonant converter (one primary tank,
one secondary tank around a compound transformer). PyMKF emits real
``S1..S4`` (primary) and ``Sa..Sd`` (secondary sync rectifier) switches
when ``bridge_simulation_mode="switch"``.

TAS-only golden — the MKF deck embeds PWM PULSE testbench scaffolding with
floating-point timing that is not fully deterministic across rebuilds.
Structural shape is validated in ``test_cllc_spice_realset``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from heaviside.decomposer import decompose_from_spec

GOLDEN_DIR = Path(__file__).parent / "golden"
TAS_GOLDEN = GOLDEN_DIR / "cllc_48to12_5A.tas.json"

# 48 V primary bus → 12 V / 5 A secondary. n=1 (symmetric tank), fsw=150 kHz
# at the resonant frequency. ``powerFlow`` lives inside the operating point
# (root-level powerFlow is silently ignored by PyMKF for CLLC).
SPEC: dict[str, object] = {
    "inputVoltage": {"nominal": 48.0, "minimum": 36.0, "maximum": 60.0},
    "currentRippleRatio": 0.4,
    "diodeVoltageDrop": 0.5,
    "maximumDutyCycle": 0.45,
    "efficiency": 0.95,
    "desiredInductance": 1e-3,
    "desiredTurnsRatios": [1.0],
    "desiredMagnetizingInductance": 1e-3,
    "minSwitchingFrequency": 80_000.0,
    "maxSwitchingFrequency": 300_000.0,
    "operatingPoints": [
        {
            "outputVoltages": [12.0],
            "outputCurrents": [5.0],
            "switchingFrequency": 150_000.0,
            "ambientTemperature": 25.0,
            "powerFlow": "forward",
        }
    ],
}
MAGNETIZING_INDUCTANCE = 1e-3
TURNS_RATIOS = [1.0]


def _maybe_update(path: Path, content: str) -> None:
    if os.environ.get("HEAVISIDE_UPDATE_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def test_cllc_decompose_matches_golden() -> None:
    _netlist, tas = decompose_from_spec(
        "cllc",
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


def test_cllc_spice_realset() -> None:
    from heaviside.decomposer.spice_parser import parse_spice
    from heaviside.decomposer.stencils import _CLLC_REAL_KINDS

    netlist, _ = decompose_from_spec(
        "cllc",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
        bridge_simulation_mode="switch",
    )
    deck = parse_spice(netlist)
    refdeses = {el.refdes for el in deck.elements}
    for r in _CLLC_REAL_KINDS:
        assert r in refdeses, f"MKF CLLC deck missing expected refdes {r!r}"


def test_cllc_tas_shape() -> None:
    _, tas = decompose_from_spec(
        "cllc",
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

    pri_names = {c["name"] for c in tas["topology"]["stages"][0]["circuit"]["components"] if not c["name"].startswith("P_")}
    assert pri_names == {"Q1", "Q2", "Q3", "Q4"}, pri_names

    iso_names = {c["name"] for c in tas["topology"]["stages"][1]["circuit"]["components"] if not c["name"].startswith("P_")}
    # L_r1 / L_r2 are absorbed into T1's compound leakage model (PyMKF does
    # not expose them as bindable extras-magnetic). See stencils.py CLLC docs.
    assert iso_names == {"C_r1", "T1", "C_r2"}, iso_names

    sec_names = {c["name"] for c in tas["topology"]["stages"][2]["circuit"]["components"] if not c["name"].startswith("P_")}
    assert sec_names == {"Q5", "Q6", "Q7", "Q8"}, sec_names

    of_names = {c["name"] for c in tas["topology"]["stages"][3]["circuit"]["components"] if not c["name"].startswith("P_")}
    assert of_names == {"C_bus_LV"}, of_names

    drives = {d["component"] for d in tas["topology"]["stages"][4]["drives"]}
    assert drives == {"Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8"}, drives

    # Vin reaches Q1.D and Q3.D (CLLC has no input bulk cap, unlike CLLLC).
    ports = {p["name"]: p for p in tas["topology"]["interStageCircuit"]}
    vin_eps = {(e["component"], e["pin"]) for e in ports["Vin"]["endpoints"] if not e["component"].startswith("P_")}
    assert vin_eps == {("Q1", "D"), ("Q3", "D")}, vin_eps


def test_cllc_registry_binding_matches_extras_roles() -> None:
    """Smoke-check: registry entry's extras-role names match the PyOM-emitted
    role names for CLLC. Only the two resonant caps are bindable; L_r1/L_r2
    are deliberately unbound (upstream does not expose them as extras)."""
    from heaviside.topologies import registry

    entry = registry.get("cllc")
    assert set(entry.magnetic_binding) == {"T1"}
    assert entry.magnetic_binding["T1"] is None

    assert set(entry.capacitor_binding) == {"C_r1", "C_r2"}
    assert entry.capacitor_binding["C_r1"] == "Cr1_resonantCapacitor_primary"
    assert entry.capacitor_binding["C_r2"] == "Cr2_resonantCapacitor_secondary"
