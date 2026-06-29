"""reporter — render a design into a deliverable PDF.

The HTML report (``heaviside.report.render_html``) carries the power-electronics
design document — key specs, magnetics, BOM, inline-SVG operating waveforms, the
power-loss budget and design margins. This stage turns that HTML into a PDF via
WeasyPrint (pure-Python HTML/CSS → PDF; the SVG plot renders natively, no
browser needed).
"""
from __future__ import annotations

from typing import Any


class ReporterError(RuntimeError):
    """Raised when PDF rendering fails (e.g. WeasyPrint not installed)."""


def html_to_pdf(html: str, *, base_url: str | None = None) -> bytes:
    """Render an HTML string to PDF bytes via WeasyPrint."""
    try:
        import weasyprint
    except ImportError as exc:  # pragma: no cover
        raise ReporterError(
            "WeasyPrint is not installed — `pip install weasyprint` "
            "(needs system pango/cairo)."
        ) from exc
    try:
        return weasyprint.HTML(string=html, base_url=base_url).write_pdf()
    except Exception as exc:
        raise ReporterError(f"PDF render failed: {type(exc).__name__}: {exc}") from exc


def design_pdf(outcome: Any) -> bytes:
    """Render a ``DesignOutcome`` (or anything ``render_html`` accepts) to PDF."""
    from heaviside.report import render_html

    return html_to_pdf(render_html(outcome))
