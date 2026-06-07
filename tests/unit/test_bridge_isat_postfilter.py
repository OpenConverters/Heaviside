"""Tests for ``heaviside.bridge._select_main_by_isat_margin`` and its
two helpers (_ipeak_worst_buck, _isat_from_mas).

Coverage: post-filter selects the first candidate clearing the isat
margin; falls back honestly to PyMKF's top scorer when no candidate
clears the threshold; behaves transparently for topologies without a
registered ``_IPEAK_WORST`` entry.

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

from heaviside.bridge import (
    BridgeError,
    MagneticDesign,
    _ipeak_worst_buck,
    _isat_from_mas,
    _select_main_by_isat_margin,
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


def test_select_picks_first_passing_candidate() -> None:
    """Top-scorer FAILS the isat margin (ungapped, ~1.3 A), second-best
    PASSES (gapped, ~22.7 A). Threshold is 1.2 * 6.364 = 7.64 A. Post-filter
    must skip candidate 0 and return candidate 1."""
    cand_fail = _candidate(score=3.0, **_FAIL)
    cand_pass = _candidate(score=2.0, **_PASS)
    chosen = _select_main_by_isat_margin(
        [cand_fail, cand_pass],
        get("buck"),
        _BUCK_SPEC,
        min_isat_ratio=1.2,
    )
    assert chosen is cand_pass


def test_select_falls_back_to_top_scorer_when_no_candidate_passes() -> None:
    """Exactly the buck-48-to-12-at-5A real-world situation: every candidate
    is below the 7.64 A threshold. Post-filter MUST return candidate[0] (top
    scorer) so realism can FAIL it honestly."""
    cands = [
        _candidate(score=3.0, **_FAIL),  # ~1.3 A
        _candidate(score=2.5, shape="ETD 29/16/10", gap_mm=0.5, turns=30),  # ~6.2 A
    ]
    chosen = _select_main_by_isat_margin(
        cands,
        get("buck"),
        _BUCK_SPEC,
        min_isat_ratio=1.2,
    )
    assert chosen is cands[0]


def test_select_disabled_when_min_ratio_zero() -> None:
    """min_isat_ratio=0 short-circuits to PyMKF's top scorer — escape hatch."""
    cands = [
        _candidate(score=3.0, **_FAIL),
        _candidate(score=2.0, **_PASS),
    ]
    chosen = _select_main_by_isat_margin(
        cands,
        get("buck"),
        _BUCK_SPEC,
        min_isat_ratio=0.0,
    )
    assert chosen is cands[0]


def test_select_passes_through_when_no_topology_ipeak_fn() -> None:
    """For topologies without a registered _IPEAK_WORST entry, post-filter
    is a no-op (returns top scorer). Boost has no entry today."""
    cands = [
        _candidate(score=3.0, **_FAIL),
        _candidate(score=2.0, **_PASS),
    ]
    chosen = _select_main_by_isat_margin(
        cands,
        get("boost"),
        {"inputVoltage": {"nominal": 12.0}},
        min_isat_ratio=1.2,
    )
    assert chosen is cands[0]


def test_select_empty_pool_raises() -> None:
    with pytest.raises(BridgeError, match="empty candidate list"):
        _select_main_by_isat_margin([], get("buck"), _BUCK_SPEC, min_isat_ratio=1.2)


def test_select_skips_candidate_with_unreadable_mas() -> None:
    """A malformed MAS in the middle of the list should be skipped (its
    Isat is unreadable -> None), not crash — pick the next readable one."""
    cand_bad = MagneticDesign(scoring=4.0, mas={"magnetic": {}}, elapsed_s=0.0)
    cand_good = _candidate(score=3.0, **_PASS)
    chosen = _select_main_by_isat_margin(
        [cand_bad, cand_good],
        get("buck"),
        _BUCK_SPEC,
        min_isat_ratio=1.2,
    )
    # cand_bad has no readable isat -> skipped; cand_good ~22.7 A -> PASS
    assert chosen is cand_good


# ---------------------------------------------------------------------------
# strict=True (tier-2 retry signal)
# ---------------------------------------------------------------------------


def test_strict_returns_none_when_no_candidate_passes() -> None:
    """The interim MKF-isat workaround needs to know when the cheap
    pool exhausted. ``strict=True`` returns None instead of falling
    back to candidates[0] so the caller can retry with a wider pool."""
    cands = [
        _candidate(score=3.0, **_FAIL),  # ~1.3 A
        _candidate(score=2.5, shape="ETD 29/16/10", gap_mm=0.5, turns=30),  # ~6.2 A
    ]
    # buck threshold ~7.64 A (1.2 * 6.364 ipeak); both cands fail.
    chosen = _select_main_by_isat_margin(
        cands,
        get("buck"),
        _BUCK_SPEC,
        min_isat_ratio=1.2,
        strict=True,
    )
    assert chosen is None


def test_strict_still_returns_candidate_when_one_passes() -> None:
    cand_fail = _candidate(score=4.0, **_FAIL)
    cand_pass = _candidate(score=2.0, **_PASS)
    chosen = _select_main_by_isat_margin(
        [cand_fail, cand_pass],
        get("buck"),
        _BUCK_SPEC,
        min_isat_ratio=1.2,
        strict=True,
    )
    assert chosen is cand_pass


def test_strict_still_falls_back_when_no_ipeak_fn_registered() -> None:
    """``strict`` only changes behaviour when a margin check ran. If
    we couldn't compute Ipeak (no topology entry), we still return
    candidates[0] as before — the caller has no signal to retry on."""
    cands = [_candidate(score=3.0, **_FAIL)]
    chosen = _select_main_by_isat_margin(
        cands,
        get("boost"),
        {"inputVoltage": {"nominal": 12.0}},
        min_isat_ratio=1.2,
        strict=True,
    )
    assert chosen is cands[0]
