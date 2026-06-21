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
try:
    from enum import StrEnum
except ImportError:  # Python < 3.11
    from enum import Enum
    class StrEnum(str, Enum):  # type: ignore[no-redef]
        pass
from pathlib import Path
from typing import Any, Final

from heaviside.catalogue._reader import iter_envelopes
from heaviside.librarian.guards import BAD_DATASHEET_URL_PATTERNS

# Default location of TAS/data/. ``HEAVISIDE_TAS_DATA_DIR`` lets tests
# point at fixtures (matches the convention used by
# heaviside.librarian.safe_access).
_REPO_ROOT: Final = Path(__file__).resolve().parents[2]
_DEFAULT_TAS_DATA_DIR: Final = _REPO_ROOT / "TAS" / "data"


def _tas_data_dir() -> Path:
    env = os.environ.get("HEAVISIDE_TAS_DATA_DIR")
    return Path(env) if env else _DEFAULT_TAS_DATA_DIR


def _datasheet_unusable(url: str) -> bool:
    """True when a datasheetUrl is absent or a known-bad search /
    aggregator / placeholder page (the same patterns the librarian
    write-guard rejects). A part whose only datasheet link is dead can
    neither be verified nor enriched, so the selector deprioritises it
    rather than shipping a design around an undocumented part."""
    if not url:
        return True
    return any(rx.search(url) for rx, _reason in BAD_DATASHEET_URL_PATTERNS)


