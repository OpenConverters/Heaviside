"""Integration test for ``heaviside.bridge`` against real PyOpenMagnetics.

Slow (~10–30 s per topology) — opt in with ``-m integration``. Skipped
automatically when PyOpenMagnetics is unavailable.

Exercises the full closed loop:

    spec → decompose → bridge.design_magnetics → attach_magnetics_to_tas
    → assert TAS has a populated MAS magnetic.

Single-magnetic topologies only — multi-magnetic mapping is covered by
unit tests.
"""

from __future__ import annotations

import pytest

pyom = pytest.importorskip("PyOpenMagnetics", reason="PyOpenMagnetics not installed")

from heaviside import bridge  # noqa: E402
from heaviside.decomposer import decompose_from_spec  # noqa: E402

BUCK_SPEC: dict = {
    "inputVoltage": {"minimum": 36, "maximum": 60, "nominal": 48},
    "desiredInductance": 22e-6,
    "currentRippleRatio": 0.4,
    "diodeVoltageDrop": 0.7,
    "efficiency": 0.95,
    "operatingPoints": [
        {
            "outputVoltages": [12.0],
            "outputCurrents": [5.0],
            "switchingFrequency": 200_000,
            "ambientTemperature": 25,
        }
    ],
}


@pytest.mark.integration
def test_buck_end_to_end_bridge() -> None:
    """Full spec → TAS → Kirchhoff-seeded magnetic design → annotated TAS."""
    # 1. Decompose to (deck, tas).
    _, tas = decompose_from_spec(
        "buck",
        BUCK_SPEC,
        turns_ratios=[],
        magnetizing_inductance=BUCK_SPEC["desiredInductance"],
    )

    # 2. Design the buck inductor from Kirchhoff's per-topology seed (della-Pollock cutover
    # abt #48 — the MKF converter-model slow path is retired; MKF designs geometry only).
    designs = bridge.design_magnetics_fast(
        "buck",
        BUCK_SPEC,
        max_results=1,
    )
    assert len(designs) == 1
    top = designs[0]
    assert top.scoring > 0
    assert top.core_shape_name  # any non-empty string
    assert top.core_material_name
    assert len(top.winding_names) == 1, f"buck has one winding, got {top.winding_names}"

    # 3. Attach into TAS.
    bridge.attach_magnetics_to_tas(tas, designs)

    # 4. The L1 component must now carry the resolved PEAS magnetic doc.
    magnetics = [
        c
        for s in tas["topology"]["stages"]
        for c in s.get("circuit", {}).get("components", [])
        if isinstance(c.get("data"), dict) and "magnetic" in c["data"]
    ]
    assert len(magnetics) == 1
    l1 = magnetics[0]
    assert l1["name"] == "L1"
    assert l1["data"]["magnetic"]["core"]["functionalDescription"]["shape"]
    assert l1["data"]["magnetic"]["coil"]["functionalDescription"]
    assert l1["scoring"] == top.scoring
