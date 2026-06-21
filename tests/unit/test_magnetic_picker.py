"""Unit tests for :mod:`heaviside.agents.magnetic_picker`.

The deterministic picker is the offline / fixture path for the
upcoming LLM-driven ``magnetic-pareto-picker`` agent. Tests use
hand-built ``MagneticDesign`` fixtures so no PyOM is needed —
the PyOM-touching end-to-end is exercised by the integration suite.
"""

from __future__ import annotations

import pytest

from heaviside.agents.magnetic_picker import (
    PARETO_CRITERIA,
    MagneticPickerError,
    pareto_summary,
    pick_best_pareto,
)
from heaviside.bridge import MagneticDesign


def _design(
    *,
    scoring: float,
    shape: str,
    material: str,
    n_turns: int,
    a_e: float,
    v_e: float,
    has_gap: bool = True,
) -> MagneticDesign:
    """Hand-build a MagneticDesign whose MAS has only the fields the
    Pareto picker / summary reads. Minimal — tests should not be
    coupled to PyOM's MAS bloat."""
    mas = {
        "magnetic": {
            "core": {
                "functionalDescription": {
                    "shape": {"name": shape},
                    "material": {"name": material},
                    "gapping": [{"length": 1e-3}] if has_gap else [],
                },
                "processedDescription": {
                    "effectiveParameters": {
                        "effectiveArea": a_e,
                        "effectiveVolume": v_e,
                    },
                },
            },
            "coil": {
                "functionalDescription": [{"numberTurns": n_turns}],
            },
        },
    }
    return MagneticDesign(scoring=scoring, mas=mas, elapsed_s=0.0)


# ---------------------------------------------------------------------------
# pareto_summary
# ---------------------------------------------------------------------------


def test_pareto_summary_extracts_shape_material_turns_volume() -> None:
    designs = [
        _design(scoring=7.5, shape="EP 17", material="3F36", n_turns=15, a_e=3.4e-5, v_e=9.8e-7),
        _design(
            scoring=8.5,
            shape="T 13/7",
            material="Edge 125",
            n_turns=16,
            a_e=2.0e-5,
            v_e=5.5e-7,
            has_gap=False,
        ),
    ]
    rows = pareto_summary(designs)
    assert len(rows) == 2
    assert rows[0]["index"] == 0
    assert rows[0]["shape"] == "EP 17"
    assert rows[0]["material"] == "3F36"
    assert rows[0]["n_turns_primary"] == 15
    assert rows[0]["has_gap"] is True
    assert rows[0]["effective_volume_m3"] == pytest.approx(9.8e-7)
    assert rows[1]["shape"] == "T 13/7"
    assert rows[1]["has_gap"] is False


def test_pareto_summary_tolerates_missing_fields() -> None:
    bare = MagneticDesign(scoring=1.0, mas={"magnetic": {}}, elapsed_s=0.0)
    rows = pareto_summary([bare])
    assert rows[0]["shape"] is None
    assert rows[0]["material"] is None
    assert rows[0]["n_turns_primary"] is None
    assert rows[0]["effective_area_m2"] is None


# ---------------------------------------------------------------------------
# pick_best_pareto
# ---------------------------------------------------------------------------


def test_pick_lowest_losses_returns_index_of_lowest_scoring() -> None:
    designs = [
        _design(scoring=9.0, shape="A", material="X", n_turns=10, a_e=1e-5, v_e=1e-7),
        _design(scoring=7.0, shape="B", material="Y", n_turns=12, a_e=2e-5, v_e=2e-7),
        _design(scoring=8.0, shape="C", material="Z", n_turns=14, a_e=3e-5, v_e=3e-7),
    ]
    assert pick_best_pareto(designs, criteria="lowest_losses") == 1


def test_pick_smallest_volume_returns_index_of_smallest_v_e() -> None:
    designs = [
        _design(scoring=1.0, shape="big", material="X", n_turns=10, a_e=1e-5, v_e=9e-7),
        _design(scoring=2.0, shape="small", material="Y", n_turns=10, a_e=1e-5, v_e=1e-7),
        _design(scoring=3.0, shape="medium", material="Z", n_turns=10, a_e=1e-5, v_e=5e-7),
    ]
    assert pick_best_pareto(designs, criteria="smallest_volume") == 1


def test_pick_highest_isat_headroom_returns_index_of_max_isat(monkeypatch) -> None:
    designs = [
        _design(scoring=1.0, shape="A", material="X", n_turns=10, a_e=1e-5, v_e=1e-7),
        _design(scoring=2.0, shape="B", material="Y", n_turns=20, a_e=2e-5, v_e=1e-7),  # ← winner
        _design(scoring=3.0, shape="C", material="Z", n_turns=15, a_e=1.5e-5, v_e=1e-7),
    ]
    # Stub out PyOM calls so the unit test only verifies the "pick highest" logic,
    # not the PyOM integration (covered by the integration suite).
    _isat_values = {id(d.magnetic): v for d, v in zip(designs, [5.0, 12.0, 8.0])}
    monkeypatch.setattr("heaviside.bridge._harvest_authoritative_inductance", lambda mas: 10e-6)
    monkeypatch.setattr("heaviside.bridge._isat_from_mas", lambda mag, L: _isat_values[id(mag)])
    assert pick_best_pareto(designs, criteria="highest_isat_headroom") == 1


def test_pick_empty_designs_raises() -> None:
    with pytest.raises(MagneticPickerError, match="empty"):
        pick_best_pareto([], criteria="lowest_losses")


def test_pick_unknown_criteria_raises() -> None:
    designs = [_design(scoring=1.0, shape="A", material="X", n_turns=10, a_e=1e-5, v_e=1e-7)]
    with pytest.raises(MagneticPickerError, match="unknown criteria"):
        pick_best_pareto(designs, criteria="cheapest")


def test_pareto_criteria_constant_lists_all_supported_options() -> None:
    """If you add a new criteria branch, add it to PARETO_CRITERIA too —
    the picker validates against this tuple before dispatching."""
    assert PARETO_CRITERIA == (
        "lowest_losses",
        "smallest_volume",
        "highest_isat_headroom",
    )
