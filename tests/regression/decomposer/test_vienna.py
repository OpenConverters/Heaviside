"""End-to-end regression test for MKF→TAS Vienna decomposer.

Vienna is a 3-phase boost-PFC rectifier. PyMKF's Phase-1 SPICE generator
emits ONE boost-cell deck representing one of three identical phases at the
line peak (V_phase = V_LL * sqrt(2)/sqrt(3)); Heaviside's stencil follows that
convention and emits the per-phase BOM (L1 + Q1 + D1 + C_bus_DC).

TAS golden is deterministic (single PWM Vpwm with fixed PULSE timing) so
the full TAS dict is golden-locked.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from heaviside.decomposer import decompose_from_spec

GOLDEN_DIR = Path(__file__).parent / "golden"
TAS_GOLDEN = GOLDEN_DIR / "vienna_400ll_to_800dc.tas.json"

# 400 V L-L 3-phase input -> 800 V DC bus (must exceed sqrt(2) * V_LL_max ~= 622 V).
# fsw = 100 kHz, lineFrequency lives at root-level (distinct from per-op
# switchingFrequency in operatingPoints).
SPEC: dict[str, object] = {
    "inputVoltage": {"nominal": 326.6, "minimum": 311.0, "maximum": 359.0},
    "lineToLineVoltage": {"minimum": 380.0, "nominal": 400.0, "maximum": 440.0},
    "outputDcVoltage": 800.0,
    "switchingFrequency": 100_000.0,
    "currentRippleRatio": 0.4,
    "diodeVoltageDrop": 0.5,
    "maximumDutyCycle": 0.45,
    "efficiency": 0.95,
    "desiredInductance": 1e-3,
    "minSwitchingFrequency": 80_000.0,
    "maxSwitchingFrequency": 300_000.0,
    "operatingPoints": [
        {
            "outputVoltages": [800.0],
            "outputCurrents": [0.5],
            "switchingFrequency": 100_000.0,
            "ambientTemperature": 25.0,
        }
    ],
}
MAGNETIZING_INDUCTANCE = 1e-3
TURNS_RATIOS: list[float] = [1.0]


def _maybe_update(path: Path, content: str) -> None:
    if os.environ.get("HEAVISIDE_UPDATE_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def test_vienna_decompose_matches_golden() -> None:
    _netlist, tas = decompose_from_spec(
        "vienna",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
    )
    tas_json = json.dumps(tas, indent=2) + "\n"

    _maybe_update(TAS_GOLDEN, tas_json)

    if not TAS_GOLDEN.exists():
        pytest.fail("Golden fixtures missing. Run with HEAVISIDE_UPDATE_GOLDENS=1 to create.")

    assert tas_json == TAS_GOLDEN.read_text()


def test_vienna_spice_realset() -> None:
    from heaviside.decomposer.spice_parser import parse_spice
    from heaviside.decomposer.stencils import _VIENNA_REAL_KINDS

    netlist, _ = decompose_from_spec(
        "vienna",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
    )
    deck = parse_spice(netlist)
    refdeses = {el.refdes for el in deck.elements}
    for r in _VIENNA_REAL_KINDS:
        assert r in refdeses, f"MKF Vienna deck missing expected refdes {r!r}"


def test_vienna_tas_shape() -> None:
    _, tas = decompose_from_spec(
        "vienna",
        SPEC,
        turns_ratios=TURNS_RATIOS,
        magnetizing_inductance=MAGNETIZING_INDUCTANCE,
    )
    roles = [s["role"] for s in tas["topology"]["stages"]]
    assert roles == ["switchingCell", "control"], roles

    names = {
        c["name"]
        for c in tas["topology"]["stages"][0]["circuit"]["components"]
        if not c["name"].startswith("P_")
    }
    assert names == {"L1", "Q1", "D1", "C_bus_DC"}, names

    drives = {d["component"] for d in tas["topology"]["stages"][1]["drives"]}
    assert drives == {"Q1"}, drives

    ports = {p["name"]: p for p in tas["topology"]["interStageCircuit"]}
    vin_eps = {
        (e["component"], e["pin"])
        for e in ports["Vin"]["endpoints"]
        if not e["component"].startswith("P_")
    }
    assert vin_eps == {("L1", "1")}, vin_eps
    vout_eps = {
        (e["component"], e["pin"])
        for e in ports["Vout"]["endpoints"]
        if not e["component"].startswith("P_")
    }
    assert vout_eps == {("D1", "K"), ("C_bus_DC", "1")}, vout_eps


def test_vienna_registry_binding_matches_extras_roles() -> None:
    """Vienna exposes zero extras-magnetic and zero extras-cap; L1 is the
    sole main magnetic, C_bus_DC is sourced from spec alone."""
    from heaviside.topologies import registry

    entry = registry.get("vienna")
    assert set(entry.magnetic_binding) == {"L1"}
    assert entry.magnetic_binding["L1"] is None
    assert entry.capacitor_binding == {}
