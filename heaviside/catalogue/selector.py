"""Typed MOSFET selector backed by ``TAS/data/mosfets.ndjson``.

Design contract (per CLAUDE.md "no fallbacks, throw"):

* Inputs are a ``MosfetConstraints`` dataclass — every field required,
  every field derived analytically from the converter spec by the
  caller (no magic defaults here).
* Output is a ``MosfetSelection`` dataclass carrying the chosen
  ``Mosfet`` typed view, the constraints we asked for, the margins we
  achieved, the explicit ``MosfetTiebreaker`` policy that picked it,
  and the count of alternatives considered.
* If zero candidates satisfy the constraints, raise
  :class:`SelectionError` with the rejection histogram. The caller
  (typically the bridge attach phase) decides whether to widen the
  search, queue a librarian fetch, or fail the design.

No silent ranking. The tiebreaker policy is explicit and the chosen
field's value is reported, so an auditor can re-execute the choice
deterministically.
"""

from __future__ import annotations

import enum
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

try:
    from enum import StrEnum
except ImportError:  # Python < 3.11

    class StrEnum(enum.StrEnum):  # type: ignore[no-redef]
        pass


from pathlib import Path
from typing import Any, Final

from heaviside.catalogue._reader import iter_envelopes

# The one dimensionWithTolerance resolver, mirroring PEAS::resolve_dimensional_values.
# A zener's breakdownVoltage is {minimum, nominal, maximum}, so it must be collapsed with
# the shared rule rather than hand-reading a bound here — house rule, and it is the
# difference between agreeing with Kelvin and quietly disagreeing with it.
from heaviside.report.model import _resolve as resolve_dimensional_value

# Default location of TAS/data/. ``HEAVISIDE_TAS_DATA_DIR`` lets tests
# point at fixtures (matches the convention used by
# heaviside.librarian.safe_access).
_REPO_ROOT: Final = Path(__file__).resolve().parents[2]
_DEFAULT_TAS_DATA_DIR: Final = _REPO_ROOT / "TAS" / "data"


def _tas_data_dir() -> Path:
    env = os.environ.get("HEAVISIDE_TAS_DATA_DIR")
    return Path(env) if env else _DEFAULT_TAS_DATA_DIR


# ---------------------------------------------------------------------------
# Typed mosfet view (subset of the CAS schema actually used by the selector)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Mosfet:
    """Subset of a TAS mosfet envelope actually consumed by the selector
    and downstream realism gate.

    Generated dynamically by :meth:`from_envelope` from the canonical
    nested JSON shape (``semiconductor.mosfet.manufacturerInfo.
    datasheetInfo.{electrical,part}``). The full schema classes live in
    ``heaviside.types`` (quicktype, ``make types``); this dataclass stays
    the selector's conversion target — the field set is intentionally
    narrow.
    """

    mpn: str
    manufacturer: str
    vds_rated: float  # drainSourceVoltage (volts)
    id_continuous: float  # continuousDrainCurrent (amps, Tc-spec)
    rds_on: float  # onResistance (ohms at gate_vgs / id_test)
    qg_total: float  # totalGateCharge (coulombs)
    coss: float  # outputCapacitance (farads); 0.0 for legacy rows -> switching-loss term vacuous
    vgs_threshold_max: float  # gateThresholdVoltage.maximum (volts)
    rth_ja: float | None  # thermalResistanceJunctionAmbient (K/W)
    rth_jc: float | None  # thermalResistanceJunctionCase (K/W)
    tj_max: float | None  # junctionTemperatureMax (°C)
    case: str  # package code from part.case
    technology: str  # Si / SiC / GaN
    status: str  # production / discontinued
    datasheet_url: str
    raw_envelope: Mapping[str, Any]  # for provenance / librarian round-trip

    @classmethod
    def from_envelope(cls, env: Mapping[str, Any]) -> Mosfet | None:
        """Project a TAS mosfet envelope into the selector's typed view.

        Returns ``None`` if any field the selector relies on is missing,
        non-numeric, or otherwise unreadable. Callers iterate
        permissively across the corpus (a row with missing fields is
        skipped, not raised) because the auditor's job — not the
        selector's — is to flag schema-incomplete rows.
        """
        try:
            mosfet = env["semiconductor"]["mosfet"]
            mi = mosfet["manufacturerInfo"]
            di = mi["datasheetInfo"]
            elec = di["electrical"]
            part = di.get("part") or {}
        except (KeyError, TypeError):
            return None

        mpn = mi.get("reference")
        manufacturer = mi.get("name")
        if not isinstance(mpn, str) or not isinstance(manufacturer, str):
            return None

        vds_rated = elec.get("drainSourceVoltage")
        id_cont = elec.get("continuousDrainCurrent")
        rds_on = elec.get("onResistance")
        qg_total = elec.get("totalGateCharge")
        if not all(isinstance(x, (int, float)) and x > 0 for x in (vds_rated, id_cont, rds_on)):
            return None
        if qg_total is None:
            qg_total = 0.0  # legacy rows; Qg constraint becomes vacuous
        if not isinstance(qg_total, (int, float)) or qg_total < 0:
            return None

        coss = elec.get("outputCapacitance")
        if coss is None:
            coss = 0.0  # legacy rows; the Coss switching-loss term becomes vacuous
        if not isinstance(coss, (int, float)) or coss < 0:
            return None

        vgs_th = elec.get("gateThresholdVoltage")
        vgs_th_max = vgs_th.get("maximum") if isinstance(vgs_th, Mapping) else vgs_th
        if not isinstance(vgs_th_max, (int, float)):
            vgs_th_max = 0.0  # rare; constraint becomes vacuous if caller cares

        case = part.get("case")
        technology = part.get("technology")
        if not isinstance(case, str):
            case = ""
        if not isinstance(technology, str):
            technology = ""

        status = mi.get("status")
        if not isinstance(status, str):
            status = "unknown"

        ds_url = mi.get("datasheetUrl")
        if not isinstance(ds_url, str):
            ds_url = ""

        thermal = di.get("thermal") or {}
        rth_ja_raw = thermal.get("thermalResistanceJunctionAmbient")
        rth_ja = (
            float(rth_ja_raw) if isinstance(rth_ja_raw, (int, float)) and rth_ja_raw > 0 else None
        )
        rth_jc_raw = thermal.get("thermalResistanceJunctionCase")
        rth_jc = (
            float(rth_jc_raw) if isinstance(rth_jc_raw, (int, float)) and rth_jc_raw > 0 else None
        )
        tj_max_raw = thermal.get("junctionTemperatureMax")
        tj_max = float(tj_max_raw) if isinstance(tj_max_raw, (int, float)) else None

        return cls(
            mpn=mpn,
            manufacturer=manufacturer,
            vds_rated=float(vds_rated),
            id_continuous=float(id_cont),
            rds_on=float(rds_on),
            qg_total=float(qg_total),
            coss=float(coss),
            vgs_threshold_max=float(vgs_th_max),
            rth_ja=rth_ja,
            rth_jc=rth_jc,
            tj_max=tj_max,
            case=case,
            technology=technology,
            status=status,
            datasheet_url=ds_url,
            raw_envelope=env,
        )


