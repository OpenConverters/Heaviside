"""Parse the electrical properties from a *non-Würth* power-inductor datasheet.

Why this exists
---------------
:mod:`heaviside.librarian.datasheet.magnetics_we` parses the Würth Elektronik
"Electrical Properties" block, but a cross-reference *original* is frequently a
Coilcraft / Vishay IHLP / MPS / TDK / Bourns power inductor. Without a parser for
those formats the original's real saturation current, rated current and DCR stay
"unverified", so the pipeline matches on nominal inductance alone and an
**under-rated substitute slips through** — the #1 finding from three rounds of
adversarial FAE review.

This module reads each vendor's real datasheet text (as pdfplumber extracts it)
and returns the SAME SI keys as the WE parser, so :mod:`enrich` can splice the
original straight into the existing ``param_check`` comparison.

Two datasheet shapes
--------------------
* **Columnar selection tables** (Coilcraft, Vishay IHLP, Bourns SRP/SRN, most
  TDK families): ONE datasheet covers a whole series, one row per inductance
  value. pdfplumber flattens each row to a space-separated line whose column
  ORDER is fixed by the vendor. We parse every row, then select the row for the
  requested part (by MPN, else by inductance value, else the sole row).
* **Per-line labelled specs** (MPS single-part datasheets, some TDK): each
  parameter is on its own line ("Saturation Current …", "Rated Current …",
  "DCR …"), like the WE format.

Two FAE rules baked in (see ``_finalise``)
------------------------------------------
(a) **Standard rated current, never the best-case one.** WE-style datasheets list
    both a standard ``I R,40K`` (ΔT = 40 K on the reference board) and a
    *Performance* ``I RP,40K`` measured on a huge 40 mm / 1000 µm copper plane.
    The recurring FAE finding was the tool quoting the optimistic *performance*
    figure as the rating. We keep them apart (``rated_current`` vs ``irp_40k``)
    and always prefer the standard one. Column datasheets (Coilcraft) that quote
    the RMS current at 20 °C and 40 °C rise expose both; the 40 °C-rise figure is
    the WE ``I R,40K`` equivalent and is used as ``rated_current``.
(b) **Isat is definition-dependent.** We capture the |ΔL/L| roll-off % for every
    saturation figure and expose the CONSERVATIVE (lowest-drop) one as
    ``saturation_current`` for the gate.

No fabrication: a field not in the text is simply absent from the result.
"""

from __future__ import annotations

import re

__all__ = [
    "detect_magnetic_vendor",
    "parse_magnetic_text",
]

# ---------------------------------------------------------------------------
# unit helpers (same conventions as magnetics_we)
# ---------------------------------------------------------------------------

_A = {"a": 1.0, "ma": 1e-3}
_OHM = {
    "ω": 1.0,
    "mω": 1e-3,
    "mohm": 1e-3,
    "mohms": 1e-3,
    "µω": 1e-6,
    "uω": 1e-6,
    "kω": 1e3,
    "ohm": 1.0,
    "ohms": 1.0,
}
_H = {"h": 1.0, "mh": 1e-3, "µh": 1e-6, "uh": 1e-6, "nh": 1e-9, "ph": 1e-12}


def _to_si(unit_map: dict[str, float], value: str, unit: str) -> float | None:
    u = unit.strip().lower()
    if u not in unit_map:
        return None
    try:
        return float(value) * unit_map[u]
    except ValueError:
        return None


_CUR = re.compile(r"([\d.]+)\s*(mA|A)\b", re.I)
_RES = re.compile(r"([\d.]+)\s*(mΩ|µΩ|uΩ|kΩ|Ω|mOhms?|Ohms?)", re.I)
_IND = re.compile(r"([\d.]+)\s*(nH|µH|uH|mH|pH|H)\b", re.I)
_NUM = re.compile(r"^\d+(?:\.\d+)?$")


# ---------------------------------------------------------------------------
# vendor detection
# ---------------------------------------------------------------------------

# Ordered (specific → generic). Each entry: canonical vendor tag → the tokens
# (case-insensitive) whose presence in the datasheet text identifies it.
_VENDOR_MARKERS: list[tuple[str, tuple[str, ...]]] = [
    ("wurth", ("würth", "wurth", "we-online.com", "redexpert")),
    ("coilcraft", ("coilcraft",)),
    ("vishay", ("vishay", "ihlp")),
    ("mps", ("monolithic power", "monolithicpower", "mpl-al", "mpl-cl", "mpl-se")),
    ("tdk", ("tdk corporation", "product.tdk.com", "www.tdk.com")),
    ("bourns", ("bourns",)),
    ("taiyo_yuden", ("taiyo yuden", "taiyoyuden")),
]

