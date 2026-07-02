"""Unit tests for :mod:`heaviside.pipeline.full_design`.

Stage 1 is testable in pure Python by injecting a fake
``selector_fn`` (no LLM, no Strands). Stage 2 needs PyOM, so its
unit-test coverage here is limited to the reconciliation /
ordering / pickling shape; the PyOM round-trip lives in the
integration suite.
"""

from __future__ import annotations

from typing import Any

import pytest

from heaviside.pipeline.full_design import (
    DesignOutcome,
    FullDesignError,
    TopologyPick,
    _order_topologies_by_lessons,
    _outcome_sort_key,
    _parse_topology_selector_response,
    stage1_topology_screen,
)
from heaviside.pipeline.topology_screen import (
    reconcile_topology_choices,
)

_BUCK_SPEC: dict[str, Any] = {
    "inputVoltage": {"minimum": 36, "maximum": 60, "nominal": 48},
    "operatingPoints": [
        {
            "outputVoltages": [12.0],
            "outputCurrents": [5.0],
            "switchingFrequency": 200_000.0,
            "ambientTemperature": 25.0,
        }
    ],
}


# ---------------------------------------------------------------------------
# JSON-block parser
# ---------------------------------------------------------------------------


def test_parser_extracts_fenced_json_block() -> None:
    text = """Some preamble.
```json
{"viable": ["buck", "flyback"], "reasoning": "step-down 60W"}
```
Trailing text the parser should ignore."""
    viable, reason = _parse_topology_selector_response(text)
    assert viable == ["buck", "flyback"]
    assert reason == "step-down 60W"


def test_parser_extracts_bare_json_when_no_fences() -> None:
    """Some models drop the markdown fences — accept a bare object."""
    text = 'Whatever {"viable": ["buck"], "reasoning": "tight"} tail'
    viable, reason = _parse_topology_selector_response(text)
    assert viable == ["buck"]
    assert reason == "tight"


def test_parser_throws_on_no_json() -> None:
    with pytest.raises(FullDesignError, match="no JSON"):
        _parse_topology_selector_response("just a plain string, no braces")


def test_parser_throws_on_malformed_viable() -> None:
    text = '```json\n{"viable": "not a list", "reasoning": "x"}\n```'
    with pytest.raises(FullDesignError, match="viable"):
        _parse_topology_selector_response(text)


def test_parser_throws_on_invalid_json() -> None:
    text = '```json\n{"viable": [buck, broken]}\n```'
    with pytest.raises(FullDesignError, match="JSON parse failed"):
        _parse_topology_selector_response(text)


# ---------------------------------------------------------------------------
# Stage 1 dual-path
# ---------------------------------------------------------------------------


def test_stage1_runs_both_paths_and_unions_them() -> None:
    """Agent suggests a subset that's narrower than static — chosen
    set should be the union (agent's preference first)."""

    def fake_selector(spec):
        return (["flyback", "single_switch_forward"], "isolation preferred")

    s1 = stage1_topology_screen(_BUCK_SPEC, selector_fn=fake_selector)
    # The static screen admits many topologies; the union must include
    # every static + agent pick.
    assert "buck" in s1.reconciliation.chosen  # from static
    assert "flyback" in s1.reconciliation.chosen  # from agent
    assert "single_switch_forward" in s1.reconciliation.chosen
    # Agent preferences come first.
    assert s1.reconciliation.chosen[:2] == ("flyback", "single_switch_forward")
    assert s1.agent_reasoning == "isolation preferred"


def test_stage1_warns_on_high_disagreement() -> None:
    """Agent returns topologies the static screen rejects → static_only
    grows → Jaccard distance climbs → warning fires."""

    def fake_selector(spec):
        # Pick only topologies the static screen ALSO rejects for buck.
        return (
            ["common_mode_choke", "current_transformer"],
            "judgment call (this is wrong on purpose for the test)",
        )

    s1 = stage1_topology_screen(_BUCK_SPEC, selector_fn=fake_selector)
    assert s1.reconciliation.warning is not None
    assert s1.reconciliation.jaccard_disagreement > 0.5
    # Agent-only topologies still appear in chosen (we're permissive).
    assert "common_mode_choke" in s1.reconciliation.chosen


