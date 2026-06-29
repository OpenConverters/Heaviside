"""Tests for the isat post-filter ``heaviside.bridge.select_fast_by_isat_margin``
and its two helpers (_ipeak_worst_buck, _isat_from_mas).

della-Pollock cutover (abt #48): the post-filter used to live in the
``_select_main_by_isat_margin`` picker (consumed by the retired
``design_converter_components``); it now lives in ``select_fast_by_isat_margin``
(the stage-2 path), which DESIGNS the candidate pool via ``design_magnetics_fast``
(here mocked) and returns the CLEARING SUBSET — falling through to the full
(unfiltered) pool, honestly, when nothing clears, so the realism gate fails the
design rather than this layer hiding it.

Coverage: the post-filter keeps the candidates clearing the isat margin;
falls back to the full pool when none clear; behaves transparently for
topologies without a registered ``_IPEAK_WORST`` entry.

Candidates here are built from **real, PyOM-evaluable** magnetics
(:func:`real_magnetic`) so ``_isat_from_mas`` exercises the genuine
``PyOpenMagnetics.calculate_saturation_current`` path — the analytical
``B_sat·N·A_e/L`` fallback was deleted (magnetics math lives in MKF, and
the project rule forbids that formula even in test fixtures used as
ground truth). Pass/fail candidates are chosen by gap + turns so their
real Isat straddles the buck spec's ~7.64 A threshold.
"""

from __future__ import annotations

import pytest

from heaviside import bridge
from heaviside.bridge import (
    MagneticDesign,
    _ipeak_worst_buck,
    _isat_from_mas,
    select_fast_by_isat_margin,
)
from heaviside.topologies.registry import get
from tests.unit._real_mas import isat_of, real_magnetic

# Real magnetics whose PyOM Isat@100 °C straddles the buck threshold
# (1.2 × 6.364 A ≈ 7.64 A). Verified: PASS ≈ 22.7 A, FAIL ≈ 1.3 A.
_PASS = {"shape": "ETD 29/16/10", "gap_mm": 1.0, "turns": 14}
_FAIL = {"shape": "ETD 29/16/10", "gap_mm": 0.0, "turns": 6}


def _candidate(
    *,
    score: float,
    shape: str,
    gap_mm: float,
    turns: int,
) -> MagneticDesign:
    """Build a MagneticDesign wrapping a complete, PyOM-evaluable magnetic
    so the post-filter's ``calculate_saturation_current`` call succeeds and
    returns real, gap-aware MKF physics."""
    mas = {
        "magnetic": real_magnetic(
            shape=shape,
            material="3C95",
            gap_mm=gap_mm,
            windings=[{"name": "primary", "turns": turns, "side": "primary"}],
        ),
    }
    return MagneticDesign(scoring=score, mas=mas, elapsed_s=0.0)


_BUCK_SPEC = {
    "inputVoltage": {"nominal": 48.0, "minimum": 36.0, "maximum": 60.0},
    "desiredInductance": 22e-6,
    "operatingPoints": [
        {
            "switchingFrequency": 200000.0,
            "outputVoltages": [12.0],
            "outputCurrents": [5.0],
            "ambientTemperature": 25.0,
        }
    ],
}


def test_ipeak_worst_buck_matches_hand_calc() -> None:
    """For 48->12V@5A, 22 uH, 200 kHz: ripple_worst = Vout*(1-Dmin)/(0.8L*fsw)
    = 12*(1-0.2)/(0.8*22e-6*200000) = 2.727 A; ipeak = 5 + 2.727/2 = 6.364 A.
    """
    ipeak = _ipeak_worst_buck(_BUCK_SPEC)
    assert ipeak == pytest.approx(6.364, abs=1e-3)


@pytest.mark.parametrize(
    "bad_spec",
    [
        {},  # no inputVoltage, no operatingPoints
        {"inputVoltage": {"nominal": 48.0}},  # missing minimum/maximum
        {**_BUCK_SPEC, "desiredInductance": 0},  # zero L
        {**_BUCK_SPEC, "operatingPoints": []},  # empty ops
    ],
)
def test_ipeak_worst_buck_returns_none_on_incomplete_spec(bad_spec: dict) -> None:
    assert _ipeak_worst_buck(bad_spec) is None


def test_ipeak_worst_buck_returns_none_if_vout_exceeds_vmin() -> None:
    """Buck cannot step up — refuse to compute rather than return nonsense."""
    bad = {**_BUCK_SPEC, "inputVoltage": {"minimum": 5.0, "maximum": 60.0}}
    assert _ipeak_worst_buck(bad) is None


def test_isat_from_mas_delegates_to_pyom() -> None:
    """``_isat_from_mas`` returns exactly PyOM's saturation current for the
    magnetic — no analytical formula. Ground truth = MKF."""
    cand = _candidate(score=1.0, **_PASS)
    isat = _isat_from_mas(cand.magnetic, L_henries=22e-6)
    assert isat == pytest.approx(isat_of(cand.magnetic, 100.0), rel=1e-9)
    assert isat > 0