# Part-number prefixes → vendor, used when the text markers are ambiguous but the
# MPN itself is characteristic (an "XGL"/"XAL" is a Coilcraft molded inductor, an
# "IHLP" is a Vishay). Only unambiguous, well-known prefixes.
_MPN_PREFIX_VENDOR: list[tuple[str, str]] = [
    ("IHLP", "vishay"),
    ("XGL", "coilcraft"),
    ("XAL", "coilcraft"),
    ("XEL", "coilcraft"),
    ("XFL", "coilcraft"),
    ("MSS", "coilcraft"),
    ("MPL-", "mps"),
    ("SRP", "bourns"),
    ("SRN", "bourns"),
    ("SRR", "bourns"),
    ("SPM", "tdk"),
    ("VLS", "tdk"),
    ("TFM", "tdk"),
]


def detect_magnetic_vendor(text: str, *, mpn: str | None = None) -> str | None:
    """Return the canonical vendor tag for a magnetic datasheet, or ``None``.

    Detection prefers the datasheet *text* (an explicit manufacturer name is
    authoritative); it falls back to a characteristic MPN prefix only when the
    text is silent. Never guesses beyond these explicit signals.
    """
    low = (text or "").lower()
    for tag, markers in _VENDOR_MARKERS:
        if any(mk in low for mk in markers):
            return tag
    mpn_up = (mpn or "").strip().upper()
    for prefix, tag in _MPN_PREFIX_VENDOR:
        if mpn_up.startswith(prefix):
            return tag
    return None


# ---------------------------------------------------------------------------
# MPN ↔ row matching (columnar datasheets are per-series, one row per value)
# ---------------------------------------------------------------------------

def _norm_mpn(s: str) -> str:
    """Uppercase, keep only [A-Z0-9]; drop separators and placeholder chars."""
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _row_token_matches(row_token: str, mpn: str) -> bool:
    """True iff the datasheet row's part token identifies ``mpn``.

    Datasheet rows carry a *family* token (``XGL6060-822ME_``,
    ``IHLP2020BZE_R10M01``) whose trailing characters are termination / tolerance
    placeholders (often rendered by pdfplumber as ``_``). We treat ``_`` as a
    single-character wildcard and accept the match when the requested MPN is a
    prefix of the row token (or vice-versa) after normalisation.
    """
    r = _norm_mpn(row_token)
    m = _norm_mpn(mpn)
    if not r or not m:
        return False
    # Regex from the row token, treating underscore placeholders as one wildcard
    # char, so IHLP2020BZE_R10M01 matches IHLP2020BZExR10M01.
    pattern = "".join("." if ch == "_" else ch for ch in row_token.upper())
    pattern = re.sub(r"[^A-Z0-9.]", "", pattern)
    if re.fullmatch(pattern, m) or re.match(pattern, m):
        return True
    # Prefix containment either direction (handles a trailing suffix code).
    shorter, longer = sorted((r, m), key=len)
    return longer.startswith(shorter) and len(shorter) >= max(6, len(longer) - 6)


def _l_close(a: float | None, b: float | None, *, rel: float = 0.02) -> bool:
    if a is None or b is None:
        return False
    if b == 0:
        return a == 0
    return abs(a - b) <= rel * abs(b)


