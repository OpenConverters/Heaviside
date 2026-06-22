"""TAS librarian: pipeline-critical-field auditor.

Read-side companion to :mod:`heaviside.librarian.tas`.  Where the
writer enforces JSON-schema validity (the *necessary* condition for
TAS membership), the auditor enforces *pipeline-readiness*: every
field the analytical design pipeline actually reads at runtime must
be present, non-null, and non-zero on every row.

These are different properties.  The SAS / CAS / RAS / MAS schemas
were deliberately permissive when they were written — making them
strict would have orphaned thousands of legitimate legacy rows
overnight.  This auditor instead reports gaps non-destructively so
the ``component-librarian`` agent can backfill in priority order.

Source-of-truth fields
----------------------

The :data:`CRITICAL_PARAMS` and :data:`REQUIRED_PARAMS` constants
mirror the Proteus auditor's "review EVERYTHING" pass (May 2026)
and were derived by tracing actual reads through
``build_loss_budget``, ``build_bom``, ``pipeline_consistency_check``,
the magnetics-designer prompt, and the gate-drive sizing tool.
**If you add a new pipeline read, update CRITICAL_PARAMS first.**

Subtype carve-outs
------------------

Some pipeline-critical fields are physically inapplicable to entire
subtype families (Schottky diodes have no Qrr; MLCCs have no
manufacturer-published ripple-current rating; signal/RF inductors
have no Isat).  The classifiers below — :func:`_is_schottky`,
:func:`_is_mlcc`, :func:`_is_rf_inductor`,
:func:`_is_transformer_or_cmc` — exempt those families per
JEDEC JESD282 / IEC 60384-21 / IEC 60747 industry consensus.

Strict-mode departures from the Proteus prototype
-------------------------------------------------

Per ``CLAUDE.md`` ("no fallbacks, no defaults, no silent
shortcuts — throw"):

* :func:`audit_category` raises :class:`LibrarianError` on a
  corrupt JSON line instead of silently ``continue``-ing past it
  (mirrors :func:`heaviside.librarian.tas.component_exists`).
* :func:`_get_mpn` does not swallow ``Exception``; the MPN
  envelope is well-defined and a lookup miss returns the
  documented ``"Unknown"`` sentinel only — TypeErrors / KeyErrors
  surface.
* No CLI / ``print`` side effects.  This is a library; rendering
  belongs to the agent layer (``heaviside/agents/``) or to the
  caller's report tool.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from heaviside.librarian import safe_access as _sa
from heaviside.librarian.physics_validator import PhysicsFinding
from heaviside.librarian.safe_access import LibrarianError

__all__ = [
    "AUDITABLE_CATEGORIES",
    "CRITICAL_PARAMS",
    "REQUIRED_PARAMS",
    "CategoryAudit",
    "ComponentAudit",
    "CorruptLine",
    "FieldGap",
    "FieldStatus",
    "audit_all",
    "audit_category",
    "audit_component",
]


# ---------------------------------------------------------------------------
# Pipeline-critical field map
# ---------------------------------------------------------------------------


# CRITICAL_PARAMS: fields the design pipeline HARD-REQUIRES at runtime.
#
# mosfets — from build_loss_budget._build_flyback / _build_buck:
#   drainSourceVoltage     derating (Vds_rated >= 1.5 * Vds_peak)
#   onResistance           conduction P = I_rms^2 * Rds(Tj)
#   continuousDrainCurrent derating (Id_rated >= 1.3 * Ipeak)
#   outputCapacitance      switching P = 1/2 * Coss * Vds^2 * fsw
#   totalGateCharge        Pdrv + bootstrap-cap sizing (Cboot ~ 50*Qg/Vgs)
#   gateThresholdVoltage   Miller plateau / driver Vgs sizing
#   junctionTemperatureMax thermal headroom
#
# diodes — from output-rectifier path:
#   reverseVoltage         derating (Vr_rated >= 1.3 * Vr_peak)
#   forwardVoltage         P = Vf * I_avg + Rd * I_rms^2
#   forwardCurrent         current derating
#   reverseRecoveryCharge  CCM recovery loss = Qrr * Vr * fsw (Si only)
#
# igbts — from IGBT loss model (Vce_sat + Eon/Eoff):
#   collectorEmitterVoltage    blocking-voltage derating
#   continuousCollectorCurrent current rating
#   collectorEmitterSaturation conduction loss
#
# capacitors — ripple loss + derating:
#   capacitance            fundamental design value
#   ratedVoltage           derating (V_rated >= 1.5 * Vmax)
#   esr                    ripple loss = I_ripple^2 * ESR (non-MLCC)
#   rippleCurrent          current rating derating         (non-MLCC)
#
# resistors:
#   resistance, tolerance, powerRating
#
# magnetics (PyOM-designed; catalog L1/T1 imports audited here):
#   inductance, dcResistance, saturationCurrentPeak
CRITICAL_PARAMS: dict[str, tuple[str, ...]] = {
    "mosfets": (
        "drainSourceVoltage",
        "onResistance",
        "continuousDrainCurrent",
        "outputCapacitance",
        "totalGateCharge",
        "gateThresholdVoltage",
        "junctionTemperatureMax",
    ),
    "diodes": (
        "reverseVoltage",
        "forwardVoltage",
        "forwardCurrent",
        "reverseRecoveryCharge",
    ),
    "igbts": (
        "collectorEmitterVoltage",
        "continuousCollectorCurrent",
        "collectorEmitterSaturation",
    ),
    "capacitors": (
        "capacitance",
        "ratedVoltage",
        "esr",
        "rippleCurrent",
    ),
    "resistors": (
        "resistance",
        "tolerance",
        "powerRating",
    ),
    "magnetics": (
        "inductance",
        "dcResistance",
        "saturationCurrentPeak",
    ),
}

# REQUIRED_PARAMS: useful but not strictly blocking.  Reported as
# warnings on the per-category audit; do not flip ``ComponentAudit.passed``.
REQUIRED_PARAMS: dict[str, tuple[str, ...]] = {
    "mosfets": ("bodyDiodeForwardVoltage", "reverseRecoveryCharge"),
    "diodes": ("dynamicResistance",),
    "igbts": ("switchingEnergyOn", "switchingEnergyOff"),
    "capacitors": ("dissipationFactor", "lifetimeHours"),
    "resistors": ("temperatureCoefficient",),
    "magnetics": ("selfResonantFrequency", "ratedCurrent"),
}

AUDITABLE_CATEGORIES: tuple[str, ...] = tuple(CRITICAL_PARAMS.keys())


# ---------------------------------------------------------------------------
# Envelope unwrap
# ---------------------------------------------------------------------------


# Soft unwrap: the auditor must keep going even when an envelope is
# malformed (it will report missing fields), unlike
# :mod:`heaviside.librarian.tas` which throws on a bad envelope as
# part of write-side validation.
_WRAPPER_KEY: dict[str, tuple[str, ...]] = {
    "mosfets": ("mosfet",),
    "diodes": ("semiconductor", "diode"),
    "igbts": ("semiconductor", "igbt"),
    "capacitors": ("capacitor",),
    "resistors": ("resistor",),
    "magnetics": ("magnetic",),
}


def _unwrap(component: Any, category: str) -> dict[str, Any]:
    """Return the inner body for ``category`` or ``{}`` if the envelope
    is malformed.  Never throws."""
    if not isinstance(component, dict):
        return {}
    path = _WRAPPER_KEY.get(category)
    if not path:
        return component
    node: Any = component
    for key in path:
        if not isinstance(node, dict):
            return {}
        node = node.get(key)
        if node is None:
            return {}
    return node if isinstance(node, dict) else {}


# ---------------------------------------------------------------------------
# Subtype carve-outs (industry-consensus exemptions)
# ---------------------------------------------------------------------------


# Murata/Coilcraft/Bourns/Panasonic RF / signal-inductor prefixes.
_RF_INDUCTOR_PREFIXES: tuple[str, ...] = (
    "LQG",
    "LQW",
    "LQP",
    "LQM",
    "MLF",
    "MLZ",
    "BLM",
    "DLW",
    "AIMC",
    "AIML",
    "AIMO",
    "CW",
    "CWF",
    "0805HP",
    "0402HP",
    "1008CS",
    "LPR",
    "LPS",
    "RLB",
    "CR32NP",
    "CR43NP",
    "CR54NP",
    "ETQ",
)

# MLCC MPN prefixes (Murata/Samsung/YAGEO/Vishay/Taiyo Yuden/TDK/generic).
_MLCC_PREFIXES: tuple[str, ...] = (
    "GRM",
    "GRT",
    "GRJ",
    "GCM",
    "CL0",
    "CL1",
    "CL2",
    "CL3",
    "CC0",
    "CC1",
    "CC2",
    "RC0",
    "RC1",
    "RC2",
    "C0402",
    "C0603",
    "C0805",
    "C1206",
    "VJ0",
    "VJ1",
    "VJ2",
    "EMK",
    "TMK",
    "LMK",
    "C5750",
    "C3225",
    "C2012",
)
_MLCC_DESC_KEYWORDS: tuple[str, ...] = (
    "mlcc",
    "ceramic",
    "class i",
    "class ii",
    "c0g",
    "np0",
    "x7r",
    "x5r",
    "x8r",
    "y5v",
    "z5u",
)

# Schottky diode MPN prefixes + the unambiguous subType / desc tokens.
_SCHOTTKY_PREFIXES: tuple[str, ...] = (
    "STPS",
    "STPSC",
    "SS",
    "SK",
    "SR",
    "MBR",
    "MBRD",
    "MBRB",
    "SBR",
    "SBRD",
    "B340",
    "B360",
    "B520",
    "B540",
    "BYS",
    "BAT",
    "SL",
    "SM",
    "SN",
    "CUS",
    "CMS",
    "NSR",
    "NSQ",
    "BAR",
    "BAS",
    "DSS",
    "DSK",
    "PDS",
)
_SCHOTTKY_DESC_KEYWORDS: tuple[str, ...] = ("schottky",)
_NO_QRR_SUBTYPES: frozenset[str] = frozenset(
    {
        "schottky",
        "sicschottky",
        "tvs",
        "zener",
        "signal",
    }
)

# Magnetic families / keywords for which Isat is not a meaningful
# datasheet field (CMCs, transformers, ferrite beads, coupled inductors).
_NO_ISAT_FAMILIES: frozenset[str] = frozenset(
    {
        "choke_cmc_1mh",
        "choke_cmc_10mh",
        "choke_cmc_100mh",
        "transformer_1to1_1mh",
        "transformer_1to1_10mh",
    }
)
_NO_ISAT_DESC_KEYWORDS: tuple[str, ...] = (
    "common mode",
    "line filter",
    "cm filter",
    "differential mode choke",
    "power line choke",
    "transformer",
    "flyback transformer",
    "coupled inductor",
    "energy harvesting",
    "balun",
    "ferrite bead",
    "emi suppression",
    "impedance bead",
    "bead array",
    "chip bead",
    "smt bead",
)
_NO_ISAT_MPN_PREFIXES: tuple[str, ...] = (
    "WE-CNSW",
    "WE-CNSA",
    "WE-SCC",
    "WE-SL1",
    "WE-CCMF",
    "WE-CNSW HF",
    "WE-EHPI",
)
_NO_ISAT_MI_FAMILIES: frozenset[str] = frozenset(
    {
        "we-fi",
        "we-cnsw",
        "we-cnsa",
        "we-scc",
        "we-sl1",
        "we-ccmf",
        "we-cbf",
        "we-cba",
        "we-mpsb",
        "we-tmsb",
        "we-cbf hf",
        "we-mls",
        "we-ukw",
        "we-pf",
        "we-pbf",
        "ferrite bead",
        "we-gf",
        "we-gfh",
    }
)


def _is_rf_inductor(component: dict[str, Any]) -> bool:
    body = _unwrap(component, "magnetics")
    mi = body.get("manufacturerInfo", {}) or {}
    mpn = (mi.get("reference") or "").upper()
    ds = mi.get("datasheetInfo") or {}
    part = ds.get("part") or {}
    desc = (part.get("description") or "").lower()
    elec = ds.get("electrical") or {}

    if any(mpn.startswith(p.upper()) for p in _RF_INDUCTOR_PREFIXES):
        return True

    srf = elec.get("selfResonantFrequency") or 0
    if isinstance(srf, dict):
        srf = srf.get("nominal") or srf.get("minimum") or 0
    ind = elec.get("inductance") or 0
    if isinstance(ind, dict):
        ind = ind.get("nominal") or ind.get("minimum") or 0
    if ind and ind < 1e-6 and srf and srf > 100e6:
        return True

    return any(
        kw in desc
        for kw in (
            "rf inductor",
            "signal inductor",
            "air core",
            "wirewound rf",
            "chip inductor",
        )
    )


def _is_transformer_or_cmc(component: dict[str, Any]) -> bool:
    body = _unwrap(component, "magnetics")
    mi = body.get("manufacturerInfo", {}) or {}
    mpn = (mi.get("reference") or "").upper()
    mi_family = (mi.get("family") or "").lower()
    ds = mi.get("datasheetInfo") or {}
    part = ds.get("part") or {}
    elec = ds.get("electrical") or {}
    desc = (part.get("description") or "").lower()
    family = (part.get("family") or part.get("matchCode") or mi_family).lower()

    if elec.get("turnsRatio") is not None or elec.get("turnsRatios") is not None:
        return True
    if family in _NO_ISAT_FAMILIES or mi_family in _NO_ISAT_MI_FAMILIES:
        return True
    if any(kw in desc for kw in _NO_ISAT_DESC_KEYWORDS):
        return True
    if any(mpn.startswith(p.upper()) for p in _NO_ISAT_MPN_PREFIXES):
        return True
    return elec.get("impedancePoints") is not None


def _is_mlcc(component: dict[str, Any]) -> bool:
    body = _unwrap(component, "capacitors")
    mi = body.get("manufacturerInfo", {}) or {}
    ds = mi.get("datasheetInfo") or {}
    part = ds.get("part") or {}
    # Capacitors carry MPN under datasheetInfo.part.partNumber first
    mpn = (part.get("partNumber") or mi.get("reference") or "").upper()
    desc = (part.get("description") or "").lower()
    tech = (part.get("technology") or "").lower()

    if any(mpn.startswith(p.upper()) for p in _MLCC_PREFIXES):
        return True
    mpn_lower = mpn.lower()
    if any(kw in mpn_lower for kw in _MLCC_DESC_KEYWORDS):
        return True
    if any(kw in desc for kw in _MLCC_DESC_KEYWORDS):
        return True
    return any(kw in tech for kw in _MLCC_DESC_KEYWORDS)


def _is_schottky(component: dict[str, Any]) -> bool:
    body = _unwrap(component, "diodes")
    mi = body.get("manufacturerInfo", {}) or {}
    mpn = (mi.get("reference") or "").upper()
    ds = mi.get("datasheetInfo") or {}
    part = ds.get("part") or {}
    desc = (part.get("description") or "").lower()
    elec = ds.get("electrical") or {}

    sub_type = (part.get("subType") or "").lower()
    if sub_type in _NO_QRR_SUBTYPES:
        return True
    if any(mpn.startswith(p.upper()) for p in _SCHOTTKY_PREFIXES):
        return True
    if any(kw in desc for kw in _SCHOTTKY_DESC_KEYWORDS):
        return True
    dtype = (elec.get("diodeType") or "").lower()
    return "schottky" in dtype


# ---------------------------------------------------------------------------
# Field-level extraction
# ---------------------------------------------------------------------------


# Allowed status values for a critical/required field at audit time.
# ``"present"`` is the only passing state; everything else counts as a
# gap of the labelled flavour for downstream reporting.
class FieldStatus:
    PRESENT = "present"
    MISSING_KEY = "missing_key"
    NULL = "null"
    ZERO = "zero"


def _extract_electrical(component: dict[str, Any], category: str) -> dict[str, Any]:
    """Return the merged ``electrical`` block for the row.

    * Strips the v2 wrapper and the per-category envelope.
    * Promotes ``thermal.maximumJunctionTemperature`` /
      ``thermal.junctionTemperatureMax`` into ``electrical`` so the
      pipeline's flat lookup works uniformly.
    * Flattens ``dimensionWithTolerance``-style ``{minimum, nominal,
      maximum}`` objects to a scalar (prefer ``nominal``, then
      ``minimum``, then ``maximum``) — the pipeline does the same
      reduction at read time.
    """
    body = _unwrap(component, category)
    ds = (
        (body.get("manufacturerInfo", {}) or {}).get("datasheetInfo")
        or body.get("datasheetInfo")
        or {}
    )
    # A malformed row may carry a non-object ``electrical`` (e.g. a list); the
    # auditor must then report that part as missing-fields rather than crash the
    # whole category audit — it surfaces the bad row instead of aborting on it.
    raw_electrical = ds.get("electrical")
    elec: dict[str, Any] = dict(raw_electrical) if isinstance(raw_electrical, dict) else {}

    thermal = ds.get("thermal") or {}
    if thermal:
        if "maximumJunctionTemperature" in thermal and "junctionTemperatureMax" not in elec:
            elec["junctionTemperatureMax"] = thermal["maximumJunctionTemperature"]
        if "junctionTemperatureMax" in thermal and "junctionTemperatureMax" not in elec:
            elec["junctionTemperatureMax"] = thermal["junctionTemperatureMax"]

    for k, v in list(elec.items()):
        if isinstance(v, dict):
            scalar = v.get("nominal")
            if scalar is None:
                scalar = v.get("minimum")
            if scalar is None:
                scalar = v.get("maximum")
            elec[k] = scalar
    return elec


def _get_mpn(component: dict[str, Any], category: str) -> str:
    """Best-effort MPN extraction for audit labels.

    Returns ``"UNKNOWN"`` only when no MPN can be located in any
    documented envelope position.  Unlike the Proteus prototype this
    does NOT swallow ``Exception`` — the envelope is well-defined
    and unexpected types must surface.
    """
    body = _unwrap(component, category)
    if not isinstance(body, dict):
        return "UNKNOWN"
    mi = body.get("manufacturerInfo", {}) or {}
    ref = mi.get("reference")
    if ref:
        return str(ref)
    ds = mi.get("datasheetInfo") or {}
    part = ds.get("part") or {}
    pn = part.get("partNumber")
    if pn:
        return str(pn)
    # Legacy: datasheetInfo at body-level, not under manufacturerInfo
    ds2 = body.get("datasheetInfo") or {}
    part2 = ds2.get("part") or {}
    pn2 = part2.get("partNumber")
    if pn2:
        return str(pn2)
    return "UNKNOWN"


def _field_status(elec: dict[str, Any], field_name: str) -> str:
    """Classify the presence of ``field_name`` in ``elec``.

    Magnetics may carry either ``dcResistance`` (scalar/object) or
    the plural ``dcResistances`` (list).  Both are accepted; the
    pipeline reduces either to a scalar.
    """
    if field_name not in elec:
        if field_name == "dcResistance" and "dcResistances" in elec:
            v = elec["dcResistances"]
            if v is None:
                return FieldStatus.NULL
            if isinstance(v, (int, float)) and v == 0:
                return FieldStatus.ZERO
            return FieldStatus.PRESENT
        return FieldStatus.MISSING_KEY
    v = elec[field_name]
    if v is None:
        return FieldStatus.NULL
    if isinstance(v, (int, float)) and v == 0:
        return FieldStatus.ZERO
    return FieldStatus.PRESENT


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldGap:
    """A single missing-or-empty pipeline field on a component."""

    field: str
    status: str  # one of FieldStatus.*


@dataclass
class ComponentAudit:
    """Per-component audit result."""

    mpn: str
    category: str
    line: int | None = None
    critical_failures: list[FieldGap] = field(default_factory=list)
    required_failures: list[FieldGap] = field(default_factory=list)
    #: Findings from the canonical C++ physics validator (only populated when
    #: ``run_physics=True``); empty otherwise so field-only audits are unchanged.
    physics_findings: list[PhysicsFinding] = field(default_factory=list)

    @property
    def physically_invalid(self) -> bool:
        """True iff the canonical validator flagged an IMPOSSIBLE finding."""
        return any(f.severity == "IMPOSSIBLE" for f in self.physics_findings)

    @property
    def passed(self) -> bool:
        return not self.critical_failures and not self.physically_invalid


@dataclass(frozen=True)
class CorruptLine:
    """A line that could not be decoded as a JSON object.

    Reported as first-class audit data when ``on_corruption="report"``;
    corruption is never silently skipped in either mode.
    """

    line: int
    reason: str


@dataclass
class CategoryAudit:
    """Per-category aggregated audit."""

    category: str
    total: int = 0
    passed: int = 0
    failures: list[ComponentAudit] = field(default_factory=list)
    warnings_only: list[ComponentAudit] = field(default_factory=list)
    critical_field_misses: dict[str, int] = field(default_factory=dict)
    required_field_misses: dict[str, int] = field(default_factory=dict)
    corrupt_lines: list[CorruptLine] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def pass_rate_pct(self) -> float:
        return (self.passed / self.total * 100.0) if self.total else 0.0


# ---------------------------------------------------------------------------
# Per-component audit
# ---------------------------------------------------------------------------


def _applicable_critical_fields(component: dict[str, Any], category: str) -> list[str]:
    """Apply subtype carve-outs to :data:`CRITICAL_PARAMS`."""
    fields = list(CRITICAL_PARAMS.get(category, ()))
    if (
        category == "magnetics"
        and "saturationCurrentPeak" in fields
        and (_is_rf_inductor(component) or _is_transformer_or_cmc(component))
    ):
        fields.remove("saturationCurrentPeak")
    if category == "capacitors":
        if "rippleCurrent" in fields and _is_mlcc(component):
            fields.remove("rippleCurrent")
        if "esr" in fields and _is_mlcc(component):
            # MLCC datasheets express loss via DF, not explicit ESR.
            fields.remove("esr")
    if category == "diodes" and "reverseRecoveryCharge" in fields and _is_schottky(component):
        fields.remove("reverseRecoveryCharge")
    return fields


def audit_component(
    component: dict[str, Any], category: str, *, run_physics: bool = False
) -> ComponentAudit:
    """Audit a single component against the pipeline-critical field map.

    Raises :class:`LibrarianError` for unknown categories — never
    returns silent pass/fail for an unrecognised one (strict-mode).

    When ``run_physics=True`` the part is also checked by the canonical C++
    ``tas_validator`` (physics: are the *values* physically possible, distinct
    from the field-presence audit). Any ``IMPOSSIBLE`` finding fails the audit.
    Physics is opt-in so field-only callers keep their exact behaviour; the
    production auditor entrypoints enable it. If physics is requested but the
    validator is unavailable, :func:`physics_validator.validate_physics` raises
    loudly — the gate is never silently skipped.
    """
    if category not in AUDITABLE_CATEGORIES:
        raise LibrarianError(
            f"audit_component: category {category!r} is not auditable.  "
            f"Known: {sorted(AUDITABLE_CATEGORIES)}."
        )
    elec = _extract_electrical(component, category)

    crit_fail = [
        FieldGap(f, _field_status(elec, f))
        for f in _applicable_critical_fields(component, category)
        if _field_status(elec, f) != FieldStatus.PRESENT
    ]
    req_fail = [
        FieldGap(f, _field_status(elec, f))
        for f in REQUIRED_PARAMS.get(category, ())
        if _field_status(elec, f) != FieldStatus.PRESENT
    ]

    physics_findings: list[PhysicsFinding] = []
    if run_physics:
        from heaviside.librarian import physics_validator as _pv

        physics_findings = list(_pv.validate_physics(component).findings)

    return ComponentAudit(
        mpn=_get_mpn(component, category),
        category=category,
        critical_failures=crit_fail,
        required_failures=req_fail,
        physics_findings=physics_findings,
    )


# ---------------------------------------------------------------------------
# Category / corpus audit
# ---------------------------------------------------------------------------


def _iter_records(
    path: Path,
    *,
    sample: int | None,
    on_corruption: str,
) -> Iterable[tuple[int, dict[str, Any] | CorruptLine]]:
    """Stream ``(lineno, record_or_corruption)`` pairs from an NDJSON file.

    ``on_corruption`` controls the behaviour on a JSON-decode error or
    a non-object top-level value:

      * ``"raise"`` (default elsewhere) — throw :class:`LibrarianError`
        immediately.  Strict-mode default, mirrors
        :func:`heaviside.librarian.tas.component_exists`.
      * ``"report"`` — yield a :class:`CorruptLine` so the caller can
        record the event as first-class audit data.  This is NOT a
        silent fallback: every bad line surfaces with a structured
        reason, just without halting the sweep.  Use this mode when
        the auditor's job is to *characterize* corruption so the
        librarian agent can plan a repair pass.
    """
    if on_corruption not in {"raise", "report"}:
        raise LibrarianError(
            f"_iter_records: on_corruption must be 'raise' or 'report', got {on_corruption!r}"
        )
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if sample is not None and lineno > sample:
                return
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rec = json.loads(stripped)
            except json.JSONDecodeError as exc:
                msg = f"JSONDecodeError: {exc.msg} (col {exc.colno})"
                if on_corruption == "raise":
                    raise LibrarianError(
                        f"auditor: corrupt JSON at {path}:{lineno}: "
                        f"{exc.msg} (col {exc.colno}).  TAS NDJSON is "
                        "append-only — corruption is a stop-the-line bug."
                    ) from exc
                yield lineno, CorruptLine(line=lineno, reason=msg)
                continue
            if not isinstance(rec, dict):
                msg = f"top-level value is {type(rec).__name__}, expected JSON object"
                if on_corruption == "raise":
                    raise LibrarianError(
                        f"auditor: {path}:{lineno} decodes to "
                        f"{type(rec).__name__}, expected JSON object."
                    )
                yield lineno, CorruptLine(line=lineno, reason=msg)
                continue
            yield lineno, rec


def audit_category(
    category: str,
    *,
    sample: int | None = None,
    on_corruption: str = "raise",
    run_physics: bool = False,
) -> CategoryAudit:
    """Audit every row of ``TAS/data/<category>.ndjson`` (or first ``sample``).

    Parameters
    ----------
    category :
        One of :data:`AUDITABLE_CATEGORIES`.
    sample :
        Limit to the first N **lines** (including blanks).  ``None``
        audits the full file.
    on_corruption :
        See :func:`_iter_records`.  Default ``"raise"`` enforces the
        strict-mode contract; pass ``"report"`` to characterize a
        corpus known to contain corruption (e.g. unresolved git
        merge-conflict markers in ``mosfets.ndjson`` pending
        librarian repair).

    Raises
    ------
    LibrarianError
        On unknown category, missing NDJSON, or — when
        ``on_corruption="raise"`` — any corrupt line.
    """
    if category not in AUDITABLE_CATEGORIES:
        raise LibrarianError(
            f"audit_category: {category!r} is not auditable.  "
            f"Known: {sorted(AUDITABLE_CATEGORIES)}."
        )
    path = _sa.TAS_DATA_DIR / f"{category}.ndjson"
    if not path.exists():
        raise LibrarianError(
            f"audit_category({category!r}): NDJSON not found at {path}.  "
            "Did the submodule fail to initialise?"
        )

    report = CategoryAudit(category=category)
    crit_misses: dict[str, int] = defaultdict(int)
    req_misses: dict[str, int] = defaultdict(int)

    for lineno, item in _iter_records(
        path,
        sample=sample,
        on_corruption=on_corruption,
    ):
        if isinstance(item, CorruptLine):
            report.corrupt_lines.append(item)
            continue
        report.total += 1
        try:
            result = audit_component(item, category, run_physics=run_physics)
        except (AttributeError, TypeError, KeyError) as exc:
            # A row whose shape is malformed (e.g. a list where an object is
            # expected) is SURFACED as a corrupt line, not allowed to abort the
            # whole category audit. Strict mode (on_corruption='raise') still
            # raises; report mode records it and moves on.
            if on_corruption == "raise":
                raise
            report.total -= 1
            report.corrupt_lines.append(
                CorruptLine(lineno, f"malformed row shape: {type(exc).__name__}: {exc}")
            )
            continue
        result.line = lineno
        for gap in result.critical_failures:
            crit_misses[gap.field] += 1
        for gap in result.required_failures:
            req_misses[gap.field] += 1
        if result.passed:
            report.passed += 1
            if result.required_failures:
                report.warnings_only.append(result)
        else:
            report.failures.append(result)

    report.critical_field_misses = dict(crit_misses)
    report.required_field_misses = dict(req_misses)
    return report


def audit_all(
    *,
    sample: int | None = None,
    on_corruption: str = "raise",
    run_physics: bool = False,
) -> dict[str, CategoryAudit]:
    """Run :func:`audit_category` for every auditable category.

    Returns a dict keyed by category name.  Categories whose NDJSON
    is missing surface as a raised :class:`LibrarianError` (we do
    not silently elide them — a missing live category is a real bug).

    ``on_corruption`` is forwarded to every per-category audit.
    """
    return {
        cat: audit_category(
            cat, sample=sample, on_corruption=on_corruption, run_physics=run_physics
        )
        for cat in AUDITABLE_CATEGORIES
    }
