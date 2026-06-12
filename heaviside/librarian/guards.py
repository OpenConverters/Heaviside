"""TAS librarian: insert-time integrity guards + offline integrity scan.

Background (June 2026 cleanup)
------------------------------

A database-cleaning pass quarantined several classes of junk rows that
earlier tooling had let into ``TAS/data/*.ndjson`` (now preserved in
``TAS/data/*.quarantine_*.ndjson`` for reference):

1. **Synthetic rows** — 4,860 bulk-generated diodes with a fake series
   taxonomy (``Schottky_25V``, ``TVS_5V``, ``SiC_Schottky_1200V``),
   fabricated MPNs (``InUF0240N003SOD-3234321``) and dead/fake
   datasheet URLs.
2. **Placeholder MPNs** — 23,084 Vishay capacitor catalog-matrix stubs
   whose ``partNumber`` merely repeats the ``series``; value-encoding
   pseudo-MPNs (``WCAP-MLCC-1nF-50V``).
3. **Wrong-part datasheet URLs** — rows pointing at a different
   manufacturer's PDF, or at search pages instead of datasheets.
4. **Telemetry records** appended to ``converters.ndjson``
   (``{'id', 'status', 'tas', ...}`` shape).

This module makes sure none of that can come back:

* :func:`guard_component` — the **single shared insert-time guard**.
  Every TAS write path (``add_component``, the staging pipeline, the
  ``librarian search --apply`` CLI, the agent ``add_component`` tool)
  funnels through it.  It throws :class:`GuardRejectionError` with
  every offending reason listed — no silent drops, no partial writes.
* :func:`integrity_issues` — the pure, pattern-based check behind the
  guard.  Objective checks only: **no network reachability probes, no
  subjective plausibility heuristics on electrical values.**  Those
  belong to the component-auditor agent's review, not an insert gate.
* :func:`integrity_scan` — the offline, **read-only** audit pass used
  by ``heaviside librarian audit --integrity``.  Reports (without
  modifying anything): rows that would fail the insert guard, exact
  duplicate payloads, same-MPN groups with more than N copies, and
  rows whose datasheet URL host belongs to a *different known*
  manufacturer than ``manufacturerInfo.name``.

All pattern tables below are module-level and reviewable; each entry
cites the quarantined junk class it was derived from.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from heaviside.librarian import safe_access as _sa
from heaviside.librarian.safe_access import LibrarianError

__all__ = [
    "BAD_DATASHEET_URL_PATTERNS",
    "MANUFACTURER_DOMAINS",
    "MPN_REQUIRED_CATEGORIES",
    "PLACEHOLDER_MPN_PATTERNS",
    "SYNTHETIC_SERIES_RE",
    "TELEMETRY_SHAPE_KEYS",
    "GuardRejectionError",
    "IntegrityFinding",
    "IntegrityReport",
    "guard_component",
    "integrity_issues",
    "integrity_scan",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GuardRejectionError(LibrarianError):
    """A candidate row was rejected by the insert-time integrity guard.

    ``reasons`` lists every objective check the row failed (the guard
    does not stop at the first hit, so the caller can fix everything
    in one pass).
    """

    def __init__(self, category: str, mpn: str, reasons: list[str]):
        self.category = category
        self.mpn = mpn
        self.reasons = reasons
        formatted = "\n".join(f"  - {r}" for r in reasons)
        super().__init__(
            f"insert guard rejected {category}/{mpn!r} "
            f"({len(reasons)} reason{'s' if len(reasons) != 1 else ''}):\n{formatted}\n"
            "Suspect rows belong in a TAS/data/*.quarantine_*.ndjson file, "
            "never in the main database."
        )


# ---------------------------------------------------------------------------
# Pattern tables (reviewable; derived from the quarantined junk classes)
# ---------------------------------------------------------------------------


# Junk class 1 — synthetic series taxonomy from the bulk-generation
# pass: 'Schottky_25V', 'TVS_5V', 'Zener_12V', 'Ultrafast_200V',
# 'SiC_Schottky_1200V', 'Si_600V' (igbts).  Real manufacturer series
# names never follow '<Word>[_<Word>]_<NN>V'.
SYNTHETIC_SERIES_RE: re.Pattern[str] = re.compile(r"^[A-Za-z]+(_[A-Za-z]+)?_\d+V$")

# Junk class 2 — placeholder / value-encoding pseudo-MPNs.
# Each entry is (compiled pattern, reason).  Patterns are matched with
# .search() against the partNumber.  Keep them TIGHT: legitimate MPNs
# embed letter runs like 'NF' without delimiters (ST 'STP40NF03L',
# Samsung 'CL05B102KB5NFNC', Mitsubishi 'CM100DU-24NF'), so the
# value-encoding pattern requires a hyphen-/end-bounded token with a
# lowercase SI prefix exactly as the quarantined rows used
# ('WCAP-MLCC-1nF-50V', 'WCAP-ATH-10uF-...').
PLACEHOLDER_MPN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"^WCAP-(MLCC|ATH)-"),
        "Würth WCAP catalog-family prefix used as a pseudo-MPN "
        "(quarantined value-encoding stubs 'WCAP-MLCC-1nF-50V'); real "
        "Würth MPNs are numeric order codes (e.g. 885012206026)",
    ),
    (
        re.compile(r"(?:^|-)\d+(?:\.\d+)?(?:uF|nF|pF|µF)(?:-|$)"),
        "hyphen-delimited capacitance token inside the partNumber — a "
        "value-encoding pseudo-MPN, not a manufacturer part number",
    ),
    (
        re.compile(r"(?:^|-)\d+(?:\.\d+)?(?:uH|nH|µH|mH)(?:-|$)"),
        "hyphen-delimited inductance token inside the partNumber — a "
        "value-encoding pseudo-MPN, not a manufacturer part number",
    ),
)

# Junk class 3 — datasheet URLs that cannot possibly be a datasheet.
# NOTE: objective string patterns only.  No reachability checks here
# (a 404 probe is a network call and does not belong at insert time)
# and no attempt to verify the PDF content.
BAD_DATASHEET_URL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"^https?://(www\.)?example\.com", re.IGNORECASE),
        "example.com placeholder URL (synthetic-row junk class)",
    ),
    (
        re.compile(r"vishay\.com/en/search", re.IGNORECASE),
        "vishay.com search-page URL — real Vishay datasheets live under "
        "vishay.com/docs/",
    ),
    (
        re.compile(r"datasheetpdf\.com", re.IGNORECASE),
        "datasheetpdf.com aggregator search page, not a manufacturer "
        "datasheet",
    ),
)

# Junk class 5 — pipeline telemetry objects appended to a component
# file.  A telemetry record carries both of these top-level keys; no
# legitimate TAS component envelope does.
TELEMETRY_SHAPE_KEYS: frozenset[str] = frozenset({"id", "status"})

# Categories whose rows are physical components and therefore MUST
# carry a resolvable manufacturer part number.  converters /
# controllers / quarantine rows use different identity schemes.
MPN_REQUIRED_CATEGORIES: frozenset[str] = frozenset(
    {"mosfets", "diodes", "igbts", "capacitors", "resistors", "magnetics"}
)

# Junk class 3 (audit-only) — known manufacturer → datasheet-host
# pairs.  Used by :func:`integrity_scan` to flag rows whose
# datasheetUrl host belongs to a DIFFERENT manufacturer in this table
# (e.g. a Vishay row pointing at nexperia.com).  Deliberately small
# and explicit: a manufacturer or host that is not listed here is
# never flagged — we do not guess.  Derived from the quarantined
# wrong-part URLs (GAN033-650WSP → nexperia.com, STP12N60M2 → aosmd.com,
# CMF20120D → microchip.com) plus the manufacturers most common in TAS.
MANUFACTURER_DOMAINS: dict[str, tuple[str, ...]] = {
    "vishay": ("vishay.com",),
    "nexperia": ("nexperia.com",),
    "infineon": ("infineon.com",),
    "stmicroelectronics": ("st.com",),
    "onsemi": ("onsemi.com",),
    "on semiconductor": ("onsemi.com",),
    "wolfspeed": ("wolfspeed.com", "cree.com"),
    "cree": ("wolfspeed.com", "cree.com"),
    "gan systems": ("gansystems.com",),
    "alpha & omega semiconductor": ("aosmd.com",),
    "alpha and omega semiconductor": ("aosmd.com",),
    "microchip": ("microchip.com",),
    "rohm": ("rohm.com",),
    "toshiba": ("toshiba.semicon-storage.com",),
    "texas instruments": ("ti.com",),
    "diodes incorporated": ("diodes.com",),
    "littelfuse": ("littelfuse.com", "ixys.com"),
    "ixys": ("littelfuse.com", "ixys.com"),
    "nichicon": ("nichicon.co.jp", "nichicon.com"),
    "murata": ("murata.com",),
    "tdk": ("tdk.com", "tdk-electronics.tdk.com", "product.tdk.com"),
    "kemet": ("kemet.com",),
    "würth elektronik": ("we-online.com", "we-online.de"),
    "wurth elektronik": ("we-online.com", "we-online.de"),
    "coilcraft": ("coilcraft.com",),
}

# Reverse index: datasheet host suffix → set of manufacturer keys it
# legitimately belongs to.
_DOMAIN_OWNERS: dict[str, set[str]] = defaultdict(set)
for _mfr, _domains in MANUFACTURER_DOMAINS.items():
    for _dom in _domains:
        _DOMAIN_OWNERS[_dom].add(_mfr)


# ---------------------------------------------------------------------------
# Envelope walking helpers
# ---------------------------------------------------------------------------


def _walk_dicts(obj: Any) -> Iterator[dict[str, Any]]:
    """Yield every dict reachable inside ``obj`` (depth-first)."""
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk_dicts(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_dicts(item)


def _collect_str(component: dict[str, Any], key: str) -> list[str]:
    """Collect every non-empty string value stored under ``key``."""
    return [
        d[key]
        for d in _walk_dicts(component)
        if isinstance(d.get(key), str) and d[key].strip()
    ]


def _collect_parts(component: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect every dict carrying a ``partNumber`` key."""
    return [d for d in _walk_dicts(component) if "partNumber" in d]


