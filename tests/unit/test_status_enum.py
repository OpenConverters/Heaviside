"""SubstitutionStatus is the closed set of cross-reference verdicts.

keep_original is folded into exact (the user doesn't care about that
distinction — a kept part is an exact match to itself), and coerce() rejects
anything outside the enum so a typo/invented status can never reach output.
"""

from __future__ import annotations

import pytest

from heaviside.pipeline.crossref import SubstitutionStatus as S


def test_canonical_set_is_closed():
    assert {m.value for m in S} == {"exact", "recommended", "partial", "no_substitute"}
    assert not hasattr(S, "KEEP_ORIGINAL")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("exact", S.EXACT),
        ("recommended", S.RECOMMENDED),
        ("partial", S.PARTIAL),
        ("no_substitute", S.NO_SUBSTITUTE),
        ("keep_original", S.EXACT),  # folded
        ("already_target", S.EXACT),  # folded
        ("keep", S.EXACT),  # folded
        (S.PARTIAL, S.PARTIAL),  # already a member
    ],
)
def test_coerce_normalises(raw, expected):
    assert S.coerce(raw) is expected


@pytest.mark.parametrize("bad", ["", "recommend", "keepOriginal", "exactly", "typo", None])
def test_coerce_rejects_unknown(bad):
    with pytest.raises(ValueError, match="unknown substitution status"):
        S.coerce(bad)
