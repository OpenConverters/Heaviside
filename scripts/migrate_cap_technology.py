#!/usr/bin/env python3
"""TAS capacitor ``technology`` migration: free-form strings → CAS enum.

Three evidence tiers, applied in order (user-approved 2026-06-12):

1. **Direct renames** — the old CAS enum strings ('MLCC Class I',
   'Alum. Electrolytic', …) map 1:1 onto the new chemistry enum.
2. **MPN-embedded dielectric codes** — manufacturer part numbers carry
   the EIA/JIS temperature characteristic literally (TDK C3216X7R…,
   KEMET C0603C102K3G…); per-manufacturer rules researched against the
   manufacturers' numbering guides live in ``scripts/cap_tech_rules/``.
3. **Series rules** — series→chemistry maps researched per datasheet
   (Vishay tantalum + film families, Würth/KEMET/AVX/Panasonic misc).

A row that no rule covers is LEFT UNCHANGED and reported — never
guessed. Rows whose ``technology`` tag is *wrong* (e.g. Vishay Draloric
ceramic RF caps tagged 'Film Capacitor') are likewise reported, not
silently reclassified, unless a verified rule covers them.

When a ceramic class is derived from an explicit dielectric code, the
code is also stored in ``part.dielectricCode`` (schema field added by
CAS baefd79) unless already present.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "TAS" / "data" / "capacitors.ndjson"
RULES_DIR = REPO / "scripts" / "cap_tech_rules"

# --- tier 1: direct renames of the old enum strings -----------------------
DIRECT: dict[str, tuple[str, str | None]] = {
    # old technology string -> (new enum value, dielectricCode or None)
    "Alum. Electrolytic": ("aluminum-electrolytic-wet", None),
    "Aluminum Electrolytic": ("aluminum-electrolytic-wet", None),
    "Aluminium Electrolytic Capacitors - SMD": ("aluminum-electrolytic-wet", None),
    "Alum. Polymer": ("aluminum-electrolytic-polymer", None),
    "Aluminum Polymer": ("aluminum-electrolytic-polymer", None),
    "Hybrid Polymer": ("aluminum-hybrid-polymer", None),
    "Tantalum Capacitors - Polymer": ("tantalum-polymer", None),
    "MLCC Class I": ("ceramic-class-1", None),
    "MLCC Class I C0G": ("ceramic-class-1", "C0G"),
    "MLCC Class II": ("ceramic-class-2", None),
    "MLCC Class II X5R": ("ceramic-class-2", "X5R"),
    "MLCC Class II X7R": ("ceramic-class-2", "X7R"),
    "X7R": ("ceramic-class-2", "X7R"),
}

# --- tier 2: plain 3-char EIA codes embedded in the MPN -------------------
# Only unambiguous >=3-char codes here; 2-letter JIS codes (SL/CH/JB…)
# are matched positionally by the per-manufacturer rules to avoid
# substring false-positives.
EIA_SUBSTRING: dict[str, str] = {
    "C0G": "ceramic-class-1",
    "NP0": "ceramic-class-1",
    "U2J": "ceramic-class-1",
    "X5R": "ceramic-class-2",
    "X6S": "ceramic-class-2",
    "X6T": "ceramic-class-2",
    "X7R": "ceramic-class-2",
    "X7S": "ceramic-class-2",
    "X7T": "ceramic-class-2",
    "X8R": "ceramic-class-2",
    "X8L": "ceramic-class-2",
    "Y5V": "ceramic-class-2",  # project convention (flagged in rules files)
    "Y5U": "ceramic-class-3",
    "Z5U": "ceramic-class-3",
}

CERAMIC_BUCKETS = {
    "MLCC",
    "MLCC (High-Q)",
    "Multilayer Ceramic Capacitors MLCC - SMD/SMT",
    "Ceramic Leaded",
    "Ceramic Disc",
}
MISC_BUCKETS = {"Other", "Feedthrough", "", None}


def _load(name: str) -> dict:
    return json.loads((RULES_DIR / name).read_text(encoding="utf-8"))


def _mfr_matches(rule_mfr: str, mfr: str) -> bool:
    if rule_mfr.endswith("*"):
        return mfr.startswith(rule_mfr[:-1])
    if "/" in rule_mfr:  # e.g. "TDK/EPCOS"
        return mfr in rule_mfr.split("/")
    return mfr == rule_mfr


class Migrator:
    def __init__(self) -> None:
        self.tantalum = _load("tantalum.json")
        self.film = _load("film.json")["rules"]
        self.ceramic = _load("ceramic_codes.json")["rules"]
        self.misc = _load("misc_buckets.json")["rules"]
        self.mapped: Counter[str] = Counter()
        self.unmapped: Counter[tuple[str | None, str, str]] = Counter()

    # -- tier 2/manufacturer-specific ceramic code extraction --------------
    def _ceramic_class(self, mfr: str, mpn: str) -> tuple[str, str] | None:
        for rule in self.ceramic:
            if not _mfr_matches(rule["manufacturer"], mfr):
                continue
            m = re.match(rule["mpn_regex"], mpn)
            if m:
                code = m.group(m.lastindex or 1)
                cls = rule["code_map"].get(code)
                if cls:
                    diel = rule.get("dielectric_code_map", {}).get(code, code)
                    return cls, diel
        # plain unambiguous EIA substring as a final ceramic tier
        for code, cls in EIA_SUBSTRING.items():
            if code in mpn.upper():
                return cls, code
        return None

    def _series_rules(self, rules: list[dict], mfr: str, series: str, mpn: str) -> tuple[str, str | None] | None:
        for rule in rules:
            if not _mfr_matches(rule["manufacturer"], mfr):
                continue
            sr, mr = rule.get("series_regex"), rule.get("mpn_regex")
            if sr and not re.match(sr, series):
                continue
            if mr and not re.match(mr, mpn):
                continue
            if not sr and not mr:
                continue
            return rule["technology"], rule.get("dielectric_code")
        return None

    def classify(self, mfr: str, tech: str | None, series: str, mpn: str) -> tuple[str, str | None] | None:
        if tech in DIRECT:
            return DIRECT[tech]
        if tech in CERAMIC_BUCKETS:
            hit = self._ceramic_class(mfr, mpn)
            if hit:
                return hit
            return self._series_rules(self.misc, mfr, series, mpn)
        if tech == "Tantalum":
            entry = self.tantalum["series"].get(series)
            if entry and mfr == self.tantalum["manufacturer"]:
                return entry["technology"], None
            return None
        if tech == "Film Capacitor":
            return self._series_rules(self.film, mfr, series, mpn)
        if tech in MISC_BUCKETS:
            hit = self._series_rules(self.misc, mfr, series, mpn)
            if hit:
                return hit
            # misc rows can also carry plain EIA codes / ceramic rules
            return self._ceramic_class(mfr, mpn)
        return None

    def run(self) -> int:
        out_lines: list[str] = []
        for line in DATA.open():
            raw = line.rstrip("\n")
            if not raw.strip():
                continue
            row = json.loads(raw)
            body = row.get("capacitor", row)
            mi = body.get("manufacturerInfo", {})
            part = mi.get("datasheetInfo", {}).get("part", {})
            tech = part.get("technology")
            mfr = mi.get("name") or ""
            series = part.get("series") or ""
            mpn = part.get("partNumber") or ""

            hit = self.classify(mfr, tech, series, mpn)
            if hit is None:
                self.unmapped[(tech, mfr, series or mpn[:14])] += 1
                out_lines.append(raw)
                continue
            new_tech, diel = hit
            part["technology"] = new_tech
            if diel and not part.get("dielectricCode"):
                part["dielectricCode"] = diel
            self.mapped[new_tech] += 1
            out_lines.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))

        tmp = DATA.with_suffix(".ndjson.migrating")
        tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        tmp.replace(DATA)

        total = sum(self.mapped.values()) + sum(self.unmapped.values())
        print(f"rows: {total}  mapped: {sum(self.mapped.values())}  unmapped: {sum(self.unmapped.values())}")
        print("\nmapped by enum value:")
        for k, n in self.mapped.most_common():
            print(f"  {n:7d}  {k}")
        print("\ntop unmapped groups (technology, manufacturer, series|mpn):")
        for k, n in self.unmapped.most_common(25):
            print(f"  {n:7d}  {k}")
        return 0


if __name__ == "__main__":
    sys.exit(Migrator().run())
