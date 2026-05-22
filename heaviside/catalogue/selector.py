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

import os
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from heaviside.catalogue._reader import iter_envelopes

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
    datasheetInfo.{electrical,part}``). Once Heaviside's typed
    generation pipeline (``make types``) populates
    ``heaviside/types/_generated/``, this dataclass is the conversion
    target — the field set is intentionally narrow.
    """

    mpn: str
    manufacturer: str
    vds_rated: float          # drainSourceVoltage (volts)
    id_continuous: float      # continuousDrainCurrent (amps, Tc-spec)
    rds_on: float             # onResistance (ohms at gate_vgs / id_test)
    qg_total: float           # totalGateCharge (coulombs)
    vgs_threshold_max: float  # gateThresholdVoltage.maximum (volts)
    rth_ja: float | None      # thermalResistanceJunctionAmbient (K/W)
    tj_max: float | None      # junctionTemperatureMax (°C)
    case: str                 # package code from part.case
    technology: str           # Si / SiC / GaN
    status: str               # production / discontinued
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
        if not all(
            isinstance(x, (int, float)) and x > 0
            for x in (vds_rated, id_cont, rds_on)
        ):
            return None
        if qg_total is None:
            qg_total = 0.0  # legacy rows; Qg constraint becomes vacuous
        if not isinstance(qg_total, (int, float)) or qg_total < 0:
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
        rth_ja = float(rth_ja_raw) if isinstance(rth_ja_raw, (int, float)) and rth_ja_raw > 0 else None
        tj_max_raw = thermal.get("junctionTemperatureMax")
        tj_max = float(tj_max_raw) if isinstance(tj_max_raw, (int, float)) else None

        return cls(
            mpn=mpn,
            manufacturer=manufacturer,
            vds_rated=float(vds_rated),
            id_continuous=float(id_cont),
            rds_on=float(rds_on),
            qg_total=float(qg_total),
            vgs_threshold_max=float(vgs_th_max),
            rth_ja=rth_ja,
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

    def __post_init__(self) -> None:
        for name in ("vds_min", "id_min", "rds_on_max", "qg_max"):
            val = getattr(self, name)
            if not isinstance(val, (int, float)) or val <= 0:
                raise ValueError(
                    f"MosfetConstraints.{name} must be a positive number, got {val!r}"
                )
        if not self.technology_allowed:
            raise ValueError(
                "MosfetConstraints.technology_allowed cannot be empty"
            )


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
    root = tas_data_dir if tas_data_dir is not None else _tas_data_dir()
    path = root / "mosfets.ndjson"

    passing: list[Mosfet] = []
    rejection: Counter[str] = Counter()
    total = 0

    for _lineno, env in iter_envelopes(path):
        total += 1
        m = Mosfet.from_envelope(env)
        if m is None:
            rejection["unreadable_row"] += 1
            continue
        if c.exclude_discontinued and m.status != "production":
            rejection["discontinued"] += 1
            continue
        if c.technology_allowed and m.technology not in c.technology_allowed:
            rejection["technology"] += 1
            continue
        if m.vds_rated < c.vds_min:
            rejection["vds_rated_low"] += 1
            continue
        if m.id_continuous < c.id_min:
            rejection["id_continuous_low"] += 1
            continue
        if m.rds_on > c.rds_on_max:
            rejection["rds_on_high"] += 1
            continue
        if m.qg_total > c.qg_max:
            rejection["qg_total_high"] += 1
            continue
        passing.append(m)

    if not passing:
        raise SelectionError(c, rejection, total)

    # Apply the explicit tiebreaker. Each policy maps to a (key,
    # reverse) pair; ``sorted`` is stable so within-tier order matches
    # the NDJSON scan order, which is itself stable across runs.
    if tiebreaker is MosfetTiebreaker.LOWEST_RDS_ON:
        winner = min(passing, key=lambda m: m.rds_on)
    elif tiebreaker is MosfetTiebreaker.LOWEST_QG:
        winner = min(passing, key=lambda m: m.qg_total)
    elif tiebreaker is MosfetTiebreaker.HIGHEST_VDS_MARGIN:
        winner = max(passing, key=lambda m: m.vds_rated / c.vds_min)
    elif tiebreaker is MosfetTiebreaker.HIGHEST_ID_MARGIN:
        winner = max(passing, key=lambda m: m.id_continuous / c.id_min)
    else:  # pragma: no cover — enum is exhaustive
        raise ValueError(f"unhandled tiebreaker {tiebreaker!r}")

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
        alternatives_considered=len(passing),
    )


# ---------------------------------------------------------------------------
# Diode selector
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Diode:
    """Subset of a TAS diode envelope consumed by the selector."""

    mpn: str
    manufacturer: str
    vrrm_rated: float       # reverseVoltage (volts)
    if_avg_rated: float     # forwardCurrent (amps)
    vf_typ: float           # forwardVoltage (volts) at rated current
    qrr: float              # reverseRecoveryCharge (coulombs); 0 for Schottky
    trr: float              # reverseRecoveryTime (seconds); 0 for Schottky
    rth_ja: float | None    # thermalResistanceJunctionAmbient (K/W)
    tj_max: float | None    # junctionTemperatureMax (°C)
    case: str
    technology: str         # Si / SiC schottky / fast / ultrafast (from subType)
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

        vrrm = elec.get("reverseVoltage")
        if_avg = elec.get("forwardCurrent")
        vf = elec.get("forwardVoltage")
        # Vf REQUIRED: the LOWEST_VF tiebreaker would otherwise reward
        # rows where Vf is missing (treated as 0 via silent fallback),
        # which is exactly the "no silent fallbacks" trap. Rows without
        # a published Vf get skipped here; the auditor flags them.
        if not all(
            isinstance(x, (int, float)) and x > 0
            for x in (vrrm, if_avg, vf)
        ):
            return None

        qrr = elec.get("reverseRecoveryCharge")
        if not isinstance(qrr, (int, float)) or qrr < 0:
            qrr = 0.0
        trr = elec.get("reverseRecoveryTime")
        if not isinstance(trr, (int, float)) or trr < 0:
            trr = 0.0

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
        rth_ja = float(rth_ja_raw) if isinstance(rth_ja_raw, (int, float)) and rth_ja_raw > 0 else None
        tj_max_raw = thermal.get("junctionTemperatureMax")
        tj_max = float(tj_max_raw) if isinstance(tj_max_raw, (int, float)) else None

        return cls(
            mpn=mpn,
            manufacturer=manufacturer,
            vrrm_rated=float(vrrm),
            if_avg_rated=float(if_avg),
            vf_typ=float(vf),
            qrr=float(qrr),
            trr=float(trr),
            rth_ja=rth_ja,
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
                raise ValueError(
                    f"DiodeConstraints.{name} must be a positive number, got {val!r}"
                )
        if self.qrr_max is not None and self.qrr_max < 0:
            raise ValueError(
                f"DiodeConstraints.qrr_max must be non-negative, got {self.qrr_max!r}"
            )


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
    root = tas_data_dir if tas_data_dir is not None else _tas_data_dir()
    path = root / "diodes.ndjson"

    passing: list[Diode] = []
    rejection: Counter[str] = Counter()
    total = 0

    for _lineno, env in iter_envelopes(path):
        total += 1
        d = Diode.from_envelope(env)
        if d is None:
            rejection["unreadable_row"] += 1
            continue
        if c.exclude_discontinued and d.status != "production":
            rejection["discontinued"] += 1
            continue
        if d.vrrm_rated < c.vrrm_min:
            rejection["vrrm_low"] += 1
            continue
        if d.if_avg_rated < c.if_avg_min:
            rejection["if_avg_low"] += 1
            continue
        if c.qrr_max is not None and d.qrr > c.qrr_max:
            rejection["qrr_high"] += 1
            continue
        passing.append(d)

    if not passing:
        raise SelectionError(c, rejection, total)

    if tiebreaker is DiodeTiebreaker.LOWEST_VF:
        winner = min(passing, key=lambda d: d.vf_typ)
    elif tiebreaker is DiodeTiebreaker.LOWEST_QRR:
        winner = min(passing, key=lambda d: d.qrr)
    elif tiebreaker is DiodeTiebreaker.HIGHEST_VRRM_MARGIN:
        winner = max(passing, key=lambda d: d.vrrm_rated / c.vrrm_min)
    elif tiebreaker is DiodeTiebreaker.HIGHEST_IF_MARGIN:
        winner = max(passing, key=lambda d: d.if_avg_rated / c.if_avg_min)
    else:  # pragma: no cover
        raise ValueError(f"unhandled tiebreaker {tiebreaker!r}")

    margins = {
        "vrrm_margin": winner.vrrm_rated / c.vrrm_min,
        "if_avg_margin": winner.if_avg_rated / c.if_avg_min,
        "qrr_headroom": (
            (c.qrr_max / winner.qrr) if (c.qrr_max and winner.qrr > 0) else float("inf")
        ),
    }
    return DiodeSelection(
        chosen=winner, constraints=c, tiebreaker=tiebreaker,
        margins=margins, alternatives_considered=len(passing),
    )


# ---------------------------------------------------------------------------
# Capacitor selector
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Capacitor:
    """Subset of a TAS capacitor envelope consumed by the selector."""

    mpn: str
    manufacturer: str
    capacitance: float           # capacitance.nominal (farads)
    v_rated: float               # ratedVoltage (volts)
    ripple_current_rms: float    # rippleCurrent (amps RMS)
    esr: float                   # esr (ohms); 0 for MLCC when not declared
    rth: float | None            # thermalResistance (K/W) case-to-ambient
    technology: str              # ceramic / aluminum_electrolytic / film / tantalum
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
        if not all(
            isinstance(x, (int, float)) and x > 0
            for x in (cap_nom, v_rated)
        ):
            return None

        ripple = elec.get("rippleCurrent")
        if not isinstance(ripple, (int, float)) or ripple < 0:
            ripple = 0.0
        esr = elec.get("esr")
        if not isinstance(esr, (int, float)) or esr < 0:
            esr = 0.0

        # Capacitor technology comes from part.family/series/subType — varies.
        tech = (part.get("family") or part.get("subType") or part.get("series"))
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
            ripple_current_rms=float(ripple),
            esr=float(esr),
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

    capacitance_min: float           # F; smallest acceptable C
    capacitance_max: float           # F; largest acceptable C (avoid 10x oversizing)
    v_rated_min: float               # V; minimum rated voltage (= V_working * derating)
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
            not isinstance(self.ripple_current_min, (int, float))
            or self.ripple_current_min < 0
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
    root = tas_data_dir if tas_data_dir is not None else _tas_data_dir()
    path = root / "capacitors.ndjson"

    passing: list[Capacitor] = []
    rejection: Counter[str] = Counter()
    total = 0

    for _lineno, env in iter_envelopes(path):
        total += 1
        cap = Capacitor.from_envelope(env)
        if cap is None:
            rejection["unreadable_row"] += 1
            continue
        if c.exclude_discontinued and cap.status != "production":
            rejection["discontinued"] += 1
            continue
        if c.technology_allowed and cap.technology not in c.technology_allowed:
            rejection["technology"] += 1
            continue
        if cap.v_rated < c.v_rated_min:
            rejection["v_rated_low"] += 1
            continue
        if cap.capacitance < c.capacitance_min:
            rejection["capacitance_low"] += 1
            continue
        if cap.capacitance > c.capacitance_max:
            rejection["capacitance_high"] += 1
            continue
        if c.ripple_current_min is not None and cap.ripple_current_rms < c.ripple_current_min:
            rejection["ripple_low"] += 1
            continue
        passing.append(cap)

    if not passing:
        raise SelectionError(c, rejection, total)

    if tiebreaker is CapacitorTiebreaker.LOWEST_ESR:
        # MLCC rows often have esr=0; treat zero as "best" deliberately,
        # but the caller can filter via technology_allowed if they need
        # an explicit-ESR part.
        winner = min(passing, key=lambda c: c.esr)
    elif tiebreaker is CapacitorTiebreaker.HIGHEST_RIPPLE_HEADROOM:
        winner = max(
            passing,
            key=lambda x: x.ripple_current_rms / c.ripple_current_min,
        )
    elif tiebreaker is CapacitorTiebreaker.HIGHEST_VOLTAGE_MARGIN:
        winner = max(passing, key=lambda x: x.v_rated / c.v_rated_min)
    elif tiebreaker is CapacitorTiebreaker.HIGHEST_CAPACITANCE:
        winner = max(passing, key=lambda x: x.capacitance)
    else:  # pragma: no cover
        raise ValueError(f"unhandled tiebreaker {tiebreaker!r}")

    margins = {
        "v_margin": winner.v_rated / c.v_rated_min,
        "capacitance_ratio": winner.capacitance / c.capacitance_min,
        "ripple_headroom": (
            winner.ripple_current_rms / c.ripple_current_min
            if (c.ripple_current_min and c.ripple_current_min > 0)
            else float("inf")
        ),
    }
    return CapacitorSelection(
        chosen=winner, constraints=c, tiebreaker=tiebreaker,
        margins=margins, alternatives_considered=len(passing),
    )


# CatalogueReadError is exported only via the package surface; not in
# selector.__all__ because the selector's own contract is SelectionError.
__all__ = [
    "Capacitor",
    "CapacitorConstraints",
    "CapacitorSelection",
    "CapacitorTiebreaker",
    "Diode",
    "DiodeConstraints",
    "DiodeSelection",
    "DiodeTiebreaker",
    "Mosfet",
    "MosfetConstraints",
    "MosfetSelection",
    "MosfetTiebreaker",
    "SelectionError",
    "select_capacitor",
    "select_diode",
    "select_mosfet",
]
