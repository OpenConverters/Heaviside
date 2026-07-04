"""Translate a component package / case code into physical body dimensions.

A cross-reference substitute must fit the board space the original occupies, so
the footprint-fit check needs real dimensions. Many catalogue records carry only
a case-code string (no mechanical drawing), and until now only 11 imperial EIA
chip sizes resolved — so a "4020" WE-MAPI inductor, a molded tantalum "B", a
"16x25" aluminium can, a SOT-23, all fell through to "unknown" and footprint fit
was silently not enforced for exactly the parts that matter.

This module maps case → (L, W, H) in **metres** using documented standards
(IPC-7351 / EIA / JEDEC) and vendor datasheets (see docs/crossref_v2_proposal.md
and the case-dimension research). Every number is from a published table — no
fabrication; an unrecognised code returns ``None`` (surface the gap, don't
guess).

THE central rule (gotcha #4 from the research): **the same 4-digit string means
different things by component family** — "4020" is 4.0×2.0 mm (L×W) as a chip but
4.0×4.0×2.0 mm (square footprint × height) on a molded power inductor, and "DxL"
on an electrolytic is diameter × height. So resolution is **category-aware**.

Heights: for chip MLCCs height is value/dielectric-dependent and NOT encoded in
the case code, so it is left ``None`` (L×W is what governs board fit); resistors,
tantalums, cans, and packaged semis/ICs have standardised heights and carry one.
"""

from __future__ import annotations

import re

# ── Chip passives: EIA imperial code → (L, W, H|None) in mm ──────────────────
# H is given for thick-film chip RESISTORS (near-fixed); left None for MLCCs
# (height varies with value/dielectric). Source: IPC-7351 / EIA chip-size std,
# mbedded.ninja, Knowles MLCC case sizes.
_CHIP_IMPERIAL_MM: dict[str, tuple[float, float, float | None]] = {
    "01005": (0.40, 0.20, 0.13),
    "0201": (0.60, 0.30, 0.23),
    "0402": (1.00, 0.50, 0.35),
    "0603": (1.60, 0.80, 0.45),
    "0805": (2.00, 1.25, 0.50),
    "1206": (3.20, 1.60, 0.55),
    "1210": (3.20, 2.50, 0.55),
    "1808": (4.50, 2.00, None),
    "1812": (4.50, 3.20, 0.60),
    "2010": (5.00, 2.50, 0.55),
    "2220": (5.70, 5.00, 0.60),
    "2225": (5.70, 6.35, 0.60),
    "2512": (6.30, 3.20, 0.55),
}

# Metric (IEC) chip codes that are NOT also a standard imperial code, so seeing
# one unambiguously means metric. (Codes like "0402" are treated as imperial —
# the common distributor convention — via the table above.) L×W in mm.
_CHIP_METRIC_MM: dict[str, tuple[float, float]] = {
    "1005": (1.00, 0.50),
    "1608": (1.60, 0.80),
    "2012": (2.00, 1.25),
    "3216": (3.20, 1.60),
    "3225": (3.20, 2.50),
    "4520": (4.50, 2.00),
    "4532": (4.50, 3.20),
    "5025": (5.00, 2.50),
    "5750": (5.70, 5.00),
    "6332": (6.30, 3.20),
}

# ── Molded tantalum: EIA letter → (L, W, H) mm. A–D agree across KEMET/AVX/
# Vishay; letters beyond D disagree by vendor and are omitted (ambiguous).
# The 3-digit metric+height also resolves (handled in the numeric parser).
_TANTALUM_LETTER_MM: dict[str, tuple[float, float, float]] = {
    "A": (3.20, 1.60, 1.80),  # 3216-18
    "B": (3.50, 2.80, 1.90),  # 3528-21
    "C": (6.00, 3.20, 2.50),  # 6032-28
    "D": (7.30, 4.30, 2.80),  # 7343-31
}
# Tantalum metric footprint (first 4 digits of a 7-digit code) → (L, W) mm.
_TANTALUM_METRIC_MM: dict[str, tuple[float, float]] = {
    "1608": (1.60, 0.80),
    "2012": (2.00, 1.25),
    "3216": (3.20, 1.60),
    "3528": (3.50, 2.80),
    "6032": (6.00, 3.20),
    "7343": (7.30, 4.30),
}