def _mosfet_evidence_incomplete(m: Mosfet) -> bool:
    """A MOSFET the design cannot fully build or verify around: no
    gate-charge datum (gate-drive and bootstrap-capacitor sizing are
    impossible — this is exactly what leaves C_boot unsized) or no
    usable datasheet. Used as the PRIMARY preference tier so a
    documented, buildable part beats an otherwise-equal thin one. It is
    never a hard reject: the legacy corpus has real enrichment gaps, so
    emptying the pool would be worse than picking the best thin part."""
    return m.qg_total <= 0 or _datasheet_unusable(m.datasheet_url)


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

    # Preference tiers (applied before the primary metric, most
    # important first):
    #   1. evidence-complete — a buildable, documented part (Qg present,
    #      datasheet usable). A thin/undocumented part (e.g. a
    #      discontinued FET with a dead datasheet and no Qg) only wins if
    #      nothing better satisfies the hard constraints.
    #   2. thermal-rich — Rth_ja + Tj_max populated, so the analyst can
    #      compute Tj and the realism gate's thermal_limit check runs.
    # Implemented as a tuple sort key so each tier orders within the one
    # above it, finally falling to the primary metric. Both tiers are
    # soft (re-rank, never reject) — the legacy corpus has real gaps.
    def _no_thermal(m: Mosfet) -> bool:
        return m.rth_ja is None or m.tj_max is None

    _ev = _mosfet_evidence_incomplete

    if tiebreaker is MosfetTiebreaker.LOWEST_RDS_ON:
        winner = min(passing, key=lambda m: (_ev(m), _no_thermal(m), m.rds_on))
    elif tiebreaker is MosfetTiebreaker.LOWEST_QG:
        winner = min(passing, key=lambda m: (_ev(m), _no_thermal(m), m.qg_total))
    elif tiebreaker is MosfetTiebreaker.HIGHEST_VDS_MARGIN:
        winner = min(passing, key=lambda m: (_ev(m), _no_thermal(m), -m.vds_rated / c.vds_min))
    elif tiebreaker is MosfetTiebreaker.HIGHEST_ID_MARGIN:
        winner = min(passing, key=lambda m: (_ev(m), _no_thermal(m), -m.id_continuous / c.id_min))
    elif tiebreaker is MosfetTiebreaker.LOWEST_TOTAL_LOSS:
        if not all(
            isinstance(x, (int, float)) and x > 0
            for x in (c.op_i_rms, c.op_vds, c.op_duty, c.op_fsw)
        ):
            raise ValueError(
                "LOWEST_TOTAL_LOSS requires op_i_rms/op_vds/op_duty/op_fsw on MosfetConstraints"
            )
        _IG = 1.0  # gate-drive current proxy; matches analyst _GATE_DRIVE_CURRENT_A

        def _total_loss(m: Mosfet) -> float:
            p_cond = float(c.op_duty) * (float(c.op_i_rms) ** 2) * m.rds_on
            p_sw = (
                (0.5 * float(c.op_vds) * float(c.op_i_rms) * m.qg_total * float(c.op_fsw) / _IG)
                if m.qg_total > 0
                else 0.0
            )
            return p_cond + p_sw

        winner = min(passing, key=lambda m: (_ev(m), _no_thermal(m), _total_loss(m)))
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
    vrrm_rated: float  # reverseVoltage (volts)
    if_avg_rated: float  # forwardCurrent (amps)
    vf_typ: float  # forwardVoltage (volts) at rated current
    qrr: float  # reverseRecoveryCharge (coulombs); 0 for Schottky
    trr: float  # reverseRecoveryTime (seconds); 0 for Schottky
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

        vrrm = elec.get("reverseVoltage")
        if_avg = elec.get("forwardCurrent")
        vf = elec.get("forwardVoltage")
        # Vf REQUIRED: the LOWEST_VF tiebreaker would otherwise reward
        # rows where Vf is missing (treated as 0 via silent fallback),
        # which is exactly the "no silent fallbacks" trap. Rows without
        # a published Vf get skipped here; the auditor flags them.
        if not all(isinstance(x, (int, float)) and x > 0 for x in (vrrm, if_avg, vf)):
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
            if_avg_rated=float(if_avg),
            vf_typ=float(vf),
            qrr=float(qrr),
            trr=float(trr),
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

    # Prefer thermal-rich parts (see MOSFET selector for the same rationale).
    def _no_thermal_d(d: Diode) -> bool:
        return d.rth_ja is None or d.tj_max is None

    if tiebreaker is DiodeTiebreaker.LOWEST_VF:
        winner = min(passing, key=lambda d: (_no_thermal_d(d), d.vf_typ))
    elif tiebreaker is DiodeTiebreaker.LOWEST_QRR:
        winner = min(passing, key=lambda d: (_no_thermal_d(d), d.qrr))
    elif tiebreaker is DiodeTiebreaker.HIGHEST_VRRM_MARGIN:
        winner = min(passing, key=lambda d: (_no_thermal_d(d), -d.vrrm_rated / c.vrrm_min))
    elif tiebreaker is DiodeTiebreaker.HIGHEST_IF_MARGIN:
        winner = min(passing, key=lambda d: (_no_thermal_d(d), -d.if_avg_rated / c.if_avg_min))
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
        chosen=winner,
        constraints=c,
        tiebreaker=tiebreaker,
        margins=margins,
        alternatives_considered=len(passing),
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
    ripple_current_rms: float  # rippleCurrent (amps RMS)
    esr: float  # esr (ohms); 0 for MLCC when not declared
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

        ripple = elec.get("rippleCurrent")
        if not isinstance(ripple, (int, float)) or ripple < 0:
            ripple = 0.0
        esr = elec.get("esr")
        if not isinstance(esr, (int, float)) or esr < 0:
            esr = 0.0

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

    # Prefer thermal-rich parts (cap thermal lives in electrical.thermalResistance).
    def _no_thermal_c(x: Capacitor) -> bool:
        return x.rth is None

    if tiebreaker is CapacitorTiebreaker.LOWEST_ESR:
        # MLCC rows often have esr=0; treat zero as "best" deliberately.
        winner = min(passing, key=lambda x: (_no_thermal_c(x), x.esr))
    elif tiebreaker is CapacitorTiebreaker.HIGHEST_RIPPLE_HEADROOM:
        ripple_min = c.ripple_current_min or 1.0  # avoid div by 0 / None
        winner = min(
            passing,
            key=lambda x: (_no_thermal_c(x), -x.ripple_current_rms / ripple_min),
        )
    elif tiebreaker is CapacitorTiebreaker.HIGHEST_VOLTAGE_MARGIN:
        winner = min(passing, key=lambda x: (_no_thermal_c(x), -x.v_rated / c.v_rated_min))
    elif tiebreaker is CapacitorTiebreaker.HIGHEST_CAPACITANCE:
        winner = min(passing, key=lambda x: (_no_thermal_c(x), -x.capacitance))
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
        chosen=winner,
        constraints=c,
        tiebreaker=tiebreaker,
        margins=margins,
        alternatives_considered=len(passing),
    )


# ---------------------------------------------------------------------------
# Controller selector
# ---------------------------------------------------------------------------
#
# TAS controllers.ndjson is a FLAT schema (not the nested CAS envelope used
# by Q/D/C): top-level name, manufacturer, topologies[], vinRange{min,max}
# (volts), switchingFrequencyRange{min,max} (kHz), integratedFET,
# integratedDriver. No Vref/Vfb data is published — feedback-divider sizing
# must come from datasheet extraction, not this selector.


