#!/usr/bin/env python3
"""Run the full Heaviside design pipeline against every topology that has
a canonical spec fixture under ``tests/regression/decomposer/``.

For each topology:
  1. Harvest its ``SPEC`` (or ``<NAME>_SPEC``) constant from the
     regression test module.
  2. Run ``python -m heaviside.cli design <topo> --spec /tmp/<topo>.spec.json
     --realism --out /tmp/<topo>.tas.json`` as a subprocess.
  3. Parse the stderr ``realism: verdict=...`` summary.
  4. Capture failing checks and any pipeline-stage errors.

Output: markdown table to stdout and ``docs/corpus-report.md``.

Exit codes:
  0 — every topology ran to a verdict (some may FAIL the realism gate;
      that's not a runner failure).
  1 — one or more topologies crashed the pipeline (BridgeError, exit code
      ≥ 3 not corresponding to a realism FAIL).
  2 — driver problem (no SPECs found, can't write report).

Why subprocess instead of in-process: each design run can SIGSEGV PyMKF
for some topologies; isolating in subprocesses keeps one bad topology
from killing the entire corpus run.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGRESSION_DIR = ROOT / "tests" / "regression" / "decomposer"
REPORT_PATH = ROOT / "docs" / "corpus-report.md"

# topology canonical name → regression test filename stem
TEST_FILE_BY_TOPOLOGY = {
    "buck": "test_buck",
    "boost": "test_boost",
    "cuk": "test_cuk",
    "sepic": "test_sepic",
    "zeta": "test_zeta",
    "four_switch_buck_boost": "test_four_switch_buck_boost",
    "isolated_buck": "test_isolated_buck",
    "isolated_buck_boost": "test_isolated_buck_boost",
    "flyback": "test_flyback",
    "single_switch_forward": "test_single_switch_forward",
    "two_switch_forward": "test_two_switch_forward",
    "active_clamp_forward": "test_active_clamp_forward",
    "push_pull": "test_push_pull",
    "asymmetric_half_bridge": "test_asymmetric_half_bridge",
    "phase_shifted_full_bridge": "test_phase_shifted_full_bridge",
    "weinberg": "test_weinberg",
    "llc": "test_llc",
    "cllc": "test_cllc",
    "clllc": "test_clllc",
    "dual_active_bridge": "test_dual_active_bridge",
    "vienna": "test_vienna",
}


@dataclass(slots=True)
class CorpusRow:
    topology: str
    status: str  # PASS / FAIL / INCOMPLETE / CRASH / NO_SPEC
    passes: int = 0
    fails: int = 0
    unavailable: int = 0
    not_applicable: int = 0
    failing_checks: list[str] = field(default_factory=list)
    error: str = ""


def _extract_module_literal(tree: ast.AST, target_name: str) -> Any:
    """Return the literal value of a top-level ``<target_name> = ...``
    assignment (Assign or AnnAssign). ``None`` if missing or non-literal."""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if not (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
                continue
            name = node.targets[0].id
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            if not isinstance(node.target, ast.Name) or node.value is None:
                continue
            name = node.target.id
            value = node.value
        else:
            continue
        if name != target_name:
            continue
        try:
            return ast.literal_eval(value)
        except (ValueError, TypeError):
            return None
    return None


def _extract_spec(test_path: Path) -> dict | None:
    """Grab the first top-level ``SPEC`` / ``<NAME>_SPEC`` dict literal
    in the file via AST parsing — no module import, no side effects.

    Also pulls neighbouring ``TURNS_RATIOS`` / ``MAGNETIZING_INDUCTANCE``
    module constants and stamps them into the returned spec under
    ``desiredTurnsRatios`` / ``desiredMagnetizingInductance`` (if not
    already present). The regression decompose tests pass those values
    to ``decompose_from_spec`` separately, so they're stored as module
    constants outside the SPEC dict — corpus_run.py needs them folded in
    so per-topology validators / MKF design dispatch see the full spec.
    """
    tree = ast.parse(test_path.read_text())
    spec: dict | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if not (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
                continue
            name = node.targets[0].id
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            if not isinstance(node.target, ast.Name) or node.value is None:
                continue
            name = node.target.id
            value = node.value
        else:
            continue
        if not (name == "SPEC" or name.endswith("_SPEC")):
            continue
        try:
            spec = ast.literal_eval(value)
            break
        except (ValueError, TypeError):
            continue
    if spec is None:
        return None

    turns = _extract_module_literal(tree, "TURNS_RATIOS")
    if isinstance(turns, list) and turns:
        spec.setdefault("desiredTurnsRatios", turns)
        # Several fixtures ship N outputs with only 1 entry in TURNS_RATIOS
        # (the decompose tests render one secondary only). When MKF
        # designs the magnetic it expects N+1 windings ⇔ N turns ratios
        # and SIGSEGVs on mismatch. Trim outputs to match the turns
        # ratio count when they disagree.
        ops = spec.get("operatingPoints")
        if isinstance(ops, list) and ops and isinstance(ops[0], dict):
            for op in ops:
                vouts = op.get("outputVoltages")
                iouts = op.get("outputCurrents")
                if isinstance(vouts, list) and len(vouts) > len(turns):
                    op["outputVoltages"] = vouts[: len(turns)]
                if isinstance(iouts, list) and len(iouts) > len(turns):
                    op["outputCurrents"] = iouts[: len(turns)]
    lm = _extract_module_literal(tree, "MAGNETIZING_INDUCTANCE")
    if isinstance(lm, (int, float)) and lm > 0:
        # The fixtures' SPEC dicts ship a small `desiredInductance`
        # (often 22 µH, holdover from a buck-style test). Real flyback /
        # forward / push-pull / etc. designs use the much larger
        # MAGNETIZING_INDUCTANCE constant (typically 0.5–10 mH) which
        # the decompose tests pass separately. Overwrite — not setdefault
        # — so the corpus design pass sees a realistic magnetising L.
        spec["desiredMagnetizingInductance"] = float(lm)
        spec["desiredInductance"] = float(lm)

    return spec


_VERDICT_RE = re.compile(
    r"^realism:\s+verdict=(\w+)\s+pass=(\d+)\s+fail=(\d+)\s+unavailable=(\d+)\s+not_applicable=(\d+)",
    re.MULTILINE,
)
_FAIL_CHECK_RE = re.compile(r"^\s*\[fail\]\s+(\w+)", re.MULTILINE)


def _enrich_for_realism(spec: dict) -> dict:
    """Patch the regression decompose-fixtures with the extra fields the
    Heaviside realism gate requires. The decompose tests only need
    ``inputVoltage.nominal`` to build the deck, but stress/realism need
    a ``minimum`` / ``maximum`` range to bound duty cycle. ±25% around
    nominal is the conventional choice (matches Heaviside's defaults).
    """
    out = json.loads(json.dumps(spec))  # deep copy via json round-trip
    iv = out.get("inputVoltage")
    if isinstance(iv, dict):
        nom = iv.get("nominal")
        if isinstance(nom, (int, float)) and nom > 0:
            iv.setdefault("minimum", round(0.75 * nom, 6))
            iv.setdefault("maximum", round(1.25 * nom, 6))
    elif isinstance(iv, (int, float)) and iv > 0:
        out["inputVoltage"] = {
            "nominal": float(iv),
            "minimum": round(0.75 * iv, 6),
            "maximum": round(1.25 * iv, 6),
        }
    return out


def _run_one(topology: str, spec: dict, timeout_s: float = 600.0) -> CorpusRow:
    spec = _enrich_for_realism(spec)
    row = CorpusRow(topology=topology, status="CRASH")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=f".{topology}.spec.json", delete=False,
    ) as f:
        json.dump(spec, f)
        spec_path = f.name
    out_path = spec_path.replace(".spec.json", ".tas.json")
    try:
        proc = subprocess.run(
            [
                sys.executable, "-X", "faulthandler",
                "-m", "heaviside.cli", "design", topology,
                "--spec", spec_path,
                "--realism",
                "--out", out_path,
            ],
            capture_output=True,
            timeout=timeout_s,
            cwd=str(ROOT),
            env={"PYTHONFAULTHANDLER": "1", **dict(__import__("os").environ)},
        )
    except subprocess.TimeoutExpired:
        row.error = f"timeout after {timeout_s:.0f}s"
        return row
    stderr = proc.stderr.decode(errors="replace") if proc.stderr else ""
    stdout = proc.stdout.decode(errors="replace") if proc.stdout else ""
    combined = stderr + "\n" + stdout

    m = _VERDICT_RE.search(combined)
    if m:
        verdict, p, f_, u, na = m.groups()
        row.status = verdict.upper()
        row.passes = int(p)
        row.fails = int(f_)
        row.unavailable = int(u)
        row.not_applicable = int(na)
        row.failing_checks = _FAIL_CHECK_RE.findall(combined)
        return row

    # No verdict → pipeline died before realism could run.
    row.status = "CRASH"
    # Pull the first line that looks like an error so the report is useful.
    for line in stderr.splitlines():
        if line.startswith("error:") or line.startswith("Fatal Python error"):
            row.error = line.strip()
            break
    if not row.error and proc.returncode != 0:
        row.error = f"exit={proc.returncode}"
    return row


def _render_report(rows: list[CorpusRow]) -> str:
    n = len(rows)
    n_pass = sum(1 for r in rows if r.status == "PASS")
    n_fail = sum(1 for r in rows if r.status == "FAIL")
    n_inc = sum(1 for r in rows if r.status == "INCOMPLETE")
    n_crash = sum(1 for r in rows if r.status == "CRASH")
    n_no = sum(1 for r in rows if r.status == "NO_SPEC")

    lines: list[str] = [
        "# Heaviside Corpus Run",
        "",
        f"Topologies attempted: **{n}**",
        f"- PASS: **{n_pass}**",
        f"- FAIL (realism rejected at least one check): **{n_fail}**",
        f"- INCOMPLETE (every applicable check UNAVAILABLE): **{n_inc}**",
        f"- CRASH (pipeline died before verdict): **{n_crash}**",
        f"- NO_SPEC (no regression fixture): **{n_no}**",
        "",
        "## Per-topology",
        "",
        "| Topology | Verdict | pass | fail | unavail | n/a | Failing checks / error |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for r in sorted(rows, key=lambda x: (x.status, x.topology)):
        cell = ", ".join(sorted(set(r.failing_checks))) or r.error or ""
        cell = cell.replace("|", "\\|")
        lines.append(
            f"| `{r.topology}` | {r.status} | {r.passes} | {r.fails} "
            f"| {r.unavailable} | {r.not_applicable} | {cell} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    rows: list[CorpusRow] = []
    _filter = os.environ.get("CORPUS_TOPOLOGIES", "").strip()
    _allowed = {t.strip() for t in _filter.split(",") if t.strip()} if _filter else None
    for topology, stem in TEST_FILE_BY_TOPOLOGY.items():
        if _allowed is not None and topology not in _allowed:
            continue
        test_path = REGRESSION_DIR / f"{stem}.py"
        if not test_path.exists():
            rows.append(CorpusRow(topology=topology, status="NO_SPEC",
                                  error=f"{test_path} missing"))
            continue
        spec = _extract_spec(test_path)
        if spec is None:
            rows.append(CorpusRow(topology=topology, status="NO_SPEC",
                                  error=f"no SPEC dict literal in {test_path.name}"))
            continue
        print(f"[corpus] running {topology}...", file=sys.stderr, flush=True)
        row = _run_one(topology, spec)
        line = (f"  {topology:<28} → {row.status:<11} "
                f"pass={row.passes:>2} fail={row.fails:>2} "
                f"unavail={row.unavailable:>2} n/a={row.not_applicable:>2}")
        if row.failing_checks:
            line += f"   fail: {','.join(row.failing_checks)}"
        if row.error:
            line += f"   err: {row.error}"
        print(line, file=sys.stderr, flush=True)
        rows.append(row)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(_render_report(rows))
    print(f"\nwrote {REPORT_PATH}", file=sys.stderr)
    print(_render_report(rows))

    if any(r.status == "CRASH" for r in rows):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
