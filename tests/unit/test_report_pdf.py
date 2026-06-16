"""Report waveform SVG + WeasyPrint PDF deliverable."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from heaviside.report.html import _waveform_svg, render_html


def _mas_with_waveforms(n=300):
    t = [k * 1e-8 for k in range(n)]
    cur = [3.0 + 0.4 * ((k % 100) / 100) for k in range(n)]
    volt = [12.0 if k % 100 < 20 else -3.3 for k in range(n)]
    return {
        "magnetic": {"core": {"name": "P 14/8"},
                     "coil": {"functionalDescription": [{"name": "Primary", "numberTurns": 12}]}},
        "inputs": {"operatingPoints": [{"name": "nom", "excitationsPerWinding": [
            {"current": {"waveform": {"time": t, "data": cur}},
             "voltage": {"waveform": {"time": t, "data": volt}}}]}]},
    }


def _outcome(mas):
    return NS(
        pick=NS(topology=NS(name="buck"), main_magnetic=NS(mas=mas, scoring=0.35)),
        tas={"topology": {"stages": []}},
        verdict_dict={"verdict": "pass", "summary": {"pass": 10, "fail": 0}, "checks": []},
        gatekeeper=None, fsw_optimal=132900.0, diagnostics=(),
    )


def test_waveform_svg_has_traces():
    svg = _waveform_svg(_mas_with_waveforms())
    assert svg.startswith("<h2>Simulation waveforms") and "<svg" in svg
    assert svg.count("<polyline") == 2  # current + voltage


def test_waveform_svg_empty_without_data():
    assert _waveform_svg({"inputs": {"operatingPoints": []}}) == ""
    assert _waveform_svg({}) == ""


def test_report_includes_waveform_section():
    html = render_html(_outcome(_mas_with_waveforms()))
    assert "Simulation waveforms" in html and "<svg" in html


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
