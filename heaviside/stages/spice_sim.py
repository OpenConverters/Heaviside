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
# deck rewriters in re_testbench (one place, already proven on real decks).
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


def simulate_self_contained_deck(
    deck: str,
    *,
    vout_target: float | None = None,
    tolerance: float = 0.02,
    timeout_s: float = 120.0,
    compute_operating_point: bool = False,
) -> SpiceResult:
    """Run a SELF-CONTAINED deck — one that carries its own ``.tran`` plus a
    ``.control``/``meas`` block and prints its output (e.g. a Kirchhoff
    ``tas_to_ngspice`` deck) — and parse the measured ``vout``.

    Unlike :func:`simulate`, this does NOT drive HS's duty-search runner: the
    deck is open-loop and self-measuring (its switch duty is fixed by the
    design), so it is run as-is via ``ngspice -b``. Parse/sim failures raise
    ``SimError`` — never a silent fallback to a plausible number.

    With ``compute_operating_point=True`` the deck is augmented with an input-
    current measurement so the FULL steady-state operating point
    (``vin, iin, vout, iout, pin, pout, total_losses, efficiency``) is returned
    in ``result`` — what the realism gate consumes via ``stamp_simulation_results``.
    This is REQUIRED when feeding the gate (a vout-only result would leave its
    efficiency/loss checks UNAVAILABLE); it fails loud if the deck's input
    source / load / input current cannot be resolved."""
    import re
    import shutil
    import subprocess
    import tempfile

    from heaviside.sim import SimError

    if shutil.which("ngspice") is None:
        raise SimError("ngspice not installed — cannot run a self-contained deck")

    src_name = vin = load_r = None
    if compute_operating_point:
        deck, src_name, vin, load_r = _augment_deck_for_operating_point(deck)

    with tempfile.TemporaryDirectory() as d:
        cir = os.path.join(d, "deck.cir")
        with open(cir, "w", encoding="utf-8") as fh:
            fh.write(deck)
        proc = subprocess.run(
            ["ngspice", "-b", cir], capture_output=True, text=True, timeout=timeout_s
        )
    out = proc.stdout + proc.stderr

    def _meas(name: str) -> list[float]:
        vals_: list[float] = []
        for m in re.findall(rf"{name}\s*=\s*([0-9.eE+\-]+)", out):
            try:
                v_ = float(m)
            except ValueError:
                continue
            if v_ == v_ and abs(v_) > 1e-12:
                vals_.append(v_)
        return vals_

    vals = [v for v in _meas("vout") if abs(v) > 1e-6]
    if not vals:
        raise SimError(
            f"self-contained deck produced no parseable vout (ngspice rc={proc.returncode}): "
            f"{out[-400:]}"
        )
    vout = max(vals, key=abs)  # settled output (signed; inverting topologies are negative)

    result: dict[str, float] = {"vout": vout}
    if compute_operating_point:
        iin_vals = _meas("iin")
        if not iin_vals or vin is None or load_r is None:
            raise SimError(
                "operating-point requested but could not resolve input current / source "
                f"({src_name}) / load from the deck (rc={proc.returncode}): {out[-300:]}"
            )
        iin = abs(max(iin_vals, key=abs))  # supplied input current (sign-agnostic)
        iout = abs(vout) / load_r
        pout = abs(vout) * iout
        pin = vin * iin
        if pin <= 0:
            raise SimError(f"non-physical input power pin={pin} (vin={vin}, iin={iin})")
        result.update(
            vin=vin, iin=iin, iout=iout, pin=pin, pout=pout,
            total_losses=pin - pout, efficiency=pout / pin,
        )

    converged = vout_target is None or (
        vout_target != 0
        and abs(abs(vout) - abs(vout_target)) / abs(vout_target) <= tolerance
    )
    return SpiceResult(
        result=result,
        closed_loop=False,
        vout_target=vout_target,
        converged=converged,
        deck=deck,
    )


