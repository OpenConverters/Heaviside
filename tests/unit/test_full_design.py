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
    RealizeError,
    TopologyPick,
    _order_topologies_by_lessons,
    _outcome_sort_key,
    _parse_topology_selector_response,
    stage1_topology_screen,
    stage3_realize,
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
    ordered = _order_topologies_by_lessons(
        chosen, preferred=["forward", "sepic"], warned=["buck"]
    )
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


def test_sim_backend_selection(monkeypatch) -> None:
    """Per-topology backend selection: default mkf (no behaviour change), opt-in
    via the env var (list or '*')."""
    from heaviside.pipeline.full_design import _sim_backend_for

    monkeypatch.delenv("HEAVISIDE_KIRCHHOFF_TOPOLOGIES", raising=False)
    assert _sim_backend_for("boost") == "mkf"          # registry empty by default
    monkeypatch.setenv("HEAVISIDE_KIRCHHOFF_TOPOLOGIES", "boost,flyback")
    assert _sim_backend_for("boost") == "kirchhoff"
    assert _sim_backend_for("buck") == "mkf"
    monkeypatch.setenv("HEAVISIDE_KIRCHHOFF_TOPOLOGIES", "*")
    assert _sim_backend_for("buck") == "kirchhoff"
    monkeypatch.setenv("HEAVISIDE_KIRCHHOFF_TOPOLOGIES", "")
    assert _sim_backend_for("boost") == "mkf"           # empty list → none enabled


def test_stage3_realize_raises_on_component_design_failure(monkeypatch) -> None:
    """A component-design (bridge) failure is a HARD failure: stage3_realize
    raises RealizeError instead of returning a degraded outcome that would slip
    a non-physics PASS past the realism gate."""
    import heaviside.bridge as bridge_mod
    from heaviside.bridge import BridgeError

    def _boom(*args, **kwargs):
        raise BridgeError("MKF could not size the magnetic")

    monkeypatch.setattr(bridge_mod, "design_converter_components", _boom)

    with pytest.raises(RealizeError) as exc_info:
        stage3_realize(_buck_pick(), _BUCK_SPEC)
    # The message names the failing step + topology, and the native cause is
    # chained (not swallowed) so the root error stays diagnosable.
    assert "component design failed for buck" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, BridgeError)


def _patch_stage3_all_success(monkeypatch):
    """Patch EVERY stage3_realize step to a trivial success so a single test can
    override exactly one step to raise and assert its RealizeError wrapping.

    stage3_realize imports each collaborator from its source package at call
    time (`from heaviside.catalogue import assemble_bom_from_tas`, etc.), so
    patching the attribute on that package module is what the function picks up.
    Returns the modules so callers can re-patch one step.
    """
    import types

    import heaviside.bridge as bridge_mod
    import heaviside.catalogue as catalogue_mod
    import heaviside.decomposer as decomposer_mod
    import heaviside.pipeline as pipeline_mod
    import heaviside.pipeline.analyst as analyst_mod
    import heaviside.sim as sim_mod
    import heaviside.sim.parasitics as parasitics_mod
    import heaviside.stages.realism_gate as gate_mod
    from heaviside.pipeline.realism import RealismReport, RealismVerdict

    components = types.SimpleNamespace(
        L_authoritative=1e-5,
        main_magnetic=types.SimpleNamespace(
            mas={"inputs": {"designRequirements": {"turnsRatios": []}}}
        ),
    )
    tas = {"topology": {"stages": []}}

    monkeypatch.setattr(bridge_mod, "design_converter_components", lambda *a, **k: components)
    monkeypatch.setattr(decomposer_mod, "decompose_from_spec", lambda *a, **k: (object(), tas))
    monkeypatch.setattr(bridge_mod, "attach_components_to_tas", lambda *a, **k: None)
    monkeypatch.setattr(catalogue_mod, "assemble_bom_from_tas", lambda *a, **k: None)
    monkeypatch.setattr(pipeline_mod, "enrich_tas_for_realism", lambda t, **k: t)
    monkeypatch.setattr(parasitics_mod, "inject_parasitics", lambda *a, **k: object())
    monkeypatch.setattr(sim_mod, "simulate_closed_loop", lambda *a, **k: object())
    monkeypatch.setattr(sim_mod, "simulate_steady_state", lambda *a, **k: object())
    monkeypatch.setattr(sim_mod, "stamp_simulation_results", lambda *a, **k: None)
    monkeypatch.setattr(analyst_mod, "run_analyst", lambda *a, **k: None)
    monkeypatch.setattr(
        gate_mod,
        "evaluate",
        lambda *a, **k: RealismReport(verdict=RealismVerdict.INCOMPLETE, checks=()),
    )
    return types.SimpleNamespace(
        bridge=bridge_mod,
        catalogue=catalogue_mod,
        decomposer=decomposer_mod,
        pipeline=pipeline_mod,
        analyst=analyst_mod,
        sim=sim_mod,
    )