def test_isat_from_mas_is_gap_aware() -> None:
    """A gapped core has a far higher Isat than the same ungapped core —
    the whole reason isat must come from PyOM (which models the gap) and
    not from a gap-blind analytical formula."""
    gapped = _candidate(score=1.0, **_PASS).magnetic
    ungapped = _candidate(score=1.0, **_FAIL).magnetic
    assert _isat_from_mas(gapped, L_henries=22e-6) > _isat_from_mas(ungapped, L_henries=22e-6)


def test_isat_from_mas_returns_none_for_malformed_mas() -> None:
    assert _isat_from_mas({}, L_henries=22e-6) is None
    assert _isat_from_mas({"magnetic": {}}, L_henries=22e-6) is None
    # Structurally incomplete magnetic PyOM cannot evaluate -> None
    # (cannot evaluate, skip the candidate), never a fabricated value.
    half_baked = {
        "core": {
            "processedDescription": {"effectiveParameters": {"effectiveArea": 1e-5}},
        },
        "coil": {"functionalDescription": [{"numberTurns": 10}]},
    }
    assert _isat_from_mas(half_baked, L_henries=22e-6) is None

# ---------------------------------------------------------------------------
# select_fast_by_isat_margin — the post-filter on the stage-2 path (abt #48).
# design_magnetics_fast (Kirchhoff-seeded + PyOM-designed) is mocked so we drive
# the filter with controlled candidates; the isat itself is REAL MKF physics.
# ---------------------------------------------------------------------------


def test_select_fast_keeps_only_clearing_candidate(monkeypatch) -> None:
    """Top scorer FAILS the isat margin (ungapped, ~1.3 A); the gapped one
    PASSES (~22.7 A). Threshold ~1.2x ipeak. The filter must drop the failing
    candidate and return the clearing one."""
    cand_fail = _candidate(score=3.0, **_FAIL)
    cand_pass = _candidate(score=2.0, **_PASS)
    monkeypatch.setattr(bridge, "design_magnetics_fast", lambda *a, **k: [cand_fail, cand_pass])
    cleared = select_fast_by_isat_margin("buck", _BUCK_SPEC, n_candidates=2, min_isat_ratio=1.2)
    assert cleared == [cand_pass]


def test_select_fast_falls_through_to_full_pool_when_none_clear(monkeypatch) -> None:
    """Every candidate is below the threshold (the real buck-48-to-12-at-5A
    situation). The filter widens the pool once, still finds nothing, and
    returns the FULL pool so realism FAILs it honestly — never an empty hide."""
    cands = [_candidate(score=3.0, **_FAIL)]
    monkeypatch.setattr(bridge, "design_magnetics_fast", lambda *a, **k: list(cands))
    result = select_fast_by_isat_margin("buck", _BUCK_SPEC, n_candidates=2, min_isat_ratio=1.2)
    assert result == cands


def test_select_fast_disabled_when_min_ratio_zero(monkeypatch) -> None:
    """min_isat_ratio=0 short-circuits the filter — escape hatch, returns the pool."""
    cands = [_candidate(score=3.0, **_FAIL), _candidate(score=2.0, **_PASS)]
    monkeypatch.setattr(bridge, "design_magnetics_fast", lambda *a, **k: list(cands))
    assert select_fast_by_isat_margin("buck", _BUCK_SPEC, n_candidates=2, min_isat_ratio=0.0) == cands


def test_select_fast_passes_through_without_ipeak_fn(monkeypatch) -> None:
    """For topologies with no registered _IPEAK_WORST entry the filter is a
    no-op (returns the pool). Boost has no entry today."""
    cands = [_candidate(score=3.0, **_FAIL), _candidate(score=2.0, **_PASS)]
    monkeypatch.setattr(bridge, "design_magnetics_fast", lambda *a, **k: list(cands))
    out = select_fast_by_isat_margin(
        "boost", {"inputVoltage": {"nominal": 12.0}}, n_candidates=2, min_isat_ratio=1.2
    )
    assert out == cands


def test_select_fast_skips_candidate_with_unreadable_mas(monkeypatch) -> None:
    """A malformed MAS (unreadable Isat -> None) is skipped, not crashed —
    the next readable, clearing candidate is kept."""
    cand_bad = MagneticDesign(scoring=4.0, mas={"magnetic": {}}, elapsed_s=0.0)
    cand_good = _candidate(score=3.0, **_PASS)
    monkeypatch.setattr(bridge, "design_magnetics_fast", lambda *a, **k: [cand_bad, cand_good])
    cleared = select_fast_by_isat_margin("buck", _BUCK_SPEC, n_candidates=2, min_isat_ratio=1.2)
    assert cleared == [cand_good]
