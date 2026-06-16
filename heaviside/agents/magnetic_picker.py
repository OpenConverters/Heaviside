"""Pareto-front magnetic selection.

Wraps :func:`heaviside.bridge.design_magnetics_fast` (which calls
``PyOpenMagnetics.calculate_advised_magnetics_fast``) to expose a
small Pareto front of candidate magnetics, then provides:

* :func:`pareto_summary` — flatten each candidate to a one-row dict
  of human-readable metrics (shape, material, turns, core volume,
  estimated losses) suitable for an LLM to read and reason about.

* :func:`pick_best_pareto` — deterministic v0.1 selector. Picks the
  candidate with the lowest losses by default (the order PyOM returns
  them in). Other criteria are supported via ``criteria=``; the LLM
  agent will eventually call this with a criterion derived from the
  spec (e.g. "smallest volume" for space-constrained designs).

This module is the bridge between PyOM's fast Pareto exploration
mode and the upcoming Strands ``magnetic-pareto-picker`` agent.
The deterministic picker stays in place after the agent lands —
it is the offline fixture / smoke-test path.

All magnetics math comes from MKF/PyOM (per project rule); this
module only assembles values it reads off the returned MAS.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from heaviside.bridge import MagneticDesign, design_magnetics_fast

PARETO_CRITERIA: tuple[str, ...] = (
    "lowest_losses",  # PyOM's default sort (ascending scoring)
    "smallest_volume",  # pick by core effectiveVolume
    "highest_isat_headroom",  # pick by Bsat/Bpeak ratio if available
)


class MagneticPickerError(RuntimeError):
    """Raised when the Pareto picker cannot reach a valid candidate."""


def _candidate_summary(d: MagneticDesign, *, index: int) -> dict[str, Any]:
    """One-row summary of a MagneticDesign for LLM/agent consumption.

    Pulls human-readable metrics straight from the MAS — no derived
    physics here (per project rule, all magnetics math lives in MKF).
    Missing fields are reported as ``None`` rather than substituted.
    """
    mag = d.magnetic
    core = mag.get("core", {}) if isinstance(mag, Mapping) else {}
    coil = mag.get("coil", {}) if isinstance(mag, Mapping) else {}

    fd_core = core.get("functionalDescription", {}) if isinstance(core, Mapping) else {}
    shape = fd_core.get("shape") if isinstance(fd_core, Mapping) else None
    shape_name = (
        shape["name"]
        if isinstance(shape, Mapping) and "name" in shape
        else (shape if isinstance(shape, str) else None)
    )
    material = fd_core.get("material") if isinstance(fd_core, Mapping) else None
    material_name = (
        material["name"]
        if isinstance(material, Mapping) and "name" in material
        else (material if isinstance(material, str) else None)
    )
    gapping = fd_core.get("gapping") if isinstance(fd_core, Mapping) else None
    has_gap = bool(gapping) if isinstance(gapping, list) else None

    ep = core.get("processedDescription", {}) if isinstance(core, Mapping) else {}
    eff = ep.get("effectiveParameters", {}) if isinstance(ep, Mapping) else {}
    a_e = eff.get("effectiveArea")
    v_e = eff.get("effectiveVolume")

    fd_coil = coil.get("functionalDescription") if isinstance(coil, Mapping) else None
    n_windings = len(fd_coil) if isinstance(fd_coil, list) else None
    n_turns_primary = (
        fd_coil[0].get("numberTurns")
        if isinstance(fd_coil, list) and fd_coil and isinstance(fd_coil[0], Mapping)
        else None
    )

    return {
        "index": int(index),
        "scoring": float(d.scoring),
        "shape": shape_name,
        "material": material_name,
        "has_gap": has_gap,
        "n_windings": n_windings,
        "n_turns_primary": n_turns_primary,
        "effective_area_m2": a_e,
        "effective_volume_m3": v_e,
    }


def pareto_summary(designs: Sequence[MagneticDesign]) -> list[dict[str, Any]]:
    """Return a one-row-per-candidate summary table.

    Each row carries the fields an LLM needs to compare candidates
    without re-reading the full MAS: shape, material, turns, area,
    volume, fast-mode scoring (lower = lower estimated losses).
    """
    return [_candidate_summary(d, index=i) for i, d in enumerate(designs)]


def _round(x: Any, n: int = 4) -> Any:
    return round(float(x), n) if isinstance(x, (int, float)) else None


def pareto_summary_from_sweep(result: Any) -> list[dict[str, Any]]:
    """Summary table for the FREQUENCY-RESOLVED feasible front (master-plan B5).

    Each row carries the base MAS metrics (shape/material/turns/volume) PLUS the
    loss split and the chosen frequency the sweep resolved:
    ``total_loss_w`` (worst-OP magnetic + switching), ``magnetic_loss_w``,
    ``switching_loss_w``, ``fsw_hz``, and the saturation margin (``isat_a`` vs
    ``ipeak_worst_a``) at ``inductance_uh``. The front is already ascending by
    total loss, so ``index 0`` is the loss argmin at ``fsw*`` — the LLM only
    moves off it for a *qualitative* reason (stock, manufacturability, exotic
    part) and must justify the move.

    ``result`` is a ``frequency_sweep.FrequencySweepResult`` (duck-typed: a
    ``front`` of candidates + ``fsw_star_hz``)."""
    fsw = float(result.fsw_star_hz)
    rows: list[dict[str, Any]] = []
    for i, cand in enumerate(result.front):
        d = MagneticDesign(scoring=float(cand.scoring), mas=cand.mas, elapsed_s=0.0)
        row = _candidate_summary(d, index=i)
        row.update({
            "total_loss_w": _round(cand.total_loss_w),
            "magnetic_loss_w": _round(cand.magnetic_loss_w),
            "switching_loss_w": _round(cand.switching_loss_w),
            "fsw_hz": fsw,
            "isat_a": _round(cand.isat_a, 3),
            "ipeak_worst_a": _round(cand.ipeak_worst_a, 3),
            "inductance_uh": _round(cand.inductance_h * 1e6, 3),
        })
        rows.append(row)
    return rows


def pick_best_from_sweep(result: Any) -> int:
    """Deterministic pick over the frequency-resolved front: the total-loss
    argmin (index 0, since the front is sorted ascending). The offline / no-key
    / smoke path the LLM suitability pick layers on top of."""
    if not getattr(result, "front", None):
        raise MagneticPickerError("frequency sweep produced an empty feasible front")
    return 0


def pick_magnetic_from_sweep_llm(result: Any, spec: Mapping[str, Any]) -> dict[str, Any]:
    """LLM suitability pick over the loss-annotated front (master-plan B5).

    The deterministic argmin (``pick_best_from_sweep``) already chose the
    loss-optimal cell; this layer lets the picker apply QUALITATIVE judgment —
    stock, manufacturability, gapability, turn-count sanity — and pick a
    *different* index ONLY with justification. It can never invent an index
    outside the front. Falls back to the deterministic pick (index 0, source
    ``deterministic``) when no API key is configured.

    Returns ``{"index": int, "source": "llm"|"deterministic", "reason": str}``.
    """
    import os

    front = getattr(result, "front", None)
    if not front:
        raise MagneticPickerError("frequency sweep produced an empty feasible front")
    n = len(front)

    if not os.environ.get("MOONSHOT_API_KEY"):
        return {"index": 0, "source": "deterministic",
                "reason": "no API key — deterministic total-loss argmin"}

    import json

    from heaviside.agents.llm_call import LLMCallError, call_agent_json

    payload = {
        "topology_spec": {
            "inputVoltage": spec.get("inputVoltage"),
            "operatingPoints": spec.get("operatingPoints"),
        },
        "fsw_hz": float(result.fsw_star_hz),
        "candidates": pareto_summary_from_sweep(result),
        "instructions": (
            "index 0 is the total-loss argmin at fsw*. Pick ONE candidate by "
            "index from this list (you cannot invent one). Prefer index 0 unless "
            "a qualitative reason — stock, manufacturability, gapability, "
            "turn-count sanity, exotic core/material — justifies a nearby cell. "
            "Return JSON {\"index\": <int>, \"reason\": \"<1-2 sentences>\"}."
        ),
    }
    try:
        data = call_agent_json("magnetic-pareto-picker", json.dumps(payload))
        idx = int(data["index"])
    except (LLMCallError, KeyError, TypeError, ValueError) as exc:
        raise MagneticPickerError(
            f"magnetic-pareto-picker returned an unusable pick: {exc}"
        ) from exc
    if not (0 <= idx < n):
        raise MagneticPickerError(
            f"magnetic-pareto-picker picked index {idx} outside the front [0,{n}) "
            f"— it may not invent a candidate"
        )
    return {"index": idx, "source": "llm", "reason": str(data.get("reason", ""))}


def pick_best_pareto(
    designs: Sequence[MagneticDesign],
    *,
    criteria: str = "lowest_losses",
) -> int:
    """Pick the index of the best Pareto candidate per ``criteria``.

    v0.1: deterministic. The LLM-driven path will eventually call this
    with a criterion synthesised from the converter spec (e.g. small
    PCBs → ``smallest_volume``; thermally-constrained →
    ``lowest_losses``).
    """
    if not designs:
        raise MagneticPickerError("designs is empty — no candidates to pick from")
    if criteria not in PARETO_CRITERIA:
        raise MagneticPickerError(f"unknown criteria {criteria!r}; supported: {PARETO_CRITERIA}")

    if criteria == "lowest_losses":
        # PyOM returns ascending losses (lower scoring = better).
        return min(range(len(designs)), key=lambda i: designs[i].scoring)
    if criteria == "smallest_volume":

        def _vol(d: MagneticDesign) -> float:
            v = (
                d.magnetic.get("core", {})
                .get("processedDescription", {})
                .get("effectiveParameters", {})
                .get("effectiveVolume")
            )
            if not isinstance(v, (int, float)) or v <= 0:
                raise MagneticPickerError(
                    f"candidate has no usable effectiveVolume: shape={d.core_shape_name!r}"
                )
            return float(v)

        return min(range(len(designs)), key=lambda i: _vol(designs[i]))
    if criteria == "highest_isat_headroom":
        # Headroom proxy: more turns + larger A_e at the same target L
        # means more Bsat headroom. Use the product N × A_e as a rough
        # ranking — exact isat lives in MKF; we just rank candidates.
        def _headroom_proxy(d: MagneticDesign) -> float:
            ep = (
                d.magnetic.get("core", {})
                .get("processedDescription", {})
                .get("effectiveParameters", {})
            )
            a_e = ep.get("effectiveArea")
            fd_coil = d.magnetic.get("coil", {}).get("functionalDescription")
            n_turns = (
                fd_coil[0].get("numberTurns") if isinstance(fd_coil, list) and fd_coil else None
            )
            if not isinstance(a_e, (int, float)) or not isinstance(n_turns, int):
                raise MagneticPickerError(
                    f"candidate missing N/A_e for headroom proxy: shape={d.core_shape_name!r}"
                )
            return float(n_turns) * float(a_e)

        return max(range(len(designs)), key=lambda i: _headroom_proxy(designs[i]))
    # Unreachable per the PARETO_CRITERIA guard above.
    raise MagneticPickerError(f"criteria {criteria!r} not implemented")


def pareto_pick_main(
    topology: str,
    converter_spec: Mapping[str, Any],
    *,
    n_candidates: int = 5,
    criteria: str = "lowest_losses",
    core_mode: str = "standard cores",
) -> tuple[MagneticDesign, list[MagneticDesign]]:
    """End-to-end: get N fast-mode candidates, pick the best per criteria.

    Returns ``(picked, all_candidates)`` so callers can inspect or
    rerank without a second PyOM call.
    """
    candidates = design_magnetics_fast(
        topology,
        converter_spec,
        max_results=int(n_candidates),
        core_mode=core_mode,
    )
    idx = pick_best_pareto(candidates, criteria=criteria)
    return candidates[idx], candidates
