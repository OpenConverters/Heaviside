"""Teacher agent — analyzes pipeline failures and extracts reusable lessons.

After a full_design run, the teacher reviews every DesignOutcome that
failed or had warnings, classifies the root cause, and writes a
structured lesson to ``knowledge/lessons.ndjson``. Future pipeline runs
query the lesson store to:

  * Skip topologies that repeatedly fail for a given spec shape
  * Relax constraints that are analytically too tight
  * Flag spec fields that are commonly missing
  * Surface component-DB gaps to the librarian

Lessons are append-only, timestamped, and deduplicated by a
(topology, category, fingerprint) key. The teacher never deletes
or mutates existing lessons — staleness is handled by a TTL field
that downstream consumers check.

Each lesson is one JSON line in the NDJSON file. Schema:

    {
      "id": "buck__margin_violation__inductor_isat_margin__2026-05-28",
      "timestamp": "2026-05-28T14:30:00Z",
      "topology": "buck",
      "category": "margin_violation",
      "severity": "warning",
      "check_name": "inductor_isat_margin",
      "detail": "margin=0.025 < threshold=0.3 — isat sizing consistently tight",
      "spec_fingerprint": "48V-12V-5A-200kHz",
      "suggestion": "increase coreAdviserSaturationMargin from 1.5 to 1.8",
      "ttl_days": 90
    }
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_LESSON_PATH = _REPO_ROOT / "knowledge" / "lessons.ndjson"


# ---------------------------------------------------------------------------
# Lesson dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Lesson:
    id: str
    timestamp: str
    topology: str
    category: str
    severity: str
    detail: str
    spec_fingerprint: str
    check_name: str | None = None
    suggestion: str | None = None
    ttl_days: int = 90


# ---------------------------------------------------------------------------
# Spec fingerprinting (for dedup + lookup)
# ---------------------------------------------------------------------------


def _spec_fingerprint(spec: Mapping[str, Any]) -> str:
    """Short human-readable fingerprint of a spec's key parameters."""
    parts = []
    vin = spec.get("inputVoltage", {})
    if isinstance(vin, dict):
        v = vin.get("nominal") or vin.get("maximum") or "?"
        parts.append(f"{v}V")
    ops = spec.get("operatingPoints") or [{}]
    op = ops[0] if isinstance(ops, list) and ops else {}
    if isinstance(op, dict):
        vouts = op.get("outputVoltages", [])
        iouts = op.get("outputCurrents", [])
        fsw = op.get("switchingFrequency")
        if vouts:
            parts.append(f"{vouts[0]}V")
        if iouts:
            parts.append(f"{iouts[0]}A")
        if isinstance(fsw, (int, float)):
            parts.append(f"{fsw / 1e3:.0f}kHz")
    return "-".join(str(p) for p in parts) or "unknown"


def _lesson_id(topology: str, category: str, detail_hash: str) -> str:
    return f"{topology}__{category}__{detail_hash}"


