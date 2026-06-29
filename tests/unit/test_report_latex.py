"""LaTeX/PDF power-electronics design report (heaviside.report.latex).

``render_latex`` is pure string assembly and runs everywhere; the actual
``pdflatex`` compile is gated behind ``shutil.which("pdflatex")`` so CI without
a LaTeX toolchain still passes.
"""
from __future__ import annotations

import shutil

import pytest

from heaviside.pipeline.converter_designer import design_converter
from heaviside.report.latex import LatexCompileError, render_latex, render_pdf

# Section titles every report must carry (the power-electronics structure).
_SECTIONS = [
    "Key Specifications",
    "Theory of Operation",
    "Design Calculations",
    "Magnetics Design",
    "Bill of Materials",
    "Operating Waveforms",
    "Power-Loss Budget",
    "Design Margins",
]

_BUCK_SPEC = {
    "inputVoltage": {"minimum": 36, "nominal": 48, "maximum": 60},
    "efficiency": 0.9,
    "currentRippleRatio": 0.3,
    "operatingPoints": [{
        "inputVoltage": 48, "switchingFrequency": 250000,
        "outputVoltages": [12], "outputCurrents": [3],
    }],
}

_PUSH_PULL_SPEC = {
    "inputVoltage": {"minimum": 36, "nominal": 48, "maximum": 60},
    "efficiency": 0.9,
    "currentRippleRatio": 0.4,
    "operatingPoints": [{
        "inputVoltage": 48, "switchingFrequency": 150000,
        "outputVoltages": [12], "outputCurrents": [5],
    }],
}


@pytest.fixture(scope="module")
def buck_design():
    return design_converter("buck", _BUCK_SPEC, use_llm=False, with_reviewers=False)


@pytest.fixture(scope="module")
def push_pull_design():
    return design_converter("push_pull", _PUSH_PULL_SPEC, use_llm=False, with_reviewers=False)


def test_render_latex_buck_has_sections(buck_design):
    tex = render_latex(buck_design)
    assert tex.strip()
    assert tex.startswith(r"\documentclass")
    assert tex.rstrip().endswith(r"\end{document}")
    for title in _SECTIONS:
        assert title in tex, f"missing section: {title}"
    # Real numbers, not placeholders.
    # The cover carries the topology label. Buck is realized diode-rectified
    # (not synchronous) by the Kirchhoff path — see commit "label buck
    # accurately" — so the report says "Buck DC-DC Converter", not "Synchronous".
    assert "Buck DC-DC Converter" in tex
    assert "48" in tex and "12" in tex            # Vin / Vout
    assert "Texas Instruments" in tex or "CSD" in tex  # real BOM part
    # Buck is non-isolated and uses an inductor (not a transformer).
    assert "Inductor" in tex
    assert r"\begin{tikzpicture}" in tex          # block diagram / plots present


def test_render_latex_transformer_has_magnetics(push_pull_design):
    tex = render_latex(push_pull_design)
    for title in _SECTIONS:
        assert title in tex, f"missing section: {title}"
    # A real transformer design exercises the turns-ratio + multi-winding path.
    assert "Push-Pull" in tex
    assert "Transformer" in tex
    assert "turns" in tex.lower()


def test_dropped_pipeline_sections_absent(buck_design):
    """The new report must NOT carry the pipeline-internal framing."""
    tex = render_latex(buck_design)
    assert "Frequency Sweep" not in tex
    assert "Realism Check" not in tex
    assert "Gatekeeper" not in tex
    assert "Diagnostics" not in tex


def test_render_latex_accepts_outcome(buck_design):
    """The renderer also accepts a bare DesignOutcome (legacy entry point)."""
    tex = render_latex(buck_design.outcome)
    assert tex.startswith(r"\documentclass")
    assert "Key Specifications" in tex


@pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")
def test_render_pdf_buck_compiles(buck_design, tmp_path):
    out = render_pdf(buck_design, tmp_path / "buck.pdf")
    assert out.exists()
    data = out.read_bytes()
    assert data[:4] == b"%PDF"
    assert len(data) > 10_000


def test_render_pdf_raises_without_pdflatex(buck_design, tmp_path, monkeypatch):
    """A clear error (not a silent fallback) when pdflatex is missing."""
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(LatexCompileError):
        render_pdf(buck_design, tmp_path / "x.pdf")