def _url_host(url: str) -> str | None:
    m = re.match(r"https?://([^/:?#]+)", url, re.IGNORECASE)
    return m.group(1).lower() if m else None


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _guard_mpn(component: dict[str, Any]) -> str:
    """Best-effort MPN label for error messages (never throws)."""
    for part in _collect_parts(component):
        pn = part.get("partNumber")
        if isinstance(pn, str) and pn.strip():
            return pn
    for d in _walk_dicts(component):
        ref = d.get("reference")
        if isinstance(ref, str) and ref.strip():
            return ref
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Pure pattern checks
# ---------------------------------------------------------------------------


def integrity_issues(
    component: dict[str, Any],
    *,
    require_mpn: bool = True,
) -> list[str]:
    """Return every objective integrity violation in ``component``.

    Pure and offline: pattern checks only, no schema validation, no
    file or network I/O.  An empty list means the row passes.  Used
    both by the insert-time :func:`guard_component` (which throws) and
    by the read-only :func:`integrity_scan` (which reports).

    ``require_mpn=False`` skips the anonymous-row check — used for
    staging (partial payloads allowed there by contract) and for
    non-component categories (converters, controllers) whose rows use
    different identity schemes.  All other pattern checks always run.
    """
    issues: list[str] = []

    if not isinstance(component, dict):
        return [f"row is {type(component).__name__}, expected a JSON object"]

    # Junk class 5 — telemetry record headed for a component file.
    if TELEMETRY_SHAPE_KEYS.issubset(component.keys()):
        issues.append(
            "telemetry-shaped object (top-level 'id' + 'status' keys) — "
            "pipeline telemetry must never be appended to a TAS component file"
        )
        return issues  # nothing below applies to a non-component object

    parts = _collect_parts(component)

    # Missing MPN.  add_component also enforces this via its own MPN
    # extraction, but the guard states it explicitly so every write
    # path rejects anonymous rows.
    if require_mpn:
        part_numbers = [
            p["partNumber"]
            for p in parts
            if isinstance(p.get("partNumber"), str) and p["partNumber"].strip()
        ]
        if not part_numbers:
            issues.append(
                "no non-empty partNumber anywhere in the envelope — a part "
                "without a resolvable real MPN is not written to the main DB"
            )

    for part in parts:
        pn = part.get("partNumber")
        if not isinstance(pn, str) or not pn.strip():
            continue
        series = part.get("series")
        # Junk class 2 — partNumber merely repeats the series
        # (Vishay 'TR3' catalog-matrix stubs).
        if isinstance(series, str) and series.strip() and pn == series:
            issues.append(
                f"partNumber {pn!r} equals its series — a catalog-matrix "
                "placeholder, not a real orderable MPN"
            )
        # Junk class 2 — value-encoding / placeholder MPN schemes.
        for pattern, reason in PLACEHOLDER_MPN_PATTERNS:
            if pattern.search(pn):
                issues.append(f"partNumber {pn!r}: {reason}")

    # Junk class 1 — synthetic series taxonomy ('Schottky_25V', ...).
    # The bulk-generation pass stored it under part.series; check
    # 'family' too since the librarian prompt routes series there.
    for key in ("series", "family"):
        for value in _collect_str(component, key):
            if SYNTHETIC_SERIES_RE.match(value):
                issues.append(
                    f"{key} {value!r} matches the synthetic bulk-generation "
                    "taxonomy '^[A-Za-z]+(_[A-Za-z]+)?_\\d+V$' — fabricated row"
                )

    # Junk class 3 — datasheet URLs that cannot be datasheets.
    for url in _collect_str(component, "datasheetUrl"):
        if not re.match(r"^https?://", url, re.IGNORECASE):
            issues.append(
                f"datasheetUrl {url!r} is not an http(s) URL"
            )
            continue
        for pattern, reason in BAD_DATASHEET_URL_PATTERNS:
            if pattern.search(url):
                issues.append(f"datasheetUrl {url!r}: {reason}")

    return issues


