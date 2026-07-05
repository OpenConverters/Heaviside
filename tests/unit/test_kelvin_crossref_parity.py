"""Parity: Kelvin's C++ cross-reference ranker agrees with Heaviside's Python
scoring engine (heaviside/pipeline/scoring.py). This is the "Kelvin selects as
good as you" guarantee — the deterministic verdicts must be identical, so
Kirchhoff (program-only, over Kelvin) and Heaviside (LLM over Kelvin) stand on
the same authority.

Skips if PyKelvin isn't built (Kelvin/build). Build with:
    cmake -S ../Kelvin -B ../Kelvin/build -G Ninja && ninja -C ../Kelvin/build PyKelvin
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_KELVIN_BUILD = Path(__file__).resolve().parents[2].parent / "Kelvin" / "build"
if _KELVIN_BUILD.is_dir():
    sys.path.insert(0, str(_KELVIN_BUILD))

PyKelvin = pytest.importorskip("PyKelvin", reason="Kelvin not built (Kelvin/build/PyKelvin*.so)")

from heaviside.pipeline.scoring import FAIL, PASS, WARN, score_primary_value  # noqa: E402


def _xref(cat, original, candidates, verified=True):
    return PyKelvin.cross_reference(cat, original, candidates, {"original_verified": verified})


@pytest.mark.parametrize(
    "cat,orig,sub,py_verdict",
    [
        ("magnetic", 1.5e-6, 330e-9, FAIL),   # the 330nH rejection
        ("magnetic", 1.5e-6, 1.5e-6, PASS),
        ("magnetic", 1.5e-6, 1.2e-6, WARN),   # 0.8x accept floor
        ("resistor", 47000.0, 10000.0, FAIL),
        ("resistor", 47000.0, 47000.0, PASS),
        ("capacitor", 1e-6, 2e-6, WARN),      # 2x in-window off-nominal
    ],
)
def test_primary_value_verdict_parity(cat, orig, sub, py_verdict):
    # Python scoring engine verdict
    has = False
    py = score_primary_value(cat, orig, sub)
    assert py is not None
    # Kelvin ranker: FAIL -> no_substitute; WARN -> partial; PASS -> recommended
    kv = _xref(cat, {"mpn": "O", "value_si": orig}, [{"mpn": "S", "value_si": sub}])
    status = kv["candidates"][0]["status"]
    expected = {FAIL: "no_substitute", WARN: "partial", PASS: "recommended"}[py.verdict]
    assert py.verdict == py_verdict
    assert status == expected, f"{cat} {orig}->{sub}: py={py.verdict} kelvin={status}"


def test_severe_current_rejected_like_python_gate():
    # Original 25.5A Isat, candidate 2.1A (<70%) -> Kelvin no_substitute
    orig = {"mpn": "O", "value_si": 1.5e-6, "saturation_current": 25.5}
    sub = {"mpn": "S", "value_si": 1.5e-6, "saturation_current": 2.1}
    r = _xref("magnetic", orig, [sub])
    assert r["candidates"][0]["status"] == "no_substitute"


def test_unverified_original_caps_at_partial():
    orig = {"mpn": "O", "value_si": 1.5e-6, "saturation_current": 3.25}
    sub = {"mpn": "S", "value_si": 1.5e-6, "saturation_current": 4.8}
    r = _xref("magnetic", orig, [sub], verified=False)
    assert r["candidates"][0]["status"] == "partial"
    assert r["candidates"][0].get("original_unverified") is True


def test_ranking_prefers_right_sized_over_oversized():
    orig = {"mpn": "O", "value_si": 1.5e-6, "saturation_current": 3.25}
    cands = [
        {"mpn": "oversize", "value_si": 1.5e-6, "saturation_current": 40.0},
        {"mpn": "rightsize", "value_si": 1.5e-6, "saturation_current": 3.6},
    ]
    r = _xref("magnetic", orig, cands)
    assert r["candidates"][0]["mpn"] == "rightsize"


def test_capacitor_max_temp_downgrade_partial():
    orig = {"mpn": "O", "value_si": 1e-7, "voltage": 16.0, "temp_max_C": 125.0}
    sub = {"mpn": "X5R", "value_si": 1e-7, "voltage": 25.0, "temp_max_C": 85.0}
    r = _xref("capacitor", orig, [sub])
    assert r["candidates"][0]["status"] == "partial"