# ── Discrete semiconductor + IC packages: canonical name → (L, W, H) mm body.
# Aliases are normalised to the canonical name first. Source: JEDEC outlines,
# manufacturer mechanicals (Nexperia/Diodes/Vishay), mbedded.ninja.
_PACKAGE_MM: dict[str, tuple[float, float, float]] = {
    # small-signal SOT / SOD
    "SOT-23": (2.90, 1.30, 1.10),
    "SOT-23-5": (2.90, 1.60, 1.10),
    "SOT-23-6": (2.90, 1.60, 1.10),
    "SOT-323": (2.00, 1.25, 0.95),
    "SOT-353": (2.00, 1.25, 0.95),
    "SOT-363": (2.00, 1.25, 0.95),
    "SOT-563": (1.60, 1.20, 0.60),
    "SOT-666": (1.60, 1.20, 0.55),
    "SOT-89": (4.50, 2.50, 1.50),
    "SOT-223": (6.50, 3.50, 1.80),
    "SOD-123": (2.70, 1.60, 1.10),
    "SOD-323": (1.70, 1.25, 0.95),
    "SOD-523": (1.20, 0.80, 0.60),
    "SOD-882": (1.00, 0.60, 0.48),
    "SOD-128": (3.80, 2.60, 1.00),
    # power discretes
    "DPAK": (6.10, 6.60, 2.30),
    "D2PAK": (10.00, 9.00, 4.50),
    "TO-220": (10.16, 4.57, 15.70),
    "TO-247": (15.90, 5.30, 20.00),
    "TO-92": (4.50, 3.80, 5.00),
    "POWERPAK-1212-8": (3.30, 3.30, 1.07),
    "POWERPAK-SO-8": (6.15, 5.15, 1.00),
    # IC (body; representative narrow-SOIC / common QFN)
    "SOIC-8": (4.90, 3.90, 1.75),
    "SOIC-14": (8.65, 3.90, 1.75),
    "SOIC-16": (9.90, 3.90, 1.75),
    "TSSOP-8": (3.00, 4.40, 1.20),
    "TSSOP-14": (5.00, 4.40, 1.20),
    "TSSOP-16": (5.00, 4.40, 1.20),
    "MSOP-8": (3.00, 3.00, 1.10),
    "MSOP-10": (3.00, 3.00, 1.10),
}

# Alias → canonical package name (JEDEC / vendor synonyms). Applied after
# normalising separators, so "SC70", "SC-70", "sc 70" all match.
_PACKAGE_ALIASES: dict[str, str] = {
    "SC-59": "SOT-23", "TO-236": "SOT-23", "SC-59A": "SOT-23",
    "SOT-25": "SOT-23-5", "SC-74A": "SOT-23-5",
    "SC-70": "SOT-323", "SC-70-3": "SOT-323",
    "SC-70-5": "SOT-353", "SC-88A": "SOT-353",
    "SC-70-6": "SOT-363", "SC-88": "SOT-363",
    "TO-243": "SOT-89", "SC-62": "SOT-89",
    "TO-261": "SOT-223", "SC-73": "SOT-223",
    "TO-252": "DPAK", "TO-252AA": "DPAK",
    "TO-263": "D2PAK", "TO-263AB": "D2PAK", "D²PAK": "D2PAK", "DDPAK": "D2PAK",
    "SO-8": "SOIC-8", "SOIC8": "SOIC-8", "SO8": "SOIC-8",
    "SO-14": "SOIC-14", "SO-16": "SOIC-16",
}

_DXL_RE = re.compile(r"(?<!\d)(\d{1,2}(?:\.\d)?)\s*[xX×]\s*(\d{1,3}(?:\.\d)?)(?!\d)")
_FOUR_DIGIT_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")
_TANT_METRIC_RE = re.compile(r"(?<!\d)(\d{4})(?:-(\d{2}))?(?!\d)")
_SEP_RE = re.compile(r"[\s_]+")


