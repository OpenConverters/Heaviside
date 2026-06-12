#!/usr/bin/env python3
"""Quarantine provably-synthetic and unusable rows out of the TAS DB.

User-ordered cleanup (2026-06-12): rows that are fabricated, placeholder,
or not orderable parts are MOVED (never deleted) to sibling quarantine
files, following the existing TAS convention (igbts.quarantine.ndjson,
resistors_quarantine_zero_r.ndjson). Criteria are objective:

diodes  -> diodes.quarantine_synthetic.ndjson
    Synthetic generator output: ``part.series`` matches the fake
    taxonomy ``Word_NNNV`` (Schottky_25V, TVS_5V, SiC_Schottky_1200V,
    ...). 4,860 rows with fabricated MPNs and fake datasheet URLs —
    no such parts exist.

capacitors -> capacitors.quarantine_stubs.ndjson
    * ``partNumber == series`` (Vishay catalog-matrix stubs: 611 rows
      all named "TR3", etc.) — real series data but not orderable
      parts; the librarian must re-fetch real MPNs.
    * ``WCAP-MLCC-*`` / ``WCAP-ATH-*`` value-encoding pseudo-MPNs
      (real Würth order codes are numeric).
    * Nichicon ``FRA1H224M23B0C\\d+`` — MPN scheme matches no Nichicon
      family, physically implausible spec, unfindable anywhere.

magnetics -> magnetics.quarantine_stubs.ndjson
    Rows with no ``core``/``coil`` (manufacturerInfo-only fetch stubs).

converters -> converters.quarantine_telemetry.ndjson
    Pipeline-run telemetry records ({'id','status','tas',...})
    accidentally appended to the converter corpus (known xfail in
    tests/regression/tas/test_database_integrity.py).

Everything kept stays byte-identical. Counts printed per criterion.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "TAS" / "data"

SYNTH_SERIES = re.compile(r"^[A-Za-z]+(_[A-Za-z]+)?_\d+V$")
WCAP_PSEUDO = re.compile(r"^WCAP-(MLCC|ATH)-")
FRA_FAKE = re.compile(r"^FRA1H224M23B0C\d+$")


def _move(name: str, quarantine_name: str, is_junk) -> None:
    path = DATA / f"{name}.ndjson"
    qpath = DATA / quarantine_name
    keep: list[str] = []
    junk: list[str] = []
    reasons: Counter[str] = Counter()
    for line in path.open():
        raw = line.rstrip("\n")
        if not raw.strip():
            continue
        reason = is_junk(json.loads(raw))
        if reason:
            junk.append(raw)
            reasons[reason] += 1
        else:
            keep.append(raw)
    if not junk:
        print(f"{name:11s} nothing to quarantine")
        return
    existing = qpath.read_text(encoding="utf-8") if qpath.exists() else ""
    qpath.write_text(existing + "\n".join(junk) + "\n", encoding="utf-8")
    tmp = path.with_suffix(".ndjson.quarantining")
    tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
    tmp.replace(path)
    print(f"{name:11s} kept={len(keep)}  quarantined={len(junk)} -> {quarantine_name}")
    for r, c in reasons.most_common():
        print(f"             {c:6d}  {r}")


def _diode_junk(row: dict) -> str | None:
    body = row.get("semiconductor", row).get("diode", row.get("diode", row))
    series = (
        body.get("manufacturerInfo", {}).get("datasheetInfo", {}).get("part", {}).get("series")
        or ""
    )
    if SYNTH_SERIES.match(series):
        return f"synthetic series taxonomy ({series.split('_')[0]}_*)"
    return None


RATINGS_MPN = re.compile(r"\d+V[-_]\d+A$")
# Fabricated "Infineon" family: IPB060N30-60A etc. — amp suffix always
# equals the prefix digits, the N30/N60/N100 voltage classes don't exist
# in OptiMOS naming, and every datasheetUrl points at a DIFFERENT part's
# PDF (one at a Littelfuse IGBT). Nexperia BUK7535-55A-style suffixes are
# REAL (voltage class + revision) and must not match.
FAKE_IPB_MPN = re.compile(r"^IP[BPIA]\d+N\d+-\d+A$")


def _mosfet_junk(row: dict) -> str | None:
    body = row.get("semiconductor", row).get("mosfet", row.get("mosfet", row))
    mi = body.get("manufacturerInfo", {})
    part = mi.get("datasheetInfo", {}).get("part", {})
    mpn = part.get("partNumber") or ""
    if RATINGS_MPN.search(mpn):
        return "ratings-encoded pseudo-MPN (fantasy-spec fabricated row)"
    if FAKE_IPB_MPN.match(mpn) and mi.get("name") == "Infineon":
        return "fabricated IPB*-NNA family (nonexistent naming, scavenged datasheet URLs)"
    return None


def _igbt_junk(row: dict) -> str | None:
    body = row.get("semiconductor", row).get("igbt", row.get("igbt", row))
    series = (
        body.get("manufacturerInfo", {}).get("datasheetInfo", {}).get("part", {}).get("series")
        or ""
    )
    if SYNTH_SERIES.match(series):
        return f"synthetic series taxonomy ({series.split('_')[0]}_*)"
    return None


def _cap_junk(row: dict) -> str | None:
    body = row.get("capacitor", row)
    mi = body.get("manufacturerInfo", {})
    part = mi.get("datasheetInfo", {}).get("part", {})
    mpn = part.get("partNumber") or ""
    series = part.get("series") or ""
    if mpn and mpn == series and mi.get("name") == "Vishay":
        return "partNumber == series (catalog-matrix stub, not orderable)"
    if WCAP_PSEUDO.match(mpn):
        return "WCAP value-encoding pseudo-MPN"
    if FRA_FAKE.match(mpn):
        return "Nichicon FRA fake MPN scheme"
    return None


def _magnetic_junk(row: dict) -> str | None:
    body = row.get("magnetic", row)
    if "core" not in body or "coil" not in body:
        return "manufacturerInfo-only fetch stub (no core/coil)"
    return None


def _converter_junk(row: dict) -> str | None:
    # Telemetry records carry id/status instead of a converter document.
    if "id" in row and "status" in row:
        return "pipeline-run telemetry record"
    return None


def main() -> int:
    _move("diodes", "diodes.quarantine_synthetic.ndjson", _diode_junk)
    _move("mosfets", "mosfets.quarantine_synthetic.ndjson", _mosfet_junk)
    _move("igbts", "igbts.quarantine_synthetic.ndjson", _igbt_junk)
    _move("capacitors", "capacitors.quarantine_stubs.ndjson", _cap_junk)
    _move("magnetics", "magnetics.quarantine_stubs.ndjson", _magnetic_junk)
    _move("converters", "converters.quarantine_telemetry.ndjson", _converter_junk)
    return 0


if __name__ == "__main__":
    sys.exit(main())