def _detail_hash(detail: str) -> str:
    return hashlib.sha256(detail.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Failure analysis — extract lessons from DesignOutcome
# ---------------------------------------------------------------------------

_TIGHT_THRESHOLDS = {
    "inductor_isat_margin": 0.3,
    "efficiency_sanity": 0.05,
    "fet_voltage_derating": 0.2,
    "diode_voltage_derating": 0.2,
    "capacitor_voltage_derating": 0.5,
    "duty_cycle_bounds": 0.1,
    "thermal_limit": 20.0,
}


def analyze_outcome(
    outcome: Any,
    spec: Mapping[str, Any],
) -> list[Lesson]:
    """Extract lessons from a single DesignOutcome."""
    lessons: list[Lesson] = []
    now = datetime.now(UTC).isoformat(timespec="seconds")
    topo = outcome.pick.topology.name
    fp = _spec_fingerprint(spec)

    # --- Diagnostics: pipeline-level failures ---
    for diag in outcome.diagnostics:
        if "StressDerivationError" in diag or "spec missing" in diag.lower():
            lessons.append(
                Lesson(
                    id=_lesson_id(topo, "missing_spec_field", _detail_hash(diag)),
                    timestamp=now,
                    topology=topo,
                    category="missing_spec_field",
                    severity="error",
                    detail=diag,
                    spec_fingerprint=fp,
                    suggestion="add the missing field to the spec before running this topology",
                )
            )
        elif "BOM selection" in diag or "SelectionError" in diag:
            lessons.append(
                Lesson(
                    id=_lesson_id(topo, "component_unavailable", _detail_hash(diag)),
                    timestamp=now,
                    topology=topo,
                    category="component_unavailable",
                    severity="warning",
                    detail=diag,
                    spec_fingerprint=fp,
                    suggestion="run librarian fetch to expand TAS DB coverage for this stress range",
                )
            )
        elif "sim skipped" in diag or "sim runner" in diag.lower():
            lessons.append(
                Lesson(
                    id=_lesson_id(topo, "simulation_failure", _detail_hash(diag)),
                    timestamp=now,
                    topology=topo,
                    category="simulation_failure",
                    severity="warning",
                    detail=diag,
                    spec_fingerprint=fp,
                )
            )
        elif "component design failed" in diag:
            lessons.append(
                Lesson(
                    id=_lesson_id(topo, "design_failure", _detail_hash(diag)),
                    timestamp=now,
                    topology=topo,
                    category="design_failure",
                    severity="error",
                    detail=diag,
                    spec_fingerprint=fp,
                )
            )

    # --- Realism gate: check-level failures and tight margins ---
    if outcome.verdict_dict:
        checks = outcome.verdict_dict.get("checks", [])

        for c in checks:
            name = c.get("name", "")
            status = c.get("status", "")
            margin = c.get("margin")
            value = c.get("value")

            if status == "fail":
                detail = f"{name} FAILED: value={value}, margin={margin}"
                lessons.append(
                    Lesson(
                        id=_lesson_id(topo, "realism_fail", _detail_hash(detail)),
                        timestamp=now,
                        topology=topo,
                        category="realism_fail",
                        severity="error",
                        check_name=name,
                        detail=detail,
                        spec_fingerprint=fp,
                        suggestion=_suggest_for_check(name, value, margin),
                    )
                )

            elif status == "pass" and margin is not None:
                threshold = _TIGHT_THRESHOLDS.get(name)
                if threshold is not None and margin < threshold:
                    detail = (
                        f"{name}: margin={margin:.4f} < {threshold} — "
                        f"tight under nominal conditions"
                    )
                    lessons.append(
                        Lesson(
                            id=_lesson_id(topo, "margin_violation", _detail_hash(detail)),
                            timestamp=now,
                            topology=topo,
                            category="margin_violation",
                            severity="warning",
                            check_name=name,
                            detail=detail,
                            spec_fingerprint=fp,
                            suggestion=_suggest_for_check(name, value, margin),
                        )
                    )

            elif status == "unavailable":
                detail = f"{name}: UNAVAILABLE — enricher or analyst not implemented"
                lessons.append(
                    Lesson(
                        id=_lesson_id(topo, "check_unavailable", _detail_hash(detail)),
                        timestamp=now,
                        topology=topo,
                        category="check_unavailable",
                        severity="info",
                        check_name=name,
                        detail=detail,
                        spec_fingerprint=fp,
                        ttl_days=30,
                    )
                )

    # --- Gatekeeper objections ---
    if outcome.gatekeeper and not outcome.gatekeeper.approved:
        for obj in outcome.gatekeeper.objections:
            lessons.append(
                Lesson(
                    id=_lesson_id(topo, "gatekeeper_block", _detail_hash(obj)),
                    timestamp=now,
                    topology=topo,
                    category="gatekeeper_block",
                    severity="error",
                    detail=obj,
                    spec_fingerprint=fp,
                )
            )

    return lessons


def _suggest_for_check(
    name: str,
    value: Any,
    margin: Any,
) -> str | None:
    """Generate an actionable suggestion for a specific check failure."""
    suggestions = {
        "inductor_isat_margin": (
            "increase coreAdviserSaturationMargin or pick a larger core; "
            "current core is running close to saturation"
        ),
        "efficiency_sanity": (
            "check for excessive switching losses (high Qg MOSFET?) or "
            "conduction losses (high Rds_on?); consider resonant topology"
        ),
        "fet_voltage_derating": (
            "MOSFET Vds rating too close to stress; pick a higher-voltage FET "
            "or reduce input voltage range"
        ),
        "diode_voltage_derating": (
            "diode Vrrm too close to reverse voltage stress; pick a higher-voltage diode"
        ),
        "capacitor_voltage_derating": (
            "capacitor voltage rating too close to working voltage; pick a higher-voltage cap"
        ),
        "duty_cycle_bounds": (
            "duty cycle at edge of valid range; consider a different "
            "topology or adjusting turns ratio"
        ),
        "thermal_limit": (
            "junction temperature close to or exceeding Tj_max; "
            "improve thermal path or reduce losses"
        ),
    }
    return suggestions.get(name)


# ---------------------------------------------------------------------------
# Lesson store (NDJSON append + dedup)
# ---------------------------------------------------------------------------


def store_lessons(
    lessons: Sequence[Lesson],
    *,
    path: Path | None = None,
) -> int:
    """Append lessons to the NDJSON store, skipping duplicates.

    Returns the number of new lessons written.
    """
    store_path = path or _DEFAULT_LESSON_PATH
    existing_ids: set[str] = set()
    if store_path.exists():
        for line in store_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                existing_ids.add(obj.get("id", ""))
            except json.JSONDecodeError:
                continue

    new_lessons = [l for l in lessons if l.id not in existing_ids]
    if not new_lessons:
        return 0

    store_path.parent.mkdir(parents=True, exist_ok=True)
    with store_path.open("a") as f:
        for l in new_lessons:
            f.write(json.dumps(asdict(l), separators=(",", ":")) + "\n")

    logger.info("teacher: wrote %d new lessons to %s", len(new_lessons), store_path)
    return len(new_lessons)


def load_lessons(
    *,
    topology: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    max_age_days: int | None = None,
    path: Path | None = None,
) -> list[Lesson]:
    """Load lessons from the store, optionally filtered."""
    store_path = path or _DEFAULT_LESSON_PATH
    if not store_path.exists():
        return []

    now = datetime.now(UTC)
    lessons: list[Lesson] = []
    for line in store_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        if topology and obj.get("topology") != topology:
            continue
        if category and obj.get("category") != category:
            continue
        if severity and obj.get("severity") != severity:
            continue

        if max_age_days is not None:
            ts = obj.get("timestamp", "")
            try:
                lesson_time = datetime.fromisoformat(ts)
                age = (now - lesson_time).days
                if age > max_age_days:
                    continue
            except (ValueError, TypeError):
                pass

        lessons.append(
            Lesson(
                id=obj.get("id", ""),
                timestamp=obj.get("timestamp", ""),
                topology=obj.get("topology", ""),
                category=obj.get("category", ""),
                severity=obj.get("severity", "info"),
                detail=obj.get("detail", ""),
                spec_fingerprint=obj.get("spec_fingerprint", ""),
                check_name=obj.get("check_name"),
                suggestion=obj.get("suggestion"),
                ttl_days=obj.get("ttl_days", 90),
            )
        )

    return lessons


# ---------------------------------------------------------------------------
# Pipeline integration: review all outcomes and store lessons
# ---------------------------------------------------------------------------


def review_design_run(
    outcomes: Sequence[Any],
    spec: Mapping[str, Any],
    *,
    store_path: Path | None = None,
) -> list[Lesson]:
    """Analyze all outcomes from a full_design run and store lessons.

    Called by the orchestrator after ``full_design()`` returns.
    Returns the full list of lessons extracted (including duplicates
    that were already in the store).
    """
    all_lessons: list[Lesson] = []
    for outcome in outcomes:
        lessons = analyze_outcome(outcome, spec)
        all_lessons.extend(lessons)

    if all_lessons:
        n_new = store_lessons(all_lessons, path=store_path)
        logger.info(
            "teacher: reviewed %d outcomes, extracted %d lessons (%d new)",
            len(outcomes),
            len(all_lessons),
            n_new,
        )

    return all_lessons


def summarize_lessons(
    lessons: Sequence[Lesson],
) -> str:
    """One-paragraph summary of lessons for the user."""
    if not lessons:
        return "No lessons extracted — all designs passed cleanly."

    by_cat: dict[str, int] = {}
    by_sev: dict[str, int] = {}
    topologies: set[str] = set()
    for l in lessons:
        by_cat[l.category] = by_cat.get(l.category, 0) + 1
        by_sev[l.severity] = by_sev.get(l.severity, 0) + 1
        topologies.add(l.topology)

    parts = [f"{len(lessons)} lessons from {len(topologies)} topologies:"]
    for sev in ("error", "warning", "info"):
        n = by_sev.get(sev, 0)
        if n:
            parts.append(f"{n} {sev}")
    parts.append("—")
    for cat, n in sorted(by_cat.items(), key=lambda x: -x[1]):
        parts.append(f"{cat}({n})")

    suggestions = [l.suggestion for l in lessons if l.suggestion and l.severity == "error"]
    if suggestions:
        parts.append(f"\nTop suggestion: {suggestions[0]}")

    return " ".join(parts)


__all__ = [
    "Lesson",
    "analyze_outcome",
    "load_lessons",
    "review_design_run",
    "store_lessons",
    "summarize_lessons",
]