# ---------------------------------------------------------------------------
# Constraints + selection types
# ---------------------------------------------------------------------------


class MosfetTiebreaker(StrEnum):
    """Explicit tiebreaker policy for selecting among multiple candidates
    that satisfy every constraint. Caller picks one; no implicit default.

    The string values double as the provenance label written to the
    realism gate's audit trail.
    """

    LOWEST_RDS_ON = "lowest_rds_on"
    LOWEST_QG = "lowest_qg"
    HIGHEST_VDS_MARGIN = "highest_vds_margin"
    HIGHEST_ID_MARGIN = "highest_id_margin"
    # Minimise conduction + switching loss at the operating point. Requires
    # the op-point fields on MosfetConstraints. Balances Rds_on vs Qg —
    # naturally favours low-Qg (GaN) parts as fsw rises, which a single-axis
    # LOWEST_RDS_ON misses (it picks huge low-Rds_on Si FETs that hard-switch
    # at high loss). This is the engineering-correct pick for high fsw/power.
    LOWEST_TOTAL_LOSS = "lowest_total_loss"


@dataclass(frozen=True, slots=True)
class MosfetConstraints:
    """Stress-derived MOSFET requirements.

    Every field is required and must be supplied by the caller from
    the spec / topology / operating-point analysis. ``vds_min`` /
    ``id_min`` are the *minimum* ratings the picked part must carry;
    the realism gate enforces additional margin on top.

    Optional filters (``technology_allowed``, ``case_disallowed``,
    ``exclude_discontinued``) narrow the candidate pool BEFORE the
    tiebreaker runs.
    """

    vds_min: float
    id_min: float
    rds_on_max: float
    qg_max: float
    technology_allowed: frozenset[str] = frozenset({"Si", "SiC", "GaN"})
    exclude_discontinued: bool = True
    # Operating point for the LOWEST_TOTAL_LOSS tiebreaker (optional).
    # When all four are set, the selector ranks candidates by
    # duty*i_rms^2*Rds_on + 0.5*vds*i_rms*Qg*fsw/Ig.
    op_i_rms: float | None = None
    op_vds: float | None = None
    op_duty: float | None = None
    op_fsw: float | None = None

    def __post_init__(self) -> None:
        for name in ("vds_min", "id_min", "rds_on_max", "qg_max"):
            val = getattr(self, name)
            if not isinstance(val, (int, float)) or val <= 0:
                raise ValueError(f"MosfetConstraints.{name} must be a positive number, got {val!r}")
        if not self.technology_allowed:
            raise ValueError("MosfetConstraints.technology_allowed cannot be empty")


@dataclass(frozen=True, slots=True)
class MosfetSelection:
    """Result of a successful :func:`select_mosfet` call.

    The ``constraints`` and ``tiebreaker`` fields together with
    ``alternatives_considered`` make the selection auditable: a
    reviewer can re-run with the same NDJSON snapshot and constraints
    and deterministically get the same MPN.

    ``margins`` records (rated / requirement) ratios, NOT (rated -
    requirement) absolutes. ratio ≥ 1.0 = satisfies; ratio = 2.0 =
    100 % headroom.
    """

    chosen: Mosfet
    constraints: MosfetConstraints
    tiebreaker: MosfetTiebreaker
    margins: Mapping[str, float]
    alternatives_considered: int


class SelectionError(LookupError):
    """No TAS row satisfies the given constraints.

    Shared across mosfet / diode / capacitor selectors — ``constraints``
    is the typed constraints dataclass used by the caller (a
    ``MosfetConstraints``, ``DiodeConstraints``, or
    ``CapacitorConstraints``). The ``rejection_counts`` field records
    *why* candidates were rejected (how many fell on Vds, how many on
    Id, etc.) so the caller can either loosen the constraint, widen
    the technology allowlist, or queue a librarian fetch for a part
    class the DB is missing.
    """

    def __init__(
        self,
        constraints: Any,
        rejection_counts: Mapping[str, int],
        total_rows_considered: int,
    ) -> None:
        self.constraints = constraints
        self.rejection_counts = dict(rejection_counts)
        self.total_rows_considered = total_rows_considered
        super().__init__(
            f"no {type(constraints).__name__} candidate in TAS satisfies "
            f"{constraints!r}. Considered {total_rows_considered} rows; "
            f"rejected by {dict(rejection_counts)}"
        )