# ---------------------------------------------------------------------------
# Insert-time guard (the single shared gate for every write path)
# ---------------------------------------------------------------------------


def guard_component(
    category: str,
    component: dict[str, Any],
    *,
    validate_schema: bool = True,
    require_mpn: bool = True,
) -> None:
    """Reject ``component`` loudly if any integrity check fails.

    Order of checks:

    1. JSON-schema validation (:func:`heaviside.librarian.tas.
       validate_component`) when ``validate_schema=True`` — staging
       passes ``False`` because it intentionally accepts partial
       payloads for the auditor to inspect; the pattern checks below
       still apply there because a placeholder MPN can never become
       valid.
    2. Telemetry shape + every pattern check (:func:`integrity_issues`).

    Raises
    ------
    ValidationError, SchemaNotFoundError, UnknownCategoryError
        Propagated unchanged from schema validation.
    GuardRejectionError
        If any pattern check fails (all reasons listed).
    """
    _sa._validate_category(category)
    if not isinstance(component, dict):
        raise GuardRejectionError(
            category,
            "UNKNOWN",
            [f"component must be a dict, got {type(component).__name__}"],
        )

    if validate_schema:
        # Local import: tas.py imports this module at top level, so the
        # reverse edge must be lazy to avoid a circular import.
        from heaviside.librarian.tas import validate_component

        validate_component(category, component)

    issues = integrity_issues(component, require_mpn=require_mpn)
    if issues:
        raise GuardRejectionError(category, _guard_mpn(component), issues)


