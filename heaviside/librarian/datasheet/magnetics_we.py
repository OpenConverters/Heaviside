"""Parse the electrical properties from a Würth Elektronik magnetics datasheet.

The bulk of the internal DB's Würth magnetics came from a vendor Access-DB export
that stored a single ``saturationCurrentPeak`` with NO %-drop definition, and the
values drifted from the current datasheets (e.g. 74438356015 had Isat 6.3 A,
Irms 5.8 A, DCR-max 22 mΩ where the datasheet says Isat 4.8 A@10% / 10.2 A@30%,
IRP,40K 8.6 A, RDC 16 mΩ typ / 19 mΩ max).

WE datasheets render the Electrical Properties as vector text, so pdfplumber
extracts clean lines like:

    Inductance L 100 kHz/ 10 mA 1.5 µH ±20%
    Performance Rated Current I RP,40K ΔT = 40K 8.6 A max.
    Saturation Current @ 10% I SAT, 10% |ΔL/L| < 10 % 4.8 A typ.
    Saturation Current @ 30% I SAT,30% |ΔL/L| < 30 % 10.2 A typ.
    DC Resistance R DC @ 20 °C 16 mΩ typ.
    DC Resistance R DC @ 20 °C 19 mΩ max.

This module turns that text into SI values WITH the Isat drop-definition attached,
so a corrector can store the authoritative numbers (and pick the conservative
10 %-drop Isat for the saturation gate). No fabrication: a field that isn't in the
text is simply absent from the result.
"""

from __future__ import annotations

import re

_A = {"a": 1.0, "ma": 1e-3}
_OHM = {"ω": 1.0, "mω": 1e-3, "µω": 1e-6, "uω": 1e-6, "kω": 1e3}
_H = {"h": 1.0, "mh": 1e-3, "µh": 1e-6, "uh": 1e-6, "nh": 1e-9, "ph": 1e-12}


def _num(unit_map: dict[str, float], value: str, unit: str) -> float | None:
    u = unit.strip().lower()
    if u not in unit_map:
        return None
    try:
        return float(value) * unit_map[u]
    except ValueError:
        return None


# Number followed by a current / resistance / inductance unit.
_CUR = re.compile(r"([\d.]+)\s*(mA|A)\b", re.I)
_RES = re.compile(r"([\d.]+)\s*(mΩ|µΩ|uΩ|kΩ|Ω)", re.I)
_IND = re.compile(r"([\d.]+)\s*(nH|µH|uH|mH|pH|H)\b", re.I)


def parse_we_magnetic_text(text: str) -> dict[str, float]:
    """Extract magnetic electrical parameters (SI units) from WE-datasheet text.

    Returns any of: ``inductance`` (H), ``tolerance`` (fraction), ``irp_40k`` (A,
    the temperature-rise rated current), ``isat_10pct`` / ``isat_20pct`` /
    ``isat_30pct`` (A, saturation current at that inductance-drop), ``rdc_typ`` /
    ``rdc_max`` (Ω). Absent fields are omitted (never guessed).
    """
    out: dict[str, float] = {}
    for raw in text.splitlines():
        line = raw.strip()
        low = line.lower()
        if "inductance" in low and "saturation" not in low and "inductance" not in out.get("_seen", ""):
            m = _IND.search(line)
            if m and "inductance" not in out:
                v = _num(_H, m.group(1), m.group(2))
                if v is not None:
                    out["inductance"] = v
                    tol = re.search(r"±\s*([\d.]+)\s*%", line)
                    if tol:
                        out["tolerance"] = float(tol.group(1)) / 100.0
        elif "rated current" in low or "irp" in low.replace(" ", "") or (
            low.replace(" ", "").startswith("ir,")
        ):
            m = _CUR.search(line)
            if m:
                v = _num(_A, m.group(1), m.group(2))
                if v is not None:
                    # Distinguish the STANDARD rated current (IR,40K) from the
                    # best-case PERFORMANCE rated current (IRP,40K), measured on a
                    # lab-grade 40 mm / 1000 µm copper plane. WE-XHMI lists both
                    # (IR,40K 13.2 A vs IRP,40K 19.35 A); quoting IRP as the rating
                    # overstates the usable current — a repeated FAE finding.
                    _compact = low.replace(" ", "")
                    if "performance" in low or "irp," in _compact or "irp4" in _compact:
                        out["irp_40k"] = v
                    else:  # "Rated Current IR,40K" (or a lone "Rated Current")
                        out["ir_40k"] = v
        elif "saturation current" in low:
            m = _CUR.search(line)
            if not m:
                continue
            v = _num(_A, m.group(1), m.group(2))
            if v is None:
                continue
            # The drop-% comes from the DEFINITION ("@ 10%" / "|ΔL/L| < 30 %"),
            # NOT from anywhere in the line — the value 10.2 also contains "10".
            drop = re.search(r"[@<]\s*(\d+)\s*%", line)
            if drop:
                out[f"isat_{drop.group(1)}pct"] = v
        elif "dc resistance" in low or low.startswith("r dc"):
            m = _RES.search(line)
            if not m:
                continue
            v = _num(_OHM, m.group(1), m.group(2))
            if v is None:
                continue
            if "max" in low:
                out["rdc_max"] = v
            elif "typ" in low or "nom" in low:
                out["rdc_typ"] = v
    out.pop("_seen", None)
    # The rated current to USE is the standard IR,40K when present; only fall
    # back to the performance IRP,40K when a part lists only that (WE-MAPI does).
    if "ir_40k" in out:
        out["rated_current"] = out["ir_40k"]
    elif "irp_40k" in out:
        out["rated_current"] = out["irp_40k"]
    return out


def extract_we_magnetic_pdf(pdf_path) -> dict[str, float]:
    """pdfplumber-extract page-1 text of a WE magnetics PDF and parse it."""
    import pdfplumber

    with pdfplumber.open(str(pdf_path)) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages[:1])
    return parse_we_magnetic_text(text)
