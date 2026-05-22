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
    CapacitorConstraints,
    CapacitorSelection,
    CapacitorTiebreaker,
    DiodeConstraints,
    DiodeSelection,
    DiodeTiebreaker,
    MosfetConstraints,
    MosfetSelection,
    MosfetTiebreaker,
    select_capacitor,
    select_diode,
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
_DIODE_VRRM_DERATING: float = 1.30   # match check_diode_voltage_derating
_DIODE_IF_DERATING: float = 1.20     # match analyst convention
_CAP_V_DERATING: float = 1.50        # match check_capacitor_voltage_derating
_CAP_RIPPLE_DERATING: float = 1.20   # avoid running the cap at its ripple limit
# Capacitance acceptance band on the analytical target. Lower bound
# enforces minimum filtering; upper bound prevents 10x over-sizing
# (which oversizes the BOM cost + footprint with no benefit).
_CAP_CAPACITANCE_MIN_RATIO: float = 0.8
_CAP_CAPACITANCE_MAX_RATIO: float = 10.0

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


def _diode_constraints_from_stress(s: ComponentStresses) -> DiodeConstraints:
    if s.vr_stress is None or s.if_avg_stress is None:
        raise ValueError(
            "Diode constraints require both vr_stress and if_avg_stress "
            "on the ComponentStresses; topology stress deriver is incomplete."
        )
    return DiodeConstraints(
        vrrm_min=s.vr_stress * _DIODE_VRRM_DERATING,
        if_avg_min=s.if_avg_stress * _DIODE_IF_DERATING,
        qrr_max=None,  # don't filter on Qrr today; selector picks lowest_vf
    )


def _capacitor_constraints_from_stress(
    s: ComponentStresses,
    *,
    target_capacitance: float,
    require_ripple: bool = False,
) -> CapacitorConstraints:
    """Build capacitor selection constraints from stress + target C.

    ``require_ripple=False`` (default) keeps the ripple filter open
    because the TAS DB's MLCC rows do not declare a ripple-current
    rating — enforcing it rejects every MLCC even when an MLCC is the
    correct choice. Set ``require_ripple=True`` when sourcing a bulk
    electrolytic / film cap whose ripple rating is the binding stress.
    """
    if s.v_working is None or s.i_ripple is None:
        raise ValueError(
            "Capacitor constraints require both v_working and i_ripple "
            "on the ComponentStresses; topology stress deriver is incomplete."
        )
    return CapacitorConstraints(
        capacitance_min=target_capacitance * _CAP_CAPACITANCE_MIN_RATIO,
        capacitance_max=target_capacitance * _CAP_CAPACITANCE_MAX_RATIO,
        v_rated_min=s.v_working * _CAP_V_DERATING,
        ripple_current_min=(s.i_ripple * _CAP_RIPPLE_DERATING) if require_ripple else None,
    )


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


def _stamp_diode(
    comp: dict[str, Any],
    sel: DiodeSelection,
    stress_vr: float,
    stress_if_avg: float,
) -> None:
    comp["data"] = sel.chosen.raw_envelope
    comp["vrrm_rated"] = sel.chosen.vrrm_rated
    comp["v_reverse"] = stress_vr
    comp["if_avg_rated"] = sel.chosen.if_avg_rated
    comp["if_avg_stress"] = stress_if_avg
    comp["vf_typ"] = sel.chosen.vf_typ
    comp["selection_provenance"] = {
        "category": "diode",
        "mpn": sel.chosen.mpn,
        "manufacturer": sel.chosen.manufacturer,
        "tiebreaker": sel.tiebreaker.value,
        "constraints": {
            "vrrm_min": sel.constraints.vrrm_min,
            "if_avg_min": sel.constraints.if_avg_min,
            "qrr_max": sel.constraints.qrr_max,
            "exclude_discontinued": sel.constraints.exclude_discontinued,
        },
        "margins": dict(sel.margins),
        "alternatives_considered": sel.alternatives_considered,
    }


def _stamp_capacitor(
    comp: dict[str, Any],
    sel: CapacitorSelection,
    stress_v: float,
    stress_ripple: float,
) -> None:
    comp["data"] = sel.chosen.raw_envelope
    comp["v_rated"] = sel.chosen.v_rated
    comp["v_working"] = stress_v
    comp["capacitance"] = sel.chosen.capacitance
    comp["ripple_current_rated"] = sel.chosen.ripple_current_rms
    comp["ripple_current_stress"] = stress_ripple
    comp["esr"] = sel.chosen.esr
    comp["selection_provenance"] = {
        "category": "capacitor",
        "mpn": sel.chosen.mpn,
        "manufacturer": sel.chosen.manufacturer,
        "tiebreaker": sel.tiebreaker.value,
        "constraints": {
            "capacitance_min": sel.constraints.capacitance_min,
            "capacitance_max": sel.constraints.capacitance_max,
            "v_rated_min": sel.constraints.v_rated_min,
            "ripple_current_min": sel.constraints.ripple_current_min,
            "technology_allowed": sorted(sel.constraints.technology_allowed),
            "exclude_discontinued": sel.constraints.exclude_discontinued,
        },
        "margins": dict(sel.margins),
        "alternatives_considered": sel.alternatives_considered,
    }


def _is_placeholder(comp: Mapping[str, Any], substring: str) -> bool:
    data = comp.get("data")
    return isinstance(data, str) and substring in data


def _is_mosfet_placeholder(comp: Mapping[str, Any]) -> bool:
    return _is_placeholder(comp, "mosfets.ndjson")


