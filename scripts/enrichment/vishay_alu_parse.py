#!/usr/bin/env python3
"""Parse Vishay aluminum-electrolytic series datasheets (pdftotext -layout output)
and emit enrichment patch lines for MPNs present in TAS/data/capacitors.ndjson.

Per-series config defines the numeric-column layout between the case-size
('D x L') tokens and the ordering-code suffix tokens (\\d{5}E3).
"""
import json
import random
import re

DS_DIR = "/tmp/vishay_ds"
TAS = "/home/alf/OpenConverters/Heaviside/TAS/data/capacitors.ndjson"
OUT = "/home/alf/OpenConverters/Heaviside/scripts/enrichment/capacitors_patch.ndjson"
with open("/tmp/vishay_urls.txt") as _urls_fh:
    URLS = dict(line.split() for line in _urls_fh)

# Config fields:
#  series: TAS series string
#  ncols: expected numeric column count between case size and suffixes
#  ir: (index, scale_to_A, freq_Hz, "conditions text")
#  esr: (index, scale_to_Ohm, freq_Hz, "label text", is_impedance) or None
CFG = {
 "28315": dict(series="148 RUS", ncols=5,
    ir=(0, 1e-3, 100, "100 Hz, 105 deg C"),
    esr=(3, 1.0, 100000, "max impedance Z at 100 kHz, 20 deg C", True)),
 "28321": dict(series="136 RVI", ncols=5,
    ir=(0, 1e-3, 100000, "100 kHz, 105 deg C"),
    esr=(3, 1e-3, 100000, "max impedance Z at 100 kHz, +20 deg C", True)),
 "28323": dict(series="150 RMI", ncols=5,
    ir=(0, 1e-3, 100000, "100 kHz, 105 deg C"),
    esr=(3, 1.0, 100000, "max impedance Z at 100 kHz, +20 deg C", True)),
 "28325": dict(series="021 ASM", ncols=5,
    ir=(0, 1e-3, 100, "100 Hz, 85 deg C"),
    esr=(3, 1.0, 100, "max ESR at 100 Hz", False)),
 "28332": dict(series="138 AML", ncols=6,
    ir=(0, 1e-3, 100, "100 Hz, 105 deg C"),
    esr=(3, 1.0, 100, "max ESR at 100 Hz", False)),
 "28334": dict(series="118 AHT", ncols=7,
    ir=(1, 1e-3, 100, "100 Hz, 125 deg C"),
    esr=(5, 1.0, 100, "max ESR at 100 Hz", False)),
 "28338": dict(series="157 PUM-SI", ncols=6,
    ir=(0, 1.0, 120, "120 Hz, 85 deg C"),
    esr=(3, 1e-3, 100, "max ESR at 100 Hz", False)),
 "28340": dict(series="056/057 PSM-SI", ncols=6,
    ir=(0, 1.0, 100, "100 Hz, 85 deg C"),
    esr=(4, 1e-3, 100, "max ESR at 100 Hz", False),
    by_prefix={"MAL2057": dict(ncols=5,
        ir=(0, 1.0, 100, "100 Hz, 85 deg C"),
        esr=(3, 1e-3, 100, "max ESR at 100 Hz", False))}),
 "28341": dict(series="159 PUL-SI", ncols=6,
    ir=(0, 1.0, 120, "120 Hz, 105 deg C"),
    esr=(3, 1e-3, 100, "max ESR at 100 Hz", False)),
 "28342": dict(series="058/059 PLL-SI", ncols=6,
    ir=(0, 1.0, 100, "100 Hz, 105 deg C"),
    esr=(4, 1e-3, 100, "max ESR at 100 Hz", False),
    by_prefix={"MAL2059": dict(ncols=5,
        ir=(0, 1.0, 100, "100 Hz, 105 deg C"),
        esr=(3, 1e-3, 100, "max ESR at 100 Hz", False))}),
 "28371": dict(series="101/102 PHR-ST", ncols=4,
    ir=(0, 1.0, 100, "100 Hz, 85 deg C"),
    esr=(2, 1e-3, 100, "max ESR at 100 Hz", False)),
 "28375": dict(series="158 PUL-SI", ncols=5,
    ir=(0, 1.0, 100, "100 Hz, 105 deg C"),
    esr=(3, 1e-3, 100, "max ESR at 100 Hz", False)),
 "28384": dict(series="106 PED-ST", ncols=4,
    ir=(0, 1.0, 100, "100 Hz, 85 deg C"),
    esr=(2, 1e-3, 100, "max ESR at 100 Hz", False)),
 "28389": dict(series="104 PHL-ST", ncols=4,
    ir=(0, 1.0, 100, "100 Hz, 105 deg C"),
    esr=(2, 1e-3, 100, "max ESR at 100 Hz", False)),
 "28390": dict(series="500 PGP-ST", ncols=5,
    ir=(0, 1.0, 100, "100 Hz, 85 deg C"),
    esr=(3, 1e-3, 100, "max ESR at 100 Hz", False)),
 "28392": dict(series="096 PLL-4TSI", ncols=4,
    ir=(0, 1.0, 100, "100 Hz, 85 deg C"),
    esr=(2, 1e-3, 100, "ESR at 100 Hz", False)),
 "28401": dict(series="146 RTI", ncols=5,
    ir=(0, 1e-3, 100000, "100 kHz, 125 deg C"),
    esr=(3, 1.0, 100000, "max impedance Z at 100 kHz, +20 deg C", True)),
 "28402": dict(series="142 RHS", ncols=3,
    ir=(0, 1e-3, 100, "100 Hz, 105 deg C"),
    esr=None),
 "28441": dict(series="259 PHM-SI", ncols=5,
    ir=(0, 1.0, 100, "100 Hz, 105 deg C"),
    esr=(3, 1e-3, 100, "max ESR at 100 Hz", False)),
 "28460": dict(series="257 PRM-SI", ncols=6,
    ir=(0, 1.0, 100, "100 Hz, 85 deg C"),
    esr=(3, 1e-3, 100, "max ESR at 100 Hz", False)),
 "28462": dict(series="170 RVZ", ncols=5,
    ir=(0, 1e-3, 100000, "100 kHz, 105 deg C"),
    esr=(3, 1.0, 100000, "max impedance Z at 100 kHz, +20 deg C", True)),
 "28465": dict(series="190 RTL", ncols=4,
    ir=(0, 1e-3, 100000, "100 kHz, 125 deg C"),
    esr=(3, 1.0, 100000, "max impedance Z at 100 kHz", True)),
 "28467": dict(series="202 PML-ST", ncols=5,
    ir=(0, 1.0, 100, "100 Hz, 85 deg C"),
    esr=(3, 1e-3, 100, "max ESR at 100 Hz", False)),
 "28499": dict(series="172 RLX", ncols=6,
    ir=(0, 1e-3, 100000, "100 kHz, 105 deg C"),
    esr=(3, 1.0, 100000, "max impedance Z at 100 kHz, +20 deg C", True)),
 "28581": dict(series="269 PLT-SI", ncols=3,
    ir=(0, 1.0, 100, "100 Hz, 105 deg C"),
    esr=None),
 # ---- batch 2 ----
 "28366": dict(series="132/133 ALL-DIN", ncols=6, ncols_min=5, fixed_prefix="MAL2", suffix_len=8,
    ir=(0, 1e-3, 100, "100 Hz, 85 deg C"),
    esr=(3, 1.0, 100, "max ESR at 100 Hz", False)),
 "28337": dict(series="156 PUM-SI", ncols=4,
    ir=(0, 1.0, 100, "100 Hz, 85 deg C"),
    esr=(2, 1e-3, 100, "max ESR at 100 Hz", False)),
 "28335": dict(series="119 AHT-DIN", ncols=7,
    ir=(1, 1e-3, 100, "100 Hz, 125 deg C"),
    esr=(5, 1.0, 100, "max ESR at 100 Hz", False)),
 "28345": dict(series="050/052 PED-PW", ncols=6,
    ir=(0, 1.0, 100, "100 Hz, 85 deg C"),
    esr=(4, 1e-3, 100, "max ESR at 100 Hz", False)),
 "28387": dict(series="090 PUL-SI", ncols=5,
    ir=(0, 1.0, 100, "100 Hz, 105 deg C"),
    esr=(3, 1e-3, 100, "max ESR at 100 Hz", False)),
 "28322": dict(series="140 RTM", ncols=5,
    ir=(0, 1e-3, 100000, "100 kHz, 125 deg C"),
    esr=(3, 1.0, 100000, "max impedance Z at 100 kHz, +20 deg C", True)),
 "28456": dict(series="501 PGM-ST", ncols=5,
    ir=(0, 1.0, 100, "100 Hz, 85 deg C"),
    esr=(3, 1e-3, 100, "max ESR at 100 Hz", False)),
 "28327": dict(series="030/031 AS", ncols=7, alpha_casecode=True, fixed_prefix="MAL2", suffix_len=8,
    ir=(1, 1e-3, 100, "100 Hz, 85 deg C"),
    esr=(5, 1.0, 100, "max ESR at 100 Hz", False)),
 "28458": dict(series="193 PUR-SI", ncols=5,
    ir=(0, 1.0, 100, "100 Hz, 105 deg C"),
    esr=(3, 1e-3, 100, "max ESR at 100 Hz", False)),
 "28395": dict(series="150 CRZ", ncols=5,
    ir=(0, 1e-3, 100000, "100 kHz, 105 deg C"),
    esr=(3, 1.0, 100000, "max impedance Z at 100 kHz, 20 deg C", True)),
 "28422": dict(series="246 RTI-V", ncols=5,
    ir=(0, 1e-3, 100000, "100 kHz, 125 deg C"),
    esr=(3, 1.0, 100000, "max impedance Z at 100 kHz, +20 deg C", True)),
 "28318": dict(series="048 RML", ncols=4,
    ir=(0, 1e-3, 100, "100 Hz, 105 deg C"),
    esr=(3, 1e-3, 100000, "max impedance Z at 100 kHz", True)),
 "28464": dict(series="125 ALS", ncols=5,
    ir=(0, 1e-3, 10000, "10 kHz, 105 deg C"),
    esr=(3, 1.0, 100, "ESR at 100 Hz", False)),
 "28570": dict(series="126 ALX", ncols=7,
    ir=(0, 1e-3, 10000, "10 kHz, 125 deg C"),
    esr=(4, 1.0, 100, "max ESR at 100 Hz", False)),
 "28437": dict(series="184 CPNS", ncols=4,
    ir=(0, 1e-3, 100000, "100 kHz, 105 deg C"),
    esr=(3, 1e-3, 100000, "ESR at 100 kHz, 20 deg C", False)),
 "28382": dict(series="094 PME-SI", ncols=4,
    ir=(0, 1.0, 120, "120 Hz, 105 deg C"),
    esr=(2, 1.0, 120, "max ESR at 120 Hz", False)),
 "28393": dict(series="095 PLL-4TSI", ncols=4,
    ir=(0, 1.0, 100, "100 Hz, 85 deg C"),
    esr=(2, 1e-3, 100, "max ESR at 100 Hz", False)),
 "28329": dict(series="041 ASH, 042 ASH, 043 ASH", ncols=7, fixed_prefix="MAL2", suffix_len=8,
    ir=(1, 1e-3, 100, "100 Hz, 85 deg C"),
    esr=(5, 1.0, 100, "max ESR at 100 Hz", False)),
 "28320": dict(series="152 RMH", ncols=4,
    ir=(0, 1e-3, 100, "100 Hz, 105 deg C"),
    esr=(3, 1.0, 10000, "max impedance Z at 10 kHz", True)),
 "28339": dict(series="198 PHR-SI", ncols=7,
    ir=(0, 1.0, 100, "100 Hz, 85 deg C"),
    esr=(4, 1e-3, 100, "max ESR at 100 Hz", False)),
 "28347": dict(series="162/163 PLL-PW", ncols=5,
    ir=(0, 1.0, 100, "100 Hz, 105 deg C"),
    esr=(3, 1e-3, 100, "max ESR at 100 Hz", False),
    by_prefix={"MAL2163": dict(ncols=5,
        ir=(0, 1.0, 100, "100 Hz, 105 deg C"),
        esr=(3, 1e-3, 100, "max ESR at 100 Hz", False))}),
 "28420": dict(series="160 RLA", ncols=5,
    ir=(0, 1e-3, 100000, "100 kHz, 150 deg C"),
    esr=(3, 1.0, 100000, "max impedance Z at 100 kHz, +20 deg C", True)),
 "28396": dict(series="140 CRH", ncols=4,
    ir=(0, 1e-3, 100000, "100 kHz, 125 deg C"),
    esr=(3, 1.0, 100000, "max impedance Z at 100 kHz, 20 deg C", True)),
 "28432": dict(series="299 PHL-4TSI", ncols=5,
    ir=(0, 1.0, 100, "100 Hz, 105 deg C"),
    esr=(3, 1e-3, 100, "max ESR at 100 Hz", False)),
 "28383": dict(series="093 PMG-SI", ncols=4,
    ir=(0, 1.0, 120, "120 Hz, 85 deg C"),
    esr=(2, 1.0, 120, "max ESR at 120 Hz", False)),
 "28346": dict(series="051/053 PEC-PW", ncols=6,
    ir=(0, 1.0, 100, "100 Hz, 85 deg C"),
    esr=(4, 1e-3, 100, "max ESR at 100 Hz", False)),
 "28577": dict(series="142 CVZ", ncols=4,
    ir=(0, 1e-3, 100000, "100 kHz, 105 deg C"),
    esr=(2, 1.0, 100000, "max impedance Z at 100 kHz, +20 deg C", True)),
 "28403": dict(series="146 CTI", ncols=5,
    ir=(0, 1e-3, 100000, "100 kHz, 125 deg C"),
    esr=(3, 1.0, 100000, "max impedance Z at 100 kHz, 20 deg C", True)),
 "28423": dict(series="250 RMI-V", ncols=5,
    ir=(0, 1e-3, 100000, "100 kHz, 105 deg C"),
    esr=(3, 1.0, 100000, "max impedance Z at 100 kHz, +20 deg C", True)),
 "28312": dict(series="036 RSP", ncols=4,
    ir=(0, 1e-3, 100, "100 Hz, 85 deg C"),
    esr=(3, 1.0, 10000, "max impedance Z at 10 kHz", True)),
 "28336": dict(series="120 ATC", ncols=8,
    ir=(0, 1e-3, 10000, "10 kHz, 125 deg C"),
    esr=(4, 1e-3, 100, "max ESR at 100 Hz", False)),
 "28313": dict(series="013 RLC", ncols=4,
    ir=(0, 1e-3, 100, "100 Hz, 85 deg C"),
    esr=(3, 1.0, 10000, "max impedance Z at 10 kHz", True)),
 "28316": dict(series="116 RLL", ncols=4,
    ir=(0, 1e-3, 100000, "100 kHz, 105 deg C"),
    esr=(3, 1.0, 100000, "max impedance Z at 100 kHz", True)),
}

