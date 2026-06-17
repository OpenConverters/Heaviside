"""The cross-reference Jobs-UI stages must stay in lock-step with the messages
run_crossref_pipeline / run_crossref_with_cre emit. Guards against drift: rename
a pipeline phase and this fails instead of the Jobs flow silently stalling."""

from __future__ import annotations

import pytest

from heaviside.api.server import (
    _CROSSREF_CORE_STAGES,
    _CROSSREF_FULL_STAGES,
    _CROSSREF_URL_STAGES,
    _crossref_stage_for_message,
)

# (message, expected-stage) for the CR core — keep in sync with the _say(...)
# calls in run_crossref_pipeline.
_CORE = [
    ("Prefetching TAS candidates for 12 components", "Prefetch TAS candidates"),
    ("Librarian: sourcing any missing components from datasheets/distributors",
     "Librarian: source missing parts"),
    ("Pre-classifying each component by category", "Pre-classify components"),
    ("Cross-referencing to Murata (LLM picks equivalents)", "Cross-reference (LLM)"),
    ("Applying guardrails (voltage/current/package physics checks)", "Guardrails"),
    ("Scoring the substitute candidates", "Score candidates"),
    ("Otto challenge (field-sales rebuttal of every no-substitute)", "Otto challenge"),
    ("Deterministic in-kind rescue for residual gaps", "In-kind rescue"),
    ("Adversarial review (Ray + Nicola)", "Review: Ray + Nicola"),
    ("Correction loop 1: addressing 3 reviewer objections", "Review: Ray + Nicola"),
    ("Learning from this run (persisting accepted substitutions)", "Learn"),
]

# (message, expected-stage) for the RE prefix — keep in sync with the _say(...)
# calls in run_crossref_with_cre.
_CRE = [
    ("Extracting the reference document (text + tables)", "Extract reference document"),
    ("Spec extract: Vin/Vout/topology/fsw from the reference", "Spec extract"),
    ("Reverse-engineering the schematic + topology", "Reverse-engineer schematic"),
    ("Verifying extracted MPNs against the catalog", "Verify MPNs"),
    ("Extracting RDS(on) for the power FETs", "Extract RDS(on)"),
    ("Extracting the reference's datasheet performance claims", "Extract datasheet claims"),
    ("Testbench: simulating the reference design", "Testbench simulation"),
    ("RE→CR bridge: extracting per-component V/I stress from the sim", "RE→CR stress bridge"),
]


@pytest.mark.parametrize("msg,expected", _CORE + _CRE)
def test_message_maps_to_expected_stage(msg, expected):
    assert _crossref_stage_for_message(msg) == expected


def test_core_messages_reach_every_core_stage():
    reached = {_crossref_stage_for_message(m) for m, _ in _CORE}
    missing = [s for s in _CROSSREF_CORE_STAGES if s not in reached]
    assert not missing, f"core stages never reached by any message: {missing}"


def test_cre_messages_reach_every_cre_prefix_stage():
    reached = {_crossref_stage_for_message(m) for m, _ in _CRE}
    # the full list is RE prefix + core; the prefix is everything before the core
    prefix = [s for s in _CROSSREF_FULL_STAGES if s not in _CROSSREF_CORE_STAGES]
    missing = [s for s in prefix if s not in reached]
    assert not missing, f"RE prefix stages never reached: {missing}"


def test_every_mapped_stage_is_declared_in_the_url_superset():
    """The URL flow declares the widest stage set; every message must map into
    it (so nothing renders off-list)."""
    for msg, expected in _CORE + _CRE:
        assert expected in _CROSSREF_URL_STAGES, (msg, expected)


def test_core_stages_in_emission_order():
    first_seen: list[str] = []
    for msg, _ in _CORE:
        stage = _crossref_stage_for_message(msg)
        if stage is not None and stage not in first_seen:
            first_seen.append(stage)
    assert first_seen == _CROSSREF_CORE_STAGES


def test_download_stage_only_in_url_flow():
    assert "Download reference" in _CROSSREF_URL_STAGES
    assert "Download reference" not in _CROSSREF_FULL_STAGES
    assert _crossref_stage_for_message("Downloading the reference from x.pdf") == "Download reference"


def test_unknown_message_returns_none():
    assert _crossref_stage_for_message("nothing relevant here") is None