# ---------------------------------------------------------------------------
# Offline integrity scan (read-only; CLI `librarian audit --integrity`)
# ---------------------------------------------------------------------------


@dataclass
class IntegrityFinding:
    """One offending row located by :func:`integrity_scan`."""

    line: int
    mpn: str
    reasons: list[str]


@dataclass
class IntegrityReport:
    """Read-only integrity findings for one TAS category file."""

    category: str
    total: int = 0
    guard_failures: list[IntegrityFinding] = field(default_factory=list)
    # payload sha1 → line numbers (only groups with >= 2 rows kept)
    exact_duplicates: dict[str, list[int]] = field(default_factory=dict)
    # mpn → row count (only groups with > max_mpn_copies kept)
    mpn_over_limit: dict[str, int] = field(default_factory=dict)
    # rows whose datasheetUrl host belongs to a different KNOWN manufacturer
    domain_mismatches: list[IntegrityFinding] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (
            self.guard_failures
            or self.exact_duplicates
            or self.mpn_over_limit
            or self.domain_mismatches
        )


def _manufacturer_name(component: dict[str, Any]) -> str | None:
    """Extract ``manufacturerInfo.name`` from any envelope depth."""
    for d in _walk_dicts(component):
        mi = d.get("manufacturerInfo")
        if isinstance(mi, dict):
            name = mi.get("name")
            if isinstance(name, str) and name.strip():
                return name
    return None


