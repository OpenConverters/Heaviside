"""CRE (Competitor Reverse-Engineering) data models.

State and outcome dataclasses carried through the CRE pipeline:
  PDF → extract spec + BOM + claims (waveforms, efficiency, operating points)
  → verify/fetch BOM into TAS (librarian)
  → map components to stencil roles (LLM)
  → build reference converter (TAS document, converter schema)
  → simulate with real parasitics
  → compare sim vs PDF claims
  → diagnose mismatches → learn → loop until match
  → output validated TAS + comparison report
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def compute_desired_inductance(
    vin_nom: float,
    vout: float,
    iout: float,
    fsw: float,
    *,
    ripple_ratio: float = 0.3,
) -> float | None:
    """Ripple-based main-inductor sizing for the MKF magnetic designer.

    Buck (Vin > Vout) and boost (Vin < Vout) closed forms at the nominal
    operating point. Returns None when inputs are non-positive (nothing to
    size) — the caller decides whether that's fatal. This is converter
    sizing, not core magnetics: the actual core/turns come from MKF.
    """
    if not (fsw > 0 and iout > 0 and vin_nom > 0 and vout > 0):
        return None
    delta_il = iout * ripple_ratio
    if vin_nom > vout:  # buck
        l_desired = (vin_nom - vout) * vout / (delta_il * fsw * vin_nom)
    else:  # boost
        d = 1 - vin_nom / vout
        l_desired = vin_nom * d / (delta_il * fsw)
    return l_desired if l_desired > 0 else None


@dataclass(frozen=True, slots=True)
class ReferenceSpec:
    """Structured specs extracted from a reference design."""

    topology: str
    vin_min: float
    vin_nom: float
    vin_max: float
    vout: float
    iout: float
    pout: float
    fsw: float
    efficiency_target: float | None = None
    isolation_required: bool = False
    turns_ratio: float | None = None
    rdson_hs: float | None = None
    rdson_ls: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_heaviside_spec(self) -> dict[str, Any]:
        """Convert to a Heaviside-format converter spec dict."""
        spec: dict[str, Any] = {
            "inputVoltage": {
                "minimum": self.vin_min,
                "nominal": self.vin_nom,
                "maximum": self.vin_max,
            },
            "operatingPoints": [
                {
                    "outputVoltages": [self.vout],
                    "outputCurrents": [self.iout],
                    "switchingFrequency": self.fsw,
                    "ambientTemperature": 25.0,
                }
            ],
            "diodeVoltageDrop": 0.7,
            "currentRippleRatio": 0.3,
        }
        spec["efficiency"] = self.efficiency_target or 0.9
        if self.turns_ratio is not None:
            spec["desiredTurnsRatios"] = [self.turns_ratio]
        # For buck/step-down: ensure Vin_min > Vout (MKF requires D < 1)
        topo_lower = self.topology.lower()
        is_step_down = any(k in topo_lower for k in ("buck", "forward", "half-bridge"))
        if is_step_down and self.vout > 0 and spec["inputVoltage"]["minimum"] <= self.vout:
            spec["inputVoltage"]["minimum"] = self.vout * 1.2

        # Compute desiredInductance for MKF magnetic design
        l_desired = compute_desired_inductance(
            self.vin_nom, self.vout, self.iout, self.fsw, ripple_ratio=0.3
        )
        if l_desired is not None:
            spec["desiredInductance"] = l_desired
        spec.update(self.extra)
        return spec


@dataclass(slots=True)
class ReferenceClaims:
    """Performance claims extracted from the reference design PDF."""

    efficiency: dict[str, float] = field(default_factory=dict)
    vout_ripple_mv: float | None = None
    vin_ripple_mv: float | None = None
    vout_measured: float | None = None
    iout_measured: float | None = None
    thermal_rise_c: float | None = None
    load_regulation_pct: float | None = None
    line_regulation_pct: float | None = None
    waveform_descriptions: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ComponentRoleMap:
    """Maps extracted BOM ref_des to stencil roles."""

    roles: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0
    unmapped: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SimComparison:
    """Comparison between simulation results and reference claims."""

    sim_efficiency: float = 0.0
    claimed_efficiency: float = 0.0
    efficiency_delta_pp: float = 0.0
    sim_vout: float = 0.0
    claimed_vout: float = 0.0
    vout_error_pct: float = 0.0
    mismatches: list[dict[str, Any]] = field(default_factory=list)
    diagnosis: str = ""
    passed: bool = False


@dataclass(slots=True)
class CREState:
    """Mutable state carried through the CRE pipeline stages."""

    reference: str
    pdf_path: Path | None = None
    pdf_text: str = ""
    ref_spec: ReferenceSpec | None = None
    ref_bom: list[dict[str, Any]] = field(default_factory=list)
    ref_claims: ReferenceClaims = field(default_factory=ReferenceClaims)
    role_map: ComponentRoleMap | None = None
    missing_mpns: list[str] = field(default_factory=list)
    netlist: str | None = None
    tas: dict[str, Any] | None = None
    sim_result: dict[str, Any] | None = None
    comparisons: list[SimComparison] = field(default_factory=list)
    design_outcome: Any | None = None
    review_verdicts: list[dict[str, Any]] = field(default_factory=list)
    lessons: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    attempt: int = 0
    passed: bool = False
    # Optional Ray+Nicola review-and-retry on the high-risk LLM extraction
    # stages (competitor specs, reverse-engineered schematic) + a (msg, pct)
    # progress sink. Off by default; the API job turns them on.
    review_llm: bool = False
    progress: Any = None


@dataclass(frozen=True, slots=True)
class CREOutcome:
    """Immutable result of a completed CRE pipeline run."""

    reference: str
    ref_spec: ReferenceSpec | None
    ref_bom: tuple[dict[str, Any], ...]
    ref_claims: ReferenceClaims | None = None
    role_map: ComponentRoleMap | None = None
    tas: dict[str, Any] | None = None
    sim_result: dict[str, Any] | None = None
    comparisons: tuple[SimComparison, ...] = ()
    design_outcome: Any | None = None
    review_verdicts: tuple[dict[str, Any], ...] = ()
    lessons: tuple[dict[str, Any], ...] = ()
    passed: bool = False
    report: str | None = None
    diagnostics: tuple[str, ...] = ()

    @classmethod
    def from_state(cls, state: CREState) -> CREOutcome:
        return cls(
            reference=state.reference,
            ref_spec=state.ref_spec,
            ref_bom=tuple(state.ref_bom),
            ref_claims=state.ref_claims,
            role_map=state.role_map,
            tas=state.tas,
            sim_result=state.sim_result,
            comparisons=tuple(state.comparisons),
            design_outcome=state.design_outcome,
            review_verdicts=tuple(state.review_verdicts),
            lessons=tuple(state.lessons),
            passed=state.passed,
            diagnostics=tuple(state.diagnostics),
        )


__all__ = [
    "CREOutcome",
    "CREState",
    "ComponentRoleMap",
    "ReferenceClaims",
    "ReferenceSpec",
    "SimComparison",
]
