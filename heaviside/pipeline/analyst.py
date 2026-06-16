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

# Design target for junction temperature as a fraction of the part's Tj_max
# — a 15% margin, the standard reliability derating for power semiconductors
# (mirrors the voltage/current derating factors used in selection). When a
# part can't stay under this on its own (junction-to-ambient, no heatsink),
# the thermal stage sizes the required heatsink instead of failing outright.
_THERMAL_TJ_DERATING: float = 0.85


# ---------------------------------------------------------------------------
# Buck loss budget
# ---------------------------------------------------------------------------


def _spec_op(spec: Mapping[str, Any], op_index: int = 0) -> Mapping[str, Any]:
    ops = spec.get("operatingPoints")
    if not isinstance(ops, list) or not ops:
        raise AnalystError("spec.operatingPoints is required (non-empty list)")
    if not (0 <= op_index < len(ops)):
        raise AnalystError(f"spec.operatingPoints[{op_index}] out of range ({len(ops)} ops)")
    op = ops[op_index]
    if not isinstance(op, Mapping):
        raise AnalystError(f"spec.operatingPoints[{op_index}] must be an object")
    return op


def _spec_fsw(spec: Mapping[str, Any], op_index: int = 0) -> float:
    op = _spec_op(spec, op_index)
    fsw = op.get("switchingFrequency")
    if not isinstance(fsw, (int, float)) or fsw <= 0:
        raise AnalystError(f"spec.operatingPoints[{op_index}].switchingFrequency must be positive")
    return float(fsw)


def _spec_vout_iout(
    spec: Mapping[str, Any],
    op_index: int = 0,
) -> tuple[float, float]:
    op = _spec_op(spec, op_index)
    vouts = op.get("outputVoltages") or []
    iouts = op.get("outputCurrents") or []
    if not vouts or not iouts:
        raise AnalystError(f"operatingPoints[{op_index}].outputVoltages/outputCurrents required")
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


def _loss_at_output(op_obj: Any) -> tuple[float | None, float | None]:
    """Core + winding loss (W) from a single PyOM ``outputs[op]`` object.

    Returns ``(core_loss, winding_loss)`` with ``None`` for any bucket the
    op object doesn't carry. The single source of truth for reading MKF's
    per-operating-point magnetic loss numbers — both the op0 reader
    (:func:`_inductor_loss_from_mas`) and the worst-OP reader
    (:func:`inductor_loss_worst_op`) delegate here so they never drift.
    """
    if not isinstance(op_obj, Mapping):
        return None, None
    core_obj = op_obj.get("coreLosses")
    core_loss: float | None = None
    if isinstance(core_obj, Mapping):
        v = core_obj.get("coreLosses")
        if isinstance(v, (int, float)) and v >= 0:
            core_loss = float(v)
    winding_obj = op_obj.get("windingLosses")
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
    return core_loss, winding_loss


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
    core_loss, winding_loss = _loss_at_output(outs[0])
    return {"L1_core": core_loss, "L1_dcr": winding_loss}


def inductor_loss_worst_op(comp: Mapping[str, Any]) -> dict[str, float | None]:
    """Worst-operating-point core + winding loss for a magnetic component.

    MKF designs the magnetic across every operating point and stamps a
    per-OP loss under ``data.outputs[op]``. The frequency sweep ranks on the
    *worst* OP (not op0), so this returns the maximum core and maximum
    winding loss observed across all outputs — each maximised independently
    (a conservative worst-OP envelope). ``None`` for a bucket means NO output
    carried that number (honest "unavailable", never a fabricated 0).

    Reads the same MAS structure as :func:`_inductor_loss_from_mas` via the
    shared :func:`_loss_at_output`, so the op0 and worst-OP readers cannot
    drift apart.
    """
    data = comp.get("data")
    if not isinstance(data, Mapping):
        return {"L1_core": None, "L1_dcr": None}
    outs = data.get("outputs")
    if not isinstance(outs, list) or not outs:
        return {"L1_core": None, "L1_dcr": None}
    worst_core: float | None = None
    worst_wind: float | None = None
    for op_obj in outs:
        core, wind = _loss_at_output(op_obj)
        if core is not None:
            worst_core = core if worst_core is None else max(worst_core, core)
        if wind is not None:
            worst_wind = wind if worst_wind is None else max(worst_wind, wind)
    return {"L1_core": worst_core, "L1_dcr": worst_wind}


def compute_buck_loss_budget(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
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
            budget["Q1_conduction"] = float(duty) * (iout**2) * float(rds_on)
        else:
            budget["Q1_conduction"] = None
        if isinstance(qg, (int, float)) and qg > 0:
            # P_sw = 0.5*Vds*Id*(Qg/Ig)*fsw (triangular V-I overlap, 2 edges).
            budget["Q1_switching"] = 0.5 * vin * iout * float(qg) * fsw / _GATE_DRIVE_CURRENT_A
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
            isinstance(esr, (int, float))
            and esr >= 0
            and isinstance(ripple_rms, (int, float))
            and ripple_rms >= 0
        ):
            budget["C_out_esr"] = (ripple_rms**2) * float(esr)
        else:
            budget["C_out_esr"] = None

    return budget


# ---------------------------------------------------------------------------
# Junction temperature
# ---------------------------------------------------------------------------


