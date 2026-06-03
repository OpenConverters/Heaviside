"""Verdict parsing for CRE and CR review stages.

Single source of truth for extracting a structured verdict (APPROVED /
REJECTED / PROCEED / BLOCK / UNKNOWN) from LLM output. Used by both
the competitor-reverse-engineer and cross-reference review pipelines.

Parsing strategy (in precedence order):
  1. XML ``<verdict>`` tag — word immediately after opening tag OR before
     closing tag (handles compact and multi-line forms).
  2. Keyword match: 'DESIGN APPROVED' / 'DESIGN REJECTED' (last
     occurrence wins when both appear).
  3. Keyword match: 'PROCEED' / 'BLOCK' (for CRE gate verdicts).
  4. Bare APPROVED / REJECTED keyword (last occurrence).
  5. Return ``'UNKNOWN'``.

Ported from ``proteus.pipelines.reverse_engineer._parse_verdict`` and
``proteus.pipelines.crossref._parse_verdict``.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Pre-compiled patterns
# ---------------------------------------------------------------------------

_VERDICT_TAG_OPEN_RE = re.compile(
    r"<verdict>\s*(APPROVED|REJECTED|PROCEED|BLOCK)", re.IGNORECASE
)
_VERDICT_TAG_CLOSE_RE = re.compile(
    r"(APPROVED|REJECTED|PROCEED|BLOCK)\s*</verdict>", re.IGNORECASE
)
_KW_DESIGN_APPROVED_RE = re.compile(r"\bDESIGN\s+APPROVED\b", re.IGNORECASE)
_KW_DESIGN_REJECTED_RE = re.compile(r"\bDESIGN\s+REJECTED\b", re.IGNORECASE)
_KW_PROCEED_RE = re.compile(r"\bPROCEED\b", re.IGNORECASE)
_KW_BLOCK_RE = re.compile(r"\bBLOCK\b", re.IGNORECASE)
_KW_APPROVED_RE = re.compile(r"\bAPPROVED\b", re.IGNORECASE)
_KW_REJECTED_RE = re.compile(r"\bREJECTED\b", re.IGNORECASE)


def parse_verdict(text: str) -> str:
    """Return ``'APPROVED'``, ``'REJECTED'``, ``'PROCEED'``, ``'BLOCK'``, or ``'UNKNOWN'``.

    Handles multiple output formats from various LLM providers:
      - ``<verdict>APPROVED</verdict>`` (compact)
      - ``<verdict>REJECTED\\n\\n...explanation...</verdict>`` (multi-line)
      - ``DESIGN APPROVED`` / ``DESIGN REJECTED`` keywords
      - ``PROCEED`` / ``BLOCK`` keywords (CRE gate)
      - Bare ``APPROVED`` / ``REJECTED`` keywords

    When multiple conflicting keywords appear, the **last** occurrence
    wins (models that deliberate often flip mid-response and the final
    answer is canonical).
    """
    text = text or ""

    # 1. Word immediately after <verdict> opening tag.
    open_matches = _VERDICT_TAG_OPEN_RE.findall(text)
    if open_matches:
        return open_matches[-1].upper()

    # 2. Word just before </verdict> closing tag.
    close_matches = _VERDICT_TAG_CLOSE_RE.findall(text)
    if close_matches:
        return close_matches[-1].upper()

    # 3. 'DESIGN APPROVED' / 'DESIGN REJECTED' keyword pair.
    approved_pos = [m.start() for m in _KW_DESIGN_APPROVED_RE.finditer(text)]
    rejected_pos = [m.start() for m in _KW_DESIGN_REJECTED_RE.finditer(text)]
    if approved_pos or rejected_pos:
        last_approved = approved_pos[-1] if approved_pos else -1
        last_rejected = rejected_pos[-1] if rejected_pos else -1
        return "APPROVED" if last_approved > last_rejected else "REJECTED"

    # 4. PROCEED / BLOCK (CRE gate verdicts).
    proceed_pos = [m.start() for m in _KW_PROCEED_RE.finditer(text)]
    block_pos = [m.start() for m in _KW_BLOCK_RE.finditer(text)]
    if proceed_pos or block_pos:
        last_proceed = proceed_pos[-1] if proceed_pos else -1
        last_block = block_pos[-1] if block_pos else -1
        return "PROCEED" if last_proceed > last_block else "BLOCK"

    # 5. Bare APPROVED / REJECTED fallback.
    approved_pos2 = [m.start() for m in _KW_APPROVED_RE.finditer(text)]
    rejected_pos2 = [m.start() for m in _KW_REJECTED_RE.finditer(text)]
    if approved_pos2 or rejected_pos2:
        last_approved2 = approved_pos2[-1] if approved_pos2 else -1
        last_rejected2 = rejected_pos2[-1] if rejected_pos2 else -1
        return "APPROVED" if last_approved2 > last_rejected2 else "REJECTED"

    return "UNKNOWN"


__all__ = ["parse_verdict"]
