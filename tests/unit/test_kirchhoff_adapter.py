"""Tests for the Kirchhoff backend adapter (heaviside.decomposer.kirchhoff_adapter).

Exercises the *real* compiled ``PyKirchhoff`` module (never a mock); skips cleanly
when it has not been built, mirroring the native-dependency convention used for
PyOpenMagnetics and ``tas_validator``. The ngspice end-to-end check is gated on
the ``ngspice`` binary being present.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from heaviside.decomposer import kirchhoff_adapter as ka

pytestmark = pytest.mark.skipif(
    not ka.available(),
    reason="PyKirchhoff not built — see docs/kirchhoff_migration_analysis.md (ninja -j3)",
)

# 48 V -> 12 V, 24 W, 100 kHz — the reference design from Kirchhoff's README.
_SPEC = {
    "designRequirements": {
        "efficiency": 1.0,
        "inputVoltage": {"minimum": 45.6, "nominal": 48, "maximum": 50.4},
        "switchingFrequency": {"nominal": 100000},
        "outputs": [{"name": "out", "voltage": {"nominal": 12}}],
    },
    "operatingPoints": [{"inputVoltage": 48, "outputs": [{"power": 24}]}],
}


def test_bound_topologies():
    topos = ka.available_topologies()
    assert "flyback" in topos
    assert "boost" in topos


def test_design_and_emit_flyback():
    tas = ka.design_topology_tas("flyback", _SPEC)
    assert isinstance(tas, dict) and "topology" in tas
    deck = ka.tas_to_ngspice(tas, "REQUIREMENTS")
    assert isinstance(deck, str) and len(deck) > 0
    low = deck.lower()
    assert ".tran" in low and (".control" in low or ".end" in low)


def test_unsupported_topology_raises():
    # A topology with no Kirchhoff converter designer must fail loud, not degrade.
    # (common_mode_choke is a filter magnetic, not a power converter Kirchhoff designs.)
    with pytest.raises(ka.KirchhoffTopologyUnsupported):
        ka.design_topology_tas("common_mode_choke", _SPEC)


# 12 V -> 24 V boost in the *Heaviside* converter-spec shape (top-level
# inputVoltage + operatingPoints with outputVoltages/outputCurrents).
_HS_SPEC = {
    "inputVoltage": {"minimum": 11.4, "nominal": 12, "maximum": 12.6},
    "efficiency": 0.9,
    "operatingPoints": [
        {
            "inputVoltage": 12,
            "switchingFrequency": 100000,
            "ambientTemperature": 25,
            "outputVoltages": [24.0],
            "outputCurrents": [1.5],
        }
    ],
}


def test_hs_spec_to_kirchhoff_maps_fields():
    k = ka.hs_spec_to_kirchhoff(_HS_SPEC)
    dr = k["designRequirements"]
    assert dr["efficiency"] == 0.9
    assert dr["inputVoltage"] == {"minimum": 11.4, "nominal": 12.0, "maximum": 12.6}
    assert dr["switchingFrequency"] == {"nominal": 100000.0}
    assert dr["outputs"] == [{"name": "out0", "voltage": {"nominal": 24.0}}]
    op = k["operatingPoints"][0]
    assert op["inputVoltage"] == 12.0
    assert op["outputs"] == [{"power": 36.0}]  # 24 V * 1.5 A


def test_hs_spec_to_kirchhoff_propagates_current_ripple_ratio():
    # Kirchhoff sizes the inductor from config "rippleRatio" (main inductor) /
    # "inductorRippleRatio" (output inductor), defaulting to 0.4 when absent. HS's
    # currentRippleRatio must reach both, or the magnetic comes back undersized and
    # saturates the realism gate (a buck was 228 µH @ 0.4 default vs 300 µH @ 0.3).
    k = ka.hs_spec_to_kirchhoff({**_HS_SPEC, "currentRippleRatio": 0.3})
    assert k["config"]["rippleRatio"] == 0.3
    assert k["config"]["inductorRippleRatio"] == 0.3


def test_hs_spec_to_kirchhoff_explicit_config_ripple_wins():
    # An explicit caller config value is authoritative over the currentRippleRatio seed.
    k = ka.hs_spec_to_kirchhoff(
        {**_HS_SPEC, "currentRippleRatio": 0.3, "config": {"rippleRatio": 0.25}}
    )
    assert k["config"]["rippleRatio"] == 0.25
    assert k["config"]["inductorRippleRatio"] == 0.3  # seeded for the unset key


def test_hs_spec_to_kirchhoff_no_ripple_no_config():
    # No currentRippleRatio and no caller config ⇒ no config key (KH uses its defaults).
    k = ka.hs_spec_to_kirchhoff(_HS_SPEC)
    assert "config" not in k


_BOOST_SPEC = {
    "designRequirements": {
        "efficiency": 1.0,
        "inputVoltage": {"nominal": 12},
        "switchingFrequency": {"nominal": 100000},
        "outputs": [{"name": "out", "voltage": {"nominal": 24}}],
    },
    "operatingPoints": [{"inputVoltage": 12, "outputs": [{"power": 24}]}],
}


def test_component_requirements_are_the_bom_to_fill():
    """Kirchhoff emits per-component requirements (the BOM HS fills): a MOSFET's
    Rds_on/Id, a diode's Vf, a cap's C/ESR, the magnetic's inductance."""
    reqs = ka.kirchhoff_component_requirements(ka.design_topology_tas("boost", _BOOST_SPEC))
    by_name = {r["name"]: r for r in reqs}
    assert by_name["Q1"]["family"] == "semiconductor" and by_name["Q1"]["kind"] == "mosfet"
    assert "maximumOnResistance" in by_name["Q1"]["requirements"]
    assert "ratedContinuousDrainCurrent" in by_name["Q1"]["requirements"]
    assert by_name["D1"]["kind"] == "diode" and "maximumForwardVoltage" in by_name["D1"]["requirements"]
    assert by_name["L1"]["family"] == "magnetic"
    assert "magnetizingInductance" in by_name["L1"]["requirements"]
    assert by_name["Cout"]["family"] == "capacitor" and "maximumEsr" in by_name["Cout"]["requirements"]


