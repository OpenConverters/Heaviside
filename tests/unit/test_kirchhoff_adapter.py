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