def _augment_deck_for_operating_point(deck: str) -> tuple[str, str, float, float]:
    """Inject an input-current ``.meas`` into a Kirchhoff deck and resolve the
    input source name + voltage and the load resistance from it.

    Returns ``(augmented_deck, src_name, vin, load_r)``. The deck's testbench is
    ``V<src> <in> 0 DC <vin>`` (the only DC source — gate drivers are PULSE) and
    ``Rload <out> 0 <R>``. Fails loud via :class:`SimError` if either is absent
    (the operating point would otherwise be silently incomplete)."""
    import re

    from heaviside.sim import SimError

    m_src = re.search(r"(?m)^(V\w+)\s+\S+\s+\S+\s+DC\s+([0-9.eE+\-]+)", deck)
    m_load = re.search(r"(?m)^(R\w*load)\s+\S+\s+\S+\s+([0-9.eE+\-]+)", deck, re.IGNORECASE)
    if m_src is None or m_load is None:
        raise SimError(
            "cannot resolve input DC source / Rload from the self-contained deck — "
            "operating-point (efficiency) measurement is unavailable; refusing to "
            "produce a vout-only result for the realism gate"
        )
    src_name, vin, load_r = m_src.group(1), float(m_src.group(2)), float(m_load.group(2))
    win = re.search(r"from=([0-9.eE+\-]+)\s+to=([0-9.eE+\-]+)", deck)
    window = f" from={win.group(1)} to={win.group(2)}" if win else ""
    inject = f"meas tran iin AVG i({src_name}){window}\nprint iin\n"
    if ".endc" in deck:
        return deck.replace(".endc", inject + ".endc", 1), src_name, vin, load_r
    raise SimError("self-contained deck has no .control/.endc block to augment for the operating point")


def simulate_from_spec(
    topology: str,
    converter_json: Any,
    turns_ratios: Any,
    magnetizing_inductance: float,
    *,
    vout_target: float | None = None,
    backend: str = "kirchhoff",
    fidelity: str = "REQUIREMENTS",
    **sim_kwargs: Any,
) -> SpiceResult:
    """Deterministic engine: turn a converter spec into a deck and ``simulate`` it.

    Only the ``"kirchhoff"`` backend exists: design + assemble + emit via
    ``PyKirchhoff`` and run its self-contained deck. Only topologies bound
    in the adapter are supported; an unbound one raises
    ``KirchhoffTopologyUnsupported`` (no silent skip). Any other ``backend``
    value raises ``ValueError`` (the MKF-stencil ``decompose_from_spec``
    backend was removed in the della-Pollock cutover — no silent fallback).

    ``turns_ratios`` / ``magnetizing_inductance`` are retained in the
    signature for call-site compatibility; the Kirchhoff path designs the
    magnetic itself from the spec and ignores them."""
    if backend != "kirchhoff":
        raise ValueError(
            f"spice_sim: unknown backend {backend!r} (expected 'kirchhoff'; "
            "the 'mkf' decompose backend was removed in the della-Pollock cutover)"
        )
    from heaviside.decomposer import kirchhoff_adapter as _ka

    # Accept either shape: a Kirchhoff-native spec (designRequirements.outputs)
    # is used as-is; a Heaviside converter spec (top-level inputVoltage +
    # operatingPoints[].outputVoltages, what the real pipeline passes) is
    # translated. No silent guessing — translation is fail-loud.
    if isinstance(converter_json, dict) and "designRequirements" in converter_json:
        tas = _ka.design_topology_tas(topology, converter_json)
    else:
        tas = _ka.design_from_hs_spec(topology, converter_json)
    deck = _ka.tas_to_ngspice(tas, fidelity)
    return simulate_self_contained_deck(
        deck,
        vout_target=vout_target,
        tolerance=sim_kwargs.get("tolerance", 0.02),
        timeout_s=sim_kwargs.get("timeout_s", 120.0),
    )


def _apply_knob(deck: str, knob: dict[str, Any]) -> str:
    """Apply one bounded knob to the deck text using the proven rewriters."""
    from heaviside.pipeline.re_testbench import (
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
