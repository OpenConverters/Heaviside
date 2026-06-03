"""Sourcing summary — read distributor cost/stock from TAS records.

The librarian imports ``distributorsInfo`` per MPN at TAS-import time
(cost, quantity, updatedAt). This module pulls those fields onto each
crossref row at no live-API cost, and computes a per-design BOM cost /
stock summary.

Ported from ``proteus.pipelines.sourcing``, adapted to use Heaviside's
TAS reader (``heaviside.catalogue._reader.iter_envelopes``) for MPN
lookups instead of ``proteus.catalogue.index``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# TAS data directory
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TAS_DATA_DEFAULT = _REPO_ROOT / "TAS" / "data"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _lookup_mpn_envelope(
    mpn: str,
    *,
    tas_data_dir: Path | None = None,
) -> Optional[dict[str, Any]]:
    """Find the raw TAS envelope for *mpn* across all NDJSON files.

    Returns the first matching envelope dict, or ``None``.
    """
    if not mpn:
        return None
    root = tas_data_dir or _TAS_DATA_DEFAULT
    if not root.is_dir():
        return None

    from heaviside.catalogue._reader import iter_envelopes

    for ndjson_file in root.glob("*.ndjson"):
        try:
            for _lineno, env in iter_envelopes(ndjson_file):
                for top_key in ("capacitor", "semiconductor", "resistor",
                                "magnetics", "magnetic"):
                    sub = env.get(top_key)
                    if not isinstance(sub, dict):
                        continue
                    for inner_key in (None, "mosfet", "diode", "igbt"):
                        record = sub if inner_key is None else sub.get(inner_key)
                        if not isinstance(record, dict):
                            continue
                        mi = record.get("manufacturerInfo")
                        if isinstance(mi, dict) and mi.get("reference") == mpn:
                            return env
        except Exception:
            continue
    return None


def _best_distributor(env: dict[str, Any]) -> Optional[dict]:
    """Pick the lowest-price distributor entry with non-zero stock.

    Walks common TAS envelope shapes to find ``distributorsInfo``.
    """
    # distributorsInfo can live at several nesting levels.
    dlist: list = []
    for top_key in ("capacitor", "semiconductor", "resistor",
                    "magnetics", "magnetic"):
        sub = env.get(top_key)
        if not isinstance(sub, dict):
            continue
        for inner_key in (None, "mosfet", "diode", "igbt"):
            record = sub if inner_key is None else sub.get(inner_key)
            if not isinstance(record, dict):
                continue
            mi = record.get("manufacturerInfo") or {}
            d = mi.get("distributorsInfo") or record.get("distributorsInfo") or []
            if d:
                dlist = d
                break
        if dlist:
            break

    if not dlist:
        return None

    candidates: list[tuple[float, int, dict]] = []
    for d in dlist:
        cost = d.get("cost") or d.get("price") or 0
        try:
            cost = float(cost)
        except (TypeError, ValueError):
            cost = 0.0
        qty = d.get("quantity") or d.get("stock") or 0
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            qty = 0
        if cost > 0:
            candidates.append((cost, qty, d))

    if not candidates:
        return None

    # Prefer in-stock at lowest price, then any at lowest price.
    in_stock = [c for c in candidates if c[1] > 0]
    pool = in_stock or candidates
    pool.sort(key=lambda c: c[0])
    return pool[0][2]


def _orig_cost(
    orig_pn: str,
    *,
    tas_data_dir: Path | None = None,
) -> Optional[float]:
    """Best-effort cost for the original part — checks if it is in TAS."""
    if not orig_pn:
        return None
    env = _lookup_mpn_envelope(orig_pn, tas_data_dir=tas_data_dir)
    if not env:
        return None
    d = _best_distributor(env)
    if not d:
        return None
    try:
        return float(d.get("cost") or 0) or None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def annotate_sourcing(
    crossref_components: list[dict],
    source_bom: list[dict],
    *,
    tas_data_dir: Path | None = None,
) -> dict:
    """Annotate crossref rows with distributor cost/stock from TAS.

    Mutates *crossref_components* in place: appends cost/stock info to
    each row's ``notes`` field (if available in TAS).

    Parameters
    ----------
    crossref_components : list[dict]
        Crossref output rows (each has ``substitute_pn``, ``status``, etc.).
    source_bom : list[dict]
        Original BOM rows (for original-part cost lookup).
    tas_data_dir : Path | None
        Override for the TAS data directory (for testing).

    Returns
    -------
    dict
        Per-design summary::

            {
              "rows_with_pricing": int,
              "total_qty_replaced": int,
              "bom_cost_orig_known": float,
              "bom_cost_sub": float,
              "delta_pct": float | None,
              "stock_warnings": list[str],
            }
    """
    src_by_first = {
        str(s.get("ref_des", "")).split(",")[0].strip(): s
        for s in (source_bom or [])
    }

    rows_priced = 0
    cost_orig_known = 0.0
    cost_sub = 0.0
    qty_total = 0
    warns: list[str] = []

    for c in crossref_components:
        if c.get("status") not in ("recommended", "partial"):
            continue
        pn = (c.get("substitute_pn") or "").strip()
        if not pn or pn == "no_substitute":
            continue
        ref = str(c.get("ref_des", "")).split(",")[0].strip()
        n_in_group = max(len(str(c.get("ref_des", "")).split(",")), 1)

        env = _lookup_mpn_envelope(pn, tas_data_dir=tas_data_dir)
        if not env:
            continue
        d = _best_distributor(env)
        if not d:
            continue

        try:
            sub_cost = float(d.get("cost") or 0)
        except (TypeError, ValueError):
            sub_cost = 0.0
        try:
            sub_qty = int(d.get("quantity") or 0)
        except (TypeError, ValueError):
            sub_qty = 0

        rows_priced += 1
        cost_sub += sub_cost * n_in_group
        qty_total += n_in_group

        # Stock warning.
        if sub_qty == 0:
            warns.append(f"{ref}: substitute {pn} has 0 stock at {d.get('name', 'distributor')}")
        elif sub_qty < n_in_group * 100:
            warns.append(f"{ref}: substitute {pn} stock low ({sub_qty} units)")

        # Annotate the row.
        notes = c.get("notes") or ""
        tag = f"[sourcing] {d.get('name', 'dist')} ${sub_cost:.4f}/ea, stock {sub_qty}"
        if "[sourcing]" not in notes:
            c["notes"] = (notes + " " + tag).strip()

        # Original cost if known.
        src = src_by_first.get(ref) or {}
        oc = _orig_cost(src.get("original_pn") or "", tas_data_dir=tas_data_dir)
        if oc:
            cost_orig_known += oc * n_in_group

    delta_pct: Optional[float] = None
    if cost_orig_known > 0:
        delta_pct = (cost_sub - cost_orig_known) / cost_orig_known * 100.0

    return {
        "rows_with_pricing": rows_priced,
        "total_qty_replaced": qty_total,
        "bom_cost_orig_known": round(cost_orig_known, 4),
        "bom_cost_sub": round(cost_sub, 4),
        "delta_pct": round(delta_pct, 2) if delta_pct is not None else None,
        "stock_warnings": warns,
    }


__all__ = ["annotate_sourcing"]
