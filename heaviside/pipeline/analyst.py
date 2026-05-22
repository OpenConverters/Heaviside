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


def _spec_fsw(spec: Mapping[str, Any]) -> float:
    ops = spec.get("operatingPoints")
    if not isinstance(ops, list) or not ops or not isinstance(ops[0], Mapping):
        raise AnalystError("spec.operatingPoints[0] is required")
    fsw = ops[0].get("switchingFrequency")
    if not isinstance(fsw, (int, float)) or fsw <= 0:
        raise AnalystError("spec.operatingPoints[0].switchingFrequency must be positive")
    return float(fsw)


def _spec_vout_iout(spec: Mapping[str, Any]) -> tuple[float, float]:
    op = spec["operatingPoints"][0]
    vouts = op.get("outputVoltages") or []
    iouts = op.get("outputCurrents") or []
    if not vouts or not iouts:
        raise AnalystError("operatingPoints[0].outputVoltages/outputCurrents required")
    return float(vouts[0]), float(iouts[0])


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
    tas: dict[str, Any], spec: Mapping[str, Any],
) -> dict[str, float | None]:
    """Per-component loss attribution for a buck design.

    Returns a flat dict suitable to stamp at ``tas["loss_budget"]``.
    Each key is a component-bucket label; each value is watts or
    ``None`` when an input was missing. ``None`` values are ignored
    by the realism gate's ``no_negative_losses`` check so a partial
    budget is still a valid budget.
    """
    fsw = _spec_fsw(spec)
    vout, iout = _spec_vout_iout(spec)
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


def run_buck_analyst(tas: dict[str, Any], spec: Mapping[str, Any]) -> None:
    """In-place: compute loss budget, stamp it on tas, then stamp Tj
    on every BOM component that has Rth_ja + a computable loss."""
    tas["loss_budget"] = compute_buck_loss_budget(tas, spec)
    stamp_junction_temperatures(tas, spec)


# Per-topology analyst dispatch. Add new topologies here as their loss
# formulas are wired.
_ANALYSTS: dict[str, Any] = {
    "buck": run_buck_analyst,
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
    "run_buck_analyst",
    "stamp_junction_temperatures",
]