def _domain_mismatch_reasons(component: dict[str, Any]) -> list[str]:
    """Flag datasheet URLs whose host belongs to a different KNOWN
    manufacturer than ``manufacturerInfo.name``.

    Only explicit :data:`MANUFACTURER_DOMAINS` pairs are consulted —
    unknown manufacturers and unknown hosts are never flagged.
    Distributor/aggregator hosts (mouser.com, digikey.com) are not in
    the table, so they never trip this check either.
    """
    name = _manufacturer_name(component)
    if name is None:
        return []
    mfr_key = name.strip().lower()
    own_domains = MANUFACTURER_DOMAINS.get(mfr_key)
    if own_domains is None:
        return []  # manufacturer not in the explicit table — never guess

    reasons: list[str] = []
    for url in _collect_str(component, "datasheetUrl"):
        host = _url_host(url)
        if host is None:
            continue
        if any(_host_matches(host, dom) for dom in own_domains):
            continue
        owners = {
            owner
            for dom, owner_set in _DOMAIN_OWNERS.items()
            if _host_matches(host, dom)
            for owner in owner_set
        }
        if owners and mfr_key not in owners:
            reasons.append(
                f"manufacturer {name!r} but datasheetUrl host {host!r} "
                f"belongs to {sorted(owners)} — wrong-part datasheet link"
            )
    return reasons


def integrity_scan(
    category: str,
    *,
    sample: int | None = None,
    max_mpn_copies: int = 1,
    check_schema: bool = False,
) -> IntegrityReport:
    """Scan ``TAS/data/<category>.ndjson`` read-only for integrity junk.

    Reports, without modifying anything:

    * rows that would fail the insert guard (pattern checks; plus
      schema validation when ``check_schema=True`` and the category
      has a schema — off by default because full-corpus jsonschema
      runs are slow on the 100k+-row files),
    * exact-duplicate payloads (key-order-insensitive),
    * MPN groups with more than ``max_mpn_copies`` rows,
    * datasheet URLs whose host belongs to a different known
      manufacturer (see :data:`MANUFACTURER_DOMAINS`).

    Raises
    ------
    UnknownCategoryError
        If ``category`` is not whitelisted.
    LibrarianError
        If the NDJSON file is missing or a line is corrupt JSON
        (corruption is a stop-the-line event, mirroring the writer).
    """
    _sa._validate_category(category)
    path = _sa.TAS_DATA_DIR / f"{category}.ndjson"
    if not path.exists():
        raise LibrarianError(
            f"integrity_scan({category!r}): NDJSON not found at {path}."
        )

    validate = None
    if check_schema:
        from heaviside.librarian.tas import SCHEMA_MAP, validate_component

        if category in SCHEMA_MAP:
            validate = validate_component

    report = IntegrityReport(category=category)
    payload_lines: dict[str, list[int]] = defaultdict(list)
    mpn_counts: Counter[str] = Counter()

    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            if sample is not None and lineno > sample:
                break
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise LibrarianError(
                    f"integrity_scan({category!r}): corrupt JSON at "
                    f"{path}:{lineno}: {exc.msg} (col {exc.colno})."
                ) from exc
            report.total += 1

            reasons = (
                integrity_issues(
                    record,
                    require_mpn=category in MPN_REQUIRED_CATEGORIES,
                )
                if isinstance(record, dict)
                else [f"top-level value is {type(record).__name__}, expected object"]
            )
            if not reasons and validate is not None:
                try:
                    validate(category, record)
                except LibrarianError as exc:
                    reasons = [f"schema validation failed: {exc}"]
            mpn = _guard_mpn(record) if isinstance(record, dict) else "UNKNOWN"
            if reasons:
                report.guard_failures.append(
                    IntegrityFinding(line=lineno, mpn=mpn, reasons=reasons)
                )

            digest = hashlib.sha1(
                json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            payload_lines[digest].append(lineno)
            if mpn != "UNKNOWN":
                mpn_counts[mpn.upper()] += 1

            if isinstance(record, dict):
                mismatches = _domain_mismatch_reasons(record)
                if mismatches:
                    report.domain_mismatches.append(
                        IntegrityFinding(line=lineno, mpn=mpn, reasons=mismatches)
                    )

    report.exact_duplicates = {
        digest: lines for digest, lines in payload_lines.items() if len(lines) > 1
    }
    report.mpn_over_limit = {
        mpn: count for mpn, count in mpn_counts.items() if count > max_mpn_copies
    }
    return report
