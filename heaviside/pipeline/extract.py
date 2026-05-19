"""Topology-aware enrichment of populated TAS for the realism gate.

The Heaviside pipeline currently produces a TAS with magnetic MAS attached
but no derived stresses, duty cycle, or scalar saturation current — so the
realism gate honestly reports INCOMPLETE.  This module fills the gap for
topologies where the derivation is cheap and unambiguous from spec + MAS
alone (no simulation, no datasheet lookup).

Public entry point: :func:`enrich_tas_for_realism`.

Per CLAUDE.md "no fallbacks, no defaults, no silent shortcuts — throw":
when a required spec or MAS field is missing, the extractor raises
:class:`EnrichmentError` rather than substituting a placeholder.  The
caller is then responsible for either fixing the spec or accepting the
honest INCOMPLETE verdict.

Topologies covered today
------------------------

  * ``buck`` — duty cycle from Vin range / Vout, scalar Isat from MAS
    saturation curve + core effective area + primary turns, worst-case
    Ipeak from spec (Vin_max, L·0.8 tolerance, Iout_max).

Everything else passes through unchanged.  Adding a new topology is a
matter of writing a ``_enrich_<topology>(tas, spec) -> None`` function
and registering it in :data:`_EXTRACTORS`.
"""

from __future__ import annotations

import copy
from typing import Any, Callable, Mapping


class EnrichmentError(Exception):
    """Raised when required spec / MAS fields for a topology extractor are
    missing or malformed.  Never silently swallowed — propagates to the
    CLI so the user sees exactly which input is wrong.
    """


# ---------------------------------------------------------------------------
# Buck extractor
# ---------------------------------------------------------------------------


def _require(d: Mapping[str, Any], path: tuple[str, ...], where: str) -> Any:
    """Walk ``d`` through ``path``; raise EnrichmentError on miss."""
    cur: Any = d
    for i, key in enumerate(path):
        if not isinstance(cur, Mapping) or key not in cur:
            joined = ".".join(path[: i + 1])
            raise EnrichmentError(f"{where}: missing required field {joined!r}")
        cur = cur[key]
    return cur


def _buck_vin_extremes(spec: Mapping[str, Any]) -> tuple[float, float]:
    vin = _require(spec, ("inputVoltage",), "buck spec")
    if not isinstance(vin, Mapping):
        raise EnrichmentError(
            f"buck spec.inputVoltage: expected mapping, got {type(vin).__name__}"
        )
    vmin = vin.get("minimum")
    vmax = vin.get("maximum")
    if not isinstance(vmin, (int, float)) or not isinstance(vmax, (int, float)):
        raise EnrichmentError(
            "buck spec.inputVoltage: requires numeric 'minimum' and 'maximum' "
            "(needed to bound duty cycle across the input range)"
        )
    if vmin <= 0 or vmax <= 0:
        raise EnrichmentError(
            f"buck spec.inputVoltage: must be positive, got min={vmin} max={vmax}"
        )
    if vmin > vmax:
        raise EnrichmentError(
            f"buck spec.inputVoltage: min={vmin} > max={vmax} (inverted)"
        )
    return float(vmin), float(vmax)


def _buck_operating_point(spec: Mapping[str, Any]) -> tuple[float, float, float]:
    """Return ``(Vout, Iout, fsw)`` from the first operating point."""
    ops = _require(spec, ("operatingPoints",), "buck spec")
    if not isinstance(ops, list) or not ops:
        raise EnrichmentError(
            "buck spec.operatingPoints: must be a non-empty list"
        )
    op = ops[0]
    if not isinstance(op, Mapping):
        raise EnrichmentError(
            f"buck spec.operatingPoints[0]: expected mapping, got {type(op).__name__}"
        )
    vouts = op.get("outputVoltages")
    iouts = op.get("outputCurrents")
    fsw = op.get("switchingFrequency")
    if not (isinstance(vouts, list) and vouts and isinstance(vouts[0], (int, float))):
        raise EnrichmentError(
            "buck spec.operatingPoints[0].outputVoltages[0]: required numeric"
        )
    if not (isinstance(iouts, list) and iouts and isinstance(iouts[0], (int, float))):
        raise EnrichmentError(
            "buck spec.operatingPoints[0].outputCurrents[0]: required numeric"
        )
    if not isinstance(fsw, (int, float)) or fsw <= 0:
        raise EnrichmentError(
            "buck spec.operatingPoints[0].switchingFrequency: required positive number"
        )
    return float(vouts[0]), float(iouts[0]), float(fsw)


