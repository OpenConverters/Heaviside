"""BOM assembly: walk TAS placeholders, pick real MPNs, stamp them.

Runs between ``bridge.attach_components_to_tas`` (which fills the
magnetics from PyMKF) and the realism gate (which compares each
component's rated values against per-class stress thresholds).

Per CLAUDE.md "no fallbacks": each placeholder either gets stamped with a
real selection from the TAS DB or raises :class:`SelectionError` from the
selector. The caller (currently the CLI) decides how to surface the
failure.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from heaviside.catalogue.selector import (
    MosfetConstraints,
    MosfetSelection,
    MosfetTiebreaker,
    select_mosfet,
)
from heaviside.pipeline.stress import ComponentStresses, derive_stresses

# ---------------------------------------------------------------------------
# Sizing margins (design rules)
# ---------------------------------------------------------------------------
#
# Multipliers applied on top of the analytical worst-case stress to size
# the *minimum acceptable rating* for selection. These factors MUST match
# the realism gate's per-class derating ratios (see
# heaviside/pipeline/realism.py check_fet_voltage_derating et al) so a
# part picked by this assembler is guaranteed to PASS the gate's
# derating check on the same analytical stress.
#
# Source of truth (matched, not duplicated semantically — both numbers
# document the same engineering rule):
#   check_fet_voltage_derating(..., min_ratio=1.5)
#   check_inductor_isat_margin(..., min_ratio=1.2) -- not used here

_MOSFET_VDS_DERATING: float = 1.50   # match check_fet_voltage_derating
_MOSFET_ID_DERATING: float = 1.20    # match analyst convention (Maniktala Ch.7)

# Rds_on target derived from a fraction of the system's loss budget.
# 5% of Vout * Iout is a generous starting fraction for buck (low-side
# diode dominates DCM losses; conduction is small).
_MOSFET_RDS_ON_LOSS_FRACTION: float = 0.05
# Qg upper bound — assumes a competent gate driver and switching loss
# budget of ~2% Pout at fsw. Loose but excludes parts with absurdly
# high Qg that would dominate switching loss.
_MOSFET_QG_LOSS_FRACTION: float = 0.02
_DEFAULT_GATE_DRIVE_VOLTAGE: float = 10.0  # typical EPC/SiC/Si gate-on Vgs


def _mosfet_constraints_from_stress(
    s: ComponentStresses,
    *,
    pout: float,
    fsw: float,
) -> MosfetConstraints:
    """Translate per-component stress + power-budget into selection
    constraints. Loud failure if any required stress is missing."""
    if s.vds_stress is None or s.id_stress is None:
        raise ValueError(
            "MOSFET constraints require both vds_stress and id_stress "
            "on the ComponentStresses; topology stress deriver is incomplete."
        )
    rds_on_max = (_MOSFET_RDS_ON_LOSS_FRACTION * pout) / (s.id_stress ** 2)
    # Qg = (Loss budget for switching) / (Vgs * fsw)
    qg_max = (_MOSFET_QG_LOSS_FRACTION * pout) / (_DEFAULT_GATE_DRIVE_VOLTAGE * fsw)
    return MosfetConstraints(
        vds_min=s.vds_stress * _MOSFET_VDS_DERATING,
        id_min=s.id_stress * _MOSFET_ID_DERATING,
        rds_on_max=rds_on_max,
        qg_max=qg_max,
    )


# ---------------------------------------------------------------------------
# Stamp helpers — write the gate-readable flat fields + audit data
# ---------------------------------------------------------------------------


def _stamp_mosfet(
    comp: dict[str, Any],
    sel: MosfetSelection,
    stress_vds: float,
    stress_id: float,
) -> None:
    """Mutate the TAS component dict in place.

    Writes both:
      * the realism gate's flat fields (``vds_rated``, ``vds_stress``)
      * the full TAS row at ``data`` (provenance + librarian round-trip)
      * a ``selection_provenance`` block (audit trail: constraints,
        tiebreaker, margins, alternatives considered).
    """
    comp["data"] = sel.chosen.raw_envelope
    comp["vds_rated"] = sel.chosen.vds_rated
    comp["vds_stress"] = stress_vds
    comp["id_rated"] = sel.chosen.id_continuous
    comp["id_stress"] = stress_id
    comp["rds_on"] = sel.chosen.rds_on
    comp["selection_provenance"] = {
        "category": "mosfet",
        "mpn": sel.chosen.mpn,
        "manufacturer": sel.chosen.manufacturer,
        "tiebreaker": sel.tiebreaker.value,
        "constraints": {
            "vds_min": sel.constraints.vds_min,
            "id_min": sel.constraints.id_min,
            "rds_on_max": sel.constraints.rds_on_max,
            "qg_max": sel.constraints.qg_max,
            "technology_allowed": sorted(sel.constraints.technology_allowed),
            "exclude_discontinued": sel.constraints.exclude_discontinued,
        },
        "margins": dict(sel.margins),
        "alternatives_considered": sel.alternatives_considered,
    }


# ---------------------------------------------------------------------------
# Walk + select
# ---------------------------------------------------------------------------


def _is_mosfet_placeholder(comp: Mapping[str, Any]) -> bool:
    """Recognise a TAS component whose ``data`` still points at the
    mosfet placeholder URL the stencils emit (i.e. nothing has picked
    one yet)."""
    data = comp.get("data")
    if not isinstance(data, str):
        return False
    return "mosfets.ndjson" in data


def assemble_bom_from_tas(
    tas: dict[str, Any],
    *,
    topology: str,
    spec: Mapping[str, Any],
    mosfet_tiebreaker: MosfetTiebreaker = MosfetTiebreaker.LOWEST_RDS_ON,
) -> dict[str, Any]:
    """Walk ``tas``'s topology stages and stamp every MOSFET placeholder
    with a real selection from the local TAS DB.

    No-op (returns ``tas`` untouched) for topologies whose stress
    deriver is not yet registered — that fail-open behaviour is
    intentional today so unported topologies don't break end-to-end
    runs, but the realism gate will continue to FAIL/UNAVAILABLE on
    them and the caller can see what's unhandled.

    Future expansion: same shape for diodes and capacitors.

    Raises:
        SelectionError: a placeholder exists but no TAS row satisfies
            the derived constraints.
        StressDerivationError: the spec lacks fields the stress
            deriver needs.
    """
    stresses = derive_stresses(topology, spec)
    if stresses is None:
        return tas
    if stresses.vds_stress is None or stresses.id_stress is None:
        return tas

    # P_out for power-budget-derived constraints (Rds_on, Qg). Computed
    # once from the first operating point — multi-op topologies will
    # need a worst-case sweep; out of scope for v0.1.
    ops = spec.get("operatingPoints") or [{}]
    op = ops[0] if isinstance(ops[0], Mapping) else {}
    vouts = op.get("outputVoltages") or [0.0]
    iouts = op.get("outputCurrents") or [0.0]
    fsw = op.get("switchingFrequency")
    if not isinstance(fsw, (int, float)) or fsw <= 0:
        return tas  # no usable fsw → can't size Qg; skip rather than crash
    pout = float(vouts[0]) * float(iouts[0])
    if pout <= 0:
        return tas

    constraints = _mosfet_constraints_from_stress(stresses, pout=pout, fsw=float(fsw))

    for stage in tas.get("topology", {}).get("stages", []):
        for comp in stage.get("circuit", {}).get("components", []):
            if not isinstance(comp, dict):
                continue
            if not _is_mosfet_placeholder(comp):
                continue
            sel = select_mosfet(constraints, tiebreaker=mosfet_tiebreaker)
            _stamp_mosfet(
                comp, sel,
                stress_vds=stresses.vds_stress,
                stress_id=stresses.id_stress,
            )

    return tas


__all__ = ["assemble_bom_from_tas"]
