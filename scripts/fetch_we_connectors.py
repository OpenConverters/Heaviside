#!/usr/bin/env python3
"""Fetch all Würth Elektronik connectors from Digi-Key and convert to CONAS format.

Würth's product website (we-online.com/connectors) is JS-rendered with no public
JSON API. Digi-Key is used as the structured data source for all Würth connector
parametric data (same products, Digi-Key is the authorised distributor).

Output: TAS/data/connectors.ndjson (one CONAS record per line)
Quarantine: TAS/data/connectors.quarantine_dk.ndjson (missing required fields)

Resumable: a checkpoint at ~/.heaviside/jobs/connectors_checkpoint.json tracks
which DK part numbers have been processed so the run can be interrupted and
continued across quota windows.

Usage:
    scripts/fetch_we_connectors.py [--dry-run] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from heaviside.librarian.fetcher.auth import load_credentials

DATA = REPO / "TAS" / "data"
JOBS = Path.home() / ".heaviside" / "jobs"
JOBS.mkdir(parents=True, exist_ok=True)
CHECKPOINT = JOBS / "connectors_checkpoint.json"

DK_KEYWORD_URL = "https://api.digikey.com/Search/v3/Products/Keyword"

# Würth connector series → CONAS family + matingPolarity
# polarity: inferred from series name; None → must be extracted from DK params
_SERIES_MAP: list[dict[str, Any]] = [
    # Terminal blocks
    {"series": "WR-TBL", "family": "terminalBlock", "polarity": "genderless"},
    # Pin headers / socket headers
    {"series": "WR-PHD", "family": "pinHeaderSocket", "polarity": None},
    # Board-to-board / mezzanine
    {"series": "WR-BTB", "family": "boardToBoard", "polarity": None},
    {"series": "WR-MM", "family": "boardToBoard", "polarity": None},
    # Wire-to-board
    {"series": "WR-WTB", "family": "wireToBoard", "polarity": "female"},
    {"series": "WR-BHD", "family": "wireToBoard", "polarity": "female"},
    {"series": "WR-CAB", "family": "wireToBoard", "polarity": "genderless"},
    {"series": "WR-MPC", "family": "wireToBoard", "polarity": "female"},
    {"series": "WR-RAST", "family": "wireToBoard", "polarity": None},
    {"series": "WR-FAST", "family": "wireToBoard", "polarity": None},
    {"series": "WR-NPC", "family": "wireToBoard", "polarity": None},
    {"series": "REDFIT", "family": "wireToBoard", "polarity": "genderless"},
    # FPC / FFC
    {"series": "WR-FPC", "family": "fpcFfc", "polarity": "genderless"},
    {"series": "WR-FFC", "family": "fpcFfc", "polarity": "genderless"},
    # Card edge
    {"series": "WR-CRD", "family": "cardEdge", "polarity": "genderless"},
    # Data interface (modular jacks, D-sub, USB, HDMI)
    {"series": "WR-MJ", "family": "dataInterface", "polarity": "female"},
    {"series": "WR-DSUB", "family": "dataInterface", "polarity": None},
    {"series": "WR-USB", "family": "dataInterface", "polarity": None},
    {"series": "WR-COM", "family": "dataInterface", "polarity": None},
    # Circular (M12)
    {"series": "WR-CIRC", "family": "circular", "polarity": None},
    # RF coaxial
    {"series": "WR-SMA", "family": "rf", "polarity": None},
    {"series": "WR-RSMA", "family": "rf", "polarity": None},
    {"series": "WR-BNC", "family": "rf", "polarity": None},
    {"series": "WR-TNC", "family": "rf", "polarity": None},
    {"series": "WR-MCX", "family": "rf", "polarity": None},
    {"series": "WR-MMCX", "family": "rf", "polarity": None},
    {"series": "WR-UMRF", "family": "rf", "polarity": None},
    {"series": "WR-SMP", "family": "rf", "polarity": None},
    {"series": "WR-SMB", "family": "rf", "polarity": None},
    # DC power jacks
    {"series": "WR-DC", "family": "power", "polarity": None},
    # LED connectors
    {"series": "WR-LECO", "family": "power", "polarity": None},
]

# Digi-Key parameter names → CONAS paths
# Each entry: (dk_param_name, unit_hint) for parsing
_PARAM_CURRENT = ("Current Rating (Amps)", "A")
_PARAM_VOLTAGE = ("Voltage Rating", "V")
_PARAM_POSITIONS = ("Number of Positions", None)
_PARAM_ROWS = ("Number of Rows", None)
_PARAM_PITCH_MATING = ("Pitch - Mating", "m")
_PARAM_MOUNTING = ("Mounting Type", None)
_PARAM_GENDER = ("Gender", None)
_PARAM_ORIENTATION = ("Connector Style", None)
_PARAM_CONTACT_PLATING = ("Contact Finish", None)
_PARAM_TEMP_MIN = ("Operating Temperature", None)
_PARAM_INS_RESIST = ("Insulation Resistance (Min)", None)
_PARAM_MATING_CYCLES = ("Mating Cycles Rated", None)
_PARAM_LOCKING = ("Locking Feature", None)


def _float_value(s: str, unit_hint: str | None = None) -> float | None:
    """Extract a float from a Digi-Key parameter value string.

    Handles: "12 A", "250mV", "1.0 kΩ", "2.54mm", "2.54 mm",
    "0.276\" (7.00mm)" (DK inch+mm), "-" → None.
    All values converted to SI base units.
    """
    if not s or s.strip() in ("-", "N/A", "~", ""):
        return None
    # DK pitch format: '0.276" (7.00mm)' — prefer the mm value in parens
    mm_in_parens = re.search(r"\((\d+\.?\d*)\s*mm\)", s)
    if mm_in_parens and unit_hint == "m":
        return float(mm_in_parens.group(1)) * 1e-3
    # Drop trailing range, grab first number
    m = re.search(r"([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*([mkµnpMGKΩΩΩohm°CAmm\"]+)?", s)
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "").strip().lower()
    # SI conversion
    if unit in ("m", "mm"):
        # For pitch: Digi-Key often says "2.54 mm" — convert to metres
        if unit_hint == "m":
            return num * 1e-3
        return num
    if unit in ("µm", "um"):
        return num * 1e-6
    if unit in ("mv",):
        return num * 1e-3 if unit_hint == "V" else num
    if unit in ("kohm", "kω", "kΩ"):
        return num * 1e3
    if unit in ("mohm", "mω", "mΩ"):
        return num * 1e-3
    if unit in ("mohm", "mω"):
        return num * 1e-3
    return num


def _int_value(s: str) -> int | None:
    m = re.search(r"(\d+)", s or "")
    return int(m.group(1)) if m else None


def _gender_to_polarity(dk_gender: str | None) -> str | None:
    """Map Digi-Key Gender strings to CONAS matingPolarity enum."""
    if not dk_gender:
        return None
    g = dk_gender.strip().lower()
    if "female" in g or "socket" in g or "receptacle" in g:
        return "female"
    if "male" in g or "plug" in g or "pin" in g:
        return "male"
    if "hermaphroditic" in g:
        return "hermaphroditic"
    return "genderless"


def _mounting_to_style(dk_mounting: str | None) -> str | None:
    """Map Digi-Key Mounting Type strings to CONAS mechanical.mountingStyle."""
    if not dk_mounting:
        return None
    m = dk_mounting.strip().lower()
    if "through hole" in m or "tht" in m:
        return "tht"
    if "surface mount" in m or "smt" in m or "smd" in m:
        return "smt"
    if "press-fit" in m or "press fit" in m:
        return "pressFit"
    if "skedd" in m:
        return "skedd"
    if "panel" in m:
        return "panel"
    if "free hanging" in m or "cable" in m or "wire" in m:
        return "cable"
    return None


def _orientation_to_conas(dk_orientation: str | None) -> str | None:
    """Map Digi-Key orientation strings to CONAS mechanical.orientation."""
    if not dk_orientation:
        return None
    o = dk_orientation.strip().lower()
    if "vertical" in o or "straight" in o:
        return "vertical"
    if "right angle" in o or "horizontal" in o:
        return "rightAngle"
    if "mezzanine" in o:
        return "mezzanine"
    return None


def _params_dict(raw_params: list[dict]) -> dict[str, str]:
    """Convert Digi-Key Parameters list to a flat name→value dict."""
    out: dict[str, str] = {}
    for e in raw_params:
        if isinstance(e, dict):
            name = e.get("Parameter") or e.get("ParameterText", "")
            val = e.get("Value") or e.get("ValueText", "")
            if name and val and val.strip() not in ("-", ""):
                out[str(name)] = str(val)
    return out


def _series_config(mpn: str, desc: str, dk_family: str = "") -> dict[str, Any] | None:
    """Match an MPN/description/DK-family to a series config entry."""
    text = f"{mpn} {desc} {dk_family}".upper()
    for cfg in _SERIES_MAP:
        if cfg["series"].upper() in text:
            return cfg
    # DK family fallback: "Terminal Blocks" → WR-TBL default
    fam = dk_family.lower()
    if "terminal block" in fam:
        return {"series": "WR-TBL", "family": "terminalBlock", "polarity": "genderless"}
    if "pin header" in fam or "socket header" in fam:
        return {"series": "WR-PHD", "family": "pinHeaderSocket", "polarity": None}
    if "board to board" in fam or "mezzanine" in fam:
        return {"series": "WR-BTB", "family": "boardToBoard", "polarity": None}
    if "ffc" in fam or "fpc" in fam or "flex" in fam:
        return {"series": "WR-FPC", "family": "fpcFfc", "polarity": "genderless"}
    if "circular" in fam or "m12" in fam:
        return {"series": "WR-CIRC", "family": "circular", "polarity": None}
    if "coaxial" in fam or "rf" in fam or "sma" in fam or "smp" in fam:
        return {"series": "WR-SMA", "family": "rf", "polarity": None}
    if "power" in fam or "dc jack" in fam:
        return {"series": "WR-DC", "family": "power", "polarity": None}
    if "wire" in fam and "board" in fam:
        return {"series": "WR-WTB", "family": "wireToBoard", "polarity": "female"}
    return None


def _convert(product: dict[str, Any], series_cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a Digi-Key product dict to a CONAS connector record.

    Returns None when required fields cannot be determined.
    """
    params = _params_dict(product.get("Parameters") or [])
    mpn = product.get("ManufacturerPartNumber", "")
    desc = product.get("Description", {})
    desc_text = desc.get("DetailedDescription") or desc.get("ProductDescription") or ""
    url = product.get("DatasheetUrl") or product.get("PrimaryDatasheet") or ""
    family_val = product.get("Family", {})
    family_val.get("Value") or ""

    # Required: ratedCurrentPerContact
    # DK uses many names depending on connector family
    i_rated = (
        _float_value(params.get("Current Rating (Amps)", ""))
        or _float_value(params.get("Current - DC (Max)", ""))
        or _float_value(params.get("Current", ""))
        or _float_value(params.get("Current Rating", ""))
        or _float_value(params.get("Current - Contact", ""))
    )
    if i_rated is None:
        return None

    # Required: ratedVoltage
    v_rated = (
        _float_value(params.get("Voltage Rating", ""))
        or _float_value(params.get("Voltage - Rated", ""))
        or _float_value(params.get("Voltage", ""))
        or _float_value(params.get("Voltage - DC", ""))
    )
    if v_rated is None:
        return None

    # Required: matingPolarity
    dk_gender = params.get("Gender") or params.get("Gender / Termination Style")
    polarity = series_cfg.get("polarity") or _gender_to_polarity(dk_gender)
    if not polarity:
        polarity = "genderless"

    # Mechanical
    pitch_mm = (
        _float_value(params.get("Pitch - Mating"), unit_hint="m")
        or _float_value(params.get("Pitch"), unit_hint="m")
        or _float_value(params.get("Pitch - Termination to Termination"), unit_hint="m")
    )
    positions = _int_value(
        params.get("Number of Positions")
        or params.get("Positions")
        or params.get("Positions Per Level")
    )
    rows = _int_value(
        params.get("Number of Rows") or params.get("Rows") or params.get("Number of Levels")
    )
    mounting = _mounting_to_style(params.get("Mounting Type") or params.get("Mounting Style"))
    orientation = _orientation_to_conas(
        params.get("Connector Style")
        or params.get("Termination Direction")
        or params.get("Mating Orientation")
    )
    mating_cycles = _int_value(params.get("Mating Cycles Rated") or params.get("Durability"))

    mechanical: dict[str, Any] = {}
    if pitch_mm is not None:
        mechanical["pitch"] = pitch_mm
    if positions is not None:
        mechanical["positions"] = positions
    if rows is not None:
        mechanical["rows"] = rows
    if mounting is not None:
        mechanical["mountingStyle"] = mounting
    if orientation is not None:
        mechanical["orientation"] = orientation
    if mating_cycles is not None:
        mechanical["matingCycles"] = mating_cycles

    # Temperature range
    env: dict[str, Any] = {}
    temp_str = params.get("Operating Temperature") or ""
    if temp_str:
        m_temp = re.search(r"([-\d.]+)\s*°?C\s*~\s*([-\d.]+)\s*°?C", temp_str)
        if m_temp:
            env["operatingTemperature"] = {
                "minimum": float(m_temp.group(1)),
                "maximum": float(m_temp.group(2)),
            }

    electrical: dict[str, Any] = {
        "ratedCurrentPerContact": i_rated,
        "ratedVoltage": v_rated,
    }
    contact_resistance = _float_value(
        params.get("Contact Resistance (Max)") or params.get("Contact Resistance")
    )
    if contact_resistance is not None:
        electrical["contactResistance"] = {"nominal": contact_resistance}
    ins_resist = _float_value(params.get("Insulation Resistance (Min)"))
    if ins_resist is not None:
        electrical["insulationResistance"] = ins_resist
    dwv = _float_value(
        params.get("Dielectric Withstanding Voltage - Rated")
        or params.get("Dielectric Withstanding Voltage")
    )
    if dwv is not None:
        electrical["dielectricWithstandingVoltage"] = dwv

    part: dict[str, Any] = {
        "matingPolarity": polarity,
        "partNumber": mpn,
        "series": series_cfg["series"],
        "description": desc_text[:200] if desc_text else None,
    }
    part = {k: v for k, v in part.items() if v is not None}

    family_details: dict[str, Any] = {"family": series_cfg["family"]}

    datasheet_info: dict[str, Any] = {
        "part": part,
        "electrical": electrical,
        "mechanical": mechanical,
        "familyDetails": family_details,
    }
    if env:
        datasheet_info["environmental"] = env

    manufacturer_info: dict[str, Any] = {
        "name": "Würth Elektronik",
        "reference": mpn,
        "datasheetInfo": datasheet_info,
    }
    if url:
        manufacturer_info["datasheetUrl"] = url

    return {"connector": {"manufacturerInfo": manufacturer_info}}


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


