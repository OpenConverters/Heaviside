"""Unit tests for :mod:`heaviside.pipeline.topology_screen`.

The screen is the deterministic half of the della-Pollock topology
gate. Tests pin down the hard physics filtering so the LLM-driven
``topology-selector`` agent can be evaluated against it.
"""

from __future__ import annotations

import pytest

from heaviside.pipeline.topology_screen import (
    TopologyScreenError,
    feasible_topologies,
    feasible_topology_names,
)


def _spec(
    *, vmin: float, vmax: float, vouts: list[float], iouts: list[float] | None = None
) -> dict:
    if iouts is None:
        iouts = [1.0] * len(vouts)
    return {
        "inputVoltage": {"minimum": vmin, "maximum": vmax, "nominal": (vmin + vmax) / 2},
        "operatingPoints": [
            {
                "outputVoltages": vouts,
                "outputCurrents": iouts,
                "switchingFrequency": 200_000.0,
                "ambientTemperature": 25.0,
            }
        ],
    }


# ---------------------------------------------------------------------------
# Step direction
# ---------------------------------------------------------------------------


def test_step_down_spec_admits_buck_rejects_boost() -> None:
    """48V→12V is hard step-down; boost cannot deliver it."""
    names = feasible_topology_names(_spec(vmin=36, vmax=60, vouts=[12.0]))
    assert "buck" in names
    assert "boost" not in names


def test_step_up_spec_admits_boost_rejects_buck() -> None:
    """12V→24V is hard step-up; buck cannot deliver it."""
    names = feasible_topology_names(_spec(vmin=9, vmax=15, vouts=[24.0]))
    assert "boost" in names
    assert "buck" not in names


def test_step_either_admits_both_directional_topologies() -> None:
    """When Vout falls inside the Vin range (e.g. 10V output with 8–14V input),
    only step-either topologies survive — buck/boost get filtered."""
    names = feasible_topology_names(_spec(vmin=8, vmax=14, vouts=[10.0]))
    assert "buck" not in names  # vout=10 ≥ vin_min=8 — buck rejected
    assert "boost" not in names  # vout=10 ≤ vin_max=14 — boost rejected
    assert "flyback" in names  # step_either OK
    assert "cuk" in names


# ---------------------------------------------------------------------------
# Multi-output
# ---------------------------------------------------------------------------


def test_multi_output_excludes_all_non_isolated() -> None:
    """A 2-output spec can only be served by isolated families with
    declared secondaries — buck/boost/cuk/sepic/zeta are single-output."""
    names = feasible_topology_names(
        _spec(vmin=36, vmax=60, vouts=[12.0, 5.0], iouts=[2.0, 0.5]),
    )
    for non_iso in ["buck", "boost", "cuk", "sepic", "zeta", "four_switch_buck_boost"]:
        assert non_iso not in names, f"{non_iso} survived multi-output filter"
    # Isolated multi-output topologies admitted.
    assert "flyback" in names
    assert "single_switch_forward" in names


def test_single_output_admits_non_isolated() -> None:
    names = feasible_topology_names(_spec(vmin=36, vmax=60, vouts=[12.0]))
    assert "buck" in names
    assert "cuk" in names
    assert "sepic" in names


# ---------------------------------------------------------------------------
# AC input
# ---------------------------------------------------------------------------


def test_dc_spec_excludes_ac_input_topologies() -> None:
    """A normal DC inputVoltage spec must not admit PFC / Vienna."""
    names = feasible_topology_names(_spec(vmin=36, vmax=60, vouts=[12.0]))
    assert "power_factor_correction" not in names
    assert "vienna" not in names


def test_ac_spec_admits_only_ac_topologies() -> None:
    spec = {
        "lineToLineVoltage": {"minimum": 380, "maximum": 440, "nominal": 400},
        "outputDcVoltage": 800.0,
        "operatingPoints": [
            {
                "outputVoltages": [800.0],
                "outputCurrents": [5.0],
                "switchingFrequency": 100_000.0,
                "ambientTemperature": 25.0,
            }
        ],
    }
    names = feasible_topology_names(spec)
    # vienna is in registry; the screen admits it because lineToLineVoltage
    # is present (is_ac_input=True). DC-only topologies excluded.
    assert "vienna" in names
    assert "buck" not in names
    assert "flyback" not in names


# ---------------------------------------------------------------------------
# Specialised topologies always require explicit opt-in
# ---------------------------------------------------------------------------


def test_magnetic_only_topologies_never_admitted() -> None:
    """Common-mode chokes / current transformers are magnetic-only —
    the screen should never recommend them for a converter spec."""
    names = feasible_topology_names(_spec(vmin=36, vmax=60, vouts=[12.0]))
    assert "common_mode_choke" not in names
    assert "differential_mode_choke" not in names
    assert "current_transformer" not in names


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_missing_input_voltage_throws() -> None:
    with pytest.raises(TopologyScreenError, match="inputVoltage"):
        feasible_topology_names({"operatingPoints": [{"outputVoltages": [12.0]}]})


def test_missing_operating_points_throws() -> None:
    with pytest.raises(TopologyScreenError, match="operatingPoints"):
        feasible_topology_names({"inputVoltage": {"minimum": 36, "maximum": 60}})


def test_nominal_only_input_voltage_inferred_to_plus_minus_25_percent() -> None:
    """Nominal-only spec is admitted with ±25% bounds (matches corpus_run
    enrichment). 48V nominal → vin=[36, 60] → buck OK for 12V out."""
    spec = {
        "inputVoltage": {"nominal": 48.0},
        "operatingPoints": [{"outputVoltages": [12.0], "outputCurrents": [5.0]}],
    }
    names = feasible_topology_names(spec)
    assert "buck" in names


def test_returns_topology_entries_with_canonical_names() -> None:
    """feasible_topologies returns full TopologyEntry objects so callers
    can read entry.family / pyom_names without a second registry lookup."""
    entries = feasible_topologies(_spec(vmin=36, vmax=60, vouts=[12.0]))
    assert all(e.name == n for e, n in zip(entries, [e.name for e in entries], strict=False))
    buck = next(e for e in entries if e.name == "buck")
    assert buck.family == "non_isolated"
