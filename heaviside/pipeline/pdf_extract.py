"""PDF text extraction using pdfplumber.

The CRE pipeline sends the extracted text to an LLM agent (reverse-
engineer, competitor) which reads BOM tables, specs, and schematics
from the raw text. Each PDF has a unique layout — the LLM handles
this naturally; regex/heuristic table parsers do not.

  * ``extract_pdf_text`` — full-text extraction with page markers.
    This is the primary entry point. The CRE pipeline sends this
    text to the LLM.
  * ``extract_pdf_tables`` — pdfplumber's built-in table detector.
    Available as a fallback but NOT used by the pipeline — LLM
    table reading is more robust across diverse PDF layouts.

Per CLAUDE.md: no silent fallbacks.  ``extract_pdf_text`` raises on
missing files or unreadable PDFs rather than returning empty strings.
No character cap — eval-board BOMs can span many pages and truncation
causes downstream extraction failures.

Requires ``pdfplumber>=0.11`` (listed in ``pyproject.toml``).
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber


def extract_pdf_text(path: Path) -> str:
    """Extract all text from a PDF, page by page.

    Parameters
    ----------
    path : Path
        Filesystem path to the PDF file.

    Returns
    -------
    str
        Concatenated text with ``--- Page N ---`` markers between pages.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist or is not a file.
    RuntimeError
        If pdfplumber cannot open the file or extraction yields no text
        from any page (corrupt / image-only PDF).
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {path}")

    try:
        parts: list[str] = []
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text()
                if page_text:
                    parts.append(f"--- Page {i + 1} ---\n{page_text}")
    except Exception as exc:
        raise RuntimeError(f"pdfplumber failed to read {path}: {exc}") from exc

    if not parts:
        raise RuntimeError(
            f"PDF extraction yielded no text from any page: {path} "
            "(file may be image-only or corrupt)"
        )

    return "\n\n".join(parts)


def extract_pdf_tables(path: Path) -> list[list[list[str]]]:
    """Extract tables from every page of a PDF.

    Parameters
    ----------
    path : Path
        Filesystem path to the PDF file.

    Returns
    -------
    list[list[list[str]]]
        Outer list: one entry per table found (across all pages).
        Middle list: rows within a table.
        Inner list: cell strings within a row.  ``None`` cells from
        pdfplumber are normalised to ``""``.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist or is not a file.
    RuntimeError
        If pdfplumber cannot open the file.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {path}")

    try:
        tables: list[list[list[str]]] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                for raw_table in page.extract_tables() or []:
                    normalised = [
                        [str(cell) if cell is not None else "" for cell in row]
                        for row in raw_table
                    ]
                    if normalised:
                        tables.append(normalised)
    except Exception as exc:
        raise RuntimeError(f"pdfplumber table extraction failed on {path}: {exc}") from exc

    return tables


__all__ = ["extract_pdf_text", "extract_pdf_tables"]