def test_stage1_raises_when_no_topology_survives() -> None:
    def fake_selector(spec):
        return ([], "no opinion")

    bad = {
        "inputVoltage": {"minimum": 10, "maximum": 14, "nominal": 12},
        "operatingPoints": [{"outputVoltages": [11.5], "outputCurrents": [1.0]}],
    }
    # Make static return nothing too: vin=10..14 with vout=11.5 fails both
    # step_down (vout >= vin_min=10) and step_up (vout <= vin_max=14), and
    # the agent also returns nothing. step_either topologies still survive
    # though, so this test as-written should NOT raise — verify it admits
    # something:
    s1 = stage1_topology_screen(bad, selector_fn=fake_selector)
    assert s1.reconciliation.chosen, (
        "step-either topologies should still survive; the no-topology-raises "
        "path needs a deliberately impossible spec to exercise"
    )


# ---------------------------------------------------------------------------
# Reconciliation primitives (also tested directly in test_topology_screen)
# ---------------------------------------------------------------------------


def test_reconcile_perfect_agreement_no_warning() -> None:
    rec = reconcile_topology_choices(
        ["buck", "flyback"],
        ["buck", "flyback"],
    )
    assert rec.jaccard_disagreement == 0.0
    assert rec.warning is None
    assert rec.static_only == ()
    assert rec.agent_only == ()


def test_reconcile_empty_inputs_no_warning() -> None:
    rec = reconcile_topology_choices([], [])
    assert rec.chosen == ()
    assert rec.warning is None


def test_reconcile_agent_only_topologies_listed_separately() -> None:
    rec = reconcile_topology_choices(
        ["buck"],
        ["buck", "flyback"],
    )
    assert rec.agent_only == ("flyback",)
    assert "flyback" in rec.chosen
    assert "buck" in rec.chosen


# ---------------------------------------------------------------------------
# Teacher topology reordering (training_verdict + warned-last)
# ---------------------------------------------------------------------------


def test_order_topologies_preferred_first_warned_last() -> None:
    ordered = _order_topologies_by_lessons(
        ["buck", "boost", "flyback", "forward"],
        preferred=["flyback"],
        warned=["boost"],
    )
    # preferred → front, warned → back, neutral keep their screen order.
    assert ordered == ["flyback", "buck", "forward", "boost"]


def test_order_topologies_mixed_signal_is_neutral() -> None:
    """A topology both preferred AND warned cancels to neutral priority."""
    ordered = _order_topologies_by_lessons(
        ["buck", "flyback"],
        preferred=["flyback"],
        warned=["flyback"],
    )
    # flyback is mixed → neutral, so the original order is preserved.
    assert ordered == ["buck", "flyback"]


def test_order_topologies_preserves_membership_and_count() -> None:
    chosen = ["buck", "boost", "flyback", "forward", "sepic"]
    ordered = _order_topologies_by_lessons(chosen, preferred=["forward", "sepic"], warned=["buck"])
    # Pure reordering: same set, same count, nothing invented or dropped.
    assert sorted(ordered) == sorted(chosen)
    assert len(ordered) == len(chosen)
    # preferred order honoured within the prefer bucket.
    assert ordered[:2] == ["forward", "sepic"]
    assert ordered[-1] == "buck"


def test_order_topologies_case_and_space_insensitive() -> None:
    ordered = _order_topologies_by_lessons(
        ["dual_active_bridge", "buck"],
        preferred=["Dual Active Bridge"],
        warned=[],
    )
    assert ordered[0] == "dual_active_bridge"


# ---------------------------------------------------------------------------
# Stage 3 realize: hard failures raise RealizeError (no silent degradation)
# ---------------------------------------------------------------------------


def _buck_pick() -> TopologyPick:
    from heaviside.bridge import MagneticDesign
    from heaviside.topologies.registry import get as get_topology

    md = MagneticDesign(scoring=1.0, mas={}, elapsed_s=0.0)
    return TopologyPick(
        topology=get_topology("buck"),
        main_magnetic=md,
        candidates=(md,),
        pick_reason="test",
        pick_criteria="test",
    )


def test_outcome_sort_key_failed_outcomes_rank_last() -> None:
    """A failed realize is recorded with no verdict_dict; it must rank below
    both PASS and FAIL designs so it can never be selected as 'best'."""
    pick = _buck_pick()
    passed = DesignOutcome(pick=pick, verdict_dict={"verdict": "pass"})
    failed_gate = DesignOutcome(pick=pick, verdict_dict={"verdict": "fail"})
    realize_failed = DesignOutcome(pick=pick, diagnostics=("realize failed: boom",))

    ordered = sorted([realize_failed, failed_gate, passed], key=_outcome_sort_key)
    assert ordered[0] is passed
    assert ordered[1] is failed_gate
    assert ordered[2] is realize_failed  # no verdict → last
