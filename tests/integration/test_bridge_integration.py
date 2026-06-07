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
    """Full spec → MKF deck + TAS → PyOM magnetic design → annotated TAS."""
    # 1. Decompose to (deck, tas).
    _, tas = decompose_from_spec(
        "buck",
        BUCK_SPEC,
        turns_ratios=[],
        magnetizing_inductance=BUCK_SPEC["desiredInductance"],
    )

    # 2. Ask PyOM to design the buck inductor.
    designs = bridge.design_magnetics(
        "buck",
        BUCK_SPEC,
        max_results=1,
        use_ngspice=False,
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


ACF_SPEC: dict = {
    "inputVoltage": {"minimum": 36.0, "nominal": 48.0, "maximum": 60.0},
    "desiredInductance": 1e-3,
    "desiredTurnsRatios": [4.0],
    "currentRippleRatio": 0.4,
    "diodeVoltageDrop": 0.5,
    "maximumDutyCycle": 0.45,
    "efficiency": 0.9,
    "operatingPoints": [
        {
            "outputVoltages": [12.0],
            "outputCurrents": [5.0],
            "switchingFrequency": 250_000,
            "ambientTemperature": 25,
        }
    ],
}


@pytest.mark.integration
def test_acf_multi_magnetic_end_to_end_bridge() -> None:
    """Full Phase A + Phase B for ACF: main transformer + output inductor.

    Verifies the multi-magnetic orchestrator:
      1. ``design_converter_components`` runs Phase A (transformer) and
         Phase B (outputInductor magnetic + clampCapacitor spec).
      2. ``attach_components_to_tas`` binds main→T1 and
         outputInductor→L_out0 via the registry's ``magnetic_binding``.

    This is slow (~2–3 minutes) because Phase A and Phase B each run
    the full PyOM design loop. Opt in with ``-m integration``.
    """
    _, tas = decompose_from_spec(
        "active_clamp_forward",
        ACF_SPEC,
        turns_ratios=ACF_SPEC["desiredTurnsRatios"],
        magnetizing_inductance=ACF_SPEC["desiredInductance"],
    )

    components = bridge.design_converter_components(
        "active_clamp_forward",
        ACF_SPEC,
        max_results=1,
        use_ngspice=False,
    )
    assert components.main_magnetic.scoring > 0
    assert len(components.main_magnetic.winding_names) >= 2, "ACF transformer must have ≥2 windings"
    assert "outputInductor" in components.extra_magnetics
    assert components.extra_magnetics["outputInductor"].scoring > 0
    # Capacitor extras are spec-only — bridge doesn't design them.
    cap_names = [c.name for c in components.extra_capacitors]
    assert "clampCapacitor" in cap_names

    bridge.attach_components_to_tas(tas, components, topology="active_clamp_forward")

    magnetics = [
        c
        for s in tas["topology"]["stages"]
        for c in s.get("circuit", {}).get("components", [])
        if isinstance(c.get("data"), dict) and "magnetic" in c["data"]
    ]
    by_name = {c["name"]: c for c in magnetics}
    assert "T1" in by_name and "L_out0" in by_name
    assert by_name["T1"]["data"]["magnetic"]["core"]["functionalDescription"]["shape"]
    assert by_name["L_out0"]["data"]["magnetic"]["core"]["functionalDescription"]["shape"]
    # Distinct designs.
    assert (
        by_name["T1"]["scoring"] != by_name["L_out0"]["scoring"]
        or by_name["T1"]["data"]["magnetic"]["core"]["functionalDescription"]["shape"]
        != by_name["L_out0"]["data"]["magnetic"]["core"]["functionalDescription"]["shape"]
    )


LLC_SPEC: dict = {
    "inputVoltage": {"minimum": 380.0, "nominal": 400.0, "maximum": 420.0},
    "currentRippleRatio": 0.4,
    "diodeVoltageDrop": 0.5,
    "maximumDutyCycle": 0.45,
    "efficiency": 0.95,
    "desiredInductance": 1e-3,
    "desiredTurnsRatios": [16.0],
    "minSwitchingFrequency": 80_000.0,
    "maxSwitchingFrequency": 300_000.0,
    "operatingPoints": [
        {
            "outputVoltages": [12.0],
            "outputCurrents": [5.0],
            "switchingFrequency": 150_000,
            "ambientTemperature": 25,
        }
    ],
}


def _llc_design_magnetics_is_safe() -> bool:
    """Return True if PyOM can run ``design_magnetics_from_converter('llc', ...)``
    without segfaulting.

    The call currently crashes the interpreter (exit 139) regardless of the
    input spec — an upstream PyOpenMagnetics bug. A segfault can't be caught
    with ``xfail`` because it kills the pytest process, so we probe via a
    subprocess and skip if it crashes. When upstream is fixed the canary
    returns True and the real test runs.
    """
    import subprocess
    import sys

    code = (
        "from PyOpenMagnetics import PyOpenMagnetics as P; "
        "spec={'inputVoltage':{'minimum':36.0,'nominal':48.0,'maximum':60.0},"
        "'currentRippleRatio':0.4,'diodeVoltageDrop':0.5,'maximumDutyCycle':0.45,"
        "'efficiency':0.95,'desiredInductance':1e-3,'desiredTurnsRatios':[4.0],"
        "'minSwitchingFrequency':80000.0,'maxSwitchingFrequency':300000.0,"
        "'operatingPoints':[{'outputVoltages':[12.0],'outputCurrents':[5.0],"
        "'switchingFrequency':150000,'ambientTemperature':25}]}; "
        "P.design_magnetics_from_converter('llc', spec, 1, 'available cores', False, []); "
        "print('ok')"
    )
    rc = subprocess.run(
        [sys.executable, "-u", "-c", code],
        capture_output=True,
        timeout=180,
    )
    return rc.returncode == 0


@pytest.mark.integration
def test_llc_multi_magnetic_end_to_end_bridge() -> None:
    """Verify the LLC ``L_r → seriesInductor`` binding end-to-end.

    Mirrors the ACF test but for the resonant family. The largest
    previously-unverified claim in ``magnetic_binding``: that the
    resonant-tank inductor emitted by the LLC stencil as ``L_r`` is
    the same object PyOM returns as the ``seriesInductor`` extras
    role. If the binding is wrong, ``attach_components_to_tas`` will
    raise ``KeyError`` here.
    """
    if not _llc_design_magnetics_is_safe():
        pytest.skip(
            "Upstream PyOM segfault in design_magnetics_from_converter('llc', ...). "
            "The Heaviside bridge path is correct (unit tests + extras-probe "
            "confirm magnetic_binding {T1=None, L_r=seriesInductor}). Skipping "
            "until upstream is fixed; canary will auto-enable the test."
        )

    _, tas = decompose_from_spec(
        "llc",
        LLC_SPEC,
        turns_ratios=LLC_SPEC["desiredTurnsRatios"],
        magnetizing_inductance=LLC_SPEC["desiredInductance"],
        bridge_simulation_mode="switch",
    )

    components = bridge.design_converter_components(
        "llc",
        LLC_SPEC,
        max_results=1,
        use_ngspice=False,
    )
    assert components.main_magnetic.scoring > 0
    assert len(components.main_magnetic.winding_names) >= 2, "LLC transformer must have ≥2 windings"
    assert "seriesInductor" in components.extra_magnetics, (
        f"PyOM did not return seriesInductor extras-role; got {sorted(components.extra_magnetics)}"
    )
    assert components.extra_magnetics["seriesInductor"].scoring > 0
    cap_names = [c.name for c in components.extra_capacitors]
    assert "resonantCapacitor" in cap_names

    bridge.attach_components_to_tas(tas, components, topology="llc")

    magnetics = [
        c
        for s in tas["topology"]["stages"]
        for c in s.get("circuit", {}).get("components", [])
        if isinstance(c.get("data"), dict) and "magnetic" in c["data"]
    ]
    by_name = {c["name"]: c for c in magnetics}
    assert "T1" in by_name and "L_r" in by_name, (
        f"LLC stencil must emit T1 + L_r; got {sorted(by_name)}"
    )
    assert by_name["T1"]["data"]["magnetic"]["core"]["functionalDescription"]["shape"]
    assert by_name["L_r"]["data"]["magnetic"]["core"]["functionalDescription"]["shape"]
    # The two designs must be distinct PyOM artefacts.
    assert (
        by_name["T1"]["scoring"] != by_name["L_r"]["scoring"]
        or by_name["T1"]["data"]["magnetic"]["core"]["functionalDescription"]["shape"]
        != by_name["L_r"]["data"]["magnetic"]["core"]["functionalDescription"]["shape"]
    )