def _mm_to_m(t: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(x / 1000.0 if x is not None else None for x in t)


def _norm_pkg(s: str) -> str:
    """Uppercase, collapse whitespace/underscores to a single hyphen-free form
    while keeping meaningful hyphens (SOT-23). Used for alias lookup."""
    s = _SEP_RE.sub("-", s.strip().upper())
    return s


def _resolve_package(code: str) -> tuple[float, float, float] | None:
    key = _norm_pkg(code)
    if key in _PACKAGE_ALIASES:
        key = _PACKAGE_ALIASES[key]
    if key in _PACKAGE_MM:
        return _PACKAGE_MM[key]
    # tolerate a leading letter+hyphen prefix a vendor may prepend, and try the
    # bare token (e.g. "PACKAGE-SOT-23" → "SOT-23")
    for canon in _PACKAGE_MM:
        if key.endswith(canon):
            return _PACKAGE_MM[canon]
    return None


def resolve_dimensions(
    case: str | None, category: str | None
) -> tuple[float, float, float | None] | None:
    """Resolve a case/package code to (length, width, height) in metres, or None
    when it is not a recognised code. Category-aware so the same 4-digit string
    resolves correctly per family (see module docstring). Height is None when the
    code fixes only a footprint.
    """
    if not case:
        return None
    raw = str(case).strip()
    if not raw:
        return None
    cat = (category or "").lower()

    # 1) Named packages (SOT/SOD/TO/DPAK/SOIC/…) and their aliases.
    pkg = _resolve_package(raw)
    if pkg is not None:
        return _mm_to_m(pkg)  # type: ignore[return-value]

    # 2) Molded tantalum EIA letter (single A–D), only for capacitors.
    if cat == "capacitor":
        letter = raw.upper().strip()
        if letter in _TANTALUM_LETTER_MM:
            return _mm_to_m(_TANTALUM_LETTER_MM[letter])  # type: ignore[return-value]

    # 3) Aluminium electrolytic / any "DxL" can code → diameter × diameter ×
    #    length (the base footprint is ~the can diameter; L is the height).
    m = _DXL_RE.search(raw)
    if m and cat in ("capacitor", "", None):
        d, ln = float(m.group(1)), float(m.group(2))
        # A DxL only makes sense when the second number is a plausible can
        # height (>= diameter is common for cans). Guard against matching a
        # random "4x4" that is really a footprint.
        if d <= 25 and ln <= 60 and ln >= d * 0.4:
            return _mm_to_m((d, d, ln))  # type: ignore[return-value]

    # 4) Four-digit numeric codes — meaning depends on family.
    fd = _FOUR_DIGIT_RE.search(raw)
    if fd:
        code = fd.group(1)
        # 4a) Chip passives (and chip inductors): imperial EIA first, then a
        #     metric code that isn't also imperial.
        if code in _CHIP_IMPERIAL_MM:
            l, w, h = _CHIP_IMPERIAL_MM[code]
            # MLCC height varies → drop it; resistors keep it.
            keep_h = h if cat in ("resistor",) else None
            return _mm_to_m((l, w, keep_h))  # type: ignore[return-value]
        # 4b) Molded power inductor: NNMM = square footprint (NN) × height (MM),
        #     both in tenths of a mm. "4020" → 4.0 × 4.0 × 2.0 mm. Only when the
        #     code is not a standard chip code (handled above).
        if cat in ("magnetic", "inductor", "chipbead"):
            side = int(code[:2]) / 10.0
            hgt = int(code[2:]) / 10.0
            if 0.5 <= side <= 30 and 0.3 <= hgt <= 25:
                return _mm_to_m((side, side, hgt))  # type: ignore[return-value]
        # 4c) Metric chip code (e.g. tantalum/MLCC "3216").
        if code in _CHIP_METRIC_MM:
            l, w = _CHIP_METRIC_MM[code]
            return _mm_to_m((l, w, None))  # type: ignore[return-value]
        if code in _TANTALUM_METRIC_MM and cat == "capacitor":
            l, w = _TANTALUM_METRIC_MM[code]
            # optional height suffix "-18"/"-43" → tenths of a mm
            tm = _TANT_METRIC_RE.search(raw)
            h = (int(tm.group(2)) / 10.0) if (tm and tm.group(2)) else None
            return _mm_to_m((l, w, h))  # type: ignore[return-value]
    return None
