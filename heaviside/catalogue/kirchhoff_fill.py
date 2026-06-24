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
    DiodeConstraints,
    DiodeTiebreaker,
    MosfetConstraints,
    MosfetTiebreaker,
    SelectionError,
    select_capacitor,
    select_diode,
    select_mosfet,
)

_FAMILIES = ("semiconductor", "magnetic", "capacitor", "resistor", "analog", "controller")


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


# Cap upper bound: the requirement's capacitance is the *minimum* for ripple;
# without an upper bound LOWEST_ESR picks the biggest low-ESR electrolytic it can
# find (e.g. 3300 µF for a 21.5 µF need), which both misdesigns the filter and
# breaks the fixed-window transient sim. Allow generous headroom, not absurdity.
_CAP_OVERSIZE_MAX = 10.0


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

            try:
                if family == "semiconductor" and kind == "mosfet":
                    sel = select_mosfet(
                        _mosfet_constraints(req), tiebreaker=mosfet_tiebreaker, tas_data_dir=tas_data_dir
                    )
                    data["semiconductor"] = sel.chosen.raw_envelope["semiconductor"]
                    rec.update(mpn=sel.chosen.mpn, filled=True)
                elif family == "semiconductor" and kind == "diode":
                    sel = select_diode(
                        _diode_constraints(req), tiebreaker=diode_tiebreaker, tas_data_dir=tas_data_dir
                    )
                    data["semiconductor"] = sel.chosen.raw_envelope["semiconductor"]
                    rec.update(mpn=sel.chosen.mpn, filled=True)
                elif family == "capacitor":
                    sel = select_capacitor(
                        _capacitor_constraints(req), tiebreaker=capacitor_tiebreaker, tas_data_dir=tas_data_dir
                    )
                    data["capacitor"] = sel.chosen.raw_envelope["capacitor"]
                    rec.update(mpn=sel.chosen.mpn, filled=True)
                elif family == "magnetic":
                    # della-Pollock: the magnetic is MKF's (MKF_MODEL), wired by the caller.
                    rec.update(filled=False, deferred="MKF magnetic-first (MKF_MODEL)")
                else:
                    rec.update(filled=False, deferred=f"no filler for {family}/{kind}")
            except SelectionError as exc:
                raise KirchhoffFillError(
                    f"no internal-DB part satisfies {name} ({family}/{kind}): {exc}"
                ) from exc
            records.append(rec)

    return records
