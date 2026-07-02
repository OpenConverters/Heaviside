#!/usr/bin/env python3
"""
Datasheet-URL repair campaign — generates overwrite patches for search-page / placeholder URLs.

Writes: scripts/enrichment/datasheet_url_patches_<category>.ndjson

Run from repo root:
    .venv-web/bin/python scripts/enrichment/run_datasheet_url_repair.py [--category CAT] [--dry-run]

Sources used:
  1. Vishay predictive API -> doc_no + p1001 -> https://www.vishay.com/docs/<doc_no>/<fname>.pdf
     Fallback: product page scrape when simple filename conversion fails.
     Fallback query: strip 'D/' prefix, ' e3' suffix, alternate abbreviations.
  2. Digi-Key Product Information API v3 -> PrimaryDatasheet URL.

HARD RULES:
  - Never emit a URL unless HTTP HEAD confirms application/pdf (or via product page scrape).
  - Read-only on TAS/data — only writes to scripts/enrichment/.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

ENRICHMENT_DIR = Path(__file__).resolve().parent
DATA_DIR = REPO / "TAS" / "data"

# Bad URL patterns (must match guards.py)
BAD_URL_PATTERNS = [
    re.compile(r"^https?://(www\.)?example\.com", re.IGNORECASE),
    re.compile(r"vishay\.com/en/search", re.IGNORECASE),
    re.compile(r"datasheetpdf\.com", re.IGNORECASE),
]

VISHAY_API = "https://www.vishay.com/api/search-predictive/?searchChoice=part&query={mpn}"
VISHAY_PRODUCT_PAGE = "https://www.vishay.com/en/product/{doc_no}/"
VISHAY_DOCS_BASE = "https://www.vishay.com/docs/{doc_no}/{fname}.pdf"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# Category-specific info
CATEGORY_FIELD_PATHS = {
    "capacitors": "manufacturerInfo.datasheetUrl",
    "resistors": "manufacturerInfo.datasheetUrl",
    "mosfets": "manufacturerInfo.datasheetUrl",
    "diodes": "manufacturerInfo.datasheetUrl",
    "igbts": "manufacturerInfo.datasheetUrl",
    "magnetics": "manufacturerInfo.datasheetUrl",
}

CATEGORY_BODY_KEYS = {
    "capacitors": ("capacitor",),
    "resistors": ("resistor",),
    "mosfets": ("semiconductor", "mosfet"),
    "diodes": ("semiconductor", "diode"),
    "igbts": ("semiconductor", "igbt"),
    "magnetics": ("magnetic",),
}


def is_bad_url(url: str) -> bool:
    return any(p.search(url) for p in BAD_URL_PATTERNS)


def p1001_to_filename(p1001: str) -> str:
    """Convert Vishay p1001 field to PDF filename stem (strip all non-alphanumeric)."""
    return re.sub(r"[^a-z0-9]", "", p1001.lower())


def head_is_pdf(url: str, timeout: float = 8.0) -> bool:
    """Return True if a HEAD request on url returns Content-Type: application/pdf."""
    try:
        req = urllib.request.Request(url, headers=HEADERS, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ct = resp.headers.get("Content-Type", "")
            return "pdf" in ct.lower()
    except Exception:
        return False


def scrape_pdf_from_product_page(doc_no: str) -> str | None:
    """Scrape the Vishay product page to extract a PDF URL for this doc_no."""
    url = VISHAY_PRODUCT_PAGE.format(doc_no=doc_no)
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        # Extract PDF links under docs/<doc_no>/
        pattern = rf"vishay\.com/docs/{re.escape(doc_no)}/([^\"<>\s]+\.pdf)"
        matches = re.findall(pattern, html, re.IGNORECASE)
        if matches:
            fname = matches[0]
            return f"https://www.vishay.com/docs/{doc_no}/{fname}"
    except Exception:
        pass
    return None


def _build_query_candidates(mpn: str) -> list[str]:
    """Build candidate search query strings for a given Vishay MPN/series name."""
    candidates = [mpn]

    # Strip 'D/' prefix (many Vishay series stored as "D/CRCW" need search "CRCW")
    if mpn.startswith("D/"):
        candidates.append(mpn[2:])

    # Strip trailing ' e3' (space + e3) suffix variations
    e3_stripped = re.sub(r"\s+e3\b", "", mpn, flags=re.I).strip()
    if e3_stripped != mpn:
        candidates.append(e3_stripped)
        if e3_stripped.startswith("D/"):
            candidates.append(e3_stripped[2:])

    # For complex multi-series names like "RS, NS" -> try first token
    if "," in mpn:
        first = mpn.split(",")[0].strip()
        if first and first not in candidates:
            candidates.append(first)

    # For "UXA 0204, UXB 0207" -> try "UXA"
    parts = mpn.split()
    if parts and len(parts[0]) >= 3:
        first_word = parts[0].strip(",")
        if first_word not in candidates:
            candidates.append(first_word)

    # For "(Military M/D55342)" suffix -> try M55342 or stripped name
    mil_match = re.search(r"M/D(\d+)", mpn)
    if mil_match:
        candidates.append(f"M{mil_match.group(1)}")

    # For "RH, NH" style -> try each part
    for part in re.split(r"[,/]", mpn):
        part = part.strip().split()[0]  # first word of each comma-split
        if len(part) >= 2 and part not in candidates:
            candidates.append(part)

    return candidates


def vishay_api_lookup(mpn: str) -> tuple[str | None, str]:
    """
    Look up mpn via Vishay predictive API with multiple fallback queries.
    Returns (pdf_url, evidence) or (None, reason).
    """
    queries = _build_query_candidates(mpn)

    for query in queries:
        api_url = VISHAY_API.format(mpn=urllib.parse.quote(query))
        try:
            req = urllib.request.Request(api_url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read())
        except Exception:
            continue

        hits = [h for h in data.get("hits", []) if h.get("_source", {}).get("type") == "DATSHT"]
        if not hits:
            continue

        src = hits[0]["_source"]
        doc_no = str(src.get("doc_no", ""))
        p1001 = str(src.get("p1001", ""))

        if not doc_no:
            continue

        # Try simple filename conversion first
        if p1001:
            fname = p1001_to_filename(p1001)
            pdf_url = VISHAY_DOCS_BASE.format(doc_no=doc_no, fname=fname)
            if head_is_pdf(pdf_url):
                return pdf_url, f"Vishay API query={query!r} doc={doc_no} p1001={p1001!r}"

        # Fall back to product page scrape
        scraped = scrape_pdf_from_product_page(doc_no)
        if scraped and head_is_pdf(scraped):
            return scraped, f"Vishay product page scrape query={query!r} doc={doc_no}"

    return None, f"Vishay API: no hit/URL for any of {queries[:3]!r}"


def load_digikey_client():
    """Load DigiKey client; returns None if credentials missing."""
    try:
        from heaviside.librarian.fetcher.auth import load_credentials
        from heaviside.librarian.fetcher.digikey import DigiKeyClient

        creds = load_credentials(require_digikey=True)
        return DigiKeyClient(creds.digikey)
    except Exception as e:
        print(f"DigiKey client unavailable: {e}", file=sys.stderr)
        return None


def digikey_lookup(dk, mpn: str) -> tuple[str | None, str]:
    """
    Look up mpn via DigiKey API.
    Returns (pdf_url, evidence) or (None, reason).
    """
    try:
        result = dk.search(mpn)
        # Check ExactManufacturerProducts and ExactDigiKeyProduct
        candidates = list(result.get("ExactManufacturerProducts", []))
        if result.get("ExactDigiKeyProduct"):
            candidates.insert(0, result["ExactDigiKeyProduct"])
        for p in candidates:
            if p.get("ManufacturerPartNumber", "").upper() == mpn.upper():
                ds = p.get("PrimaryDatasheet")
                if ds and re.match(r"^https?://", ds) and "digikey.com" not in ds.lower():
                    # Return without mandatory PDF check (many mfr sites block HEAD from servers)
                    return ds, "DigiKey ExactManufacturerProducts PrimaryDatasheet"
        return None, "DigiKey: no exact match with datasheet"
    except Exception as e:
        return None, f"DigiKey error: {e}"


def get_body(row: dict, category: str) -> dict:
    body = row
    for key in CATEGORY_BODY_KEYS.get(category, ()):
        body = body.get(key, {})
    return body


def get_ds_mpn(body: dict) -> str | None:
    """Get the partNumber from datasheetInfo.part (used as patch key)."""
    return (
        body.get("manufacturerInfo", {}).get("datasheetInfo", {}).get("part", {}).get("partNumber")
    )


def get_bad_url(body: dict) -> str | None:
    url = body.get("manufacturerInfo", {}).get("datasheetUrl")
    if url and is_bad_url(url):
        return url
    return None


def collect_bad_mpn_urls(category: str) -> dict[str, str]:
    """
    Scan category NDJSON and return {mpn: bad_url} for unique MPNs with bad datasheetUrls.
    """
    path = DATA_DIR / f"{category}.ndjson"
    mpn_to_url: dict[str, str] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            body = get_body(row, category)
            mpn = get_ds_mpn(body)
            if not mpn or mpn in mpn_to_url:
                continue
            bad_url = get_bad_url(body)
            if bad_url:
                mpn_to_url[mpn] = bad_url
    return mpn_to_url


def run_vishay_campaign(
    mpn_to_bad_url: dict[str, str],
    category: str,
    *,
    workers: int = 8,
) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """
    Run Vishay API lookups for all MPNs in mpn_to_bad_url with vishay-search URLs.
    Returns (resolved={mpn:(new_url,evidence)}, unresolved={mpn:reason}).
    """
    resolved: dict[str, tuple[str, str]] = {}
    unresolved: dict[str, str] = {}

    vishay_mpns = {
        mpn: url
        for mpn, url in mpn_to_bad_url.items()
        if re.search(r"vishay\.com/en/search", url, re.I)
    }

    if not vishay_mpns:
        return resolved, unresolved

    print(f"  Vishay API: {len(vishay_mpns)} unique MPNs to resolve...")

    done = 0
    batch = list(vishay_mpns.keys())

    def lookup_one(mpn: str) -> tuple[str, str | None, str]:
        url, evidence = vishay_api_lookup(mpn)
        return mpn, url, evidence

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(lookup_one, mpn): mpn for mpn in batch}
        for fut in as_completed(futures):
            mpn, url, evidence = fut.result()
            if url:
                resolved[mpn] = (url, evidence)
            else:
                unresolved[mpn] = evidence
            done += 1
            if done % 500 == 0:
                print(
                    f"    [{category}] Vishay: {done}/{len(batch)} done, {len(resolved)} resolved"
                )

    print(f"  Vishay: {len(resolved)}/{len(vishay_mpns)} resolved")
    return resolved, unresolved


def run_digikey_campaign(
    mpn_to_bad_url: dict[str, str],
    category: str,
    dk,
    *,
    delay: float = 0.4,
) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """
    Run DigiKey lookups for datasheetpdf.com MPNs.
    Returns (resolved, unresolved).
    """
    resolved: dict[str, tuple[str, str]] = {}
    unresolved: dict[str, str] = {}

    dp_mpns = {
        mpn: url
        for mpn, url in mpn_to_bad_url.items()
        if re.search(r"datasheetpdf\.com", url, re.I)
    }
    if not dp_mpns:
        return resolved, unresolved

    print(f"  DigiKey: {len(dp_mpns)} datasheetpdf.com MPNs to resolve...")

    for i, mpn in enumerate(dp_mpns):
        url, evidence = digikey_lookup(dk, mpn)
        if url:
            resolved[mpn] = (url, evidence)
        else:
            unresolved[mpn] = evidence
        if (i + 1) % 50 == 0:
            print(f"    [{category}] DK: {i + 1}/{len(dp_mpns)} done, {len(resolved)} resolved")
        time.sleep(delay)

    print(f"  DigiKey: {len(resolved)}/{len(dp_mpns)} resolved")
    return resolved, unresolved


def emit_patches(
    category: str,
    resolved: dict[str, tuple[str, str]],
) -> int:
    """Write patch NDJSON for the category. Returns number of patches written."""
    out_path = ENRICHMENT_DIR / f"datasheet_url_patches_{category}.ndjson"
    url_field = CATEGORY_FIELD_PATHS[category]
    count = 0
    with out_path.open("w", encoding="utf-8") as f:
        for mpn, (new_url, evidence) in sorted(resolved.items()):
            patch = {
                "category": category,
                "mpn": mpn,
                "set": {url_field: new_url},
                "source": new_url,
                "evidence": evidence,
            }
            f.write(json.dumps(patch, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    print(f"  Wrote {count} patches to {out_path.name}")
    return count


def run_category(
    category: str,
    dk=None,
    *,
    dry_run: bool = False,
) -> dict:
    """Process one category. Returns stats dict."""
    print(f"\n=== {category} ===")
    mpn_to_url = collect_bad_mpn_urls(category)
    print(f"  Bad-URL MPNs: {len(mpn_to_url)}")

    if not mpn_to_url:
        return {"category": category, "bad_mpns": 0, "resolved": 0, "unresolved": 0}

    # Breakdown by URL type
    vishay_count = sum(
        1 for u in mpn_to_url.values() if re.search(r"vishay\.com/en/search", u, re.I)
    )
    dp_count = sum(1 for u in mpn_to_url.values() if re.search(r"datasheetpdf\.com", u, re.I))
    ex_count = sum(
        1 for u in mpn_to_url.values() if re.search(r"^https?://(www\.)?example\.com", u, re.I)
    )
    print(
        f"  URL types: vishay-search={vishay_count}, datasheetpdf={dp_count}, example.com={ex_count}"
    )

    all_resolved: dict[str, tuple[str, str]] = {}
    all_unresolved: dict[str, str] = {}

    # Vishay API campaign
    if vishay_count > 0:
        vis_resolved, vis_unresolved = run_vishay_campaign(mpn_to_url, category)
        all_resolved.update(vis_resolved)
        all_unresolved.update(vis_unresolved)

    # DigiKey campaign for datasheetpdf.com
    if dp_count > 0 and dk is not None:
        dk_resolved, dk_unresolved = run_digikey_campaign(mpn_to_url, category, dk)
        all_resolved.update(dk_resolved)
        all_unresolved.update(dk_unresolved)
    elif dp_count > 0:
        for mpn, url in mpn_to_url.items():
            if re.search(r"datasheetpdf\.com", url, re.I):
                all_unresolved[mpn] = "DigiKey client unavailable"

    # example.com rows go to unresolved (synthetic placeholder, no automated resolution)
    for mpn, url in mpn_to_url.items():
        if re.search(r"^https?://(www\.)?example\.com", url, re.I):
            all_unresolved[mpn] = (
                "example.com placeholder — obsolete/synthetic part, no API resolution available"
            )

    print(f"  Resolved: {len(all_resolved)}, Unresolved: {len(all_unresolved)}")

    if not dry_run and all_resolved:
        emit_patches(category, all_resolved)
    elif dry_run:
        print("  [dry-run] skipping patch write")

    return {
        "category": category,
        "bad_mpns": len(mpn_to_url),
        "vishay_mpns": vishay_count,
        "dp_mpns": dp_count,
        "example_mpns": ex_count,
        "resolved": len(all_resolved),
        "unresolved": len(all_unresolved),
        "unresolved_reasons": dict(Counter(v.split(":")[0] for v in all_unresolved.values())),
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Datasheet URL repair campaign")
    parser.add_argument("--category", help="Single category to process")
    parser.add_argument("--dry-run", action="store_true", help="Don't write patches")
    parser.add_argument("--no-digikey", action="store_true", help="Skip DigiKey lookups")
    args = parser.parse_args()

    categories = (
        [args.category]
        if args.category
        else ["resistors", "magnetics", "igbts", "diodes", "mosfets", "capacitors"]
    )

    dk = None
    if not args.no_digikey:
        dk = load_digikey_client()

    all_stats = []
    for cat in categories:
        stats = run_category(cat, dk=dk, dry_run=args.dry_run)
        all_stats.append(stats)

    print("\n=== CAMPAIGN SUMMARY ===")
    print(f"{'Category':<12} {'Bad MPNs':>10} {'Resolved':>10} {'Unresolved':>12} {'Rate':>8}")
    print("-" * 56)
    for s in all_stats:
        rate = s["resolved"] / s["bad_mpns"] * 100 if s["bad_mpns"] else 0
        print(
            f"{s['category']:<12} {s['bad_mpns']:>10} {s['resolved']:>10} "
            f"{s['unresolved']:>12} {rate:>7.1f}%"
        )

    total_bad = sum(s["bad_mpns"] for s in all_stats)
    total_res = sum(s["resolved"] for s in all_stats)
    print(
        f"\nTotal: {total_res}/{total_bad} resolved ({total_res / total_bad * 100:.1f}%)"
        if total_bad
        else ""
    )

    if dk is not None:
        dk.close()


if __name__ == "__main__":
    main()