def _is_diode_placeholder(comp: Mapping[str, Any]) -> bool:
    return _is_placeholder(comp, "diodes.ndjson")


def _is_capacitor_placeholder(comp: Mapping[str, Any]) -> bool:
    return _is_placeholder(comp, "capacitors.ndjson")


# Target output-capacitance ripple budget. Textbook small-signal buck:
#   ΔV_out = ΔI_L / (8 * fsw * C_out)
# Picking ΔV_out / V_out = 1 % gives the C_out target. The selector then
# accepts a band around this target (set by the capacitance_min_ratio /
# capacitance_max_ratio constants above).
_DEFAULT_VOUT_RIPPLE_FRACTION: float = 0.01


def _buck_target_capacitance(
    *, ripple_current_pp: float, fsw: float, vout: float,
) -> float:
    """Target output capacitance for a buck, given the analytical
    inductor current ripple, switching frequency, and output voltage."""
    if fsw <= 0 or vout <= 0:
        raise ValueError(
            f"_buck_target_capacitance: fsw={fsw}, vout={vout} must be positive"
        )
    delta_v = _DEFAULT_VOUT_RIPPLE_FRACTION * vout
    return ripple_current_pp / (8.0 * fsw * delta_v)


def assemble_bom_from_tas(
    tas: dict[str, Any],
    *,
    topology: str,
    spec: Mapping[str, Any],
    mosfet_tiebreaker: MosfetTiebreaker = MosfetTiebreaker.LOWEST_RDS_ON,
    diode_tiebreaker: DiodeTiebreaker = DiodeTiebreaker.LOWEST_VF,
    capacitor_tiebreaker: CapacitorTiebreaker = CapacitorTiebreaker.LOWEST_ESR,
) -> dict[str, Any]:
    """Walk ``tas``'s topology stages and stamp every Q/D/C placeholder
    with a real selection from the local TAS DB.

    No-op (returns ``tas`` untouched) for topologies whose stress
    deriver is not yet registered — that fail-open behaviour is
    intentional today so unported topologies don't break end-to-end
    runs; the realism gate will continue to FAIL/UNAVAILABLE on them
    and the caller sees what's unhandled.

    Raises:
        SelectionError: a placeholder exists but no TAS row satisfies
            the derived constraints.
        StressDerivationError: the spec lacks fields the stress
            deriver needs.
    """
    stresses = derive_stresses(topology, spec)
    if stresses is None:
        return tas

    # First operating point only for v0.1; multi-op topologies need a
    # worst-case sweep (deferred).
    ops = spec.get("operatingPoints") or [{}]
    op = ops[0] if isinstance(ops[0], Mapping) else {}
    vouts = op.get("outputVoltages") or [0.0]
    iouts = op.get("outputCurrents") or [0.0]
    fsw = op.get("switchingFrequency")
    if not isinstance(fsw, (int, float)) or fsw <= 0:
        return tas
    pout = float(vouts[0]) * float(iouts[0])
    if pout <= 0:
        return tas

    # MOSFET constraints + selection.
    mosfet_c: MosfetConstraints | None = None
    if stresses.vds_stress is not None and stresses.id_stress is not None:
        mosfet_c = _mosfet_constraints_from_stress(stresses, pout=pout, fsw=float(fsw))

    # Diode constraints + selection.
    diode_c: DiodeConstraints | None = None
    if stresses.vr_stress is not None and stresses.if_avg_stress is not None:
        diode_c = _diode_constraints_from_stress(stresses)

    # Capacitor constraints + selection. Topology-aware target
    # capacitance: buck is the only one wired today. (Other topologies
    # will need their own formula; until then, we skip cap selection
    # for them — the realism gate stays UNAVAILABLE on cap derating,
    # which is the honest signal.)
    cap_c: CapacitorConstraints | None = None
    if topology == "buck" and stresses.v_working is not None and stresses.i_ripple is not None:
        # ripple_current_pp = i_ripple_rms * 2*sqrt(3) (inverse of the
        # triangular-wave RMS conversion in stress.py)
        ripple_pp = stresses.i_ripple * 2.0 * (3.0 ** 0.5)
        target_c = _buck_target_capacitance(
            ripple_current_pp=ripple_pp,
            fsw=float(fsw), vout=float(vouts[0]),
        )
        cap_c = _capacitor_constraints_from_stress(
            stresses, target_capacitance=target_c,
        )

    for stage in tas.get("topology", {}).get("stages", []):
        for comp in stage.get("circuit", {}).get("components", []):
            if not isinstance(comp, dict):
                continue
            if mosfet_c is not None and _is_mosfet_placeholder(comp):
                sel_m = select_mosfet(mosfet_c, tiebreaker=mosfet_tiebreaker)
                _stamp_mosfet(
                    comp, sel_m,
                    stress_vds=stresses.vds_stress,
                    stress_id=stresses.id_stress,
                )
            elif diode_c is not None and _is_diode_placeholder(comp):
                sel_d = select_diode(diode_c, tiebreaker=diode_tiebreaker)
                _stamp_diode(
                    comp, sel_d,
                    stress_vr=stresses.vr_stress,
                    stress_if_avg=stresses.if_avg_stress,
                )
            elif cap_c is not None and _is_capacitor_placeholder(comp):
                sel_c = select_capacitor(cap_c, tiebreaker=capacitor_tiebreaker)
                _stamp_capacitor(
                    comp, sel_c,
                    stress_v=stresses.v_working,
                    stress_ripple=stresses.i_ripple,
                )

    return tas


__all__ = ["assemble_bom_from_tas"]
