#!/usr/bin/env python3
"""Backfill Infineon mosfet electrical fields from Infineon's own product-table API.

Distributors (Digi-Key/Mouser) don't index many Infineon automotive/CoolMOS
parts by base MPN. Infineon's product-table pages expose a JSON API
(``.../product-table/<table>.product-table.en.json?familyIds=...``) with the
full parametric data — but the endpoint is session/anti-bot gated, so we fetch
it through a real (headless) browser context that carries the page cookies.

Parameters used (Infineon parameterId -> TAS electrical field, with unit fix):
  308 VDS  (V)    -> drainSourceVoltage
  394 RDS(on)(mOhm)-> onResistance         (*1e-3)
  286 ID   (A)    -> continuousDrainCurrent
  313 VGS(th)(V)  -> gateThresholdVoltage  (-> {maximum/minimum})
  382 QG   (nC)   -> totalGateCharge        (*1e-9)

Matching: our DB base MPN against Infineon opnName/productName, exact
(alphanumeric-normalised) first, then unique-prefix (base MPN is a prefix of
an orderable opnName) — guarded so all prefix hits resolve to ONE family
(consistent field values), never an ambiguous merge. Only missing fields are
filled; existing values are never overwritten. No estimation.

Usage:
  scripts/backfill_infineon_producttable.py --tables power-mosfets [coolmos-...] [--dry-run]
  scripts/backfill_infineon_producttable.py --cached /tmp/ifx_data.json  # reuse a fetch
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "TAS" / "data" / "mosfets.ndjson"
REQ = ["drainSourceVoltage", "onResistance", "continuousDrainCurrent",
       "gateThresholdVoltage", "totalGateCharge"]
PMAP = {308: ("drainSourceVoltage", 1.0), 394: ("onResistance", 1e-3),
        286: ("continuousDrainCurrent", 1.0), 313: ("gateThresholdVoltage", 1.0),
        382: ("totalGateCharge", 1e-9)}


def norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def fetch_table(table: str) -> list:
    """Fetch one Infineon product-table's JSON via a headless browser context."""
    from playwright.sync_api import sync_playwright
    page_url = f"https://www.infineon.com/product-table/{table}?page=1"
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(page_url, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(4000)
        # familyIds come from the producttable element's data-queryparam
        qp = pg.get_attribute('[data-component="producttable"]', "data-queryparam") or ""
        api = (f"https://www.infineon.com/dataApi/en/product-table/{table}"
               f".product-table.en.json?collectionName=&familyIds={qp}")
        resp = ctx.request.get(api, headers={"Referer": page_url})
        if resp.status != 200:
            raise RuntimeError(f"{table}: HTTP {resp.status}")
        data = resp.json()
        b.close()
    return _find_rows(data)


def _find_rows(o):
    if isinstance(o, list) and o and isinstance(o[0], dict) and len(o) > 5:
        return o
    if isinstance(o, dict):
        for v in o.values():
            r = _find_rows(v)
            if r:
                return r
    return None


def build_index(rows: list) -> dict:
    """{normalized_key: (fields, family_id)} over opnName + productName."""
    idx = {}
    for r in rows:
        fields = {}
        for pv in (r.get("parameterValues") or []):
            pid = pv.get("parameterId")
            if pid not in PMAP:
                continue
            name, scale = PMAP[pid]
            if name == "gateThresholdVoltage":
                obj = {}
                if pv.get("valueMax") is not None:
                    obj["maximum"] = pv["valueMax"]
                if pv.get("valueMin") is not None:
                    obj["minimum"] = pv["valueMin"]
                if not obj and pv.get("valueNumber") is not None:
                    obj["maximum"] = pv["valueNumber"]
                if obj:
                    fields[name] = obj
            else:
                v = next((pv[k] for k in ("valueMax", "valueNumber", "valueMin")
                          if pv.get(k) is not None), None)
                if v is not None:
                    fields[name] = round(v * scale, 12)
        fam = r.get("familyId")
        for o in (r.get("opns") or []):
            for key in (o.get("opnName"), o.get("productName")):
                if key:
                    idx[norm(key)] = (fields, fam)
    return idx


def lookup(mpn: str, idx: dict):
    """Exact normalized match, else unique-prefix (one family) match."""
    k = norm(mpn)
    if not k:
        return None
    if k in idx:
        return idx[k][0]
    if len(k) >= 8:
        fams = {idx[ik][1] for ik in idx if ik.startswith(k)}
        if len(fams) == 1:
            return next(idx[ik][0] for ik in idx if ik.startswith(k))
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tables", nargs="*", default=["power-mosfets"])
    ap.add_argument("--cached", help="reuse a saved product-table JSON instead of fetching")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = []
    if args.cached:
        rows = _find_rows(json.load(open(args.cached)))
    else:
        for t in args.tables:
            r = fetch_table(t)
            print(f"  fetched {t}: {len(r) if r else 0} families")
            rows.extend(r or [])
    idx = build_index(rows)
    print(f"Infineon index: {len(idx)} normalized keys from {len(rows)} families")

    lines = DATA.read_text().splitlines()
    filled = ported = 0
    out = []
    for line in lines:
        s = line.strip()
        if not s:
            out.append(line)
            continue
        rec = json.loads(s)
        mf = rec["semiconductor"]["mosfet"]
        mi = mf.get("manufacturerInfo", {})
        man = (mi.get("manufacturer") or mi.get("name") or "")
        di = mi.get("datasheetInfo", {})
        el = di.get("electrical", {})
        miss = [x for x in REQ if x not in el or el[x] in (None, "")]
        if not miss or "infineon" not in man.lower():
            out.append(line)
            continue
        pn = (di.get("part", {}) or {}).get("partNumber")
        fields = lookup(pn or "", idx)
        if not fields:
            out.append(line)
            continue
        changed = False
        for f in miss:
            if fields.get(f) is not None:
                el[f] = fields[f]
                ported += 1
                changed = True
        if changed:
            filled += 1
            mf["manufacturerInfo"]["datasheetInfo"]["electrical"] = el
            out.append(json.dumps(rec))
        else:
            out.append(line)
    print(f"Infineon mosfets filled: {filled} | fields ported: {ported}")
    if not args.dry_run and filled:
        tmp = DATA.with_suffix(".ndjson.tmp")
        tmp.write_text("\n".join(out) + "\n")
        os.replace(tmp, DATA)
        print("written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
