"""Report waveform SVG + WeasyPrint PDF deliverable."""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from heaviside.report.html import _try_waveforms, _waveform_svg, render_html


def _mas_with_waveforms(n=300):
    t = [k * 1e-8 for k in range(n)]
    cur = [3.0 + 0.4 * ((k % 100) / 100) for k in range(n)]
    volt = [12.0 if k % 100 < 20 else -3.3 for k in range(n)]
    return {
        "magnetic": {
            "core": {"name": "P 14/8"},
            "coil": {"functionalDescription": [{"name": "Primary", "numberTurns": 12}]},
        },
        "inputs": {
            "operatingPoints": [
                {
                    "name": "nom",
                    "excitationsPerWinding": [
                        {
                            "current": {"waveform": {"time": t, "data": cur}},
                            "voltage": {"waveform": {"time": t, "data": volt}},
                        }
                    ],
                }
            ]
        },
    }


def _waveforms_from_mas(mas):
    """Extract waveform list from a MAS dict (test helper)."""
    return _try_waveforms(mas)


def _outcome(mas):
    return NS(
        pick=NS(topology=NS(name="buck"), main_magnetic=NS(mas=mas, scoring=0.35)),
        tas={"topology": {"stages": []}},
        verdict_dict={"verdict": "pass", "summary": {"pass": 10, "fail": 0}, "checks": []},
        gatekeeper=None,
        fsw_optimal=132900.0,
        diagnostics=(),
    )


def test_waveform_svg_has_traces():
    wfs = _waveforms_from_mas(_mas_with_waveforms())
    assert wfs, "expected waveforms extracted from MAS"
    svg = _waveform_svg(wfs)
    assert "<svg" in svg
    assert svg.count("<polyline") == 2  # current + voltage


def test_waveform_svg_empty_without_data():
    assert _waveform_svg(_waveforms_from_mas({"inputs": {"operatingPoints": []}})) == ""
    assert _waveform_svg([]) == ""


def test_report_includes_waveform_section():
    html = render_html(_outcome(_mas_with_waveforms()))
    # Unified report: the waveforms section is now titled "Operating Waveforms"
    # (matching the LaTeX/PDF report); pipeline-internal sections are gone.
    assert "Operating Waveforms" in html and "<svg" in html
    assert "Realism Checks" not in html and "Gatekeeper" not in html


def test_phase2_sections_omitted_for_minimal_outcome():
    """A minimal outcome (empty stages, no regulatable TAS, no thermal data)
    must OMIT every Phase-2 section rather than fabricate one — the report still
    renders the Phase-1 waveform section."""
    html = render_html(_outcome(_mas_with_waveforms()))
    assert "Operating Waveforms" in html  # Phase-1 still present
    for section in (
        "Efficiency & Regulation",
        "Thermal (Junction Temperature)",
        "Schematic (Netlist)",
        "Analyst vs Simulation Reconciliation",
    ):
        assert section not in html, f"Phase-2 section should be omitted: {section}"


def test_report_renders_to_pdf():
    pytest.importorskip("weasyprint")
    from heaviside.stages.reporter import design_pdf

    pdf = design_pdf(_outcome(_mas_with_waveforms()))
    assert pdf[:4] == b"%PDF" and len(pdf) > 2000


def test_html_to_pdf_basic():
    pytest.importorskip("weasyprint")
    from heaviside.stages.reporter import html_to_pdf

    pdf = html_to_pdf("<html><body><h1>Hello</h1></body></html>")
    assert pdf[:4] == b"%PDF"