SUFFIX = re.compile(r"^\d{5}E3$")
CODE = re.compile(r"^(MF\d|L\d)$")
PREFIX = re.compile(r"MAL2(\d{3})\s*\.")
NUM = re.compile(r"^\d+(\.\d+)?$")


def parse_doc(doc):
    cfg = CFG[doc]
    suffix_re = re.compile(rf"^\d{{{cfg.get('suffix_len', 5)}}}E3$")
    rows = []          # (mpn, ir_A, esr_Ohm_or_None, case, raw_line, subcfg)
    rejected = []
    prefix = cfg.get("fixed_prefix")
    with open(f"{DS_DIR}/{doc}.txt", errors="ignore") as fh:
        lines = fh.readlines()
    for raw in lines:
        if "fixed_prefix" not in cfg:
            m = PREFIX.search(raw)
            if m:
                prefix = "MAL2" + m.group(1)
        toks = raw.split()
        if len(toks) < 6:
            continue
        subcfg = cfg.get("by_prefix", {}).get(prefix, cfg)
        # locate case size 'D x L' (or chip 'L x W x H')
        ci = None
        for i in range(1, len(toks) - 1):
            if toks[i] == "x" and NUM.match(toks[i-1]) and NUM.match(toks[i+1]):
                ci = i
                break
        if ci is None:
            continue
        if ci + 3 < len(toks) and toks[ci+2] == "x" and NUM.match(toks[ci+3]):
            case = f"{toks[ci-1]}x{toks[ci+1]}x{toks[ci+3]} mm"
            ci += 2
        else:
            case = f"{toks[ci-1]}x{toks[ci+1]} mm"
        after = toks[ci+2:]
        # find first suffix token
        si = next((j for j, t in enumerate(after) if suffix_re.match(t)), None)
        if si is None:
            continue
        nums = [t.replace(",", ".") for t in after[:si]]
        # strip trailing freq/life codes and '-'/'n/a' placeholders
        while nums and (nums[-1] in ("-", "n/a") or CODE.match(nums[-1])):
            nums.pop()
        ncols_min = subcfg.get("ncols_min", subcfg["ncols"])
        check = nums[1:] if subcfg.get("alpha_casecode") else nums
        if not (ncols_min <= len(nums) <= subcfg["ncols"]) or not all(NUM.match(t) for t in check):
            rejected.append(raw.rstrip())
            continue
        if prefix is None:
            rejected.append("NO PREFIX: " + raw.rstrip())
            continue
        suffixes = [t for t in after[si:] if suffix_re.match(t)]
        ii, isc, _ifreq, _icond = subcfg["ir"]
        ir_A = float(nums[ii]) * isc
        esr = None
        if subcfg["esr"]:
            ei, esc, _efreq, _elabel, _is_z = subcfg["esr"]
            esr = float(nums[ei]) * esc
        for s in suffixes:
            rows.append((prefix + s, ir_A, esr, case, raw.rstrip(), subcfg, s))
    return rows, rejected


