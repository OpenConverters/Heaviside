"""spice_sim — build and run a SPICE simulation of a converter.

Engine (deterministic, this module): given a SPICE deck (or a converter
spec we decompose into one via the MKF/PyOM path), run the simulation and
return the steady-state operating point. A PWM deck with a target output
is run closed-loop (the runner searches duty until vout converges); a
non-PWM deck is run open at steady state. Convergence failures propagate
as ``SimError`` — never a silent fallback to a plausible number.

Optional LLM layer (``calibrate``): the deck exposes a few *bounded*
knobs (passive values, switching frequency) an LLM may nudge to bring a
measured metric (e.g. efficiency) closer to a target. Crucially the LLM
only *proposes* knob settings — every proposal is applied and re-simulated,
and a change is kept ONLY if the physics (the sim) shows a measured
improvement. The LLM can never assert a result, fabricate a number, or
fit-to-desired; the simulator is the sole judge. Falls back to the pure
engine result when no LLM key is configured or no metric target is given.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

# bounded knob kinds the calibrate layer may touch, mapped to the existing
# deck rewriters in cre_testbench (one place, already proven on real decks).
_ALLOWED_KNOBS = ("component_value", "fsw")


@dataclass
class SpiceResult:
    """One simulation outcome, PEAS-spec-agnostic (pure electrical result)."""

    result: dict[str, float]  # SimResult.as_dict(): vin/iin/vout/iout/pin/pout/losses/efficiency
    closed_loop: bool
    vout_target: float | None
    converged: bool
    deck: str
    knobs: dict[str, Any] = field(default_factory=dict)  # applied calibration knobs


def simulate(
    deck: str,
    *,
    vout_target: float | None = None,
    tolerance: float = 0.02,
    max_iterations: int = 12,
    timeout_s: float = 60.0,
) -> SpiceResult:
    """Deterministic engine: run ``deck`` and return its operating point.

    With a ``vout_target`` and a PWM source the deck is run closed-loop
    (duty search); otherwise it's a steady-state run. A closed-loop deck
    that fails to converge raises ``SimError`` from the runner — we do not
    swallow it (CLAUDE.md: surface problems, no silent fallback)."""
    from heaviside.sim.runner import (
        _read_pwm_pulse,
        simulate_closed_loop,
        simulate_steady_state,
    )

    has_pwm = _read_pwm_pulse(deck) is not None
    if vout_target is not None and has_pwm:
        r = simulate_closed_loop(
            deck,
            vout_target=vout_target,
            tolerance=tolerance,
            max_iterations=max_iterations,
            timeout_s=timeout_s,
        )
        # simulate_closed_loop raises unless it converged, so this is True here.
        return SpiceResult(
            result=r.as_dict(), closed_loop=True, vout_target=vout_target,
            converged=True, deck=deck,
        )

    r = simulate_steady_state(deck, timeout_s=timeout_s)
    converged = (
        vout_target is None
        or (vout_target != 0 and abs(r.vout - vout_target) / abs(vout_target) <= tolerance)
    )
    return SpiceResult(
        result=r.as_dict(), closed_loop=False, vout_target=vout_target,
        converged=converged, deck=deck,
    )


def simulate_from_spec(
    topology: str,
    converter_json: Any,
    turns_ratios: Any,
    magnetizing_inductance: float,
    *,
    vout_target: float | None = None,
    **sim_kwargs: Any,
) -> SpiceResult:
    """Deterministic engine: decompose a converter spec into a deck via the
    MKF/PyOM path, then ``simulate`` it. All magnetics math stays in MKF —
    this only orchestrates (CLAUDE.md: no downstream magnetics math)."""
    from heaviside.decomposer import decompose_from_spec

    deck, _tas = decompose_from_spec(
        topology, converter_json, turns_ratios, magnetizing_inductance
    )
    return simulate(deck, vout_target=vout_target, **sim_kwargs)


def _apply_knob(deck: str, knob: dict[str, Any]) -> str:
    """Apply one bounded knob to the deck text using the proven rewriters."""
    from heaviside.pipeline.cre_testbench import (
        _rewrite_component_value,
        _rewrite_fsw,
    )
    from heaviside.pipeline.value_parse import parse_si_value

    kind = knob.get("kind")
    if kind == "component_value":
        # the rewriter writes a raw SI float into the deck, so parse e.g.
        # "100uF" / "47uH" / "10" to its base-unit value first.
        raw = knob["value"]
        value_si = float(raw) if isinstance(raw, (int, float)) else parse_si_value(str(raw))
        if value_si is None:
            raise ValueError(f"spice_sim.calibrate: cannot parse knob value {raw!r}")
        return _rewrite_component_value(deck, knob["refdes"], value_si)
    if kind == "fsw":
        return _rewrite_fsw(deck, float(knob["value"]))
    raise ValueError(f"spice_sim.calibrate: unsupported knob kind {kind!r}")


def calibrate(
    deck: str,
    *,
    vout_target: float,
    efficiency_target: float | None = None,
    allowed_refdes: tuple[str, ...] = (),
    max_rounds: int = 3,
    tolerance: float = 0.02,
    timeout_s: float = 60.0,
) -> SpiceResult:
    """Optional LLM layer: nudge bounded knobs to bring measured efficiency
    toward ``efficiency_target``. The LLM only proposes a knob; each proposal
    is applied and re-simulated, and kept ONLY when the sim shows the metric
    actually moved closer (physics is the judge, never the LLM). Returns the
    pure engine result when there's no key or no metric target."""
    baseline = simulate(deck, vout_target=vout_target, tolerance=tolerance, timeout_s=timeout_s)
    if efficiency_target is None or not os.environ.get("MOONSHOT_API_KEY"):
        return baseline

    def err(r: SpiceResult) -> float:
        return abs(r.result["efficiency"] - efficiency_target)

    best = baseline
    current_deck = deck
    for _round in range(max_rounds):
        knob = _propose_knob(current_deck, vout_target, efficiency_target, best, allowed_refdes)
        if not knob:
            break
        try:
            cand_deck = _apply_knob(current_deck, knob)
            cand = simulate(cand_deck, vout_target=vout_target, tolerance=tolerance, timeout_s=timeout_s)
        except Exception:
            continue
        if cand.converged and err(cand) < err(best):  # measured improvement only
            cand.knobs = {**best.knobs, f"round{_round}": knob}
            best = cand
            current_deck = cand_deck
    return best