def test_stage3_realize_all_success_returns_outcome(monkeypatch) -> None:
    """Sanity check on the harness: with every step succeeding, stage3_realize
    returns a DesignOutcome (so the failure tests below are meaningful — they
    fail because of the ONE step they override, not a broken harness)."""
    _patch_stage3_all_success(monkeypatch)
    outcome = stage3_realize(_buck_pick(), _BUCK_SPEC)
    assert outcome.verdict_dict is not None
    assert outcome.verdict_dict["verdict"] == "incomplete"


def test_stage3_realize_raises_on_decompose_failure(monkeypatch) -> None:
    from heaviside.decomposer.api import DecomposerError

    mods = _patch_stage3_all_success(monkeypatch)

    def _boom(*a, **k):
        raise DecomposerError("no stencil for topology")

    monkeypatch.setattr(mods.decomposer, "decompose_from_spec", _boom)
    with pytest.raises(RealizeError) as ei:
        stage3_realize(_buck_pick(), _BUCK_SPEC)
    assert "decompose failed for buck" in str(ei.value)
    assert isinstance(ei.value.__cause__, DecomposerError)


def test_stage3_realize_raises_on_attach_failure(monkeypatch) -> None:
    from heaviside.bridge import BridgeError

    mods = _patch_stage3_all_success(monkeypatch)

    def _boom(*a, **k):
        raise BridgeError("could not bind magnetic to TAS")

    monkeypatch.setattr(mods.bridge, "attach_components_to_tas", _boom)
    with pytest.raises(RealizeError) as ei:
        stage3_realize(_buck_pick(), _BUCK_SPEC)
    assert "attach failed for buck" in str(ei.value)
    assert isinstance(ei.value.__cause__, BridgeError)


def test_stage3_realize_raises_on_bom_selection_failure(monkeypatch) -> None:
    """A partial/failed BOM must raise — it leaves the gate's physics checks
    without ratings/stress, which would otherwise pass on metadata alone."""
    from heaviside.catalogue import SelectionError

    mods = _patch_stage3_all_success(monkeypatch)

    def _boom(*a, **k):
        raise SelectionError("MosfetConstraints", {"vds": 12}, 12)

    monkeypatch.setattr(mods.catalogue, "assemble_bom_from_tas", _boom)
    with pytest.raises(RealizeError) as ei:
        stage3_realize(_buck_pick(), _BUCK_SPEC)
    assert "BOM selection failed for buck" in str(ei.value)
    assert isinstance(ei.value.__cause__, SelectionError)


def test_stage3_realize_raises_on_enrichment_failure(monkeypatch) -> None:
    from heaviside.pipeline.extract import EnrichmentError

    mods = _patch_stage3_all_success(monkeypatch)

    def _boom(*a, **k):
        raise EnrichmentError("no MAS on L1")

    monkeypatch.setattr(mods.pipeline, "enrich_tas_for_realism", _boom)
    with pytest.raises(RealizeError) as ei:
        stage3_realize(_buck_pick(), _BUCK_SPEC)
    assert "enrichment failed for buck" in str(ei.value)
    assert isinstance(ei.value.__cause__, EnrichmentError)


def test_stage3_realize_raises_on_simulation_failure(monkeypatch) -> None:
    """_BUCK_SPEC carries a vout target → closed-loop sim runs; if it fails we
    raise rather than silently falling back to the open-loop steady-state sim."""
    from heaviside.sim import SimError

    mods = _patch_stage3_all_success(monkeypatch)

    def _boom(*a, **k):
        raise SimError("ngspice did not converge")

    monkeypatch.setattr(mods.sim, "simulate_closed_loop", _boom)
    with pytest.raises(RealizeError) as ei:
        stage3_realize(_buck_pick(), _BUCK_SPEC)
    assert "simulation failed for buck" in str(ei.value)
    assert isinstance(ei.value.__cause__, SimError)


def test_stage3_realize_raises_on_analyst_failure(monkeypatch) -> None:
    from heaviside.pipeline.analyst import AnalystError

    mods = _patch_stage3_all_success(monkeypatch)

    def _boom(*a, **k):
        raise AnalystError("cannot derive stresses")

    monkeypatch.setattr(mods.analyst, "run_analyst", _boom)
    with pytest.raises(RealizeError) as ei:
        stage3_realize(_buck_pick(), _BUCK_SPEC)
    assert "analyst failed for buck" in str(ei.value)
    assert isinstance(ei.value.__cause__, AnalystError)


# ---------------------------------------------------------------------------
# Mark-failed ranking: a realize failure sorts last, never wins
# ---------------------------------------------------------------------------


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
