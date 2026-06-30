"""bom_extract — extract a BOM from a source into PEAS-aligned components.

Engine (deterministic, this module): structured sources — CSV / table
rows / lists of dicts — parsed into a canonical :class:`BomComponent`.
The canonical shape mirrors PEAS: ``category`` is a PEAS oneOf key
(capacitor / magnetic / semiconductor / resistor / controller), specs use
PEAS/CAS field names (capacitance/resistance/inductance, ratedVoltage,
technology, case), values are parsed to SI via ``value_parse``. A
component therefore lines up directly with the PEAS catalogue types in
``heaviside.types`` and with the TAS rows the matcher queries — no field
translation, no ``type`` vs ``component_type`` / ``part`` vs ``mpn`` drift.

The unstructured PDF/image path (LLM) lands in a sibling function on top
of this engine; it returns the SAME :class:`BomComponent` list, so the
deterministic normalize/dedup/validate below is shared and tested once.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from heaviside.pipeline.value_parse import parse_si_value

logger = logging.getLogger(__name__)

# PEAS oneOf component keys (heaviside.types.Peas) — the canonical category set.
PEAS_CATEGORIES = ("capacitor", "magnetic", "semiconductor", "resistor", "controller")

# loose source words -> PEAS category. Includes the TAS *plural* category
# names (capacitors/magnetics/diodes/...) the LLM extractor emits, so a
# reverse-engineered BOM normalizes the same as a hand-written one.
_CATEGORY_ALIASES: dict[str, str] = {
    "capacitor": "capacitor", "capacitors": "capacitor", "cap": "capacitor",
    "mlcc": "capacitor",
    "inductor": "magnetic", "inductors": "magnetic", "magnetic": "magnetic",
    "magnetics": "magnetic", "choke": "magnetic", "transformer": "magnetic",
    "ferrite": "magnetic", "ferrite_bead": "magnetic", "ferrite bead": "magnetic",
    "bead": "magnetic",
    "resistor": "resistor", "resistors": "resistor", "res": "resistor",
    "mosfet": "semiconductor", "mosfets": "semiconductor",
    "diode": "semiconductor", "diodes": "semiconductor",
    "transistor": "semiconductor", "igbt": "semiconductor", "igbts": "semiconductor",
    "bjt": "semiconductor", "semiconductor": "semiconductor",
    "fet": "semiconductor", "rectifier": "semiconductor", "tvs": "semiconductor",
    "ic": "controller", "controller": "controller", "controllers": "controller",
    "regulator": "controller",
}

# source column header (lowercased) -> canonical field
_COLUMN_ALIASES: dict[str, str] = {
    "ref_des": "ref_des", "refdes": "ref_des", "ref": "ref_des",
    "designator": "ref_des", "reference": "ref_des", "designators": "ref_des",
    "type": "category", "category": "category", "component_type": "category",
    "mpn": "mpn", "part": "mpn", "part_number": "mpn", "partnumber": "mpn",
    "manufacturer part number": "mpn", "manufacturer_part_number": "mpn",
    "manufacturer": "manufacturer", "mfr": "manufacturer", "mfg": "manufacturer",
    "value": "value", "val": "value",
    "voltage": "rated_voltage", "rated_voltage": "rated_voltage",
    "rated voltage": "rated_voltage", "vdc": "rated_voltage",
    "package": "package", "footprint": "package", "case": "package", "size": "package",
    "technology": "technology", "dielectric": "technology", "tempco": "technology",
    "temperature coefficient": "technology",
    "qty": "quantity", "quantity": "quantity", "qnty": "quantity",
    "notes": "notes", "note": "notes", "description": "notes",
}

_VALUE_PARSE_UNIT = {"capacitor": "F", "magnetic": "H", "resistor": "Ω"}


@dataclass
class BomComponent:
    """One BOM line, PEAS-aligned. Sparse by nature (a design reference,
    not a full datasheet); fields use PEAS/CAS names so it maps 1:1 onto
    the PEAS catalogue types and TAS rows."""

    ref_des: str
    category: str  # a PEAS_CATEGORIES value, or "" when unclassifiable
    mpn: str | None = None
    manufacturer: str | None = None
    value: str | None = None  # raw/human, e.g. "4.7µF"
    value_si: float | None = None  # parsed SI base unit
    rated_voltage: float | None = None
    package: str | None = None
    technology: str | None = None
    quantity: int = 1
    notes: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_peas_category(self) -> str | None:
        """PEAS oneOf key for this component, or None if unclassified."""
        return self.category if self.category in PEAS_CATEGORIES else None


def normalize_category(raw: str | None) -> str:
    if not raw:
        return ""
    key = str(raw).strip().lower()
    if key in PEAS_CATEGORIES:
        return key
    return _CATEGORY_ALIASES.get(key, "")


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return parse_si_value(v)


def _component_from_fields(fields: Mapping[str, Any]) -> BomComponent | None:
    ref = str(fields.get("ref_des") or "").strip()
    if not ref:
        return None
    category = normalize_category(fields.get("category"))
    value = fields.get("value")
    value = str(value).strip() if value not in (None, "") else None
    value_si = None
    if value is not None and category in _VALUE_PARSE_UNIT:
        value_si = parse_si_value(value)
    qty_raw = fields.get("quantity")
    try:
        quantity = int(float(qty_raw)) if qty_raw not in (None, "") else 1
    except (TypeError, ValueError):
        quantity = 1
    mpn = fields.get("mpn")
    return BomComponent(
        ref_des=ref,
        category=category,
        mpn=str(mpn).strip() if mpn not in (None, "") else None,
        manufacturer=(str(fields["manufacturer"]).strip()
                      if fields.get("manufacturer") not in (None, "") else None),
        value=value,
        value_si=value_si,
        rated_voltage=_to_float(fields.get("rated_voltage")),
        package=(str(fields["package"]).strip()
                 if fields.get("package") not in (None, "") else None),
        technology=(str(fields["technology"]).strip()
                    if fields.get("technology") not in (None, "") else None),
        quantity=quantity,
        notes=(str(fields["notes"]).strip()
               if fields.get("notes") not in (None, "") else None),
        raw=dict(fields),
    )


def _map_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Map a source row's headers onto canonical field names."""
    mapped: dict[str, Any] = {}
    for k, v in row.items():
        if k is None:
            continue
        key = str(k).strip().lower()
        canon = (_COLUMN_ALIASES.get(key)
                 or _COLUMN_ALIASES.get(key.replace(" ", "_"))
                 or _COLUMN_ALIASES.get(key.replace("_", " ")))
        if canon and (canon not in mapped or mapped[canon] in (None, "")):
            mapped[canon] = v
    return mapped


