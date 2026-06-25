"""BOM-fill: fill Kirchhoff's per-component requirements with real internal-DB parts.

Real PyKirchhoff + real internal-DB selectors (never mocked); the ngspice leg is
gated on the binary. Proves the "Kirchhoff returns design requirements as a BOM,
HS fills them in" contract end-to-end: design -> read requirements -> select real
parts -> stamp -> DATASHEET deck with real silicon -> delivers spec.
"""

from __future__ import annotations

import shutil

import pytest

from heaviside.catalogue.kirchhoff_fill import (
    KirchhoffFillError,
    fill_kirchhoff_bom,
    stamp_mkf_magnetic,
)
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


def test_unify_hs_tas_semiconductors_restamps_and_fails_loud():
    from heaviside.catalogue.kirchhoff_fill import unify_hs_tas_semiconductors

    records = fill_kirchhoff_bom(ka.design_topology_tas("boost", _BOOST))
    hs_tas = {"topology": {"stages": [{"circuit": {"components": [
        # Q1 carries the OPERATING stress HS's assemble_bom_from_tas already stamped;
        # D1 has none (exercises the conservative fallback to the requirement rating).
        {"name": "Q1", "data": {"semiconductor": {"mosfet": {}}}, "vds_stress": 24.0},
        {"name": "D1", "data": {"semiconductor": {"diode": {}}}},
    ]}}]}}
    assert unify_hs_tas_semiconductors(hs_tas, records) == 2
    q1 = _comp(hs_tas, "Q1")
    assert q1["selection_provenance"]["category"] == "mosfet"
    assert q1["data"]["semiconductor"]["mosfet"]            # Kirchhoff-selected part stamped into HS TAS
    assert q1["vds_stress"] == pytest.approx(24.0)          # OPERATING stress PRESERVED, not the req rating (30)
    assert _comp(hs_tas, "D1")["v_reverse"] == pytest.approx(30.0)  # no HS stress -> conservative fallback to req
    # Kirchhoff selections with no HS-TAS counterpart must fail loud, not silently drop.
    with pytest.raises(KirchhoffFillError):
        unify_hs_tas_semiconductors({"topology": {"stages": []}}, records)


def test_unify_hs_tas_capacitors_restamps_output_cap_leaves_aux():
    from heaviside.catalogue.kirchhoff_fill import unify_hs_tas_capacitors

    records = fill_kirchhoff_bom(ka.design_topology_tas("boost", _BOOST))
    hs_tas = {"topology": {"stages": [{"circuit": {"components": [
        {"name": "C_out", "data": {"capacitor": {}}},   # outputFilter → must be re-stamped
        {"name": "Cboot", "data": {"capacitor": {}}},    # synthesized aux → must NOT be touched
    ]}}]}}
    assert unify_hs_tas_capacitors(hs_tas, records) == 1   # only C_out matched
    cout = _comp(hs_tas, "C_out")
    assert cout["selection_provenance"]["category"] == "capacitor"
    assert cout["data"]["capacitor"]                       # Kirchhoff-selected part stamped
    assert cout["v_rated"] and cout["v_working"]           # gate-readable stress fields
    assert _comp(hs_tas, "Cboot")["data"] == {"capacitor": {}}        # aux cap untouched
    assert "selection_provenance" not in _comp(hs_tas, "Cboot")


def test_fill_skips_numerical_aids_and_defers_controller():
    """Phase 0: numerical convergence aids (Csn*/Rsn*/Csw*) are sim-only — the fill
    must NOT source a real part for them even though they carry a capacitance
    requirement. Phase 1: a controller seed is sourceable but defers cleanly when the
    converter context (topology/Vin/fsw) is not supplied (rather than failing)."""
    tas = {
        "inputs": {"designRequirements": {"inputVoltage": {"nominal": 12.0},
                                          "switchingFrequency": {"nominal": 100000.0}}},
        "topology": {"stages": [{"circuit": {"components": [
            {"name": "CsnA", "data": {"capacitor": {}, "inputs": {"designRequirements": {
                "capacitance": {"nominal": 2.2e-9}, "ratedVoltage": 50.0}}}},   # numerical aid
            {"name": "U1", "data": {"controller": {}}},                          # control IC seed
        ]}}]}}
    recs = {r["name"]: r for r in fill_kirchhoff_bom(tas)}   # no topology -> controller defers
    assert recs["CsnA"]["filled"] is False and "numerical" in recs["CsnA"]["deferred"]
    assert tas["topology"]["stages"][0]["circuit"]["components"][0]["data"]["capacitor"] == {}  # NOT sourced
    assert recs["U1"]["filled"] is False and "topology" in recs["U1"]["deferred"]


