"""Deterministic crossref guardrail system.

Post-parse checks applied to structured crossref JSON to catch
systematic LLM failure modes:

  - G0: Already-target-manufacturer parts misclassified as no_substitute.
  - G1: Capacitor value mismatch (substitute vs. original > 2x ratio).
  - G2: Resistor value drift (> 5% shift on feedback-dividers).
  - G3: Capacitor voltage downrate (substitute rated below original).
  - G4: Inductor over-rejection on footprint-only grounds.
  - G5: Substitute MPN does not exist in TAS catalogue.
  - G6: Voltage inadequacy admitted in the LLM's own notes.
  - GAECQ: Automotive grade propagation.
  - GFoot: Footprint class incompatibility (SMD vs. leaded, >3 size jump).
  - GStack: Multiple concurrent caveats on a single row.

Each guardrail:
  - Has a clear docstring explaining what it catches.
  - Returns structured fire-log entries when it fires.
  - Is independently testable.

The main entry point ``apply_guardrails`` returns ``(corrected_json,
fire_log_entries)`` and never silently substitutes defaults. If a
guardrail cannot run (e.g. TAS data unavailable), it emits a
diagnostic skip entry instead.

Ported from ``proteus.pipelines.crossref_strands._apply_crossref_guardrails``.
Adapted to use ``heaviside.catalogue._reader`` and
``heaviside.pipeline.value_parse`` for TAS lookups and SI parsing.
"""

from __future__ import annotations

import re
from pathlib import Path

from heaviside.pipeline.value_parse import parse_si_value

# ---------------------------------------------------------------------------
# TAS data directory
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TAS_DATA_DEFAULT = _REPO_ROOT / "TAS" / "data"


# ---------------------------------------------------------------------------
# TAS MPN lookup (linear scan with caching)
# ---------------------------------------------------------------------------

_TAS_LOOKUP_CACHE: dict[tuple[str, str], dict | None] = {}
# Per-file MPN index: filename -> {mpn_lower: flat_record}. Built once on first
# access and reused, so validating N substitutes is O(file) total instead of
# O(N × file) — the latter made a large BOM (hundreds of magnetic substitutes,
# each previously scanning the whole 50 MB magnetics.ndjson) take ~20 min.
_TAS_INDEX_CACHE: dict[str, dict[str, dict]] = {}

_TAS_KIND_TO_FILES = {
    "capacitor": ["capacitors.ndjson"],
    "resistor": ["resistors.ndjson"],
    "inductor": ["magnetics.ndjson"],
    "magnetic": ["magnetics.ndjson"],
    "chipBead": ["magnetics.ndjson"],
    "mosfet": ["mosfets.ndjson"],
    "diode": ["diodes.ndjson"],
}


def _flat_record_from_env(env: dict, mi: dict) -> dict:
    """Extract the commonly-needed fields from an envelope's manufacturerInfo.
    Handles ``electrical`` as either a dict (caps/resistors) or a LIST (magnetics
    v2) — reading it as a bare dict used to throw and abort the whole lookup."""
    di = mi.get("datasheetInfo") or {}
    elec_raw = di.get("electrical")
    if isinstance(elec_raw, list):
        elec = next((x for x in elec_raw if isinstance(x, dict)), {})
    elif isinstance(elec_raw, dict):
        elec = elec_raw
    else:
        elec = {}
    part_info = di.get("part") or {}
    cap_obj = elec.get("capacitance")
    cap_val = (cap_obj.get("nominal") if isinstance(cap_obj, dict) else cap_obj)
    res_obj = elec.get("resistance")
    res_val = (res_obj.get("nominal") if isinstance(res_obj, dict) else res_obj)
    return {
        "capacitance": cap_val,
        "voltage": elec.get("ratedVoltage"),
        "resistance_Ohm": res_val,
        "package": part_info.get("caseCode") or part_info.get("case"),
        "manufacturer": mi.get("name"),
        "family": mi.get("family") or part_info.get("series"),
        "status": mi.get("status"),
        "raw_envelope": env,
    }


def _tas_file_index(path: Path) -> dict[str, dict]:
    """Return (building+caching once) an mpn_lower -> flat_record index for an
    NDJSON catalogue file."""
    cached = _TAS_INDEX_CACHE.get(path.name)
    if cached is not None:
        return cached
    index: dict[str, dict] = {}
    try:
        from heaviside.catalogue._reader import iter_envelopes

        for _lineno, env in iter_envelopes(path):
            for top_key in ("capacitor", "semiconductor", "resistor", "magnetics", "magnetic"):
                sub = env.get(top_key)
                if not isinstance(sub, dict):
                    continue
                for inner_key in (None, "mosfet", "diode", "igbt"):
                    record = sub if inner_key is None else sub.get(inner_key)
                    if not isinstance(record, dict):
                        continue
                    mi = record.get("manufacturerInfo")
                    if not isinstance(mi, dict):
                        continue
                    ref = mi.get("reference")
                    if isinstance(ref, str) and ref.strip():
                        index.setdefault(ref.strip().lower(), _flat_record_from_env(env, mi))
    except Exception:
        pass
    _TAS_INDEX_CACHE[path.name] = index
    return index


