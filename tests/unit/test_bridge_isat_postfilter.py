"""Tests for ``heaviside.bridge._select_main_by_isat_margin`` and its
two helpers (_ipeak_worst_buck, _isat_from_mas).

Coverage: post-filter selects the first candidate clearing the isat
margin; falls back honestly to PyMKF's top scorer when no candidate
clears the threshold; behaves transparently for topologies without a
registered ``_IPEAK_WORST`` entry.
"""

from __future__ import annotations

from typing import Any

import pytest

from heaviside.bridge import (
    BridgeError,
    MagneticDesign,
    _ipeak_worst_buck,
    _isat_from_mas,
    _select_main_by_isat_margin,
)
from heaviside.topologies.registry import get


def _candidate(
    *, score: float, b_sat: float, n_turns: int, a_e: float,
) -> MagneticDesign:
    """Build a MagneticDesign with the bare minimum MAS shape the
    post-filter inspects (effectiveArea, saturation curve, numberTurns)."""
    mas = {
        "magnetic": {
            "core": {
                "processedDescription": {
                    "effectiveParameters": {"effectiveArea": a_e},
                },
                "functionalDescription": {
                    "material": {
                        "saturation": [
                            {"temperature": 100.0, "magneticFluxDensity": b_sat},
                            {"temperature": 25.0, "magneticFluxDensity": b_sat * 1.25},
                        ],
                    },
                },
            },
            "coil": {
                "functionalDescription": [{"numberTurns": n_turns}],
            },
        },
    }
    return MagneticDesign(scoring=score, mas=mas, elapsed_s=0.0)


_BUCK_SPEC = {
    "inputVoltage": {"nominal": 48.0, "minimum": 36.0, "maximum": 60.0},
    "desiredInductance": 22e-6,
    "operatingPoints": [{
        "switchingFrequency": 200000.0,
        "outputVoltages": [12.0],
        "outputCurrents": [5.0],
        "ambientTemperature": 25.0,
    }],
}


def test_ipeak_worst_buck_matches_hand_calc() -> None:
    """For 48->12V@5A, 22 uH, 200 kHz: ripple_worst = Vout*(1-Dmin)/(0.8L*fsw)
    = 12*(1-0.2)/(0.8*22e-6*200000) = 2.727 A; ipeak = 5 + 2.727/2 = 6.364 A.
    """
    ipeak = _ipeak_worst_buck(_BUCK_SPEC)
    assert ipeak == pytest.approx(6.364, abs=1e-3)


@pytest.mark.parametrize("bad_spec", [
    {},  # no inputVoltage, no operatingPoints
    {"inputVoltage": {"nominal": 48.0}},  # missing minimum/maximum
    {**_BUCK_SPEC, "desiredInductance": 0},  # zero L
    {**_BUCK_SPEC, "operatingPoints": []},  # empty ops
])
def test_ipeak_worst_buck_returns_none_on_incomplete_spec(bad_spec: dict) -> None:
    assert _ipeak_worst_buck(bad_spec) is None


def test_ipeak_worst_buck_returns_none_if_vout_exceeds_vmin() -> None:
    """Buck cannot step up — refuse to compute rather than return nonsense."""
    bad = {**_BUCK_SPEC, "inputVoltage": {"minimum": 5.0, "maximum": 60.0}}
    assert _ipeak_worst_buck(bad) is None


def test_isat_from_mas_basic_arithmetic() -> None:
    """isat = B_sat * N * A_e / L = 0.44 * 6 * 3.45e-5 / 22e-6 ~= 4.14 A."""
    cand = _candidate(score=1.0, b_sat=0.44, n_turns=6, a_e=3.45e-5)
    isat = _isat_from_mas(cand.magnetic, L_henries=22e-6)
    assert isat == pytest.approx(4.14, abs=0.02)


def test_isat_from_mas_picks_minimum_bsat_across_temperatures() -> None:
    """The 100 C entry (lower B_sat) wins over the 25 C entry."""
    cand = _candidate(score=1.0, b_sat=0.44, n_turns=6, a_e=3.45e-5)
    # 0.44 is the 100 C entry; 0.55 is the 25 C entry. Result should be 0.44.
    isat = _isat_from_mas(cand.magnetic, L_henries=22e-6)
    expected_at_44 = 0.44 * 6 * 3.45e-5 / 22e-6
    assert isat == pytest.approx(expected_at_44, abs=1e-4)


