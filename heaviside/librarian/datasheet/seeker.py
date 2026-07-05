"""Datasheet-seeker cache — sourced specs for out-of-DB cross-reference originals.

The `datasheet-seeker` agent (Haiku + web) reads a part's real datasheet and
returns its electrical specs. Those land here, in a small on-disk cache keyed by
MPN, which the cross-reference param-check consults BEFORE the deterministic
datasheet fetch. This gives the tool the same advantage a senior FAE has — it
pulls the original's datasheet — without writing a full schema envelope into the
shared DB (which the nightly re-fetch would race) and without a live web call in
the headless pipeline.

Every cached value is grounded in a fetched datasheet by the seeker agent (no
fabrication); a field the datasheet lacked is simply absent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _cache_path() -> Path:
    p = os.environ.get("HEAVISIDE_SEEKER_CACHE")
    if p:
        return Path(p)
    return Path.home() / ".heaviside" / "seeker_cache.json"


def _load() -> dict[str, Any]:
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _key(category: str, mpn: str) -> str:
    return f"{category}:{str(mpn).strip().lower()}"


def read(category: str, mpn: str) -> dict[str, Any] | None:
    """Return the seeker-sourced summary dict for (category, mpn), or None."""
    if not mpn:
        return None
    return _load().get(_key(category, mpn))


def write(category: str, mpn: str, summary: dict[str, Any]) -> None:
    """Persist a summary-keyed spec dict for (category, mpn)."""
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load()
    data[_key(category, mpn)] = summary
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


def magnetic_summary_from_seeker(specs: dict[str, Any]) -> dict[str, Any]:
    """Map a datasheet-seeker magnetic JSON to the _summarize_candidate keys the
    gates read. Isat uses the CONSERVATIVE lowest-drop value (10% preferred);
    rated current is the standard IR when the seeker marked it so. Absent fields
    are omitted — never guessed."""
    out: dict[str, Any] = {"mpn": specs.get("mpn")}
    L = specs.get("inductance_H")
    if isinstance(L, (int, float)):
        out["inductance"] = float(L)
        out["value_si"] = float(L)
    isat = specs.get("isat_A") or {}
    for k in ("drop_10pct", "drop_20pct", "drop_30pct"):
        v = isat.get(k)
        if isinstance(v, (int, float)):
            out["saturation_current"] = float(v)
            out["saturation_current_drop_pct"] = int(k.split("_")[1].rstrip("pct"))
            break
    rc = specs.get("rated_current_A")
    if isinstance(rc, (int, float)):
        out["rated_current"] = float(rc)
    dcr = specs.get("dcr_ohm") or {}
    # gate uses the max (worst-case) DCR when present, else typ.
    for k in ("max", "typ"):
        v = dcr.get(k)
        if isinstance(v, (int, float)):
            out["dcr"] = float(v)
            break
    dims = specs.get("dimensions_mm") or {}
    if any(isinstance(dims.get(k), (int, float)) for k in ("length", "width", "height")):
        out["dimensions_mm"] = {
            k: dims.get(k) for k in ("length", "width", "height") if isinstance(dims.get(k), (int, float))
        }
    return out