def _buck_inductance(spec: Mapping[str, Any]) -> float:
    L = spec.get("desiredInductance")
    if not isinstance(L, (int, float)) or L <= 0:
        raise EnrichmentError(
            "buck spec.desiredInductance: required positive number (henries) — "
            "needed to compute inductor ripple and worst-case peak current"
        )
    return float(L)


def _conservative_bsat(saturation_curve: list[Mapping[str, Any]]) -> float:
    """Pick the worst-case (lowest) magneticFluxDensity from the MAS
    saturation curve, across all temperature samples.

    Ferrite saturation flux density falls with temperature, so picking the
    minimum is the right conservative choice for any operating Tj within
    the curve's temperature range.  If the curve has only 25 °C samples,
    that minimum is itself a warning that the material is being used near
    its weakest published point — but the check still runs.
    """
    if not isinstance(saturation_curve, list) or not saturation_curve:
        raise EnrichmentError(
            "MAS core.functionalDescription.material.saturation: "
            "expected non-empty list of {magneticField, magneticFluxDensity, temperature}"
        )
    b_values: list[float] = []
    for pt in saturation_curve:
        if not isinstance(pt, Mapping):
            raise EnrichmentError(
                f"saturation curve entry: expected mapping, got {type(pt).__name__}"
            )
        b = pt.get("magneticFluxDensity")
        if not isinstance(b, (int, float)) or b <= 0:
            raise EnrichmentError(
                f"saturation curve entry: invalid magneticFluxDensity {b!r}"
            )
        b_values.append(float(b))
    return min(b_values)


def _find_magnetic_component(tas: Mapping[str, Any]) -> tuple[int, int, Mapping[str, Any]]:
    """Return ``(stage_idx, comp_idx, comp)`` for the first magnetic.

    Buck has exactly one inductor (L1).  If the TAS holds more than one
    magnetic component, we still pick the first — buck's main output
    inductor.
    """
    stages = tas.get("stages")
    if not isinstance(stages, list):
        raise EnrichmentError("tas.stages: must be a list")
    for si, stage in enumerate(stages):
        if not isinstance(stage, Mapping):
            continue
        comps = stage.get("circuit", {}).get("components") if isinstance(stage.get("circuit"), Mapping) else None
        if not isinstance(comps, list):
            continue
        for ci, c in enumerate(comps):
            if isinstance(c, Mapping) and c.get("category") == "magnetic":
                return si, ci, c
    raise EnrichmentError(
        "buck enrichment: no component with category='magnetic' found — "
        "the bridge attach phase must have populated the inductor MAS first"
    )