def _expand_grouped_refs(comp: BomComponent) -> list[BomComponent]:
    """A row like 'C1, C2, C3' or 'R1-R4' is several components."""
    import re

    ref = comp.ref_des
    parts = [p.strip() for p in ref.replace(";", ",").split(",") if p.strip()]
    expanded: list[str] = []
    # range forms: "R1-R4" or "R1-4" (prefix + lo - [prefix] + hi)
    range_re = re.compile(r"^([A-Za-z]+)(\d+)\s*-\s*([A-Za-z]*)(\d+)$")
    for p in parts:
        m = range_re.match(p.replace(" ", ""))
        if m:
            prefix, lo, prefix2, hi = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
            if (not prefix2 or prefix2 == prefix) and 0 <= hi - lo <= 200:
                expanded.extend(f"{prefix}{i}" for i in range(lo, hi + 1))
                continue
        expanded.append(p)
    if len(expanded) <= 1:
        return [comp]
    out = []
    for r in expanded:
        from dataclasses import replace
        out.append(replace(comp, ref_des=r, quantity=1))
    return out


def extract_bom_from_rows(rows: Iterable[Mapping[str, Any]]) -> list[BomComponent]:
    """Deterministic engine: list-of-dict / table rows -> BomComponents.
    Headers are matched case-insensitively against known aliases; grouped
    ref-des cells are expanded into individual components."""
    out: list[BomComponent] = []
    for row in rows:
        comp = _component_from_fields(_map_row(row))
        if comp is not None:
            out.extend(_expand_grouped_refs(comp))
    return out


def extract_bom_from_csv(source: str | Path) -> list[BomComponent]:
    """Deterministic engine: a CSV file (or CSV text) -> BomComponents."""
    text = (
        Path(source).read_text(encoding="utf-8")
        if isinstance(source, Path) or (isinstance(source, str) and "\n" not in source
                                        and Path(source).exists())
        else str(source)
    )
    reader = csv.DictReader(io.StringIO(text))
    return extract_bom_from_rows(reader)


# --- extraction-completeness guard --------------------------------------
# One LLM draw of a complex/multi-page BOM table occasionally drops many rows
# (a "bad draw"): the model is stochastic, so the same PDF that usually yields
# the full census sometimes returns a truncated one. Those dropped rows never
# reach the (working) cross-reference, so CR coverage looks low for no reason
# other than the draw — pure run-to-run variance. The guard below re-extracts
# and MERGES only when a draw looks under-complete, recovering the missed rows
# without paying for a second LLM call in the common (already-complete) case.

# A draw is "complete enough" when it covers at least this fraction of the
# reference designators we detected in the source text. Below it, we re-draw
# and merge. 0.90 tolerates the long tail of designators the text mentions but
# the BOM legitimately omits (DNP ranges, test points written as ranges, etc.)
# while still catching a draw that dropped a meaningful block of rows.
_COVERAGE_OK_THRESHOLD = 0.90

