"""Fill a Kirchhoff TAS's per-component design requirements with real parts.

Kirchhoff (the deterministic sim/circuit engine) emits each TAS component as a
SEED carrying a typed design requirement — the *BOM to fill*. This module is the
HS side of that contract: for each semiconductor / capacitor seed it builds the
catalogue selector's constraints from Kirchhoff's requirement, selects a real
part from the internal DB, and stamps the part's PEAS envelope into the
component's ``data`` slot — which promotes that component to DATASHEET fidelity
in Kirchhoff's deck emission (``infer_fidelity``: a bound part -> real model).

The MAGNETIC is intentionally NOT selected here: per the della-Pollock
magnetic-first method it is designed by MKF (and stamped as MKF_MODEL elsewhere);
this module reports it as deferred so the caller wires the MKF magnetic in.

Fail-loud: a requirement with no satisfying part raises ``KirchhoffFillError``
(never a silent skip — a missing part must surface, not degrade the deck).
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from heaviside.catalogue.selector import (
    CapacitorConstraints,
    CapacitorTiebreaker,
    ControllerConstraints,
    DiodeConstraints,
    DiodeTiebreaker,
    MosfetConstraints,
    MosfetTiebreaker,
    ResistorConstraints,
    SelectionError,
    select_capacitor,
    select_controller,
    select_diode,
    select_mosfet,
    select_resistor,
)

_FAMILIES = ("semiconductor", "magnetic", "capacitor", "resistor", "analog", "controller")

# Numerical convergence aids the Kirchhoff assembler injects into the IDEAL-switch
# deck (Csn*/Rsn*/Csw* — see Kirchhoff TasAssembler::is_numerical_snubber). They tame
# an ideal switch's infinite dV/dt; they are NOT physical parts to source (a real
# switch's Coss does the job). The fill must skip them even though they carry a
# capacitance designRequirement — otherwise HS sources a real 2.2 nF part for a solver
# aid. Same name convention as Kirchhoff's, so the two stay in lockstep.
def _is_numerical_aid(name: str | None) -> bool:
    n = name or ""
    return n.startswith(("Csn", "Rsn", "Csw"))


class KirchhoffFillError(RuntimeError):
    """A Kirchhoff component requirement could not be filled with a real part."""


def _mosfet_constraints(req: dict[str, Any]) -> MosfetConstraints:
    return MosfetConstraints(
        vds_min=float(req["ratedDrainSourceVoltage"]),
        id_min=float(req["ratedContinuousDrainCurrent"]),
        rds_on_max=float(req["maximumOnResistance"]),
        qg_max=math.inf,  # Kirchhoff does not emit a gate-charge limit
    )


def _diode_constraints(req: dict[str, Any]) -> DiodeConstraints:
    # Kirchhoff emits maximumReverseRecoveryTime (a time); the selector constrains
    # qrr (a charge), so trr is not mapped here — the chosen part's real qrr/trr
    # rides along in the stamped envelope and is reflected in the sim.
    return DiodeConstraints(
        vrrm_min=float(req["ratedReverseVoltage"]),
        if_avg_min=float(req["ratedForwardCurrent"]),
    )


# Cap upper bound: the requirement's capacitance is the *minimum* for ripple.
# LOWEST_ESR otherwise picks the biggest low-ESR electrolytic it can find (3300 µF
# for a 21.5 µF need) — which misdesigns the filter AND, via a huge output RC,
# breaks the regulated transient sim's settle/measurement. Keep the part close to
# the design value (modest ripple headroom), not a giant electrolytic.
_CAP_OVERSIZE_MAX = 2.0


def _capacitor_constraints(req: dict[str, Any]) -> CapacitorConstraints:
    cnom = float(req["capacitance"]["nominal"])
    ripple = req.get("minimumRippleCurrent")
    return CapacitorConstraints(
        capacitance_min=cnom,
        capacitance_max=cnom * _CAP_OVERSIZE_MAX,
        v_rated_min=float(req["ratedVoltage"]),
        ripple_current_min=float(ripple) if isinstance(ripple, (int, float)) else None,
    )


def stamp_mkf_magnetic(
    tas: dict[str, Any],
    magnetic: dict[str, Any],
    *,
    pyom: Any,
    component_name: str | None = None,
) -> dict[str, Any]:
    """Stamp an MKF-designed magnetic into the Kirchhoff TAS as MKF_MODEL.

    This is the della-Pollock magnetic path: MKF designs the magnetic (the
    ``magnetic`` object — core + coil), here it is exported as a SPICE subcircuit
    (``pyom.export_magnetic_as_subcircuit``) and stamped into the TAS magnetic
    slot as ``magnetic.modelOutputs.spiceSubcircuit = {text, reference}`` — the
    shape Kirchhoff's assembler hoists + instantiates, and which ``infer_fidelity``
    promotes to MKF_MODEL (real Rdc + AC ladder + magnetizing L). The slot's
    ``inputs.designRequirements`` (turnsRatios) is preserved for winding/port
    wiring.

    ``pyom`` is the imported PyOpenMagnetics module (injected by the caller, e.g.
    ``bridge._import_pyom_vendor()``). Fail-loud ``KirchhoffFillError`` on a bad
    export or if there is no magnetic component to stamp.
    """
    subckt = pyom.export_magnetic_as_subcircuit(magnetic)
    if not isinstance(subckt, str) or ".subckt" not in subckt.lower():
        raise KirchhoffFillError("export_magnetic_as_subcircuit returned no .subckt text")
    m = re.search(r"(?mi)^\.subckt\s+(\S+)", subckt)
    if m is None:
        raise KirchhoffFillError("exported subcircuit has no '.subckt <name>' line")
    ref = m.group(1)

    stamped = 0
    for st in tas.get("topology", {}).get("stages", []):
        for comp in st.get("circuit", {}).get("components", []):
            data = comp.get("data")
            if not isinstance(data, dict) or "magnetic" not in data:
                continue
            if component_name is not None and comp.get("name") != component_name:
                continue
            # Replace the magnetic seed's family slot; keep data.inputs (turnsRatios)
            # which the assembler needs to wire the subcircuit's P<i>± ports.
            data["magnetic"] = {"modelOutputs": {"spiceSubcircuit": {"text": subckt, "reference": ref}}}
            stamped += 1

    if stamped == 0:
        raise KirchhoffFillError(
            f"no magnetic component to stamp"
            + (f" (name={component_name!r})" if component_name else "")
        )
    return {"reference": ref, "stamped": stamped}


def fill_kirchhoff_bom(
    tas: dict[str, Any],
    *,
    topology: str | None = None,
    tas_data_dir: Path | None = None,
    # LOWEST_RDS_ON needs no operating-point fields (LOWEST_TOTAL_LOSS would require
    # op_i_rms/vds/duty/fsw, which Kirchhoff's rating-only requirement doesn't carry).
    mosfet_tiebreaker: MosfetTiebreaker = MosfetTiebreaker.LOWEST_RDS_ON,
    diode_tiebreaker: DiodeTiebreaker = DiodeTiebreaker.LOWEST_VF,
    capacitor_tiebreaker: CapacitorTiebreaker = CapacitorTiebreaker.LOWEST_ESR,
) -> list[dict[str, Any]]:
    """Select + stamp a real part for every fillable component seed in ``tas``.

    Mutates ``tas`` in place (stamps the chosen part's PEAS envelope into each
    component's ``data``). Returns one record per component:
    ``{name, family, kind, mpn?, filled, deferred?}``. The magnetic is recorded
    as deferred to MKF (della-Pollock). Raises ``KirchhoffFillError`` if any
    requirement has no satisfying part.
    """
    topo = tas.get("topology")
    if not isinstance(topo, dict) or not isinstance(topo.get("stages"), list):
        raise KirchhoffFillError("TAS has no topology.stages[] to fill")

    # Converter-level quantities the controller selector needs (Vin, fsw) — read from
    # the TAS designRequirements so a controller seed can be sourced by topology+Vin+fsw.
    _dr = tas.get("inputs", {}).get("designRequirements", {})
    def _scalar(x: Any) -> float | None:
        if isinstance(x, dict):
            x = x.get("nominal") or x.get("minimum") or x.get("maximum")
        return float(x) if isinstance(x, (int, float)) else None
    _vin = _scalar(_dr.get("inputVoltage"))
    _fsw = _scalar(_dr.get("switchingFrequency"))

    records: list[dict[str, Any]] = []
    for st in topo["stages"]:
        for comp in st.get("circuit", {}).get("components", []):
            data = comp.get("data")
            if not isinstance(data, dict):
                continue
            family = next((f for f in _FAMILIES if f in data), None)
            if family is None:
                continue
            slot = data.get(family)
            kind = next(iter(slot), None) if isinstance(slot, dict) and slot else None
            req = data.get("inputs", {}).get("designRequirements", {})
            name = comp.get("name")
            rec: dict[str, Any] = {"name": name, "family": family, "kind": kind}

            # Phase 0: numerical convergence aids are sim-only, never sourced.
            if _is_numerical_aid(name):
                rec.update(filled=False, deferred="numerical convergence aid (sim-only, not sourced)")
                records.append(rec)
                continue

            try:
                if family == "semiconductor" and kind == "mosfet":
                    sel = select_mosfet(
                        _mosfet_constraints(req), tiebreaker=mosfet_tiebreaker, tas_data_dir=tas_data_dir
                    )
                    data["semiconductor"] = sel.chosen.raw_envelope["semiconductor"]
                    rec.update(mpn=sel.chosen.mpn, filled=True, selection=sel, requirement=req)
                elif family == "semiconductor" and kind == "diode":
                    sel = select_diode(
                        _diode_constraints(req), tiebreaker=diode_tiebreaker, tas_data_dir=tas_data_dir
                    )
                    data["semiconductor"] = sel.chosen.raw_envelope["semiconductor"]
                    rec.update(mpn=sel.chosen.mpn, filled=True, selection=sel, requirement=req)
                elif family == "capacitor":
                    sel = select_capacitor(
                        _capacitor_constraints(req), tiebreaker=capacitor_tiebreaker, tas_data_dir=tas_data_dir
                    )
                    data["capacitor"] = sel.chosen.raw_envelope["capacitor"]
                    rec.update(mpn=sel.chosen.mpn, filled=True, selection=sel, requirement=req)
                elif family == "resistor":
                    # snubber damping R / current sense / bias / feedback divider.
                    sel = select_resistor(
                        ResistorConstraints(
                            target_ohms=float(req["resistance"]["nominal"]),
                            max_tolerance=float(req.get("tolerance", 0.05)),
                            max_value_deviation=0.2),
                        tas_data_dir=tas_data_dir)
                    data["resistor"] = sel.chosen.raw_envelope["resistor"]
                    rec.update(mpn=sel.chosen.mpn, filled=True, selection=sel, requirement=req)
                elif family == "magnetic":
                    # della-Pollock: the magnetic is MKF's (MKF_MODEL), wired by the caller.
                    rec.update(filled=False, deferred="MKF magnetic-first (MKF_MODEL)")
                elif family == "controller":
                    # Phase 1: source the control IC / gate driver (CTAS family) by
                    # topology + Vin + fsw. Needs the converter context; if absent
                    # (a bare BOM fill with no topology/spec) defer rather than fail.
                    _cat = req.get("function", {}).get("category") if isinstance(req.get("function"), dict) else None
                    _cat = _cat if isinstance(_cat, str) and _cat else None
                    if topology is None or _vin is None or _fsw is None:
                        rec.update(filled=False, deferred="controller: need topology + Vin + fsw to source")
                    else:
                        # A missing control IC is a CATALOG GAP, surfaced (filled=False with the
                        # reason) — NOT a hard failure that sinks an otherwise-valid power design
                        # (unlike a missing power semi/cap, which stays fail-loud). The unfilled
                        # record is visible to the gate/completeness check.
                        try:
                            sel = select_controller(
                                ControllerConstraints(
                                    topology=topology, vin_nom=_vin,
                                    fsw_khz=_fsw / 1000.0, integrated_fet=None, category=_cat),
                                tas_data_dir=tas_data_dir)
                            data["controller"] = sel.chosen.raw_envelope
                            rec.update(mpn=sel.chosen.mpn, filled=True, selection=sel, requirement=req)
                        except SelectionError:
                            rec.update(filled=False,
                                       deferred=f"no catalog control IC for {topology}/{_cat or 'any'}")
                else:
                    rec.update(filled=False, deferred=f"no filler for {family}/{kind}")
            except SelectionError as exc:
                raise KirchhoffFillError(
                    f"no internal-DB part satisfies {name} ({family}/{kind}): {exc}"
                ) from exc
            records.append(rec)

    return records


def unify_hs_tas_semiconductors(
    hs_tas: dict[str, Any], fill_records: list[dict[str, Any]]
) -> int:
    """Re-stamp HS's TAS power semiconductors (MOSFET/diode) with the parts the
    Kirchhoff-requirement fill chose — so the realism gate validates exactly the
    devices the Kirchhoff sim used. This makes Kirchhoff's per-component
    requirement the SINGLE part-selection authority (no drift versus HS's parallel
    analytical stress deriver). Stress for the gate's rating checks comes from the
    Kirchhoff requirement's ratings. Matched by device kind in declaration order;
    fail-loud if a Kirchhoff selection has no HS counterpart (TAS shapes diverge).

    Returns the count re-stamped. Capacitors / synthesized aux parts are left to
    HS's assemble_bom_from_tas for now — a documented follow-up."""
    from heaviside.catalogue.assemble import _stamp_diode, _stamp_mosfet

    by_kind: dict[str, list[dict[str, Any]]] = {"mosfet": [], "diode": []}
    for r in fill_records:
        if r.get("filled") and r.get("family") == "semiconductor" and r.get("kind") in by_kind:
            by_kind[r["kind"]].append(r)

    restamped = 0
    for st in hs_tas.get("topology", {}).get("stages", []):
        for comp in st.get("circuit", {}).get("components", []):
            data = comp.get("data")
            if not isinstance(data, dict) or not isinstance(data.get("semiconductor"), dict):
                continue
            semi = data["semiconductor"]
            kind = "mosfet" if "mosfet" in semi else "diode" if "diode" in semi else None
            if kind is None or not by_kind[kind]:
                continue
            rec = by_kind[kind].pop(0)
            sel, req = rec["selection"], rec["requirement"]
            # Swap only the PART (ratings/Rds_on/Qg from the Kirchhoff selection);
            # PRESERVE the operating stress HS already stamped (vds_stress / v_reverse
            # = the actual switch voltage at the operating point). The Kirchhoff
            # requirement's ratedDrainSourceVoltage / ratedReverseVoltage are the
            # REQUIRED ratings (operating / vDerate), NOT the operating stress — using
            # them as the stress collapses the derating ratio to ~1.0 (stress==rating)
            # and the gate spuriously fails a sound design. Fall back to the rating
            # only if HS left no stress (then the check is conservative, not wrong).
            def _pos(v: Any, fallback: float) -> float:
                return float(v) if isinstance(v, (int, float)) and v > 0 else float(fallback)
            if kind == "mosfet":
                _stamp_mosfet(
                    comp, sel,
                    stress_vds=_pos(comp.get("vds_stress"), req["ratedDrainSourceVoltage"]),
                    stress_id=_pos(comp.get("id_stress"), req["ratedContinuousDrainCurrent"]),
                )
            else:
                _stamp_diode(
                    comp, sel,
                    stress_vr=_pos(comp.get("v_reverse"), req["ratedReverseVoltage"]),
                    stress_if_avg=_pos(comp.get("if_avg_stress"), req["ratedForwardCurrent"]),
                )
            restamped += 1

    leftover = sum(len(v) for v in by_kind.values())
    if leftover:
        raise KirchhoffFillError(
            f"BOM unification: {leftover} Kirchhoff semiconductor selection(s) had no "
            f"matching HS-TAS component (re-stamped {restamped}); TAS shapes diverge"
        )
    return restamped


# Kirchhoff power-capacitor role -> HS TAS designator. HS's synthesized aux caps
# (Cboot/Cvcc/Css) are intentionally NOT listed — Kirchhoff doesn't emit them, so
# they keep HS's own selection.
_CAP_ROLE_TO_HS_NAME = {"outputFilter": "C_out", "inputFilter": "Cin"}


def unify_hs_tas_capacitors(hs_tas: dict[str, Any], fill_records: list[dict[str, Any]]) -> int:
    """Re-stamp HS's TAS power capacitors (output / input filter) with the parts
    the Kirchhoff fill chose, matched by Kirchhoff role → HS designator
    (``outputFilter``→``C_out``, ``inputFilter``→``Cin``). HS's synthesized aux
    caps (Cboot/Cvcc/Css) are left untouched. Lenient (unlike the semiconductor
    unifier): a Kirchhoff cap with no HS counterpart is left to HS's own
    selection rather than fail-loud — cap designators vary by topology/TAS shape
    and the output cap is lower-stress than the semis. Returns the count
    re-stamped."""
    from heaviside.catalogue.assemble import _stamp_capacitor

    hs_caps: dict[str, dict[str, Any]] = {}
    for st in hs_tas.get("topology", {}).get("stages", []):
        for comp in st.get("circuit", {}).get("components", []):
            data = comp.get("data")
            if isinstance(data, dict) and "capacitor" in data:
                hs_caps[comp.get("name")] = comp

    restamped = 0
    for r in fill_records:
        if not (r.get("filled") and r.get("family") == "capacitor"):
            continue
        req = r.get("requirement") or {}
        comp = hs_caps.get(_CAP_ROLE_TO_HS_NAME.get(req.get("role")))
        if comp is None:  # no HS counterpart — leave HS's selection
            continue
        ripple = req.get("minimumRippleCurrent")
        _stamp_capacitor(
            comp,
            r["selection"],
            stress_v=float(req["ratedVoltage"]),
            stress_ripple=float(ripple) if isinstance(ripple, (int, float)) else 0.0,
        )
        restamped += 1
    return restamped
