"""Kelvin deterministic-crossref PRIMITIVES — the single source of truth.

The Python scoring / param-check / stress / rescue functions delegate their
DECISION logic here; Kelvin (PyKelvin, C++) computes it. This is the cutover
seam: the Python files keep only glue (display strings, catalogue/network I/O)
and call through to Kelvin for every verdict/score/size decision.

No silent fallback (per the no-fallbacks rule): if PyKelvin can't be imported the
call raises KelvinUnavailable — build Kelvin and put PyKelvin on the path.
"""

from __future__ import annotations

import functools
import sys
from pathlib import Path
from typing import Any


class KelvinUnavailable(RuntimeError):
    """PyKelvin is not importable / not built — never a silent fallback to Python."""


@functools.lru_cache(maxsize=1)
def _pk():
    try:
        import PyKelvin  # noqa: PLC0415

        return PyKelvin
    except ImportError:
        # Co-dev convenience: add the sibling Kelvin build dir if present.
        build = Path(__file__).resolve().parents[2].parent / "Kelvin" / "build"
        if build.is_dir() and str(build) not in sys.path:
            sys.path.insert(0, str(build))
        try:
            import PyKelvin  # noqa: PLC0415

            return PyKelvin
        except ImportError as exc:  # pragma: no cover - environment-specific
            raise KelvinUnavailable(
                "PyKelvin not importable; build Kelvin (cmake --build) and put it on PYTHONPATH"
            ) from exc


# ── Thin pass-throughs (same signatures the Python callers expect) ────────────
def score_primary_value(category: str, original: float | None, substitute: float | None) -> dict | None:
    """{'verdict','penalty'} or None when the category has no primary-value spec."""
    return _pk().score_primary_value(category, original, substitute)


def over_dimensioning_penalty(required: float | None, actual: float | None, weight: float = 1.0) -> float:
    return _pk().over_dimensioning_penalty(required, actual, weight)


def evaluate_params(category: str, original: dict, substitute: dict) -> list[dict]:
    """List of {'name','verdict'} — one per parameter with data on a side."""
    return _pk().evaluate_params(category, original or {}, substitute or {})


def required_inductance(topology: str, spec: dict) -> float | None:
    return _pk().required_inductance(topology, spec)


def footprint_area_mm2(summary: dict) -> float:
    return _pk().footprint_area_mm2(summary)


def operating_point_magnetic_rescue(
    l_required: float, i_peak: float, i_rms: float | None, candidates: list[dict[str, Any]]
) -> dict | None:
    """{'summary','inductance'} of the right-sized pick, or None. The caller
    supplies the candidate pool (catalogue access stays in Python) and does the
    network MPN-existence check; Kelvin does the sizing/ranking."""
    return _pk().operating_point_magnetic_rescue(l_required, i_peak, i_rms, candidates)