def test_component_requirements_malformed_tas_raises():
    with pytest.raises(ka.KirchhoffSpecError):
        ka.kirchhoff_component_requirements({"no": "topology"})


def test_hs_spec_to_kirchhoff_fail_loud():
    import copy

    # missing efficiency
    s = copy.deepcopy(_HS_SPEC)
    del s["efficiency"]
    with pytest.raises(ka.KirchhoffSpecError):
        ka.hs_spec_to_kirchhoff(s)
    # output lists mismatched in length
    s = copy.deepcopy(_HS_SPEC)
    s["operatingPoints"][0]["outputCurrents"] = [1.0, 2.0]
    with pytest.raises(ka.KirchhoffSpecError):
        ka.hs_spec_to_kirchhoff(s)
    # no operating points
    s = copy.deepcopy(_HS_SPEC)
    s["operatingPoints"] = []
    with pytest.raises(ka.KirchhoffSpecError):
        ka.hs_spec_to_kirchhoff(s)
    # missing switching frequency
    s = copy.deepcopy(_HS_SPEC)
    del s["operatingPoints"][0]["switchingFrequency"]
    with pytest.raises(ka.KirchhoffSpecError):
        ka.hs_spec_to_kirchhoff(s)


@pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")
def test_flyback_deck_simulates_to_target():
    """The self-contained deck runs in ngspice and regulates near the 12 V target."""
    deck = ka.tas_to_ngspice(ka.design_topology_tas("flyback", _SPEC), "REQUIREMENTS")
    with tempfile.TemporaryDirectory() as d:
        cir = Path(d) / "flyback.cir"
        cir.write_text(deck)
        res = subprocess.run(
            ["ngspice", "-b", str(cir)], capture_output=True, text=True, timeout=120
        )
    text = res.stdout + res.stderr
    vouts = [
        float(m)
        for m in re.findall(r"vout\s*=\s*([0-9.eE+\-]+)", text)
        if _finite_positive(m)
    ]
    assert vouts, f"no vout measurement parsed from ngspice output:\n{text[-500:]}"
    settled = max(vouts)
    # Ideal flyback for a 12 V target — generous band (build-version tolerant).
    assert 10.0 <= settled <= 13.5, f"flyback vout {settled} V off the 12 V target"


def test_spice_sim_unknown_backend_raises():
    """The spice_sim seam fails loud on an unknown backend (no silent default)."""
    from heaviside.stages import spice_sim

    with pytest.raises(ValueError):
        spice_sim.simulate_from_spec("flyback", _SPEC, [], 0.0, backend="bogus")


@pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")
def test_spice_sim_kirchhoff_backend_flyback():
    """The spice_sim seam routes deck-gen + sim through the Kirchhoff backend
    and returns a SpiceResult regulating near the 12 V target."""
    from heaviside.stages import spice_sim

    r = spice_sim.simulate_from_spec(
        "flyback",
        _SPEC,
        turns_ratios=[],
        magnetizing_inductance=0.0,
        vout_target=12.0,
        backend="kirchhoff",
    )
    assert "vout" in r.result
    assert 10.0 <= r.result["vout"] <= 13.5, r.result
    assert r.deck  # the Kirchhoff-assembled deck is carried on the result


def _finite_positive(s: str) -> bool:
    try:
        v = float(s)
    except ValueError:
        return False
    return v == v and v > 0.1  # exclude ~0 and NaN measurements
