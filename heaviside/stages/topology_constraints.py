"""topology_constraints — propose the converter-level design constraints MKF
needs to derive the magnetic (master-plan step B2).

MKF's BASE converter models derive L, the turns ratio, and the conduction
mode from the operating point plus two designer-chosen constraints:

* ``maximumDutyCycle`` — the duty ceiling the controller designs to.
* ``maximumDrainSourceVoltage`` — the worst-case voltage the main switch
  blocks (the FET voltage class).

Before B2 these were hardcoded (0.5 and 3·Vmax) inside ``converter_spec_build``.
This stage centralises them as a two-layer capability:

* engine ``deterministic`` — the band-guarded 0.5 / 3·Vmax fallback (one place,
  no longer smeared as literals);
* LLM ``propose`` — the ``topology-constraint-proposer`` agent picks
  per-topology values; a deterministic guard (``validate``) enforces the band
  (0.05 < D < 0.95, Vmax < Vds ≤ 20·Vmax) AND that the Vds class maps to a real
  switch present in TAS, and RAISES on a violation (no silent fix — surface it,
  per house rule). With no API key the engine fallback is used instead.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# Deterministic band — the guard rejects anything outside it.
DUTY_MIN: float = 0.05
DUTY_MAX: float = 0.95
VDS_FACTOR_DEFAULT: float = 3.0
VDS_FACTOR_MAX: float = 20.0


class TopologyConstraintError(ValueError):
    """A proposed constraint is out of band, or no real switch class backs the
    proposed Vds. Raised loudly so the orchestrator can re-propose / surface —
    never silently clamped."""


@dataclass(frozen=True, slots=True)
class DesignConstraints:
    maximum_duty_cycle: float
    maximum_drain_source_voltage: float
    source: str  # "deterministic" | "llm"
    rationale: str = ""

    def stamp(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Write the two constraints onto ``spec`` (only where absent, so an
        explicit caller-set value still wins) and return it."""
        spec.setdefault("maximumDutyCycle", self.maximum_duty_cycle)
        spec.setdefault("maximumDrainSourceVoltage", self.maximum_drain_source_voltage)
        return spec


def _vmax(spec: Mapping[str, Any]) -> float:
    iv = spec.get("inputVoltage") or {}
    vmax = iv.get("maximum") or iv.get("nominal") if isinstance(iv, Mapping) else None
    if not isinstance(vmax, (int, float)) or vmax <= 0:
        raise TopologyConstraintError(
            "spec.inputVoltage.maximum (or .nominal) required to size the switch class"
        )
    return float(vmax)


def deterministic(spec: Mapping[str, Any], topology: str | None = None) -> DesignConstraints:
    """The band-guarded fallback: 0.5 duty ceiling, 3·Vmax switch class.

    This is the single home of the values previously hardcoded in
    ``converter_spec_build``; it is in-band by construction (0.05 < 0.5 < 0.95;
    Vmax < 3·Vmax ≤ 20·Vmax)."""
    vmax = _vmax(spec)
    return DesignConstraints(
        maximum_duty_cycle=0.5,
        maximum_drain_source_voltage=round(vmax * VDS_FACTOR_DEFAULT, 1),
        source="deterministic",
        rationale=f"deterministic 0.5 duty / {VDS_FACTOR_DEFAULT:g}·Vmax fallback",
    )


def _real_switch_class_exists(vds_v: float, spec: Mapping[str, Any]) -> bool:
    """True if TAS has a production MOSFET that can block ``vds_v``.

    Reuses the tested selector (so the catalogue read + schema projection live
    in one place). A nominal Id floor is derived from the spec's output power so
    we don't reject a class for lack of a tiny-current part."""
    from heaviside.catalogue.selector import (
        MosfetConstraints,
        MosfetTiebreaker,
        SelectionError,
        select_mosfet,
    )

    # nominal Id: P_out / Vmax (a lower bound on switch current), floored small.
    id_floor = 0.1
    ops = spec.get("operatingPoints")
    if isinstance(ops, list) and ops and isinstance(ops[0], Mapping):
        vs = ops[0].get("outputVoltages") or []
        cs = ops[0].get("outputCurrents") or []
        if vs and cs and isinstance(vs[0], (int, float)) and isinstance(cs[0], (int, float)):
            pout = abs(float(vs[0]) * float(cs[0]))
            id_floor = max(id_floor, pout / _vmax(spec))
    try:
        select_mosfet(
            MosfetConstraints(
                vds_min=float(vds_v),
                id_min=float(id_floor),
                rds_on_max=float("inf"),
                qg_max=float("inf"),
            ),
            tiebreaker=MosfetTiebreaker.HIGHEST_VDS_MARGIN,
        )
        return True
    except SelectionError:
        return False