# ---------------------------------------------------------------------------
# select_mosfet
# ---------------------------------------------------------------------------


def select_mosfet(
    c: MosfetConstraints,
    *,
    tiebreaker: MosfetTiebreaker,
    tas_data_dir: Path | None = None,
) -> MosfetSelection:
    """Walk ``TAS/data/mosfets.ndjson`` and return the best fit.

    Raises :class:`SelectionError` if zero rows pass every constraint
    — caller MUST handle this. Reading errors on the NDJSON file
    surface as :class:`CatalogueReadError`.
    """
    from heaviside.catalogue import kelvin_adapter

    req = {"ratedDrainSourceVoltage": c.vds_min, "ratedContinuousDrainCurrent": c.id_min,
           "maximumOnResistance": c.rds_on_max}
    opts: dict[str, Any] = {"tiebreaker": str(tiebreaker), "excludeDiscontinued": c.exclude_discontinued}
    if c.qg_max != float("inf"):
        opts["qgMax"] = c.qg_max
    if c.technology_allowed and set(c.technology_allowed) != {"Si", "SiC", "GaN"}:
        opts["technologyAllowed"] = sorted(c.technology_allowed)
    _op = (c.op_i_rms, c.op_vds, c.op_duty, c.op_fsw)
    if all(isinstance(x, (int, float)) and x > 0 for x in _op):
        opts["operatingPoint"] = {"iRms": c.op_i_rms, "vds": c.op_vds, "duty": c.op_duty, "fsw": c.op_fsw}
    elif tiebreaker is MosfetTiebreaker.LOWEST_TOTAL_LOSS:
        raise ValueError(
            "LOWEST_TOTAL_LOSS requires op_i_rms/op_vds/op_duty/op_fsw on MosfetConstraints"
        )

    pk = kelvin_adapter.PyKelvin()
    try:
        r = kelvin_adapter.select("mosfet", req, opts,
                                  data_dir=str(tas_data_dir) if tas_data_dir else None)
    except pk.NoCandidates as e:
        pl = kelvin_adapter.no_candidates_payload(e)
        raise SelectionError(c, pl.get("rejections", {}), pl.get("totalRowsConsidered", 0)) from e

    winner = Mosfet.from_envelope(r["candidates"][0]["envelope"])
    margins = {
        "vds_margin": winner.vds_rated / c.vds_min,
        "id_margin": winner.id_continuous / c.id_min,
        "rds_on_headroom": c.rds_on_max / winner.rds_on,
        "qg_headroom": (c.qg_max / winner.qg_total) if winner.qg_total > 0 else float("inf"),
    }
    return MosfetSelection(
        chosen=winner,
        constraints=c,
        tiebreaker=tiebreaker,
        margins=margins,
        alternatives_considered=r["alternativesConsidered"],
    )


