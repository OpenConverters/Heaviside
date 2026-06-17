"""mpn_verify — does this MPN exist in TAS, and what is it?

Pure-Python stage (no LLM): the single "is this a real catalogue part"
check shared by RE MPN verification and the CR ``component_exists`` tool.
Maps a PEAS category to its TAS file(s), reuses
``librarian.tas.component_exists`` for the boolean, and returns the PEAS
envelope when found.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# PEAS category -> TAS category file stem(s)
_PEAS_TO_TAS: dict[str, tuple[str, ...]] = {
    "capacitor": ("capacitors",),
    "resistor": ("resistors",),
    "magnetic": ("magnetics",),
    "controller": ("controllers",),
    "semiconductor": ("mosfets", "diodes", "igbts"),
}
_ALL_TAS = ("capacitors", "resistors", "magnetics", "mosfets", "diodes", "igbts", "controllers")


@dataclass
class MpnVerification:
    mpn: str
    exists: bool
    tas_category: str | None = None  # the TAS file it was found in
    env: dict[str, Any] | None = None  # the PEAS/TAS envelope, when found


def _tas_categories(category: str | None) -> tuple[str, ...]:
    if category is None:
        return _ALL_TAS
    return _PEAS_TO_TAS.get(category, ())


def verify_mpn(mpn: str, *, category: str | None = None) -> MpnVerification:
    """Verify ``mpn`` against TAS. ``category`` is an optional PEAS category
    to narrow the search; without it, all component files are searched.
    Returns existence + the TAS file + the envelope (when found)."""
    from heaviside.librarian.tas import component_exists

    if not mpn or not str(mpn).strip():
        return MpnVerification(mpn=mpn, exists=False)
    cats = _tas_categories(category)
    if not cats:
        raise ValueError(f"verify_mpn: unknown PEAS category {category!r}")
    for tas_cat in cats:
        try:
            if component_exists(tas_cat, mpn):
                return MpnVerification(
                    mpn=mpn, exists=True, tas_category=tas_cat,
                    env=_find_env(tas_cat, mpn),
                )
        except Exception:
            continue
    return MpnVerification(mpn=mpn, exists=False)


def _find_env(tas_cat: str, mpn: str) -> dict[str, Any] | None:
    """Return the full envelope for ``mpn`` in ``tas_cat`` (best-effort)."""
    import json
    from pathlib import Path

    from heaviside.librarian import safe_access as _sa

    path = Path(_sa.TAS_DATA_DIR) / f"{tas_cat}.ndjson"
    if not path.exists():
        return None
    target = mpn.upper()
    for line in path.open(encoding="utf-8"):
        if not line.strip():
            continue
        if target not in line.upper():  # cheap pre-filter
            continue
        env = json.loads(line)
        body = env
        for k in ("semiconductor", "capacitor", "resistor", "magnetic", "controller"):
            inner = body.get(k) if isinstance(body, dict) else None
            if isinstance(inner, dict):
                body = inner
                break
        mi = body.get("manufacturerInfo", {}) if isinstance(body, dict) else {}
        di = mi.get("datasheetInfo", {})
        pn = (mi.get("reference") or di.get("part", {}).get("partNumber") or "")
        if str(pn).upper() == target:
            return env
    return None
