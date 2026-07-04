"""Deterministic value-integrity invariants for a cross-reference result.

This is the machine-checkable half of the FAE evaluation: given a finished
crossref result (the rows the customer sees) and a per-ref answer key, it
reports every substitution that violates a physically-required invariant —
without any datasheet lookup or LLM. It reuses the same proximity engine the
pipeline gates on, so "would the gate have caught this?" and "did the output
actually respect it?" are answered by one source of truth.

Two uses:
  * the FAE orchestrator auto-grades a live result BEFORE spending tokens on the
    adversarial judge (a value-integrity violation is a certain finding);
  * a CI golden test can pin the invariant over curated fixtures.

An invariant dict (per ref_des) may carry:
  category                 : the component category (magnetic/resistor/…)
  original_value_si        : the original's primary value in SI base units
  primary_value_accept_lo  : min multiplier of the original the substitute may be
  primary_value_accept_hi  : max multiplier
  dielectric_class_min     : lowest acceptable dielectric class (capacitors)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Violation:
    ref_des: str
    parameter: str
    detail: str
    substitute: str


_SUBSTITUTED = ("exact", "recommended", "partial")


def _value_si(row: dict[str, Any], category: str, field: str) -> float | None:
    from heaviside.pipeline.crossref_pipeline import _parse_value_si

    return _parse_value_si(row.get(field, ""), category)


def check_row(row: dict[str, Any], inv: dict[str, Any]) -> list[Violation]:
    """Return the invariant violations for one result row (empty = clean)."""
    ref = str(row.get("ref_des", "?"))
    status = str(row.get("status", ""))
    sub = str(row.get("substitute_pn") or "")
    out: list[Violation] = []

    # Invariants only bite when the tool actually PROPOSED a substitute. A
    # no_substitute/keep_original is the tool declining — not a violation here.
    if status not in _SUBSTITUTED or not sub or sub == "no_substitute":
        return out

    category = str(inv.get("category") or row.get("component_type") or "")

    # Primary-value window: the substitute's value must sit within the accept
    # band of the original. This is the 330nH-for-1.5uH guard, generalised.
    orig_si = inv.get("original_value_si")
    if orig_si is None:
        orig_si = _value_si(row, category, "original_value")
    sub_si = _value_si(row, category, "substitute_value")
    lo = inv.get("primary_value_accept_lo")
    hi = inv.get("primary_value_accept_hi")
    if isinstance(orig_si, (int, float)) and orig_si > 0 and isinstance(sub_si, (int, float)):
        ratio = sub_si / orig_si
        if lo is not None and ratio < lo - 1e-9:
            out.append(
                Violation(
                    ref, "primary_value",
                    f"substitute value {sub_si:g} is {ratio:.2f}× the original {orig_si:g} "
                    f"(below the {lo:g}× floor) yet shipped as '{status}'",
                    sub,
                )
            )
        elif hi is not None and ratio > hi + 1e-9:
            out.append(
                Violation(
                    ref, "primary_value",
                    f"substitute value {sub_si:g} is {ratio:.2f}× the original {orig_si:g} "
                    f"(above the {hi:g}× ceiling) yet shipped as '{status}'",
                    sub,
                )
            )

    # Dielectric-class floor (capacitors): the substitute must not downgrade
    # below the stated minimum class (X7R must not become Y5V).
    dmin = inv.get("dielectric_class_min")
    if dmin and category == "capacitor":
        from heaviside.pipeline.param_check import _DIELECTRIC_RANK, _norm_class

        want = _DIELECTRIC_RANK.get(_norm_class(dmin))
        got_raw = row.get("substitute_dielectric") or row.get("substitute_technology") or ""
        got = _DIELECTRIC_RANK.get(_norm_class(got_raw))
        if want is not None and got is not None and got < want:
            out.append(
                Violation(
                    ref, "dielectric_class",
                    f"substitute dielectric {got_raw!r} is below the required {dmin!r}",
                    sub,
                )
            )
    return out


def check_result(
    result_rows: list[dict[str, Any]],
    invariants_by_ref: dict[str, dict[str, Any]],
) -> list[Violation]:
    """Check every row that has an invariant entry. Rows without one are skipped
    (the answer key is intentionally partial — it pins the traps, not everything)."""
    by_ref = {str(r.get("ref_des", "")): r for r in result_rows}
    violations: list[Violation] = []
    for ref, inv in invariants_by_ref.items():
        row = by_ref.get(ref)
        if row is None:
            continue
        violations.extend(check_row(row, inv))
    return violations