def _enrich_buck(tas: dict, spec: Mapping[str, Any]) -> None:
    """Stamp ``duty`` on TAS root and ``isat`` / ``ipeak_worst`` on L1.

    Computed quantities (CCM buck, ideal duty-cycle relation):

      * ``D_max = Vout / Vin_min`` (worst case for high duty)
      * ``D_min = Vout / Vin_max`` (used by the ripple computation below
        because buck inductor ripple peaks at Vin_max → D_min)
      * ``ΔIL_worst = Vout · (1 − D_min) / (0.8·L · fsw)`` (the −20 %
        inductance tolerance from PROTEUS.md design rules)
      * ``Ipeak_worst = Iout + ΔIL_worst / 2``
      * ``Isat = B_sat · N · A_e / L`` (from ``L · I = N · Φ = N · B · A_e``)

    The duty stamped on TAS is ``D_max`` because the duty-cycle bounds
    check fails on the upper bound first (D > 0.95).  If a design also
    violates D < 0.05, that would happen at ``D_min`` — but a 0.05 lower
    bound is rarely the binding constraint for buck.  We additionally
    stamp ``duty_min`` / ``duty_max`` so a future extension of
    ``check_duty_cycle_bounds`` can check both bounds explicitly.
    """
    vmin, vmax = _buck_vin_extremes(spec)
    vout, iout, fsw = _buck_operating_point(spec)
    L = _buck_inductance(spec)

    if vout >= vmin:
        raise EnrichmentError(
            f"buck enrichment: Vout ({vout}) must be less than Vin_min ({vmin}) — "
            "buck cannot step up"
        )

    d_max = vout / vmin
    d_min = vout / vmax
    L_worst = 0.8 * L
    ripple_worst = vout * (1.0 - d_min) / (L_worst * fsw)
    ipeak_worst = iout + ripple_worst / 2.0

    si, ci, comp = _find_magnetic_component(tas)
    mas = comp.get("mas")
    if not isinstance(mas, Mapping):
        raise EnrichmentError(
            f"buck enrichment: TAS magnetic component {comp.get('name')!r} has no MAS "
            "payload — bridge attach phase must run before enrichment"
        )
    # Effective core area
    A_e = _require(
        mas, ("core", "processedDescription", "effectiveParameters", "effectiveArea"),
        "buck inductor MAS",
    )
    if not isinstance(A_e, (int, float)) or A_e <= 0:
        raise EnrichmentError(
            f"buck inductor MAS: effectiveArea must be positive, got {A_e!r}"
        )
    # Primary turns (buck has a single winding)
    fd = _require(mas, ("coil", "functionalDescription"), "buck inductor MAS")
    if not isinstance(fd, list) or not fd:
        raise EnrichmentError(
            "buck inductor MAS: coil.functionalDescription must be a non-empty list"
        )
    primary = fd[0]
    N = primary.get("numberTurns") if isinstance(primary, Mapping) else None
    if not isinstance(N, (int, float)) or N <= 0:
        raise EnrichmentError(
            f"buck inductor MAS: primary numberTurns must be positive, got {N!r}"
        )
    # Saturation flux density (conservative across temperature)
    sat = _require(
        mas, ("core", "functionalDescription", "material", "saturation"),
        "buck inductor MAS",
    )
    b_sat = _conservative_bsat(sat)

    # Isat = B_sat · N · A_e / L
    isat = b_sat * float(N) * float(A_e) / L

    # Stamp results
    tas["duty"] = round(d_max, 6)
    tas["duty_min"] = round(d_min, 6)
    tas["duty_max"] = round(d_max, 6)

    # Mutate in-place via the same path the orchestrator reads.
    enriched_comp = dict(comp)
    enriched_comp["isat"] = round(isat, 6)
    enriched_comp["ipeak_worst"] = round(ipeak_worst, 6)
    enriched_comp["isat_provenance"] = {
        "method": "B_sat * N * A_e / L (buck v0.1 extractor)",
        "b_sat_T": round(b_sat, 6),
        "n_turns": int(N),
        "effective_area_m2": float(A_e),
        "inductance_H": L,
    }
    enriched_comp["ipeak_provenance"] = {
        "method": "Iout + ripple_worst/2 at Vin_max, L*0.8",
        "iout_A": iout,
        "ripple_worst_A_pp": round(ripple_worst, 6),
        "vin_max_V": vmax,
        "fsw_Hz": fsw,
        "L_worst_H": L_worst,
    }
    tas["stages"][si]["circuit"]["components"][ci] = enriched_comp


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


_EXTRACTORS: dict[str, Callable[[dict, Mapping[str, Any]], None]] = {
    "buck": _enrich_buck,
}


def enrich_tas_for_realism(
    tas: Mapping[str, Any],
    *,
    topology: str,
    spec: Mapping[str, Any],
) -> dict:
    """Return a deep copy of ``tas`` with topology-specific derived fields
    stamped on so the realism orchestrator has data to check.

    For any topology without a registered extractor, returns a deep copy
    unchanged — the realism gate will then honestly report UNAVAILABLE for
    the relevant checks, which is the correct fail-closed behaviour per
    AGENTS.md rule 5.
    """
    if not isinstance(tas, Mapping):
        raise EnrichmentError(f"tas: expected mapping, got {type(tas).__name__}")
    if not isinstance(topology, str) or not topology.strip():
        raise EnrichmentError(f"topology: expected non-empty string, got {topology!r}")
    if not isinstance(spec, Mapping):
        raise EnrichmentError(f"spec: expected mapping, got {type(spec).__name__}")

    enriched = copy.deepcopy(dict(tas))
    extractor = _EXTRACTORS.get(topology.lower())
    if extractor is not None:
        extractor(enriched, spec)
    return enriched


__all__ = ("EnrichmentError", "enrich_tas_for_realism")
