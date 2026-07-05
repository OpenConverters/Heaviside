#!/usr/bin/env python3
"""Refresh Würth magnetic electrical data (Isat/Irms/DCR) from the AUTHORITATIVE
datasheet PDFs, replacing the stale, definition-less values imported from the old
vendor .mdb export.

Root cause (see the provenance investigation): the 7,430 Würth magnetics came
from a Würth Access-DB export that stored one ``saturationCurrentPeak`` with no
inductance-drop definition, and the numbers drifted from the current datasheets
(e.g. 74438356015: DB Isat 6.3A/Irms 5.8A/DCR-max 22mΩ vs datasheet 4.8A@10% /
10.2A@30%, IRP,40K 8.6A, 16/19mΩ). The datasheet is authoritative and WE renders
it as vector text, so we parse it directly (heaviside.librarian.datasheet.
magnetics_we) and store the conservative 10 %-drop Isat for the saturation gate.

Governance: TAS/CLAUDE.md discourages blind bulk rewrites. This tool therefore
defaults to a bounded --limit sample and a dry run; only records whose datasheet
values genuinely DIFFER are touched, and each change is printed. Use --all
--apply for the full catalogue (a large, deliberate operation: ~7k PDF fetches).

Usage:
    python3 scripts/refetch_wurth_magnetics_from_datasheet.py --mpn 74438356015
    python3 scripts/refetch_wurth_magnetics_from_datasheet.py --limit 20
    python3 scripts/refetch_wurth_magnetics_from_datasheet.py --limit 20 --apply
    python3 scripts/refetch_wurth_magnetics_from_datasheet.py --all --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from heaviside.catalogue.selector import _tas_data_dir
from heaviside.librarian.datasheet.cache import PdfCache
from heaviside.librarian.datasheet.magnetics_we import extract_we_magnetic_pdf
from heaviside.librarian.datasheet.magnetics_we_url import (
    resolve_we_datasheet_pdf,
    we_datasheet_candidate_urls,
)

_REL = 0.02  # 2% tolerance before a value is considered "drifted"


def _is_wurth(env: dict) -> bool:
    name = str(env.get("magnetic", {}).get("manufacturerInfo", {}).get("name") or "").lower()
    return "rth" in name  # matches Würth / Wurth / Wuerth


def _elec_row(env: dict) -> dict | None:
    elec = env.get("magnetic", {}).get("manufacturerInfo", {}).get("datasheetInfo", {}).get("electrical")
    if isinstance(elec, list) and elec and isinstance(elec[0], dict):
        return elec[0]
    return elec if isinstance(elec, dict) else None


def _differs(a, b) -> bool:
    if a is None or b is None:
        return a is not b
    try:
        return abs(float(a) - float(b)) > _REL * max(abs(float(a)), abs(float(b)), 1e-12)
    except (TypeError, ValueError):
        return True


def _corrections(row: dict, ds: dict) -> dict:
    """Return the electrical fields that should change, given datasheet values.
    Saturation current uses the conservative 10 %-drop value (falls back to 30 %
    only if 10 % is absent). Only fields the datasheet actually provided."""
    out: dict = {}
    isat = ds.get("isat_10pct") or ds.get("isat_20pct") or ds.get("isat_30pct")
    if isat is not None and _differs(row.get("saturationCurrentPeak"), isat):
        out["saturationCurrentPeak"] = isat
    # Full I_sat table WITH its inductance-drop basis (MAS saturationCurrents),
    # so cross-manufacturer comparison can normalize to a common %-drop instead of
    # comparing a WE 10 %-drop figure against a competitor's 20 %-drop figure. The
    # datasheet parser exposes the per-criterion values; store every one it found.
    # The FIELD is manufacturer-agnostic — this WE parser is just its first filler.
    sat_points = [
        {"percentInductanceDrop": pct, "current": ds[key]}
        for pct, key in ((10, "isat_10pct"), (20, "isat_20pct"), (30, "isat_30pct"))
        if isinstance(ds.get(key), (int, float)) and ds[key] > 0
    ]
    if sat_points and row.get("saturationCurrents") != sat_points:
        out["saturationCurrents"] = sat_points
    # Use the STANDARD rated current (IR,40K), not the best-case IRP,40K, when the
    # datasheet distinguishes them (parse_we_magnetic_text.rated_current does).
    rated = ds.get("rated_current")
    if rated is None:
        rated = ds.get("ir_40k") or ds.get("irp_40k")
    if rated is not None:
        cur = row.get("ratedCurrents")
        cur0 = cur[0] if isinstance(cur, list) and cur else None
        if _differs(cur0, rated):
            out["ratedCurrents"] = [rated]
    dcr_typ, dcr_max = ds.get("rdc_typ"), ds.get("rdc_max")
    if dcr_typ is not None or dcr_max is not None:
        existing = row.get("dcResistance") if isinstance(row.get("dcResistance"), dict) else {}
        new = dict(existing)
        if dcr_typ is not None:
            new["nominal"] = dcr_typ
        if dcr_max is not None:
            new["maximum"] = dcr_max
        if new != existing:
            out["dcResistance"] = new
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--all", action="store_true", help="process the whole catalogue")
    ap.add_argument("--mpn", default=None, help="a single MPN")
    ap.add_argument(
        "--delay",
        type=float,
        default=0.1,
        help="politeness delay (s) after each *network* fetch (0 to disable)",
    )
    ap.add_argument(
        "--progress-every",
        type=int,
        default=200,
        help="print a running tally every N examined parts",
    )
    args = ap.parse_args()

    path = _tas_data_dir() / "magnetics.ndjson"
    cache = PdfCache()
    tmp_fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    seen = fetched = drifted = corrected = 0
    unfetchable: list[str] = []
    parse_failed: list[str] = []
    limit = None if (args.all or args.mpn) else args.limit

    with os.fdopen(tmp_fd, "w") as out, path.open() as fh:
        for line in fh:
            s = line.strip()
            if not s:
                continue
            env = json.loads(s)
            mi = env.get("magnetic", {}).get("manufacturerInfo", {})
            ref = mi.get("reference")
            url = str(mi.get("datasheetUrl") or "")
            # Every Würth magnetic is a candidate — the datasheet URL is resolved
            # (not merely trusted) so REDEXPERT-page / katalog / empty-URL parts
            # are corrected too, not just the ones that happen to store a .pdf.
            take = _is_wurth(env)
            if args.mpn:
                take = take and ref == args.mpn
            if not take or (limit is not None and seen >= limit):
                out.write(line)
                continue
            seen += 1
            row = _elec_row(env)
            if row is None:
                out.write(line)
                continue

            candidates = we_datasheet_candidate_urls(ref, url)
            pre_cached = {u for u in candidates if cache.is_cached(u)}
            try:
                resolved = resolve_we_datasheet_pdf(cache, ref, url)
            except Exception as e:  # transport-level (DNS/timeout) — report, keep
                unfetchable.append(f"{ref} (fetch error: {e})")
                out.write(line)
                continue
            if resolved is None:
                # No candidate URL served a real PDF for this exact article. Never
                # transform the MPN to dodge the 404 (that would fetch a different
                # part's datasheet) — skip and report.
                unfetchable.append(f"{ref} (no PDF at any of: {', '.join(candidates)})")
                out.write(line)
                continue
            resolved_url, pdf = resolved
            if args.delay and resolved_url not in pre_cached:
                time.sleep(args.delay)
            try:
                ds = extract_we_magnetic_pdf(pdf)
                fetched += 1
            except Exception as e:
                parse_failed.append(f"{ref} ({e})")
                print(f"  {ref}: parse failed ({e})")
                out.write(line)
                continue

            corr = _corrections(row, ds)
            if corr:
                drifted += 1
                print(f"  {ref}: DRIFT {json.dumps(corr)}")
                if args.apply:
                    row.update(corr)
                    corrected += 1
                    out.write(json.dumps(env, ensure_ascii=False) + "\n")
                    if args.progress_every and seen % args.progress_every == 0:
                        print(
                            f"  … {seen} examined | {fetched} parsed | "
                            f"{drifted} drifted | {len(unfetchable)} unfetchable",
                            flush=True,
                        )
                    continue
            out.write(line)
            if args.progress_every and seen % args.progress_every == 0:
                print(
                    f"  … {seen} examined | {fetched} parsed | "
                    f"{drifted} drifted | {len(unfetchable)} unfetchable",
                    flush=True,
                )

    if args.apply and corrected:
        os.replace(tmp, path)
        print(f"APPLIED — {corrected} record(s) corrected in {path}")
    else:
        os.unlink(tmp)

    print(
        f"\nWürth magnetics examined: {seen} | datasheets parsed: {fetched} | "
        f"drifted: {drifted} | corrected: {corrected} | "
        f"un-fetchable: {len(unfetchable)} | parse-failed: {len(parse_failed)}"
        + ("" if args.apply else "  (dry run — pass --apply)")
    )
    if unfetchable:
        print(f"\nUn-fetchable ({len(unfetchable)}) — no datasheet PDF for the exact "
              "article (skipped, NOT guessed):")
        for u in unfetchable:
            print(f"  - {u}")
    if parse_failed:
        print(f"\nParse-failed ({len(parse_failed)}) — PDF fetched but no electrical "
              "fields extracted:")
        for u in parse_failed:
            print(f"  - {u}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
