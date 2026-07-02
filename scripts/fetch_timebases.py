#!/usr/bin/env python3
"""Fetch crystals / oscillators / resonators from Digi-Key into TBAS format.

Manufacturer-agnostic (per repo policy): the default set covers the crossref
target (Würth Elektronik) plus the major original-side crystal vendors, and
``--manufacturers`` overrides it. Digi-Key is the structured parametric source
(same approach as scripts/fetch_we_connectors.py).

Every emitted row is validated against the TBAS schema (via the librarian's
validator registry) BEFORE it is written — no schema-invalid row ever lands in
TAS/data/timebases.ndjson, per the schema-valid-at-every-stage rule. Rows that
fail validation or lack the identity fields (frequency, technology) go to
timebases.quarantine_dk.ndjson with the raw Digi-Key product attached.

Output:     TAS/data/timebases.ndjson    ({"timeBase": {"oscillator": {...}}})
Quarantine: TAS/data/timebases.quarantine_dk.ndjson

Resumable: checkpoint at ~/.heaviside/jobs/timebases_checkpoint.json.

Usage:
    scripts/fetch_timebases.py [--dry-run] [--limit N]
                               [--manufacturers "Würth Elektronik,Abracon"]
                               [--test-mpn 830502587]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from heaviside.librarian.fetcher.auth import load_credentials  # noqa: E402

DATA = REPO / "TAS" / "data"
JOBS = Path.home() / ".heaviside" / "jobs"
JOBS.mkdir(parents=True, exist_ok=True)
CHECKPOINT = JOBS / "timebases_checkpoint.json"

DK_KEYWORD_URL = "https://api.digikey.com/Search/v3/Products/Keyword"

# Default manufacturer sweep: the crossref target + the major crystal vendors
# BOMs actually name on the original side.
_DEFAULT_MANUFACTURERS = [
    "Würth Elektronik",
    "Abracon",
    "ECS",
    "TXC",
    "Epson",
]

# Per-manufacturer search terms. Digi-Key keyword search is loose, so every
# hit is re-filtered on the Manufacturer field + a time-base family below.
_KIND_KEYWORDS = ["crystal", "oscillator", "ceramic resonator"]

# Digi-Key family names that are time bases. Anything else (crystal DRIVERS,
# RTC ICs, filters) is skipped.
_DK_FAMILY_TECH: list[tuple[re.Pattern[str], str | None]] = [
    (re.compile(r"^crystals$", re.I), "quartzCrystal"),
    (re.compile(r"^resonators$", re.I), "ceramicResonator"),
    (re.compile(r"^oscillators$", re.I), None),  # technology from the Type param
    (re.compile(r"^vcxos?\b", re.I), "vcxo"),
    (re.compile(r"^stand alone programmable", re.I), "programmable"),
]

# Oscillator "Type" parameter → TBAS oscillatorTechnology.
_OSC_TYPE_TECH: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"tcxo", re.I), "tcxo"),
    (re.compile(r"vcxo", re.I), "vcxo"),
    (re.compile(r"ocxo", re.I), "ocxo"),
    (re.compile(r"mems", re.I), "mems"),
    (re.compile(r"programmable", re.I), "programmable"),
    (re.compile(r"silicon", re.I), "siliconRC"),
    (re.compile(r"xo|standard|clock", re.I), "crystalOscillator"),
]

# Digi-Key "Output" parameter → TBAS outputType.
_OUTPUT_MAP: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"lvpecl", re.I), "lvpecl"),
    (re.compile(r"lvds", re.I), "lvds"),
    (re.compile(r"hcsl", re.I), "hcsl"),
    (re.compile(r"clipped\s*sine", re.I), "clippedSine"),
    (re.compile(r"sine", re.I), "sine"),
    (re.compile(r"h?cmos|ttl|squarewave|square\s*wave", re.I), "cmos"),
]

_MODE_MAP: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"fundamental", re.I), "fundamental"),
    (re.compile(r"3rd|third", re.I), "overtone3"),
    (re.compile(r"5th|fifth", re.I), "overtone5"),
    (re.compile(r"7th|seventh", re.I), "overtone7"),
]


def _params_dict(product: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in product.get("Parameters") or []:
        name, value = p.get("Parameter"), p.get("Value")
        if isinstance(name, str) and isinstance(value, str):
            out[name] = value
    return out


_FREQ_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(k|m|g)?hz", re.I)


def _parse_frequency(s: str | None) -> float | None:
    if not s:
        return None
    m = _FREQ_RE.search(s)
    if not m:
        return None
    mult = {"k": 1e3, "m": 1e6, "g": 1e9}.get((m.group(2) or "").lower(), 1.0)
    return float(m.group(1)) * mult


_PPM_RE = re.compile(r"([-+±]?\s*\d+(?:\.\d+)?)\s*ppm", re.I)
_PCT_RE = re.compile(r"([-+±]?\s*\d+(?:\.\d+)?)\s*%")


def _parse_fraction(s: str | None) -> float | None:
    """'±20ppm' → 2e-05; '±0.5%' → 5e-3. Returns the magnitude."""
    if not s:
        return None
    m = _PPM_RE.search(s)
    if m:
        return abs(float(m.group(1).replace("±", "").replace(" ", ""))) * 1e-6
    m = _PCT_RE.search(s)
    if m:
        return abs(float(m.group(1).replace("±", "").replace(" ", ""))) * 1e-2
    return None


_CAP_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(p|n)?f", re.I)


def _parse_capacitance(s: str | None) -> float | None:
    if not s or "series" in s.lower():
        return None  # series-resonant: no external load capacitance
    m = _CAP_RE.search(s)
    if not m:
        return None
    mult = {"p": 1e-12, "n": 1e-9}.get((m.group(2) or "").lower())
    if mult is None:
        return None  # a bare "F" value in a crystal row is a parse trap
    return float(m.group(1)) * mult


_OHM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(k|m)?\s*ohms?", re.I)


def _parse_esr(s: str | None) -> float | None:
    if not s:
        return None
    m = _OHM_RE.search(s)
    if not m:
        return None
    mult = {"k": 1e3, "m": 1e-3}.get((m.group(2) or "").lower(), 1.0)
    # Digi-Key writes "70 kOhms"/"50 Ohms"; a capital-M megohm ESR does not
    # exist for real crystals, so 'm' is treated as milli only when spelled
    # lowercase — DK uses 'kOhms' and 'Ohms' in practice.
    return float(m.group(1)) * mult


_TEMP_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*°?C?\s*~\s*(-?\d+(?:\.\d+)?)\s*°?C", re.I)


def _parse_temp_range(s: str | None) -> tuple[float, float] | None:
    if not s:
        return None
    m = _TEMP_RE.search(s)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


_VRANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*V\s*~\s*(\d+(?:\.\d+)?)\s*V", re.I)
_V_RE = re.compile(r"(\d+(?:\.\d+)?)\s*V", re.I)


def _parse_supply(s: str | None) -> tuple[float | None, float | None]:
    if not s:
        return None, None
    m = _VRANGE_RE.search(s)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = _V_RE.search(s)
    if m:
        v = float(m.group(1))
        return v, v
    return None, None


_A_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(m|µ|u|n)?a", re.I)


def _parse_current(s: str | None) -> float | None:
    if not s:
        return None
    m = _A_RE.search(s)
    if not m:
        return None
    mult = {"m": 1e-3, "µ": 1e-6, "u": 1e-6, "n": 1e-9}.get((m.group(2) or "").lower(), 1.0)
    return float(m.group(1)) * mult


def _map_first(maps: list[tuple[re.Pattern[str], str]], s: str | None) -> str | None:
    if not s:
        return None
    for pat, val in maps:
        if pat.search(s):
            return val
    return None


def _technology(product: dict[str, Any], params: dict[str, str]) -> str | None:
    family = (product.get("Family") or {}).get("Value") or ""
    for pat, tech in _DK_FAMILY_TECH:
        if pat.search(family.strip()):
            if tech is not None:
                return tech
            # Oscillators: refine from the Type parameter (or the description).
            type_s = params.get("Type") or params.get("Oscillator Type") or ""
            desc = product.get("Description") or {}
            text = type_s + " " + (
                desc.get("DetailedDescription") or desc.get("ProductDescription") or ""
            )
            return _map_first(_OSC_TYPE_TECH, text) or "crystalOscillator"
    return None


def _convert(product: dict[str, Any], canonical_mfr: str) -> tuple[dict[str, Any] | None, str]:
    """Digi-Key product → TBAS envelope. Returns (record, reason); record is
    None when the part is not a usable time base (reason says why)."""
    params = _params_dict(product)
    mpn = product.get("ManufacturerPartNumber") or ""
    if not mpn:
        return None, "no MPN"

    tech = _technology(product, params)
    if tech is None:
        return None, "not a time-base Digi-Key family"
    freq = _parse_frequency(params.get("Frequency"))
    if freq is None:
        return None, "no parsable frequency (identity field)"

    electrical: dict[str, Any] = {"technology": tech, "frequency": freq}
    tol = _parse_fraction(params.get("Frequency Tolerance"))
    if tol is not None:
        electrical["frequencyTolerance"] = tol
    stab = _parse_fraction(params.get("Frequency Stability"))
    if stab is not None:
        electrical["frequencyStability"] = stab
    cl = _parse_capacitance(params.get("Load Capacitance"))
    if cl is not None:
        electrical["loadCapacitance"] = cl
    esr = _parse_esr(params.get("ESR (Equivalent Series Resistance)"))
    if esr is not None:
        electrical["equivalentSeriesResistance"] = esr
    mode = _map_first(_MODE_MAP, params.get("Operating Mode"))
    if mode is not None:
        electrical["mode"] = mode
    out_type = _map_first(_OUTPUT_MAP, params.get("Output"))
    if tech in ("quartzCrystal", "ceramicResonator"):
        electrical["outputType"] = "none"  # passive: nothing drives out
    elif out_type is not None:
        electrical["outputType"] = out_type
    v_min, v_max = _parse_supply(params.get("Voltage - Supply"))
    i_supply = _parse_current(params.get("Current - Supply (Max)"))
    supply: dict[str, Any] = {}
    if v_min is not None:
        supply["minimumSupplyVoltage"] = v_min
    if v_max is not None:
        supply["maximumSupplyVoltage"] = v_max
    if i_supply is not None:
        supply["currentConsumption"] = i_supply
    if supply:
        electrical["supply"] = supply

    part: dict[str, Any] = {"partNumber": mpn}
    pkg = params.get("Package / Case") or params.get("Size / Dimension")
    if pkg:
        part["package"] = pkg.strip()
    desc = product.get("Description") or {}
    desc_s = desc.get("DetailedDescription") or desc.get("ProductDescription") or ""
    if desc_s:
        part["description"] = desc_s

    datasheet_info: dict[str, Any] = {
        "part": part,
        "electrical": electrical,
        "provenance": [
            {
                "source": "distributor",
                "sourceName": "Digi-Key Product Search v3",
                "retrievedDate": _dt.date.today().isoformat(),
            }
        ],
    }
    temp = _parse_temp_range(params.get("Operating Temperature"))
    if temp is not None:
        datasheet_info["thermal"] = {
            "operatingTemperature": {"minimum": temp[0], "maximum": temp[1]}
        }

    manufacturer_info: dict[str, Any] = {
        "name": canonical_mfr,
        "reference": mpn,
        "datasheetInfo": datasheet_info,
    }
    dk_status = ((product.get("ProductStatus") or "") if isinstance(product.get("ProductStatus"), str) else "").lower()
    status = {
        "active": "production",
        "obsolete": "obsolete",
        "discontinued at digi-key": "production",  # distributor stocking, not lifecycle
        "not for new designs": "nrnd",
        "last time buy": "nrnd",
    }.get(dk_status)
    if status:
        manufacturer_info["status"] = status
    url = product.get("PrimaryDatasheet") or product.get("DatasheetUrl")
    if isinstance(url, str) and url.startswith("http"):
        manufacturer_info["datasheetUrl"] = url
    series = (product.get("Series") or {}).get("Value")
    if isinstance(series, str) and series.strip() and series.strip() != "-":
        manufacturer_info["series"] = series.strip()

    return {"timeBase": {"oscillator": {"manufacturerInfo": manufacturer_info}}}, ""


def _canonicalize_mfr(dk_name: str, requested: str) -> str:
    """Normalize vendor spellings (e.g. every Würth variant → 'Würth Elektronik')."""
    low = dk_name.lower()
    if "würth" in low or "wurth" in low:
        return "Würth Elektronik"
    return dk_name or requested


def _mfr_matches(dk_name: str, requested: str) -> bool:
    def _n(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower().replace("ü", "u"))

    return _n(requested) in _n(dk_name)


def _dk_headers(token: str, client_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-DIGIKEY-Client-Id": client_id,
        "Content-Type": "application/json",
        "accept": "application/json",
    }


def _search_page(
    session: httpx.Client,
    headers: dict[str, str],
    keyword: str,
    offset: int,
    count: int = 50,
) -> dict[str, Any]:
    body = {
        "Keywords": keyword,
        "RecordCount": count,
        "RecordStartPosition": offset,
        "Filters": {},
        "Sort": {"SortOption": "SortByDigiKeyPartNumber", "Direction": "Ascending"},
        "SearchOptions": [],
        "ExcludeMarketPlaceProducts": True,
    }
    for attempt in range(5):
        try:
            r = session.post(DK_KEYWORD_URL, headers=headers, json=body, timeout=40)
        except httpx.TransportError:
            if attempt >= 4:
                raise
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 429:
            retry_after = r.headers.get("Retry-After", "30")
            wait = int(retry_after) if retry_after.isdigit() else 30
            print(f"  Rate limited — sleeping {wait}s", flush=True)
            time.sleep(wait + 1)
            continue
        if r.status_code != 200:
            print(f"  DK {r.status_code} for {keyword!r}: {r.text[:120]}", flush=True)
            return {}
        return r.json()
    return {}


def _get_token(session: httpx.Client, dk: Any) -> str:
    from heaviside.librarian.fetcher.auth import TokenCache

    token_cache = TokenCache()
    cached = token_cache.load()
    if cached and token_cache.is_fresh(cached):
        return str(cached["access_token"])
    # Digi-Key ROTATES the refresh token on every use — the cache holds the
    # newest one; the credentials-file token is only the bootstrap fallback.
    refresh_candidates = []
    if cached and cached.get("refresh_token"):
        refresh_candidates.append(str(cached["refresh_token"]))
    refresh_candidates.append(dk.refresh_token)
    last_err = ""
    for refresh_token in refresh_candidates:
        r = session.post(
            "https://api.digikey.com/v1/oauth2/token",
            data={
                "client_id": dk.client_id,
                "client_secret": dk.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            headers={"accept": "application/json"},
        )
        if r.status_code == 200:
            tok_data = r.json()
            token = str(tok_data["access_token"])
            token_cache.save(
                access_token=token,
                refresh_token=str(tok_data.get("refresh_token", refresh_token)),
                expires_in=int(tok_data.get("expires_in", 1798)),
                token_type=str(tok_data.get("token_type", "Bearer")),
            )
            return token
        last_err = f"{r.status_code} {r.text[:200]}"
    sys.exit(f"Token refresh failed: {last_err}")


def _load_checkpoint() -> dict[str, Any]:
    if CHECKPOINT.exists():
        return json.loads(CHECKPOINT.read_text())
    return {"done": [], "quarantine_reasons": {}}


def _save_checkpoint(cp: dict[str, Any]) -> None:
    CHECKPOINT.write_text(json.dumps(cp, indent=2))


def _validate(record: dict[str, Any]) -> str | None:
    """TBAS-schema validation via the librarian registry. None = valid."""
    from heaviside.librarian.tas import ValidationError, validate_component

    try:
        validate_component("timebases", record)
    except ValidationError as exc:
        return str(exc)
    return None


def _synthetic_dry_run() -> None:
    synthetic = {
        "ManufacturerPartNumber": "830502587",
        "Description": {"DetailedDescription": "CRYSTAL 32.768KHZ 12.5PF SMD"},
        "PrimaryDatasheet": "https://www.we-online.com/components/products/datasheet/830502587.pdf",
        "Manufacturer": {"Value": "Würth Elektronik"},
        "Family": {"Value": "Crystals"},
        "Series": {"Value": "WE-XTAL"},
        "Parameters": [
            {"Parameter": "Frequency", "Value": "32.768 kHz"},
            {"Parameter": "Frequency Tolerance", "Value": "±20ppm"},
            {"Parameter": "Load Capacitance", "Value": "12.5 pF"},
            {"Parameter": "ESR (Equivalent Series Resistance)", "Value": "70 kOhms Max"},
            {"Parameter": "Operating Temperature", "Value": "-40°C ~ 85°C"},
            {"Parameter": "Operating Mode", "Value": "Fundamental"},
            {"Parameter": "Package / Case", "Value": "4-SMD, No Lead"},
        ],
    }
    record, reason = _convert(synthetic, "Würth Elektronik")
    if record is None:
        print(f"  ERROR: conversion returned None: {reason}")
        sys.exit(1)
    err = _validate(record)
    if err:
        print(f"  ERROR: synthetic record fails TBAS validation: {err}")
        sys.exit(1)
    print("  Synthetic WE-XTAL conversion OK (TBAS-valid):")
    print(json.dumps(record, indent=2, ensure_ascii=False))
    print("[dry-run] OK — no API calls made")


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch crystals/oscillators from Digi-Key → TBAS")
    ap.add_argument("--dry-run", action="store_true", help="No API calls; synthetic conversion")
    ap.add_argument("--limit", type=int, default=0, help="Max parts written per manufacturer")
    ap.add_argument(
        "--manufacturers",
        default=",".join(_DEFAULT_MANUFACTURERS),
        help="Comma-separated manufacturer sweep",
    )
    ap.add_argument("--test-mpn", default="", help="Fetch & print a single MPN for validation")
    args = ap.parse_args()

    if args.dry_run:
        print("[dry-run] Validating conversion with synthetic WE-XTAL data...")
        _synthetic_dry_run()
        return

    creds = load_credentials()
    dk = creds.digikey
    manufacturers = [m.strip() for m in args.manufacturers.split(",") if m.strip()]

    cp = _load_checkpoint()
    done_set: set[str] = set(cp.get("done", []))
    quarantine_reasons: dict[str, str] = cp.get("quarantine_reasons", {})

    out_path = DATA / "timebases.ndjson"
    quar_path = DATA / "timebases.quarantine_dk.ndjson"

    existing_mpns: set[str] = set()
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                _, block = next(
                    (
                        (k, v)
                        for k, v in (rec.get("timeBase") or {}).items()
                        if isinstance(v, dict) and "manufacturerInfo" in v
                    ),
                    (None, {}),
                )
                mpn = (block.get("manufacturerInfo") or {}).get("reference", "")
                if mpn:
                    existing_mpns.add(mpn)
            except json.JSONDecodeError:
                pass

    total_written = 0
    total_quarantine = 0

    with httpx.Client(timeout=40) as session:
        token = _get_token(session, dk)
        headers = _dk_headers(token, dk.client_id)

        if args.test_mpn:
            page = _search_page(session, headers, args.test_mpn, 0, 1)
            products = page.get("Products") or []
            if not products:
                print(f"No results for {args.test_mpn!r}")
                return
            product = products[0]
            print("DK parameters:")
            for p in product.get("Parameters", []):
                print(f"  {p.get('Parameter')}: {p.get('Value')}")
            mfr_name = (product.get("Manufacturer") or {}).get("Value") or ""
            record, reason = _convert(product, _canonicalize_mfr(mfr_name, mfr_name))
            print("\nTBAS record:" if record else f"\nSkipped: {reason}")
            if record:
                err = _validate(record)
                print(json.dumps(record, indent=2, ensure_ascii=False))
                print("TBAS validation:", err or "OK")
            return

        for mfr in manufacturers:
            written_for_mfr = 0
            for kind in _KIND_KEYWORDS:
                keyword = f"{mfr} {kind}"
                print(f"\n{'=' * 50}\nSweep: {keyword!r}", flush=True)
                offset = 0
                page_size = 50
                total_for_kw = None
                while True:
                    page = _search_page(session, headers, keyword, offset, page_size)
                    if not page:
                        break
                    products = page.get("Products") or []
                    if total_for_kw is None:
                        total_for_kw = page.get("ProductsCount", 0)
                        print(f"  Total DK results: {total_for_kw}", flush=True)
                    if not products:
                        break

                    for product in products:
                        mpn = product.get("ManufacturerPartNumber", "")
                        if not mpn or mpn in done_set or mpn in existing_mpns:
                            done_set.add(mpn) if mpn else None
                            continue
                        dk_mfr = (product.get("Manufacturer") or {}).get("Value") or ""
                        if not _mfr_matches(dk_mfr, mfr):
                            continue

                        record, reason = _convert(product, _canonicalize_mfr(dk_mfr, mfr))
                        done_set.add(mpn)
                        if record is not None:
                            err = _validate(record)
                            if err:
                                record, reason = None, f"TBAS validation: {err}"
                        if record is None:
                            if "not a time-base" in reason:
                                continue  # unrelated hit (RTC, driver, filter…)
                            quarantine_reasons[mpn] = reason
                            total_quarantine += 1
                            with quar_path.open("a") as f:
                                f.write(
                                    json.dumps({"_mpn": mpn, "_reason": reason, "_dk": product})
                                    + "\n"
                                )
                        else:
                            total_written += 1
                            written_for_mfr += 1
                            existing_mpns.add(mpn)
                            with out_path.open("a") as f:
                                f.write(json.dumps(record, ensure_ascii=False) + "\n")

                    cp["done"] = list(done_set)
                    cp["quarantine_reasons"] = quarantine_reasons
                    _save_checkpoint(cp)

                    offset += len(products)
                    if offset >= (total_for_kw or 0):
                        break
                    if args.limit > 0 and written_for_mfr >= args.limit:
                        print(f"  Hit --limit {args.limit} for {mfr}", flush=True)
                        break
                    time.sleep(0.5)
                if args.limit > 0 and written_for_mfr >= args.limit:
                    break

    print(f"\n{'=' * 50}")
    print(f"Written:     {total_written} time-base records")
    print(f"Quarantined: {total_quarantine}")


if __name__ == "__main__":
    main()
