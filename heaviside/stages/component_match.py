"""component_match — find TAS substitute candidates for a spec, in-kind.

Engine (deterministic, this module): given a component spec (category +
value + technology + voltage + package) and a target manufacturer, return
a correct, complete, RANKED candidate list from TAS. This is the single
matcher both the CR pipeline and the designer should share; it reuses the
proven, technology-aware ranking in ``crossref_pipeline._rank_candidates``
(the ceramic-vs-supercap fix lives there) behind a clean PEAS-typed
interface. The optional LLM ``select_candidate`` layer picks the best of
a sound set (it may choose a non-top candidate for footprint/lifecycle/
application reasons) — it can only choose AMONG these candidates, never
invent one.
"""
from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# PEAS category -> TAS ndjson file
_CATEGORY_FILE = {
    "capacitor": "capacitors.ndjson",
    "resistor": "resistors.ndjson",
    "magnetic": "magnetics.ndjson",
    "semiconductor": None,  # split across mosfets/diodes/igbts — handled below
}
_SEMI_FILES = ("mosfets.ndjson", "diodes.ndjson", "igbts.ndjson")
_CR_CATEGORY = {"capacitor": "capacitor", "resistor": "resistor", "magnetic": "magnetic"}


@dataclass
class Candidate:
    """A ranked TAS substitute, PEAS-aligned summary."""

    mpn: str
    category: str
    manufacturer: str | None
    value_si: float | None
    voltage: float | None
    technology: str | None
    package: str | None
    rank: int
    env: dict[str, Any] = field(default_factory=dict)  # full PEAS/TAS envelope


def _tas_dir() -> Path:
    return Path(
        os.environ.get(
            "HEAVISIDE_TAS_DATA_DIR",
            str(Path(__file__).resolve().parents[2] / "TAS" / "data"),
        )
    )


def _category_files(category: str) -> tuple[str, ...]:
    if category == "semiconductor":
        return _SEMI_FILES
    f = _CATEGORY_FILE.get(category)
    return (f,) if f else ()


def find_candidates(
    *,
    category: str,
    target_manufacturer: str,
    value_si: float | None = None,
    technology: str | None = None,
    min_voltage: float | None = None,
    package: str | None = None,
    max_results: int = 50,
) -> list[Candidate]:
    """Deterministic engine: ranked in-technology TAS candidates from the
    target manufacturer. Reuses the crossref ranker so the
    ceramic/technology/voltage invariants are enforced in one place."""
    from heaviside.catalogue._reader import CatalogueReadError, iter_envelopes
    from heaviside.pipeline.crossref_pipeline import (
        _extract_manufacturer,
        _extract_value,
        _normalize_manufacturer,
        _rank_candidates,
        _summarize_candidate,
    )

    cr_cat = _CR_CATEGORY.get(category)
    if cr_cat is None:
        # semiconductor ranking is type-specific; not yet unified here
        raise ValueError(
            f"component_match: category {category!r} not yet supported "
            f"(supported: {sorted(_CR_CATEGORY)})"
        )

    target = _normalize_manufacturer(target_manufacturer)
    tas_dir = _tas_dir()
    rows: list[dict[str, Any]] = []
    for fname in _category_files(category):
        path = tas_dir / fname
        if not path.exists():
            continue
        try:
            for _lineno, env in iter_envelopes(path):
                mfr = _extract_manufacturer(env, cr_cat)
                if mfr and target in _normalize_manufacturer(mfr):
                    rows.append(env)
        except CatalogueReadError:
            continue

    # Build the BOM-spec shape _rank_candidates expects, including the
    # technology hint so the family penalty fires.
    comp: dict[str, Any] = {"component_type": cr_cat}
    if value_si is not None:
        comp["value"] = _humanize(value_si, cr_cat)
    if min_voltage is not None:
        comp["rated_voltage"] = str(min_voltage)
    if package:
        comp["package"] = package
    if technology:
        comp["technology"] = technology

    ranked = _rank_candidates(comp, cr_cat, rows, max_results=max_results)
    out: list[Candidate] = []
    for i, env in enumerate(ranked):
        s = _summarize_candidate(env, cr_cat)
        out.append(Candidate(
            mpn=s.get("mpn") or "?",
            category=category,
            manufacturer=_extract_manufacturer(env, cr_cat),
            value_si=_extract_value(env, cr_cat),
            voltage=s.get("voltage"),
            technology=s.get("technology"),
            package=s.get("package"),
            rank=i,
            env=env,
        ))
    return out


def _humanize(value_si: float, category: str) -> str:
    """SI -> a human value string _rank_candidates can re-parse."""
    if category == "capacitor":
        return f"{value_si * 1e6:g}uF"
    if category == "magnetic":
        return f"{value_si * 1e6:g}uH"
    if category == "resistor":
        return f"{value_si:g}"
    return str(value_si)


def select_candidate(
    candidates: Iterable[Candidate],
    *,
    original_mpn: str,
    requirement: str = "",
) -> Candidate | None:
    """Optional LLM layer: pick the best candidate from a SOUND set. May
    choose a non-#1 for footprint/lifecycle/application reasons, but can
    only return one of the given candidates (never fabricates). Falls back
    to the top-ranked candidate when no LLM key is configured."""
    cands = list(candidates)
    if not cands:
        return None
    if not os.environ.get("MOONSHOT_API_KEY"):
        return cands[0]
    import json

    from heaviside.agents.llm_call import call_agent_json

    options = [
        {"index": i, "mpn": c.mpn, "value_si": c.value_si, "voltage": c.voltage,
         "technology": c.technology, "package": c.package}
        for i, c in enumerate(cands[:25])
    ]
    msg = json.dumps({
        "task": "Pick the single best drop-in substitute for the original part.",
        "original_mpn": original_mpn,
        "requirement": requirement,
        "candidates": options,
        "instructions": "Return {\"index\": <int>} choosing ONE candidate by its "
                        "index. Prefer same package and adequate voltage; you may "
                        "pick a non-first candidate when it fits better. Choose only "
                        "from the given candidates.",
    })
    try:
        data = call_agent_json("cross-referencer", msg, max_tokens=1024, max_retries=1)
        idx = int(data.get("index"))
        if 0 <= idx < len(cands):
            return cands[idx]
    except Exception:
        pass
    return cands[0]
