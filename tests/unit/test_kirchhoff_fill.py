"""BOM-fill: fill Kirchhoff's per-component requirements with real internal-DB parts.

Real PyKirchhoff + real internal-DB selectors (never mocked); the ngspice leg is
gated on the binary. Proves the "Kirchhoff returns design requirements as a BOM,
HS fills them in" contract end-to-end: design -> read requirements -> select real
parts -> stamp -> DATASHEET deck with real silicon -> delivers spec.
"""

from __future__ import annotations

import shutil

import pytest

from heaviside.catalogue.kirchhoff_fill import KirchhoffFillError, fill_kirchhoff_bom
from heaviside.decomposer import kirchhoff_adapter as ka
from heaviside.stages.spice_sim import simulate_self_contained_deck

pytestmark = pytest.mark.skipif(
    not ka.available(), reason="PyKirchhoff not built — see docs/kirchhoff_migration_analysis.md"
)

_BOOST = {
    "designRequirements": {
        "efficiency": 1.0,
        "inputVoltage": {"nominal": 12},
        "switchingFrequency": {"nominal": 100000},
        "outputs": [{"name": "out", "voltage": {"nominal": 24}}],
    },
    "operatingPoints": [{"inputVoltage": 12, "outputs": [{"power": 24}]}],
}


def _comp(tas, name):
    return next(c for st in tas["topology"]["stages"] for c in st["circuit"]["components"] if c["name"] == name)


def test_fill_selects_and_stamps_real_parts():
    tas = ka.design_topology_tas("boost", _BOOST)
    recs = {r["name"]: r for r in fill_kirchhoff_bom(tas)}
    # semis + cap filled with a real MPN; magnetic deferred to MKF (della-Pollock)
    assert recs["Q1"]["filled"] and recs["Q1"]["mpn"]
    assert recs["D1"]["filled"] and recs["D1"]["mpn"]
    assert recs["Cout"]["filled"] and recs["Cout"]["mpn"]
    assert recs["L1"]["filled"] is False and "MKF" in recs["L1"]["deferred"]
    # the seed slot now holds a real (non-empty) part -> promotes to DATASHEET fidelity
    assert _comp(tas, "Q1")["data"]["semiconductor"]["mosfet"]
    assert _comp(tas, "Cout")["data"]["capacitor"]


def test_fill_malformed_tas_raises():
    with pytest.raises(KirchhoffFillError):
        fill_kirchhoff_bom({"nope": 1})


@pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")
def test_filled_tas_emits_real_deck_and_delivers_spec():
    tas = ka.design_topology_tas("boost", _BOOST)
    fill_kirchhoff_bom(tas)
    deck = ka.tas_to_ngspice(tas, "DATASHEET")
    # real-component deck (per the assembler header), carrying the selected silicon
    assert "real" in deck[:200].lower()
    r = simulate_self_contained_deck(deck, vout_target=24.0, tolerance=0.05)
    # real Rds_on/Vf/ESR + an ideal magnetic seed: still delivers the 24 V spec
    assert 22.0 <= r.result["vout"] <= 26.0, r.result
