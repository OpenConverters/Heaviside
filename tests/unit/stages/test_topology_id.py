"""Unit tests for the topology_id stage (deterministic engine paths)."""
from __future__ import annotations

from heaviside.stages.topology_id import (
    TopologyChoice,
    canonical_names,
    feasible,
    identify,
    resolve,
)


def _spec(*, vmin, vmax, vouts, iouts=None):
    iouts = iouts or [1.0] * len(vouts)
    return {
        "inputVoltage": {"minimum": vmin, "maximum": vmax, "nominal": (vmin + vmax) / 2},
        "operatingPoints": [{
            "outputVoltages": vouts, "outputCurrents": iouts,
            "switchingFrequency": 200_000.0, "ambientTemperature": 25.0,
        }],
    }


def test_canonical_names_includes_core_topologies():
    names = canonical_names()
    assert "buck" in names and "boost" in names and "flyback" in names


def test_resolve_normalizes_aliases():
    assert resolve("Buck") == "buck"
    assert resolve("BUCK CONVERTER") in canonical_names() or resolve("BUCK CONVERTER") == "buck"
    # unknown string passes through unchanged (screen will reject it later)
    assert resolve("not-a-real-topology") == "not-a-real-topology"


def test_feasible_step_down_admits_buck_rejects_boost():
    names = feasible(_spec(vmin=36, vmax=60, vouts=[12.0]))
    assert "buck" in names
    assert "boost" not in names


def test_feasible_step_up_admits_boost_rejects_buck():
    names = feasible(_spec(vmin=9, vmax=15, vouts=[24.0]))
    assert "boost" in names
    assert "buck" not in names


def test_identify_falls_back_to_static_without_llm(monkeypatch):
    # no LLM key -> the selector falls back to the deterministic screen;
    # the result must be a subset-consistent, physically feasible set.
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    spec = _spec(vmin=36, vmax=60, vouts=[12.0])
    choice = identify(spec)
    assert isinstance(choice, TopologyChoice)
    assert choice.static == feasible(spec)
    assert "buck" in choice.viable
    assert "boost" not in choice.viable
