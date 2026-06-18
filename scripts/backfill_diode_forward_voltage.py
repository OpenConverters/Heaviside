#!/usr/bin/env python3
"""Backfill forwardVoltage for diodes missing it, by parsing Vishay datasheet PDFs.

Distributors (DK/Mouser) either don't index these Vishay VS- parts or have
exhausted daily quotas. The datasheetUrl field already points to the correct
Vishay PDF. pdfplumber extracts the VFM value from the electrical table on p.1-2.

Usage:
  scripts/backfill_diode_forward_voltage.py [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
import pdfplumber

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "TAS" / "data" / "diodes.ndjson"
CHECKPOINT = Path.home() / ".heaviside" / "jobs" / "diodes_vf_checkpoint.json"


def extract_vf_from_pdf(pdf_bytes: bytes) -> float | None:
    """Extract max forward voltage (VFM) from a Vishay diode datasheet PDF."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages[:3]:
            text = page.extract_text() or ""
            lines = text.splitlines()
            for j, line in enumerate(lines):
                if re.search(r'VF\s*M|V_?FM|forward\s+voltage', line, re.I):
                    block = "\n".join(lines[j:j + 8])
                    # Pattern: "- 0.715 -" (typical/max table format)
                    m = re.search(r'[-–]\s*([0-9]+\.[0-9]+)\s*[-–]', block)
                    if not m:
                        m = re.search(r'\b([0-9]+\.[0-9]+)\s*V?\b', block)
                    if m:
                        val = float(m.group(1))
                        if 0.1 < val < 5.0:
                            return val
    return None


def load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        return json.loads(CHECKPOINT.read_text())
    return {}


def save_checkpoint(cp: dict) -> None:
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT.write_text(json.dumps(cp, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="max parts to process (0=all)")
    args = ap.parse_args()

    lines = DATA.read_text().splitlines()
    cp = load_checkpoint()

    filled = 0
    out = []
    processed = 0
    for line in lines:
        s = line.strip()
        if not s:
            out.append(line)
            continue

        rec = json.loads(s)
        d = rec.get("semiconductor", {}).get("diode", {})
        mi = d.get("manufacturerInfo", {})
        di = mi.get("datasheetInfo", {})
        el = di.get("electrical", {})

        if "forwardVoltage" in el:
            out.append(line)
            continue

        pn = mi.get("reference") or (di.get("part") or {}).get("partNumber", "")
        cp_entry = cp.get(pn, {})
        status = cp_entry.get("status", "pending")

        if status == "done":
            # Already resolved — apply stored value if present
            vf = cp_entry.get("forwardVoltage")
            if vf is not None:
                el["forwardVoltage"] = vf
                d["manufacturerInfo"]["datasheetInfo"]["electrical"] = el
                out.append(json.dumps(rec))
                filled += 1
            else:
                out.append(line)
            continue

        if status == "notfound":
            out.append(line)
            continue

        if args.limit and processed >= args.limit:
            out.append(line)
            continue

        pdf_url = mi.get("datasheetUrl", "")
        if not pdf_url or not pdf_url.endswith(".pdf"):
            cp[pn] = {"status": "notfound", "reason": "no_pdf_url"}
            out.append(line)
            continue

        processed += 1
        print(f"  [{processed}] {pn}: fetching {pdf_url}", flush=True)
        try:
            resp = httpx.get(pdf_url, timeout=30, follow_redirects=True)
            if resp.status_code != 200:
                cp[pn] = {"status": "notfound", "reason": f"HTTP {resp.status_code}"}
                out.append(line)
                continue

            vf = extract_vf_from_pdf(resp.content)
            if vf is None:
                cp[pn] = {"status": "notfound", "reason": "vf_not_in_pdf"}
                print(f"    VF not found in PDF")
                out.append(line)
                continue

            cp[pn] = {"status": "done", "forwardVoltage": vf}
            el["forwardVoltage"] = vf
            d["manufacturerInfo"]["datasheetInfo"]["electrical"] = el
            out.append(json.dumps(rec))
            filled += 1
            print(f"    -> forwardVoltage={vf}")
            time.sleep(0.5)

        except Exception as e:
            cp[pn] = {"status": "error", "reason": str(e)[:200]}
            print(f"    ERROR: {e}")
            out.append(line)

    save_checkpoint(cp)
    print(f"\nFilled forwardVoltage for {filled} diodes")

    if not args.dry_run and filled:
        tmp = DATA.with_suffix(".ndjson.tmp")
        tmp.write_text("\n".join(out) + "\n")
        os.replace(tmp, DATA)
        print("Written.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
