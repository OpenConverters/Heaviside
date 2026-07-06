#!/usr/bin/env python3
"""Ingest FAE-judge JSON verdicts into the persistent FAE-findings memory.

This closes the learning loop automatically: after a judge shreds a crossref
report, run this on its JSON output and the durable store (fae_memory) picks up
the part-level defects so the pipeline never re-ships them (see
heaviside/pipeline/fae_memory.py).

Usage:
    python scripts/record_fae_findings.py <judge1.json> [<judge2.json> ...]

Each input is one judge verdict object (the JSON the judge_prompt emits, with a
top-level "findings" array). We record only defects that are PART-INTRINSIC —
a property of the substitute itself regardless of which original it replaces:
footprint/over-dimensioning, rated-current/Isat/Irms shortfall, DC-bias
collapse, phantom MPN, wrong headline value. We deliberately DO NOT record
context-dependent findings (temperature-grade / automotive-vs-commercial /
"contradictory rationale") because the MPN-keyed store would then wrongly demote
a part that is a perfectly good match for a different, lower-grade original. The
deterministic gates (PARAM:temp_max_C, AUTOMOTIVE_DOWNGRADE, VOLTAGE_DOWNGRADE)
already handle those per-row, where the original's grade is known.
"""

from __future__ import annotations

import json
import re
import sys

from heaviside.pipeline.fae_memory import record_findings

# Parameter-name signatures of an INTRINSIC defect (property of the sub alone).
_INTRINSIC_PARAM = re.compile(
    r"footprint|over[\s-]?dimension|board\s*area|height|package\s*size"
    r"|rated\s*current|isat|saturation|irms|ripple\s*current|current\s*rating"
    r"|dc[\s-]?bias|effective\s*cap|phantom|does\s*not\s*exist|non-?existent"
    r"|wrong\s*value|headline\s*value|inductance\s*value|capacitance\s*value",
    re.IGNORECASE,
)


def _is_intrinsic(finding: dict) -> bool:
    param = str(finding.get("parameter", ""))
    return bool(_INTRINSIC_PARAM.search(param))


def main(paths: list[str]) -> int:
    total = 0
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            verdict = json.load(fh)
        design = verdict.get("design", "")
        findings = [f for f in verdict.get("findings", []) if _is_intrinsic(f)]
        n = record_findings(findings, design=design)
        skipped = len(verdict.get("findings", [])) - len(findings)
        print(f"{p}: recorded {n} intrinsic finding(s) "
              f"({skipped} context-dependent skipped)")
        total += n
    print(f"total intrinsic findings recorded: {total}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1:]))
