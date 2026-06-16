"""cross_check — triangulate INDEPENDENT estimators (master-plan step B7).

A design number is only corroborated when two *genuinely independent* methods
agree. The trap (called out in the master plan, §3 I3): the analyst's magnetic
loss is **read from MKF's MAS**, so "analyst magnetic loss vs MKF magnetic loss"
is the same number compared to itself — a vacuous check that manufactures false
confidence. This stage only triangulates estimators that are actually
independent, with a per-quantity tolerance, and FAILs (surfaces) on
disagreement rather than averaging it away.

Independent pairs we DO triangulate:
* **efficiency** — analyst closed-form vs ngspice sim (Pin/Pout).
* **total_loss** — analyst loss sum vs sim Pin−Pout.
* **tj** — analyst Rth·P vs any sim/thermal estimate.

The realism gate gains an ``estimators_agree`` check that reads the recorded
disagreements (``tas["cross_check"]``) and FAILs on any beyond tolerance.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Per-quantity relative tolerances for "independent estimators agree".
# ZVS-resonant efficiency gets a wider band because the analyst models P_sw=0
# while the sim sees real (small) transition loss — a legitimate, bounded gap.
DEFAULT_TOLERANCES: dict[str, float] = {
    "efficiency": 0.03,      # 3 percentage-points-ish, relative
    "efficiency_zvs": 0.06,  # wider for ZVS-resonant (analyst P_sw=0 vs sim)
    "total_loss": 0.25,      # loss models are coarser; 25% relative
    "tj": 0.15,              # thermal 15% relative
}

# Estimator sources that are NOT mutually independent — comparing them is
# vacuous (same underlying number). The guard rejects such a pairing loudly.
_NON_INDEPENDENT = frozenset({
    frozenset({"analyst_magnetic_loss", "mkf_magnetic_loss"}),
})


class CrossCheckError(ValueError):
    """A cross-check was set up wrong — e.g. two non-independent estimators were
    offered as if independent. Raised so a vacuous check never silently passes."""


@dataclass(frozen=True, slots=True)
class Estimate:
    quantity: str  # "efficiency" | "total_loss" | "tj"
    value: float
    source: str    # e.g. "analyst" | "ngspice_sim"


@dataclass(frozen=True, slots=True)
class Disagreement:
    quantity: str
    sources: tuple[str, str]
    values: tuple[float, float]
    relative_diff: float
    tolerance: float

    @property
    def agree(self) -> bool:
        return self.relative_diff <= self.tolerance


def _relative_diff(a: float, b: float) -> float:
    scale = max(abs(a), abs(b))
    return abs(a - b) / scale if scale > 0 else 0.0


def triangulate(
    estimates: Sequence[Estimate],
    *,
    tolerances: Mapping[str, float] | None = None,
    zvs: bool = False,
) -> list[Disagreement]:
    """Compare every pair of estimates for the same quantity from DIFFERENT
    sources, returning a ``Disagreement`` per pair.

    Raises :class:`CrossCheckError` if a pair is non-independent (e.g.
    analyst-vs-MKF magnetic loss) — such a comparison is vacuous and must not be
    presented as corroboration."""
    tol = {**DEFAULT_TOLERANCES, **(tolerances or {})}
    by_q: dict[str, list[Estimate]] = {}
    for e in estimates:
        by_q.setdefault(e.quantity, []).append(e)

    out: list[Disagreement] = []
    for quantity, group in by_q.items():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if a.source == b.source:
                    continue  # same method twice — not independent
                if frozenset({a.source, b.source}) in _NON_INDEPENDENT:
                    raise CrossCheckError(
                        f"{a.source} and {b.source} are not independent estimators "
                        f"of {quantity!r} (same underlying MKF number) — comparing "
                        f"them is vacuous; remove one or use a real second method."
                    )
                key = "efficiency_zvs" if (quantity == "efficiency" and zvs) else quantity
                t = tol.get(key, tol.get(quantity, 0.10))
                out.append(Disagreement(
                    quantity=quantity, sources=(a.source, b.source),
                    values=(a.value, b.value),
                    relative_diff=_relative_diff(a.value, b.value), tolerance=t,
                ))
    return out


def all_agree(disagreements: Sequence[Disagreement]) -> bool:
    return all(d.agree for d in disagreements)


def to_record(disagreements: Sequence[Disagreement]) -> dict[str, Any]:
    """Serialise for stamping onto ``tas["cross_check"]`` (the realism gate's
    ``estimators_agree`` check reads this)."""
    return {
        "all_agree": all_agree(disagreements),
        "comparisons": [
            {"quantity": d.quantity, "sources": list(d.sources), "values": list(d.values),
             "relative_diff": round(d.relative_diff, 4), "tolerance": d.tolerance,
             "agree": d.agree}
            for d in disagreements
        ],
    }
