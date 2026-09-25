"""Kelvin as the crossref ranker: the glue that feeds it and reads it back."""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from heaviside.pipeline import kelvin_rank


def test_magnetic_requirements_carry_the_fae_margins() -> None:
    from heaviside.pipeline.crossref_pipeline import _IR_MARGIN, _ISAT_MARGIN

    req = kelvin_rank.stress_requirements("magnetic", NS(v_peak=None, i_peak=3.45, i_rms=3.0,
                                                         i_avg=None))
    assert req["saturation_current"] == pytest.approx(3.45 * _ISAT_MARGIN)
    assert req["rated_current"] == pytest.approx(3.0 * _IR_MARGIN)


def test_no_stress_no_requirements() -> None:
    assert kelvin_rank.stress_requirements("magnetic", None) == {}
    assert kelvin_rank.stress_requirements("resistor", NS(v_peak=5, i_peak=1, i_rms=1,
                                                          i_avg=1)) == {}


def test_unknown_identity_original_gets_no_candidates() -> None:
    # A connector of which nothing is known cannot be matched to anything.
    assert kelvin_rank.rank({"ref_des": "J1"}, "connector", [{"x": 1}]) == ([], {})


def test_summaries_carry_kelvins_rank_and_verdict() -> None:
    from heaviside.pipeline.crossref_pipeline import _candidate_summaries_for_llm

    env = {"capacitor": {"manufacturerInfo": {"reference": "A", "datasheetInfo": {
        "electrical": {"capacitance": 1e-6}, "part": {"case": "0402"}}}}}
    env2 = {"capacitor": {"manufacturerInfo": {"reference": "B", "datasheetInfo": {
        "electrical": {"capacitance": 1e-6}, "part": {"case": "0402"}}}}}
    s = _candidate_summaries_for_llm([env, env2], "capacitor", None, limit=5, kelvin_verdicts={
        "A": {"status": "recommended", "grade": "drop_in"}, "B": {"status": "partial"}})
    assert s[0]["kelvin"] == {"rank": 1, "status": "recommended", "grade": "drop_in"}
    assert s[1]["kelvin"]["rank"] == 2
