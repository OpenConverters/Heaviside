"""The Kirchhoff-native magnetics design must fail loud when it cannot compute
a magnetic's saturation current.

The gate's saturation check is safety-critical (it caught the ABT #12
oversized-inductance bug). Swallowing a PyOM failure to ``isat = None`` left
the field unstamped, so the realism gate marked saturation UNAVAILABLE and
never FAILed — a silent disable of exactly the check that matters.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from heaviside.pipeline.full_design import RealizeError, _design_ktas_magnetics


class _FakeBridgeIsatRaises:
    @staticmethod
    def design_magnetic_from_mas_inputs(seed, max_results=1):
        return [SimpleNamespace(mas={"designed": True}, magnetic={"core": "stub"})]

    @staticmethod
    def _harvest_authoritative_inductance(mas):
        return 100e-6

    @staticmethod
    def _isat_from_mas(magnetic, L):
        raise RuntimeError("PyOM rejected the saturation model")


def _k_tas_with_one_magnetic() -> dict:
    return {
        "topology": {
            "stages": [
                {
                    "circuit": {
                        "components": [
                            {"name": "L1", "data": {"magnetic": {}, "inputs": {}}},
                        ]
                    }
                }
            ]
        }
    }


def test_isat_computation_failure_raises_not_swallowed() -> None:
    with pytest.raises(RealizeError, match="saturation current") as ei:
        _design_ktas_magnetics(
            _k_tas_with_one_magnetic(),
            bridge_mod=_FakeBridgeIsatRaises(),
            pyom_vendor=object(),  # not reached — we raise before autocomplete
            stamp_fn=lambda *a, **k: None,
        )
    # The original PyOM error is chained, not lost.
    assert isinstance(ei.value.__cause__, RuntimeError)
    assert "PyOM rejected" in str(ei.value.__cause__)
