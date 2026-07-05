"""Python must reproduce the crossref GOLDEN corpus.

The golden (Kelvin/tests/golden/crossref_parity.json) is generated FROM these
exact Python functions (tests/evals/kelvin_parity/generate_golden.py). This test
re-runs them and asserts the output still matches — a regression guard on the
reference behavior, and the proof that the golden really is "what Python does".

The SAME corpus is the target the Kelvin C++ port must reproduce
(test_kelvin_golden_parity.py + Kelvin's Catch2 golden test). When an intentional
Python change moves a value, regenerate the golden and eyeball the diff.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from heaviside.pipeline.crossref_pipeline import (
    _footprint_area_mm2,
    _operating_point_magnetic_rescue,
)
from heaviside.pipeline.scoring import over_dimensioning_penalty, score_primary_value
from heaviside.pipeline.stress import required_inductance

pytestmark = pytest.mark.unit

_GOLDEN = Path(__file__).resolve().parents[2].parent / "Kelvin" / "tests" / "golden" / "crossref_parity.json"


def _golden() -> dict:
    if not _GOLDEN.exists():
        pytest.skip(f"golden corpus not generated ({_GOLDEN}); run generate_golden.py")
    return json.loads(_GOLDEN.read_text())


def _approx(a, b, rel=1e-6):
    if a == "inf" or b == float("inf"):
        return (a == "inf") and (b == float("inf"))
    return a == pytest.approx(b, rel=rel)


def test_score_primary_value_matches_golden() -> None:
    for c in _golden()["score_primary_value"]:
        r = score_primary_value(c["category"], c["original"], c["substitute"])
        exp = c["expect"]
        if exp is None:
            assert r is None, f"{c['category']} {c['original']}->{c['substitute']}: expected no spec"
            continue
        assert r is not None
        assert r.verdict == exp["verdict"], f"{c['category']} {c['original']}->{c['substitute']}"
        assert _approx(exp["penalty"], r.penalty), f"penalty {c}"


def test_over_dimensioning_penalty_matches_golden() -> None:
    for c in _golden()["over_dimensioning_penalty"]:
        got = over_dimensioning_penalty(c["required"], c["actual"])
        assert _approx(c["expect"], got), c


def test_required_inductance_matches_golden() -> None:
    for c in _golden()["required_inductance"]:
        got = required_inductance(c["topology"], c["spec"])
        exp = c["expect"]
        if exp is None:
            assert got is None, f"{c['topology']}: expected None"
        else:
            assert got is not None and got == pytest.approx(exp, rel=1e-6), c["topology"]


def test_footprint_area_matches_golden() -> None:
    for c in _golden()["footprint_area_mm2"]:
        got = _footprint_area_mm2(c["summary"])
        assert _approx(c["expect"], got), c["summary"]


def test_operating_point_rescue_matches_golden(monkeypatch) -> None:
    import heaviside.pipeline.crossref_pipeline as cp
    from types import SimpleNamespace

    for c in _golden()["operating_point_rescue"]:
        pool = c["pool"]
        monkeypatch.setattr(cp, "_target_manufacturer_envelopes",
                            lambda mfr, cat, cache, _p=pool: [{"_fixed": x} for x in _p])
        monkeypatch.setattr(cp, "_summarize_candidate", lambda env, cat: env["_fixed"])
        monkeypatch.setattr(cp, "_extract_value", lambda env, cat: env["_fixed"].get("value_si"))
        stress = SimpleNamespace(**c["stress"])
        r = _operating_point_magnetic_rescue("Würth Elektronik", stress, {})
        got = None if r is None else r["summary"]["mpn"]
        assert got == c["expect"]["mpn"], c["stress"]