# Extra re-extraction attempts when a draw is under-covered (so at most 3 draws
# total: the first plus these). Each is merged by ref-des into the running BOM,
# so successive draws can only add coverage, never lose it.
_MAX_EXTRA_DRAWS = 2

# The coverage guard only engages when we detected at least this many distinct
# designators — a tiny excerpt or a sparse PDF can't give a trustworthy
# coverage signal, so we don't spend extra LLM calls chasing it.
_MIN_EXPECTED_REFS_FOR_GUARD = 8

# Reference-designator detector. Matches a designator PREFIX (the conventional
# letters: C R L D Q U J Y T plus the two-letter FB / MH) immediately followed
# by 1-3 digits, e.g. C1, R12, U1, L2, FB1, MH4. Deliberately conservative to
# avoid false positives:
#   * the digit suffix is required, so dielectric codes like "C0G" (letter
#     after the digit -> the trailing (?!\w) fails) and bare package sizes like
#     "0402" (no letter prefix) are NOT counted;
#   * (?<![\w-]) forbids a leading word char or hyphen, so MPNs ("...C81...")
#     and qualification tags ("AEC-Q200") don't masquerade as designators;
#   * matching is upper-case only — real designators are upper-case, and this
#     keeps lower-case prose ("u1 of the figure") from inflating the count.
_REFDES_RE = re.compile(r"(?<![\w-])(?:FB|MH|[CRLDQUJYT])\d{1,3}(?!\w)")

# Fields whose presence makes a BOM row "more populated"; on a ref-des collision
# during a merge we keep the row that fills more of these (more useful to CR).
_MERGE_RICHNESS_FIELDS = (
    "mpn", "value", "value_si", "technology",
    "manufacturer", "rated_voltage", "package", "notes",
)


def _detect_expected_refdes(text: str) -> set[str]:
    """Conservatively detect the reference designators present in source text.

    Returns the de-duplicated set of designator tokens (C1, R12, U1, ...). This
    is the *expected* part list the extraction should cover; it intentionally
    under-counts (ranges like "C22-C24" contribute only C22, and codes/packages
    are excluded) so the coverage signal errs toward NOT triggering spurious
    re-draws."""
    return set(_REFDES_RE.findall(text or ""))


def _refdes_coverage(bom: list[BomComponent], expected: set[str]) -> float:
    """Fraction of *expected* designators that appear in *bom* (1.0 if nothing
    was expected — no signal means nothing to flag)."""
    if not expected:
        return 1.0
    present = {c.ref_des for c in bom}
    return len(expected & present) / len(expected)


def _richness(comp: BomComponent) -> int:
    """How many of the CR-relevant fields this row actually fills."""
    return sum(
        1 for f in _MERGE_RICHNESS_FIELDS if getattr(comp, f) not in (None, "")
    )


def _merge_boms(primary: list[BomComponent], secondary: list[BomComponent]) -> list[BomComponent]:
    """Union two draws by ref-des. ``primary`` order is preserved and its rows
    are kept unless ``secondary`` has the SAME ref-des with strictly more
    populated fields (the better-detailed row wins); ref-des only in
    ``secondary`` are appended. A merge can only add coverage, never drop it."""
    by_ref: dict[str, BomComponent] = {}
    order: list[str] = []
    for comp in (*primary, *secondary):
        r = comp.ref_des
        if r not in by_ref:
            by_ref[r] = comp
            order.append(r)
        elif _richness(comp) > _richness(by_ref[r]):
            by_ref[r] = comp
    return [by_ref[r] for r in order]