# ---------------------------------------------------------------------------
# Diode selector
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Diode:
    """Subset of a TAS diode envelope consumed by the selector."""

    mpn: str
    manufacturer: str
    vrrm_rated: float  # reverseVoltage (volts)
    if_avg_rated: float | None  # forwardCurrent (amps); None for zener/TVS/ESD (ABT #466)
    vf_typ: float | None  # forwardVoltage (volts); None when not published at rated current
    # reverseRecoveryCharge (coulombs) / reverseRecoveryTime (seconds). None when the
    # record states neither — 15,165 of the 17,888 of them — because 0 is the IDEAL
    # diode, and the 260 records that genuinely state Qrr = 0 (257 sicSchottky, 3
    # schottky: no minority charge to recover) produce the same number from the opposite
    # cause. Absent, never 0 — the same rule vf_typ above already follows (ABT #489).
    qrr: float | None
    trr: float | None
    rth_ja: float | None  # thermalResistanceJunctionAmbient (K/W)
    rth_jc: float | None  # thermalResistanceJunctionCase (K/W)
    tj_max: float | None  # junctionTemperatureMax (°C)
    case: str
    technology: str  # Si / SiC schottky / fast / ultrafast (from subType)
    status: str
    datasheet_url: str
    raw_envelope: Mapping[str, Any]

    @classmethod
    def from_envelope(cls, env: Mapping[str, Any]) -> Diode | None:
        try:
            diode = env["semiconductor"]["diode"]
            mi = diode["manufacturerInfo"]
            di = mi["datasheetInfo"]
            elec = di["electrical"]
            part = di.get("part") or {}
        except (KeyError, TypeError):
            return None

        mpn = mi.get("reference")
        manufacturer = mi.get("name")
        if not isinstance(mpn, str) or not isinstance(manufacturer, str):
            return None

        # Diode subtypes are characterised by DIFFERENT parameters, and demanding the
        # rectifier set from all of them silently emptied whole families: every TVS and
        # ESD part, and all but ~100 zeners, were dropped as unreadable. Measured over
        # TAS diodes.ndjson:
        #   zener    carries breakdownVoltage (its Vz); only 101/8,274 carry reverseVoltage
        #   tvs/esd  carry standoffVoltage; NEITHER carries forwardCurrent at all
        # So take each subtype's own reverse-direction rating, and require the forward
        # parameters only from the subtypes that actually have them. This mirrors
        # Kelvin's extract_diode (ABT #423); the two are parity-locked, and Heaviside
        # lagging here made the parity golden un-regenerable (ABT #466).
        sub = part.get("subType")
        sub = sub if isinstance(sub, str) else ""
        rectifier_like = sub not in ("zener", "tvs", "esd")

        vrrm = elec.get("reverseVoltage")
        if not (isinstance(vrrm, (int, float)) and vrrm > 0):
            # _resolve, not a raw read: a zener's breakdownVoltage is a
            # dimensionWithTolerance (Vz is a graded range, so all 8,274 are objects)
            # and reading the field directly sees only a dict.
            if sub == "zener":
                vrrm = resolve_dimensional_value(elec.get("breakdownVoltage"))
            elif sub in ("tvs", "esd"):
                vrrm = resolve_dimensional_value(elec.get("standoffVoltage"))
        if not (isinstance(vrrm, (int, float)) and vrrm > 0):
            return None

        if_avg = elec.get("forwardCurrent")
        vf = elec.get("forwardVoltage")
        if_avg = float(if_avg) if isinstance(if_avg, (int, float)) and if_avg > 0 else None
        vf = float(vf) if isinstance(vf, (int, float)) and vf > 0 else None
        # Vf REQUIRED for a rectifier: the LOWEST_VF tiebreaker would otherwise reward
        # rows where Vf is missing (treated as 0 via silent fallback), which is exactly
        # the "no silent fallbacks" trap. A zener/TVS/ESD publishes no average forward
        # current at all, so for those it stays None — absent, never 0.
        if rectifier_like and (if_avg is None or vf is None):
            return None

        qrr = elec.get("reverseRecoveryCharge")
        qrr = float(qrr) if isinstance(qrr, (int, float)) and qrr >= 0 else None
        trr = elec.get("reverseRecoveryTime")
        trr = float(trr) if isinstance(trr, (int, float)) and trr >= 0 else None

        case = part.get("case")
        if not isinstance(case, str):
            case = ""
        # Diode "technology" lives at part.subType (Schottky / FastRecovery / ...)
        tech = part.get("subType")
        if not isinstance(tech, str):
            tech = ""

        status = mi.get("status")
        if not isinstance(status, str):
            status = "unknown"

        ds_url = mi.get("datasheetUrl")
        if not isinstance(ds_url, str):
            ds_url = ""

        thermal = di.get("thermal") or {}
        rth_ja_raw = thermal.get("thermalResistanceJunctionAmbient")
        rth_ja = (
            float(rth_ja_raw) if isinstance(rth_ja_raw, (int, float)) and rth_ja_raw > 0 else None
        )
        rth_jc_raw = thermal.get("thermalResistanceJunctionCase")
        rth_jc = (
            float(rth_jc_raw) if isinstance(rth_jc_raw, (int, float)) and rth_jc_raw > 0 else None
        )
        tj_max_raw = thermal.get("junctionTemperatureMax")
        tj_max = float(tj_max_raw) if isinstance(tj_max_raw, (int, float)) else None

        return cls(
            mpn=mpn,
            manufacturer=manufacturer,
            vrrm_rated=float(vrrm),
            if_avg_rated=if_avg,
            vf_typ=vf,
            qrr=qrr,
            trr=trr,
            rth_ja=rth_ja,
            rth_jc=rth_jc,
            tj_max=tj_max,
            case=case,
            technology=tech,
            status=status,
            datasheet_url=ds_url,
            raw_envelope=env,
        )


class DiodeTiebreaker(StrEnum):
    LOWEST_VF = "lowest_vf"
    LOWEST_QRR = "lowest_qrr"
    HIGHEST_VRRM_MARGIN = "highest_vrrm_margin"
    HIGHEST_IF_MARGIN = "highest_if_margin"


@dataclass(frozen=True, slots=True)
class DiodeConstraints:
    vrrm_min: float
    if_avg_min: float
    qrr_max: float | None = None  # None means "no Qrr filter" (e.g. Schottky-only)
    exclude_discontinued: bool = True

    def __post_init__(self) -> None:
        for name in ("vrrm_min", "if_avg_min"):
            val = getattr(self, name)
            if not isinstance(val, (int, float)) or val <= 0:
                raise ValueError(f"DiodeConstraints.{name} must be a positive number, got {val!r}")
        if self.qrr_max is not None and self.qrr_max < 0:
            raise ValueError(f"DiodeConstraints.qrr_max must be non-negative, got {self.qrr_max!r}")


@dataclass(frozen=True, slots=True)
class DiodeSelection:
    chosen: Diode
    constraints: DiodeConstraints
    tiebreaker: DiodeTiebreaker
    margins: Mapping[str, float]
    alternatives_considered: int


