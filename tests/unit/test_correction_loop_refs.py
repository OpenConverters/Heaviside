"""The correction loop must map reviewer objections to the *actual* ref_des in the
result — including synthetic ones like ``CMP#25`` (assigned to pasted rows with no
designator) and an explicit ``ref_des`` field on a structured objection — not a
bare ``[A-Z]+\\d+`` regex. Otherwise it logs "no ref_des found in objections" and
spins through re-review rounds without being able to act (the prod warning)."""

from __future__ import annotations

from heaviside.pipeline.crossref_pipeline import _objection_refs

_KNOWN = {"C1", "C10", "R12", "J1", "CMP#25", "U3"}


def test_conventional_ref_matched():
    assert _objection_refs(["C1 exceeds voltage rating"], _KNOWN) == {"C1"}


def test_c1_not_matched_inside_c10():
    # "C10" must not also pull in "C1".
    assert _objection_refs(["the cap C10 is the wrong value"], _KNOWN) == {"C10"}


def test_synthetic_ref_with_hash_matched():
    # CMP#25 is what pasted rows with no designator get — the old regex missed it.
    assert _objection_refs(["CMP#25 terminal-block pitch mismatch"], _KNOWN) == {"CMP#25"}


def test_dict_objection_ref_field():
    assert _objection_refs([{"ref_des": "J1", "issue": "gender mismatch"}], _KNOWN) == {"J1"}


def test_dict_objection_grouped_refs():
    assert _objection_refs([{"ref_des": "C1, R12", "issue": "x"}], _KNOWN) == {"C1", "R12"}


def test_no_actionable_ref_returns_empty():
    assert _objection_refs(["general concern about derating margins"], _KNOWN) == set()


def test_never_invents_an_unknown_ref():
    # A cited ref that isn't in the result is dropped — never fabricated.
    assert _objection_refs(["unknown ref Z99 cited here"], _KNOWN) == set()