@dataclass(frozen=True, slots=True)
class Controller:
    """Subset of a TAS controller envelope (flat schema)."""

    mpn: str
    manufacturer: str
    topologies: tuple[str, ...]
    vin_min: float
    vin_max: float
    fsw_min_khz: float
    fsw_max_khz: float
    integrated_fet: bool
    integrated_driver: bool
    vref: float | None  # feedbackReferenceVoltage (volts), if known
    datasheet_url: str
    raw_envelope: Mapping[str, Any]

    @classmethod
    def from_envelope(cls, env: Mapping[str, Any]) -> Controller | None:
        mpn = env.get("name")
        manufacturer = env.get("manufacturer")
        if not isinstance(mpn, str) or not isinstance(manufacturer, str):
            return None
        topos = env.get("topologies")
        if not isinstance(topos, list):
            return None
        vin = env.get("vinRange") or {}
        fsw = env.get("switchingFrequencyRange") or {}
        vmin, vmax = vin.get("min"), vin.get("max")
        fmin, fmax = fsw.get("min"), fsw.get("max")
        if not all(isinstance(x, (int, float)) for x in (vmin, vmax, fmin, fmax)):
            return None
        vref_raw = env.get("feedbackReferenceVoltage")
        vref = float(vref_raw) if isinstance(vref_raw, (int, float)) and vref_raw > 0 else None
        return cls(
            mpn=mpn,
            manufacturer=manufacturer,
            topologies=tuple(str(t).lower() for t in topos),
            vin_min=float(vmin),
            vin_max=float(vmax),
            fsw_min_khz=float(fmin),
            fsw_max_khz=float(fmax),
            integrated_fet=bool(env.get("integratedFET", False)),
            integrated_driver=bool(env.get("integratedDriver", False)),
            vref=vref,
            datasheet_url=env.get("datasheetUrl")
            if isinstance(env.get("datasheetUrl"), str)
            else "",
            raw_envelope=env,
        )


@dataclass(frozen=True, slots=True)
class ControllerConstraints:
    """Controller selection constraints derived from the converter spec."""

    topology: str  # normalized topology name (e.g. "buck")
    vin_nom: float  # nominal input voltage (volts) — must be in range
    fsw_khz: float  # switching frequency (kHz) — must be in range
    integrated_fet: bool | None  # True/False to require; None = don't care


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
    root = tas_data_dir if tas_data_dir is not None else _tas_data_dir()
    path = root / "controllers.ndjson"

    topo = c.topology.lower()
    passing: list[Controller] = []
    rejection: Counter[str] = Counter()
    total = 0

    for _lineno, env in iter_envelopes(path):
        total += 1
        ctrl = Controller.from_envelope(env)
        if ctrl is None:
            rejection["unreadable_row"] += 1
            continue
        if (
            topo not in ctrl.topologies
            and "any" not in ctrl.topologies
            and "all" not in ctrl.topologies
        ):
            rejection["topology"] += 1
            continue
        if not (ctrl.vin_min <= c.vin_nom <= ctrl.vin_max):
            rejection["vin_out_of_range"] += 1
            continue
        if not (ctrl.fsw_min_khz <= c.fsw_khz <= ctrl.fsw_max_khz):
            rejection["fsw_out_of_range"] += 1
            continue
        if c.integrated_fet is not None and ctrl.integrated_fet != c.integrated_fet:
            rejection["integrated_fet_mismatch"] += 1
            continue
        passing.append(ctrl)

    if not passing:
        raise SelectionError(c, rejection, total)

    # Tiebreaker, in priority order:
    #  1. Real switching controllers (fsw_min > 0) over gate-driver-like
    #     parts that declare fsw_min=0 (those are drivers tagged with the
    #     topology, not regulators).
    #  2. Controllers with a known Vref — we can fully specify the design
    #     (feedback divider) only when Vref is available. Both data-
    #     completeness and a valid engineering choice.
    #  3. Target fsw sitting centrally in the range (max distance to the
    #     nearest fsw edge) — most robust margin against fsw drift.
    #  4. Widest Vin range as a final, deterministic discriminator.
    def _key(x: Controller) -> tuple[int, int, float, float]:
        real_ctrl = 1 if x.fsw_min_khz > 0 else 0
        has_vref = 1 if x.vref is not None else 0
        edge_dist = min(c.fsw_khz - x.fsw_min_khz, x.fsw_max_khz - c.fsw_khz)
        return (real_ctrl, has_vref, edge_dist, x.vin_max - x.vin_min)

    winner = max(passing, key=_key)
    return ControllerSelection(
        chosen=winner,
        constraints=c,
        alternatives_considered=len(passing),
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
    root = tas_data_dir if tas_data_dir is not None else _tas_data_dir()
    path = root / "resistors.ndjson"

    best: Resistor | None = None
    best_key: tuple[float, float] | None = None
    rejection: Counter[str] = Counter()
    total = 0
    considered = 0

    for _lineno, env in iter_envelopes(path):
        total += 1
        r = Resistor.from_envelope(env)
        if r is None:
            rejection["unreadable_row"] += 1
            continue
        if r.tolerance > c.max_tolerance:
            rejection["tolerance_loose"] += 1
            continue
        dev = abs(r.resistance - c.target_ohms) / c.target_ohms
        if dev > c.max_value_deviation:
            rejection["value_far"] += 1
            continue
        considered += 1
        key = (r.tolerance, dev)
        if best_key is None or key < best_key:
            best_key = key
            best = r

    if best is None:
        raise SelectionError(c, rejection, total)
    return ResistorSelection(
        chosen=best,
        constraints=c,
        deviation=(best.resistance - c.target_ohms) / c.target_ohms,
        alternatives_considered=considered,
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