def select_diode(
    c: DiodeConstraints,
    *,
    tiebreaker: DiodeTiebreaker,
    tas_data_dir: Path | None = None,
) -> DiodeSelection:
    from heaviside.catalogue import kelvin_adapter

    req = {"ratedReverseVoltage": c.vrrm_min, "ratedForwardCurrent": c.if_avg_min}
    opts: dict[str, Any] = {"tiebreaker": str(tiebreaker), "excludeDiscontinued": c.exclude_discontinued}
    if c.qrr_max is not None:
        opts["qrrMax"] = c.qrr_max

    pk = kelvin_adapter.PyKelvin()
    try:
        r = kelvin_adapter.select("diode", req, opts,
                                  data_dir=str(tas_data_dir) if tas_data_dir else None)
    except pk.NoCandidates as e:
        pl = kelvin_adapter.no_candidates_payload(e)
        raise SelectionError(c, pl.get("rejections", {}), pl.get("totalRowsConsidered", 0)) from e

    winner = Diode.from_envelope(r["candidates"][0]["envelope"])
    margins = {
        "vrrm_margin": winner.vrrm_rated / c.vrrm_min,
        "if_avg_margin": (
            winner.if_avg_rated / c.if_avg_min if winner.if_avg_rated is not None else None
        ),
        "qrr_headroom": ((c.qrr_max / winner.qrr) if (c.qrr_max and winner.qrr) else float("inf")),
    }
    return DiodeSelection(
        chosen=winner,
        constraints=c,
        tiebreaker=tiebreaker,
        margins=margins,
        alternatives_considered=r["alternativesConsidered"],
    )


# ---------------------------------------------------------------------------
# Capacitor selector
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Capacitor:
    """Subset of a TAS capacitor envelope consumed by the selector."""

    mpn: str
    manufacturer: str
    capacitance: float  # capacitance.nominal (farads)
    v_rated: float  # ratedVoltage (volts)
    ripple_current_rms: float | None  # rippleCurrent (amps RMS); None when not published
    esr: float | None  # esr (ohms); None when not published — never 0 (ABT #455)
    rth: float | None  # thermalResistance (K/W) case-to-ambient
    technology: str  # ceramic / aluminum_electrolytic / film / tantalum
    case: str
    status: str
    datasheet_url: str
    raw_envelope: Mapping[str, Any]

    @classmethod
    def from_envelope(cls, env: Mapping[str, Any]) -> Capacitor | None:
        try:
            cap = env["capacitor"]
            mi = cap["manufacturerInfo"]
            di = mi["datasheetInfo"]
            elec = di["electrical"]
            part = di.get("part") or {}
        except (KeyError, TypeError):
            return None

        mpn = mi.get("reference")
        manufacturer = mi.get("name")
        if not isinstance(mpn, str) or not isinstance(manufacturer, str):
            return None

        # capacitance may be a number or {nominal,minimum,maximum}.
        cap_field = elec.get("capacitance")
        cap_nom = cap_field.get("nominal") if isinstance(cap_field, Mapping) else cap_field
        v_rated = elec.get("ratedVoltage")
        if not all(isinstance(x, (int, float)) and x > 0 for x in (cap_nom, v_rated)):
            return None

        # Absent stays None, NOT 0.0. Most of the catalogue does not publish these —
        # 204,060 records carry no ESR and 218,775 no ripple current — and writing 0.0
        # for "not stated" is an in-band sentinel: nothing downstream can tell an absent
        # value from a real one, because they are the same value. A 0 ohm ESR is also
        # physically impossible, and under the lowest_esr tiebreaker it sorted every part
        # with NO data ahead of the genuinely low-ESR parts the caller asked for. A stated
        # 0 is treated as not-stated for the same reason: no capacitor has zero ESR.
        # (ABT #455; the identical defect was fixed in Kelvin's Views.cpp, and the parity
        # golden is regenerated from here.)
        ripple = elec.get("rippleCurrent")
        if not isinstance(ripple, (int, float)) or ripple <= 0:
            ripple = None
        esr = elec.get("esr")
        if not isinstance(esr, (int, float)) or esr <= 0:
            esr = None

        # Capacitor technology comes from part.family/series/subType — varies.
        tech = part.get("family") or part.get("subType") or part.get("series")
        if not isinstance(tech, str):
            tech = ""

        case = part.get("case")
        if not isinstance(case, str):
            case = ""

        status = mi.get("status")
        if not isinstance(status, str):
            status = "unknown"

        ds_url = mi.get("datasheetUrl")
        if not isinstance(ds_url, str):
            ds_url = ""

        rth_raw = elec.get("thermalResistance")
        rth = float(rth_raw) if isinstance(rth_raw, (int, float)) and rth_raw > 0 else None

        return cls(
            mpn=mpn,
            manufacturer=manufacturer,
            capacitance=float(cap_nom),
            v_rated=float(v_rated),
            ripple_current_rms=float(ripple) if ripple is not None else None,
            esr=float(esr) if esr is not None else None,
            rth=rth,
            technology=tech,
            case=case,
            status=status,
            datasheet_url=ds_url,
            raw_envelope=env,
        )


class CapacitorTiebreaker(StrEnum):
    LOWEST_ESR = "lowest_esr"
    HIGHEST_RIPPLE_HEADROOM = "highest_ripple_headroom"
    HIGHEST_VOLTAGE_MARGIN = "highest_voltage_margin"
    HIGHEST_CAPACITANCE = "highest_capacitance"


