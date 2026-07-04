#!/usr/bin/env python3
"""FAE-eval harness — run BOM fixtures through the REAL cross-reference server.

This is the deterministic half of the FAE Adversary Loop (docs/crossref_v2_
proposal.md, Part 6.5). It drives the exact HTTP endpoints the web GUI calls —
`POST /jobs/crossref/from-bom`, `GET /jobs/{id}`, `GET /jobs/{id}/report.pdf` —
so a run exercises the identical pipeline a customer would (real LLM stages,
real tokens, the real PDF report), and is fully repeatable/scriptable.

For each fixture it writes, under a timestamped run directory:
  <ref>/result.json   — the full crossref outcome (components, statuses, notes,
                        match_detail, guardrail_fires)
  <ref>/report.pdf    — the customer-facing PDF (the judge's input)
  <ref>/violations.json — deterministic value-integrity violations (auto-grade)
  run_summary.json    — per-design coverage + violation counts

The ADVERSARIAL JUDGE step is NOT run here: it is an independent Opus 4.8 agent
(see tests/evals/fae/judge_prompt.md and tests/evals/fae/README.md) that reads
each report.pdf and returns structured findings. Keeping the token-burning judge
out of this script means the harness itself is cheap to re-run and the judge can
be pointed at any past run directory.

Usage:
    python3 scripts/fae_eval/run_designs.py                 # all fixtures
    python3 scripts/fae_eval/run_designs.py --only trap_inductor_1p5uH
    python3 scripts/fae_eval/run_designs.py --base-url http://127.0.0.1:8773
    python3 scripts/fae_eval/run_designs.py --run-dir /path/to/out

Assumes a Heaviside server is already running (start one on a fresh port with
your current code:  `python3 -m uvicorn heaviside.api:app --port 8788`).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib import error, parse, request

# Make `heaviside` importable when run as a script (sys.path[0] is this dir).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "evals" / "fae" / "fixtures"
RUNS_DIR = Path(__file__).resolve().parents[2] / "tests" / "evals" / "fae" / "runs"


def _http_json(url: str, *, method: str = "GET", timeout: float = 30.0) -> dict:
    req = request.Request(url, method=method)
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _post_multipart_bom(base_url: str, target: str, csv_path: Path) -> str:
    """Submit a BOM CSV exactly as the GUI's 'Upload BOM' does; return job_id."""
    boundary = "----faeeval7f3c2b1a"
    filename = csv_path.name
    body = bytearray()
    body += f"--{boundary}\r\n".encode()
    body += (
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
    ).encode()
    body += b"Content-Type: text/csv\r\n\r\n"
    body += csv_path.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode()
    q = parse.urlencode({"target_manufacturer": target})
    url = f"{base_url}/jobs/crossref/from-bom?{q}"
    req = request.Request(url, data=bytes(body), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with request.urlopen(req, timeout=60.0) as resp:
        return json.loads(resp.read().decode())["job_id"]


def _poll(base_url: str, job_id: str, *, timeout_s: float = 1200.0) -> dict:
    """Poll GET /jobs/{id} until done/error (the GUI polls every 2.5 s)."""
    deadline = time.monotonic() + timeout_s
    last_pct = -1
    while time.monotonic() < deadline:
        job = _http_json(f"{base_url}/jobs/{job_id}")
        status = job.get("status")
        pct = int((job.get("progress") or 0) * 100) if isinstance(job.get("progress"), float) else job.get("progress", 0)
        if pct != last_pct:
            print(f"    [{job_id}] {status} {pct}%", flush=True)
            last_pct = pct
        if status == "done":
            return job
        if status in ("error", "cancelled"):
            raise RuntimeError(f"job {job_id} ended {status}: {job.get('error')}")
        time.sleep(2.5)
    raise TimeoutError(f"job {job_id} did not finish within {timeout_s}s")


def _download_pdf(base_url: str, job_id: str, dest: Path) -> bool:
    # The PDF path needs WeasyPrint (system pango/cairo), which isn't always
    # installed. It's a best-effort bonus; the HTML report (below) is the
    # canonical judge artifact and needs no native deps.
    try:
        with request.urlopen(f"{base_url}/jobs/{job_id}/report.pdf", timeout=120.0) as resp:
            dest.write_bytes(resp.read())
        return True
    except (error.HTTPError, error.URLError) as e:
        print(f"    PDF unavailable ({e}) — using HTML report instead", flush=True)
        return False


def _render_html(result: dict, dest: Path, *, title: str) -> bool:
    """Render the self-contained customer-facing HTML report in-process (the
    same renderer the server's PDF path uses, minus WeasyPrint). This is the
    judge's canonical input — it is exactly what the customer sees, and needs no
    native libraries."""
    try:
        from heaviside.report.crossref_html import render_crossref_html

        dest.write_text(render_crossref_html(result, title=title))
        return True
    except Exception as e:  # pragma: no cover - surfaced, not swallowed
        print(f"    HTML report render failed: {e}", flush=True)
        return False


def _grade(result: dict, invariants: dict) -> list[dict]:
    from heaviside.pipeline.crossref_invariants import check_result

    comps = result.get("components", [])
    # Normalise the server's component shape to the row shape the checker reads.
    rows = []
    for c in comps:
        rows.append(
            {
                "ref_des": c.get("ref_des"),
                "component_type": c.get("component_type"),
                "original_value": c.get("original_value"),
                "substitute_value": c.get("substitute_value"),
                "substitute_pn": c.get("substitute_mpn") or c.get("substitute_pn"),
                "substitute_dielectric": c.get("substitute_dielectric"),
                "substitute_technology": c.get("substitute_technology"),
                "status": c.get("status"),
            }
        )
    return [v.__dict__ for v in check_result(rows, invariants)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://127.0.0.1:8773")
    ap.add_argument("--only", default=None, help="fixture file stem to run alone")
    ap.add_argument("--run-dir", default=None, help="output dir (default: runs/<ts>)")
    ap.add_argument("--timestamp", default=None, help="run id (Date.now unavailable in-harness)")
    args = ap.parse_args()

    manifest = json.loads((FIXTURES_DIR / "manifest.json").read_text())
    target = manifest["target"]
    fixtures = manifest["fixtures"]
    if args.only:
        fixtures = [f for f in fixtures if Path(f["file"]).stem == args.only]
        if not fixtures:
            print(f"no fixture matching {args.only!r}", file=sys.stderr)
            return 2

    # Health check — fail loud if the server isn't up (no silent skip).
    try:
        _http_json(f"{args.base_url}/health")
    except Exception as e:
        print(f"server not reachable at {args.base_url}: {e}\n"
              f"start one with: python3 -m uvicorn heaviside.api:app --port "
              f"{parse.urlparse(args.base_url).port or 8000}", file=sys.stderr)
        return 3

    run_id = args.timestamp or str(int(time.time()))
    run_dir = Path(args.run_dir) if args.run_dir else (RUNS_DIR / run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"FAE eval run → {run_dir}  (target: {target})", flush=True)

    summary = []
    for fx in fixtures:
        stem = Path(fx["file"]).stem
        csv_path = FIXTURES_DIR / fx["file"]
        d = run_dir / stem
        d.mkdir(exist_ok=True)
        print(f"\n▶ {stem}: submitting {csv_path.name}", flush=True)
        try:
            job_id = _post_multipart_bom(args.base_url, target, csv_path)
            job = _poll(args.base_url, job_id)
        except Exception as e:
            print(f"  ✗ {stem} failed: {e}", flush=True)
            summary.append({"design": stem, "error": str(e)})
            continue
        result = job.get("result", {})
        (d / "result.json").write_text(json.dumps(result, indent=2))
        has_pdf = _download_pdf(args.base_url, job_id, d / "report.pdf")
        has_html = _render_html(result, d / "report.html", title=fx.get("title", stem))
        violations = _grade(result, fx.get("invariants", {}))
        (d / "violations.json").write_text(json.dumps(violations, indent=2))
        row = {
            "design": stem,
            "job_id": job_id,
            "coverage_pct": result.get("coverage_pct"),
            "n_components": len(result.get("components", [])),
            "n_violations": len(violations),
            "judge_artifact": str(d / ("report.pdf" if has_pdf else "report.html"))
            if (has_pdf or has_html)
            else None,
        }
        summary.append(row)
        flag = "⚠ VIOLATIONS" if violations else "clean"
        print(f"  ✓ {stem}: {row['n_components']} rows, coverage "
              f"{row['coverage_pct']}%, invariants {flag}", flush=True)
        for v in violations:
            print(f"      - {v['ref_des']}/{v['parameter']}: {v['detail']}", flush=True)

    (run_dir / "run_summary.json").write_text(json.dumps(summary, indent=2))
    total_viol = sum(r.get("n_violations", 0) for r in summary)
    print(f"\n== run complete: {len(summary)} designs, {total_viol} invariant "
          f"violation(s) ==\nnext: run the independent Opus 4.8 FAE judge on each "
          f"report.pdf (see tests/evals/fae/README.md)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