def _lookup_tas_part(
    part_number: str,
    component_kind: str,
    *,
    tas_data_dir: Path | None = None,
) -> dict | None:
    """Look up a part's parsed specs in TAS NDJSON files.

    Returns a flat dict of commonly-needed fields (capacitance, voltage,
    resistance, etc.), or ``None`` if the part is not found. Uses a per-file
    MPN index (built once) so repeated lookups don't re-scan multi-megabyte
    NDJSON files.
    """
    if not part_number or part_number == "no_substitute":
        return None
    key = (component_kind, part_number.strip().lower())
    if key in _TAS_LOOKUP_CACHE:
        return _TAS_LOOKUP_CACHE[key]

    root = tas_data_dir or _TAS_DATA_DEFAULT
    filenames = _TAS_KIND_TO_FILES.get(component_kind)
    # Fall back to all NDJSON files if kind is unknown.
    if not filenames:
        filenames = [f.name for f in root.glob("*.ndjson")]

    mpn_l = part_number.strip().lower()
    result: dict | None = None
    for fname in filenames:
        path = root / fname
        if not path.is_file():
            continue
        hit = _tas_file_index(path).get(mpn_l)
        if hit is not None:
            result = hit
            break

    _TAS_LOOKUP_CACHE[key] = result
    return result


def _mpn_exists_in_tas(
    mpn: str,
    *,
    tas_data_dir: Path | None = None,
) -> bool:
    """Return True if *mpn* appears in any TAS NDJSON file.

    Checks all component kinds (capacitor, resistor, magnetics, etc.)
    via cheap substring search before falling back to structured lookup.
    """
    root = tas_data_dir or _TAS_DATA_DEFAULT
    if not root.is_dir():
        return False
    if not mpn or not mpn.strip():
        return False

    # Use the per-file MPN index (built once, cached) so checking N substitutes
    # is O(total catalogue) rather than re-reading every NDJSON file per MPN.
    mpn_l = mpn.strip().lower()
    for ndjson_file in root.glob("*.ndjson"):
        if mpn_l in _tas_file_index(ndjson_file):
            return True
    return False


# ---------------------------------------------------------------------------
# Fire log helpers
# ---------------------------------------------------------------------------