@dataclass(frozen=True, slots=True)
class CapacitorConstraints:
    """Capacitor selection constraints.

    ``ripple_current_min`` is OPTIONAL (``None`` = skip the filter)
    because MLCC datasheets do not publish a ripple-current rating —
    enforcing it would reject every MLCC even when an MLCC is the
    correct choice. Set it to a positive value only when sourcing an
    electrolytic / film / tantalum bulk cap where ripple is the
    binding stress.
    """

    capacitance_min: float  # F; smallest acceptable C
    capacitance_max: float  # F; largest acceptable C (avoid 10x oversizing)
    v_rated_min: float  # V; minimum rated voltage (= V_working * derating)
    ripple_current_min: float | None = None
    technology_allowed: frozenset[str] = frozenset()  # empty = any
    exclude_discontinued: bool = True

    def __post_init__(self) -> None:
        for name in ("capacitance_min", "capacitance_max", "v_rated_min"):
            val = getattr(self, name)
            if not isinstance(val, (int, float)) or val <= 0:
                raise ValueError(
                    f"CapacitorConstraints.{name} must be a positive number, got {val!r}"
                )
        if self.ripple_current_min is not None and (
            not isinstance(self.ripple_current_min, (int, float)) or self.ripple_current_min < 0
        ):
            raise ValueError(
                f"CapacitorConstraints.ripple_current_min must be non-negative or None, "
                f"got {self.ripple_current_min!r}"
            )
        if self.capacitance_min > self.capacitance_max:
            raise ValueError(
                "CapacitorConstraints.capacitance_min > capacitance_max "
                f"({self.capacitance_min} > {self.capacitance_max})"
            )


@dataclass(frozen=True, slots=True)
class CapacitorSelection:
    chosen: Capacitor
    constraints: CapacitorConstraints
    tiebreaker: CapacitorTiebreaker
    margins: Mapping[str, float]
    alternatives_considered: int


def select_capacitor(
    c: CapacitorConstraints,
    *,
    tiebreaker: CapacitorTiebreaker,
    tas_data_dir: Path | None = None,
) -> CapacitorSelection:
    from heaviside.catalogue import kelvin_adapter

    req: dict[str, Any] = {"capacitance": {"nominal": c.capacitance_min}, "ratedVoltage": c.v_rated_min}
    if c.ripple_current_min:
        req["minimumRippleCurrent"] = c.ripple_current_min
    opts: dict[str, Any] = {"tiebreaker": str(tiebreaker), "excludeDiscontinued": c.exclude_discontinued,
                            "capacitanceMin": c.capacitance_min, "capacitanceMax": c.capacitance_max}
    if c.technology_allowed:
        opts["technologyAllowed"] = sorted(c.technology_allowed)

    pk = kelvin_adapter.PyKelvin()
    try:
        r = kelvin_adapter.select("capacitor", req, opts,
                                  data_dir=str(tas_data_dir) if tas_data_dir else None)
    except pk.NoCandidates as e:
        pl = kelvin_adapter.no_candidates_payload(e)
        raise SelectionError(c, pl.get("rejections", {}), pl.get("totalRowsConsidered", 0)) from e

    winner = Capacitor.from_envelope(r["candidates"][0]["envelope"])
    margins = {
        "v_margin": winner.v_rated / c.v_rated_min,
        "capacitance_ratio": winner.capacitance / c.capacitance_min,
        "ripple_headroom": (
            winner.ripple_current_rms / c.ripple_current_min
            if (c.ripple_current_min and c.ripple_current_min > 0
                and winner.ripple_current_rms is not None)
            else float("inf")
        ),
    }
    return CapacitorSelection(
        chosen=winner,
        constraints=c,
        tiebreaker=tiebreaker,
        margins=margins,
        alternatives_considered=r["alternativesConsidered"],
    )


def catalogue_max_capacitance(
    manufacturer: str,
    *,
    v_rated_min: float,
    case: str | None = None,
    case_matcher: "Callable[[str, str], bool] | None" = None,
    tas_data_dir: Path | None = None,
) -> tuple[float, str, str] | None:
    """Largest-capacitance part a manufacturer actually offers at ``case`` and
    ``v_rated >= v_rated_min``, excluding only KNOWN-dead parts (obsolete/nrnd) —
    unknown/production both count, so a null-status row is not silently dropped.
    Returns (capacitance_F, mpn, case) or None when nothing matches.

    Answers the no-substitute question deterministically — "what is the real
    ceiling in this family?" — instead of letting an LLM invent one (FAE trap C2:
    prose claimed 47µF was the Würth 1206 ceiling; a 100µF/6.3V 1206 exists and
    is now in the catalogue). ``case_matcher`` supplies a package normaliser (EIA
    metric↔imperial) so "1206" matches whatever spelling the row carries."""
    root = tas_data_dir if tas_data_dir is not None else _tas_data_dir()
    path = root / "capacitors.ndjson"
    mfr_l = (manufacturer or "").strip().lower()
    best: tuple[float, str, str] | None = None
    for _lineno, env in iter_envelopes(path):
        cap = Capacitor.from_envelope(env)
        if cap is None or cap.status in ("obsolete", "nrnd"):
            continue
        if mfr_l and cap.manufacturer.strip().lower() != mfr_l:
            continue
        if cap.v_rated < v_rated_min:
            continue
        if case:
            if case_matcher is not None:
                if not case_matcher(case, cap.case):
                    continue
            elif cap.case.strip().lower() != case.strip().lower():
                continue
        if best is None or cap.capacitance > best[0]:
            best = (cap.capacitance, cap.mpn, cap.case)
    return best


# ---------------------------------------------------------------------------
# Controller selector
# ---------------------------------------------------------------------------
#
# TAS controllers.ndjson is the CTAS NESTED envelope:
#   controller.manufacturerInfo.{name, reference, datasheetInfo.{function.{category,
#   intendedTopologies[]}, part.partNumber, electrical{...}}}
# The records carry NO Vin/fsw ranges (the electrical block is sparse: gateDrive,
# currentMode, referenceVoltage, …), so selection matches on the function CATEGORY
# (pwmController / gateDriver / llcController / pfcController / …) and the
# intendedTopologies (converter-named: buckConverter, boostConverter, …), which we
# normalize to HS short names. Vin/fsw are left permissive — we cannot filter on data
# the catalog does not publish.

