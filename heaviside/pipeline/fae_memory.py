"""Persistent FAE-findings memory — the adversarial loop's learning store.

The FAE Adversary Loop (tests/evals/fae) has independent senior-FAE judges shred
the crossref output; their findings drive each development round. This module
makes that learning DURABLE and ONLINE: a finding the FAE already proved (a
substitute MPN with a real defect — "WE-LQ 7440450015 rated only 1.75 A for a
3.25 A original", "GRT21…→885012106032 is an X7T→X5R dielectric downgrade") is
recorded here, and the crossref pipeline consults it as a deterministic guard so
the SAME mistake never ships again. The agent learns from its own review history.

The store lives OUTSIDE the app directory ($HEAVISIDE_FAE_MEMORY, default
~/.heaviside/fae_findings.jsonl) so it survives code deploys on prod — the same
place the runtime TAS delta and job state persist. One JSON object per line:

    {"substitute_mpn": "...", "original_mpn": "...", "parameter": "...",
     "severity": "critical|major|...", "reality": "...", "design": "...", "ts": "..."}

No fabrication: only judge-emitted, datasheet-backed findings are recorded, and a
guard only DEMOTES/flags (never fabricates a pass) — the same fail-loud discipline
as the rest of the pipeline.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SEVERITY_RANK = {"critical": 3, "major": 2, "minor": 1, "nitpick": 0}


def _store_path() -> Path:
    return Path(
        os.environ.get(
            "HEAVISIDE_FAE_MEMORY",
            str(Path.home() / ".heaviside" / "fae_findings.jsonl"),
        )
    )


def _norm(mpn: Any) -> str:
    return str(mpn or "").strip().lower()


def record_findings(findings: list[dict[str, Any]], *, design: str = "", ts: str = "") -> int:
    """Append FAE judge findings (the JSON `findings` array) to the durable store.
    Only records entries that name a substitute MPN and a real (major/critical)
    defect — nitpicks/presentation notes are not learned. Returns the count
    written. De-dupes on (substitute_mpn, parameter, severity) against what's
    already stored so re-recording a run is idempotent."""
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {
        (_norm(r.get("substitute_mpn")), str(r.get("parameter", "")), str(r.get("severity", "")))
        for r in _read_all()
    }
    written = 0
    with path.open("a", encoding="utf-8") as fh:
        for f in findings or []:
            if not isinstance(f, dict):
                continue
            sub = _norm(f.get("substitute"))
            sev = str(f.get("severity", "")).lower()
            # Only learn real, part-specific defects — a guard we can act on later.
            if not sub or _SEVERITY_RANK.get(sev, 0) < 2:
                continue
            param = str(f.get("parameter", ""))
            key = (sub, param, sev)
            if key in existing:
                continue
            existing.add(key)
            rec = {
                "substitute_mpn": f.get("substitute"),
                "original_mpn": f.get("original"),
                "ref_des": f.get("ref_des"),
                "parameter": param,
                "severity": sev,
                "reality": f.get("reality") or f.get("how_a_customer_gets_burned") or "",
                "design": design,
                "ts": ts,
            }
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1
    if written:
        logger.info("FAE memory: recorded %d finding(s) to %s", written, path)
    return written


def _read_all() -> list[dict[str, Any]]:
    path = _store_path()
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def known_findings_for(substitute_mpn: Any) -> list[dict[str, Any]]:
    """Prior FAE findings against this exact substitute MPN (major/critical only),
    most-severe first. Empty when the store is absent or the part is unflagged —
    so this is a pure, side-effect-free lookup the pipeline can call per row."""
    sub = _norm(substitute_mpn)
    if not sub:
        return []
    hits = [r for r in _read_all() if _norm(r.get("substitute_mpn")) == sub]
    hits.sort(key=lambda r: _SEVERITY_RANK.get(str(r.get("severity", "")).lower(), 0), reverse=True)
    return hits