def test_fill_sources_controller_from_ctas_catalog():
    """With converter context (topology/Vin/fsw), a controller seed sources a real
    control IC from the CTAS-shaped controllers.ndjson (selector reads the nested
    manufacturerInfo.datasheetInfo.function shape + normalizes intendedTopologies)."""
    tas = {
        "inputs": {"designRequirements": {"inputVoltage": {"nominal": 12.0},
                                          "switchingFrequency": {"nominal": 100000.0}}},
        "topology": {"stages": [{"circuit": {"components": [
            {"name": "U1", "data": {"controller": {}}}]}}]}}
    recs = fill_kirchhoff_bom(tas, topology="boost")
    assert recs[0]["filled"] is True and recs[0]["mpn"]   # a real control IC was sourced
    assert recs[0]["selection"].alternatives_considered > 0


def test_fill_sources_gate_driver_and_resistor():
    """Phase 2/3: a gateDriver-category control seed sources a real gate driver, a real
    resistor seed sources a real resistor, and an Rsn* numerical-aid resistor is skipped."""
    gd = {"inputs": {"designRequirements": {"inputVoltage": {"nominal": 400.0},
                                            "switchingFrequency": {"nominal": 100000.0}}},
          "topology": {"stages": [{"circuit": {"components": [
              {"name": "UDR", "data": {"controller": {}, "inputs": {"designRequirements":
                  {"function": {"category": "gateDriver"}}}}}]}}]}}
    r = fill_kirchhoff_bom(gd, topology="phase_shifted_full_bridge")[0]
    assert r["filled"] is True and r["selection"].chosen.category == "gateDriver"

    def _fill_resistor(name):
        t = {"inputs": {"designRequirements": {}}, "topology": {"stages": [{"circuit": {"components": [
            {"name": name, "data": {"resistor": {}, "inputs": {"designRequirements": {
                "resistance": {"nominal": 100.0}, "powerRating": 1.0, "tolerance": 0.05}}}}]}}]}}
        return fill_kirchhoff_bom(t)[0]
    assert _fill_resistor("Rsense")["filled"] is True       # real resistor sourced
    assert _fill_resistor("Rsn1")["filled"] is False        # numerical-aid resistor skipped


_SUBCKT = (
    "* Magnetic model made with OpenMagnetics\n"
    ".subckt PQ_3F3_TURNS_5 P1+ P1-\n"
    "Rdc1 P1+ n1 0.01\n"
    "Lmag_1 n1 P1- 150u\n"
    ".ends\n"
)


class _StubPyom:
    """Test double for the PyOM export (we are testing the stamp, not MKF physics)."""

    def export_magnetic_as_subcircuit(self, magnetic):
        return _SUBCKT


def test_stamp_mkf_magnetic_places_subcircuit_object():
    tas = ka.design_topology_tas("boost", _BOOST)
    rec = stamp_mkf_magnetic(tas, {"any": "magnetic"}, pyom=_StubPyom())
    assert rec == {"reference": "PQ_3F3_TURNS_5", "stamped": 1}
    sub = _comp(tas, "L1")["data"]["magnetic"]["modelOutputs"]["spiceSubcircuit"]
    assert sub == {"text": _SUBCKT, "reference": "PQ_3F3_TURNS_5"}  # {text,reference}, not a bare str


def test_stamp_mkf_magnetic_fail_loud():
    class _Bad:
        def export_magnetic_as_subcircuit(self, m):
            return "no subckt line here"

    with pytest.raises(KirchhoffFillError):
        stamp_mkf_magnetic(ka.design_topology_tas("boost", _BOOST), {}, pyom=_Bad())


@pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")
def test_stage3_kirchhoff_backend_stamps_regulated_operating_point():
    """The stage3_realize Kirchhoff backend helper designs+fills+sims via Kirchhoff
    (real semis + MKF_MODEL magnetic, closed-loop regulated) and stamps a realistic
    regulated operating point into HS's TAS for the realism gate."""
    import types

    from heaviside import bridge
    from heaviside.pipeline.full_design import _simulate_kirchhoff_backend

    try:
        pyom = bridge._import_pyom_vendor()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PyOM vendor not available: {exc}")
    conv = {
        "inputVoltage": {"nominal": 12.0}, "efficiency": 0.9, "diodeVoltageDrop": 0.7,
        "currentRippleRatio": 0.4,
        "operatingPoints": [{"inputVoltage": 12.0, "switchingFrequency": 100000.0,
                             "ambientTemperature": 25.0, "currentRippleRatio": 0.4,
                             "outputVoltages": [24.0], "outputCurrents": [1.0]}],
    }
    mag = pyom.design_magnetics_from_converter("boost", conv, 1, "available cores", False, None)["data"][0]["mas"]["magnetic"]
    components = types.SimpleNamespace(main_magnetic=types.SimpleNamespace(mas={"magnetic": mag}))
    spec_dict = {
        "inputVoltage": {"nominal": 12.0}, "efficiency": 0.9,
        "operatingPoints": [{"inputVoltage": 12.0, "switchingFrequency": 100000.0,
                             "outputVoltages": [24.0], "outputCurrents": [1.0]}],
    }
    # A minimal HS TAS with the boost's power semiconductors (as HS's decompose
    # leaves them) — the backend must unify these with the Kirchhoff sim's parts.
    tas: dict = {"topology": {"stages": [{"circuit": {"components": [
        # Q1 carries the operating Vds stress HS's assemble_bom_from_tas stamps (24 V
        # for a 12->24 boost); the unify must preserve it, swapping only the part.
        {"name": "Q1", "data": {"semiconductor": {"mosfet": {}}}, "vds_stress": 24.0},
        {"name": "D1", "data": {"semiconductor": {"diode": {}}}},
    ]}}]}}
    _simulate_kirchhoff_backend(
        tas, topology="boost", spec_dict=spec_dict, components=components,
        first_op=spec_dict["operatingPoints"][0], vout_target=24.0,
    )
    op = tas["simulation_results"]["op0"]
    assert abs(op["vout"] - 24.0) <= 1.5                 # regulated near target
    assert 0.85 <= op["efficiency"] <= 1.0               # realistic (not the open-loop artifact)
    assert op["pin"] > op["pout"] > 0                    # physical
    assert op["total_losses"] == pytest.approx(op["pin"] - op["pout"], rel=1e-6)
    # unified: HS's gate semis now carry the Kirchhoff-selected parts + their stress
    q1 = _comp(tas, "Q1")
    assert q1["data"]["semiconductor"]["mosfet"]                       # real part stamped
    assert q1["selection_provenance"]["category"] == "mosfet"
    assert q1["vds_stress"] == pytest.approx(24.0)                     # operating stress preserved (not the req rating)


@pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")
def test_full_cutover_real_semis_and_mkf_magnetic():
    """End-to-end: della-Pollock MKF magnetic (MKF_MODEL) + Kirchhoff-requirement
    BOM-fill (DATASHEET semis/caps) -> a real deck that delivers the spec."""
    from heaviside import bridge

    try:
        pyom = bridge._import_pyom_vendor()
    except Exception as exc:  # noqa: BLE001 - native dep optional in some envs
        pytest.skip(f"PyOM vendor not available: {exc}")
    conv = {
        "inputVoltage": {"nominal": 12.0},
        "efficiency": 0.9,
        "diodeVoltageDrop": 0.7,
        "currentRippleRatio": 0.4,
        "operatingPoints": [
            {
                "inputVoltage": 12.0,
                "switchingFrequency": 100000.0,
                "ambientTemperature": 25.0,
                "currentRippleRatio": 0.4,
                "outputVoltages": [24.0],
                "outputCurrents": [1.0],
            }
        ],
    }
    designed = pyom.design_magnetics_from_converter("boost", conv, 1, "available cores", False, None)
    magnetic = designed["data"][0]["mas"]["magnetic"]

    tas = ka.design_topology_tas("boost", _BOOST)
    fill_kirchhoff_bom(tas)
    rec = stamp_mkf_magnetic(tas, magnetic, pyom=pyom)
    assert rec["stamped"] >= 1

    deck = ka.tas_to_ngspice(tas, "MKF_MODEL")
    assert rec["reference"][:12] in deck  # the MKF subckt was hoisted into the deck
    r = simulate_self_contained_deck(deck, vout_target=24.0, tolerance=0.05)
    assert 22.0 <= r.result["vout"] <= 26.0, r.result


@pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")
def test_operating_point_adapter_returns_full_op():
    """simulate_self_contained_deck(compute_operating_point=True) returns the full
    operating point the realism gate consumes; on the ideal deck efficiency is
    plausible (~96%), which validates the input-current measurement."""
    deck = ka.tas_to_ngspice(ka.design_topology_tas("boost", _BOOST), "REQUIREMENTS")
    r = simulate_self_contained_deck(deck, vout_target=24.0, tolerance=0.05, compute_operating_point=True)
    op = r.result
    assert set(op) >= {"vin", "iin", "vout", "iout", "pin", "pout", "total_losses", "efficiency"}
    assert op["pout"] == pytest.approx(abs(op["vout"]) * op["iout"], rel=1e-6)
    assert op["pin"] == pytest.approx(op["vin"] * op["iin"], rel=1e-6)
    assert 0.90 <= op["efficiency"] <= 1.0  # ideal-component deck


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