# CTAS intendedTopologies use full converter names; map them to HS short topology names.
# Only names whose default camelCase→snake_case normalization does NOT equal the HS
# stencil key need an explicit entry here. The resonant cousins and the Vienna rectifier
# are the cases that diverge (e.g. "cllcResonantConverter" → "cllc_resonant" ≠ "cllc"),
# so without these the selector silently rejects correctly-tagged controllers for them.
_CTAS_TOPOLOGY = {
    "buckConverter": "buck",
    "boostConverter": "boost",
    "buckBoostConverter": "buck_boost",
    "flybackConverter": "flyback",
    "forwardConverter": "forward",
    "llcResonantConverter": "llc",
    "powerFactorCorrection": "power_factor_correction",
    "sepicConverter": "sepic",
    "cukConverter": "cuk",
    "zetaConverter": "zeta",
    "pushPullConverter": "push_pull",
    "phaseShiftedFullBridge": "phase_shifted_full_bridge",
    "dualActiveBridge": "dual_active_bridge",
    "cllcResonantConverter": "cllc",
    "clllcResonantConverter": "clllc",
    "seriesResonantConverter": "series_resonant",
    "viennaRectifierConverter": "vienna",
}


def _normalize_ctas_topology(name: str) -> str:
    s = str(name)
    if s in _CTAS_TOPOLOGY:
        return _CTAS_TOPOLOGY[s]
    if s.endswith("Converter"):
        s = s[:-9]
    out = []
    for i, ch in enumerate(s):
        if ch.isupper() and i > 0:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


@dataclass(frozen=True, slots=True)
class Controller:
    """Subset of a CTAS controller envelope (nested schema)."""

    mpn: str
    manufacturer: str
    category: str  # CTAS function.category: pwmController / gateDriver / llcController / …
    topologies: tuple[str, ...]  # normalized HS short names from intendedTopologies
    vin_min: float
    vin_max: float
    fsw_min_khz: float
    fsw_max_khz: float
    integrated_fet: bool
    integrated_driver: bool
    vref: float | None  # referenceVoltage (volts), if published
    datasheet_url: str
    raw_envelope: Mapping[str, Any]

    @classmethod
    def from_envelope(cls, env: Mapping[str, Any]) -> Controller | None:
        # CTAS nested: controller.manufacturerInfo.datasheetInfo.{function, electrical}.
        ctrl = env.get("controller") if isinstance(env.get("controller"), Mapping) else env
        mi = (
            ctrl.get("manufacturerInfo")
            if isinstance(ctrl.get("manufacturerInfo"), Mapping)
            else {}
        )
        ds = mi.get("datasheetInfo") if isinstance(mi.get("datasheetInfo"), Mapping) else {}
        fn = ds.get("function") if isinstance(ds.get("function"), Mapping) else {}
        part = ds.get("part") if isinstance(ds.get("part"), Mapping) else {}
        mpn = mi.get("reference") or part.get("partNumber")
        manufacturer = mi.get("name")
        if not isinstance(mpn, str) or not isinstance(manufacturer, str):
            return None
        category = fn.get("category") if isinstance(fn.get("category"), str) else ""
        topos = tuple(
            _normalize_ctas_topology(t)
            for t in (fn.get("intendedTopologies") or [])
            if isinstance(t, str)
        )
        el = ds.get("electrical") if isinstance(ds.get("electrical"), Mapping) else {}
        vref_blk = (
            el.get("referenceVoltage") if isinstance(el.get("referenceVoltage"), Mapping) else {}
        )
        vref_raw = vref_blk.get("nominal")
        vref = float(vref_raw) if isinstance(vref_raw, (int, float)) and vref_raw > 0 else None
        ds_url = ctrl.get("datasheetUrl") or mi.get("datasheetUrl")
        return cls(
            mpn=mpn,
            manufacturer=manufacturer,
            category=category,
            topologies=topos,
            # CTAS carries no Vin/fsw ranges -> permissive (cannot filter on absent data).
            vin_min=0.0,
            vin_max=1e12,
            fsw_min_khz=0.0,
            fsw_max_khz=1e12,
            integrated_fet=bool(fn.get("integratedFet", False)),
            integrated_driver=category == "gateDriver" or bool(fn.get("integratedDriver", False)),
            vref=vref,
            datasheet_url=ds_url if isinstance(ds_url, str) else "",
            raw_envelope=env,
        )


@dataclass(frozen=True, slots=True)
class ControllerConstraints:
    """Controller selection constraints derived from the converter spec."""

    topology: str  # normalized topology name (e.g. "buck")
    vin_nom: float  # nominal input voltage (volts) — must be in range
    fsw_khz: float  # switching frequency (kHz) — must be in range
    integrated_fet: bool | None  # True/False to require; None = don't care
    category: str | None = (
        None  # CTAS function.category to require (e.g. "pwmController"); None = any
    )


@dataclass(frozen=True, slots=True)
class ControllerSelection:
    chosen: Controller
    constraints: ControllerConstraints
    alternatives_considered: int


