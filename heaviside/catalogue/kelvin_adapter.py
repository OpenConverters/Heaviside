"""Heaviside <-> Kelvin switchover seam (plan phase 4).

Kelvin (PyKelvin) is the shared C++ selector that replaces this package's Python `selector.py`
internals. This adapter is the single call site HS routes selection through at switchover; it
keeps the LLM chooser (`stages/component_match.select_candidate`) and the BOM stamping in HS —
only the deterministic filter/rank + the requirements->constraints mapping move into Kelvin.

Not wired into the live pipeline yet: the switchover is gated on the differential fuzzer
(`heaviside/tools/diff_kelvin.py`) staying green in CI for a release, then deleting selector.py's
bodies (needs sign-off). Until then this is opt-in and the Python selector remains canonical.

Environment:
  PyKelvin on PYTHONPATH; HEAVISIDE_TAS_DATA_DIR -> data dir; KELVIN_INDEX_DIR -> shard cache.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any


class KelvinUnavailable(RuntimeError):
    """PyKelvin is not importable / not built (never a silent fallback to the Python selector)."""


@functools.lru_cache(maxsize=1)
def _engine():
    try:
        import PyKelvin  # noqa: PLC0415
    except ImportError as exc:  # build it — do not silently fall back
        raise KelvinUnavailable(
            "PyKelvin not importable; build Kelvin (cmake --build) and put it on PYTHONPATH"
        ) from exc
    data = os.environ.get("HEAVISIDE_TAS_DATA_DIR")
    if not data:
        data = str(Path(__file__).resolve().parents[2] / "TAS" / "data")
    cache = os.environ.get("KELVIN_INDEX_DIR", str(Path.home() / ".kelvin" / "index"))
    Path(cache).mkdir(parents=True, exist_ok=True)
    return PyKelvin, PyKelvin.Engine(data, cache, True)


def select(category: str, design_requirements: dict[str, Any], options: dict[str, Any] | None = None
           ) -> dict[str, Any]:
    """Return Kelvin's SelectionResult dict (see Kelvin/docs/CONTRACT.md). Raises PyKelvin
    exceptions (NoCandidates / InvalidOptions) unchanged so HS can map them to SelectionError /
    KirchhoffFillError exactly as today."""
    _, eng = _engine()
    return eng.select(category, design_requirements, options or {})


def select_components(tas: dict[str, Any], options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Walk a TAS document and select per component (the same authority KH uses)."""
    _, eng = _engine()
    return eng.select_components(tas, options or {})


def cross_reference(
    category: str,
    original: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    original_verified: bool = True,
    max_results: int = 25,
) -> dict[str, Any]:
    """Deterministic cross-reference ranker (Kelvin C++). Given an ORIGINAL part's
    spec dict and category-appropriate candidate spec dicts (SI base units),
    returns ``{category, original_verified, candidates:[{mpn, status, penalty,
    params, ...}]}`` ranked best-first with the honesty gates applied.

    This is the shared selection authority: Kirchhoff consumes ``candidates[]``
    directly (program-only); Heaviside runs its LLM chooser over the same list
    (program + LLM). No LLM ever enters Kelvin. ``original_verified=False`` tells
    the ranker the original's specs weren't resolved, so nothing is a clean
    'recommended' (capped at 'partial')."""
    pk, _ = _engine()
    return pk.cross_reference(
        category,
        original,
        candidates,
        {"original_verified": original_verified, "max_results": max_results},
    )


def chooser_candidates(result: dict[str, Any], limit: int = 25) -> list[dict[str, Any]]:
    """Project a SelectionResult into the compact records HS's LLM chooser
    (`component_match.select_candidate`) consumes: pick-among a ranked list, never invent."""
    out = []
    for c in result.get("candidates", [])[:limit]:
        out.append({"index": len(out), "mpn": c["mpn"], "manufacturer": c.get("manufacturer"),
                    "margins": c.get("margins"), "evidence": c.get("evidence")})
    return out
