"""Suitability pick over the frequency-resolved front (master-plan step B5).

pareto_summary_from_sweep annotates each candidate with the loss split + fsw;
pick_best_from_sweep is the deterministic argmin; pick_magnetic_from_sweep_llm
is the qualitative LLM layer that can move to a nearby cell with justification
but never invents an index, and falls back to the deterministic pick with no
API key. Tested with a fake sweep result + fake LLM (no MKF).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from heaviside.agents import magnetic_picker as mp

# --- minimal fakes mirroring frequency_sweep.SweepCandidate/Result ----------


@dataclass
class _FakeCand:
    total_loss_w: float
    magnetic_loss_w: float
    switching_loss_w: float
    isat_a: float
    ipeak_worst_a: float
    inductance_h: float
    scoring: float = 1.0
    mas: dict = field(
        default_factory=lambda: {
            "magnetic": {
                "core": {
                    "functionalDescription": {
                        "shape": {"name": "PQ 20/16"},
                        "material": {"name": "3C95"},
                        "gapping": [{"length": 1e-4}],
                    },
                    "processedDescription": {
                        "effectiveParameters": {"effectiveArea": 6e-5, "effectiveVolume": 2e-6}
                    },
                },
                "coil": {"functionalDescription": [{"numberTurns": 12}]},
            }
        }
    )


@dataclass
class _FakeResult:
    fsw_star_hz: float
    front: list[Any]


def _result():
    # front ascending by total loss (as the sweep guarantees)
    return _FakeResult(
        fsw_star_hz=250_000.0,
        front=[
            _FakeCand(1.10, 0.80, 0.30, isat_a=5.0, ipeak_worst_a=3.2, inductance_h=22e-6),
            _FakeCand(1.35, 1.00, 0.35, isat_a=6.0, ipeak_worst_a=3.2, inductance_h=20e-6),
            _FakeCand(1.60, 1.20, 0.40, isat_a=8.0, ipeak_worst_a=3.2, inductance_h=18e-6),
        ],
    )


# --- summary -----------------------------------------------------------------


def test_summary_carries_loss_and_fsw_columns():
    rows = mp.pareto_summary_from_sweep(_result())
    assert len(rows) == 3
    r0 = rows[0]
    # base MAS metrics preserved
    assert r0["shape"] == "PQ 20/16" and r0["material"] == "3C95"
    assert r0["n_turns_primary"] == 12
    # B5 annotations present
    assert r0["total_loss_w"] == pytest.approx(1.10)
    assert r0["magnetic_loss_w"] == pytest.approx(0.80)
    assert r0["switching_loss_w"] == pytest.approx(0.30)
    assert r0["fsw_hz"] == pytest.approx(250_000.0)
    assert r0["isat_a"] == pytest.approx(5.0)
    assert r0["ipeak_worst_a"] == pytest.approx(3.2)
    assert r0["inductance_uh"] == pytest.approx(22.0)
    # total = magnetic + switching (consistency)
    assert r0["total_loss_w"] == pytest.approx(r0["magnetic_loss_w"] + r0["switching_loss_w"])


def test_summary_indices_are_sequential():
    rows = mp.pareto_summary_from_sweep(_result())
    assert [r["index"] for r in rows] == [0, 1, 2]


# --- deterministic pick ------------------------------------------------------


def test_deterministic_pick_is_argmin():
    assert mp.pick_best_from_sweep(_result()) == 0  # ascending front ⇒ index 0


def test_deterministic_pick_empty_front_raises():
    with pytest.raises(mp.MagneticPickerError):
        mp.pick_best_from_sweep(_FakeResult(250_000.0, []))


# --- LLM suitability layer ---------------------------------------------------


def test_llm_pick_falls_back_to_deterministic_without_key(monkeypatch):
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    out = mp.pick_magnetic_from_sweep_llm(_result(), {"inputVoltage": {}})
    assert out == {
        "index": 0,
        "source": "deterministic",
        "reason": "no API key — deterministic total-loss argmin",
    }


def test_llm_pick_uses_valid_index(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "fake")
    monkeypatch.setattr(
        mp,
        "call_agent_json" if hasattr(mp, "call_agent_json") else "pareto_summary_from_sweep",
        lambda *a, **k: None,
        raising=False,
    )
    # patch the call inside the function's import site
    import heaviside.agents.llm_call as llm

    monkeypatch.setattr(
        llm,
        "call_agent_json",
        lambda name, msg, **kw: {"index": 2, "reason": "in stock + better isat headroom"},
    )
    out = mp.pick_magnetic_from_sweep_llm(_result(), {"inputVoltage": {}})
    assert out["index"] == 2
    assert out["source"] == "llm"
    assert "stock" in out["reason"]


def test_llm_pick_rejects_invented_index(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "fake")
    import heaviside.agents.llm_call as llm

    monkeypatch.setattr(
        llm, "call_agent_json", lambda name, msg, **kw: {"index": 9, "reason": "made up"}
    )
    with pytest.raises(mp.MagneticPickerError, match="outside the front"):
        mp.pick_magnetic_from_sweep_llm(_result(), {"inputVoltage": {}})


def test_llm_pick_malformed_response_raises(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "fake")
    import heaviside.agents.llm_call as llm

    monkeypatch.setattr(
        llm, "call_agent_json", lambda name, msg, **kw: {"reason": "no index field"}
    )
    with pytest.raises(mp.MagneticPickerError):
        mp.pick_magnetic_from_sweep_llm(_result(), {"inputVoltage": {}})
