"""ABT #6: manufacturer-agnostic rated-current fallback for missing Isat.

Guards :func:`_effective_saturation_current` — when a magnetic candidate has no
published ``saturationCurrentPeak``, the CR ranker uses ``ratedCurrent`` as the
current bound; a real ``saturationCurrentPeak`` always wins.
"""
from __future__ import annotations

from heaviside.pipeline.crossref_pipeline import _effective_saturation_current


def test_falls_back_to_rated_current_when_isat_missing() -> None:
    elec = {"ratedCurrent": 2.5}  # no saturationCurrentPeak
    assert _effective_saturation_current(elec) == 2.5


def test_fallback_is_manufacturer_agnostic() -> None:
    elec = {"ratedCurrent": 2.5}
    # same result regardless of who made it — no manufacturer scoping
    assert _effective_saturation_current(elec) == 2.5


def test_real_isat_always_wins_over_rated_current() -> None:
    elec = {"saturationCurrentPeak": 0.27, "ratedCurrent": 1.5}
    assert _effective_saturation_current(elec) == 0.27


def test_missing_both_stays_none() -> None:
    assert _effective_saturation_current({}) is None
    assert _effective_saturation_current({"saturationCurrentPeak": None}) is None