def select_controller(
    c: ControllerConstraints,
    *,
    tas_data_dir: Path | None = None,
) -> ControllerSelection:
    """Pick a controller IC matching topology, Vin range, and fsw range.

    Tiebreaker: widest fsw-range headroom around the target (most robust
    margin), then widest Vin range. Raises SelectionError if none match.
    """
    from heaviside.catalogue import kelvin_adapter

    opts: dict[str, Any] = {"topology": c.topology, "inputVoltage": c.vin_nom,
                            "switchingFrequency": c.fsw_khz * 1000.0}
    if c.integrated_fet is not None:
        opts["integratedFet"] = c.integrated_fet
    req = {"category": c.category} if c.category else {}

    pk = kelvin_adapter.PyKelvin()
    try:
        r = kelvin_adapter.select("controller", req, opts,
                                  data_dir=str(tas_data_dir) if tas_data_dir else None)
    except pk.NoCandidates as e:
        pl = kelvin_adapter.no_candidates_payload(e)
        raise SelectionError(c, pl.get("rejections", {}), pl.get("totalRowsConsidered", 0)) from e

    winner = Controller.from_envelope(r["candidates"][0]["envelope"])
    return ControllerSelection(
        chosen=winner,
        constraints=c,
        alternatives_considered=r["alternativesConsidered"],
    )


# ---------------------------------------------------------------------------
# Resistor selector
# ---------------------------------------------------------------------------
#
# Used for feedback-divider sizing (Rtop/Rbot) and other fixed-value
# resistors. TAS resistors.ndjson is a nested CAS envelope:
# resistor.manufacturerInfo.datasheetInfo.{part, electrical{resistance,
# tolerance, powerRating}}. ~117k rows; selection is nearest-value with a
# tolerance preference.


@dataclass(frozen=True, slots=True)
class Resistor:
    mpn: str
    manufacturer: str
    resistance: float  # electrical.resistance.nominal (ohms)
    tolerance: float  # fractional (0.01 = 1%)
    power_rating: float  # watts
    case: str
    status: str
    raw_envelope: Mapping[str, Any]

    @classmethod
    def from_envelope(cls, env: Mapping[str, Any]) -> Resistor | None:
        try:
            res = env["resistor"]
            mi = res["manufacturerInfo"]
            di = mi["datasheetInfo"]
            elec = di["electrical"]
            part = di.get("part") or {}
        except (KeyError, TypeError):
            return None
        mpn = mi.get("reference") or part.get("partNumber")
        manufacturer = mi.get("name")
        if not isinstance(mpn, str):
            return None
        if not isinstance(manufacturer, str):
            manufacturer = ""
        r_field = elec.get("resistance")
        r_nom = r_field.get("nominal") if isinstance(r_field, Mapping) else r_field
        if not isinstance(r_nom, (int, float)) or r_nom <= 0:
            return None
        tol = elec.get("tolerance")
        tol = float(tol) if isinstance(tol, (int, float)) and tol > 0 else 0.05
        pw = elec.get("powerRating")
        pw = float(pw) if isinstance(pw, (int, float)) and pw > 0 else 0.0
        case = part.get("case") if isinstance(part.get("case"), str) else ""
        status = mi.get("status") if isinstance(mi.get("status"), str) else "unknown"
        return cls(
            mpn=mpn,
            manufacturer=manufacturer,
            resistance=float(r_nom),
            tolerance=tol,
            power_rating=pw,
            case=case,
            status=status,
            raw_envelope=env,
        )


@dataclass(frozen=True, slots=True)
class ResistorConstraints:
    target_ohms: float
    max_tolerance: float = 0.01  # prefer ≤1% for feedback dividers
    max_value_deviation: float = 0.05  # accept within ±5% of target


@dataclass(frozen=True, slots=True)
class ResistorSelection:
    chosen: Resistor
    constraints: ResistorConstraints
    deviation: float  # signed (chosen - target) / target
    alternatives_considered: int


def select_resistor(
    c: ResistorConstraints,
    *,
    tas_data_dir: Path | None = None,
) -> ResistorSelection:
    """Pick the resistor nearest ``target_ohms`` within tolerance + deviation
    bounds. Prefers tighter tolerance, then smallest |deviation|."""
    from heaviside.catalogue import kelvin_adapter

    req = {"resistance": {"nominal": c.target_ohms}}
    opts: dict[str, Any] = {"maxValueDeviation": c.max_value_deviation, "maxTolerance": c.max_tolerance}

    pk = kelvin_adapter.PyKelvin()
    try:
        r = kelvin_adapter.select("resistor", req, opts,
                                  data_dir=str(tas_data_dir) if tas_data_dir else None)
    except pk.NoCandidates as e:
        pl = kelvin_adapter.no_candidates_payload(e)
        raise SelectionError(c, pl.get("rejections", {}), pl.get("totalRowsConsidered", 0)) from e

    winner = Resistor.from_envelope(r["candidates"][0]["envelope"])
    return ResistorSelection(
        chosen=winner,
        constraints=c,
        deviation=(winner.resistance - c.target_ohms) / c.target_ohms,
        alternatives_considered=r["alternativesConsidered"],
    )


# CatalogueReadError is exported only via the package surface; not in
# selector.__all__ because the selector's own contract is SelectionError.
__all__ = [
    "Capacitor",
    "CapacitorConstraints",
    "CapacitorSelection",
    "CapacitorTiebreaker",
    "Controller",
    "ControllerConstraints",
    "ControllerSelection",
    "Diode",
    "DiodeConstraints",
    "DiodeSelection",
    "DiodeTiebreaker",
    "Mosfet",
    "MosfetConstraints",
    "MosfetSelection",
    "MosfetTiebreaker",
    "Resistor",
    "ResistorConstraints",
    "ResistorSelection",
    "SelectionError",
    "select_capacitor",
    "select_controller",
    "select_diode",
    "select_mosfet",
    "select_resistor",
]
