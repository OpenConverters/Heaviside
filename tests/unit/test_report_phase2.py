"""Phase-2 design-report sections (efficiency-vs-load, load/line regulation,
loss reconciliation, magnetic BOM row, junction-temperature thermal table,
schematic/netlist).

These exercise the shared :class:`heaviside.report.model.ReportModel` and both
renderers (LaTeX + HTML) WITHOUT a real ``design_converter`` run: a canned,
regulatable Kirchhoff-style TAS plus a monkeypatched ``simulate_regulated`` make
the closed-loop re-sim fast and deterministic. House rule (CLAUDE.md): a device
without datasheet thermal data renders ``n/a`` — never a fabricated number.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from heaviside.report import model as report_model
from heaviside.report.html import render_html
from heaviside.report.latex import render_latex
from heaviside.report.model import ReportModel


# ── canned design fixtures ────────────────────────────────────────────────────

def _mas() -> dict:
    """A compact-but-complete buck-inductor MAS (one winding, MKF loss outputs)."""
    return {
        "magnetic": {
            "core": {
                "name": "T 17/9.5/7.0",
                "functionalDescription": {
                    "type": "toroidal", "gapping": [],
                    "material": {"name": "High Flux 160"},
                    "shape": {"name": "T 17/9.5/7.0"},
                },
                "processedDescription": {
                    "effectiveParameters": {
                        "effectiveArea": 6.5e-5, "effectiveLength": 4.0e-2,
                        "effectiveVolume": 2.6e-6,
                    },
                },
            },
            "coil": {
                "functionalDescription": [
                    {"name": "Primary", "isolationSide": "primary", "numberTurns": 37,
                     "numberParallels": 1, "wire": {"conductingDiameter": {"nominal": 0.8e-3}}},
                ],
            },
        },
        "inputs": {
            "designRequirements": {
                "magnetizingInductance": {"nominal": 47e-6},
                "turnsRatios": [],
            },
            "operatingPoints": [],
        },
        "outputs": [
            {"coreLosses": {"coreLosses": 0.19,
                            "magneticFluxDensity": {"processed": {"peak": 0.12}}},
             "windingLosses": {"windingLosses": 0.53}},
        ],
    }


def _tas() -> dict:
    """A regulatable Kirchhoff-style buck TAS: thermal-stamped Q1/D1, a magnetic
    L1, a load-bearing operating point, loss budget + full-load sim result."""
    return {
        "inputs": {
            "designRequirements": {
                "efficiency": 0.9,
                "inputVoltage": {"minimum": 36, "nominal": 48, "maximum": 60},
                "outputs": [{"name": "out", "regulation": "voltage",
                             "voltage": {"nominal": 12.0}}],
                "switchingFrequency": {"nominal": 250000},
            },
            "operatingPoints": [{
                "name": "full_load", "inputVoltage": 48.0, "ambientTemperature": 25.0,
                "outputs": [{"name": "out", "power": 36.0}],
            }],
        },
        "duty": 0.25,
        "loss_budget": {
            "Q1_conduction": 0.0021, "Q1_switching": 0.152,
            "D1_conduction": 1.17, "D1_switching": 0.0,
            "L1_core": None, "L1_dcr": None,
        },
        "simulation_results": {
            "op0": {"vin": 48.0, "iin": 0.79, "vout": 12.0, "iout": 3.0,
                    "pin": 37.8, "pout": 36.0, "total_losses": 1.83, "efficiency": 0.9516},
        },
        "topology": {
            "interStageConnections": [],
            "stages": [
                {"name": "control", "circuit": {"components": [{"name": "U1"}],
                                                "connections": []}},
                {"name": "switchingCell", "circuit": {
                    "components": [
                        {"name": "Q1", "rth_ja": 40.0, "rth_jc": 0.85, "tj_max": 175.0,
                         "vds_rated": 100.0, "vds_stress": 60.0,
                         "selection_provenance": {"category": "mosfet", "mpn": "CSD19536",
                                                  "manufacturer": "Texas Instruments"}},
                        {"name": "L1", "isat": 8.0, "ipeak_worst": 3.6},
                        {"name": "D1", "rth_ja": 62.0, "rth_jc": 1.0, "tj_max": 150.0,
                         "vrrm_rated": 120.0, "v_reverse": 60.0,
                         "selection_provenance": {"category": "diode", "mpn": "STPS",
                                                  "manufacturer": "ST"}},
                    ],
                    "connections": [
                        {"name": "vin_net", "endpoints": [
                            {"component": "Q1", "pin": "drain"}, {"port": "vin"}]},
                        {"name": "sw_node", "endpoints": [
                            {"component": "Q1", "pin": "source"},
                            {"component": "L1", "pin": "primary_start"},
                            {"component": "D1", "pin": "cathode"}]},
                        {"name": "gnd_net", "endpoints": [
                            {"component": "D1", "pin": "anode"}, {"port": "gnd"}]},
                        {"name": "vout_net", "endpoints": [
                            {"component": "L1", "pin": "primary_end"}]},
                    ],
                }},
                {"name": "filter", "circuit": {
                    "components": [{"name": "Cout",
                                    "selection_provenance": {"category": "capacitor"}}],
                    "connections": [
                        {"name": "out", "endpoints": [
                            {"component": "Cout", "pin": "1"}, {"port": "in"}]},
                    ],
                }},
            ],
        },
    }


def _outcome() -> NS:
    return NS(
        pick=NS(topology=NS(name="buck"), main_magnetic=NS(mas=_mas(), scoring=0.3)),
        tas=_tas(),
        verdict_dict={"verdict": "pass", "summary": {"pass": 10, "fail": 0}, "checks": []},
        fsw_optimal=250000.0,
    )


def _fake_simulate_regulated(tas, target_vout, topology, *, fidelity=None, tol=0.01):
    """Deterministic regulated op: a load-dependent efficiency curve + tight
    regulation, reading the (scaled) load + Vin off the modified TAS copy."""
    op = tas["inputs"]["operatingPoints"][0]
    pout = sum(o["power"] for o in op["outputs"] if isinstance(o.get("power"), (int, float)))
    vin = op.get("inputVoltage", 48.0)
    frac = pout / 36.0
    eff = 0.90 + 0.055 * frac - 0.03 * frac * frac          # peaks mid-load
    eff -= 0.0008 * (vin - 48.0)                             # mild line dependence
    pin = pout / eff
    return {"regulated": True, "converged": True, "vout": float(target_vout),
            "pin": pin, "pout": pout, "efficiency": eff, "control": "duty", "value": 0.25}


@pytest.fixture
def patched_sim(monkeypatch):
    import heaviside.decomposer.kirchhoff_adapter as ka
    monkeypatch.setattr(ka, "simulate_regulated", _fake_simulate_regulated, raising=True)
    return ka


# ── model-level data ──────────────────────────────────────────────────────────

def test_efficiency_load_points_resim(patched_sim):
    m = ReportModel(_outcome())
    el = m.efficiency_load_points()
    pts = el["points"]
    assert len(pts) == 5                       # bounded to the 5 load fractions
    assert el["note"] is None                  # all converged
    fracs = [p["frac"] for p in pts]
    assert fracs == [0.2, 0.4, 0.6, 0.8, 1.0]
    # Real, load-dependent efficiency + measured Iout (= Pout/Vout).
    assert all(0.85 < p["eff"] < 1.0 for p in pts)
    assert pts[-1]["iout"] == pytest.approx(3.0, abs=0.05)
    assert pts[0]["iout"] == pytest.approx(0.6, abs=0.05)


def test_line_regulation_points_resim(patched_sim):
    m = ReportModel(_outcome())
    m.efficiency_load_points()                 # populates the full-load anchor
    lr = m.line_regulation_points()
    pts = lr["points"]
    assert [p["vin"] for p in pts] == [36.0, 48.0, 60.0]
    assert all(p["vout"] == pytest.approx(12.0, abs=0.05) for p in pts)


def test_resim_skipped_when_disabled(monkeypatch, patched_sim):
    monkeypatch.setenv("HEAVISIDE_REPORT_NO_RESIM", "1")
    m = ReportModel(_outcome())
    assert m.efficiency_load_points()["points"] == []
    assert m.line_regulation_points()["points"] == []


def test_loss_reconciliation_surfaces_delta():
    m = ReportModel(_outcome())
    recon = m.loss_reconciliation()
    assert recon is not None
    # Analyst total = semis (1.324) + MKF magnetic (0.19+0.53) ≈ 2.045.
    assert recon["analyst_total"] == pytest.approx(2.044, abs=0.02)
    assert recon["sim_total"] == pytest.approx(1.83, abs=0.01)
    assert recon["delta_w"] == pytest.approx(0.214, abs=0.02)
    assert recon["delta_pct"] == pytest.approx(11.7, abs=1.0)
    assert "different models" in recon["note"].lower()


def test_magnetic_bom_row():
    m = ReportModel(_outcome())
    rows = m.magnetic_bom_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["ref"] == "L1"
    assert "T 17/9.5/7.0" in row["summary"]
    assert "N=37" in row["summary"]


def test_thermal_rows_real_and_na():
    m = ReportModel(_outcome())
    th = m.thermal_rows()
    by_ref = {r["ref"]: r for r in th["rows"]}
    assert th["ambient_c"] == 25.0
    # D1: Tj = 1.17 * 62 + 25 = 97.54 °C, margin 52.46 °C to 150.
    assert by_ref["D1"]["tj"] == pytest.approx(97.54, abs=0.2)
    assert by_ref["D1"]["margin_c"] == pytest.approx(52.46, abs=0.2)
    # Q1: low loss -> cool junction, big margin.
    assert by_ref["Q1"]["tj"] == pytest.approx(31.17, abs=0.3)
    # L1 has no datasheet Rθ -> n/a, never fabricated.
    assert by_ref["L1"]["rth_ja"] is None
    assert by_ref["L1"]["tj"] is None
    assert th["note"] is not None


def test_schematic_rows_netlist():
    m = ReportModel(_outcome())
    rows = m.schematic_rows()
    by_ref = {r["ref"]: r for r in rows}
    assert set(by_ref) >= {"U1", "Q1", "L1", "D1", "Cout"}
    assert by_ref["Q1"]["type"] == "mosfet"
    assert ("drain", "vin_net") in by_ref["Q1"]["nets"]
    assert any(net == "sw_node" for _pin, net in by_ref["L1"]["nets"])


# ── rendered output (both renderers) ──────────────────────────────────────────

_PHASE2_HTML_SECTIONS = [
    "Efficiency & Regulation", "Efficiency vs Load", "Line Regulation",
    "Analyst vs Simulation Reconciliation", "Thermal (Junction Temperature)",
    "Schematic (Netlist)",
]
_PHASE2_TEX_SECTIONS = [
    r"Efficiency \& Regulation", "Efficiency vs Load", "Line Regulation",
    "Reconciliation", "Thermal (Junction Temperature)", "Schematic (Netlist)",
]


def test_render_html_phase2_sections(patched_sim):
    h = render_html(_outcome())
    for s in _PHASE2_HTML_SECTIONS:
        assert s in h, f"missing HTML section: {s}"
    assert "<svg" in h                         # efficiency-vs-load inline SVG
    assert "Custom magnetic" in h and "designed" in h
    assert "97.5" in h                          # D1 junction temperature


def test_render_latex_phase2_sections(patched_sim):
    tex = render_latex(_outcome())
    for s in _PHASE2_TEX_SECTIONS:
        assert s in tex, f"missing LaTeX section: {s}"
    assert "Custom magnetic" in tex
    assert r"\addplot" in tex                    # efficiency pgfplots curve


def test_phase2_sections_omitted_without_resim(monkeypatch):
    """No closed-loop sim -> the efficiency/regulation section is omitted (not
    fabricated); the non-sim Phase-2 sections still render."""
    monkeypatch.setenv("HEAVISIDE_REPORT_NO_RESIM", "1")
    h = render_html(_outcome())
    assert "Efficiency & Regulation" not in h
    assert "Thermal (Junction Temperature)" in h
    assert "Schematic (Netlist)" in h
