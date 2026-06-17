"""CR (Cross-Reference) data models.

State and outcome dataclasses for the BOM cross-reference pipeline:
  source BOM → prefetch TAS candidates → LLM crossref → guardrails
  → match scoring → sourcing → review → self-audit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


@dataclass(frozen=True, slots=True)
class SimDerivedStress:
    """Per-component electrical stress derived from RE simulation.

    Populated by ``extract_component_stress()`` after the RE testbench
    runs. Fields are ``None`` when the simulation didn't measure that
    quantity for this component.
    """

    ref_des: str
    role: str
    v_peak: float | None = None
    v_dc: float | None = None
    i_peak: float | None = None
    i_avg: float | None = None
    i_rms: float | None = None
    p_dissipated: float | None = None


class SubstitutionStatus(StrEnum):
    EXACT = "exact"
    RECOMMENDED = "recommended"
    PARTIAL = "partial"
    NO_SUBSTITUTE = "no_substitute"
    KEEP_ORIGINAL = "keep_original"


@dataclass(frozen=True, slots=True)
class CrossRefComponent:
    """One row of the cross-reference result."""

    ref_des: str
    component_type: str
    original_mpn: str
    original_value: str
    original_voltage: str
    original_package: str
    substitute_mpn: str | None
    substitute_value: str
    substitute_voltage: str
    substitute_package: str
    status: SubstitutionStatus
    match_score: dict[str, Any] | None = None
    sourcing: dict[str, Any] | None = None
    notes: str = ""
    guardrail_fires: tuple[str, ...] = ()


@dataclass(slots=True)
class CrossRefState:
    """Mutable state carried through the CR pipeline stages."""

    source_bom: list[dict[str, Any]]
    target_manufacturer: str
    circuit_context: str | None = None
    candidates_by_ref: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    preclassified: dict[str, dict[str, Any]] = field(default_factory=dict)
    crossref_result: list[dict[str, Any]] = field(default_factory=list)
    guardrail_log: list[dict[str, Any]] = field(default_factory=list)
    otto_log: dict[str, Any] = field(default_factory=dict)
    review_verdicts: list[dict[str, Any]] = field(default_factory=list)
    reviewer_log: str = ""
    attempt: int = 0
    passed: bool = False
    stress_by_ref: dict[str, SimDerivedStress] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CrossRefOutcome:
    """Immutable result of a completed CR pipeline run."""

    source_bom: tuple[dict[str, Any], ...]
    target_manufacturer: str
    components: tuple[CrossRefComponent, ...]
    passed: bool
    report: str | None = None
    sourcing_summary: dict[str, Any] | None = None
    guardrail_log: tuple[dict[str, Any], ...] = ()
    otto_log: dict[str, Any] = field(default_factory=dict)
    review_verdicts: tuple[dict[str, Any], ...] = ()
    reviewer_log: str = ""
    diagnostics: tuple[str, ...] = ()

    @classmethod
    def from_state(cls, state: CrossRefState) -> CrossRefOutcome:
        components = []
        for row in state.crossref_result:
            components.append(
                CrossRefComponent(
                    ref_des=row.get("ref_des", "?"),
                    component_type=row.get("component_type", "?"),
                    original_mpn=row.get("original_pn", ""),
                    original_value=row.get("original_value", ""),
                    original_voltage=row.get("original_voltage", ""),
                    original_package=row.get("original_package", ""),
                    substitute_mpn=row.get("substitute_pn"),
                    substitute_value=row.get("substitute_value", ""),
                    substitute_voltage=row.get("substitute_voltage", ""),
                    substitute_package=row.get("substitute_package", ""),
                    status=SubstitutionStatus(row.get("status", "no_substitute")),
                    notes=row.get("notes", ""),
                    guardrail_fires=tuple(row.get("guardrail_fires", [])),
                )
            )
        return cls(
            source_bom=tuple(state.source_bom),
            target_manufacturer=state.target_manufacturer,
            components=tuple(components),
            passed=state.passed,
            guardrail_log=tuple(state.guardrail_log),
            otto_log=state.otto_log,
            review_verdicts=tuple(state.review_verdicts),
            reviewer_log=state.reviewer_log,
            diagnostics=tuple(state.diagnostics),
        )


__all__ = [
    "CrossRefComponent",
    "CrossRefOutcome",
    "CrossRefState",
    "SimDerivedStress",
    "SubstitutionStatus",
]