def main():
    # TAS index: which Vishay MAL2/other MPNs exist and what they're missing
    tas = {}
    with open(TAS) as tas_fh:
        for line in tas_fh:
            line = line.strip()
            if not line:
                continue
            try:
                c = json.loads(line)["capacitor"]
            except Exception:
                continue
            mi = c.get("manufacturerInfo", {})
            dsi = mi.get("datasheetInfo", {})
            pn = dsi.get("part", {}).get("partNumber")
            el = dsi.get("electrical", {})
            if pn:
                tas[pn] = (el.get("esr") is not None, el.get("rippleCurrent") is not None)

    out = open(OUT, "a")  # noqa: SIM115 — closed explicitly after the doc loop
    stats = {}
    samples_for_verify = {}
    seen = set()
    for doc, cfg in sorted(CFG.items()):
        rows, rejected = parse_doc(doc)
        url = URLS[doc]
        n_pdf = len(rows)
        n_match = n_patch = 0
        patched_rows = []
        for mpn, ir_A, esr, case, raw, subcfg, sfx in rows:
            if mpn in seen:
                continue
            if mpn not in tas:
                continue
            n_match += 1
            has_esr, has_rc = tas[mpn]
            if has_esr and has_rc:
                continue
            seen.add(mpn)
            _ii, _isc, ifreq, icond = subcfg["ir"]
            st = {
                "manufacturerInfo.datasheetInfo.electrical.rippleCurrent": round(ir_A, 6),
                "manufacturerInfo.datasheetInfo.electrical.rippleCurrentFrequency": ifreq,
            }
            ev = (f"Vishay {cfg['series']} datasheet, 'Electrical Data and Ordering "
                  f"Information' table, row with ordering-code suffix {sfx} "
                  f"(case {case}): rated ripple current {ir_A:g} A RMS at {icond}")
            if esr is not None and subcfg["esr"]:
                _ei, _esc, efreq, elabel, is_z = subcfg["esr"]
                st["manufacturerInfo.datasheetInfo.electrical.esr"] = round(esr, 6)
                st["manufacturerInfo.datasheetInfo.electrical.esrFrequency"] = efreq
                ev += f"; {elabel} = {esr:g} Ohm"
                if is_z:
                    ev += " (datasheet specifies max impedance Z, not ESR)"
            rec = {"category": "capacitors", "mpn": mpn, "set": st,
                   "source": url, "evidence": ev}
            out.write(json.dumps(rec) + "\n")
            patched_rows.append((mpn, raw))
            n_patch += 1
        stats[doc] = (cfg["series"], n_pdf, n_match, n_patch, len(rejected))
        if patched_rows:
            samples_for_verify[doc] = random.sample(patched_rows, min(3, len(patched_rows)))
        if rejected and doc in ("28402", "28371"):
            pass
    out.close()

    print(f"{'doc':>6} {'series':<18} {'pdf_codes':>9} {'in_TAS':>7} {'patched':>8} {'rej_lines':>9}")
    tot = 0
    for doc, (s, a, b, c, r) in sorted(stats.items()):
        print(f"{doc:>6} {s:<18} {a:>9} {b:>7} {c:>8} {r:>9}")
        tot += c
    print("TOTAL patched:", tot)
    with open("/tmp/verify_samples.json", "w") as verify_fh:
        json.dump(dict(samples_for_verify.items()), verify_fh, indent=1)


if __name__ == "__main__":
    random.seed(42)
    main()
