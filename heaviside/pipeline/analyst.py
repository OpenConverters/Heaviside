"""Loss-budget + junction-temperature analyst stage.

Runs between the catalogue selector (which stamped Rds_on / Vf / ESR /
Rth_ja / Tj_max on every BOM component) and the realism gate (which
reads ``tas.loss_budget`` for ``no_negative_losses`` and per-component
``tj`` / ``tj_max`` for ``thermal_limit``).

Per-component loss attribution uses standard closed-form expressions
(Maniktala "Switching Power Supplies A to Z" Ch.7-8). Source of all
inputs:

  * spec / operating point: Vin, Vout, Iout, fsw, ambient_T
  * sim averages (heaviside.sim.runner): iin_avg, iout_avg as
    sanity checks; not on the critical path here
  * stamped component fields: Rds_on, Qg_total (mosfet), Vf, Qrr
    (diode), ESR (capacitor), Rth_ja (all)
  * PyOpenMagnetics outputs[op].coreLosses + windingLosses for the
    inductor

Per CLAUDE.md "no fallbacks": missing inputs (e.g. unstamped Rds_on
because the selector didn't run) -> stage records loss=None for that
bucket -> realism gate keeps no_negative_losses UNAVAILABLE rather
than silently skipping. The thermal stage propagates the same way:
unstamped Rth_ja means no Tj for that component, which keeps the
gate's check UNAVAILABLE.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class AnalystError(ValueError):
    """Raised when the spec or TAS lacks fields required by the analyst."""


# ---------------------------------------------------------------------------
# Standard gate-driver assumption for switching loss estimation
# ---------------------------------------------------------------------------
#
# Switching loss approximation P_sw ~ Vds * Id * Qg * fsw / Ig_avg
# where Ig_avg is the gate driver's average sourcing/sinking current
# during the Miller plateau. 1.0 A is a conservative-realistic default
# for the SiC/GaN/Si gate drivers Heaviside's catalogue picks; tighter
# numbers will come from a dedicated gate-driver selector later.
_GATE_DRIVE_CURRENT_A: float = 1.0


# ---------------------------------------------------------------------------
# Buck loss budget
# ---------------------------------------------------------------------------


def _spec_op(spec: Mapping[str, Any], op_index: int = 0) -> Mapping[str, Any]:
    ops = spec.get("operatingPoints")
    if not isinstance(ops, list) or not ops:
        raise AnalystError("spec.operatingPoints is required (non-empty list)")
    if not (0 <= op_index < len(ops)):
        raise AnalystError(
            f"spec.operatingPoints[{op_index}] out of range ({len(ops)} ops)"
        )
    op = ops[op_index]
    if not isinstance(op, Mapping):
        raise AnalystError(f"spec.operatingPoints[{op_index}] must be an object")
    return op


def _spec_fsw(spec: Mapping[str, Any], op_index: int = 0) -> float:
    op = _spec_op(spec, op_index)
    fsw = op.get("switchingFrequency")
    if not isinstance(fsw, (int, float)) or fsw <= 0:
        raise AnalystError(
            f"spec.operatingPoints[{op_index}].switchingFrequency must be positive"
        )
    return float(fsw)


def _spec_vout_iout(
    spec: Mapping[str, Any], op_index: int = 0,
) -> tuple[float, float]:
    op = _spec_op(spec, op_index)
    vouts = op.get("outputVoltages") or []
    iouts = op.get("outputCurrents") or []
    if not vouts or not iouts:
        raise AnalystError(
            f"operatingPoints[{op_index}].outputVoltages/outputCurrents required"
        )
    return float(vouts[0]), float(iouts[0])


def _num_ops(spec: Mapping[str, Any]) -> int:
    ops = spec.get("operatingPoints")
    return len(ops) if isinstance(ops, list) else 0


def _spec_vin_nominal(spec: Mapping[str, Any]) -> float:
    vin = spec.get("inputVoltage")
    if not isinstance(vin, Mapping):
        raise AnalystError("spec.inputVoltage must be an object")
    nom = vin.get("nominal")
    if not isinstance(nom, (int, float)) or nom <= 0:
        raise AnalystError("spec.inputVoltage.nominal must be a positive number")
    return float(nom)


def _spec_ambient_temp(spec: Mapping[str, Any]) -> float:
    op = spec.get("operatingPoints", [{}])[0]
    t = op.get("ambientTemperature")
    if not isinstance(t, (int, float)):
        raise AnalystError(
            "spec.operatingPoints[0].ambientTemperature is required for Tj computation"
        )
    return float(t)


def _find_named(tas: Mapping[str, Any], name: str) -> dict[str, Any] | None:
    for stage in tas.get("topology", {}).get("stages", []):
        for c in stage.get("circuit", {}).get("components", []):
            if isinstance(c, dict) and c.get("name") == name:
                return c
    return None


def _inductor_loss_from_mas(comp: Mapping[str, Any]) -> dict[str, float | None]:
    """Pull core + winding loss out of the PyOM outputs already stamped
    on the magnetic component.

    PyMKF populates ``data.outputs[op].coreLosses.coreLosses`` (W) and
    ``data.outputs[op].windingLosses`` (an object whose total is the
    sum across windings). We use the first operating point (op0).
    Returns ``{"L1_core": ..., "L1_dcr": ...}`` with ``None`` for any
    bucket that's missing.
    """
    data = comp.get("data")
    if not isinstance(data, Mapping):
        return {"L1_core": None, "L1_dcr": None}
    outs = data.get("outputs")
    if not isinstance(outs, list) or not outs:
        return {"L1_core": None, "L1_dcr": None}
    op0 = outs[0]
    if not isinstance(op0, Mapping):
        return {"L1_core": None, "L1_dcr": None}
    core_obj = op0.get("coreLosses")
    core_loss: float | None = None
    if isinstance(core_obj, Mapping):
        v = core_obj.get("coreLosses")
        if isinstance(v, (int, float)) and v >= 0:
            core_loss = float(v)
    winding_obj = op0.get("windingLosses")
    winding_loss: float | None = None
    if isinstance(winding_obj, Mapping):
        # PyMKF reports per-winding DC loss; sum across windings.
        total = 0.0
        seen = False
        per = winding_obj.get("windingLosses")
        if isinstance(per, list):
            for entry in per:
                if isinstance(entry, Mapping):
                    pl = entry.get("totalLosses") or entry.get("dcLosses")
                    if isinstance(pl, (int, float)) and pl >= 0:
                        total += float(pl)
                        seen = True
        # Fallback path used by some PyMKF builds: a single scalar.
        if not seen:
            scalar = winding_obj.get("dcResistancePerWinding") or winding_obj.get("totalLosses")
            if isinstance(scalar, (int, float)) and scalar >= 0:
                total = float(scalar)
                seen = True
        if seen:
            winding_loss = total
    return {"L1_core": core_loss, "L1_dcr": winding_loss}


def compute_buck_loss_budget(
    tas: dict[str, Any], spec: Mapping[str, Any], *, op_index: int = 0,
) -> dict[str, float | None]:
    """Per-component loss attribution for a buck design at ``op_index``.

    Returns a flat dict suitable to stamp at ``tas["loss_budget"]``.
    Each key is a component-bucket label; each value is watts or
    ``None`` when an input was missing. ``None`` values are ignored
    by the realism gate's ``no_negative_losses`` check so a partial
    budget is still a valid budget.
    """
    fsw = _spec_fsw(spec, op_index)
    vout, iout = _spec_vout_iout(spec, op_index)
    vin = _spec_vin_nominal(spec)
    duty = vout / vin if vin > 0 else 0.0

    budget: dict[str, float | None] = {}

    # Q1 (high-side MOSFET)
    q1 = _find_named(tas, "Q1")
    if q1 is not None:
        rds_on = q1.get("rds_on")
        qg = q1.get("qg_total")
        if isinstance(rds_on, (int, float)) and rds_on > 0:
            budget["Q1_conduction"] = float(duty) * (iout ** 2) * float(rds_on)
        else:
            budget["Q1_conduction"] = None
        if isinstance(qg, (int, float)) and qg > 0:
            # P_sw ~ Vds * Id * (Qg / Ig) * fsw
            budget["Q1_switching"] = vin * iout * float(qg) * fsw / _GATE_DRIVE_CURRENT_A
        else:
            budget["Q1_switching"] = None

    # D1 (freewheeling diode)
    d1 = _find_named(tas, "D1")
    if d1 is not None:
        vf = d1.get("vf_typ")
        qrr = d1.get("qrr")
        if isinstance(vf, (int, float)) and vf >= 0:
            budget["D1_conduction"] = (1.0 - duty) * iout * float(vf)
        else:
            budget["D1_conduction"] = None
        # Diode reverse-recovery loss: P_rr = 0.5 * Vr * Qrr * fsw
        if isinstance(qrr, (int, float)) and qrr >= 0:
            budget["D1_switching"] = 0.5 * vin * float(qrr) * fsw
        else:
            budget["D1_switching"] = None

    # L1 (inductor) — values come from PyMKF's MAS outputs
    l1 = _find_named(tas, "L1")
    if l1 is not None:
        budget.update(_inductor_loss_from_mas(l1))

    # C_out — ESR loss from RMS ripple current
    cout = _find_named(tas, "C_out")
    if cout is not None:
        esr = cout.get("esr")
        ripple_rms = cout.get("ripple_current_stress")
        if (
            isinstance(esr, (int, float)) and esr >= 0
            and isinstance(ripple_rms, (int, float)) and ripple_rms >= 0
        ):
            budget["C_out_esr"] = (ripple_rms ** 2) * float(esr)
        else:
            budget["C_out_esr"] = None

    return budget


# ---------------------------------------------------------------------------
# Junction temperature
# ---------------------------------------------------------------------------


def _component_loss(
    budget: Mapping[str, Any], prefix: str,
) -> float | None:
    """Sum every loss bucket whose key starts with ``<prefix>_``.

    Returns ``None`` if no bucket exists OR every matching bucket is
    ``None`` — caller treats None as "Tj not computable for this part".
    """
    total = 0.0
    seen = False
    for k, v in budget.items():
        if not k.startswith(f"{prefix}_"):
            continue
        if isinstance(v, (int, float)):
            total += float(v)
            seen = True
    return total if seen else None


def stamp_junction_temperatures(
    tas: dict[str, Any], spec: Mapping[str, Any],
) -> None:
    """For each Q/D/C component with a known Rth_ja and a computable
    loss, stamp ``comp["tj"] = T_ambient + loss * Rth_ja``. The realism
    gate already reads ``comp["tj"]`` and ``comp["tj_max"]`` (selector
    stamped the latter) — this just adds the computed Tj alongside.

    Components without Rth_ja are skipped silently — the gate will see
    no ``tj`` field and mark thermal_limit UNAVAILABLE for that part.
    """
    budget = tas.get("loss_budget")
    if not isinstance(budget, Mapping):
        return
    t_amb = _spec_ambient_temp(spec)

    for stage in tas.get("topology", {}).get("stages", []):
        for comp in stage.get("circuit", {}).get("components", []):
            if not isinstance(comp, dict):
                continue
            name = comp.get("name")
            if not isinstance(name, str):
                continue
            rth = comp.get("rth_ja")
            if not isinstance(rth, (int, float)) or rth <= 0:
                continue
            loss = _component_loss(budget, name)
            if loss is None:
                continue
            comp["tj"] = round(t_amb + loss * float(rth), 2)
            comp["tj_provenance"] = {
                "method": "Tj = T_amb + loss * Rth_ja",
                "t_ambient_c": t_amb,
                "loss_w": round(loss, 6),
                "rth_ja_c_per_w": float(rth),
            }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _stamp_analyst_efficiency(tas: dict[str, Any], spec: Mapping[str, Any]) -> None:
    """Derive efficiency from the analyst's loss budget + spec Pout and
    stamp it into ``tas.simulation_results.op0.efficiency_analyst``.

    This is engineering-truth efficiency (picked components against spec
    operating point), distinct from the sim runner's measured efficiency
    which is dominated by lossy testbench scaffolding (snubbers, ideal
    diode models) in MKF's stock decks. The realism gate prefers this
    key over sim's ``efficiency`` so the verdict reflects the design,
    not the deck-modelling artefacts.

    No-op if loss_budget is incomplete (every bucket None) — caller
    must run the loss extractor first; we never invent an efficiency
    out of partial data.
    """
    budget = tas.get("loss_budget")
    if not isinstance(budget, Mapping):
        return
    total_loss = 0.0
    seen = False
    for v in budget.values():
        if isinstance(v, (int, float)):
            total_loss += float(v)
            seen = True
    if not seen:
        return
    vout, iout = _spec_vout_iout(spec)
    pout = vout * iout
    if pout <= 0:
        return
    pin = pout + total_loss
    if pin <= 0:
        return
    sim = tas.setdefault("simulation_results", {})
    if not isinstance(sim, dict):
        return
    op = sim.setdefault("op0", {})
    if not isinstance(op, dict):
        return
    op["efficiency_analyst"] = round(pout / pin, 4)
    op["pout_analyst"] = round(pout, 4)
    op["pin_analyst"] = round(pin, 4)
    op["total_loss_analyst"] = round(total_loss, 4)


def _worst_case_loss_budget(
    per_op_budgets: list[dict[str, float | None]],
) -> dict[str, float | None]:
    """Reduce a list of per-op loss budgets to a single worst-case
    (max per bucket) flat budget.

    ``None`` values propagate as None when EVERY op reports None for
    that bucket (i.e. the input is genuinely unmeasurable); otherwise
    the max of the numeric values wins. The realism gate's
    ``no_negative_losses`` reads from this flat budget directly.
    """
    if not per_op_budgets:
        return {}
    all_keys: set[str] = set()
    for b in per_op_budgets:
        all_keys.update(b)
    worst: dict[str, float | None] = {}
    for k in sorted(all_keys):
        numeric = [b[k] for b in per_op_budgets
                   if isinstance(b.get(k), (int, float))]
        worst[k] = max(numeric) if numeric else None
    return worst


def _stamp_per_op_budgets(
    tas: dict[str, Any], per_op_budgets: list[dict[str, float | None]],
) -> None:
    """Stamp each op's loss budget at ``simulation_results.op<i>.loss_budget``
    for per-op visibility. The flat ``tas.loss_budget`` is the worst-case
    reduction and is what the realism gate evaluates.
    """
    sim = tas.setdefault("simulation_results", {})
    if not isinstance(sim, dict):
        return
    for i, budget in enumerate(per_op_budgets):
        op_block = sim.setdefault(f"op{i}", {})
        if isinstance(op_block, dict):
            op_block["loss_budget"] = budget


def run_buck_analyst(tas: dict[str, Any], spec: Mapping[str, Any]) -> None:
    """In-place: compute per-op loss budgets across every operating
    point, stamp the worst-case-per-bucket at ``tas.loss_budget``,
    stamp per-op breakdowns at ``simulation_results.op<i>.loss_budget``,
    then stamp Tj on every BOM component (using the worst-case loss)
    and the analyst-derived efficiency.
    """
    n = max(_num_ops(spec), 1)
    per_op = [compute_buck_loss_budget(tas, spec, op_index=i) for i in range(n)]
    _stamp_per_op_budgets(tas, per_op)
    tas["loss_budget"] = _worst_case_loss_budget(per_op)
    stamp_junction_temperatures(tas, spec)
    _stamp_analyst_efficiency(tas, spec)


def _compute_generic_loss_budget(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
    *,
    duty: float,
    pri_current: float,
    sec_current: float,
    op_index: int = 0,
) -> dict[str, float | None]:
    """Topology-generic loss budget keyed by closed-form duty + currents.

    Used by boost/cuk/flyback analysts that share the same buck-class
    loss decomposition (MOSFET cond+sw, diode cond+rev-recovery, cap
    ESR, inductor from MAS) but differ in WHICH current flows through
    WHICH device. Callers supply:

      * ``duty``: switch on-time fraction (used for cond loss split)
      * ``pri_current``: the device-side current during switch ON
        (= inductor current for buck, primary-winding current for
        flyback, total switch-node current for cuk)
      * ``sec_current``: the device-side current during switch OFF
        (diode forward current avg; output cap loading)
      * ``op_index``: which operating point to use for fsw / Vout

    Inductor losses come straight from PyMKF's MAS outputs (same as
    buck — that path is topology-independent because PyMKF computes
    losses for the winding it designed).
    """
    fsw = _spec_fsw(spec, op_index)
    vout, iout = _spec_vout_iout(spec, op_index)
    vin = _spec_vin_nominal(spec)

    budget: dict[str, float | None] = {}

    q1 = _find_named(tas, "Q1")
    if q1 is not None:
        rds_on = q1.get("rds_on")
        qg = q1.get("qg_total")
        if isinstance(rds_on, (int, float)) and rds_on > 0:
            budget["Q1_conduction"] = float(duty) * (pri_current ** 2) * float(rds_on)
        else:
            budget["Q1_conduction"] = None
        if isinstance(qg, (int, float)) and qg > 0:
            # P_sw ~ Vds_off * Id * (Qg / Ig) * fsw; use spec.Vout for
            # boost / Vin for buck — but Vds_off varies by topology.
            # Use the higher of vin/vout as a conservative proxy.
            vds_off = max(vin, vout)
            budget["Q1_switching"] = vds_off * pri_current * float(qg) * fsw / _GATE_DRIVE_CURRENT_A
        else:
            budget["Q1_switching"] = None

    d1 = _find_named(tas, "D1")
    if d1 is not None:
        vf = d1.get("vf_typ")
        qrr = d1.get("qrr")
        if isinstance(vf, (int, float)) and vf >= 0:
            budget["D1_conduction"] = (1.0 - float(duty)) * sec_current * float(vf)
        else:
            budget["D1_conduction"] = None
        if isinstance(qrr, (int, float)) and qrr >= 0:
            vr = max(vin, vout)
            budget["D1_switching"] = 0.5 * vr * float(qrr) * fsw
        else:
            budget["D1_switching"] = None

    l1 = _find_named(tas, "L1")
    if l1 is not None:
        budget.update(_inductor_loss_from_mas(l1))

    # T1 (transformer) for isolated topologies — same MAS-loss path
    t1 = _find_named(tas, "T1")
    if t1 is not None:
        # Re-key to T1_core / T1_dcr.
        t1_losses = _inductor_loss_from_mas(t1)
        budget["T1_core"] = t1_losses.get("L1_core")
        budget["T1_dcr"] = t1_losses.get("L1_dcr")

    cout = _find_named(tas, "C_out") or _find_named(tas, "C_bus_DC")
    if cout is not None:
        esr = cout.get("esr")
        ripple_rms = cout.get("ripple_current_stress")
        if (
            isinstance(esr, (int, float)) and esr >= 0
            and isinstance(ripple_rms, (int, float)) and ripple_rms >= 0
        ):
            budget["C_out_esr"] = (ripple_rms ** 2) * float(esr)
        else:
            budget["C_out_esr"] = None

    return budget


def _run_generic_analyst(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
    compute_op_budget: Any,
) -> None:
    """Shared driver: walk every op, stamp per-op budgets, reduce to
    worst-case at root, then Tj + analyst-derived efficiency.

    ``compute_op_budget(tas, spec, op_index)`` returns one op's budget.
    """
    n = max(_num_ops(spec), 1)
    per_op = [compute_op_budget(tas, spec, op_index=i) for i in range(n)]
    _stamp_per_op_budgets(tas, per_op)
    tas["loss_budget"] = _worst_case_loss_budget(per_op)
    stamp_junction_temperatures(tas, spec)
    _stamp_analyst_efficiency(tas, spec)


def _boost_op_budget(
    tas: dict[str, Any], spec: Mapping[str, Any], *, op_index: int = 0,
) -> dict[str, float | None]:
    vout, iout = _spec_vout_iout(spec, op_index)
    vin = _spec_vin_nominal(spec)
    duty = 1.0 - vin / vout
    iin = iout * vout / vin  # inductor avg = input avg for boost
    return _compute_generic_loss_budget(
        tas, spec, duty=duty, pri_current=iin, sec_current=iout,
        op_index=op_index,
    )


def _cuk_op_budget(
    tas: dict[str, Any], spec: Mapping[str, Any], *, op_index: int = 0,
) -> dict[str, float | None]:
    vout, iout = _spec_vout_iout(spec, op_index)
    vin = _spec_vin_nominal(spec)
    vout_abs = abs(vout)
    duty = vout_abs / (vin + vout_abs)
    iin = iout * vout_abs / vin
    return _compute_generic_loss_budget(
        tas, spec, duty=duty, pri_current=(iin + iout), sec_current=iout,
        op_index=op_index,
    )


def _flyback_op_budget(
    tas: dict[str, Any], spec: Mapping[str, Any], *, op_index: int = 0,
) -> dict[str, float | None]:
    ratios = spec.get("desiredTurnsRatios") or [1.0]
    n = float(ratios[0])
    d_max = float(spec.get("maximumDutyCycle", 0.5))
    _, iout = _spec_vout_iout(spec, op_index)
    ipri = (iout / n) * 1.5
    isec_avg = iout / (1.0 - d_max)
    return _compute_generic_loss_budget(
        tas, spec, duty=d_max, pri_current=ipri, sec_current=isec_avg,
        op_index=op_index,
    )


def run_boost_analyst(tas: dict[str, Any], spec: Mapping[str, Any]) -> None:
    """Boost loss budget + Tj across every operating point."""
    _run_generic_analyst(tas, spec, _boost_op_budget)


def run_cuk_analyst(tas: dict[str, Any], spec: Mapping[str, Any]) -> None:
    """Cuk loss budget + Tj across every operating point."""
    _run_generic_analyst(tas, spec, _cuk_op_budget)


def run_flyback_analyst(tas: dict[str, Any], spec: Mapping[str, Any]) -> None:
    """Flyback loss budget + Tj across every operating point."""
    _run_generic_analyst(tas, spec, _flyback_op_budget)


# Per-topology analyst dispatch. Add new topologies here as their loss
# formulas are wired.
_ANALYSTS: dict[str, Any] = {
    "buck": run_buck_analyst,
    "boost": run_boost_analyst,
    "cuk": run_cuk_analyst,
    "flyback": run_flyback_analyst,
}


def run_analyst(topology: str, tas: dict[str, Any], spec: Mapping[str, Any]) -> None:
    """Dispatch to the per-topology analyst. No-op for unported
    topologies — the realism gate will keep their loss/thermal checks
    UNAVAILABLE, which is the honest failure mode."""
    fn = _ANALYSTS.get(topology)
    if fn is not None:
        fn(tas, spec)


__all__ = [
    "AnalystError",
    "compute_buck_loss_budget",
    "run_analyst",
    "run_boost_analyst",
    "run_buck_analyst",
    "run_cuk_analyst",
    "run_flyback_analyst",
    "stamp_junction_temperatures",
]