def extract_bom_from_pdf(
    pdf_path: str | Path | None = None,
    *,
    reference: str | None = None,
    pdf_text: str | None = None,
) -> list[BomComponent]:
    """LLM adapter: an unstructured datasheet / eval-board PDF -> the SAME
    canonical BomComponents. The LLM boundary is the ``bom-extractor`` agent
    (a COMPLETE BOM census — every line item, every reference designator),
    then the raw rows go through the deterministic engine above so
    normalization/field-drift/grouped-ref-expansion are shared and tested
    once. Requires MOONSHOT_API_KEY in the environment.

    Note the deliberate choice of agent: the RE ``reverse-engineer`` agent
    extracts only the *power-path* components (what the converter designer
    needs), so it under-counts vs a full BOM. ``bom-extractor`` is scoped to
    the full parts census, which is what "extract a BOM" means here.

    Pass ``pdf_text`` to skip PDF text extraction and feed text straight to
    the extractor (caller already has the text, or to keep a test fast on a
    small excerpt).

    A single LLM draw of a complex BOM is stochastic and occasionally drops a
    block of rows. To make the result reproducible we estimate the expected
    reference designators from the source text and, ONLY when a draw covers too
    few of them, re-extract and merge by ref-des (see the guard constants
    above). The common case — a draw that is already complete — costs exactly
    one LLM call. A merged BOM that is still short of the expected designators
    is surfaced via a loud WARNING rather than silently passed off as complete."""
    if pdf_path is None and pdf_text is None:
        raise ValueError("extract_bom_from_pdf: provide pdf_path or pdf_text")
    ref = reference or (Path(pdf_path).stem if pdf_path else "bom")
    if pdf_text is None:
        from heaviside.pipeline.pdf_extract import extract_pdf_text

        pdf_text = extract_pdf_text(Path(pdf_path))

    expected = _detect_expected_refdes(pdf_text)

    # Reading before thinking: when the PDF carries a structured ruled BOM table
    # covering the detected designators, parse it deterministically — variance-
    # free, no LLM call. Only "wins" when it provably covers >= the coverage
    # threshold; otherwise fall through to the LLM census below.
    if pdf_path is not None and len(expected) >= _MIN_EXPECTED_REFS_FOR_GUARD:
        from heaviside.stages.bom_table import parse_bom_table

        table_rows = parse_bom_table(pdf_path)
        if table_rows:
            det_bom: list[BomComponent] = []
            for row in table_rows:
                comp = _component_from_fields(row)
                if comp is not None:
                    det_bom.extend(_expand_grouped_refs(comp))
            det_cov = _refdes_coverage(det_bom, expected)
            if det_cov >= _COVERAGE_OK_THRESHOLD:
                logger.info(
                    "bom_extract[%s]: deterministic BOM-table parse — %d components, "
                    "coverage %.0f%% of %d designators (no LLM)",
                    ref, len(det_bom), det_cov * 100, len(expected),
                )
                return det_bom
            logger.info(
                "bom_extract[%s]: deterministic table coverage %.0f%% < %.0f%% — "
                "falling back to LLM census",
                ref, det_cov * 100, _COVERAGE_OK_THRESHOLD * 100,
            )

    bom = extract_bom_from_rows(_extract_full_bom_rows(pdf_text, ref))

    # Too few designators to trust the coverage signal -> accept the draw as-is.
    if len(expected) < _MIN_EXPECTED_REFS_FOR_GUARD:
        return bom

    coverage = _refdes_coverage(bom, expected)
    if coverage >= _COVERAGE_OK_THRESHOLD:
        return bom

    # Under-covered first draw -> likely a bad draw. Re-extract and merge,
    # recovering the dropped rows. Successive draws only add coverage.
    logger.warning(
        "bom_extract[%s]: first draw covers %.0f%% of %d detected designators "
        "(below %.0f%%) — re-extracting to recover dropped rows",
        ref, coverage * 100, len(expected), _COVERAGE_OK_THRESHOLD * 100,
    )
    for _ in range(_MAX_EXTRA_DRAWS):
        bom = _merge_boms(bom, extract_bom_from_rows(_extract_full_bom_rows(pdf_text, ref)))
        coverage = _refdes_coverage(bom, expected)
        if coverage >= _COVERAGE_OK_THRESHOLD:
            break

    if coverage < _COVERAGE_OK_THRESHOLD:
        missing = sorted(expected - {c.ref_des for c in bom})
        logger.warning(
            "bom_extract[%s]: KNOWN-INCOMPLETE BOM — after %d draws coverage is "
            "%.0f%% (%d/%d designators); missing %d: %s",
            ref, _MAX_EXTRA_DRAWS + 1, coverage * 100,
            len(expected) - len(missing), len(expected), len(missing), missing,
        )
    return bom


def _extract_full_bom_rows(
    pdf_text: str, reference: str, *, extra_instructions: str | None = None
) -> list[dict[str, Any]]:
    """LLM boundary: full-BOM census via the ``bom-extractor`` agent. Returns
    the raw row dicts with grouped ``ref_des`` preserved (e.g. "C1, C3, C59,
    C63") for the deterministic engine to expand. JSON mode keeps the model
    from rambling past the JSON (kimi appends prose otherwise).

    ``extra_instructions`` is appended ahead of the PDF body (so a review-loop
    feedback note survives the 200k-char truncation); the RE pipeline uses it to
    feed reviewer objections back into a re-extraction."""
    from heaviside.agents.llm_call import call_agent_json

    msg = (
        f"Reference design: {reference}\n\n"
        f"Extract the COMPLETE bill of materials from this PDF.\n"
        + (f"\n{extra_instructions}\n" if extra_instructions else "")
        + f"\nPDF CONTENT:\n\n{pdf_text[:200_000]}"
    )
    # Full BOM + descriptions need output headroom; scale with input size.
    max_tokens = min(8192 + len(pdf_text) // 2, 32768)
    data = call_agent_json(
        "bom-extractor", msg, max_tokens=max_tokens, max_retries=2, json_mode=True
    )
    return data.get("bom", []) or []