def test_isat_from_mas_returns_none_for_malformed_mas() -> None:
    assert _isat_from_mas({}, L_henries=22e-6) is None
    assert _isat_from_mas({"magnetic": {}}, L_henries=22e-6) is None
    # Missing saturation curve
    half_baked = {
        "core": {
            "processedDescription": {"effectiveParameters": {"effectiveArea": 1e-5}},
        },
        "coil": {"functionalDescription": [{"numberTurns": 10}]},
    }
    assert _isat_from_mas(half_baked, L_henries=22e-6) is None


def test_select_picks_first_passing_candidate() -> None:
    """Two candidates: top-scorer FAILS isat (isat=4), second-best PASSES (isat=10).
    Threshold for buck spec is 1.2 * 6.364 = 7.64 A. Post-filter must skip
    candidate 0 and return candidate 1."""
    cand_fail = _candidate(score=3.0, b_sat=0.4, n_turns=6, a_e=3.67e-5)
    # isat = 0.4 * 6 * 3.67e-5 / 22e-6 = 4.0 A  -> FAIL
    cand_pass = _candidate(score=2.0, b_sat=0.4, n_turns=14, a_e=4.0e-5)
    # isat = 0.4 * 14 * 4e-5 / 22e-6 = 10.18 A  -> PASS
    chosen = _select_main_by_isat_margin(
        [cand_fail, cand_pass], get("buck"), _BUCK_SPEC, min_isat_ratio=1.2,
    )
    assert chosen is cand_pass


def test_select_falls_back_to_top_scorer_when_no_candidate_passes() -> None:
    """Exactly the buck-48-to-12-at-5A real-world situation: PyMKF's whole
    library tops out at ~5.5 A isat, threshold is 7.64 A. Post-filter MUST
    return candidate[0] (top scorer) so realism can FAIL it honestly."""
    cands = [
        _candidate(score=3.0, b_sat=0.44, n_turns=6, a_e=3.45e-5),  # ~4.14 A
        _candidate(score=2.5, b_sat=0.40, n_turns=8, a_e=3.0e-5),   # ~4.36 A
    ]
    chosen = _select_main_by_isat_margin(
        cands, get("buck"), _BUCK_SPEC, min_isat_ratio=1.2,
    )
    assert chosen is cands[0]


def test_select_disabled_when_min_ratio_zero() -> None:
    """min_isat_ratio=0 short-circuits to PyMKF's top scorer — escape hatch."""
    cands = [
        _candidate(score=3.0, b_sat=0.4, n_turns=6, a_e=3.5e-5),
        _candidate(score=2.0, b_sat=0.4, n_turns=14, a_e=4e-5),
    ]
    chosen = _select_main_by_isat_margin(
        cands, get("buck"), _BUCK_SPEC, min_isat_ratio=0.0,
    )
    assert chosen is cands[0]


def test_select_passes_through_when_no_topology_ipeak_fn() -> None:
    """For topologies without a registered _IPEAK_WORST entry, post-filter
    is a no-op (returns top scorer). Boost has no entry today."""
    cands = [
        _candidate(score=3.0, b_sat=0.4, n_turns=6, a_e=3.5e-5),
        _candidate(score=2.0, b_sat=0.4, n_turns=14, a_e=4e-5),
    ]
    chosen = _select_main_by_isat_margin(
        cands, get("boost"), {"inputVoltage": {"nominal": 12.0}}, min_isat_ratio=1.2,
    )
    assert chosen is cands[0]


def test_select_empty_pool_raises() -> None:
    with pytest.raises(BridgeError, match="empty candidate list"):
        _select_main_by_isat_margin([], get("buck"), _BUCK_SPEC, min_isat_ratio=1.2)


def test_select_skips_candidate_with_unreadable_mas() -> None:
    """A malformed MAS in the middle of the list should be silently skipped,
    not crash — pick the next readable one."""
    cand_bad = MagneticDesign(scoring=4.0, mas={"magnetic": {}}, elapsed_s=0.0)
    cand_good = _candidate(score=3.0, b_sat=0.4, n_turns=14, a_e=4e-5)
    chosen = _select_main_by_isat_margin(
        [cand_bad, cand_good], get("buck"), _BUCK_SPEC, min_isat_ratio=1.2,
    )
    # cand_bad has no readable isat -> skipped; cand_good has isat ~10.2 -> PASS
    assert chosen is cand_good
