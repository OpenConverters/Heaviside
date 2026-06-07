#!/usr/bin/env python3
"""Populate TAS controllers.ndjson with feedbackReferenceVoltage (Vref/Vfb).

Downloads each controller's datasheet and LLM-extracts the feedback
reference voltage so the assembler can size the output feedback divider.
No guessing — if a datasheet can't be fetched/parsed, that controller is
left without Vref (the assembler then skips its divider with a diagnostic).

Usage:
    python scripts/populate_controller_vref.py LTC7891 LTC3892 [...]
    python scripts/populate_controller_vref.py            # default set
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MOONSHOT_API_KEY", "sk-viKudfa58QW8GjUm8aYxkfv5hmz0i5Y3HRdMKKpphPUupleQ")

import logging

logging.basicConfig(level=logging.WARNING)

TAS_PATH = Path(__file__).resolve().parents[1] / "TAS" / "data" / "controllers.ndjson"
DEFAULT_MPNS = ["LTC7891", "LTC3892", "LM5146", "ISL8117", "LM5148-Q1"]


def _download_datasheet_text(mpn: str, url: str) -> str:
    import httpx

    from heaviside.pipeline.pdf_extract import extract_pdf_text

    urls = [url] if url else []
    # DuckDuckGo fallback (ADI/some vendors 403 direct fetches)
    try:
        import re
        import urllib.parse as up

        ddg = httpx.get(
            "https://html.duckduckgo.com/html/",
            params={"q": f"{mpn} datasheet pdf"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
            follow_redirects=True,
        )
        if ddg.status_code == 200:
            for u in re.findall(r"uddg=([^&\"]+)", ddg.text):
                dec = up.unquote(u)
                if dec.endswith(".pdf") and dec not in urls:
                    urls.append(dec)
    except Exception:
        pass

    for u in urls:
        try:
            r = httpx.get(
                u, timeout=30.0, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}
            )
            ct = r.headers.get("content-type", "")
            if r.status_code == 200 and ("pdf" in ct or len(r.content) > 50_000):
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                    f.write(r.content)
                    tmp = f.name
                try:
                    txt = extract_pdf_text(Path(tmp))
                finally:
                    os.unlink(tmp)
                if txt:
                    print(f"  downloaded {len(txt)} chars from {u[:55]}", flush=True)
                    return txt
        except Exception as e:
            print(f"  fetch failed {u[:55]}: {str(e)[:50]}", flush=True)
    return ""


def _extract_vref(mpn: str, text: str) -> float | None:
    from heaviside.agents.llm_call import LLMCallError, call_agent_json

    # Send a bounded slice (Vref lives in the Electrical Characteristics table)
    snippet = text[:40000]
    try:
        data = call_agent_json(
            "competitor",
            "Extract ONLY the feedback regulated/reference voltage (Vfb or "
            "Vref, the voltage the error amp regulates the FB pin to) from "
            f"this {mpn} controller datasheet. Reply JSON: "
            '{"feedbackReferenceVoltage": <volts as float>} or '
            '{"feedbackReferenceVoltage": null} if not found.\n\n' + snippet,
            max_tokens=2048,
            max_retries=2,
        )
        v = data.get("feedbackReferenceVoltage")
        if isinstance(v, (int, float)) and 0 < v < 5:
            return float(v)
    except LLMCallError as e:
        print(f"  LLM extract failed: {str(e)[:60]}", flush=True)
    return None


def main():
    mpns = sys.argv[1:] or DEFAULT_MPNS
    rows = [json.loads(l) for l in TAS_PATH.read_text().splitlines() if l.strip()]
    by_mpn = {r.get("name"): r for r in rows}
    updated = 0
    for mpn in mpns:
        r = by_mpn.get(mpn)
        if r is None:
            print(f"{mpn}: not in TAS", flush=True)
            continue
        if isinstance(r.get("feedbackReferenceVoltage"), (int, float)):
            print(f"{mpn}: already has Vref={r['feedbackReferenceVoltage']}", flush=True)
            continue
        print(f"{mpn}: fetching datasheet...", flush=True)
        txt = _download_datasheet_text(mpn, r.get("datasheetUrl", ""))
        if not txt:
            print(f"{mpn}: NO datasheet — skipped (no guess)", flush=True)
            continue
        vref = _extract_vref(mpn, txt)
        if vref is None:
            print(f"{mpn}: Vref not found in datasheet — skipped", flush=True)
            continue
        r["feedbackReferenceVoltage"] = vref
        updated += 1
        print(f"{mpn}: Vref = {vref} V ✓", flush=True)

    if updated:
        TAS_PATH.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        print(f"\nWrote {updated} Vref values to {TAS_PATH}", flush=True)
    else:
        print("\nNo Vref values written.", flush=True)


if __name__ == "__main__":
    main()