def validate(c: DesignConstraints, spec: Mapping[str, Any], *, check_tas: bool = True) -> None:
    """Raise :class:`TopologyConstraintError` if ``c`` is out of band or its Vds
    class has no real TAS switch. Pure guard — never mutates / clamps."""
    vmax = _vmax(spec)
    d = c.maximum_duty_cycle
    vds = c.maximum_drain_source_voltage
    if not isinstance(d, (int, float)) or not (DUTY_MIN < d < DUTY_MAX):
        raise TopologyConstraintError(
            f"maximumDutyCycle {d!r} out of band ({DUTY_MIN} < D < {DUTY_MAX})"
        )
    if not isinstance(vds, (int, float)) or not (vmax < vds <= VDS_FACTOR_MAX * vmax):
        raise TopologyConstraintError(
            f"maximumDrainSourceVoltage {vds!r} out of band "
            f"({vmax:g} < Vds ≤ {VDS_FACTOR_MAX:g}·Vmax={VDS_FACTOR_MAX * vmax:g})"
        )
    if check_tas and not _real_switch_class_exists(vds, spec):
        raise TopologyConstraintError(
            f"no production MOSFET in TAS can block {vds:g} V — the proposed Vds "
            f"class maps to no real switch (would thrash the refinement loop). "
            f"Re-propose a stocked voltage class."
        )


def _propose_llm(spec: Mapping[str, Any], topology: str | None) -> DesignConstraints:
    import json

    from heaviside.agents.llm_call import call_agent_json

    payload = {"topology": topology, "spec": _spec_digest(spec)}
    data = call_agent_json("topology-constraint-proposer", json.dumps(payload))
    try:
        d = float(data["maximumDutyCycle"])
        vds = float(data["maximumDrainSourceVoltage"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TopologyConstraintError(
            f"topology-constraint-proposer returned a malformed constraint object "
            f"({exc}): {data!r}"
        ) from exc
    return DesignConstraints(
        maximum_duty_cycle=d,
        maximum_drain_source_voltage=vds,
        source="llm",
        rationale=str(data.get("rationale", "")),
    )


def _spec_digest(spec: Mapping[str, Any]) -> dict[str, Any]:
    """The minimal spec slice the proposer needs (keeps the prompt small)."""
    ops = spec.get("operatingPoints")
    op0 = ops[0] if isinstance(ops, list) and ops and isinstance(ops[0], Mapping) else {}
    return {
        "inputVoltage": spec.get("inputVoltage"),
        "outputVoltages": op0.get("outputVoltages"),
        "outputCurrents": op0.get("outputCurrents"),
    }


def propose(
    spec: Mapping[str, Any],
    topology: str | None = None,
    *,
    use_llm: bool = True,
    check_tas: bool = True,
) -> DesignConstraints:
    """Return validated design constraints for ``topology``.

    LLM path (``use_llm`` and ``MOONSHOT_API_KEY`` set): the proposer agent
    picks the values; ``validate`` enforces the band + a real TAS switch class
    and RAISES on a violation. No-key / ``use_llm=False``: the deterministic
    fallback (in-band by construction). The deterministic result is validated
    too (so a TAS gap on the 3·Vmax class still surfaces)."""
    if use_llm and os.environ.get("MOONSHOT_API_KEY"):
        c = _propose_llm(spec, topology)
    else:
        c = deterministic(spec, topology)
    validate(c, spec, check_tas=check_tas)
    return c
