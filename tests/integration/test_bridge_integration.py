"""Integration test for ``heaviside.bridge`` against real PyOpenMagnetics.

Slow (~10–30 s per topology) — opt in with ``-m integration``. Skipped
automatically when PyOpenMagnetics is unavailable.

Exercises the magnetic-design + attach loop on a real Kirchhoff-built TAS:

    spec → kirchhoff_adapter.design_from_hs_spec (TAS) →
    bridge.design_magnetics_fast → attach_magnetics_to_tas
    → assert the TAS magnetic now carries a populated MAS magnetic.

The MKF-stencil ``decompose_from_spec`` deck generator that this test
formerly built its TAS from was removed in the della-Pollock cutover;
the live Kirchhoff seam now owns TAS generation. Single-magnetic
topologies only — multi-magnetic mapping is covered by unit tests.
"""

from __future__ import annotations

import pytest

pyom = pytest.importorskip("PyOpenMagnetics", reason="PyOpenMagnetics not installed")

from heaviside import bridge  # noqa: E402
from heaviside.decomposer import kirchhoff_adapter as _ka  # noqa: E402

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
    """Full spec → Kirchhoff TAS → magnetic design → annotated TAS."""
    # 1. Build the TAS via the live Kirchhoff design seam (the della-Pollock
    #    cutover retired the MKF-stencil decompose deck — Kirchhoff owns TAS gen).
    try:
        tas = _ka.design_from_hs_spec("buck", BUCK_SPEC)
    except (_ka.KirchhoffUnavailable, _ka.KirchhoffTopologyUnsupported) as exc:
        pytest.skip(f"Kirchhoff backend unavailable: {exc}")

    # 2. Design the buck inductor from Kirchhoff's per-topology seed (della-Pollock
    #    abt #48 — the MKF converter-model slow path is retired; MKF designs geometry only).
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

    # 3. Attach into the Kirchhoff TAS (exactly one magnetic → designs[0]).
    bridge.attach_magnetics_to_tas(tas, designs)

    # 4. The single magnetic component must now carry the resolved PEAS magnetic doc.
    magnetics = [
        c
        for s in tas["topology"]["stages"]
        for c in s.get("circuit", {}).get("components", [])
        if isinstance(c.get("data"), dict) and "magnetic" in c["data"]
    ]
    assert len(magnetics) == 1
    mag = magnetics[0]
    assert mag["data"]["magnetic"]["core"]["functionalDescription"]["shape"]
    assert mag["data"]["magnetic"]["coil"]["functionalDescription"]
    assert mag["scoring"] == top.scoring
