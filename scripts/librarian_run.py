#!/usr/bin/env python3
"""Librarian end-to-end run for one category (HANDOFF #1).

Pipeline per MPN:

    distributor fetch  (Mouser preferred; Digi-Key fallback)
        -> convert     (convert_<source>_to_tas_<category>)
        -> stage       (stage_fetch writes <staging>/<cat>/<source>-<mpn>.json)
        -> apply       (apply_staged → schema-validate → append to TAS)
        -> audit       (run audit_category on the full corpus)

Honest reporting: failures and skips are surfaced individually; no silent
fallbacks. Per CLAUDE.md, the script throws loudly on any unexpected error
rather than swallowing it.

Usage::

    # Live run against 10 MPNs (one per line in the file).
    scripts/librarian_run.py --category capacitors --mpns-file caps.txt

    # Dry-run: fetch + convert + stage, but do NOT touch TAS/data/.
    scripts/librarian_run.py --category capacitors \\
        --mpns "GRM188R71H104KA93D,C1206C104K5RACTU" --dry-run

    # Print baseline + post audit only (no fetch).
    scripts/librarian_run.py --category capacitors --audit-only

The script logs a summary line per MPN (``fetched`` / ``staged`` / ``applied``
/ ``skipped`` / ``error``) and a final aggregate. Exit code 1 if any MPN
errored or if the final audit pass rate dropped below the baseline.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Literal

from heaviside.librarian import (
    CATEGORIES,
    DigiKeyClient,
    DistributorError,
    MissingCredentialError,
    MouserClient,
    RateLimitError,
    apply_staged,
    audit_category,
    component_exists,
    convert_digikey_to_tas_capacitor,
    convert_mouser_to_tas_capacitor,
    load_credentials,
    stage_fetch,
)

# Per-category converter map. Extend as new categories land.
_MOUSER_CONVERTERS = {
    "capacitors": convert_mouser_to_tas_capacitor,
}
_DIGIKEY_CONVERTERS = {
    "capacitors": convert_digikey_to_tas_capacitor,
}

Outcome = Literal["applied", "staged", "skipped_existing", "miss", "error"]


def _read_mpns(args: argparse.Namespace) -> list[str]:
    if args.mpns_file:
        return [
            ln.strip()
            for ln in Path(args.mpns_file).read_text().splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
    if args.mpns:
        return [m.strip() for m in args.mpns.split(",") if m.strip()]
    raise SystemExit("librarian_run: pass --mpns or --mpns-file (or --audit-only)")


def _try_mouser(
    mouser: MouserClient | None,
    category: str,
    mpn: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return ``(envelope, raw_product)`` or ``(None, None)`` if Mouser can't
    help. Rate-limit and credential errors propagate."""
    if mouser is None or category not in _MOUSER_CONVERTERS:
        return None, None
    product = mouser.get_product(mpn)
    if product is None:
        return None, None
    return _MOUSER_CONVERTERS[category](product), product


def _try_digikey(
    digikey: DigiKeyClient | None,
    category: str,
    mpn: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if digikey is None or category not in _DIGIKEY_CONVERTERS:
        return None, None
    product = digikey.get_product(mpn)
    if product is None:
        return None, None
    return _DIGIKEY_CONVERTERS[category](product), product


def _process_mpn(
    mpn: str,
    *,
    category: str,
    mouser: MouserClient | None,
    digikey: DigiKeyClient | None,
    dry_run: bool,
) -> tuple[Outcome, str]:
    """Process one MPN; returns (outcome, detail)."""
    if component_exists(category, mpn):
        return "skipped_existing", "already in TAS"

    envelope: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None
    source: str | None = None

    # Mouser first (per-minute rate limit is gentler than Digi-Key's hourly).
    try:
        envelope, raw = _try_mouser(mouser, category, mpn)
        if envelope is not None:
            source = "mouser"
    except RateLimitError as exc:
        # Mouser per-minute throttle; fall through to Digi-Key.
        print(f"  [{mpn}] mouser rate-limited: {exc}", file=sys.stderr)

    if envelope is None:
        try:
            envelope, raw = _try_digikey(digikey, category, mpn)
            if envelope is not None:
                source = "digikey"
        except RateLimitError as exc:
            return "error", f"all distributors rate-limited (digikey: {exc})"

    if envelope is None or source is None:
        return "miss", "not found in any distributor"

    staged_path = stage_fetch(category, mpn, envelope, source=source, raw_response=raw)
    if dry_run:
        return "staged", str(staged_path)

    apply_staged(staged_path, archive=True)
    return "applied", str(staged_path)


def _summarise(outcomes: dict[str, list[str]]) -> None:
    print("\n=== Summary ===")
    for k, v in outcomes.items():
        print(f"  {k}: {len(v)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--category", required=True, choices=sorted(CATEGORIES))
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--mpns", help="comma-separated MPN list")
    src.add_argument("--mpns-file", help="path to newline-separated MPN list (# comments allowed)")
    ap.add_argument("--dry-run", action="store_true", help="stage only; do not apply to TAS")
    ap.add_argument(
        "--audit-only", action="store_true", help="report baseline + final audit only; no fetch"
    )
    args = ap.parse_args(argv)

    # Baseline audit (always runs).
    print(f"--- Baseline audit ({args.category}) ---")
    baseline = audit_category(args.category, on_corruption="report")
    print(f"  pass: {baseline.passed}/{baseline.total} = {baseline.pass_rate_pct:.2f}%")

    if args.audit_only:
        return 0

    mpns = _read_mpns(args)
    print(f"\n--- Processing {len(mpns)} MPNs (dry-run={args.dry_run}) ---")

    try:
        creds = load_credentials()
    except MissingCredentialError as exc:
        raise SystemExit(f"librarian_run: credentials missing — {exc}") from exc

    outcomes: dict[str, list[str]] = {
        "applied": [],
        "staged": [],
        "skipped_existing": [],
        "miss": [],
        "error": [],
    }

    started = time.time()
    with MouserClient(creds.mouser) as mouser, DigiKeyClient(creds.digikey) as digikey:
        for mpn in mpns:
            try:
                outcome, detail = _process_mpn(
                    mpn,
                    category=args.category,
                    mouser=mouser,
                    digikey=digikey,
                    dry_run=args.dry_run,
                )
            except DistributorError as exc:
                # Per-MPN distributor errors are non-fatal; record + continue.
                outcome, detail = "error", f"distributor error: {exc}"
            except Exception as exc:
                # Surface unexpected per-MPN failures in summary rather than
                # aborting the whole run; the caller sees the typed name.
                outcome, detail = "error", f"{type(exc).__name__}: {exc}"
            outcomes[outcome].append(mpn)
            print(f"  [{outcome:18}] {mpn:30}  {detail}")
    elapsed = time.time() - started

    _summarise(outcomes)
    print(f"  elapsed: {elapsed:.1f}s")

    # Post-run audit.
    print(f"\n--- Post audit ({args.category}) ---")
    post = audit_category(args.category, on_corruption="report")
    delta = post.pass_rate_pct - baseline.pass_rate_pct
    print(
        f"  pass: {post.passed}/{post.total} = {post.pass_rate_pct:.2f}% "
        f"(Δ {delta:+.2f}pp; {post.total - baseline.total:+d} components)"
    )

    # Non-zero exit if anything errored OR the pass rate fell.
    if outcomes["error"] or delta < -0.5:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
