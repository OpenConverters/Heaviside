"""Unit tests for normalize_reviewer_verdict — Ray/Nicola JSON verdict
validation + natural-vocabulary mapping (see llm_call.normalize_reviewer_verdict)."""

from __future__ import annotations

import pytest

from heaviside.agents.llm_call import LLMCallError, normalize_reviewer_verdict


@pytest.mark.parametrize("raw,expected", [
    ("APPROVED", "APPROVED"),
    ("REJECTED", "REJECTED"),
    ("INCOMPLETE", "INCOMPLETE"),
    ("approved", "APPROVED"),
    # Ray's persona vocabulary
    ("PROCEED", "APPROVED"),
    ("PROCEED WITH CAUTION", "APPROVED"),
    ("😤 PROCEED WITH CAUTION", "APPROVED"),
    ("NOT ACCEPTABLE", "REJECTED"),
    ("STILL NOT ACCEPTABLE", "REJECTED"),
    # Nicola's vocabulary
    ("NOT_APPROVED", "REJECTED"),
    ("NOT APPROVED", "REJECTED"),
    # other common phrasings
    ("REJECT", "REJECTED"),
    ("APPROVED WITH CAVEATS", "APPROVED"),
    ("PASS", "APPROVED"),
])
def test_verdict_mapping(raw, expected):
    out = normalize_reviewer_verdict({"verdict": raw, "objections": []}, "ray")
    assert out["verdict"] == expected


def test_not_approved_not_misread_as_approved():
    # "NOT APPROVED" contains "APPROV" — must map to REJECTED, not APPROVED.
    assert normalize_reviewer_verdict({"verdict": "NOT APPROVED"}, "nicola")["verdict"] == "REJECTED"


def test_objections_defaulted_to_list():
    out = normalize_reviewer_verdict({"verdict": "APPROVED"}, "ray")
    assert out["objections"] == []


@pytest.mark.parametrize("bad", [
    {"verdict": {"verdict": "pass"}, "topology": "buck"},  # echoed input dict
    {"scratchpad": "...reasoning..."},                       # scratchpad blob
    {"verdict": "maybe"},                                     # unmappable wording
    {"verdict": ""},                                          # empty
    {"objections": []},                                       # no verdict key
    "not even a dict",                                        # wrong type
])
def test_malformed_raises(bad):
    with pytest.raises(LLMCallError):
        normalize_reviewer_verdict(bad, "ray")


def test_bad_objections_type_raises():
    with pytest.raises(LLMCallError):
        normalize_reviewer_verdict({"verdict": "APPROVED", "objections": "nope"}, "ray")
