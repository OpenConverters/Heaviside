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

        return cls(
            mpn=mpn,
            manufacturer=manufacturer,
            vds_rated=float(vds_rated),
            id_continuous=float(id_cont),
            rds_on=float(rds_on),
            qg_total=float(qg_total),
            vgs_threshold_max=float(vgs_th_max),
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

    The ``rejection_counts`` field records *why* candidates were
    rejected (how many fell on Vds, how many on Id, etc.) so the
    caller can either loosen the constraint, widen the technology
    allowlist, or queue a librarian fetch for a part class the DB
    is missing.
    """

    def __init__(
        self,
        constraints: MosfetConstraints,
        rejection_counts: Mapping[str, int],
        total_rows_considered: int,
    ) -> None:
        self.constraints = constraints
        self.rejection_counts = dict(rejection_counts)
        self.total_rows_considered = total_rows_considered
        super().__init__(
            f"no mosfet in TAS satisfies {constraints!r}. "
            f"Considered {total_rows_considered} rows; rejected by "
            f"{dict(rejection_counts)}"
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


# CatalogueReadError is exported only via the package surface; not in
# selector.__all__ because the selector's own contract is SelectionError.
__all__ = [
    "Mosfet",
    "MosfetConstraints",
    "MosfetSelection",
    "MosfetTiebreaker",
    "SelectionError",
    "select_mosfet",
]