def _load_checkpoint() -> dict[str, Any]:
    if CHECKPOINT.exists():
        return json.loads(CHECKPOINT.read_text())
    return {"done": [], "quarantine_reasons": {}}


def _save_checkpoint(cp: dict[str, Any]) -> None:
    CHECKPOINT.write_text(json.dumps(cp, indent=2))


def _synthetic_dry_run() -> None:
    """Validate conversion logic with a synthetic WR-TBL product dict (no network calls)."""
    synthetic = {
        "ManufacturerPartNumber": "691212200001",
        "Description": {"DetailedDescription": "WR-TBL Rising Cage Clamp 2 pos 7.00mm SMT"},
        "DatasheetUrl": "https://example.com/ds.pdf",
        "Manufacturer": {"Value": "Würth Elektronik"},
        "Family": {"Value": "Terminal Blocks"},
        "Parameters": [
            {"Parameter": "Current Rating (Amps)", "Value": "13 A"},
            {"Parameter": "Voltage Rating", "Value": "300 V"},
            {"Parameter": "Number of Positions", "Value": "2"},
            {"Parameter": "Pitch - Mating", "Value": "7.00 mm"},
            {"Parameter": "Mounting Type", "Value": "Through Hole"},
            {"Parameter": "Gender", "Value": "Genderless"},
            {"Parameter": "Operating Temperature", "Value": "-40°C ~ 100°C"},
        ],
    }
    cfg = {"series": "WR-TBL", "family": "terminalBlock", "polarity": "genderless"}
    record = _convert(synthetic, cfg)
    if record is None:
        print("  ERROR: conversion returned None for synthetic product")
        sys.exit(1)
    print("  Synthetic WR-TBL conversion OK:")
    print(json.dumps(record, indent=2, ensure_ascii=False))
    print("[dry-run] OK — no API calls made")