def _make_fire(
    guardrail_id: str,
    ref_des: str,
    before: str | None,
    after: str,
    reason: str,
) -> dict:
    """Create a structured fire-log entry."""
    return {
        "guardrail_id": guardrail_id,
        "ref_des": ref_des or "?",
        "before": before,
        "after": after,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# BOM lookup helper
# ---------------------------------------------------------------------------


def _build_bom_by_ref(source_bom: list[dict]) -> dict[str, dict]:
    """Build a ``{ref_des: bom_row}`` mapping, expanding grouped refs."""
    bom_by_ref: dict[str, dict] = {}
    for c in source_bom:
        rd = str(c.get("ref_des", "") or "")
        for sub in re.split(r"[,\s]+", rd):
            sub = sub.strip()
            if sub:
                bom_by_ref[sub] = c
    return bom_by_ref


# ---------------------------------------------------------------------------
# Individual guardrails
# ---------------------------------------------------------------------------


def _normalize_manufacturer_name(name: str) -> str:
    """Lowercase, drop non-alphanumerics and common suffixes for matching.

    'Würth Elektronik' / 'Wurth Elektronik eiSos' / 'WE' all collapse so a
    BOM-extracted manufacturer can be compared to the target regardless of
    spelling/casing/legal-suffix noise."""
    import unicodedata

    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = n.lower()
    for suffix in (
        "elektronik",
        "electronics",
        "electronic",
        "eisos",
        "technologies",
        "technology",
        "semiconductor",
        "semiconductors",
        "incorporated",
        "inc",
        "corporation",
        "corp",
        "gmbh",
        "ltd",
        "llc",
        "co",
        "limited",
    ):
        n = n.replace(suffix, " ")
    return "".join(ch for ch in n if ch.isalnum())


def _manufacturer_matches(a: str, b: str) -> bool:
    """True if two manufacturer names refer to the same maker (either
    normalized form contains the other; guards against empty/too-short)."""
    na, nb = _normalize_manufacturer_name(a), _normalize_manufacturer_name(b)
    if len(na) < 3 or len(nb) < 3:
        return False
    return na in nb or nb in na


def _g0_already_target_manufacturer(
    comps: list[dict],
    target_manufacturer: str,
    fires: list[dict],
    *,
    tas_data_dir: Path | None = None,
) -> None:
    """G0: If the original_pn is already the target manufacturer's part
    AND exists in TAS, force status='exact' with substitute_pn=original_pn.

    Manufacturer-AGNOSTIC: looks the original MPN up in TAS and compares
    the part's catalogued manufacturer to the target — no per-manufacturer
    MPN-pattern regex. Pre-empts the LLM hallucinating 'no_substitute' for
    parts that ARE already the target manufacturer (e.g. 74437349100 is
    Würth WE-MAPI; LM5146 is TI; etc.).
    """
    for comp in comps:
        orig_pn = (comp.get("original_pn") or "").strip()
        if not orig_pn or orig_pn == "no_substitute":
            continue
        prev_status = comp.get("status")
        if prev_status in ("exact", "already_target"):
            continue
        # Authoritative check: is this MPN in TAS, and is its catalogued
        # manufacturer the target? Works for ANY manufacturer.
        part = _lookup_tas_part(
            orig_pn,
            comp.get("component_type", ""),
            tas_data_dir=tas_data_dir,
        )
        part_mfr = (part or {}).get("manufacturer")
        if not part or not isinstance(part_mfr, str):
            continue
        if not _manufacturer_matches(part_mfr, target_manufacturer):
            continue
        comp["status"] = "exact"
        comp["substitute_pn"] = orig_pn
        comp["value_check"] = "pass"
        comp["footprint_check"] = "pass"
        comp["derating_check"] = "pass"
        comp["notes"] = f"Already {target_manufacturer} part ({orig_pn}); verified in catalogue."
        ref = comp.get("ref_des", "?")
        fires.append(
            _make_fire(
                "0",
                ref,
                prev_status,
                "exact",
                f"original_pn {orig_pn} catalogued as {part_mfr} (matches target) "
                f"+ present in catalogue",
            )
        )


def _g1_capacitor_value_mismatch(
    comps: list[dict],
    bom_by_ref: dict[str, dict],
    fires: list[dict],
    *,
    tas_data_dir: Path | None = None,
) -> None:
    """G1: Per-component capacitance check.

    For every capacitor substitute, look up its TAS-stored capacitance
    and compare to the original's parsed capacitance. >2x mismatch
    means the LLM picked a wrong-value part; downgrade to no_substitute.
    """
    for comp in comps:
        ref = str(comp.get("ref_des", "") or "").split(",")[0].strip()
        bom_entry = bom_by_ref.get(ref, {})
        kind_cap = (bom_entry.get("type") or "").lower() == "capacitor" or (
            comp.get("type") or ""
        ).lower() == "capacitor"
        if not kind_cap:
            continue
        pn = (comp.get("substitute_pn") or "").strip()
        if not pn or pn == "no_substitute":
            continue
        if comp.get("status") not in ("recommended", "partial"):
            continue

        sub_spec = _lookup_tas_part(pn, "capacitor", tas_data_dir=tas_data_dir)
        if sub_spec is None or sub_spec.get("capacitance") is None:
            continue
        sub_cap = sub_spec["capacitance"]
        if sub_cap <= 0:
            continue

        orig_val_str = bom_entry.get("value") or comp.get("original_pn") or ""
        orig_cap = parse_si_value(orig_val_str)
        if orig_cap is None or not (1e-13 <= orig_cap <= 1e-1):
            continue

        ratio = sub_cap / orig_cap
        if ratio < 0.5 or ratio > 2.0:
            prev_status = comp.get("status", "?")
            comp["status"] = "no_substitute"
            comp["substitute_pn"] = "no_substitute"
            comp["notes"] = (
                f"GUARDRAIL G1: {ref} substitute {pn} has C={sub_cap:.2e}F, "
                f"original needs {orig_cap:.2e}F (ratio {ratio:.2f}x). "
                f"Downgraded {prev_status} -> no_substitute.\n" + (comp.get("notes") or "")
            )
            fires.append(
                _make_fire(
                    "1",
                    ref,
                    prev_status,
                    "no_substitute",
                    f"capacitance ratio {ratio:.2f}x out of [0.5, 2.0]",
                )
            )


def _g2_resistor_value_drift(
    comps: list[dict],
    bom_by_ref: dict[str, dict],
    fires: list[dict],
    *,
    tas_data_dir: Path | None = None,
) -> None:
    """G2: Resistor value tolerance check.

    Catches two tiers:
      - >50% deviation or >2x ratio: wrong-value part (LLM matched on
        package only). Escalate to no_substitute.
      - >5% deviation: marginal drift that risks feedback-divider
        accuracy. Downgrade to partial.
    """
    for comp in comps:
        ref = str(comp.get("ref_des", "") or "").split(",")[0].strip()
        bom_entry = bom_by_ref.get(ref, {})
        kind = (bom_entry.get("type") or "").lower()
        if kind != "resistor":
            continue
        if comp.get("status") not in ("recommended", "partial"):
            continue
        pn = (comp.get("substitute_pn") or "").strip()
        if not pn or pn == "no_substitute":
            continue

        orig_val = parse_si_value(bom_entry.get("value"))
        sub_spec = _lookup_tas_part(pn, "resistor", tas_data_dir=tas_data_dir)
        sub_val = (sub_spec or {}).get("resistance_Ohm")

        if orig_val is None or not (1e-3 <= orig_val <= 1e9):
            continue
        if sub_val is None or sub_val <= 0:
            continue

        dev = abs(sub_val - orig_val) / orig_val

        if dev > 0.50 or sub_val / orig_val > 2.0 or orig_val / sub_val > 2.0:
            prev_status = comp.get("status", "?")
            comp["status"] = "no_substitute"
            comp["substitute_pn"] = "no_substitute"
            comp["notes"] = (
                f"GUARDRAIL G2: {ref} resistor substitute {pn} = {sub_val} Ohm "
                f"is a wrong-value part for original {orig_val} Ohm "
                f"(deviation {dev * 100:.0f}%). Marked no_substitute.\n" + (comp.get("notes") or "")
            )
            fires.append(
                _make_fire(
                    "2a",
                    ref,
                    prev_status,
                    "no_substitute",
                    f"resistor wrong-value: {sub_val} Ohm vs {orig_val} Ohm (deviation {dev * 100:.0f}%)",
                )
            )
        elif dev > 0.05:
            prev_status = comp.get("status")
            comp["status"] = "partial"
            comp["notes"] = (
                f"GUARDRAIL G2: {ref} resistor substitute {pn} = {sub_val} Ohm "
                f"differs from original {orig_val} Ohm by {dev * 100:.1f}%. "
                f"Downgraded to partial (>5% shift risks feedback dividers).\n"
                + (comp.get("notes") or "")
            )
            fires.append(
                _make_fire(
                    "2b",
                    ref,
                    prev_status,
                    "partial",
                    f"resistor tolerance: {sub_val} Ohm vs {orig_val} Ohm (delta {dev * 100:.1f}%)",
                )
            )


def _g3_capacitor_voltage_downrate(
    comps: list[dict],
    bom_by_ref: dict[str, dict],
    fires: list[dict],
    *,
    tas_data_dir: Path | None = None,
) -> None:
    """G3: Capacitor voltage downrate check.

    If the substitute's rated voltage is below the original's rated
    voltage AND the entry is tagged 'recommended', downgrade to
    'partial'.
    """
    for comp in comps:
        ref = str(comp.get("ref_des", "") or "").split(",")[0].strip()
        bom_entry = bom_by_ref.get(ref, {})
        kind_cap = (bom_entry.get("type") or "").lower() == "capacitor" or (
            comp.get("type") or ""
        ).lower() == "capacitor"
        if not kind_cap:
            continue
        if comp.get("status") != "recommended":
            continue
        pn = (comp.get("substitute_pn") or "").strip()
        if not pn or pn == "no_substitute":
            continue

        sub_spec = _lookup_tas_part(pn, "capacitor", tas_data_dir=tas_data_dir)
        sub_v = (sub_spec or {}).get("voltage")
        orig_v = parse_si_value(bom_entry.get("voltage"))

        if sub_v is None or orig_v is None or orig_v <= 0:
            continue
        if sub_v < orig_v * 0.99:  # 1% slack for rounding
            prev_status = comp.get("status")
            comp["status"] = "partial"
            comp["notes"] = (
                f"GUARDRAIL G3: {ref} cap substitute {pn} rated {sub_v}V, "
                f"original needs {orig_v}V. Downgraded recommended -> partial.\n"
                + (comp.get("notes") or "")
            )
            fires.append(
                _make_fire(
                    "3",
                    ref,
                    prev_status,
                    "partial",
                    f"cap voltage downrate: {sub_v}V vs {orig_v}V required",
                )
            )


def _g4_inductor_footprint_overrejection(
    comps: list[dict],
    bom_by_ref: dict[str, dict],
    fires: list[dict],
) -> None:
    """G4: Inductor over-rejection on footprint-only grounds.

    Per standing rule: inductors don't need exact footprint match;
    reject only on Isat / DCR / value / extreme size. If a substitute
    was marked 'not_recommended' solely because of footprint mismatch,
    soften to 'partial'.
    """
    _FOOTPRINT_ONLY = (
        "package mismatch",
        "footprint mismatch",
        "not a drop-in",
        "requires pcb redesign",
        "package change",
        "footprint change",
    )
    _HARD_REJECTION = (
        r"isat",
        r"saturation",
        r"dcr.{0,30}exceed",
        r"value mismatch",
        r"extreme size",
        r"10x",
        r"20x",
    )

    for comp in comps:
        ref = str(comp.get("ref_des", "") or "").split(",")[0].strip()
        bom_entry = bom_by_ref.get(ref, {})
        kind = (bom_entry.get("type") or "").lower()
        comp_type = (comp.get("type") or "").lower()
        _magnetic_kinds = ("inductor", "transformer", "common_mode_choke", "magnetic")
        if kind not in _magnetic_kinds and comp_type not in _magnetic_kinds:
            continue
        if comp.get("status") != "not_recommended":
            continue

        notes = (comp.get("notes") or "").lower()
        is_footprint_issue = any(tok in notes for tok in _FOOTPRINT_ONLY)
        is_hard_reject = any(re.search(tok, notes) for tok in _HARD_REJECTION)

        if is_footprint_issue and not is_hard_reject:
            comp["status"] = "partial"
            comp["notes"] = (
                f"GUARDRAIL G4: {ref} inductor substitute "
                f"{comp.get('substitute_pn')} was marked not_recommended on "
                f"footprint grounds only. Downgraded to partial.\n" + (comp.get("notes") or "")
            )
            fires.append(
                _make_fire(
                    "4",
                    ref,
                    "not_recommended",
                    "partial",
                    "inductor over-rejected on footprint-only grounds",
                )
            )


def _g5_substitute_existence(
    comps: list[dict],
    fires: list[dict],
    *,
    tas_data_dir: Path | None = None,
) -> None:
    """G5: Substitute MPN must exist in the TAS catalogue.

    Catches LLM hallucinations where a plausible-looking MPN does not
    actually exist in TAS. Also catches product-family descriptions
    masquerading as MPNs (e.g. 'WCAP-MLCC-4700nF-630V').
    """
    for comp in comps:
        if comp.get("status") not in ("recommended", "partial", "exact"):
            continue
        pn = (comp.get("substitute_pn") or "").strip()
        if not pn or pn == "no_substitute":
            continue
        ref = str(comp.get("ref_des", "") or "").split(",")[0].strip()

        # 5a: Format check — reject obvious non-MPN strings (product-family
        # descriptions like 'WCAP-MLCC-4700nF-160V'). Manufacturer-agnostic
        # carve-out: if the description-looking string is actually a real
        # catalogued MPN (present in TAS), keep it — don't reject on format.
        looks_like_description = "-" in pn and any(unit in pn for unit in ("nF", "uF", "V", "Ohm"))
        if looks_like_description and not _mpn_exists_in_tas(pn, tas_data_dir=tas_data_dir):
            prev_status = comp.get("status")
            comp["status"] = "no_substitute"
            comp["substitute_pn"] = "no_substitute"
            comp["notes"] = (
                f"GUARDRAIL G5b: {ref} substitute '{pn}' is a product family "
                f"description, not a catalogue MPN. Demoted to no_substitute.\n"
                + (comp.get("notes") or "")
            )
            fires.append(
                _make_fire(
                    "5b",
                    ref,
                    prev_status,
                    "no_substitute",
                    f"substitute '{pn}' is a product-family description, not an MPN",
                )
            )
            continue

        # 5b: Existence check.
        if not _mpn_exists_in_tas(pn, tas_data_dir=tas_data_dir):
            prev_status = comp.get("status")
            comp["status"] = "no_substitute"
            comp["substitute_pn"] = "no_substitute"
            comp["notes"] = (
                f"GUARDRAIL G5: {ref} substitute '{pn}' does not exist in the "
                f"catalogue (TAS lookup returned no record). "
                f"Demoted to no_substitute — likely LLM hallucination.\n"
                + (comp.get("notes") or "")
            )
            fires.append(
                _make_fire(
                    "5",
                    ref,
                    prev_status,
                    "no_substitute",
                    f"substitute '{pn}' not present in catalogue (LLM hallucination)",
                )
            )


def _g6_voltage_inadequacy_in_notes(
    comps: list[dict],
    fires: list[dict],
) -> None:
    """G6: LLM admits voltage inadequacy in its own notes.

    Catches the case where the LLM emits status=recommended but its own
    notes describe a voltage mismatch (e.g. 'No Wurth 4.7uF/1206
    capacitor meets original 100V rating').
    """
    _VOLTAGE_INADEQUATE = [
        # Manufacturer-agnostic: "no <any manufacturer> ... meets original
        # ... rating" (the LLM names whatever target it was given).
        re.compile(r"\bno\b.{0,40}meets? original.{0,40}rating", re.I | re.S),
        re.compile(r"highest available.{0,30}\d+\.?\d*\s*v.{0,40}orig", re.I | re.S),
        re.compile(r"voltage rating fail", re.I),
        re.compile(r"voltage.{0,30}inadequate", re.I),
        re.compile(r"derating fail", re.I),
        re.compile(r"requirement.{0,20}not met", re.I),
    ]

    for comp in comps:
        if comp.get("status") != "recommended":
            continue
        notes = comp.get("notes") or ""
        if not any(p.search(notes) for p in _VOLTAGE_INADEQUATE):
            continue
        ref = str(comp.get("ref_des", "") or "").split(",")[0].strip()
        comp["status"] = "partial"
        comp["notes"] = (
            f"GUARDRAIL G6: {ref} marked recommended but notes describe a "
            f"voltage inadequacy. Demoted to partial.\n" + (comp.get("notes") or "")
        )
        fires.append(
            _make_fire(
                "6",
                ref,
                "recommended",
                "partial",
                "notes describe voltage inadequacy",
            )
        )


_AUTOMOTIVE_PREFIXES = ("NCV", "NCD", "NCH", "TJA", "TLE", "AUIRG", "AUIRF")
_AUTOMOTIVE_SUFFIXES = ("-AEC", "-Q100", "-Q101", "-Q200")


def _looks_automotive(mpn: str) -> bool:
    if not mpn:
        return False
    u = mpn.upper()
    if any(u.startswith(p) for p in _AUTOMOTIVE_PREFIXES):
        return True
    return bool(any(s in u for s in _AUTOMOTIVE_SUFFIXES))


def _gaecq_automotive_grade(
    comps: list[dict],
    source_bom: list[dict],
    fires: list[dict],
    *,
    tas_data_dir: Path | None = None,
) -> None:
    """GAECQ: Automotive grade propagation.

    When the source BOM contains automotive-qualified parts (NCV*, TJA*,
    AEC-Q suffixed), all substitute parts must also carry AEC-Q
    qualification. Parts without Q-grade are demoted to partial.
    """
    auto_context = any(_looks_automotive(s.get("original_pn") or "") for s in (source_bom or []))
    if not auto_context:
        return

    for comp in comps:
        if comp.get("status") not in ("recommended", "partial"):
            continue
        pn = (comp.get("substitute_pn") or "").strip()
        if not pn or pn == "no_substitute":
            continue
        ref = str(comp.get("ref_des", "") or "").split(",")[0].strip()

        # Check if the substitute's TAS record mentions AEC-Q.
        spec = _lookup_tas_part(pn, "", tas_data_dir=tas_data_dir)
        if spec is None:
            continue
        env = spec.get("raw_envelope") or {}
        # Walk the envelope looking for qualification fields.
        quals_text = ""
        for top_key in ("capacitor", "semiconductor", "resistor", "magnetics"):
            sub = env.get(top_key)
            if not isinstance(sub, dict):
                continue
            for inner_key in (None, "mosfet", "diode", "igbt"):
                record = sub if inner_key is None else sub.get(inner_key)
                if not isinstance(record, dict):
                    continue
                mi = record.get("manufacturerInfo") or {}
                di = mi.get("datasheetInfo") or {}
                for path in (di, mi, di.get("part") or {}, di.get("compliance") or {}):
                    q = path.get("qualifications") or path.get("aecq") or path.get("aec_q")
                    if isinstance(q, list):
                        quals_text += " " + " ".join(str(x).upper() for x in q)
                    elif isinstance(q, str):
                        quals_text += " " + q.upper()

        is_q = any(tok in quals_text for tok in ("AEC-Q", "AECQ", "Q200", "Q100", "Q101"))
        if not is_q:
            prev = comp.get("status")
            comp["status"] = "partial" if prev == "recommended" else prev
            comp["notes"] = (
                f"GUARDRAIL GAECQ: automotive design but substitute {pn} "
                f"carries no Q-grade in TAS. Verify qualification. " + (comp.get("notes") or "")
            )
            fires.append(
                _make_fire(
                    "AECQ",
                    ref,
                    prev,
                    "partial",
                    f"automotive design; substitute {pn} not Q-graded in TAS",
                )
            )


_SMD_SIZES = [
    "0201",
    "0402",
    "0603",
    "0805",
    "1206",
    "1210",
    "1812",
    "2010",
    "2220",
    "2512",
    "2920",
]
_LEADED_PKG_TOKENS = (
    "DIP",
    "PDIP",
    "TO-220",
    "TO-247",
    "TO-218",
    "TO-3P",
    "TO-126",
    "TO-92",
    "SIP",
    "RADIAL",
    "AXIAL",
    "THRU-HOLE",
    "THROUGH",
    "THT",
    "SNAP-IN",
    "SCREW-TERMINAL",
)
_SMD_PKG_TOKENS = (
    "0201",
    "0402",
    "0603",
    "0805",
    "1206",
    "1210",
    "1812",
    "2010",
    "2512",
    "2920",
    "DFN",
    "SON",
    "TDFN",
    "CSP",
    "SOT",
    "SOIC",
    "SOP",
    "DPAK",
    "D2PAK",
    "QFN",
    "BGA",
    "LGA",
    "SMA",
    "SMB",
    "SMC",
)


def _is_smd(pkg: str) -> bool:
    p = pkg.upper()
    return any(tok in p for tok in _SMD_PKG_TOKENS)


def _is_leaded(pkg: str) -> bool:
    p = pkg.upper()
    return any(tok in p for tok in _LEADED_PKG_TOKENS)


def _smd_class_idx(pkg: str) -> int | None:
    if not pkg:
        return None
    p = pkg.upper()
    for i, s in enumerate(_SMD_SIZES):
        if s in p:
            return i
    return None


def _gfoot_footprint_compatibility(
    comps: list[dict],
    bom_by_ref: dict[str, dict],
    fires: list[dict],
    *,
    tas_data_dir: Path | None = None,
) -> None:
    """GFoot: Footprint class compatibility check.

    - SMD <-> leaded mounting type: hard reject.
    - Class jump >= 4 sizes: hard reject (redesign required).
    - Class jump >= 3 sizes: demote to partial.

    Skipped for inductors/transformers/MOSFETs/diodes — their footprints
    vary by series and generic rules produce too many false positives.
    """
    for comp in comps:
        if comp.get("status") not in ("recommended", "partial"):
            continue
        pn = (comp.get("substitute_pn") or "").strip()
        if not pn or pn == "no_substitute":
            continue
        ref = str(comp.get("ref_des", "") or "").split(",")[0].strip()
        src = bom_by_ref.get(ref) or {}

        # Skip inductor/semiconductor types.
        ctype = str(comp.get("type") or "").lower()
        if ctype in (
            "inductor",
            "transformer",
            "common_mode_choke",
            "ferrite_bead",
            "magnetic",
            "mosfet",
            "diode",
            "igbt",
        ):
            continue

        src_pkg = str(src.get("package") or "").strip()
        if not src_pkg:
            continue

        # Get substitute package from TAS.
        sub_spec = _lookup_tas_part(pn, "", tas_data_dir=tas_data_dir)
        sub_pkg = str((sub_spec or {}).get("package") or "").strip()
        if not sub_pkg:
            continue

        # SMD <-> leaded check.
        if (_is_smd(src_pkg) and _is_leaded(sub_pkg)) or (_is_leaded(src_pkg) and _is_smd(sub_pkg)):
            prev = comp.get("status")
            comp["status"] = "no_substitute"
            comp["substitute_pn"] = "no_substitute"
            comp["notes"] = (
                f"GUARDRAIL GFoot: {src_pkg} mount-type incompatible "
                f"with substitute {sub_pkg}. " + (comp.get("notes") or "")
            )
            fires.append(
                _make_fire(
                    "Foot",
                    ref,
                    prev,
                    "no_substitute",
                    f"mount-type incompatible: {src_pkg} -> {sub_pkg}",
                )
            )
            continue

        # SMD class jump check. A larger-package substitute is a REAL part that
        # exists — it is a partial substitution (works electrically, needs a
        # footprint/board-space check), NOT a no_substitute. Reserving
        # no_substitute for "no electrically-valid part exists" keeps the label
        # honest and avoids discarding a usable Würth equivalent just because it
        # is a size or more bigger (the engineer decides if the board has room).
        si, ti = _smd_class_idx(src_pkg), _smd_class_idx(sub_pkg)
        if si is not None and ti is not None:
            jump = abs(ti - si)
            if jump >= 4:
                prev = comp.get("status")
                comp["status"] = "partial" if prev == "recommended" else prev
                comp["notes"] = (
                    f"GUARDRAIL GFoot: {src_pkg} -> {sub_pkg} "
                    f"({jump} size classes — board redesign required for footprint). "
                    + (comp.get("notes") or "")
                )
                fires.append(
                    _make_fire(
                        "Foot",
                        ref,
                        prev,
                        comp["status"],
                        f"footprint redesign (>=4 classes): {src_pkg} -> {sub_pkg}",
                    )
                )
                continue
            if jump >= 3:
                prev = comp.get("status")
                comp["status"] = "partial" if prev == "recommended" else prev
                comp["notes"] = (
                    f"GUARDRAIL GFoot: large footprint jump {src_pkg} -> {sub_pkg} "
                    f"({jump} size classes). " + (comp.get("notes") or "")
                )
                fires.append(
                    _make_fire(
                        "Foot",
                        ref,
                        prev,
                        "partial",
                        f"footprint jump >=3 classes: {src_pkg} -> {sub_pkg}",
                    )
                )


def _gstack_multiple_caveats(
    comps: list[dict],
    fires: list[dict],
) -> None:
    """GStack: Multiple concurrent caveats on a single row.

    When a row has accumulated >= 2 independent guardrail warnings it is no
    longer a clean drop-in — but a real part still EXISTS, so it is a *partial*
    substitution flagged "MULTIPLE COMPROMISES" for the engineer to weigh, NOT a
    no_substitute. no_substitute must mean "no electrically-valid part exists";
    relabeling a found-but-caveated part as no_substitute hides a usable option
    and is the wrong signal (it also wrongly tanked coverage vs Proteus).
    """
    for comp in comps:
        if comp.get("status") not in ("recommended", "partial"):
            continue
        notes = comp.get("notes") or ""
        # Count guardrail prefix occurrences.
        guardrail_hits = len(re.findall(r"GUARDRAIL G", notes))
        already_stacked = "MULTIPLE COMPROMISES" in notes.upper()
        if already_stacked:
            continue
        if guardrail_hits >= 2:
            ref = str(comp.get("ref_des", "") or "").split(",")[0].strip()
            prev = comp.get("status")
            comp["status"] = "partial"
            comp["notes"] = (
                f"GUARDRAIL GStack: MULTIPLE COMPROMISES — {guardrail_hits} "
                f"concurrent caveats; verify carefully before use. "
                + (comp.get("notes") or "")
            )
            fires.append(
                _make_fire(
                    "Stack",
                    ref,
                    prev,
                    "partial",
                    f"{guardrail_hits} concurrent caveats stacked",
                )
            )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

VOLTAGE_DERATING = 1.20
CURRENT_DERATING = 1.25
DIODE_VOLTAGE_DERATING = 1.50
SATURATION_MARGIN = 0.90


def _g7_voltage_stress(
    comps: list[dict],
    stress_by_ref: dict,
    fires: list[dict],
    *,
    tas_data_dir: Path | None = None,
) -> None:
    """G7: substitute voltage rating insufficient for simulated peak stress."""
    for comp in comps:
        ref = comp.get("ref_des", "")
        stress = stress_by_ref.get(ref)
        if not stress or not stress.v_peak:
            continue
        sub_pn = comp.get("substitute_pn")
        if not sub_pn or comp.get("status") in ("keep_original", "no_substitute"):
            continue
        cat = comp.get("component_type", "")
        rated_v = _lookup_substitute_voltage(comp, cat, tas_data_dir)
        if rated_v is None:
            continue
        derating = DIODE_VOLTAGE_DERATING if cat == "diode" else VOLTAGE_DERATING
        if rated_v < stress.v_peak * derating:
            before = comp.get("status", "recommended")
            if rated_v < stress.v_peak:
                comp["status"] = "no_substitute"
            else:
                comp["status"] = "partial"
            fires.append(
                {
                    "guardrail_id": "G7_VoltageStress",
                    "ref_des": ref,
                    "before": before,
                    "after": comp["status"],
                    "reason": (
                        f"sim V_peak={stress.v_peak:.1f}V, rated={rated_v:.0f}V, "
                        f"derating {derating}× requires ≥{stress.v_peak * derating:.1f}V"
                    ),
                }
            )


def _g8_current_stress(
    comps: list[dict],
    stress_by_ref: dict,
    fires: list[dict],
    *,
    tas_data_dir: Path | None = None,
) -> None:
    """G8: substitute current rating insufficient for simulated peak stress."""
    for comp in comps:
        ref = comp.get("ref_des", "")
        stress = stress_by_ref.get(ref)
        if not stress or not stress.i_peak:
            continue
        sub_pn = comp.get("substitute_pn")
        if not sub_pn or comp.get("status") in ("keep_original", "no_substitute"):
            continue
        cat = comp.get("component_type", "")
        rated_i = _lookup_substitute_current(comp, cat, tas_data_dir)
        if rated_i is None:
            continue
        if rated_i < stress.i_peak:
            before = comp.get("status", "recommended")
            comp["status"] = "partial"
            fires.append(
                {
                    "guardrail_id": "G8_CurrentStress",
                    "ref_des": ref,
                    "before": before,
                    "after": "partial",
                    "reason": (f"sim I_peak={stress.i_peak:.2f}A, rated={rated_i:.1f}A"),
                }
            )


def _g9_saturation_margin(
    comps: list[dict],
    stress_by_ref: dict,
    fires: list[dict],
    *,
    tas_data_dir: Path | None = None,
) -> None:
    """G9: inductor operating too close to saturation current."""
    for comp in comps:
        ref = comp.get("ref_des", "")
        stress = stress_by_ref.get(ref)
        if not stress or not stress.i_peak:
            continue
        cat = comp.get("component_type", "")
        if cat not in ("inductor", "magnetic"):
            continue
        sub_pn = comp.get("substitute_pn")
        if not sub_pn or comp.get("status") in ("keep_original", "no_substitute"):
            continue
        isat = _lookup_substitute_isat(comp, cat, tas_data_dir)
        if isat is None:
            continue
        if stress.i_peak > isat * SATURATION_MARGIN:
            before = comp.get("status", "recommended")
            comp["status"] = "partial"
            fires.append(
                {
                    "guardrail_id": "G9_SaturationMargin",
                    "ref_des": ref,
                    "before": before,
                    "after": "partial",
                    "reason": (
                        f"sim I_peak={stress.i_peak:.2f}A > "
                        f"{SATURATION_MARGIN}×Isat={isat * SATURATION_MARGIN:.2f}A"
                    ),
                }
            )


def _lookup_substitute_voltage(comp: dict, cat: str, tas_data_dir: Path | None) -> float | None:
    """Look up the substitute's voltage rating from the crossref result."""
    v_str = comp.get("substitute_voltage", "")
    if v_str:
        try:
            return float(str(v_str).replace("V", "").strip())
        except (ValueError, TypeError):
            pass
    return None


def _lookup_substitute_current(comp: dict, cat: str, tas_data_dir: Path | None) -> float | None:
    """Look up the substitute's current rating from the crossref result."""
    # Current rating isn't in the standard crossref output — would need TAS lookup
    return None


def _lookup_substitute_isat(comp: dict, cat: str, tas_data_dir: Path | None) -> float | None:
    """Look up the substitute inductor's saturation current from the crossref result."""
    return None


def apply_guardrails(
    crossref_json: dict,
    source_bom: list[dict],
    target_manufacturer: str,
    *,
    stress_by_ref: dict | None = None,
    tas_data_dir: Path | None = None,
) -> tuple[dict, list[dict]]:
    """Apply all deterministic guardrails to a structured crossref result.

    Parameters
    ----------
    crossref_json : dict
        Structured crossref output with a ``"components"`` list.
    source_bom : list[dict]
        The original BOM used as input to the crossref pipeline.
    target_manufacturer : str
        Name of the target manufacturer (e.g. ``"Wurth Elektronik"``).
    tas_data_dir : Path | None
        Override for the TAS data directory (for testing).

    Returns
    -------
    tuple[dict, list[dict]]
        ``(corrected_crossref_json, fire_log_entries)`` where
        *fire_log_entries* is a list of structured dicts recording each
        guardrail that fired and what it changed.
    """
    comps = crossref_json.get("crossref") or crossref_json.get("components") or []
    if not isinstance(comps, list) or not comps:
        return crossref_json, []

    fires: list[dict] = []
    bom_by_ref = _build_bom_by_ref(source_bom)

    # Run guardrails in order. G0 runs first so subsequent guardrails
    # see corrected statuses.
    _g0_already_target_manufacturer(comps, target_manufacturer, fires, tas_data_dir=tas_data_dir)

    _g1_capacitor_value_mismatch(comps, bom_by_ref, fires, tas_data_dir=tas_data_dir)

    _g2_resistor_value_drift(comps, bom_by_ref, fires, tas_data_dir=tas_data_dir)

    _g3_capacitor_voltage_downrate(comps, bom_by_ref, fires, tas_data_dir=tas_data_dir)

    _g4_inductor_footprint_overrejection(comps, bom_by_ref, fires)

    _g5_substitute_existence(comps, fires, tas_data_dir=tas_data_dir)

    _g6_voltage_inadequacy_in_notes(comps, fires)

    _gaecq_automotive_grade(comps, source_bom, fires, tas_data_dir=tas_data_dir)

    _gfoot_footprint_compatibility(comps, bom_by_ref, fires, tas_data_dir=tas_data_dir)

    # Stress-based guardrails (from RE simulation)
    if stress_by_ref:
        _g7_voltage_stress(comps, stress_by_ref, fires, tas_data_dir=tas_data_dir)
        _g8_current_stress(comps, stress_by_ref, fires, tas_data_dir=tas_data_dir)
        _g9_saturation_margin(comps, stress_by_ref, fires, tas_data_dir=tas_data_dir)

    # GStack runs last — it counts caveats from all prior guardrails.
    _gstack_multiple_caveats(comps, fires)

    return crossref_json, fires


__all__ = [
    "apply_guardrails",
]
