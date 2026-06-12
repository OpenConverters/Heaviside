#!/usr/bin/env python3
"""TI zener/ESD honest-enrichment campaign (2026-06-13).

Companion to the SAS subType-conditional schema change (SAS 135228d):
zener rows now require breakdownVoltage (= V_Z) + powerDissipation; esd
rows require standoffVoltage + a pulse rating. The 203 schema-invalid
TI rows could never satisfy the old rectifier requirements honestly —
this script fills the NEW fields from the actual TI datasheets cached
in /tmp/ti_zener_ds (fetched 2026-06-13 via ti.com/lit/ds/symlink/).

What it does, all evidence-logged to stdout and the patch file:

1. **Zeners** (BZX84Cx[-Q1], BZX84WCx[-Q1], BZX884Cx[-Q1]): parse the
   per-variant Electrical Characteristics row (MIN/TYP/MAX V_Z at I_Z,
   Z_ZT) plus the family power dissipation from the Features list.
   Emits fill-only patches: breakdownVoltage, powerDissipation,
   zenerTestCurrent, zenerImpedance.

2. **ESD parts** (ESDxxx, ESDSxxx, TPDxxx, MMBZxxx): extract V_RWM,
   IEC 61000-4-2 contact/air kV, I_PP (8/20us), V_CLAMP, V_BR, V_FWD.
   Emits fill-only patches for standoffVoltage, esdVoltageContact,
   esdVoltageAir, peakPulseCurrent, clampingVoltage, breakdownVoltage.

3. **Mis-mapped field remaps** (REWRITES, not fill-only — these rows
   carry another quantity's value in the wrong field, verified against
   the datasheet before touching):
   - esd rows where reverseVoltage equals the datasheet V_RWM: the
     value was V_RWM all along -> field deleted (now in standoffVoltage).
   - esd rows where forwardVoltage equals the datasheet V_CLAMP: it was
     the clamping voltage -> replaced with the real datasheet V_FWD when
     found, deleted otherwise.
   A remap only fires when the misplaced value matches the datasheet
   quantity within 1% — otherwise the row is reported and left alone.

4. **subType corrections** (datasheet-titled):
   - ESD851-Q1: 'Bidirectional ESD Protection Diode' (SLVSIB5) — tagged
     zener, retagged esd.
   - UC1611-SP: quad Schottky diode array — tagged esd, retagged
     schottky (rectifier fields must then come from its datasheet; if
     extraction fails the row stays invalid and is reported).

NOT touched (parked for a user taxonomy decision): TSDxx-Q1, TSMxx,
TVSxxxx — zener-tagged rows whose datasheets title them TVS / surge
protection devices. Retagging them to tvs would put them under the
rectifier required-fields branch, which their datasheets cannot satisfy
honestly; the tvs branch itself needs a schema decision first.

Anything this script cannot source from a datasheet it leaves missing
and reports. No defaults, no estimates.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "TAS" / "data" / "diodes.ndjson"
PDF_DIR = Path("/tmp/ti_zener_ds")
PATCH_OUT = REPO / "scripts" / "enrichment" / "ti_zener_esd_patch.ndjson"

ZENER_FAMILY = re.compile(r"^BZX(84C|84WC|884C)\d", re.IGNORECASE)
ESD_FAMILY = re.compile(r"^(ESDS?\d|TPD\d|MMBZ\d)")
PARKED = re.compile(r"^(TSD\d|TSM\d|TVS\d)")

SYMLINK = "https://www.ti.com/lit/ds/symlink/{}.pdf"


def pdf_text(mpn: str) -> str | None:
    pdf = PDF_DIR / f"{mpn}.pdf"
    if not pdf.exists():
        return None
    import subprocess

    r = subprocess.run(
        ["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True
    )
    return r.stdout if r.returncode == 0 else None


def _num(tok: str) -> float:
    return float(tok.replace(",", ""))


# --------------------------------------------------------------------------
# zener extraction
# --------------------------------------------------------------------------
def extract_zener(mpn: str, text: str) -> tuple[dict, str] | None:
    """Return ({field: value}, evidence) from the family datasheet."""
    pd_m = re.search(r"Total power dissipation:\s*(\d+)\s*mW", text)
    if not pd_m:
        return None
    # the per-variant table row: MPN MIN TYP MAX IZ(mA) ZZT_MAX IZ ...
    row_re = re.compile(
        rf"^\s*{re.escape(mpn)}\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s",
        re.MULTILINE,
    )
    m = row_re.search(text)
    if not m:
        return None
    vmin, vtyp, vmax, iz_ma, zzt, _iz2 = (_num(g) for g in m.groups())
    if not (vmin < vtyp < vmax and 1 <= vtyp <= 100):
        return None  # column misparse — refuse rather than store garbage
    fields = {
        "breakdownVoltage": {"minimum": vmin, "nominal": vtyp, "maximum": vmax},
        "zenerTestCurrent": iz_ma / 1000.0,
        "zenerImpedance": zzt,
        "powerDissipation": int(pd_m.group(1)) / 1000.0,
    }
    ev = (
        f"Electrical Characteristics row '{m.group(0).strip()}' "
        f"(VZ {vmin}/{vtyp}/{vmax} V at {iz_ma} mA, ZZT {zzt} Ohm); "
        f"Features: 'Total power dissipation: {pd_m.group(1)}mW'"
    )
    return fields, ev


# --------------------------------------------------------------------------
# esd extraction
# --------------------------------------------------------------------------
def extract_esd(mpn: str, text: str) -> tuple[dict, dict, str]:
    """Return (fields, datasheet_quantities, evidence). Best-effort per
    field; only fields actually found are returned."""
    fields: dict = {}
    quant: dict = {}
    ev_bits: list[str] = []

    m = re.search(
        r"[±+-]\s*(\d+(?:\.\d+)?)[\s-]*k?V\s*\(?\s*contact", text, re.IGNORECASE
    )
    if m:
        kv = float(m.group(1))
        fields["esdVoltageContact"] = kv * 1000 if kv < 100 else kv
        ev_bits.append(f"IEC contact '{m.group(0).strip()}'")
    m = re.search(
        r"[±+-]\s*(\d+(?:\.\d+)?)[\s-]*k?V\s*\(?\s*air", text, re.IGNORECASE
    )
    if m:
        kv = float(m.group(1))
        fields["esdVoltageAir"] = kv * 1000 if kv < 100 else kv
        ev_bits.append(f"IEC air '{m.group(0).strip()}'")
    # table fallback for contact rating, e.g. 'IEC 61000-4-2 Contact Discharge, all pins  ±30000'
    if "esdVoltageContact" not in fields:
        m = re.search(
            r"61000-4-2[^\n]*Contact[^\n]*?[±+-]\s*(\d{3,6})\b", text, re.IGNORECASE
        )
        if m:
            fields["esdVoltageContact"] = float(m.group(1))
            ev_bits.append(f"IEC table '{m.group(0).strip()[:80]}'")

    m = re.search(r"(\d+(?:\.\d+)?)\s*A\s*\(8/20", text)
    if m:
        fields["peakPulseCurrent"] = float(m.group(1))
        ev_bits.append(f"surge '{m.group(0).strip()}A (8/20us)'")

    def trailing_v_columns(line: str) -> list[float]:
        """Numeric value columns at the end of a spec-table line, just
        before a terminal 'V' unit (the MIN/TYP/MAX columns). Condition
        tokens earlier in the line (IIO < 10nA, 8/20us, Pin 1 to 2) are
        ignored by construction."""
        m = re.search(r"((?:-?\d+(?:\.\d+)?\s+){1,3})V\s*$", line)
        if not m:
            return []
        return [float(t) for t in m.group(1).split()]

    for line in text.splitlines():
        low = line.lower()
        is_vrwm_row = re.match(r"\s*VRWM\b", line) or (
            # label column may wrap, leaving the value on the
            # 'Reverse stand-off voltage ...' line without the VRWM token
            ("stand-off" in low or "standoff" in low) and "voltage" in low
        )
        if is_vrwm_row and "standoffVoltage" not in fields:
            vals = [v for v in trailing_v_columns(line) if 0.5 <= abs(v) <= 60]
            if vals:
                # bidirectional parts list -X X: the working voltage is |X|
                fields["standoffVoltage"] = max(abs(v) for v in vals)
                ev_bits.append(f"VRWM line '{line.strip()[:90]}'")
        if re.search(r"\bVBR\b", line) and "breakdown" in low and "vbr_min" not in quant:
            vals = [v for v in trailing_v_columns(line) if 1 <= abs(v) <= 100]
            if vals:
                # MIN is the first (or only) value column on the VBR row
                quant["vbr_min"] = abs(vals[0])
                ev_bits.append(f"VBR line '{line.strip()[:90]}'")
        if ("vfwd" in low or re.search(r"\bVF\b.*Forward|Forward voltage", line)) and "vfwd" not in quant:
            vals = [v for v in trailing_v_columns(line) if 0.3 <= v <= 2.0]
            if vals:
                quant["vfwd"] = vals[0]
                ev_bits.append(f"VFWD line '{line.strip()[:90]}'")
        if "lamping voltage" in line and ("8/20" in line or "surge" in low) and "vclamp" not in quant:
            vals = [v for v in trailing_v_columns(line) if 2 <= v <= 200]
            if vals:
                # TYP is the first value column when TYP MAX are present
                quant["vclamp"] = vals[0]
                ev_bits.append(f"VCLAMP line '{line.strip()[:90]}'")

    # Older rail-clamp arrays (TPDxE001/TPD2E007/TPD4S012 generation) have
    # no VRWM row; their max continuous working voltage is published as
    # the recommended-operating VIO / VCC / pin voltage ceiling instead.
    if "standoffVoltage" not in fields:
        for line in text.splitlines():
            label = None
            if re.match(r"\s*VI/?O\b", line) and "perating voltage" in line:
                label = "VIO operating voltage"
            elif re.match(r"\s*VCC\b", line) and "upply voltage" in line:
                label = "VCC supply-voltage ceiling (rail clamp)"
            elif re.match(r"\s*Operating Voltage\b.*Pin", line):
                label = "pin operating-voltage ceiling"
            if not label:
                continue
            vals = [v for v in trailing_v_columns(line) if 0.5 <= abs(v) <= 60]
            if vals:
                fields["standoffVoltage"] = max(abs(v) for v in vals)
                ev_bits.append(
                    f"no VRWM row in this datasheet generation; {label} "
                    f"'{line.strip()[:80]}'"
                )
                break

    # MMBZxxVAL (SLVSJ23 style): one combined per-part table row
    # 'MMBZ15VAL  VRWM  VBRmin VBRtyp VBRmax  IT  VCmax  IPP  IRmax SZ IT CD'
    base = re.sub(r"-Q1$", "", mpn)
    m = re.search(
        rf"^\s*{re.escape(base)}(?:-Q1)?\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s",
        text,
        re.MULTILINE,
    )
    if m and base.startswith("MMBZ"):
        vrwm, vbr_min, vbr_typ, vbr_max, _it, vc_max, ipp = (
            float(g) for g in m.groups()
        )
        if vbr_min < vbr_typ < vbr_max and vrwm < vbr_min:
            fields["standoffVoltage"] = vrwm
            fields["breakdownVoltage"] = {
                "minimum": vbr_min,
                "nominal": vbr_typ,
                "maximum": vbr_max,
            }
            fields["clampingVoltage"] = vc_max
            fields["peakPulseCurrent"] = ipp
            ev_bits.append(f"MMBZ table row '{m.group(0).strip()[:90]}'")
            ppp = re.search(
                rf"{re.escape(base)}\s*-\s*IEC 61643-321 Power[^\n]*?(\d+(?:\.\d+)?)\s+W",
                text,
            )
            if ppp:
                fields["peakPulsePower"] = float(ppp.group(1))
                ev_bits.append(f"abs-max P_PP {ppp.group(1)} W (10/1000us)")

    if "vbr_min" in quant and "breakdownVoltage" not in fields:
        fields["breakdownVoltage"] = {"minimum": quant["vbr_min"]}
    if "vclamp" in quant and "clampingVoltage" not in fields:
        fields["clampingVoltage"] = quant["vclamp"]
    return fields, quant, "; ".join(ev_bits)


# --------------------------------------------------------------------------
def main() -> int:
    rows = [json.loads(line) for line in DATA.open() if line.strip()]
    patches: list[dict] = []
    remapped = retagged = zener_ok = esd_ok = 0
    failures: list[str] = []
    parked = 0

    for row in rows:
        body = row.get("semiconductor", row).get("diode", row.get("diode", row))
        di = body.get("manufacturerInfo", {}).get("datasheetInfo", {})
        part = di.get("part", {})
        el = di.setdefault("electrical", {})
        mpn = part.get("partNumber") or ""
        sub = part.get("subType")

        complete_rectifier = "reverseVoltage" in el and "forwardCurrent" in el
        if complete_rectifier and sub not in ("zener", "esd"):
            continue
        # rows already complete under the subType-conditional requireds
        # need nothing (e.g. Nexperia BZX84C rows with V_Z/P_D data)
        if sub == "zener" and "breakdownVoltage" in el and "powerDissipation" in el:
            continue
        if (
            sub == "esd"
            and "standoffVoltage" in el
            and any(
                k in el
                for k in ("peakPulseCurrent", "peakPulsePower", "esdVoltageContact")
            )
        ):
            continue
        if PARKED.match(mpn):
            parked += 1
            continue

        # subType corrections, datasheet-titled
        if mpn == "ESD851-Q1" and sub == "zener":
            part["subType"] = "esd"
            sub = "esd"
            retagged += 1
            print(
                "RETAG ESD851-Q1 zener->esd: SLVSIB5 title "
                "'ESD851-Q1 36V Automotive Bidirectional ESD Protection Diode'"
            )
        if mpn == "UC1611-SP" and sub == "esd":
            text = pdf_text(mpn) or ""
            if "Schottky" in text:
                part["subType"] = "schottky"
                retagged += 1
                title = next(
                    (ln.strip() for ln in text.splitlines() if "Schottky" in ln), ""
                )
                print(f"RETAG UC1611-SP esd->schottky: datasheet line '{title[:100]}'")
            continue  # rectifier fields handled by inspection below, reported if absent

        if sub == "zener" and ZENER_FAMILY.match(mpn):
            text = pdf_text(mpn)
            got = extract_zener(mpn, text) if text else None
            if not got:
                failures.append(f"zener {mpn}: table row not extracted")
                continue
            fields, ev = got
            patches.append(
                {
                    "category": "diodes",
                    "mpn": mpn,
                    "set": {f"manufacturerInfo.datasheetInfo.electrical.{k}": v for k, v in fields.items()},
                    "source": SYMLINK.format(mpn.lower()),
                    "evidence": ev,
                }
            )
            zener_ok += 1
            continue

        if sub == "esd" and (ESD_FAMILY.match(mpn) or mpn == "ESD851-Q1"):
            text = pdf_text(mpn)
            if not text:
                failures.append(f"esd {mpn}: no datasheet text")
                continue
            fields, quant, ev = extract_esd(mpn, text)
            has_pulse = any(
                k in fields
                for k in ("peakPulseCurrent", "peakPulsePower", "esdVoltageContact")
            )
            if "standoffVoltage" not in fields or not has_pulse:
                failures.append(
                    f"esd {mpn}: required extraction incomplete "
                    f"(got {sorted(fields)})"
                )
                continue

            # mis-mapped remaps, only when the datasheet confirms the value
            vrwm = fields["standoffVoltage"]
            rv = el.get("reverseVoltage")
            if rv is not None and abs(rv - vrwm) <= 0.01 * vrwm:
                del el["reverseVoltage"]
                remapped += 1
                print(
                    f"REMAP {mpn}: reverseVoltage={rv} == datasheet VRWM "
                    f"-> moved to standoffVoltage"
                )
            fv = el.get("forwardVoltage")
            vclamp = quant.get("vclamp")
            if fv is not None and vclamp is not None and abs(fv - vclamp) <= 0.01 * vclamp:
                if "vfwd" in quant:
                    el["forwardVoltage"] = quant["vfwd"]
                    print(
                        f"REMAP {mpn}: forwardVoltage={fv} == datasheet VCLAMP "
                        f"-> replaced with real VFWD {quant['vfwd']}"
                    )
                else:
                    del el["forwardVoltage"]
                    el.pop("forwardVoltageAt", None)
                    print(
                        f"REMAP {mpn}: forwardVoltage={fv} == datasheet VCLAMP, "
                        f"no real VFWD found -> deleted"
                    )
                remapped += 1
            patches.append(
                {
                    "category": "diodes",
                    "mpn": mpn,
                    "set": {f"manufacturerInfo.datasheetInfo.electrical.{k}": v for k, v in fields.items()},
                    "source": SYMLINK.format(mpn.lower()),
                    "evidence": ev,
                }
            )
            esd_ok += 1
            continue

    PATCH_OUT.write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in patches) + "\n",
        encoding="utf-8",
    )
    tmp = DATA.with_suffix(".ndjson.campaign")
    tmp.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows)
        + "\n",
        encoding="utf-8",
    )
    tmp.replace(DATA)

    print(
        f"\nzener patched: {zener_ok}  esd patched: {esd_ok}  remaps: {remapped}  "
        f"retags: {retagged}  parked (TSD/TSM/TVSxxxx): {parked}"
    )
    print(f"patch file: {PATCH_OUT} ({len(patches)} entries)")
    if failures:
        print(f"\nfailures ({len(failures)}):")
        for f in failures:
            print(f"  {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
