"""Regression: a large LumiQuote BOM ("Test BOM -V2.xlsx", 847 rows) must
cross-reference instead of failing.

Two prod failures are guarded here:
1. The whole BOM was sent to the cross-referencer in ONE LLM call, producing a
   ~580k-token request that the API rejected (400 "exceeded model token limit:
   262144") — so the run referenced nothing. `_stage3_crossref` now batches.
2. The BOM has no value/package columns (they live in the description), so
   ranking had nothing to filter on and footprint-fit warned on every row.
   `_normalize_bom` now recovers value + package from the description.

All deterministic — no LLM calls. The real distributor export is committed at
tests/fixtures/lumiquote_bom_v2.xlsx.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from heaviside.pipeline.bom_import import parse_bom_file
from heaviside.pipeline.crossref import CrossRefState
from heaviside.pipeline.crossref_pipeline import (
    _CROSSREF_BATCH_MAX_PARTS,
    _batch_for_llm,
    _build_bom_for_llm,
    _normalize_bom,
    _stage1_prefetch,
)

# The cross-referencer model context window (tokens). Every batch must stay
# under this or the API returns 400 and the crossref produces nothing.
_MODEL_TOKEN_LIMIT = 262_144
# Conservative chars→tokens ratio. The real BOM payload is dense (~2.3
# chars/token); use 2.0 so the test OVER-estimates tokens and stays strict.
_CHARS_PER_TOKEN = 2.0

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"
# Two real LumiQuote exports: v2 = 847 rows, v1 = the 2101-row "final boss".
# Both must parse, enrich, and batch under the token limit deterministically.
_FIXTURES = ["lumiquote_bom_v2.xlsx", "lumiquote_bom_v1.xlsx"]


def _load_normalized(fixture: str):
    path = _FIXTURE_DIR / fixture
    return _normalize_bom(parse_bom_file(path.read_bytes(), path.name))


@pytest.mark.parametrize("fixture", _FIXTURES)
def test_fixture_parses_to_full_bom(fixture):
    nb = _load_normalized(fixture)
    # Both exports are large (v2 ≈ 847, v1 ≈ 2101 rows).
    assert len(nb) > 800
    # Component types were inferred from the description (no category column maps
    # cleanly): the BOM is mostly passives.
    cats = {c.get("component_type") for c in nb}
    assert {"capacitor", "magnetic"} <= cats


@pytest.mark.parametrize("fixture", _FIXTURES)
def test_value_and_package_recovered_for_most_rows(fixture):
    nb = _load_normalized(fixture)
    with_value = sum(1 for c in nb if c.get("value"))
    with_pkg = sum(1 for c in nb if c.get("package"))
    # Values/packages live in the free-text description; we must recover the
    # large majority so ranking can value-filter and footprint-fit has a size.
    assert with_value > 0.7 * len(nb)
    assert with_pkg > 0.5 * len(nb)


def _batches_for_fixture(fixture: str):
    nb = _load_normalized(fixture)
    state = _stage1_prefetch(
        CrossRefState(source_bom=nb, target_manufacturer="Würth Elektronik")
    )
    entries = _build_bom_for_llm(state)
    return entries, _batch_for_llm(entries)


@pytest.mark.parametrize("fixture", _FIXTURES)
def test_large_bom_is_split_into_multiple_batches(fixture):
    entries, batches = _batches_for_fixture(fixture)
    # Unbatched these BOMs are far over the limit, so they MUST split.
    assert len(batches) > 1
    # Every component is covered exactly once across the batches (none dropped).
    assert sum(len(b) for b in batches) == len(entries)


@pytest.mark.parametrize("fixture", _FIXTURES)
def test_no_batch_exceeds_part_cap(fixture):
    """Each batch is capped at ≤50 components so every LLM call stays small and
    fast (and can run concurrently)."""
    _entries, batches = _batches_for_fixture(fixture)
    for b in batches:
        assert len(b) <= _CROSSREF_BATCH_MAX_PARTS


@pytest.mark.parametrize("fixture", _FIXTURES)
def test_every_batch_stays_under_model_token_limit(fixture):
    """The core guard: no single cross-referencer request exceeds the context
    window, so the prod 400 ("exceeded model token limit") cannot recur — even
    for the 2101-row "final boss" BOM."""
    _entries, batches = _batches_for_fixture(fixture)
    assert batches, "expected at least one batch"
    for i, batch in enumerate(batches, 1):
        payload = json.dumps(
            {
                "source_bom": batch,
                "target_manufacturer": "Würth Elektronik",
                "circuit_context": None,
            }
        )
        est_tokens = len(payload) / _CHARS_PER_TOKEN
        assert est_tokens < _MODEL_TOKEN_LIMIT, (
            f"{fixture} batch {i} ~{est_tokens:.0f} tokens exceeds {_MODEL_TOKEN_LIMIT}"
        )


def test_batch_helper_respects_char_budget():
    """Unit-level: _batch_for_llm never exceeds the budget except for a single
    oversized entry (which still goes out alone rather than being dropped)."""
    entries = [{"i": i, "pad": "x" * 1000} for i in range(50)]
    budget = 5000
    batches = _batch_for_llm(entries, max_chars=budget, max_parts=1000)
    assert sum(len(b) for b in batches) == len(entries)  # nothing dropped
    for b in batches:
        if len(b) > 1:
            assert len(json.dumps(b)) <= budget + len(json.dumps(b[0]))


def test_batch_helper_respects_part_cap():
    """Unit-level: the part cap splits even when the char budget is huge."""
    entries = [{"i": i} for i in range(125)]
    batches = _batch_for_llm(entries, max_chars=10_000_000, max_parts=50)
    assert [len(b) for b in batches] == [50, 50, 25]
    assert sum(len(b) for b in batches) == 125


@pytest.mark.parametrize("fixture", _FIXTURES)
def test_prefetch_finds_candidates_for_enriched_passives(fixture):
    """With value recovered, the deterministic prefetch returns real Würth
    candidates for the common passives (the precondition for referencing)."""
    nb = _load_normalized(fixture)
    sub = [c for c in nb if c.get("component_type") in ("capacitor", "magnetic")][:20]
    state = _stage1_prefetch(
        CrossRefState(source_bom=sub, target_manufacturer="Würth Elektronik")
    )
    with_cands = sum(1 for c in sub if state.candidates_by_ref.get(c["ref_des"]))
    # The overwhelming majority of common passives should have candidates.
    assert with_cands >= 0.8 * len(sub)