def _component_loss(
    budget: Mapping[str, Any],
    prefix: str,
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
    tas: dict[str, Any],
    spec: Mapping[str, Any],
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

            rth_ja = float(rth)
            tj_noheatsink = t_amb + loss * rth_ja
            tj_max = comp.get("tj_max")
            target = (
                float(tj_max) * _THERMAL_TJ_DERATING
                if isinstance(tj_max, (int, float)) and tj_max > 0
                else None
            )

            # Case 1: junction-to-ambient already within the derated target —
            # no heatsink needed.
            if target is None or tj_noheatsink <= target:
                comp["tj"] = round(tj_noheatsink, 2)
                comp["tj_provenance"] = {
                    "method": "Tj = T_amb + loss * Rth_ja (no heatsink)",
                    "t_ambient_c": t_amb,
                    "loss_w": round(loss, 6),
                    "rth_ja_c_per_w": rth_ja,
                }
                continue

            # Needs a heatsink. Size it from the junction-to-CASE resistance.
            rth_jc = comp.get("rth_jc")
            if not isinstance(rth_jc, (int, float)) or rth_jc <= 0:
                # Can't size a heatsink without Rth_jc — surface it (the gate
                # will fail thermal_limit on the no-heatsink Tj). No guessing.
                comp["tj"] = round(tj_noheatsink, 2)
                comp["tj_provenance"] = {
                    "method": "Tj = T_amb + loss * Rth_ja — NEEDS HEATSINK but "
                    "Rth_jc unknown (fetch from datasheet to size it)",
                    "t_ambient_c": t_amb,
                    "loss_w": round(loss, 6),
                    "rth_ja_c_per_w": rth_ja,
                }
                continue

            rth_jc = float(rth_jc)
            tj_floor = t_amb + loss * rth_jc  # ideal infinite heatsink
            if tj_floor < target:
                # Feasible: a heatsink+interface with sink-side thermal
                # resistance <= rsa_budget keeps Tj at the derated target.
                rsa_budget = (target - t_amb) / loss - rth_jc
                comp["tj"] = round(target, 2)
                comp["heatsink_required_rth_sa_max"] = round(rsa_budget, 3)
                comp["tj_provenance"] = {
                    "method": "Tj = target with heatsink: Rth_jc + required sink Rth_sa <= budget",
                    "t_ambient_c": t_amb,
                    "loss_w": round(loss, 6),
                    "rth_jc_c_per_w": rth_jc,
                    "tj_target_c": round(target, 2),
                    "required_rth_sa_max_c_per_w": round(rsa_budget, 3),
                }
            else:
                # Even an ideal (zero-resistance) heatsink can't hold Tj under
                # target — the die itself can't shed the heat. Genuine fail;
                # needs paralleled devices or a larger die.
                comp["tj"] = round(tj_floor, 2)
                comp["tj_provenance"] = {
                    "method": "Tj floor = T_amb + loss * Rth_jc (ideal sink) — "
                    "INFEASIBLE: exceeds target even with perfect "
                    "heatsink; parallel devices / larger die needed",
                    "t_ambient_c": t_amb,
                    "loss_w": round(loss, 6),
                    "rth_jc_c_per_w": rth_jc,
                    "tj_target_c": round(target, 2),
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
        numeric = [b[k] for b in per_op_budgets if isinstance(b.get(k), (int, float))]
        worst[k] = max(numeric) if numeric else None
    return worst


def _stamp_per_op_budgets(
    tas: dict[str, Any],
    per_op_budgets: list[dict[str, float | None]],
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
            budget["Q1_conduction"] = float(duty) * (pri_current**2) * float(rds_on)
        else:
            budget["Q1_conduction"] = None
        if isinstance(qg, (int, float)) and qg > 0:
            # P_sw ~ Vds_off * Id * (Qg / Ig) * fsw; use spec.Vout for
            # boost / Vin for buck — but Vds_off varies by topology.
            # Use the higher of vin/vout as a conservative proxy.
            vds_off = max(vin, vout)
            budget["Q1_switching"] = (
                0.5 * vds_off * pri_current * float(qg) * fsw / _GATE_DRIVE_CURRENT_A
            )
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
            isinstance(esr, (int, float))
            and esr >= 0
            and isinstance(ripple_rms, (int, float))
            and ripple_rms >= 0
        ):
            budget["C_out_esr"] = (ripple_rms**2) * float(esr)
        else:
            budget["C_out_esr"] = None

    # Isolated / multi-output topologies: the output rectifier(s) are named
    # D_out{i} / C_out{i}, one per rail — NOT the buck-class D1 / C_out the
    # block above looks for. Sum each rail's diode conduction (Vf · Iout_i,
    # the average current delivered to that load) and output-cap ESR.
    op = spec.get("operatingPoints") or [{}]
    op_i = op[op_index] if op_index < len(op) else (op[0] if op else {})
    rail_iouts = op_i.get("outputCurrents") or []
    i = 0
    while True:
        d_out = _find_named(tas, f"D_out{i}")
        c_out = _find_named(tas, f"C_out{i}")
        if d_out is None and c_out is None:
            break
        iout_i = float(rail_iouts[i]) if i < len(rail_iouts) else iout
        if d_out is not None:
            vf = d_out.get("vf_typ")
            qrr = d_out.get("qrr")
            budget[f"D_out{i}_conduction"] = (
                iout_i * float(vf) if isinstance(vf, (int, float)) and vf >= 0 else None
            )
            if isinstance(qrr, (int, float)) and qrr >= 0:
                budget[f"D_out{i}_switching"] = 0.5 * max(vin, vout) * float(qrr) * fsw
        if c_out is not None:
            esr = c_out.get("esr")
            ripple_rms = c_out.get("ripple_current_stress")
            budget[f"C_out{i}_esr"] = (
                (float(ripple_rms) ** 2) * float(esr)
                if isinstance(esr, (int, float))
                and esr >= 0
                and isinstance(ripple_rms, (int, float))
                and ripple_rms >= 0
                else None
            )
        i += 1

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
    tas: dict[str, Any],
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> dict[str, float | None]:
    vout, iout = _spec_vout_iout(spec, op_index)
    vin = _spec_vin_nominal(spec)
    duty = 1.0 - vin / vout
    iin = iout * vout / vin  # inductor avg = input avg for boost
    # The output diode conducts the INDUCTOR current during the OFF
    # interval; the generic budget multiplies sec_current by (1-duty),
    # so passing iin yields the correct average diode current
    # (1-duty)*iin = iout (a boost diode carries the full output charge).
    # Passing iout here would triple-undercount the diode conduction
    # loss and inflate efficiency past the realism sanity ceiling.
    return _compute_generic_loss_budget(
        tas,
        spec,
        duty=duty,
        pri_current=iin,
        sec_current=iin,
        op_index=op_index,
    )


def _cuk_op_budget(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> dict[str, float | None]:
    vout, iout = _spec_vout_iout(spec, op_index)
    vin = _spec_vin_nominal(spec)
    vout_abs = abs(vout)
    duty = vout_abs / (vin + vout_abs)
    iin = iout * vout_abs / vin
    return _compute_generic_loss_budget(
        tas,
        spec,
        duty=duty,
        pri_current=(iin + iout),
        sec_current=iout,
        op_index=op_index,
    )


def _flyback_op_budget(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> dict[str, float | None]:
    ratios = spec.get("desiredTurnsRatios") or [1.0]
    n = float(ratios[0])
    d_max = float(spec.get("maximumDutyCycle", 0.5))
    _, iout = _spec_vout_iout(spec, op_index)
    ipri = (iout / n) * 1.5
    isec_avg = iout / (1.0 - d_max)
    return _compute_generic_loss_budget(
        tas,
        spec,
        duty=d_max,
        pri_current=ipri,
        sec_current=isec_avg,
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


# ---------------------------------------------------------------------------
# Helper: MOSFET / diode loss for a single named device
# ---------------------------------------------------------------------------


def _mosfet_loss(
    comp: dict[str, Any] | None,
    name: str,
    *,
    duty: float,
    i_on: float,
    vds_off: float,
    fsw: float,
    zvs: bool = False,
) -> dict[str, float | None]:
    """Return ``{name_conduction: ..., name_switching: ...}`` for one MOSFET."""
    if comp is None:
        return {}
    budget: dict[str, float | None] = {}
    rds_on = comp.get("rds_on")
    qg = comp.get("qg_total")
    if isinstance(rds_on, (int, float)) and rds_on > 0:
        budget[f"{name}_conduction"] = float(duty) * (i_on**2) * float(rds_on)
    else:
        budget[f"{name}_conduction"] = None
    if zvs:
        budget[f"{name}_switching"] = 0.0
    elif isinstance(qg, (int, float)) and qg > 0:
        # Hard-switching V-I overlap loss: E = 0.5*Vds*Id*t_transition per
        # edge, two edges/cycle → P = 0.5*Vds*Id*(Qg/Ig)*fsw. The 0.5 is the
        # triangular-overlap factor (was previously omitted, ~2x high).
        # NOTE: Qg overestimates the Miller-plateau (Qgd) transition charge;
        # this remains a conservative upper bound until Qgd is in TAS.
        budget[f"{name}_switching"] = 0.5 * vds_off * i_on * float(qg) * fsw / _GATE_DRIVE_CURRENT_A
    else:
        budget[f"{name}_switching"] = None
    return budget


def _diode_loss(
    comp: dict[str, Any] | None,
    name: str,
    *,
    duty_off: float,
    i_fwd: float,
    vr: float,
    fsw: float,
) -> dict[str, float | None]:
    """Return ``{name_conduction: ..., name_switching: ...}`` for one diode."""
    if comp is None:
        return {}
    budget: dict[str, float | None] = {}
    vf = comp.get("vf_typ")
    qrr = comp.get("qrr")
    if isinstance(vf, (int, float)) and vf >= 0:
        budget[f"{name}_conduction"] = float(duty_off) * i_fwd * float(vf)
    else:
        budget[f"{name}_conduction"] = None
    if isinstance(qrr, (int, float)) and qrr >= 0:
        budget[f"{name}_switching"] = 0.5 * vr * float(qrr) * fsw
    else:
        budget[f"{name}_switching"] = None
    return budget


def _cap_esr_loss(
    comp: dict[str, Any] | None,
    name: str,
) -> dict[str, float | None]:
    """Return ``{name_esr: ...}`` for a capacitor."""
    if comp is None:
        return {}
    esr = comp.get("esr")
    ripple_rms = comp.get("ripple_current_stress")
    if (
        isinstance(esr, (int, float))
        and esr >= 0
        and isinstance(ripple_rms, (int, float))
        and ripple_rms >= 0
    ):
        return {f"{name}_esr": (ripple_rms**2) * float(esr)}
    return {f"{name}_esr": None}


def _inductor_loss_keyed(
    comp: dict[str, Any] | None,
    name: str,
) -> dict[str, float | None]:
    """Return ``{name_core: ..., name_dcr: ...}`` for any inductor/xfmr."""
    if comp is None:
        return {}
    raw = _inductor_loss_from_mas(comp)
    return {f"{name}_core": raw.get("L1_core"), f"{name}_dcr": raw.get("L1_dcr")}


# ---------------------------------------------------------------------------
# SEPIC
# ---------------------------------------------------------------------------


def _sepic_op_budget(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> dict[str, float | None]:
    fsw = _spec_fsw(spec, op_index)
    vout, iout = _spec_vout_iout(spec, op_index)
    vin = _spec_vin_nominal(spec)
    duty = vout / (vin + vout) if (vin + vout) > 0 else 0.0
    iin = iout * vout / vin if vin > 0 else 0.0
    vds_off = vin + vout

    budget: dict[str, float | None] = {}
    budget.update(
        _mosfet_loss(
            _find_named(tas, "Q1"),
            "Q1",
            duty=duty,
            i_on=iin,
            vds_off=vds_off,
            fsw=fsw,
        )
    )
    budget.update(
        _diode_loss(
            _find_named(tas, "D1"),
            "D1",
            duty_off=(1.0 - duty),
            i_fwd=iout,
            vr=vds_off,
            fsw=fsw,
        )
    )
    budget.update(_inductor_loss_keyed(_find_named(tas, "L1"), "L1"))
    budget.update(_inductor_loss_keyed(_find_named(tas, "L2"), "L2"))
    budget.update(_cap_esr_loss(_find_named(tas, "C1"), "C1"))
    cout = _find_named(tas, "C_out") or _find_named(tas, "Cout")
    budget.update(_cap_esr_loss(cout, "C_out"))
    return budget


def run_sepic_analyst(tas: dict[str, Any], spec: Mapping[str, Any]) -> None:
    """SEPIC loss budget + Tj across every operating point."""
    _run_generic_analyst(tas, spec, _sepic_op_budget)


# ---------------------------------------------------------------------------
# Zeta
# ---------------------------------------------------------------------------


def _zeta_op_budget(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> dict[str, float | None]:
    fsw = _spec_fsw(spec, op_index)
    vout, iout = _spec_vout_iout(spec, op_index)
    vin = _spec_vin_nominal(spec)
    duty = vout / (vin + vout) if (vin + vout) > 0 else 0.0
    iin = iout * vout / vin if vin > 0 else 0.0
    vds_off = vin + vout

    budget: dict[str, float | None] = {}
    budget.update(
        _mosfet_loss(
            _find_named(tas, "Q1"),
            "Q1",
            duty=duty,
            i_on=iin,
            vds_off=vds_off,
            fsw=fsw,
        )
    )
    budget.update(
        _diode_loss(
            _find_named(tas, "D1"),
            "D1",
            duty_off=(1.0 - duty),
            i_fwd=iout,
            vr=vds_off,
            fsw=fsw,
        )
    )
    budget.update(_inductor_loss_keyed(_find_named(tas, "L1"), "L1"))
    budget.update(_inductor_loss_keyed(_find_named(tas, "L2"), "L2"))
    budget.update(_cap_esr_loss(_find_named(tas, "C1"), "C1"))
    cout = _find_named(tas, "C_out") or _find_named(tas, "Cout")
    budget.update(_cap_esr_loss(cout, "C_out"))
    return budget


def run_zeta_analyst(tas: dict[str, Any], spec: Mapping[str, Any]) -> None:
    """Zeta loss budget + Tj across every operating point."""
    _run_generic_analyst(tas, spec, _zeta_op_budget)


# ---------------------------------------------------------------------------
# Four-switch buck-boost
# ---------------------------------------------------------------------------


def _four_switch_buck_boost_op_budget(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> dict[str, float | None]:
    fsw = _spec_fsw(spec, op_index)
    vout, iout = _spec_vout_iout(spec, op_index)
    vin = _spec_vin_nominal(spec)
    # Buck mode when Vin > Vout, boost mode otherwise
    duty = (vout / vin if vin > 0 else 0.0) if vin > vout else 1.0 - vin / vout if vout > 0 else 0.0
    il = max(iout, iout * vout / vin) if vin > 0 else iout

    budget: dict[str, float | None] = {}
    for qname in ("Q1", "Q2", "Q3", "Q4"):
        q = _find_named(tas, qname)
        budget.update(
            _mosfet_loss(
                q,
                qname,
                duty=duty,
                i_on=il,
                vds_off=max(vin, vout),
                fsw=fsw,
            )
        )
    budget.update(_inductor_loss_keyed(_find_named(tas, "L1"), "L1"))
    cout = _find_named(tas, "C_out") or _find_named(tas, "Cout")
    budget.update(_cap_esr_loss(cout, "C_out"))
    return budget


def run_four_switch_buck_boost_analyst(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
) -> None:
    """Four-switch buck-boost loss budget + Tj across every operating point."""
    _run_generic_analyst(tas, spec, _four_switch_buck_boost_op_budget)


# ---------------------------------------------------------------------------
# Single-switch forward
# ---------------------------------------------------------------------------


def _turns_ratio(spec: Mapping[str, Any]) -> float:
    ratios = spec.get("desiredTurnsRatios") or [1.0]
    return float(ratios[0])


def _single_switch_forward_op_budget(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> dict[str, float | None]:
    fsw = _spec_fsw(spec, op_index)
    vin = _spec_vin_nominal(spec)

    # Per-rail operating point — mirrors the two-switch forward budget. The
    # regulated rail (0) sets the duty; every rail shares the common primary
    # excitation (V_sec_i = Vin·D/n_i = Vout_i), so the primary current is
    # the sum of reflected secondary currents. The single-switch reset is via
    # the demagnetisation winding + diode (D_demag), which clamps V_DS(Q1) to
    # ~2·Vin during reset (vs. Vin for the two-switch/active-clamp variants).
    op = spec.get("operatingPoints") or [{}]
    op_i = op[op_index] if op_index < len(op) else (op[0] if op else {})
    vouts = op_i.get("outputVoltages") or []
    iouts = op_i.get("outputCurrents") or []
    ratios = spec.get("desiredTurnsRatios") or [_turns_ratio(spec)]
    n_rails = max(len(vouts), len(iouts), len(ratios), 1)

    def _rail(seq: Any, i: int, fallback: float) -> float:
        return float(seq[i]) if i < len(seq) else float(fallback)

    n0 = _rail(ratios, 0, 1.0)
    vout0 = _rail(vouts, 0, 0.0)
    duty = vout0 * n0 / vin if vin > 0 else 0.0
    duty = min(duty, 0.5)  # forward converter limit

    # Reflected primary current = Σ Iout_i / n_i.
    ipri = 0.0
    for i in range(n_rails):
        n_i = _rail(ratios, i, 1.0)
        if n_i > 0:
            ipri += _rail(iouts, i, 0.0) / n_i
    vds_off = 2.0 * vin  # demag reset clamps V_DS to ~2·Vin

    budget: dict[str, float | None] = {}
    budget.update(
        _mosfet_loss(
            _find_named(tas, "Q1"),
            "Q1",
            duty=duty,
            i_on=ipri,
            vds_off=vds_off,
            fsw=fsw,
        )
    )
    # Demagnetisation diode conducts during reset (duty_off ~ 1-duty),
    # returning the magnetising current to Vin.
    budget.update(
        _diode_loss(
            _find_named(tas, "D_demag"),
            "D_demag",
            duty_off=(1.0 - duty),
            i_fwd=ipri,
            vr=vin,
            fsw=fsw,
        )
    )
    # Per-rail output stage: forward rectifier (D_fwd{i}), freewheel diode
    # (D_fw{i}), output choke (L_out{i}), and output cap (C_out{i}). Each
    # rail's components carry that rail's output current.
    for i in range(n_rails):
        vout_i = _rail(vouts, i, vout0)
        iout_i = _rail(iouts, i, 0.0)
        budget.update(
            _diode_loss(
                _find_named(tas, f"D_fwd{i}"),
                f"D_fwd{i}",
                duty_off=duty,
                i_fwd=iout_i,
                vr=vout_i,
                fsw=fsw,
            )
        )
        budget.update(
            _diode_loss(
                _find_named(tas, f"D_fw{i}"),
                f"D_fw{i}",
                duty_off=(1.0 - duty),
                i_fwd=iout_i,
                vr=vout_i,
                fsw=fsw,
            )
        )
        budget.update(_inductor_loss_keyed(_find_named(tas, f"L_out{i}"), f"L_out{i}"))
        budget.update(_cap_esr_loss(_find_named(tas, f"C_out{i}"), f"C_out{i}"))
    budget.update(_inductor_loss_keyed(_find_named(tas, "T1"), "T1"))
    return budget


def run_single_switch_forward_analyst(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
) -> None:
    """Single-switch forward loss budget + Tj."""
    _run_generic_analyst(tas, spec, _single_switch_forward_op_budget)


# ---------------------------------------------------------------------------
# Two-switch forward
# ---------------------------------------------------------------------------


def _two_switch_forward_op_budget(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> dict[str, float | None]:
    fsw = _spec_fsw(spec, op_index)
    vin = _spec_vin_nominal(spec)

    # Per-rail operating point. The regulated rail (0) sets the duty;
    # every rail shares the common primary excitation, so per-rail turns
    # ratios are solved such that V_sec_i = Vin·D/n_i = Vout_i. The
    # primary current is the sum of reflected secondary currents.
    op = spec.get("operatingPoints") or [{}]
    op_i = op[op_index] if op_index < len(op) else (op[0] if op else {})
    vouts = op_i.get("outputVoltages") or []
    iouts = op_i.get("outputCurrents") or []
    ratios = spec.get("desiredTurnsRatios") or [_turns_ratio(spec)]
    n_rails = max(len(vouts), len(iouts), len(ratios), 1)

    def _rail(seq: Any, i: int, fallback: float) -> float:
        return float(seq[i]) if i < len(seq) else float(fallback)

    n0 = _rail(ratios, 0, 1.0)
    vout0 = _rail(vouts, 0, 0.0)
    duty = vout0 * n0 / vin if vin > 0 else 0.0
    duty = min(duty, 0.5)

    # Reflected primary current = Σ Iout_i / n_i.
    ipri = 0.0
    for i in range(n_rails):
        n_i = _rail(ratios, i, 1.0)
        if n_i > 0:
            ipri += _rail(iouts, i, 0.0) / n_i
    vds_off = vin  # clamped to Vin by body diodes

    budget: dict[str, float | None] = {}
    # Two primary MOSFETs share the same current
    for qname in ("Q1", "Q2"):
        budget.update(
            _mosfet_loss(
                _find_named(tas, qname),
                qname,
                duty=duty,
                i_on=ipri,
                vds_off=vds_off,
                fsw=fsw,
            )
        )
    # D1, D2: body/clamp diodes (conduct during reset, duty_off ~ 1-duty)
    for dname in ("D1", "D2"):
        budget.update(
            _diode_loss(
                _find_named(tas, dname),
                dname,
                duty_off=(1.0 - duty),
                i_fwd=ipri,
                vr=vin,
                fsw=fsw,
            )
        )
    # Per-rail output stage: forward rectifier (D_fwd{i}), freewheel
    # diode (D_fw{i}), and output cap (C_out{i}). Each rail's diodes
    # carry that rail's output current.
    for i in range(n_rails):
        vout_i = _rail(vouts, i, vout0)
        iout_i = _rail(iouts, i, 0.0)
        budget.update(
            _diode_loss(
                _find_named(tas, f"D_fwd{i}"),
                f"D_fwd{i}",
                duty_off=duty,
                i_fwd=iout_i,
                vr=vout_i,
                fsw=fsw,
            )
        )
        budget.update(
            _diode_loss(
                _find_named(tas, f"D_fw{i}"),
                f"D_fw{i}",
                duty_off=(1.0 - duty),
                i_fwd=iout_i,
                vr=vout_i,
                fsw=fsw,
            )
        )
        budget.update(_inductor_loss_keyed(_find_named(tas, f"L_out{i}"), f"L_out{i}"))
        budget.update(_cap_esr_loss(_find_named(tas, f"C_out{i}"), f"C_out{i}"))
    budget.update(_inductor_loss_keyed(_find_named(tas, "T1"), "T1"))
    return budget


def run_two_switch_forward_analyst(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
) -> None:
    """Two-switch forward loss budget + Tj."""
    _run_generic_analyst(tas, spec, _two_switch_forward_op_budget)


# ---------------------------------------------------------------------------
# Active-clamp forward
# ---------------------------------------------------------------------------


def _active_clamp_forward_op_budget(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> dict[str, float | None]:
    fsw = _spec_fsw(spec, op_index)
    vin = _spec_vin_nominal(spec)

    # Per-rail operating point — mirrors the two-switch forward budget, but
    # the active clamp replaces the reset diodes with Q_clamp + C_clamp and
    # the regulated duty may exceed 0.5 (clamp absorbs the reset
    # volt-seconds). The regulated rail (0) sets the duty; every rail shares
    # the common primary excitation, so the primary current is the sum of
    # reflected secondary currents.
    op = spec.get("operatingPoints") or [{}]
    op_i = op[op_index] if op_index < len(op) else (op[0] if op else {})
    vouts = op_i.get("outputVoltages") or []
    iouts = op_i.get("outputCurrents") or []
    ratios = spec.get("desiredTurnsRatios") or [_turns_ratio(spec)]
    n_rails = max(len(vouts), len(iouts), len(ratios), 1)

    def _rail(seq: Any, i: int, fallback: float) -> float:
        return float(seq[i]) if i < len(seq) else float(fallback)

    n0 = _rail(ratios, 0, 1.0)
    vout0 = _rail(vouts, 0, 0.0)
    duty = vout0 * n0 / vin if vin > 0 else 0.0

    # Reflected primary current = Σ Iout_i / n_i.
    ipri = 0.0
    for i in range(n_rails):
        n_i = _rail(ratios, i, 1.0)
        if n_i > 0:
            ipri += _rail(iouts, i, 0.0) / n_i
    # Clamp lifts V_DS to Vin/(1-D) during the off-time.
    vds_off = vin / (1.0 - duty) if duty < 1.0 else vin

    budget: dict[str, float | None] = {}
    budget.update(
        _mosfet_loss(
            _find_named(tas, "Q1"),
            "Q1",
            duty=duty,
            i_on=ipri,
            vds_off=vds_off,
            fsw=fsw,
        )
    )
    budget.update(
        _mosfet_loss(
            _find_named(tas, "Q_clamp"),
            "Q_clamp",
            duty=(1.0 - duty),
            i_on=ipri,
            vds_off=vds_off,
            fsw=fsw,
        )
    )
    # Per-rail output stage: forward rectifier (D_fwd{i}), freewheel
    # diode (D_fw{i}), output choke (L_out{i}) and output cap (C_out{i}).
    for i in range(n_rails):
        vout_i = _rail(vouts, i, vout0)
        iout_i = _rail(iouts, i, 0.0)
        budget.update(
            _diode_loss(
                _find_named(tas, f"D_fwd{i}"),
                f"D_fwd{i}",
                duty_off=duty,
                i_fwd=iout_i,
                vr=vout_i,
                fsw=fsw,
            )
        )
        budget.update(
            _diode_loss(
                _find_named(tas, f"D_fw{i}"),
                f"D_fw{i}",
                duty_off=(1.0 - duty),
                i_fwd=iout_i,
                vr=vout_i,
                fsw=fsw,
            )
        )
        budget.update(_inductor_loss_keyed(_find_named(tas, f"L_out{i}"), f"L_out{i}"))
        budget.update(_cap_esr_loss(_find_named(tas, f"C_out{i}"), f"C_out{i}"))
    budget.update(_inductor_loss_keyed(_find_named(tas, "T1"), "T1"))
    return budget


def run_active_clamp_forward_analyst(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
) -> None:
    """Active-clamp forward loss budget + Tj."""
    _run_generic_analyst(tas, spec, _active_clamp_forward_op_budget)


# ---------------------------------------------------------------------------
# Push-pull
# ---------------------------------------------------------------------------


def _push_pull_op_budget(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> dict[str, float | None]:
    fsw = _spec_fsw(spec, op_index)
    vout, iout = _spec_vout_iout(spec, op_index)
    vin = _spec_vin_nominal(spec)
    n = _turns_ratio(spec)
    duty = vout * n / (2.0 * vin) if vin > 0 else 0.0
    ipri = iout / n
    vds_off = 2.0 * vin

    budget: dict[str, float | None] = {}
    for qname in ("Q1", "Q2"):
        budget.update(
            _mosfet_loss(
                _find_named(tas, qname),
                qname,
                duty=duty,
                i_on=ipri,
                vds_off=vds_off,
                fsw=fsw,
            )
        )
    # Output rectifiers: each conducts for duty, off for 0.5-duty
    for dname in ("D1", "D2"):
        budget.update(
            _diode_loss(
                _find_named(tas, dname),
                dname,
                duty_off=duty,
                i_fwd=iout,
                vr=2.0 * vout,
                fsw=fsw,
            )
        )
    budget.update(_inductor_loss_keyed(_find_named(tas, "L1"), "L1"))
    budget.update(_inductor_loss_keyed(_find_named(tas, "T1"), "T1"))
    cout = _find_named(tas, "C_out") or _find_named(tas, "Cout")
    budget.update(_cap_esr_loss(cout, "C_out"))
    return budget


def run_push_pull_analyst(tas: dict[str, Any], spec: Mapping[str, Any]) -> None:
    """Push-pull loss budget + Tj."""
    _run_generic_analyst(tas, spec, _push_pull_op_budget)


# ---------------------------------------------------------------------------
# Asymmetric half-bridge
# ---------------------------------------------------------------------------


def _asymmetric_half_bridge_op_budget(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> dict[str, float | None]:
    fsw = _spec_fsw(spec, op_index)
    vout, iout = _spec_vout_iout(spec, op_index)
    vin = _spec_vin_nominal(spec)
    n = _turns_ratio(spec)
    duty = vout * n / vin if vin > 0 else 0.0
    ipri = iout / n

    budget: dict[str, float | None] = {}
    for qname in ("Q1", "Q2"):
        budget.update(
            _mosfet_loss(
                _find_named(tas, qname),
                qname,
                duty=duty,
                i_on=ipri,
                vds_off=vin,
                fsw=fsw,
            )
        )
    for dname in ("D1", "D2"):
        budget.update(
            _diode_loss(
                _find_named(tas, dname),
                dname,
                duty_off=duty,
                i_fwd=iout,
                vr=vout,
                fsw=fsw,
            )
        )
    budget.update(_inductor_loss_keyed(_find_named(tas, "L1"), "L1"))
    budget.update(_inductor_loss_keyed(_find_named(tas, "T1"), "T1"))
    cout = _find_named(tas, "C_out") or _find_named(tas, "Cout")
    budget.update(_cap_esr_loss(cout, "C_out"))
    return budget


def run_asymmetric_half_bridge_analyst(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
) -> None:
    """Asymmetric half-bridge loss budget + Tj."""
    _run_generic_analyst(tas, spec, _asymmetric_half_bridge_op_budget)


# ---------------------------------------------------------------------------
# Phase-shifted full bridge
# ---------------------------------------------------------------------------


def _phase_shifted_full_bridge_op_budget(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> dict[str, float | None]:
    fsw = _spec_fsw(spec, op_index)
    vout, iout = _spec_vout_iout(spec, op_index)
    vin = _spec_vin_nominal(spec)
    n = _turns_ratio(spec)
    duty = vout * n / vin if vin > 0 else 0.0
    ipri = iout / n

    budget: dict[str, float | None] = {}
    for qname in ("Q1", "Q2", "Q3", "Q4"):
        budget.update(
            _mosfet_loss(
                _find_named(tas, qname),
                qname,
                duty=0.5,
                i_on=ipri,
                vds_off=vin,
                fsw=fsw,
            )
        )
    for dname in ("D1", "D2"):
        budget.update(
            _diode_loss(
                _find_named(tas, dname),
                dname,
                duty_off=duty,
                i_fwd=iout,
                vr=vout,
                fsw=fsw,
            )
        )
    budget.update(_inductor_loss_keyed(_find_named(tas, "L1"), "L1"))
    budget.update(_inductor_loss_keyed(_find_named(tas, "T1"), "T1"))
    cout = _find_named(tas, "C_out") or _find_named(tas, "Cout")
    budget.update(_cap_esr_loss(cout, "C_out"))
    return budget


def run_phase_shifted_full_bridge_analyst(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
) -> None:
    """Phase-shifted full bridge loss budget + Tj."""
    _run_generic_analyst(tas, spec, _phase_shifted_full_bridge_op_budget)


# ---------------------------------------------------------------------------
# Weinberg
# ---------------------------------------------------------------------------


def _weinberg_op_budget(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> dict[str, float | None]:
    fsw = _spec_fsw(spec, op_index)
    vout, iout = _spec_vout_iout(spec, op_index)
    vin = _spec_vin_nominal(spec)
    n = _turns_ratio(spec)
    duty = vout * n / (2.0 * vin) if vin > 0 else 0.0
    ipri = iout / n
    vds_off = 2.0 * vin

    budget: dict[str, float | None] = {}
    for qname in ("Q1", "Q2"):
        budget.update(
            _mosfet_loss(
                _find_named(tas, qname),
                qname,
                duty=duty,
                i_on=ipri,
                vds_off=vds_off,
                fsw=fsw,
            )
        )
    for dname in ("D1", "D2"):
        budget.update(
            _diode_loss(
                _find_named(tas, dname),
                dname,
                duty_off=duty,
                i_fwd=iout,
                vr=2.0 * vout,
                fsw=fsw,
            )
        )
    budget.update(_inductor_loss_keyed(_find_named(tas, "L1"), "L1"))
    budget.update(_inductor_loss_keyed(_find_named(tas, "T1"), "T1"))
    cout = _find_named(tas, "C_out") or _find_named(tas, "Cout")
    budget.update(_cap_esr_loss(cout, "C_out"))
    return budget


def run_weinberg_analyst(tas: dict[str, Any], spec: Mapping[str, Any]) -> None:
    """Weinberg loss budget + Tj."""
    _run_generic_analyst(tas, spec, _weinberg_op_budget)


# ---------------------------------------------------------------------------
# LLC (and CLLC, CLLLC — same loss structure)
# ---------------------------------------------------------------------------


def _llc_op_budget(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> dict[str, float | None]:
    fsw = _spec_fsw(spec, op_index)
    vout, iout = _spec_vout_iout(spec, op_index)
    vin = _spec_vin_nominal(spec)
    n = _turns_ratio(spec)
    # Half-bridge: each switch conducts ~50% of the period
    ipri = iout / n
    # RMS approximation for sinusoidal resonant current ~ pi/2*sqrt(2) * Iavg
    # Simplified: use Ipri as a conservative proxy for RMS
    irms_pri = ipri

    budget: dict[str, float | None] = {}
    # ZVS: switching losses are 0
    for qname in ("Q1", "Q2"):
        budget.update(
            _mosfet_loss(
                _find_named(tas, qname),
                qname,
                duty=0.5,
                i_on=irms_pri,
                vds_off=vin,
                fsw=fsw,
                zvs=True,
            )
        )
    # Secondary rectifiers: each conducts ~50%, forward current ~ Iout
    for dname in ("D1", "D2"):
        budget.update(
            _diode_loss(
                _find_named(tas, dname),
                dname,
                duty_off=0.5,
                i_fwd=iout,
                vr=2.0 * vout,
                fsw=fsw,
            )
        )
    budget.update(_inductor_loss_keyed(_find_named(tas, "T1"), "T1"))
    budget.update(_inductor_loss_keyed(_find_named(tas, "Lr"), "Lr"))
    # Cr (resonant cap) ESR loss
    budget.update(_cap_esr_loss(_find_named(tas, "Cr"), "Cr"))
    cout = _find_named(tas, "C_out") or _find_named(tas, "Cout")
    budget.update(_cap_esr_loss(cout, "C_out"))
    return budget


def run_llc_analyst(tas: dict[str, Any], spec: Mapping[str, Any]) -> None:
    """LLC loss budget + Tj."""
    _run_generic_analyst(tas, spec, _llc_op_budget)


def run_cllc_analyst(tas: dict[str, Any], spec: Mapping[str, Any]) -> None:
    """CLLC loss budget + Tj (same structure as LLC)."""
    _run_generic_analyst(tas, spec, _llc_op_budget)


def _clllc_op_budget(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> dict[str, float | None]:
    """CLLLC / dual-active-bridge loss budget.

    CLLLC is a DUAL FULL BRIDGE — not the half-bridge + diode rectifier of LLC.
    Primary HV bridge Q1–Q4 carries the reflected output current; the LV
    synchronous-rectifier bridge Q5–Q8 carries the FULL output current (the
    loss term the LLC budget missed entirely — it looked for diodes D1/D2 that
    a synchronous-rectified converter does not have, leaving η ~0.999).
    Resonant ⇒ ZVS, so switching loss ≈ 0; conduction dominates. In a full
    bridge two diagonal FETs conduct each half-period (duty≈0.5 per device).
    """
    fsw = _spec_fsw(spec, op_index)
    vout, iout = _spec_vout_iout(spec, op_index)
    vin = _spec_vin_nominal(spec)
    n = _turns_ratio(spec)
    ipri = iout / n if n else iout  # output current reflected to the HV side

    budget: dict[str, float | None] = {}
    # HV primary full bridge — reflected (small) current.
    for q in ("Q1", "Q2", "Q3", "Q4"):
        budget.update(
            _mosfet_loss(
                _find_named(tas, q),
                q,
                duty=0.5,
                i_on=ipri,
                vds_off=vin,
                fsw=fsw,
                zvs=True,
            )
        )
    # LV synchronous-rectifier full bridge — full output current (dominant).
    for q in ("Q5", "Q6", "Q7", "Q8"):
        budget.update(
            _mosfet_loss(
                _find_named(tas, q),
                q,
                duty=0.5,
                i_on=iout,
                vds_off=vout,
                fsw=fsw,
                zvs=True,
            )
        )
    # Resonant tank + transformer + bus caps (any that are present).
    budget.update(_inductor_loss_keyed(_find_named(tas, "T1"), "T1"))
    for lr in ("Lr", "Lr1", "Lr2"):
        budget.update(_inductor_loss_keyed(_find_named(tas, lr), lr))
    for cr in ("Cr", "Cr1", "Cr2"):
        budget.update(_cap_esr_loss(_find_named(tas, cr), cr))
    for c in ("C_bus_HV", "C_bus_LV", "C_out", "Cout"):
        budget.update(_cap_esr_loss(_find_named(tas, c), c))
    return budget


def run_clllc_analyst(tas: dict[str, Any], spec: Mapping[str, Any]) -> None:
    """CLLLC loss budget + Tj (dual full bridge — distinct from LLC)."""
    _run_generic_analyst(tas, spec, _clllc_op_budget)


# ---------------------------------------------------------------------------
# Series resonant converter (SRC)
# ---------------------------------------------------------------------------


def _src_op_budget(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> dict[str, float | None]:
    """Series-resonant loss budget for one operating point.

    SRC differs from LLC in two structural ways that matter for the
    budget:

    * The switching cell is the same half-bridge (Q_HI / Q_LO, ZVS), but
      the device names follow the SRC stencil (``Q_HI``/``Q_LO`` not
      ``Q1``/``Q2``) and the series-resonant inductor is ``L_r``.
    * Each output rail uses a *full-bridge* diode rectifier
      (``D_h1_{i}`` / ``D_h2_{i}`` / ``D_l1_{i}`` / ``D_l2_{i}``) instead
      of LLC's centre-tapped two-diode rectifier. In a full bridge two
      diodes (a diagonal pair) conduct the load current each half-cycle,
      so each of the four diodes carries the rail current for ~50 % of
      the period: conduction loss per diode = 0.5·Iout_i·Vf, and the
      bridge always has two diodes in series with the load.

    Multi-output: walks every rail (``Vout{i}``/``Iout{i}`` + per-rail
    turns ratio) and sums the reflected primary current, mirroring the
    generic multi-output loss summation used elsewhere.
    """
    fsw = _spec_fsw(spec, op_index)
    vout, iout = _spec_vout_iout(spec, op_index)
    vin = _spec_vin_nominal(spec)

    op = spec.get("operatingPoints") or [{}]
    op_i = op[op_index] if op_index < len(op) else (op[0] if op else {})
    vouts = op_i.get("outputVoltages") or [vout]
    iouts = op_i.get("outputCurrents") or [iout]
    ratios = spec.get("desiredTurnsRatios") or [_turns_ratio(spec)]
    n_rails = max(len(vouts), len(iouts), 1)

    def _rail(seq: Any, i: int, fallback: float) -> float:
        return float(seq[i]) if i < len(seq) else float(fallback)

    # Primary current = sum of reflected secondary load currents (each
    # rail referred through its own turns ratio n_i = N_pri / N_sec_i).
    ipri = 0.0
    for i in range(n_rails):
        iout_i = _rail(iouts, i, iout)
        n_i = _rail(ratios, i, 1.0)
        if n_i > 0:
            ipri += iout_i / n_i

    budget: dict[str, float | None] = {}
    # Half-bridge primary switches (ZVS — resonant soft switching).
    for qname in ("Q_HI", "Q_LO"):
        budget.update(
            _mosfet_loss(
                _find_named(tas, qname),
                qname,
                duty=0.5,
                i_on=ipri,
                vds_off=vin,
                fsw=fsw,
                zvs=True,
            )
        )

    # Per-rail full-bridge diode rectifier: each of the 4 diodes conducts
    # the rail current for ~50 % of the period.
    for i in range(n_rails):
        iout_i = _rail(iouts, i, iout)
        vout_i = _rail(vouts, i, vout)
        for dname in (f"D_h1_{i}", f"D_h2_{i}", f"D_l1_{i}", f"D_l2_{i}"):
            budget.update(
                _diode_loss(
                    _find_named(tas, dname),
                    dname,
                    duty_off=0.5,
                    i_fwd=iout_i,
                    vr=vout_i,
                    fsw=fsw,
                )
            )
        budget.update(_cap_esr_loss(_find_named(tas, f"C_out{i}"), f"C_out{i}"))

    # Resonant tank + transformer magnetics (losses straight from MAS).
    budget.update(_inductor_loss_keyed(_find_named(tas, "T1"), "T1"))
    budget.update(_inductor_loss_keyed(_find_named(tas, "L_r"), "L_r"))
    # Resonant capacitor C_r ESR loss.
    budget.update(_cap_esr_loss(_find_named(tas, "C_r"), "C_r"))
    return budget


def run_series_resonant_analyst(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
) -> None:
    """Series-resonant converter loss budget + Tj."""
    _run_generic_analyst(tas, spec, _src_op_budget)


# ---------------------------------------------------------------------------
# Dual active bridge
# ---------------------------------------------------------------------------


def _dual_active_bridge_op_budget(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> dict[str, float | None]:
    fsw = _spec_fsw(spec, op_index)
    vout, iout = _spec_vout_iout(spec, op_index)
    vin = _spec_vin_nominal(spec)
    n = _turns_ratio(spec)
    ipri = iout / n

    budget: dict[str, float | None] = {}
    # Primary full-bridge Q1-Q4 (ZVS, each conducts ~50%)
    for qname in ("Q1", "Q2", "Q3", "Q4"):
        budget.update(
            _mosfet_loss(
                _find_named(tas, qname),
                qname,
                duty=0.5,
                i_on=ipri,
                vds_off=vin,
                fsw=fsw,
                zvs=True,
            )
        )
    # Secondary full-bridge Q5-Q8 (sync rect, ZVS, each conducts ~50%)
    for qname in ("Q5", "Q6", "Q7", "Q8"):
        budget.update(
            _mosfet_loss(
                _find_named(tas, qname),
                qname,
                duty=0.5,
                i_on=iout,
                vds_off=vout,
                fsw=fsw,
                zvs=True,
            )
        )
    budget.update(_inductor_loss_keyed(_find_named(tas, "T1"), "T1"))
    cout = _find_named(tas, "C_out") or _find_named(tas, "Cout")
    budget.update(_cap_esr_loss(cout, "C_out"))
    return budget


def run_dual_active_bridge_analyst(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
) -> None:
    """Dual active bridge loss budget + Tj."""
    _run_generic_analyst(tas, spec, _dual_active_bridge_op_budget)


# ---------------------------------------------------------------------------
# Isolated buck
# ---------------------------------------------------------------------------


def _isolated_buck_op_budget(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
    *,
    op_index: int = 0,
) -> dict[str, float | None]:
    """Flybuck loss budget.

    The flybuck is a *synchronous buck* on the primary: ``Q1`` (HS) and
    ``Q2`` (LS, sync rectifier) drive the transformer primary, which IS the
    buck inductor. The primary buck rail (``Vout_pri`` = outputVoltages[0])
    is regulated; each isolated secondary ``i`` (``Vout{i}``) rectifies
    through ``D_out{i}`` into ``C_out{i}`` open-loop.

      * duty       D = Vout_pri / Vin  (buck conversion ratio)
      * I_L_pri    = Iout_pri + Σ_i Iout_i / N_i  (the primary winding
        carries its own buck load PLUS every reflected secondary current)

    The per-rail output rectifiers (``D_out{i}``/``C_out{i}``), the
    transformer (``T1`` core+dcr) and the primary buck rail come from
    :func:`_compute_generic_loss_budget`. The generic budget only models the
    high-side switch ``Q1``; we add the low-side synchronous switch ``Q2``
    and the primary buck output cap ``C_pri`` here.
    """
    fsw = _spec_fsw(spec, op_index)
    vin = _spec_vin_nominal(spec)

    op = spec.get("operatingPoints") or [{}]
    op_i = op[op_index] if op_index < len(op) else (op[0] if op else {})
    vouts = op_i.get("outputVoltages") or []
    iouts = op_i.get("outputCurrents") or []
    if not vouts or not iouts:
        raise AnalystError(f"operatingPoints[{op_index}].outputVoltages/outputCurrents required")
    vout_pri = float(vouts[0])
    iout_pri = float(iouts[0])

    # Buck duty from the regulated primary rail.
    duty = vout_pri / vin if vin > 0 else 0.0

    # Primary winding (= buck inductor) current: own load + reflected
    # secondaries. Secondary i reflects Iout_i / N_i to the primary.
    ratios = spec.get("desiredTurnsRatios") or []
    i_l_pri = iout_pri
    for i in range(1, len(iouts)):
        n_i = float(ratios[i - 1]) if (i - 1) < len(ratios) else 1.0
        if n_i <= 0:
            n_i = 1.0
        i_l_pri += float(iouts[i]) / n_i

    # Q1 (HS) + T1 + per-rail D_out{i}/C_out{i} from the generic budget. The
    # generic switch model uses pri_current for the HS device conduction;
    # sec_current is unused here (no buck-class D1 in a flybuck), so pass the
    # primary current for both.
    budget = _compute_generic_loss_budget(
        tas,
        spec,
        duty=duty,
        pri_current=i_l_pri,
        sec_current=i_l_pri,
        op_index=op_index,
    )

    # Low-side synchronous rectifier Q2 conducts during the (1-D) interval,
    # carrying the same primary winding current. vds_off = Vin (it blocks the
    # full bus when the HS is on).
    budget.update(
        _mosfet_loss(
            _find_named(tas, "Q2"),
            "Q2",
            duty=(1.0 - duty),
            i_on=i_l_pri,
            vds_off=vin,
            fsw=fsw,
        )
    )

    # Primary buck output cap (named C_pri in the flybuck stencil, not the
    # buck-class C_out the generic budget looks for).
    budget.update(_cap_esr_loss(_find_named(tas, "C_pri"), "C_pri"))
    return budget


def run_isolated_buck_analyst(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
) -> None:
    """Isolated buck loss budget + Tj."""
    _run_generic_analyst(tas, spec, _isolated_buck_op_budget)


# ---------------------------------------------------------------------------
# Isolated buck-boost (flyback family)
# ---------------------------------------------------------------------------


def run_isolated_buck_boost_analyst(
    tas: dict[str, Any],
    spec: Mapping[str, Any],
) -> None:
    """Isolated buck-boost loss budget + Tj (same as flyback)."""
    _run_generic_analyst(tas, spec, _flyback_op_budget)


# ---------------------------------------------------------------------------
# Per-topology analyst dispatch
# ---------------------------------------------------------------------------

# Per-topology analyst dispatch. Add new topologies here as their loss
# formulas are wired.
_ANALYSTS: dict[str, Any] = {
    "buck": run_buck_analyst,
    "boost": run_boost_analyst,
    "cuk": run_cuk_analyst,
    "flyback": run_flyback_analyst,
    "sepic": run_sepic_analyst,
    "zeta": run_zeta_analyst,
    "four_switch_buck_boost": run_four_switch_buck_boost_analyst,
    "single_switch_forward": run_single_switch_forward_analyst,
    "two_switch_forward": run_two_switch_forward_analyst,
    "active_clamp_forward": run_active_clamp_forward_analyst,
    "push_pull": run_push_pull_analyst,
    "asymmetric_half_bridge": run_asymmetric_half_bridge_analyst,
    "phase_shifted_full_bridge": run_phase_shifted_full_bridge_analyst,
    "weinberg": run_weinberg_analyst,
    "llc": run_llc_analyst,
    "cllc": run_cllc_analyst,
    "clllc": run_clllc_analyst,
    "series_resonant": run_series_resonant_analyst,
    "dual_active_bridge": run_dual_active_bridge_analyst,
    "isolated_buck": run_isolated_buck_analyst,
    "isolated_buck_boost": run_isolated_buck_boost_analyst,
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
    "run_active_clamp_forward_analyst",
    "run_analyst",
    "run_asymmetric_half_bridge_analyst",
    "run_boost_analyst",
    "run_buck_analyst",
    "run_cllc_analyst",
    "run_clllc_analyst",
    "run_cuk_analyst",
    "run_dual_active_bridge_analyst",
    "run_flyback_analyst",
    "run_four_switch_buck_boost_analyst",
    "run_isolated_buck_analyst",
    "run_isolated_buck_boost_analyst",
    "run_llc_analyst",
    "run_phase_shifted_full_bridge_analyst",
    "run_push_pull_analyst",
    "run_sepic_analyst",
    "run_series_resonant_analyst",
    "run_single_switch_forward_analyst",
    "run_two_switch_forward_analyst",
    "run_weinberg_analyst",
    "run_zeta_analyst",
    "stamp_junction_temperatures",
]