def _test_single_mpn(dk: Any, mpn: str) -> None:
    """Fetch and convert a single MPN for validation."""
    from heaviside.librarian.fetcher.auth import TokenCache

    with httpx.Client(timeout=40) as session:
        token_cache = TokenCache()
        cached = token_cache.load()
        if cached and token_cache.is_fresh(cached):
            token = str(cached["access_token"])
        else:
            r = session.post(
                "https://api.digikey.com/v1/oauth2/token",
                data={
                    "client_id": dk.client_id,
                    "client_secret": dk.client_secret,
                    "refresh_token": dk.refresh_token,
                    "grant_type": "refresh_token",
                },
                headers={"accept": "application/json"},
            )
            r.raise_for_status()
            tok_data = r.json()
            token = str(tok_data["access_token"])
            token_cache.save(
                access_token=token,
                refresh_token=str(tok_data.get("refresh_token", dk.refresh_token)),
                expires_in=int(tok_data.get("expires_in", 1798)),
                token_type=str(tok_data.get("token_type", "Bearer")),
            )
        headers = _dk_headers(token, dk.client_id)
        page = _search_page(session, headers, mpn, offset=0, count=1)
        products = page.get("Products") or []
        if not products:
            print(f"No results for {mpn!r}")
            return
        product = products[0]
        print("DK parameters:")
        for p in product.get("Parameters", []):
            print(f"  {p.get('Parameter')}: {p.get('Value')}")
        desc_obj = product.get("Description") or {}
        desc_str = desc_obj.get("DetailedDescription") or desc_obj.get("ProductDescription") or ""
        dk_fam = (product.get("Family") or {}).get("Value") or ""
        cfg = _series_config(mpn, desc_str, dk_fam) or {
            "series": "UNKNOWN",
            "family": "terminalBlock",
            "polarity": None,
        }
        record = _convert(product, cfg)
        print("\nCONAS record:")
        print(json.dumps(record, indent=2, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch Würth connectors from Digi-Key → CONAS")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="No DK API calls; just check code paths with synthetic data",
    )
    ap.add_argument("--limit", type=int, default=0, help="Max parts per series (0=unlimited)")
    ap.add_argument(
        "--series", default="", help="Comma-separated series filter (e.g. 'WR-TBL,WR-PHD')"
    )
    ap.add_argument("--test-mpn", default="", help="Fetch & print a single MPN for validation")
    args = ap.parse_args()

    if args.dry_run:
        print("[dry-run] Validating conversion logic with synthetic WR-TBL data...")
        _synthetic_dry_run()
        return

    creds = load_credentials()
    dk = creds.digikey

    series_filter = (
        {s.strip().upper() for s in args.series.split(",") if s.strip()} if args.series else set()
    )

    if args.test_mpn:
        _test_single_mpn(dk, args.test_mpn)
        return

    cp = _load_checkpoint()
    done_set: set[str] = set(cp.get("done", []))
    quarantine_reasons: dict[str, str] = cp.get("quarantine_reasons", {})

    out_path = DATA / "connectors.ndjson"
    quar_path = DATA / "connectors.quarantine_dk.ndjson"

    # Load existing MPN set to avoid duplicates
    existing_mpns: set[str] = set()
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                mpn = rec.get("connector", {}).get("manufacturerInfo", {}).get("reference", "")
                if mpn:
                    existing_mpns.add(mpn)
            except json.JSONDecodeError:
                pass

    total_written = 0
    total_quarantine = 0

    with httpx.Client(timeout=40) as session:
        # Get token
        from heaviside.librarian.fetcher.auth import TokenCache

        token_cache = TokenCache()
        cached = token_cache.load()
        if cached and token_cache.is_fresh(cached):
            token = str(cached["access_token"])
        else:
            # Need to refresh via client_secret + refresh_token
            r = session.post(
                "https://api.digikey.com/v1/oauth2/token",
                data={
                    "client_id": dk.client_id,
                    "client_secret": dk.client_secret,
                    "refresh_token": dk.refresh_token,
                    "grant_type": "refresh_token",
                },
                headers={"accept": "application/json"},
            )
            if r.status_code != 200:
                sys.exit(f"Token refresh failed: {r.status_code} {r.text[:200]}")
            tok_data = r.json()
            token = str(tok_data["access_token"])
            token_cache.save(
                access_token=token,
                refresh_token=str(tok_data.get("refresh_token", dk.refresh_token)),
                expires_in=int(tok_data.get("expires_in", 1798)),
                token_type=str(tok_data.get("token_type", "Bearer")),
            )

        headers = _dk_headers(token, dk.client_id)

        for cfg in _SERIES_MAP:
            series_name = cfg["series"]
            if series_filter and series_name.upper() not in series_filter:
                continue

            keyword = f"Würth {series_name}"
            print(f"\n{'=' * 50}\nSeries: {series_name} ({cfg['family']})", flush=True)

            offset = 0
            page_size = 50
            total_for_series = None

            while True:
                page = _search_page(session, headers, keyword, offset, page_size)
                if not page:
                    break

                products = page.get("Products") or []
                if total_for_series is None:
                    total_for_series = page.get("ProductsCount", 0)
                    print(f"  Total DK results: {total_for_series}", flush=True)

                if not products:
                    break

                for product in products:
                    mpn = product.get("ManufacturerPartNumber", "")
                    if not mpn:
                        continue
                    if mpn in done_set:
                        continue
                    if mpn in existing_mpns:
                        done_set.add(mpn)
                        continue

                    # Filter: must be a Würth Elektronik part
                    mfr = product.get("Manufacturer") or {}
                    mfr_name = mfr.get("Value") or ""
                    if "würth" not in mfr_name.lower() and "wurth" not in mfr_name.lower():
                        continue

                    # Determine series config from actual MPN + description
                    desc = product.get("Description") or {}
                    desc_text = (
                        desc.get("DetailedDescription") or desc.get("ProductDescription") or ""
                    )
                    dk_fam = (product.get("Family") or {}).get("Value") or ""
                    actual_cfg = _series_config(mpn, desc_text, dk_fam) or cfg

                    record = _convert(product, actual_cfg)
                    done_set.add(mpn)

                    if record is None:
                        reason = "missing required electrical params"
                        quarantine_reasons[mpn] = reason
                        total_quarantine += 1
                        if not args.dry_run:
                            with quar_path.open("a") as f:
                                # Store raw DK product for later processing
                                f.write(
                                    json.dumps({"_mpn": mpn, "_reason": reason, "_dk": product})
                                    + "\n"
                                )
                    else:
                        total_written += 1
                        existing_mpns.add(mpn)
                        if not args.dry_run:
                            with out_path.open("a") as f:
                                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                        if args.limit > 0 and total_written >= args.limit:
                            print(f"  Hit --limit {args.limit}", flush=True)
                            break

                if not args.dry_run:
                    cp["done"] = list(done_set)
                    cp["quarantine_reasons"] = quarantine_reasons
                    _save_checkpoint(cp)

                offset += len(products)
                if offset >= (total_for_series or 0):
                    break
                if args.limit > 0 and total_written >= args.limit:
                    break

                # Brief pause between pages to respect rate limits
                time.sleep(0.5)

    print(f"\n{'=' * 50}")
    print(f"Written:     {total_written} connector records")
    print(f"Quarantined: {total_quarantine} (missing required fields)")
    if args.dry_run:
        print("[dry-run: no files written]")


if __name__ == "__main__":
    main()
