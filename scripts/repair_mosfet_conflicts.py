#!/usr/bin/env python3
"""One-off repair of the 6 TAS mosfet rows whose datasheetInfo existed BOTH at
the mosfet body root AND under manufacturerInfo with contradictory values
(left untouched by migrate_mosfet_structure.py --leave-conflicts).

For each row the two fragments are unioned and every conflicting (or
dict-shaped-where-schema-wants-a-number, or datasheet-contradicted) field is
set to the value literally read from the manufacturer datasheet fetched on
2026-06-12. The merged result lands under manufacturerInfo.datasheetInfo and
the body-root fragment is deleted. All other rows stay byte-identical.

Corrected values and their sources (page/table per field):

| part        | field                                  | value                | datasheet evidence |
|-------------|----------------------------------------|----------------------|--------------------|
| IPP60R190P6 | electrical.gateThresholdVoltage        | 3.5/4.0/4.5 V        | P6 [1] Electrical characteristics: "Gate threshold voltage V(GS)th 3.5 4.0 4.5 V, VDS=VGS, ID=0.63mA" (root fragment was right; MI min=3.0 was wrong) |
| IPP60R190P6 | electrical.bodyDiodeForwardVoltage     | 0.9 V                | P6 [1]: "Diode forward voltage VSD - 0.9 - V, VGS=0V, IF=9.5A, Tj=25C" (root {typ:102,max:103} is garbage) |
| IPP60R190P6 | electrical.inputCapacitance            | 1.75e-9 F            | P6 [1]: "Input capacitance Ciss - 1750 - pF, VGS=0V, VDS=100V, f=1MHz" (MI 1.7e-9 was wrong) |
| IPP60R190P6 | electrical.outputCapacitance           | 7.6e-11 F            | P6 [1]: "Output capacitance Coss - 76 - pF, VGS=0V, VDS=100V, f=1MHz" (number, not {typical:...}) |
| IPP60R190P6 | electrical.capacitanceMeasurementVds   | 100 V                | P6 [1]: capacitance test condition is VDS=100V (MI said 400) |
| IPA60R190P6 | (same five as IPP60R190P6)             | same                 | same shared datasheet [1] |
| IPA60R190P6 | electrical.continuousDrainCurrent      | 20.2 A               | P6 [1] Table 2 Maximum ratings: "Continuous drain current ID - - 20.2 A, TC=25C" (MI 9.5 was the Qg test-condition current, not ID) |
| IPP60R099P7 | electrical.bodyDiodeForwardVoltage     | 0.9 V                | [2] Rev2.1 p.7 Reverse diode: "Diode forward voltage VSD - 0.9 - V, VGS=0V, IF=10.5A, Tj=25C" (root {typ:125,max:150} garbage) |
| IPP60R099P7 | electrical.gateThresholdVoltage        | 3.0/3.5/4.0 V        | [2]: "Gate threshold voltage V(GS)th 3 3.5 4 V, VDS=VGS, ID=0.53mA" (fragments agreed; kept) |
| IPP60R099P7 | electrical.inputCapacitance            | 1.952e-9 F           | [2]: "Input capacitance Ciss - 1952 - pF, VGS=0V, VDS=400V, f=250kHz" |
| IPP60R099P7 | electrical.outputCapacitance           | 3.3e-11 F            | [2]: "Output capacitance Coss - 33 - pF, VGS=0V, VDS=400V, f=250kHz" |
| IPP60R099P7 | electrical.capacitanceMeasurementVds   | 400 V                | [2]: capacitance test condition VDS=400V |
| IPP60R080P7 | same five fields                       | Vf 0.9; Vth 3/3.5/4 (ID=0.59mA); Ciss 2.18e-9; Coss 3.7e-11; capVds 400 | [3] Rev2.1, same tables |
| IPP60R060P7 | same five fields                       | Vf 0.9; Vth 3/3.5/4 (ID=0.8mA); Ciss 2.895e-9; Coss 4.8e-11; capVds 400 | [4] Rev2.1, same tables |
| IPP60R280C6 | part.series                            | "CoolMOS C6"         | [5] Rev2.3 cover: "600V CoolMOS C6 Power Transistor IPx60R280C6" (MI said "CoolMOS C7" - P7-sheet contamination; row datasheetUrl pointed at the ipp60r280p7 PDF) |
| IPP60R280C6 | electrical.gateThresholdVoltage        | 2.5/3.0/3.5 V        | [5] Table 6 p.6: "Gate threshold voltage VGS(th) 2.5 3 3.5 V, VGS=VDS, ID=0.43mA" (MI 1.5/2.5/3.5 is the P7 value) |
| IPP60R280C6 | electrical.continuousDrainCurrent      | 13.8 A               | [5] Table 2 p.4: "Continuous drain current ID - - 13.8 A, TC=25C" (MI 28 wrong) |
| IPP60R280C6 | electrical.pulsedDrainCurrent          | 40 A                 | [5] Table 2 p.4: "Pulsed drain current ID,pulse - - 40 A, TC=25C" |
| IPP60R280C6 | electrical.onResistanceId              | 6.5 A                | [5] Table 6 p.6: "RDS(on) - 0.25 0.28 Ohm, VGS=10V, ID=6.5A" (MI 14 is the P7 condition) |
| IPP60R280C6 | electrical.gateSourceVoltageMax        | 20 V                 | [5] Table 2 p.4: "Gate source voltage VGS -20 - 20 V static" |
| IPP60R280C6 | electrical.powerDissipation            | 104 W                | [5] Table 2 p.4: "Power dissipation for TO-220... Ptot - - 104 W, TC=25C" |
| IPP60R280C6 | electrical.totalGateCharge             | 4.3e-8 C             | [5] Table 8 p.7: "Gate charge total Qg - 43 - nC, VDD=480V, ID=6.5A, VGS=0 to 10V" (MI agreed; confirmed) |
| IPP60R280C6 | electrical.inputCapacitance            | 9.5e-10 F            | [5] Table 7 p.6: "Input capacitance Ciss - 950 - pF, VGS=0V, VDS=100V, f=1MHz" (root 7.61e-10 is the P7 value) |
| IPP60R280C6 | electrical.outputCapacitance           | 6.0e-11 F            | [5] Table 7 p.6: "Output capacitance Coss - 60 - pF" |
| IPP60R280C6 | electrical.capacitanceMeasurementVds   | 100 V                | [5] Table 7 p.6: capacitance condition VDS=100V |
| IPP60R280C6 | electrical.bodyDiodeForwardVoltage     | 0.9 V                | [5] Table 9 p.7: "Diode forward voltage VSD - 0.9 - V, VGS=0V, IF=6.5A, Tj=25C" |
| IPP60R280C6 | thermal.junctionTemperatureMax         | 150 C                | [5] Table 2 p.4: "Operating and storage temperature Tj,Tstg -55 - 150 C" (MI 175 is the P7 value) |
| IPP60R280C6 | thermal.thermalResistanceJunctionCase  | 1.2 C/W              | [5] Table 3 p.5 (TO-220 IPP60R280C6): "RthJC - - 1.2" |
| IPP60R280C6 | thermal.thermalResistanceJunctionAmbient | 62 C/W             | [5] Table 3 p.5: "RthJA - - 62, leaded" |
| IPP60R280C6 | manufacturerInfo.datasheetUrl          | [5]                  | row pointed at the IPP60R280P7 datasheet; replaced with the real C6 sheet |

Datasheet sources (all fetched 2026-06-12, curl, infineon.com):
[1] https://www.infineon.com/assets/row/public/documents/24/49/infineon-ipx60r190p6-ds-en.pdf  (IPx60R190P6, Rev 2.2, 2015-07-10; covers IPP60R190P6 + IPA60R190P6)
[2] https://www.infineon.com/dgdl/Infineon-IPP60R099P7-DS-v02_01-EN.pdf  (Rev 2.1, 2018-05-15)
[3] https://www.infineon.com/dgdl/Infineon-IPP60R080P7-DS-v02_01-EN.pdf  (Rev 2.1, 2018-05-15)
[4] https://www.infineon.com/dgdl/Infineon-IPP60R060P7-DS-v02_01-EN.pdf  (Rev 2.1, 2018-05-15)
[5] https://www.infineon.com/assets/row/public/documents/24/49/infineon-ipp60r280c6-ds-en.pdf  (IPx60R280C6, Rev 2.3, 2018-02-26)

Run from the Heaviside repo root:
    .venv-web/bin/python scripts/repair_mosfet_conflicts.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "TAS" / "data" / "mosfets.ndjson"

URL_P6 = "https://www.infineon.com/assets/row/public/documents/24/49/infineon-ipx60r190p6-ds-en.pdf"
URL_099 = "https://www.infineon.com/dgdl/Infineon-IPP60R099P7-DS-v02_01-EN.pdf"
URL_080 = "https://www.infineon.com/dgdl/Infineon-IPP60R080P7-DS-v02_01-EN.pdf"
URL_060 = "https://www.infineon.com/dgdl/Infineon-IPP60R060P7-DS-v02_01-EN.pdf"
URL_C6 = "https://www.infineon.com/assets/row/public/documents/24/49/infineon-ipp60r280c6-ds-en.pdf"

_P6_COMMON = {
    "electrical.gateThresholdVoltage": {"minimum": 3.5, "nominal": 4.0, "maximum": 4.5},
    "electrical.bodyDiodeForwardVoltage": 0.9,
    "electrical.inputCapacitance": 1.75e-09,
    "electrical.outputCapacitance": 7.6e-11,
    "electrical.capacitanceMeasurementVds": 100,
    # Table 2 Maximum ratings: "Pulsed drain current ID,pulse - - 57 A, TC=25C"
    # (IPP row carried 60.6, the 3x-ID rule of thumb, not the datasheet value)
    "electrical.pulsedDrainCurrent": 57,
}

# part reference -> (datasheet url, {dotted datasheetInfo path: datasheet value})
OVERRIDES: dict[str, tuple[str, dict[str, object]]] = {
    "IPP60R190P6": (URL_P6, dict(_P6_COMMON)),
    "IPA60R190P6": (URL_P6, {**_P6_COMMON, "electrical.continuousDrainCurrent": 20.2}),
    "IPP60R099P7": (URL_099, {
        "electrical.gateThresholdVoltage": {"minimum": 3.0, "nominal": 3.5, "maximum": 4.0},
        "electrical.bodyDiodeForwardVoltage": 0.9,
        "electrical.inputCapacitance": 1.952e-09,
        "electrical.outputCapacitance": 3.3e-11,
        "electrical.capacitanceMeasurementVds": 400,
    }),
    "IPP60R080P7": (URL_080, {
        "electrical.gateThresholdVoltage": {"minimum": 3.0, "nominal": 3.5, "maximum": 4.0},
        "electrical.bodyDiodeForwardVoltage": 0.9,
        "electrical.inputCapacitance": 2.18e-09,
        "electrical.outputCapacitance": 3.7e-11,
        "electrical.capacitanceMeasurementVds": 400,
    }),
    "IPP60R060P7": (URL_060, {
        "electrical.gateThresholdVoltage": {"minimum": 3.0, "nominal": 3.5, "maximum": 4.0},
        "electrical.bodyDiodeForwardVoltage": 0.9,
        "electrical.inputCapacitance": 2.895e-09,
        "electrical.outputCapacitance": 4.8e-11,
        "electrical.capacitanceMeasurementVds": 400,
    }),
    "IPP60R280C6": (URL_C6, {
        "part.series": "CoolMOS C6",
        "electrical.gateThresholdVoltage": {"minimum": 2.5, "nominal": 3.0, "maximum": 3.5},
        "electrical.continuousDrainCurrent": 13.8,
        "electrical.pulsedDrainCurrent": 40,
        "electrical.onResistanceId": 6.5,
        "electrical.gateSourceVoltageMax": 20,
        "electrical.powerDissipation": 104,
        "electrical.totalGateCharge": 4.3e-08,
        "electrical.inputCapacitance": 9.5e-10,
        "electrical.outputCapacitance": 6.0e-11,
        "electrical.capacitanceMeasurementVds": 100,
        "electrical.bodyDiodeForwardVoltage": 0.9,
        "thermal.junctionTemperatureMax": 150,
        "thermal.thermalResistanceJunctionCase": 1.2,
        "thermal.thermalResistanceJunctionAmbient": 62,
    }),
}


class RepairError(RuntimeError):
    pass


def _union(dst: dict, src: dict) -> None:
    """Merge src into dst recursively; nulls in src never overwrite; on a
    scalar disagreement keep dst (the OVERRIDES table is applied afterwards
    and must cover every such field — verified below)."""
    for key, val in src.items():
        if val is None:
            continue
        if key not in dst or dst[key] is None:
            dst[key] = val
        elif isinstance(dst[key], dict) and isinstance(val, dict):
            _union(dst[key], val)
        # else: keep dst; the override pass settles it


def _conflict_paths(a: dict, b: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for key in set(a) & set(b):
        va, vb = a[key], b[key]
        if va is None or vb is None:
            continue
        path = f"{prefix}{key}"
        if isinstance(va, dict) and isinstance(vb, dict):
            out |= _conflict_paths(va, vb, path + ".")
        elif va != vb or type(va) is not type(vb):
            out.add(path)
    return out


def _set_path(obj: dict, dotted: str, value: object) -> None:
    keys = dotted.split(".")
    for key in keys[:-1]:
        obj = obj.setdefault(key, {})
        if not isinstance(obj, dict):
            raise RepairError(f"path {dotted}: {key} is not an object")
    obj[keys[-1]] = value


def repair(path: Path) -> int:
    pending = dict(OVERRIDES)
    out_lines: list[str] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            row = json.loads(line)
            body = row.get("semiconductor", {}).get("mosfet")
            mi = (body or {}).get("manufacturerInfo") or {}
            ref = mi.get("reference")
            if ref not in pending or "datasheetInfo" not in (body or {}):
                out_lines.append(line.rstrip("\n"))
                continue

            url, overrides = pending.pop(ref)
            root_dsi = body["datasheetInfo"]
            mi_dsi = mi.get("datasheetInfo")
            if not isinstance(root_dsi, dict) or not isinstance(mi_dsi, dict):
                raise RepairError(f"line {lineno} ({ref}): unexpected fragment shape")

            # union of the two fragments...
            merged = json.loads(json.dumps(mi_dsi))
            _union(merged, root_dsi)
            # ...then force every datasheet-verified field
            for dotted, value in overrides.items():
                _set_path(merged, dotted, value)

            # safety: no field that disagreed between the fragments may remain
            # unsettled by the OVERRIDES table
            unsettled = {
                p for p in _conflict_paths(root_dsi, mi_dsi)
                if not any(p == o or p.startswith(o + ".") for o in overrides)
            }
            if unsettled:
                raise RepairError(
                    f"line {lineno} ({ref}): conflicting field(s) not covered by "
                    f"the datasheet table: {sorted(unsettled)}"
                )

            mi["datasheetInfo"] = merged
            del body["datasheetInfo"]
            if ref == "IPP60R280C6":
                # row pointed at the IPP60R280P7 datasheet (wrong part)
                mi["datasheetUrl"] = URL_C6
            out_lines.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            print(f"repaired line {lineno}: {ref}  ({url})")

    if pending:
        raise RepairError(f"conflict rows not found in dataset: {sorted(pending)}")

    tmp = path.with_suffix(".ndjson.repairing")
    tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    tmp.replace(path)
    print(f"done: 6 rows repaired, {len(out_lines) - 6} rows byte-identical")
    return 0


if __name__ == "__main__":
    sys.exit(repair(PATH))