def _propose_knob(
    deck: str,
    vout_target: float,
    efficiency_target: float,
    current: SpiceResult,
    allowed_refdes: tuple[str, ...],
) -> dict[str, Any] | None:
    """Ask the LLM for ONE bounded knob proposal. Returns None on any
    contract violation (the caller then stops — no fabricated knob)."""
    import json

    from heaviside.agents.llm_call import call_agent_json

    msg = json.dumps({
        "task": "Propose ONE knob change to move measured efficiency toward the target.",
        "vout_target": vout_target,
        "efficiency_target": efficiency_target,
        "current_result": current.result,
        "allowed_refdes": list(allowed_refdes),
        "allowed_knobs": list(_ALLOWED_KNOBS),
        "instructions": (
            "Return {\"kind\": \"component_value\", \"refdes\": <one of allowed_refdes>, "
            "\"value\": \"<e.g. 100uF / 47uH / 10>\"} or "
            "{\"kind\": \"fsw\", \"value\": <hz>}. Propose only a physically sensible "
            "nudge; the simulator will verify it. Return {} if nothing should change."
        ),
    })
    try:
        data = call_agent_json("cross-referencer", msg, max_tokens=512, max_retries=1)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("kind") not in _ALLOWED_KNOBS:
        return None
    if data["kind"] == "component_value":
        if not data.get("refdes") or data.get("value") in (None, ""):
            return None
        if allowed_refdes and data["refdes"] not in allowed_refdes:
            return None
    elif data["kind"] == "fsw" and not isinstance(data.get("value"), (int, float)):
        return None
    return data