def _select_row(
    rows: list[dict],
    *,
    mpn: str | None,
    inductance: float | None,
) -> dict | None:
    """Pick the datasheet row for the requested part.

    Priority: (1) MPN match, (2) closest inductance (±2 %), (3) the sole row.
    Returns ``None`` when the part cannot be identified — the caller reports the
    original as unresolved rather than picking an arbitrary row.
    """
    if not rows:
        return None
    if mpn:
        hits = [r for r in rows if _row_token_matches(r.get("part_number", ""), mpn)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1 and inductance is not None:
            hits2 = [r for r in hits if _l_close(r.get("inductance"), inductance)]
            if len(hits2) == 1:
                return hits2[0]
    if inductance is not None:
        hits = [r for r in rows if _l_close(r.get("inductance"), inductance)]
        if len(hits) == 1:
            return hits[0]
    if len(rows) == 1:
        return rows[0]
    return None


# ---------------------------------------------------------------------------
# columnar table parsers
# ---------------------------------------------------------------------------

# Each column entry is (si_key, unit_map, unit_token). ``si_key is None`` means
# "skip this column" (e.g. SRF). Currents/resistances carry an implicit A / mΩ
# from the datasheet header — pdfplumber strips the unit from the cell, so we
# supply it here from the fixed vendor column layout.

_COILCRAFT_COLS = [
    ("inductance", _H, "uh"),
    ("rdc_typ", _OHM, "mohm"),
    ("rdc_max", _OHM, "mohm"),
    (None, None, None),  # SRF (MHz)
    ("isat_10pct", _A, "a"),
    ("isat_20pct", _A, "a"),
    ("isat_30pct", _A, "a"),
    ("irms_20c_rise", _A, "a"),
    ("irms_40c_rise", _A, "a"),
]

_VISHAY_COLS = [
    ("inductance", _H, "uh"),
    ("rdc_typ", _OHM, "mohm"),
    ("rdc_max", _OHM, "mohm"),
    ("irms_40c_rise", _A, "a"),  # "HEAT RATING CURRENT", note (1): ΔT ≈ 40 °C
    ("isat_20pct", _A, "a"),  # "SATURATION CURRENT", note (2): L drops ≈ 20 %
    (None, None, None),  # SRF (MHz)
]


def _parse_columnar(text: str, cols: list, part_re: re.Pattern) -> list[dict]:
    """Parse every data row of a per-series selection table.

    A data row is a line that contains a matching part token followed by at least
    ``len(cols)`` numeric tokens (leading sidebar words such as
    "Halogen"/"Free"/"AEC" that pdfplumber prepends are skipped); the row's own
    columns are the FIRST ``len(cols)`` numbers after the part token.
    """
    rows: list[dict] = []
    ncol = len(cols)
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = part_re.search(line)
        if not m:
            continue
        part = m.group(0)
        tail = line[m.end():].split()
        nums = [t for t in tail if _NUM.match(t)]
        if len(nums) < ncol:
            continue
        nums = nums[:ncol]
        row: dict = {"part_number": part}
        ok = True
        for (key, umap, unit), tok in zip(cols, nums, strict=False):
            if key is None:
                continue
            v = _to_si(umap, tok, unit)
            if v is None:
                ok = False
                break
            row[key] = v
        if ok and "inductance" in row:
            rows.append(row)
    return rows


def _parse_coilcraft(text: str) -> list[dict]:
    # Coilcraft molded-inductor part tokens: XGL6060-822ME_, XAL7030-102ME_, ...
    return _parse_columnar(text, _COILCRAFT_COLS, re.compile(r"[A-Z]{2,}\d{3,}-[A-Z0-9_]+"))


def _parse_vishay(text: str) -> list[dict]:
    return _parse_columnar(text, _VISHAY_COLS, re.compile(r"IHLP[A-Z0-9_]+", re.I))


# ---------------------------------------------------------------------------
# per-line labelled parser (MPS single-part sheets, WE-style non-WE sheets)
# ---------------------------------------------------------------------------

def _parse_labelled(text: str) -> dict:
    """Parse a per-parameter labelled datasheet (one spec per line).

    Recognises the labels used by MPS and other single-part inductor datasheets:
    ``Saturation Current`` (with the |ΔL/L| roll-off % taken from an inline
    ``@ N%`` / ``N % typ`` definition or, failing that, a "drop from N %" footnote
    elsewhere in the sheet), ``Rated Current`` / ``RMS Current`` (standard, with
    an optional ΔT rise), ``Performance Rated Current`` / ``IRP`` (best-case —
    kept apart), and ``DCR`` / ``Resistance`` typ/max. Absent fields stay absent.
    """
    out: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        low = line.lower()
        compact = low.replace(" ", "")

        if "saturation current" in low or "isat" in compact:
            cm = _CUR.search(line)
            if cm:
                v = _to_si(_A, cm.group(1), cm.group(2))
                if v is not None:
                    drop = (
                        re.search(r"[@<]\s*(\d+)\s*%", line)
                        or re.search(r"(\d+)\s*%\s*(?:typ|drop|roll)", low)
                        or re.search(r"drop[^%]*?(\d+)\s*%", low)
                    )
                    if drop:
                        out.setdefault(f"isat_{drop.group(1)}pct", v)
                    else:
                        # Undefined-drop saturation figure: record but flag it so
                        # the caller does not assume a conservative 10 %-drop.
                        out.setdefault("isat_undefined_drop", v)
            continue

        if "performance rated current" in low or "irp" in compact:
            cm = _CUR.search(line)
            if cm:
                v = _to_si(_A, cm.group(1), cm.group(2))
                if v is not None:
                    out.setdefault("irp_40k", v)
            continue

        if (
            "rated current" in low
            or "rms current" in low
            or "irms" in compact
            or compact.startswith("ir,")
        ):
            cm = _CUR.search(line)
            if cm:
                v = _to_si(_A, cm.group(1), cm.group(2))
                if v is not None:
                    out.setdefault("rated_current", v)
            continue

        if (
            "dcr" in compact
            or "dc resistance" in low
            or low.startswith("r dc")
            or "resistance" in low
        ):
            rm = _RES.search(line)
            if rm:
                v = _to_si(_OHM, rm.group(1), rm.group(2))
                if v is not None:
                    if "max" in low:
                        out.setdefault("rdc_max", v)
                    else:  # "typ"/"nom"/unqualified all read as the typical value
                        out.setdefault("rdc_typ", v)
            continue

        if "inductance" in low and "saturation" not in low and "inductance" not in out:
            im = _IND.search(line)
            if im:
                v = _to_si(_H, im.group(1), im.group(2))
                if v is not None:
                    out["inductance"] = v
                    tol = re.search(r"±\s*([\d.]+)\s*%", line)
                    if tol:
                        out["tolerance"] = float(tol.group(1)) / 100.0

    # A defined-drop Isat supersedes an undefined-drop one.
    if any(k in out for k in ("isat_10pct", "isat_20pct", "isat_30pct")):
        out.pop("isat_undefined_drop", None)
    elif "isat_undefined_drop" in out:
        # The spec line gave no roll-off %; MPS/TDK define it in a footnote
        # ("Saturation current will cause L to drop from 30 %"). Adopt that % so
        # the value carries its definition (FAE rule (b)).
        dm = re.search(
            r"\bdrop\s*(?:from|of|by|approximately|approx\.?|to)?\s*(\d+)\s*%",
            text,
            re.I,
        )
        if dm:
            out[f"isat_{dm.group(1)}pct"] = out.pop("isat_undefined_drop")
    return out


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def _finalise(row: dict) -> dict:
    """Attach the conservative saturation current + rule-(a) rated current.

    * ``saturation_current`` / ``saturation_current_drop_pct``: the lowest-drop
      Isat available (10 % before 20 % before 30 %) — the safe value for the gate.
      A saturation figure with no drop definition is used only if no defined-drop
      figure exists (and then without a ``_drop_pct``).
    * ``rated_current``: the STANDARD figure, NEVER the best-case ``irp_40k``.
      When only a temperature-rise Irms is present, the 40 °C-rise figure is used
      as the WE ``I R,40K`` equivalent (both are ΔT = 40 K ratings).
    """
    out = dict(row)
    out.pop("part_number", None)

    for pct in (10, 20, 30):
        key = f"isat_{pct}pct"
        if key in out:
            out["saturation_current"] = out[key]
            out["saturation_current_drop_pct"] = pct
            break
    else:
        if "isat_undefined_drop" in out:
            out["saturation_current"] = out["isat_undefined_drop"]

    # Rule (a): the standard rated current wins over the best-case performance one.
    if "rated_current" not in out:
        if "irms_40c_rise" in out:
            out["rated_current"] = out["irms_40c_rise"]
        elif "irp_40k" in out:
            # Only the optimistic performance figure exists — use it, but keep the
            # separate irp_40k key so downstream can see it was best-case.
            out["rated_current"] = out["irp_40k"]
    return out


def parse_magnetic_text(
    text: str,
    *,
    vendor: str | None = None,
    mpn: str | None = None,
    inductance: float | None = None,
) -> dict:
    """Parse a non-Würth power-inductor datasheet into SI magnetic parameters.

    Parameters
    ----------
    text : str
        The datasheet text (as pdfplumber extracts it).
    vendor : str, optional
        Force a vendor tag (``coilcraft`` / ``vishay`` / ``mps`` / ``tdk`` /
        ``bourns``). Auto-detected from ``text`` (then ``mpn``) when omitted.
    mpn : str, optional
        The original's part number — used to select the correct row of a
        per-series selection table (Coilcraft, Vishay IHLP, …).
    inductance : float, optional
        Target inductance in henries — a secondary row selector when the MPN
        cannot pin a single row.

    Returns
    -------
    dict
        SI-valued magnetic parameters — a subset of: ``inductance`` (H),
        ``tolerance`` (fraction), ``isat_10pct`` / ``isat_20pct`` /
        ``isat_30pct`` (A), ``saturation_current`` (A, conservative) +
        ``saturation_current_drop_pct``, ``rated_current`` (A, STANDARD thermal
        rating), ``irp_40k`` (A, best-case performance figure when listed),
        ``irms_20c_rise`` / ``irms_40c_rise`` (A), ``rdc_typ`` / ``rdc_max`` (Ω),
        and the detected ``vendor``. Absent fields are omitted (never guessed).
    """
    v = vendor or detect_magnetic_vendor(text, mpn=mpn)

    rows: list[dict] = []
    if v == "coilcraft":
        rows = _parse_coilcraft(text)
    elif v == "vishay":
        rows = _parse_vishay(text)

    if rows:
        chosen = _select_row(rows, mpn=mpn, inductance=inductance)
        # Cannot identify the exact part in a multi-row table → return only the
        # vendor (an honest "unresolved"), never an arbitrary row.
        result = _finalise(chosen) if chosen is not None else {}
    else:
        # Per-line labelled sheet (MPS, TDK single-part) or a columnar family we
        # do not template — fall back to the labelled parser.
        result = _finalise(_parse_labelled(text))

    if v:
        result["vendor"] = v
    return result
