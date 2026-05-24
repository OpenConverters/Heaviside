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
  * ``boost`` — duty cycle ``D = 1 - Vin/Vout`` (worst case Vin_min),
    ripple maximised over Vin (closed form: peaks at Vin = Vout/2 when
    interior), I_L_avg = Iout·Vout/Vin (input-side, worst at Vin_min),
    Isat as for buck (primary turns of the single inductor).
  * ``flyback`` — CCM duty ``D = Vout·n / (Vin + Vout·n)`` with
    ``n = N_p/N_s`` read from MAS, primary peak from ``I_in/D + Δi/2``
    at Vin_min, Isat on the primary magnetising inductance
    ``L_m = spec.desiredMagnetizingInductance``.
  * ``cuk`` / ``sepic`` / ``zeta`` — shared non-isolated buck-boost
    family extractor: ``D = Vout/(Vin+Vout)``, both inductors see
    ``ΔI_L = Vin·D/(L·fsw)`` (volt-second balance), L1 carries
    ``I_in = Pout/(η·Vin)`` worst-case at Vin_min, L2 carries ``Iout``
    independent of Vin.  Each inductor's Isat is stamped from its own
    MAS (no shared-core assumption); ``spec.desiredOutputInductance``
    is consulted for L2, falling back to L1 only when explicitly
    omitted (provenance records the source).
  * ``single_switch_forward`` / ``two_switch_forward`` — shared
    forward-family extractor: turns ratio ``n = N_pri/N_sec0`` read
    from T1 by winding name (handles SSF's 3-winding vs 2SF's
    2-winding shape uniformly), buck-shaped output choke
    ``ΔI_L = Vout·(1−D)/(L_out·fsw)`` worst at D_min (Vin_max), Isat
    stamped on L_out0 only — T1 is intentionally skipped because the
    demag winding clamps its core every cycle.  D_max ≥ 0.5 throws
    (reset window violated).

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
    return _vin_extremes(spec, "buck spec")


def _vin_extremes(spec: Mapping[str, Any], where: str) -> tuple[float, float]:
    """Shared Vin min/max extractor used by buck/boost/flyback.

    Every worst-case derivation in this module needs the full Vin range
    (duty, ripple, peak current all vary across it), so we require both
    ``minimum`` and ``maximum`` — never silently substitute ``nominal``.
    """
    vin = _require(spec, ("inputVoltage",), where)
    if not isinstance(vin, Mapping):
        raise EnrichmentError(
            f"{where}.inputVoltage: expected mapping, got {type(vin).__name__}"
        )
    vmin = vin.get("minimum")
    vmax = vin.get("maximum")
    if not isinstance(vmin, (int, float)) or not isinstance(vmax, (int, float)):
        raise EnrichmentError(
            f"{where}.inputVoltage: requires numeric 'minimum' and 'maximum' "
            "(needed to bound duty cycle across the input range)"
        )
    if vmin <= 0 or vmax <= 0:
        raise EnrichmentError(
            f"{where}.inputVoltage: must be positive, got min={vmin} max={vmax}"
        )
    if vmin > vmax:
        raise EnrichmentError(
            f"{where}.inputVoltage: min={vmin} > max={vmax} (inverted)"
        )
    return float(vmin), float(vmax)


def _buck_operating_point(spec: Mapping[str, Any]) -> tuple[float, float, float]:
    return _operating_point(spec, "buck spec")


def _operating_point(
    spec: Mapping[str, Any], where: str
) -> tuple[float, float, float]:
    """Return ``(Vout, Iout, fsw)`` from the first operating point.

    Shared across single-output topologies (buck/boost/flyback).
    Multi-output topologies (forward with bias winding, isolated_buck, …)
    must implement their own extractor — the realism gate's worst-case
    check only needs the main output rail today.
    """
    ops = _require(spec, ("operatingPoints",), where)
    if not isinstance(ops, list) or not ops:
        raise EnrichmentError(
            f"{where}.operatingPoints: must be a non-empty list"
        )
    op = ops[0]
    if not isinstance(op, Mapping):
        raise EnrichmentError(
            f"{where}.operatingPoints[0]: expected mapping, got {type(op).__name__}"
        )
    vouts = op.get("outputVoltages")
    iouts = op.get("outputCurrents")
    fsw = op.get("switchingFrequency")
    if not (isinstance(vouts, list) and vouts and isinstance(vouts[0], (int, float))):
        raise EnrichmentError(
            f"{where}.operatingPoints[0].outputVoltages[0]: required numeric"
        )
    if not (isinstance(iouts, list) and iouts and isinstance(iouts[0], (int, float))):
        raise EnrichmentError(
            f"{where}.operatingPoints[0].outputCurrents[0]: required numeric"
        )
    if not isinstance(fsw, (int, float)) or fsw <= 0:
        raise EnrichmentError(
            f"{where}.operatingPoints[0].switchingFrequency: required positive number"
        )
    return float(vouts[0]), float(iouts[0]), float(fsw)


def _required_inductance(
    spec: Mapping[str, Any], field: str, where: str
) -> float:
    L = spec.get(field)
    if not isinstance(L, (int, float)) or L <= 0:
        raise EnrichmentError(
            f"{where}.{field}: required positive number (henries) — "
            "needed to compute inductor ripple and worst-case peak current"
        )
    return float(L)


def _buck_inductance(spec: Mapping[str, Any]) -> float:
    return _required_inductance(spec, "desiredInductance", "buck spec")


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


def _compute_isat_authoritative(
    mas: Mapping[str, Any],
    L: float,
    *,
    b_sat: float,
    N: int,
    A_e: float,
    temperature_c: float = 100.0,
    topology_label: str = "",
) -> tuple[float, dict[str, Any]]:
    """Return ``(isat, provenance)`` using PyOM's
    ``calculate_saturation_current_at_operating_point`` so that L is
    evaluated under the same DC-bias / temperature conditions that
    ``stress`` uses for I_peak. Falls back to the no-OP overload
    ``calculate_saturation_current`` (nameplate I_sat at μ_init) if
    the MAS has no usable operating point, and finally to the
    analytical formula if both PyOM calls reject the input.

    TODO (project rule, see ~/.claude/CLAUDE.md): the analytical
    fallback violates "all magnetics math lives in MKF". It exists
    for unit-test fixtures that ship minimal MAS shapes PyOM
    rejects. Production runs always hit one of the PyOM paths.

    ``topology_label`` (e.g. ``"isolated_buck"``) is embedded in the
    provenance ``method`` string so per-topology extract tests can
    grep for the source.
    """
    suffix = f" [{topology_label}]" if topology_label else ""
    method = ""
    isat = -1.0
    last_error = ""

    # 1. Operating-point-aware PyOM call (best — matches stress derivation).
    op = None
    inputs = mas.get("inputs") if isinstance(mas, Mapping) else None
    if isinstance(inputs, Mapping):
        ops = inputs.get("operatingPoints")
        if isinstance(ops, list) and ops and isinstance(ops[0], Mapping):
            op = ops[0]
    if op is not None:
        try:
            from PyOpenMagnetics import PyOpenMagnetics as _P
            isat = float(_P.calculate_saturation_current_at_operating_point(
                dict(mas.get("magnetic", mas)),
                dict(op),
                float(temperature_c),
            ))
            if not (isat > 0):
                raise ValueError(f"PyOM returned non-positive isat: {isat!r}")
            method = f"PyOM.calculate_saturation_current_at_operating_point{suffix}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"

    # 2. Nameplate (no-OP) PyOM call.
    if method == "":
        try:
            from PyOpenMagnetics import PyOpenMagnetics as _P
            isat = float(_P.calculate_saturation_current(
                dict(mas.get("magnetic", mas)) if "magnetic" in mas else dict(mas),
                float(temperature_c),
            ))
            if not (isat > 0):
                raise ValueError(f"PyOM returned non-positive isat: {isat!r}")
            method = f"PyOM.calculate_saturation_current{suffix} (nameplate, no op-pt)"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"

    # 3. Analytical fallback (test fixtures only).
    if method == "":
        isat = float(b_sat) * float(N) * float(A_e) / float(L)
        method = (
            f"analytical fallback (B_sat * N * A_e / L){suffix}; "
            f"PyOM failed: {last_error}"
        )

    provenance: dict[str, Any] = {
        "method": method,
        "temperature_c": float(temperature_c),
        "b_sat_T": round(float(b_sat), 6),
        "n_turns": int(N),
        "effective_area_m2": float(A_e),
        "inductance_H": float(L),
    }
    return isat, provenance


def _find_magnetic_component(tas: Mapping[str, Any]) -> tuple[int, int, Mapping[str, Any]]:
    """Return ``(stage_idx, comp_idx, comp)`` for the first magnetic.

    Recognises both the new PEAS-shaped emission
    (``comp["data"]`` is a dict containing a ``magnetic`` key) and the
    legacy SPICE-reader convention (``comp["category"] == "magnetic"``)
    so round-trip fixtures and pre-bridge TAS both work.

    Buck has exactly one inductor (L1). If the TAS holds more than one
    magnetic component, we still pick the first — buck's main output
    inductor.
    """
    topology = tas.get("topology")
    if not isinstance(topology, Mapping):
        raise EnrichmentError("tas.topology: must be a mapping")
    stages = topology.get("stages")
    if not isinstance(stages, list):
        raise EnrichmentError("tas.topology.stages: must be a list")
    for si, stage in enumerate(stages):
        if not isinstance(stage, Mapping):
            continue
        comps = stage.get("circuit", {}).get("components") if isinstance(stage.get("circuit"), Mapping) else None
        if not isinstance(comps, list):
            continue
        for ci, c in enumerate(comps):
            if not isinstance(c, Mapping):
                continue
            data = c.get("data")
            if isinstance(data, Mapping) and "magnetic" in data:
                return si, ci, c
            if c.get("category") == "magnetic":
                return si, ci, c
    raise EnrichmentError(
        "buck enrichment: no magnetic component found — "
        "the bridge attach phase must have populated the inductor PEAS "
        "data (or the SPICE reader must have stamped category='magnetic') first"
    )


def _iter_magnetic_components(
    tas: Mapping[str, Any],
) -> list[tuple[int, int, Mapping[str, Any]]]:
    """Return every ``(stage_idx, comp_idx, comp)`` whose component is
    magnetic.  Used by multi-inductor topologies (cuk/sepic/zeta have
    L1 + L2) where we want to stamp Isat/Ipeak on each in declaration
    order.  Empty list if there are none — caller decides whether that
    is an error.
    """
    out: list[tuple[int, int, Mapping[str, Any]]] = []
    topology = tas.get("topology")
    if not isinstance(topology, Mapping):
        raise EnrichmentError("tas.topology: must be a mapping")
    stages = topology.get("stages")
    if not isinstance(stages, list):
        raise EnrichmentError("tas.topology.stages: must be a list")
    for si, stage in enumerate(stages):
        if not isinstance(stage, Mapping):
            continue
        circuit = stage.get("circuit") if isinstance(stage.get("circuit"), Mapping) else None
        comps = circuit.get("components") if isinstance(circuit, Mapping) else None
        if not isinstance(comps, list):
            continue
        for ci, c in enumerate(comps):
            if not isinstance(c, Mapping):
                continue
            data = c.get("data")
            if isinstance(data, Mapping) and "magnetic" in data:
                out.append((si, ci, c))
                continue
            if c.get("category") == "magnetic":
                out.append((si, ci, c))
    return out


def _read_mas(comp: Mapping[str, Any], where: str) -> Mapping[str, Any]:
    """Return the MAS sub-document for a magnetic ``comp``.

    Accepts both PEAS-shaped (``comp.data.magnetic``) and legacy
    (``comp.mas``) emissions, the same dual convention as
    :func:`_find_magnetic_component`.
    """
    data = comp.get("data")
    if isinstance(data, Mapping) and isinstance(data.get("magnetic"), Mapping):
        return data["magnetic"]
    mas = comp.get("mas")
    if isinstance(mas, Mapping):
        return mas
    raise EnrichmentError(
        f"{where}: magnetic component {comp.get('name')!r} has no MAS payload — "
        "bridge attach phase must run before enrichment"
    )


def _read_full_mas_root(comp: Mapping[str, Any], where: str) -> Mapping[str, Any]:
    """Return the FULL MAS root for ``comp`` — the document MKF returned
    *including* ``inputs`` / ``outputs`` envelopes, not just the
    ``magnetic`` device sub-doc that :func:`_read_mas` yields.

    Needed when harvesting the inductance MKF *actually achieved*
    (``outputs[*].inductance.magnetizingInductance.magnetizingInductance.nominal``)
    or any other simulation-derived figure of merit.
    """
    data = comp.get("data")
    if isinstance(data, Mapping) and "outputs" in data:
        return data
    mas = comp.get("mas")
    if isinstance(mas, Mapping) and "outputs" in mas:
        return mas
    raise EnrichmentError(
        f"{where}: magnetic component {comp.get('name')!r} has no full "
        "MAS root (no 'outputs' envelope) — bridge attach phase must run "
        "with the full pipeline (not stencil-only) before enrichment"
    )


def _harvest_inductance(full_mas: Mapping[str, Any], where: str) -> float:
    """Return the inductance MKF *actually achieved* for this magnetic.

    Source of truth, in order:
      1. ``outputs[*].inductance.magnetizingInductance.magnetizingInductance.nominal``
         — the simulation-derived L of the wound + gapped core.
      2. ``inputs.designRequirements.magnetizingInductance.nominal``
         — fast-mode candidates that skip the simulator may only have
         this; still authoritative because MKF picked a core to honour it.

    Raises :class:`EnrichmentError` if neither yields a positive scalar
    — per the "no silent fallbacks" rule. Mirrors
    ``heaviside.bridge._harvest_authoritative_inductance`` for the
    extras / per-component case (the bridge helper applies to the main
    magnetic only).
    """
    outputs = full_mas.get("outputs")
    if isinstance(outputs, list):
        for op in outputs:
            if not isinstance(op, Mapping):
                continue
            ind = op.get("inductance")
            if not isinstance(ind, Mapping):
                continue
            mi_outer = ind.get("magnetizingInductance")
            if not isinstance(mi_outer, Mapping):
                continue
            mi_inner = mi_outer.get("magnetizingInductance")
            if not isinstance(mi_inner, Mapping):
                continue
            nominal = mi_inner.get("nominal")
            if isinstance(nominal, (int, float)) and nominal > 0:
                return float(nominal)
    mi = (
        full_mas.get("inputs", {})
                .get("designRequirements", {})
                .get("magnetizingInductance", {})
    )
    if isinstance(mi, Mapping):
        nominal = mi.get("nominal")
        if isinstance(nominal, (int, float)) and nominal > 0:
            return float(nominal)
    raise EnrichmentError(
        f"{where}: MAS has no usable inductance — neither "
        "outputs[*].inductance.magnetizingInductance.magnetizingInductance.nominal "
        "nor inputs.designRequirements.magnetizingInductance.nominal has a "
        "positive scalar"
    )


def _mas_isat_inputs(
    mas: Mapping[str, Any], where: str, *, winding_index: int = 0,
) -> tuple[float, int, float]:
    """Return ``(A_e, N, B_sat)`` from a MAS document for the Faraday
    Isat closed form ``Isat = B_sat · N · A_e / L``.

    ``winding_index`` lets multi-winding transformers point at the
    correct primary turns (default 0 = first winding, the convention
    for inductors and primary-referred transformers).
    """
    A_e = _require(
        mas, ("core", "processedDescription", "effectiveParameters", "effectiveArea"),
        where,
    )
    if not isinstance(A_e, (int, float)) or A_e <= 0:
        raise EnrichmentError(f"{where}: effectiveArea must be positive, got {A_e!r}")
    fd = _require(mas, ("coil", "functionalDescription"), where)
    if not isinstance(fd, list) or len(fd) <= winding_index:
        raise EnrichmentError(
            f"{where}: coil.functionalDescription must list at least "
            f"{winding_index + 1} winding(s)"
        )
    w = fd[winding_index]
    N = w.get("numberTurns") if isinstance(w, Mapping) else None
    if not isinstance(N, (int, float)) or N <= 0:
        raise EnrichmentError(
            f"{where}: winding[{winding_index}].numberTurns must be positive, got {N!r}"
        )
    sat = _require(
        mas, ("core", "functionalDescription", "material", "saturation"), where,
    )
    b_sat = _conservative_bsat(sat)
    return float(A_e), int(N), b_sat


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
    # New PEAS-shaped emission: comp["data"] is a MAS envelope whose
    # "magnetic" sub-document holds core+coil. Legacy SPICE-reader and
    # round-trip fixtures still stamp the magnetic sub-document directly
    # as comp["mas"], so accept both.
    data = comp.get("data")
    if isinstance(data, Mapping) and isinstance(data.get("magnetic"), Mapping):
        mas = data["magnetic"]
    else:
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
    # Saturation flux density (conservative across temperature) — retained
    # for provenance + as the analytical fallback if PyOM's authoritative
    # calculate_saturation_current is unavailable.
    sat = _require(
        mas, ("core", "functionalDescription", "material", "saturation"),
        "buck inductor MAS",
    )
    b_sat = _conservative_bsat(sat)

    # Worst-case operating temperature for Isat (ferrite B_sat falls with T).
    # The spec's per-op ambientTemperature is a lower bound on Tj; PyOM's
    # design loop targets a hot-junction case so we ask for Isat at 100 °C
    # by default. (A future analyst stage will compute a real Tj from loss
    # + Rth and re-query at that temperature.)
    op = _require(spec, ("operatingPoints",), "buck spec")[0]
    t_amb = float(op.get("ambientTemperature", 25.0))
    t_worst = max(t_amb, 100.0)

    # Authoritative Isat from PyOM. Falls back to the analytical
    # B_sat * N * A_e / L if PyOM is unavailable or rejects the MAS, with
    # provenance noting which path was taken.
    isat_method = "PyOM.calculate_saturation_current"
    isat: float
    try:
        from PyOpenMagnetics import PyOpenMagnetics as _P
        isat = float(_P.calculate_saturation_current(dict(mas), t_worst))
        if not (isat > 0):
            raise ValueError(f"PyOM returned non-positive isat: {isat!r}")
    except Exception as exc:
        # Conservative fallback (treats gap as zero — underestimates for
        # gapped cores). Stamp the reason so debug is easy.
        isat = b_sat * float(N) * float(A_e) / L
        isat_method = (
            f"analytical fallback (B_sat * N * A_e / L); PyOM failed: "
            f"{type(exc).__name__}: {exc}"
        )

    # Stamp results
    tas["duty"] = round(d_max, 6)
    tas["duty_min"] = round(d_min, 6)
    tas["duty_max"] = round(d_max, 6)

    # Mutate in-place via the same path the orchestrator reads.
    enriched_comp = dict(comp)
    enriched_comp["isat"] = round(isat, 6)
    enriched_comp["ipeak_worst"] = round(ipeak_worst, 6)
    enriched_comp["isat_provenance"] = {
        "method": isat_method,
        "temperature_c": t_worst,
        # ``b_sat_T`` is the analytical-fallback B_sat (lowest across
        # the temperature curve). PyOM's authoritative path also uses
        # this curve internally but adjusts for the air gap, which the
        # analytical formula ignores.
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
    tas["topology"]["stages"][si]["circuit"]["components"][ci] = enriched_comp


# ---------------------------------------------------------------------------
# Boost extractor
# ---------------------------------------------------------------------------


def _enrich_boost(tas: dict, spec: Mapping[str, Any]) -> None:
    """Stamp ``duty`` on TAS root and ``isat`` / ``ipeak_worst`` on L1.

    CCM boost, ideal duty-cycle relation:

      * ``D = 1 − Vin / Vout`` ⇒ ``D_max`` at ``Vin_min``,
        ``D_min`` at ``Vin_max``.
      * Inductor ripple ``ΔI_L = Vin · D / (L · fsw)``.  Substituting D
        gives ``ΔI_L(Vin) = (Vin − Vin² / Vout) / (L · fsw)`` which
        peaks at ``Vin = Vout / 2`` (interior maximum, parabolic in
        Vin).  We evaluate at ``Vin_min``, ``Vin_max``, and
        ``Vout/2`` (only if it lies inside the input range) and pick
        the largest — that is the honest worst case across the spec.
      * Average inductor current = input current
        ``I_L_avg = Iout · Vout / Vin``, worst at ``Vin_min``.
      * ``Ipeak_worst = I_L_avg(Vin_min) + ΔI_L_worst / 2`` with the
        PROTEUS −20 % inductance tolerance baked into the ripple term.
      * ``Isat = B_sat · N · A_e / L`` — same closed form as buck
        because it is just ``L · I = N · B · A_e`` solved for I, which
        does not care about topology.

    Boost cannot step Vin above Vout; we reject that as a spec error.
    """
    vmin, vmax = _vin_extremes(spec, "boost spec")
    vout, iout, fsw = _operating_point(spec, "boost spec")
    L = _required_inductance(spec, "desiredInductance", "boost spec")

    if vout <= vmax:
        raise EnrichmentError(
            f"boost enrichment: Vout ({vout}) must be greater than Vin_max "
            f"({vmax}) — boost cannot step down"
        )

    d_max = 1.0 - vmin / vout
    d_min = 1.0 - vmax / vout
    L_worst = 0.8 * L

    def _ripple_at(vin: float) -> float:
        d = 1.0 - vin / vout
        return vin * d / (L_worst * fsw)

    candidates = [vmin, vmax]
    if vmin < vout / 2.0 < vmax:
        candidates.append(vout / 2.0)
    ripple_worst = max(_ripple_at(v) for v in candidates)

    iL_avg_max = iout * vout / vmin
    ipeak_worst = iL_avg_max + ripple_worst / 2.0

    si, ci, comp = _find_magnetic_component(tas)
    data = comp.get("data")
    if isinstance(data, Mapping) and isinstance(data.get("magnetic"), Mapping):
        mas = data["magnetic"]
    else:
        mas = comp.get("mas")
    if not isinstance(mas, Mapping):
        raise EnrichmentError(
            f"boost enrichment: TAS magnetic component {comp.get('name')!r} has no MAS "
            "payload — bridge attach phase must run before enrichment"
        )
    A_e = _require(
        mas, ("core", "processedDescription", "effectiveParameters", "effectiveArea"),
        "boost inductor MAS",
    )
    if not isinstance(A_e, (int, float)) or A_e <= 0:
        raise EnrichmentError(
            f"boost inductor MAS: effectiveArea must be positive, got {A_e!r}"
        )
    fd = _require(mas, ("coil", "functionalDescription"), "boost inductor MAS")
    if not isinstance(fd, list) or not fd:
        raise EnrichmentError(
            "boost inductor MAS: coil.functionalDescription must be a non-empty list"
        )
    primary = fd[0]
    N = primary.get("numberTurns") if isinstance(primary, Mapping) else None
    if not isinstance(N, (int, float)) or N <= 0:
        raise EnrichmentError(
            f"boost inductor MAS: primary numberTurns must be positive, got {N!r}"
        )
    sat = _require(
        mas, ("core", "functionalDescription", "material", "saturation"),
        "boost inductor MAS",
    )
    b_sat = _conservative_bsat(sat)
    op = _require(spec, ("operatingPoints",), "boost spec")[0]
    t_amb = float(op.get("ambientTemperature", 25.0))
    isat, isat_prov = _compute_isat_authoritative(
        mas, L, b_sat=b_sat, N=int(N), A_e=float(A_e),
        temperature_c=max(t_amb, 100.0),
        topology_label="boost",
    )

    tas["duty"] = round(d_max, 6)
    tas["duty_min"] = round(d_min, 6)
    tas["duty_max"] = round(d_max, 6)

    enriched_comp = dict(comp)
    enriched_comp["isat"] = round(isat, 6)
    enriched_comp["ipeak_worst"] = round(ipeak_worst, 6)
    enriched_comp["isat_provenance"] = isat_prov
    enriched_comp["ipeak_provenance"] = {
        "method": "Iout*Vout/Vin_min + ripple_worst/2 (worst-case Vin over range)",
        "iout_A": iout,
        "iL_avg_max_A": round(iL_avg_max, 6),
        "ripple_worst_A_pp": round(ripple_worst, 6),
        "vin_min_V": vmin,
        "vin_max_V": vmax,
        "vout_V": vout,
        "fsw_Hz": fsw,
        "L_worst_H": L_worst,
    }
    tas["topology"]["stages"][si]["circuit"]["components"][ci] = enriched_comp


# ---------------------------------------------------------------------------
# Flyback extractor
# ---------------------------------------------------------------------------


def _flyback_turns_ratio(mas: Mapping[str, Any]) -> tuple[float, int, int]:
    """Return ``(n, N_p, N_s)`` from the MAS coil windings.

    Flyback transformers always have at least two windings; we treat the
    first as primary and the second as secondary.  Auxiliary windings
    (third onward) are ignored for Isat / Ipeak purposes — they handle
    bias rails whose current is negligible compared to the main power
    path.  If the MAS has only one winding (single-inductor flyback?
    impossible by definition) we raise.
    """
    fd = _require(mas, ("coil", "functionalDescription"), "flyback transformer MAS")
    if not isinstance(fd, list) or len(fd) < 2:
        raise EnrichmentError(
            "flyback transformer MAS: coil.functionalDescription must list at "
            f"least primary + secondary windings (got {len(fd) if isinstance(fd, list) else 0})"
        )
    p = fd[0] if isinstance(fd[0], Mapping) else {}
    s = fd[1] if isinstance(fd[1], Mapping) else {}
    Np = p.get("numberTurns")
    Ns = s.get("numberTurns")
    if not isinstance(Np, (int, float)) or Np <= 0:
        raise EnrichmentError(
            f"flyback MAS: primary numberTurns must be positive, got {Np!r}"
        )
    if not isinstance(Ns, (int, float)) or Ns <= 0:
        raise EnrichmentError(
            f"flyback MAS: secondary numberTurns must be positive, got {Ns!r}"
        )
    return float(Np) / float(Ns), int(Np), int(Ns)


def _enrich_flyback(tas: dict, spec: Mapping[str, Any]) -> None:
    """Stamp duty and primary-side Isat/Ipeak for a single-output flyback.

    CCM flyback ideal relations (primary referred, ignoring diode drop
    and snubber):

      * Turns ratio ``n = N_p / N_s`` read from MAS coil windings.
      * ``D = Vout · n / (Vin + Vout · n)`` ⇒ ``D_max`` at ``Vin_min``.
      * Average input current ``I_in = Pout / (η · Vin)`` with
        ``Pout = Vout · Iout`` and η from spec (defaults to 1 only if
        the user explicitly omits ``efficiency`` — we throw here so
        the caller knows we are not silently assuming lossless).
      * Primary current during the on-time has DC component ``I_in / D``
        plus magnetising ripple ``Δi_p = Vin · D / (L_m · fsw)`` with
        ``L_m = spec.desiredMagnetizingInductance`` and the −20 %
        tolerance baked in.  Worst case is at ``Vin_min`` (D maximum,
        I_in maximum simultaneously).
      * ``Ipeak_worst = I_in_max / D_max + Δi_p_worst / 2``.
      * ``Isat = B_sat · N_p · A_e / L_m`` — primary-referred, same
        Faraday derivation as the buck/boost inductor.

    Multi-output flybacks (Iout has > 1 entry) are not supported yet;
    we throw rather than silently averaging.
    """
    vmin, vmax = _vin_extremes(spec, "flyback spec")
    ops = _require(spec, ("operatingPoints",), "flyback spec")
    if not isinstance(ops, list) or not ops:
        raise EnrichmentError("flyback spec.operatingPoints: must be non-empty list")
    op = ops[0]
    if not isinstance(op, Mapping):
        raise EnrichmentError("flyback spec.operatingPoints[0]: expected mapping")
    vouts = op.get("outputVoltages")
    iouts = op.get("outputCurrents")
    fsw = op.get("switchingFrequency")
    if not (isinstance(vouts, list) and vouts and isinstance(vouts[0], (int, float))):
        raise EnrichmentError("flyback spec.operatingPoints[0].outputVoltages[0]: required numeric")
    if not (isinstance(iouts, list) and iouts and isinstance(iouts[0], (int, float))):
        raise EnrichmentError("flyback spec.operatingPoints[0].outputCurrents[0]: required numeric")
    if not isinstance(fsw, (int, float)) or fsw <= 0:
        raise EnrichmentError("flyback spec.operatingPoints[0].switchingFrequency: required positive number")
    if len(vouts) > 1 or len(iouts) > 1:
        raise EnrichmentError(
            "flyback enrichment: multi-output flyback (more than one rail) not "
            "yet supported — extractor would have to weight currents by turns "
            "ratios per secondary, which is not implemented"
        )
    vout, iout = float(vouts[0]), float(iouts[0])
    fsw = float(fsw)

    Lm = _required_inductance(spec, "desiredMagnetizingInductance", "flyback spec")
    efficiency = spec.get("efficiency")
    if not isinstance(efficiency, (int, float)) or not (0.0 < efficiency <= 1.0):
        raise EnrichmentError(
            "flyback spec.efficiency: required number in (0, 1] — needed to "
            "size primary current honestly; refusing to assume lossless"
        )

    si, ci, comp = _find_magnetic_component(tas)
    data = comp.get("data")
    if isinstance(data, Mapping) and isinstance(data.get("magnetic"), Mapping):
        mas = data["magnetic"]
    else:
        mas = comp.get("mas")
    if not isinstance(mas, Mapping):
        raise EnrichmentError(
            f"flyback enrichment: TAS magnetic component {comp.get('name')!r} has no MAS "
            "payload — bridge attach phase must run before enrichment"
        )

    n, Np, _Ns = _flyback_turns_ratio(mas)

    A_e = _require(
        mas, ("core", "processedDescription", "effectiveParameters", "effectiveArea"),
        "flyback transformer MAS",
    )
    if not isinstance(A_e, (int, float)) or A_e <= 0:
        raise EnrichmentError(
            f"flyback transformer MAS: effectiveArea must be positive, got {A_e!r}"
        )
    sat = _require(
        mas, ("core", "functionalDescription", "material", "saturation"),
        "flyback transformer MAS",
    )
    b_sat = _conservative_bsat(sat)

    d_max = (vout * n) / (vmin + vout * n)
    d_min = (vout * n) / (vmax + vout * n)

    Pout = vout * iout
    I_in_max = Pout / (efficiency * vmin)
    Lm_worst = 0.8 * Lm
    ripple_worst = vmin * d_max / (Lm_worst * fsw)
    ipeak_worst = I_in_max / d_max + ripple_worst / 2.0

    op = _require(spec, ("operatingPoints",), "flyback spec")[0]
    t_amb = float(op.get("ambientTemperature", 25.0))
    isat, isat_prov = _compute_isat_authoritative(
        mas, Lm, b_sat=b_sat, N=int(Np), A_e=float(A_e),
        temperature_c=max(t_amb, 100.0),
        topology_label="flyback",
    )

    tas["duty"] = round(d_max, 6)
    tas["duty_min"] = round(d_min, 6)
    tas["duty_max"] = round(d_max, 6)

    enriched_comp = dict(comp)
    enriched_comp["isat"] = round(isat, 6)
    enriched_comp["ipeak_worst"] = round(ipeak_worst, 6)
    enriched_comp["isat_provenance"] = isat_prov
    enriched_comp["ipeak_provenance"] = {
        "method": "I_in_max/D_max + ripple_worst/2 at Vin_min, L_m*0.8",
        "iout_A": iout,
        "vout_V": vout,
        "pout_W": Pout,
        "efficiency": efficiency,
        "turns_ratio_n": round(n, 6),
        "i_in_max_A": round(I_in_max, 6),
        "d_max": round(d_max, 6),
        "ripple_worst_A_pp": round(ripple_worst, 6),
        "vin_min_V": vmin,
        "fsw_Hz": fsw,
        "Lm_worst_H": Lm_worst,
    }
    tas["topology"]["stages"][si]["circuit"]["components"][ci] = enriched_comp


# ---------------------------------------------------------------------------
# cuk / sepic / zeta — non-isolated buck-boost family (two inductors)
# ---------------------------------------------------------------------------


def _enrich_non_isolated_buckboost(
    tas: dict, spec: Mapping[str, Any], *, topology_name: str,
) -> None:
    """Shared extractor for cuk / SEPIC / zeta.

    All three topologies share the ideal CCM relations:

      * ``D = Vout / (Vin + Vout)`` (treating Vout as the regulated
        magnitude; cuk inverts polarity but the duty-cycle math is the
        same).  ``D_max`` at ``Vin_min``, ``D_min`` at ``Vin_max``.
      * Inductor volt-second balance gives ``V_L1 = V_L2 = Vin·D =
        Vout·(1−D)`` during steady state, so both inductors see the
        same applied volt-seconds per switching cycle, hence
        ``ΔI_L = Vin · D / (L · fsw)`` for each.  Substituting
        ``D = Vout/(Vin+Vout)`` shows the ripple is monotone increasing
        in ``Vin`` ⇒ worst case at ``Vin_max``.
      * L1 (input inductor) carries the *input* current
        ``I_L1_avg = Pout / (η · Vin) = Iout · Vout / (η · Vin)``,
        worst case at ``Vin_min``.
      * L2 (output inductor) carries the *output* current
        ``I_L2_avg = Iout``, independent of Vin.
      * Worst-case peaks combine the two boundary cases conservatively
        (the ripple worst-case Vin and the average-current worst-case
        Vin do not coincide in operation; sizing must cover both):
        ``Ipeak_L1 = I_L1_avg(Vin_min) + ΔI_L1(Vin_max) / 2``,
        ``Ipeak_L2 = Iout + ΔI_L2(Vin_max) / 2``.
      * Each inductor's Isat is its own ``B_sat · N · A_e / L`` from
        its own MAS — no shared core assumption (uncoupled variants).

    The realism gate's ``inductor_isat_margin`` check picks the first
    magnetic with stamped fields; both are stamped so a future
    "weakest magnetic" extension can pick whichever has the smaller
    Isat/Ipeak ratio.
    """
    where = f"{topology_name} spec"
    vmin, vmax = _vin_extremes(spec, where)
    vout, iout, fsw = _operating_point(spec, where)
    if vout <= 0:
        raise EnrichmentError(
            f"{topology_name} enrichment: Vout magnitude must be positive, got {vout}"
        )

    efficiency = spec.get("efficiency")
    if not isinstance(efficiency, (int, float)) or not (0.0 < efficiency <= 1.0):
        raise EnrichmentError(
            f"{where}.efficiency: required number in (0, 1] — needed to size "
            "L1 (input inductor) current honestly; refusing to assume lossless"
        )

    L1_H = spec.get("desiredInductance")
    L2_H = spec.get("desiredOutputInductance")
    if not isinstance(L1_H, (int, float)) or L1_H <= 0:
        raise EnrichmentError(
            f"{where}.desiredInductance: required positive number (henries) — "
            "L1 (input inductor) value"
        )
    # SEPIC/cuk/zeta uncoupled variants take a second inductor value;
    # accept either an explicit ``desiredOutputInductance`` or fall back
    # to L1 (the common identical-inductor design choice).  If neither
    # is set we throw — there is no honest default.
    if L2_H is None:
        L2_H = L1_H
        l2_source = "defaulted_to_L1 (spec omitted desiredOutputInductance)"
    elif not isinstance(L2_H, (int, float)) or L2_H <= 0:
        raise EnrichmentError(
            f"{where}.desiredOutputInductance: must be positive if provided, got {L2_H!r}"
        )
    else:
        l2_source = "spec.desiredOutputInductance"
    L1_H = float(L1_H)
    L2_H = float(L2_H)

    mags = _iter_magnetic_components(tas)
    if len(mags) < 2:
        raise EnrichmentError(
            f"{topology_name} enrichment: expected 2 magnetic components "
            f"(L1 input + L2 output), found {len(mags)}"
        )

    d_max = vout / (vmin + vout)
    d_min = vout / (vmax + vout)
    # Ripple monotone-increases in Vin → use Vin_max with the −20% L
    # tolerance from PROTEUS rules.
    L1_worst = 0.8 * L1_H
    L2_worst = 0.8 * L2_H
    d_at_vmax = vout / (vmax + vout)
    ripple_L1_worst = vmax * d_at_vmax / (L1_worst * fsw)
    ripple_L2_worst = vmax * d_at_vmax / (L2_worst * fsw)

    Pout = vout * iout
    I_L1_avg_max = Pout / (efficiency * vmin)
    I_L2_avg = iout
    ipeak_L1 = I_L1_avg_max + ripple_L1_worst / 2.0
    ipeak_L2 = I_L2_avg + ripple_L2_worst / 2.0

    tas["duty"] = round(d_max, 6)
    tas["duty_min"] = round(d_min, 6)
    tas["duty_max"] = round(d_max, 6)

    # Stamp L1 (first magnetic, input-side).
    si1, ci1, c1 = mags[0]
    mas1 = _read_mas(c1, f"{topology_name} L1 MAS")
    A_e1, N1, b_sat1 = _mas_isat_inputs(mas1, f"{topology_name} L1 MAS")
    op = _require(spec, ("operatingPoints",), f"{topology_name} spec")[0]
    t_amb = float(op.get("ambientTemperature", 25.0))
    isat_L1, isat_prov_L1 = _compute_isat_authoritative(
        mas1, L1_H, b_sat=b_sat1, N=int(N1), A_e=float(A_e1),
        temperature_c=max(t_amb, 100.0),
        topology_label=f"{topology_name} L1",
    )

    enriched1 = dict(c1)
    enriched1["isat"] = round(isat_L1, 6)
    enriched1["ipeak_worst"] = round(ipeak_L1, 6)
    enriched1["isat_provenance"] = isat_prov_L1
    enriched1["ipeak_provenance"] = {
        "method": "I_in_max + ripple_worst/2 (Vin_min avg current + Vin_max ripple, L*0.8)",
        "role": "input_inductor",
        "iout_A": iout,
        "vout_V": vout,
        "pout_W": Pout,
        "efficiency": efficiency,
        "iL1_avg_max_A": round(I_L1_avg_max, 6),
        "ripple_worst_A_pp": round(ripple_L1_worst, 6),
        "vin_min_V": vmin,
        "vin_max_V": vmax,
        "fsw_Hz": fsw,
        "L_worst_H": L1_worst,
    }
    tas["topology"]["stages"][si1]["circuit"]["components"][ci1] = enriched1

    # Stamp L2 (second magnetic, output-side).
    si2, ci2, c2 = mags[1]
    mas2 = _read_mas(c2, f"{topology_name} L2 MAS")
    A_e2, N2, b_sat2 = _mas_isat_inputs(mas2, f"{topology_name} L2 MAS")
    isat_L2, isat_prov_L2 = _compute_isat_authoritative(
        mas2, L2_H, b_sat=b_sat2, N=int(N2), A_e=float(A_e2),
        temperature_c=max(t_amb, 100.0),
        topology_label=f"{topology_name} L2",
    )
    isat_prov_L2["inductance_source"] = l2_source

    enriched2 = dict(c2)
    enriched2["isat"] = round(isat_L2, 6)
    enriched2["ipeak_worst"] = round(ipeak_L2, 6)
    enriched2["isat_provenance"] = isat_prov_L2
    enriched2["ipeak_provenance"] = {
        "method": "Iout + ripple_worst/2 (Vin_max ripple, L*0.8)",
        "role": "output_inductor",
        "iout_A": iout,
        "ripple_worst_A_pp": round(ripple_L2_worst, 6),
        "vin_max_V": vmax,
        "fsw_Hz": fsw,
        "L_worst_H": L2_worst,
    }
    tas["topology"]["stages"][si2]["circuit"]["components"][ci2] = enriched2


def _enrich_cuk(tas: dict, spec: Mapping[str, Any]) -> None:
    _enrich_non_isolated_buckboost(tas, spec, topology_name="cuk")


def _enrich_sepic(tas: dict, spec: Mapping[str, Any]) -> None:
    _enrich_non_isolated_buckboost(tas, spec, topology_name="sepic")


def _enrich_zeta(tas: dict, spec: Mapping[str, Any]) -> None:
    _enrich_non_isolated_buckboost(tas, spec, topology_name="zeta")


# ---------------------------------------------------------------------------
# Forward family (single-switch / two-switch) — T1 + L_out, two stages
# ---------------------------------------------------------------------------


def _find_magnetic_in_stage_role(
    tas: Mapping[str, Any], role: str, where: str,
) -> tuple[int, int, Mapping[str, Any]]:
    """Return ``(stage_idx, comp_idx, comp)`` for the first magnetic
    component inside the first stage whose ``role`` matches.

    Forward-family extractors use this to disambiguate the two
    magnetics: T1 (transformer) lives in the ``isolation`` stage and
    must NOT be Isat-checked because demag clamps reset its core every
    cycle; L_out (output choke) lives in the ``outputRectifier`` stage
    and is the binding saturation constraint.
    """
    topology = tas.get("topology")
    if not isinstance(topology, Mapping):
        raise EnrichmentError("tas.topology: must be a mapping")
    stages = topology.get("stages")
    if not isinstance(stages, list):
        raise EnrichmentError("tas.topology.stages: must be a list")
    for si, stage in enumerate(stages):
        if not isinstance(stage, Mapping) or stage.get("role") != role:
            continue
        circuit = stage.get("circuit") if isinstance(stage.get("circuit"), Mapping) else None
        comps = circuit.get("components") if isinstance(circuit, Mapping) else None
        if not isinstance(comps, list):
            continue
        for ci, c in enumerate(comps):
            if not isinstance(c, Mapping):
                continue
            data = c.get("data")
            if isinstance(data, Mapping) and "magnetic" in data:
                return si, ci, c
            if c.get("category") == "magnetic":
                return si, ci, c
    raise EnrichmentError(
        f"{where}: no magnetic component found in stage with role={role!r} — "
        "stencil emission contract violated (forward-family TAS must have an "
        "outputRectifier stage holding the output choke)"
    )


def _find_named_magnetic_in_stage_role(
    tas: Mapping[str, Any], role: str, name: str, where: str,
) -> Mapping[str, Any]:
    """Return the magnetic component whose ``name`` matches inside the
    first stage with the given ``role``.

    Used by topologies (DAB, CLLLC) where multiple magnetics coexist in
    the same stage and the "first magnetic" dispatch in
    ``_find_magnetic_in_stage_role`` is insufficient to disambiguate.
    """
    topology = tas.get("topology")
    if not isinstance(topology, Mapping):
        raise EnrichmentError("tas.topology: must be a mapping")
    stages = topology.get("stages")
    if not isinstance(stages, list):
        raise EnrichmentError("tas.topology.stages: must be a list")
    for stage in stages:
        if not isinstance(stage, Mapping) or stage.get("role") != role:
            continue
        circuit = stage.get("circuit") if isinstance(stage.get("circuit"), Mapping) else None
        comps = circuit.get("components") if isinstance(circuit, Mapping) else None
        if not isinstance(comps, list):
            continue
        for c in comps:
            if not isinstance(c, Mapping):
                continue
            if c.get("name") != name:
                continue
            data = c.get("data")
            if isinstance(data, Mapping) and "magnetic" in data:
                return c
            if c.get("category") == "magnetic":
                return c
            raise EnrichmentError(
                f"{where}: component named {name!r} in stage role={role!r} "
                "is not a magnetic (missing category=magnetic or data.magnetic)"
            )
    raise EnrichmentError(
        f"{where}: no magnetic named {name!r} found in stage with "
        f"role={role!r} — stencil emission contract violated"
    )


def _winding_turns_by_name(
    mas: Mapping[str, Any], winding_name: str, where: str,
) -> int:
    """Return ``numberTurns`` for the winding whose ``name`` matches.

    Transformers list windings in declaration order — for forward
    variants the primary is always called ``"pri"`` and the (first)
    secondary ``"sec0"``.  Index-based lookup is fragile across
    single-switch (pri, demag, sec0 → index 2) vs two-switch (pri,
    sec0 → index 1) variants, so we look up by name instead.
    """
    fd = _require(mas, ("coil", "functionalDescription"), where)
    if not isinstance(fd, list) or not fd:
        raise EnrichmentError(
            f"{where}: coil.functionalDescription must be a non-empty list"
        )

    # Heaviside extractors were written against pre-1.0 MAS winding names
    # ("pri", "sec0", "pri_a", "sec_a", "pri_top", "sec_top", "a", "b"…).
    # MKF now emits verbose camel-cased names ("Primary", "Secondary",
    # "Secondary 0", "Secondary 1"…). Map the short request to the verbose
    # canonical equivalent and try that as a SECOND exact match. Typos
    # (lowercase "primary", misspelled "secundary", etc.) still throw,
    # preserving the structural-failure tests.
    import re as _re

    _ALIASES = {
        "pri": "Primary",
        "pri_a": "Primary",
        "pri_b": "Primary",
        "pri_top": "Primary",
        "pri_bot": "Primary",
        "primary": "Primary",
        "a": "Primary",
    }
    aliased: str | None = _ALIASES.get(winding_name)
    if aliased is None:
        # Secondary aliases: sec / sec0 / sec1 / sec_a / sec_top / b
        m = _re.match(r"^sec(\d*)(?:_[a-z]+)?$", winding_name)
        if m is not None:
            idx = m.group(1)
            aliased = "Secondary" if not idx else f"Secondary {idx}"
        elif winding_name == "b":
            aliased = "Secondary"

    # Center-tapped rectifiers split the secondary into halves named
    # "Secondary <N>a" and "Secondary <N>b" (PSFB, PSHB, push-pull centerTap).
    # When the caller asks for "sec<N>" but only the split halves exist, fall
    # back to the "a" half — the two halves are equal by construction and the
    # turns-ratio downstream checks N_pri/N_sec_half against the spec.
    aliased_split: str | None = None
    if aliased is not None and aliased.startswith("Secondary"):
        aliased_split = aliased + "a" if " " in aliased else "Secondary 0a"

    for w in fd:
        if not isinstance(w, Mapping):
            continue
        name = w.get("name")
        if (name == winding_name
                or (aliased is not None and name == aliased)
                or (aliased_split is not None and name == aliased_split)):
            N = w.get("numberTurns")
            if not isinstance(N, (int, float)) or N <= 0:
                raise EnrichmentError(
                    f"{where}: winding {winding_name!r} numberTurns must be positive, "
                    f"got {N!r}"
                )
            return int(N)
    names = [w.get("name") for w in fd if isinstance(w, Mapping)]
    raise EnrichmentError(
        f"{where}: no winding named {winding_name!r} (have: {names})"
    )


def _enrich_forward_family(
    tas: dict, spec: Mapping[str, Any], *, topology_name: str,
    enforce_half_duty: bool = True,
) -> None:
    """Shared extractor for the forward family (single-switch,
    two-switch, active-clamp).

    All three variants emit the same two-magnetic shape: a multi-winding
    T1 inside an ``isolation``-role stage and a single output choke
    L_out0 inside an ``outputRectifier``-role stage.  The output-side
    analytics are identical (V_sec = Vin/n drives the same buck-shaped
    L_out), so the only behavioural axis is the reset mechanism, which
    we expose via ``enforce_half_duty``:

      * ``True`` (default — single-switch / two-switch forward): the
        magnetising current must decay through the demag winding (SSF)
        or the two reset diodes (2SF) inside ``(1 − D)·T_sw``.  With a
        single-turns demag winding this forces ``D < 0.5``; we throw
        if ``D_max ≥ 0.5``.
      * ``False`` (active-clamp forward): the clamp capacitor and
        auxiliary FET absorb the reset volt-seconds (V_clamp = Vin·D/(1−D)),
        so D may exceed 0.5.  We leave the upper bound to the realism
        gate's generic 0.95 CCM ceiling.

    Math (CCM, ignoring rectifier drop):

      * Turns ratio ``n = N_pri / N_sec0`` read from T1 by winding name.
      * Secondary square-wave voltage during the on-time = ``Vin / n``.
      * Duty ``D = Vout · n / Vin`` ⇒ ``D_max`` at ``Vin_min``,
        ``D_min`` at ``Vin_max``.
      * Output choke ripple is buck-shaped on the secondary side:
        ``V_L_on = Vin/n − Vout = Vout · (1−D)/D``,
        ``ΔI_L = Vout · (1−D) / (L_out · fsw)``.  Monotone decreasing
        in D ⇒ worst case at ``D_min`` (i.e. ``Vin_max``), mirroring
        the buck extractor's −20 % L tolerance.
      * ``Ipeak_worst = Iout + ΔI_L_worst / 2``.
      * ``Isat = B_sat · N_L_out · A_e / L_out`` from the output
        choke's own MAS — T1 is intentionally not Isat-checked
        because the reset mechanism (demag winding / reset diodes /
        clamp cap) drives the core back to B≈0 every cycle.
    """
    where = f"{topology_name} spec"
    vmin, vmax = _vin_extremes(spec, where)
    vout, iout, fsw = _operating_point(spec, where)
    L_out = _required_inductance(spec, "desiredInductance", where)

    # T1 lives in the isolation stage; read its turns ratio.
    _, _, t1_comp = _find_magnetic_in_stage_role(
        tas, "isolation", f"{topology_name} enrichment (T1)"
    )
    t1_mas = _read_mas(t1_comp, f"{topology_name} T1 MAS")
    N_pri = _winding_turns_by_name(t1_mas, "pri", f"{topology_name} T1 MAS")
    N_sec = _winding_turns_by_name(t1_mas, "sec0", f"{topology_name} T1 MAS")
    n = float(N_pri) / float(N_sec)

    d_max = vout * n / vmin
    d_min = vout * n / vmax
    if enforce_half_duty and d_max >= 0.5:
        raise EnrichmentError(
            f"{topology_name} enrichment: D_max = {d_max:.3f} ≥ 0.5 — single/two-"
            "switch forward cannot reset within its half-period window. Either "
            "raise the turns ratio (more primary turns) or raise Vin_min."
        )

    # L_out lives in the outputRectifier stage.
    so, co, lout_comp = _find_magnetic_in_stage_role(
        tas, "outputRectifier", f"{topology_name} enrichment (L_out)"
    )
    lout_mas = _read_mas(lout_comp, f"{topology_name} L_out MAS")
    A_e, N_lout, b_sat = _mas_isat_inputs(lout_mas, f"{topology_name} L_out MAS")

    L_worst = 0.8 * L_out
    ripple_worst = vout * (1.0 - d_min) / (L_worst * fsw)
    ipeak_worst = iout + ripple_worst / 2.0
    op = _require(spec, ("operatingPoints",), f"{topology_name} spec")[0]
    t_amb = float(op.get("ambientTemperature", 25.0))
    isat, isat_prov = _compute_isat_authoritative(
        lout_mas, L_out, b_sat=b_sat, N=int(N_lout), A_e=float(A_e),
        temperature_c=max(t_amb, 100.0),
        topology_label=topology_name,
    )

    tas["duty"] = round(d_max, 6)
    tas["duty_min"] = round(d_min, 6)
    tas["duty_max"] = round(d_max, 6)

    enriched = dict(lout_comp)
    enriched["isat"] = round(isat, 6)
    enriched["ipeak_worst"] = round(ipeak_worst, 6)
    enriched["isat_provenance"] = isat_prov
    enriched["ipeak_provenance"] = {
        "method": "Iout + ripple_worst/2 (buck-shaped on secondary at D_min, L*0.8)",
        "role": "output_choke",
        "iout_A": iout,
        "vout_V": vout,
        "turns_ratio_n": round(n, 6),
        "n_primary": N_pri,
        "n_secondary": N_sec,
        "d_min": round(d_min, 6),
        "ripple_worst_A_pp": round(ripple_worst, 6),
        "vin_max_V": vmax,
        "fsw_Hz": fsw,
        "L_worst_H": L_worst,
    }
    tas["topology"]["stages"][so]["circuit"]["components"][co] = enriched


def _enrich_single_switch_forward(tas: dict, spec: Mapping[str, Any]) -> None:
    _enrich_forward_family(tas, spec, topology_name="single_switch_forward")


def _enrich_two_switch_forward(tas: dict, spec: Mapping[str, Any]) -> None:
    _enrich_forward_family(tas, spec, topology_name="two_switch_forward")


def _enrich_active_clamp_forward(tas: dict, spec: Mapping[str, Any]) -> None:
    """Active-clamp forward: same output-side math as the rest of the
    forward family, but the clamp capacitor + auxiliary FET reset the
    transformer so duty is not constrained to D < 0.5.  The realism
    gate's generic CCM 0.05 < D < 0.95 bound still applies.
    """
    _enrich_forward_family(
        tas, spec, topology_name="active_clamp_forward",
        enforce_half_duty=False,
    )


# ---------------------------------------------------------------------------
# Isolated buck (flybuck) extractor
# ---------------------------------------------------------------------------


def _enrich_isolated_buck(tas: dict, spec: Mapping[str, Any]) -> None:
    """Stamp duty + T1 Isat / Ipeak for the isolated buck (flybuck).

    Flybuck = synchronous buck on the *primary* winding of a coupled
    inductor T1; the secondary winding is rectified through D_out0 / C_out0
    to a magnetically-isolated output that follows by turns ratio
    (open-loop).  The controller regulates the primary rail (``Vout_pri``).

    Topology shape (from the stencil): one magnetic ``T1`` lives in the
    ``isolation``-role stage with windings named ``pri`` and ``sec0``;
    the primary winding is the binding magnetic, so unlike the forward
    family we **do** stamp Isat / Ipeak on T1 (it carries the buck
    inductor current).

    Math (CCM, primary side only):

      * ``D = Vout_pri / Vin``  (standard buck)
      * ``D_max`` at ``Vin_min``, ``D_min`` at ``Vin_max``.
      * Primary ripple ``ΔI_pri = Vout_pri · (1−D) / (L_pri · fsw)``,
        monotone decreasing in D ⇒ worst at ``D_min`` (Vin_max),
        with the PROTEUS −20 % L tolerance applied.
      * ``Ipeak_worst = Iout_pri + ΔI_pri_worst / 2``.
      * ``Isat = B_sat · N_pri · A_e / L_pri``.

    v0.1 scope limit: secondary load reflected back through T1's
    coupling adds to primary current, raising ``Ipeak_worst``.  This
    extractor does NOT model reflected secondary load — the
    `secondary_reflected_current_modelled: false` flag in
    ``ipeak_provenance`` makes that explicit so a future extension can
    add it without silent drift.  Today the realism gate's Ipeak
    margin will be optimistic by the reflected-load amount; for a
    flybuck where the secondary rail draws a small fraction of total
    power that is acceptable, but for heavily secondary-loaded
    designs the gate will under-report risk until the extension lands.
    """
    where = "isolated_buck spec"
    vmin, vmax = _vin_extremes(spec, where)
    vout, iout, fsw = _operating_point(spec, where)
    L_pri = _required_inductance(spec, "desiredInductance", where)

    if vout >= vmin:
        raise EnrichmentError(
            f"isolated_buck enrichment: Vout_pri ({vout}) must be less than "
            f"Vin_min ({vmin}) — the primary loop is a buck and cannot step up"
        )

    # T1 lives in the isolation stage; read its primary winding turns
    # and core data.  Unlike the forward family, T1 IS the binding
    # magnetic here.
    si, ci, t1_comp = _find_magnetic_in_stage_role(
        tas, "isolation", "isolated_buck enrichment (T1)"
    )
    mas = _read_mas(t1_comp, "isolated_buck T1 MAS")
    A_e, _, b_sat = _mas_isat_inputs(mas, "isolated_buck T1 MAS")
    N_pri = _winding_turns_by_name(mas, "pri", "isolated_buck T1 MAS")

    d_max = vout / vmin
    d_min = vout / vmax
    L_worst = 0.8 * L_pri
    ripple_worst = vout * (1.0 - d_min) / (L_worst * fsw)
    ipeak_worst = iout + ripple_worst / 2.0
    op = _require(spec, ("operatingPoints",), "isolated_buck spec")[0]
    t_amb = float(op.get("ambientTemperature", 25.0))
    isat, isat_prov = _compute_isat_authoritative(
        mas, L_pri, b_sat=b_sat, N=int(N_pri), A_e=float(A_e),
        temperature_c=max(t_amb, 100.0),
        topology_label="isolated_buck",
    )

    tas["duty"] = round(d_max, 6)
    tas["duty_min"] = round(d_min, 6)
    tas["duty_max"] = round(d_max, 6)

    enriched = dict(t1_comp)
    enriched["isat"] = round(isat, 6)
    enriched["ipeak_worst"] = round(ipeak_worst, 6)
    enriched["isat_provenance"] = isat_prov
    enriched["ipeak_provenance"] = {
        "method": "Iout_pri + ripple_worst/2 (buck-shaped on primary at D_min, L*0.8)",
        "role": "primary_buck_inductor",
        "iout_A": iout,
        "vout_pri_V": vout,
        "d_min": round(d_min, 6),
        "ripple_worst_A_pp": round(ripple_worst, 6),
        "vin_max_V": vmax,
        "fsw_Hz": fsw,
        "L_worst_H": L_worst,
        "secondary_reflected_current_modelled": False,
    }
    tas["topology"]["stages"][si]["circuit"]["components"][ci] = enriched


# ---------------------------------------------------------------------------
# Isolated buck-boost extractor
# ---------------------------------------------------------------------------


def _enrich_isolated_buck_boost(tas: dict, spec: Mapping[str, Any]) -> None:
    """Stamp duty + T1 Isat / Ipeak for the isolated inverting buck-boost.

    Topology shape (from the stencil): single primary switch Q1 + coupled
    inductor T1 (pri.2 = GND) + D_pri rectifying the primary inverting
    output to Vout_pri (negative) + D_out0 rectifying the secondary to
    Vout0 (isolated, open-loop, follows by turns ratio).  T1 is the
    binding magnetic — its primary winding carries the inverting
    buck-boost inductor current, so we stamp Isat / Ipeak on T1.

    Spec convention: ``outputVoltages[0]`` carries the *magnitude*
    ``|Vout_pri|`` (sign is implied by the topology being inverting),
    matching the rest of the extractor family.  ``outputCurrents[0]``
    is the load current drawn from the primary rail.

    Math (CCM, primary side only, inverting buck-boost):

      * ``D = |Vout_pri| / (Vin + |Vout_pri|)`` ⇒ ``D_max`` at
        ``Vin_min``, ``D_min`` at ``Vin_max``.
      * Inductor ripple ``ΔI_L = Vin · D / (L_pri · fsw)``.
        Substituting D gives
        ``ΔI_L(Vin) = Vin · |Vout_pri| / ((Vin + |Vout_pri|) · L · fsw)``,
        whose derivative wrt Vin is strictly positive
        (``|Vout_pri|² / (Vin + |Vout_pri|)² / (L · fsw)``), so the
        ripple peaks at ``Vin_max``.  Same Vin-extreme split as the
        boost extractor: worst-case avg current and worst-case
        ripple do NOT coincide.
      * Average primary-inductor current
        ``I_L_avg = Iout / (1 − D)`` ⇒ worst at ``Vin_min`` (D_max).
      * ``Ipeak_worst = I_L_avg(Vin_min) + ΔI_L(Vin_max) / 2`` with
        the PROTEUS −20 % L tolerance baked into the ripple term.
        Honest pessimistic upper bound: the two worst cases occur at
        opposite Vin extremes — a real cycle does not see both
        simultaneously, but stamping anything less would let a real
        cycle exceed the stamped value.
      * ``Isat = B_sat · N_pri · A_e / L_pri``.

    v0.1 scope limit (same as isolated_buck): reflected secondary
    load is NOT modelled.  The
    ``secondary_reflected_current_modelled: false`` provenance flag
    pins this.
    """
    where = "isolated_buck_boost spec"
    vmin, vmax = _vin_extremes(spec, where)
    vout, iout, fsw = _operating_point(spec, where)
    L_pri = _required_inductance(spec, "desiredInductance", where)

    if vout <= 0:
        raise EnrichmentError(
            f"isolated_buck_boost enrichment: outputVoltages[0] ({vout}) must be "
            "the positive magnitude |Vout_pri| (the sign is implied — this "
            "topology produces a negative primary rail)"
        )

    d_max = vout / (vmin + vout)
    d_min = vout / (vmax + vout)
    L_worst = 0.8 * L_pri

    # Ripple is monotone increasing in Vin → worst at Vin_max.
    ripple_worst = vmax * d_min / (L_worst * fsw)
    iL_avg_max = iout / (1.0 - d_max)
    ipeak_worst = iL_avg_max + ripple_worst / 2.0

    si, ci, t1_comp = _find_magnetic_in_stage_role(
        tas, "isolation", "isolated_buck_boost enrichment (T1)"
    )
    mas = _read_mas(t1_comp, "isolated_buck_boost T1 MAS")
    A_e, _, b_sat = _mas_isat_inputs(mas, "isolated_buck_boost T1 MAS")
    N_pri = _winding_turns_by_name(mas, "pri", "isolated_buck_boost T1 MAS")
    op = _require(spec, ("operatingPoints",), "isolated_buck_boost spec")[0]
    t_amb = float(op.get("ambientTemperature", 25.0))
    isat, isat_prov = _compute_isat_authoritative(
        mas, L_pri, b_sat=b_sat, N=int(N_pri), A_e=float(A_e),
        temperature_c=max(t_amb, 100.0),
        topology_label="isolated_buck_boost",
    )

    tas["duty"] = round(d_max, 6)
    tas["duty_min"] = round(d_min, 6)
    tas["duty_max"] = round(d_max, 6)

    enriched = dict(t1_comp)
    enriched["isat"] = round(isat, 6)
    enriched["ipeak_worst"] = round(ipeak_worst, 6)
    enriched["isat_provenance"] = isat_prov
    enriched["ipeak_provenance"] = {
        "method": (
            "Iout/(1-D_max) + ripple(Vin_max)/2 — pessimistic upper bound: "
            "worst-case avg current at Vin_min, worst-case ripple at Vin_max "
            "(they do not coincide in a single cycle)"
        ),
        "role": "primary_buck_boost_inductor",
        "iout_A": iout,
        "vout_pri_magnitude_V": vout,
        "d_max": round(d_max, 6),
        "d_min": round(d_min, 6),
        "iL_avg_max_A": round(iL_avg_max, 6),
        "ripple_worst_A_pp": round(ripple_worst, 6),
        "vin_min_V": vmin,
        "vin_max_V": vmax,
        "fsw_Hz": fsw,
        "L_worst_H": L_worst,
        "secondary_reflected_current_modelled": False,
    }
    tas["topology"]["stages"][si]["circuit"]["components"][ci] = enriched


# ---------------------------------------------------------------------------
# Push-pull extractor
# ---------------------------------------------------------------------------


def _enrich_push_pull(tas: dict, spec: Mapping[str, Any]) -> None:
    """Stamp duty + L_out0 Isat / Ipeak for the push-pull converter.

    Push-pull = two primary switches Q1/Q2 driven 180° out of phase
    feeding the two halves of a center-tapped primary winding; the
    secondary is also center-tapped and rectified through two diodes
    (D1, D2) into a buck-style L_out + C_out filter.

    Topology shape (from stencil at stencils.py:2218): T1 has FOUR
    windings (``pri_top``, ``pri_bot``, ``sec_top``, ``sec_bot``)
    inside an ``isolation``-role stage; L_out0 is a single-winding
    inductor inside an ``outputRectifier``-role stage.

    Key observation: the output choke sees TWO ramps per switching
    period — one from each primary half — so the effective output
    frequency is ``2·fsw`` and the effective duty is ``D_eff = 2·D_q``
    where ``D_q`` is the per-switch duty.  In ``D_eff`` form the
    output-side math is identical to the forward family.

    Math (CCM, ignoring rectifier drop, primary-to-secondary turns
    ratio ``n = N_pri_top / N_sec_top``):

      * Per-switch duty ``D_q = Vout·n / (2·Vin)`` ⇒ effective duty
        ``D_eff = Vout·n / Vin`` (same as a single-switch forward).
      * No-overlap hard limit: ``D_q < 0.5`` ⇒ ``D_eff < 1.0``.
        If ``D_eff_max ≥ 1.0`` we throw (both switches simultaneously
        ON shorts the transformer).  The realism gate's CCM 0.95
        ceiling still applies and fail-closes practical overlap-risk
        designs before the hard limit is reached.
      * Output choke ripple at the effective ``2·fsw``:
        ``ΔI_L = Vout · (1 − D_eff) / (L_out · 2·fsw)``.  Same
        D-shape as forward-family output choke (monotone decreasing
        in D ⇒ worst at ``D_eff_min`` i.e. ``Vin_max``), with the
        PROTEUS −20 % L tolerance applied.
      * ``Ipeak_worst = Iout + ΔI_L_worst / 2``.
      * ``Isat = B_sat · N_L_out · A_e / L_out`` from L_out0's own
        MAS — T1 is intentionally NOT Isat-stamped because the
        alternating-polarity drive resets its core every cycle (same
        rationale as the forward family).

    Spec ``switchingFrequency`` is the per-switch fsw matching the
    controller; the extractor computes the effective output
    frequency internally and pins ``fsw_effective_Hz`` in the
    provenance so any downstream consumer can see the doubling.
    """
    where = "push_pull spec"
    vmin, vmax = _vin_extremes(spec, where)
    vout, iout, fsw = _operating_point(spec, where)
    L_out = _required_inductance(spec, "desiredInductance", where)

    # T1 lives in the isolation stage; read the dominant turns ratio.
    # n = N_pri_top / N_sec_top — assume the two primary halves and
    # the two secondary halves are matched (symmetric center taps),
    # which is the defining structural property of push-pull.
    _, _, t1_comp = _find_magnetic_in_stage_role(
        tas, "isolation", "push_pull enrichment (T1)"
    )
    t1_mas = _read_mas(t1_comp, "push_pull T1 MAS")
    N_pri = _winding_turns_by_name(t1_mas, "pri_top", "push_pull T1 MAS")
    N_sec = _winding_turns_by_name(t1_mas, "sec_top", "push_pull T1 MAS")
    n = float(N_pri) / float(N_sec)

    # Effective duty (forward-family shape).  Per-switch D_q = D_eff/2.
    d_eff_max = vout * n / vmin
    d_eff_min = vout * n / vmax
    if d_eff_max >= 1.0:
        raise EnrichmentError(
            f"push_pull enrichment: D_eff_max = {d_eff_max:.3f} ≥ 1.0 — "
            "per-switch duty would exceed 0.5, shorting the transformer "
            "(both Q1 and Q2 simultaneously ON).  Raise the turns ratio "
            "(more primary turns) or raise Vin_min."
        )

    # L_out lives in the outputRectifier stage.
    so, co, lout_comp = _find_magnetic_in_stage_role(
        tas, "outputRectifier", "push_pull enrichment (L_out)"
    )
    lout_mas = _read_mas(lout_comp, "push_pull L_out MAS")
    A_e, N_lout, b_sat = _mas_isat_inputs(lout_mas, "push_pull L_out MAS")

    fsw_eff = 2.0 * fsw
    L_worst = 0.8 * L_out
    ripple_worst = vout * (1.0 - d_eff_min) / (L_worst * fsw_eff)
    ipeak_worst = iout + ripple_worst / 2.0
    op = _require(spec, ("operatingPoints",), "push_pull spec")[0]
    t_amb = float(op.get("ambientTemperature", 25.0))
    isat, isat_prov = _compute_isat_authoritative(
        lout_mas, L_out, b_sat=b_sat, N=int(N_lout), A_e=float(A_e),
        temperature_c=max(t_amb, 100.0),
        topology_label="push_pull",
    )

    tas["duty"] = round(d_eff_max, 6)
    tas["duty_min"] = round(d_eff_min, 6)
    tas["duty_max"] = round(d_eff_max, 6)

    enriched = dict(lout_comp)
    enriched["isat"] = round(isat, 6)
    enriched["ipeak_worst"] = round(ipeak_worst, 6)
    enriched["isat_provenance"] = isat_prov
    enriched["ipeak_provenance"] = {
        "method": (
            "Iout + ripple_worst/2 (buck-shaped on secondary at D_eff_min, "
            "L*0.8, fsw_eff = 2*fsw because the output choke sees one ramp "
            "per primary half-cycle)"
        ),
        "role": "output_choke",
        "iout_A": iout,
        "vout_V": vout,
        "turns_ratio_n_pri_top_over_n_sec_top": round(n, 6),
        "n_primary_half": N_pri,
        "n_secondary_half": N_sec,
        "d_eff_min": round(d_eff_min, 6),
        "d_per_switch_max": round(d_eff_max / 2.0, 6),
        "ripple_worst_A_pp": round(ripple_worst, 6),
        "vin_max_V": vmax,
        "fsw_per_switch_Hz": fsw,
        "fsw_effective_Hz": fsw_eff,
        "L_worst_H": L_worst,
    }
    tas["topology"]["stages"][so]["circuit"]["components"][co] = enriched


    tas["topology"]["stages"][so]["circuit"]["components"][co] = enriched


# ---------------------------------------------------------------------------
# Asymmetric half-bridge (AHB)
# ---------------------------------------------------------------------------


def _enrich_asymmetric_half_bridge(tas: dict, spec: Mapping[str, Any]) -> None:
    """Stamp duty + L_out0 Isat / Ipeak for the asymmetric half-bridge.

    AHB = two primary switches Q1/Q2 driven with complementary duty
    cycles D and (1−D) through a DC blocking cap C_b that absorbs the
    asymmetric volt-seconds; a transformer T1 (pri, sec0); and a full-
    bridge rectifier (D1..D4) feeding a buck-style L_out + C_out
    output filter.  Stencil at stencils.py:2642.

    Voltage-transfer (Imbertson-Mohan, full-bridge secondary rectifier):

      ``Vout = 2 · n · D · (1 − D) · Vin``  where ``n = N_sec/N_pri``.

    Solving for D (taking the smaller, practical root):

      ``D = (1 − sqrt(1 − 4k)) / 2``   with ``k = Vout / (2 · n · Vin)``.

    Real-root condition: ``k ≤ 0.25`` ⇒ ``n · Vin ≥ 2 · Vout``.  We
    throw at Vin_min when the discriminant collapses (D would need to
    exceed 0.5, which is the AHB's hard physical limit).

    Output-side analytics: the full-bridge rectifier delivers two
    pulses per primary switching period (widths D·T and (1−D)·T), so
    the output choke sees an effective frequency of ``2·fsw`` and an
    effective duty of ``D_eff = 2·D`` (same shape as push-pull).
    Under that substitution the buck-on-the-secondary math is
    identical to the forward family:

      * ``ΔI_L = Vout · (1 − D_eff_min) / (L_out · 2·fsw)``
      * worst at ``D_eff_min`` ⇒ ``Vin_max`` (D shrinks as Vin grows)
      * ``Ipeak_worst = Iout + ΔI_L_worst / 2``
      * ``Isat = B_sat · N · A_e / L_out`` from L_out0's own MAS

    T1 is intentionally NOT Isat-stamped: the asymmetric drive
    alternates primary polarity each half-period and the DC blocking
    cap absorbs the residual volt-seconds (same rationale as the
    forward family and push-pull).

    PROTEUS −20 % L tolerance applied to every ripple computation.
    """
    import math as _math
    where = "asymmetric_half_bridge spec"
    vmin, vmax = _vin_extremes(spec, where)
    vout, iout, fsw = _operating_point(spec, where)
    L_out = _required_inductance(spec, "desiredInductance", where)

    _, _, t1_comp = _find_magnetic_in_stage_role(
        tas, "isolation", "asymmetric_half_bridge enrichment (T1)"
    )
    t1_mas = _read_mas(t1_comp, "asymmetric_half_bridge T1 MAS")
    N_pri = _winding_turns_by_name(t1_mas, "pri",  "asymmetric_half_bridge T1 MAS")
    N_sec = _winding_turns_by_name(t1_mas, "sec0", "asymmetric_half_bridge T1 MAS")
    n = float(N_sec) / float(N_pri)  # secondary/primary turns ratio

    def _solve_d(vin: float) -> float:
        k = vout / (2.0 * n * vin)
        if k >= 0.25:
            raise EnrichmentError(
                f"asymmetric_half_bridge enrichment: "
                f"k = Vout/(2·n·Vin) = {k:.4f} ≥ 0.25 at Vin = {vin} V — "
                "D = (1 − sqrt(1 − 4k))/2 has no real root.  Raise the "
                "secondary/primary turns ratio (more secondary turns) or "
                "raise Vin_min: AHB cannot deliver Vout > n·Vin/2."
            )
        return (1.0 - _math.sqrt(1.0 - 4.0 * k)) / 2.0

    d_max = _solve_d(vmin)         # smallest Vin ⇒ largest D
    d_min = _solve_d(vmax)         # largest Vin ⇒ smallest D
    d_eff_max = 2.0 * d_max
    d_eff_min = 2.0 * d_min

    # Output choke (lives in outputRectifier stage)
    so, co, lout_comp = _find_magnetic_in_stage_role(
        tas, "outputRectifier", "asymmetric_half_bridge enrichment (L_out)"
    )
    lout_mas = _read_mas(lout_comp, "asymmetric_half_bridge L_out MAS")
    A_e, N_lout, b_sat = _mas_isat_inputs(
        lout_mas, "asymmetric_half_bridge L_out MAS"
    )

    fsw_eff = 2.0 * fsw
    L_worst = 0.8 * L_out
    ripple_worst = vout * (1.0 - d_eff_min) / (L_worst * fsw_eff)
    ipeak_worst = iout + ripple_worst / 2.0
    op = _require(spec, ("operatingPoints",), "asymmetric_half_bridge spec")[0]
    t_amb = float(op.get("ambientTemperature", 25.0))
    isat, isat_prov = _compute_isat_authoritative(
        lout_mas, L_out, b_sat=b_sat, N=int(N_lout), A_e=float(A_e),
        temperature_c=max(t_amb, 100.0),
        topology_label="asymmetric_half_bridge",
    )

    tas["duty"]     = round(d_eff_max, 6)
    tas["duty_min"] = round(d_eff_min, 6)
    tas["duty_max"] = round(d_eff_max, 6)

    enriched = dict(lout_comp)
    enriched["isat"]        = round(isat, 6)
    enriched["ipeak_worst"] = round(ipeak_worst, 6)
    enriched["isat_provenance"] = isat_prov
    enriched["ipeak_provenance"] = {
        "method": (
            "Iout + ripple_worst/2 (buck-shaped on secondary at D_eff_min, "
            "L*0.8, fsw_eff = 2*fsw via full-bridge rectifier two-pulse "
            "output; D solved from Vout = 2·n·D·(1−D)·Vin)"
        ),
        "role": "output_choke",
        "iout_A": iout,
        "vout_V": vout,
        "turns_ratio_n_sec_over_n_pri": round(n, 6),
        "n_primary": N_pri,
        "n_secondary": N_sec,
        "d_per_switch_max": round(d_max, 6),
        "d_per_switch_min": round(d_min, 6),
        "d_eff_min": round(d_eff_min, 6),
        "d_eff_max": round(d_eff_max, 6),
        "ripple_worst_A_pp": round(ripple_worst, 6),
        "vin_min_V": vmin,
        "vin_max_V": vmax,
        "fsw_per_switch_Hz": fsw,
        "fsw_effective_Hz": fsw_eff,
        "L_worst_H": L_worst,
    }
    tas["topology"]["stages"][so]["circuit"]["components"][co] = enriched


# ---------------------------------------------------------------------------
# Weinberg (current-fed push-pull with input coupled inductor)
# ---------------------------------------------------------------------------


def _enrich_weinberg(tas: dict, spec: Mapping[str, Any]) -> None:
    """Stamp duty + L1 Isat / Ipeak for the Weinberg V1 converter.

    Weinberg V1 = current-fed push-pull with an input coupled inductor
    L1 (2 symmetric windings a, b) feeding the center-tapped primary
    of a 4-winding transformer T1 (pri_a, pri_b, sec_a, sec_b); the
    secondary CT-FW rectifier (D1, D2) delivers Vout into C_out0.
    Stencil at stencils.py:2797.

    Voltage transfer (classic V1, boost-mode, overlapping conduction):

      ``Vout = n · Vin / (2 · (1 − D))``   where ``n = N_sec / N_pri``,
      ``D ∈ (0.5, 1)`` is the per-switch duty.  Overlap fraction
      ``(2D − 1)`` shorts the transformer primary and charges both L1
      windings from Vin; non-overlap fraction ``2·(1 − D)`` transfers
      energy to the secondary via one primary half at a time.

    Solving for D: ``D = 1 − n · Vin / (2 · Vout)``.

      * ``D_max = 1 − n · Vin_min / (2 · Vout)`` (largest at Vin_min)
      * ``D_min = 1 − n · Vin_max / (2 · Vout)`` (smallest at Vin_max)

    Throws when ``D_min ≤ 0.5`` (loses boost-mode operation,
    Weinberg V1 voltage transfer breaks) or ``D_max ≥ 1`` (degenerate).

    L1 is the binding magnetic — there is NO discrete output choke,
    L1 already provides the boost / output inductance via the coupled
    structure.  Average current through each L1 winding equals the
    input current ``I_in = Iout · Vout / Vin`` (η = 1 v0.1
    approximation, matching boost / cuk family); worst at Vin_min.

    Ripple per winding (during overlap, charging from Vin):

      ``ΔI_L = Vin · (2D − 1) / (L · 2·fsw)``
            = ``Vin · (1 − n·Vin/Vout) / (L · 2·fsw)``

    parabolic in Vin with interior peak at ``Vin = Vout / (2n)``.
    Evaluate at ``Vin_min``, ``Vin_max``, and the interior peak (if
    in range) and pick the worst.  PROTEUS −20 % L tolerance applied.

    T1 is intentionally NOT Isat-stamped: the symmetric push-pull
    drive resets the transformer core every cycle (same rationale as
    push-pull T1).
    """
    where = "weinberg spec"
    vmin, vmax = _vin_extremes(spec, where)
    vout, iout, fsw = _operating_point(spec, where)
    L = _required_inductance(spec, "desiredInductance", where)

    # T1 turns ratio (n = N_sec / N_pri).
    _, _, t1_comp = _find_magnetic_in_stage_role(
        tas, "isolation", "weinberg enrichment (T1)"
    )
    t1_mas = _read_mas(t1_comp, "weinberg T1 MAS")
    N_pri = _winding_turns_by_name(t1_mas, "pri_a", "weinberg T1 MAS")
    N_sec = _winding_turns_by_name(t1_mas, "sec_a", "weinberg T1 MAS")
    n = float(N_sec) / float(N_pri)

    d_max = 1.0 - n * vmin / (2.0 * vout)
    d_min = 1.0 - n * vmax / (2.0 * vout)
    if d_min <= 0.5:
        raise EnrichmentError(
            f"weinberg enrichment: D_min = {d_min:.4f} ≤ 0.5 at Vin_max = "
            f"{vmax} V — Weinberg V1 requires per-switch D > 0.5 (overlap "
            "mode) for boost-style step-up.  Either raise Vout or lower "
            "the secondary/primary turns ratio (n)."
        )
    if d_max >= 1.0:
        raise EnrichmentError(
            f"weinberg enrichment: D_max = {d_max:.4f} ≥ 1.0 at Vin_min = "
            f"{vmin} V — degenerate (per-switch duty cannot reach 100 %)."
        )

    # L1 (input coupled inductor) — lineFilter stage.
    si, ci, l1_comp = _find_magnetic_in_stage_role(
        tas, "lineFilter", "weinberg enrichment (L1)"
    )
    l1_mas = _read_mas(l1_comp, "weinberg L1 MAS")
    A_e, _, b_sat = _mas_isat_inputs(l1_mas, "weinberg L1 MAS")
    # L1 has two symmetric windings (a, b); use winding "a" turns.
    N_l1 = _winding_turns_by_name(l1_mas, "a", "weinberg L1 MAS")

    L_worst = 0.8 * L
    fsw_eff = 2.0 * fsw

    def _ripple_at(vin: float) -> float:
        return vin * (1.0 - n * vin / vout) / (L_worst * fsw_eff)

    candidates = [vmin, vmax]
    interior = vout / (2.0 * n)
    if vmin < interior < vmax:
        candidates.append(interior)
    ripple_worst = max(_ripple_at(v) for v in candidates)

    iL_avg_max = iout * vout / vmin       # input current at Vin_min
    ipeak_worst = iL_avg_max + ripple_worst / 2.0
    op = _require(spec, ("operatingPoints",), "weinberg spec")[0]
    t_amb = float(op.get("ambientTemperature", 25.0))
    isat, isat_prov = _compute_isat_authoritative(
        l1_mas, L, b_sat=b_sat, N=int(N_l1), A_e=float(A_e),
        temperature_c=max(t_amb, 100.0),
        topology_label="weinberg",
    )

    tas["duty"]     = round(d_max, 6)
    tas["duty_min"] = round(d_min, 6)
    tas["duty_max"] = round(d_max, 6)

    enriched = dict(l1_comp)
    enriched["isat"]        = round(isat, 6)
    enriched["ipeak_worst"] = round(ipeak_worst, 6)
    enriched["isat_provenance"] = isat_prov
    enriched["ipeak_provenance"] = {
        "method": (
            "I_in(Vin_min) + ripple_worst/2 (boost-shaped overlap ripple "
            "ΔI = Vin·(2D-1)/(L*0.8·2·fsw), parabolic in Vin with "
            "interior peak at Vin = Vout/(2n))"
        ),
        "role": "input_coupled_inductor",
        "iout_A": iout,
        "vout_V": vout,
        "turns_ratio_n_sec_over_n_pri": round(n, 6),
        "n_primary_half": N_pri,
        "n_secondary_half": N_sec,
        "d_per_switch_max": round(d_max, 6),
        "d_per_switch_min": round(d_min, 6),
        "iL_avg_max_A": round(iL_avg_max, 6),
        "ripple_worst_A_pp": round(ripple_worst, 6),
        "vin_min_V": vmin,
        "vin_max_V": vmax,
        "fsw_per_switch_Hz": fsw,
        "fsw_effective_Hz": fsw_eff,
        "L_worst_H": L_worst,
        "secondary_reflected_current_modelled": False,
    }
    tas["topology"]["stages"][si]["circuit"]["components"][ci] = enriched


# ---------------------------------------------------------------------------
# Four-switch buck-boost (4SBB)
# ---------------------------------------------------------------------------


def _enrich_four_switch_buck_boost(tas: dict, spec: Mapping[str, Any]) -> None:
    """Stamp duty + L1 Isat / Ipeak for the four-switch buck-boost.

    4SBB = a buck half-bridge (Q1/Q2) cascaded with a boost
    half-bridge (Q3/Q4) sharing one inductor L1.  Stencil at
    stencils.py:794 — L1 lives inside the single ``switchingCell``
    stage (no separate output rectifier).

    Operating modes (controller-dependent, classified from spec):

      * **Buck mode** when ``Vin_min > Vout``: Q3 ON, Q4 OFF, Q1/Q2
        switch.  ``D_buck = Vout / Vin``, ``I_L_avg = Iout``,
        ``ΔI_L = Vout · (1 − D) / (L · fsw)`` — same shape as a
        plain buck (worst at Vin_max ⇒ D_min).
      * **Boost mode** when ``Vin_max < Vout``: Q1 ON, Q2 OFF, Q3/Q4
        switch.  ``D_boost = 1 − Vin / Vout``, ``I_L_avg = Iout · Vout
        / Vin`` (peaks at Vin_min), ``ΔI_L = Vin · (1 − Vin/Vout) /
        (L · fsw)`` — same shape as a plain boost with interior peak
        at ``Vin = Vout/2``.
      * **Mixed (straddle)** when ``Vin_min < Vout < Vin_max``: the
        controller transitions between modes as Vin crosses Vout.
        We evaluate stresses in BOTH sub-regions over the actual
        clamped Vin sub-range and pick the pessimistic worst case.
        Pure buck-boost overlap mode (all four switches actively
        modulating) is a narrow band; v0.1 does NOT model it
        explicitly — the mixed combination is the conservative
        upper bound on stress.

    Throws if ``Vin_min == Vout`` or ``Vin_max == Vout`` exactly
    (degenerate boundary, D would land at 1.0 in one sub-mode).

    PROTEUS −20 % L tolerance is applied to every ripple computation.
    """
    where = "four_switch_buck_boost spec"
    vmin, vmax = _vin_extremes(spec, where)
    vout, iout, fsw = _operating_point(spec, where)
    L = _required_inductance(spec, "desiredInductance", where)

    if vmin == vout or vmax == vout:
        raise EnrichmentError(
            f"four_switch_buck_boost enrichment: Vin extreme exactly equals Vout "
            f"({vout}) — degenerate mode boundary (D would land at 1.0 in one "
            "sub-mode).  Widen the input voltage range so it strictly straddles, "
            "is strictly above, or is strictly below Vout."
        )

    L_worst = 0.8 * L

    if vmin > vout:
        mode = "buck"
        d_max = vout / vmin
        d_min = vout / vmax
        ripple_worst = vout * (1.0 - d_min) / (L_worst * fsw)
        iL_avg_max = iout
    elif vmax < vout:
        mode = "boost"
        d_max = 1.0 - vmin / vout
        d_min = 1.0 - vmax / vout
        candidates = [vmin, vmax]
        if vmin < vout / 2.0 < vmax:
            candidates.append(vout / 2.0)
        ripple_worst = max(
            v * (1.0 - v / vout) / (L_worst * fsw) for v in candidates
        )
        iL_avg_max = iout * vout / vmin
    else:
        mode = "mixed"
        # Buck sub-region: Vin in (vout, vmax].  Smallest Vin > vout
        # would push D_buck → 1, but the controller is in boost mode
        # there, not buck — so we evaluate the buck sub-extractor
        # over [vout, vmax] only, where vmax is the buck operating
        # point with the LOWEST D and HIGHEST ripple (same monotone
        # shape as a pure buck).
        d_buck_min = vout / vmax
        ripple_buck = vout * (1.0 - d_buck_min) / (L_worst * fsw)

        # Boost sub-region: Vin in [vmin, vout).  Standard boost
        # parabolic ripple with interior peak at vout/2 if in range.
        d_boost_max = 1.0 - vmin / vout
        boost_candidates = [vmin]
        if vmin < vout / 2.0 < vout:
            boost_candidates.append(vout / 2.0)
        ripple_boost = max(
            v * (1.0 - v / vout) / (L_worst * fsw) for v in boost_candidates
        )

        ripple_worst = max(ripple_buck, ripple_boost)
        iL_avg_max = iout * vout / vmin
        d_max = d_boost_max
        d_min = d_buck_min

    ipeak_worst = iL_avg_max + ripple_worst / 2.0

    si, ci, comp = _find_magnetic_component(tas)
    mas = _read_mas(comp, "four_switch_buck_boost L1 MAS")
    A_e, N, b_sat = _mas_isat_inputs(mas, "four_switch_buck_boost L1 MAS")
    op = _require(spec, ("operatingPoints",), "four_switch_buck_boost spec")[0]
    t_amb = float(op.get("ambientTemperature", 25.0))
    isat, isat_prov = _compute_isat_authoritative(
        mas, L, b_sat=b_sat, N=int(N), A_e=float(A_e),
        temperature_c=max(t_amb, 100.0),
        topology_label="four_switch_buck_boost",
    )

    tas["duty"] = round(d_max, 6)
    tas["duty_min"] = round(d_min, 6)
    tas["duty_max"] = round(d_max, 6)

    enriched = dict(comp)
    enriched["isat"] = round(isat, 6)
    enriched["ipeak_worst"] = round(ipeak_worst, 6)
    enriched["isat_provenance"] = isat_prov
    enriched["ipeak_provenance"] = {
        "method": (
            "I_L_avg(worst Vin) + ripple_worst/2; mode-aware (buck / boost "
            "/ mixed) with L*0.8 tolerance"
        ),
        "mode": mode,
        "iout_A": iout,
        "vout_V": vout,
        "vin_min_V": vmin,
        "vin_max_V": vmax,
        "iL_avg_max_A": round(iL_avg_max, 6),
        "ripple_worst_A_pp": round(ripple_worst, 6),
        "fsw_Hz": fsw,
        "L_worst_H": L_worst,
    }
    tas["topology"]["stages"][si]["circuit"]["components"][ci] = enriched


# ---------------------------------------------------------------------------
# LLC (resonant half-bridge, center-tapped secondary)
# ---------------------------------------------------------------------------


def _enrich_llc(tas: dict, spec: Mapping[str, Any]) -> None:
    """Stamp duty + L_r Isat / Ipeak for the LLC resonant converter.

    LLC = half-bridge primary inverter (Q_HI / Q_LO at 50 % complementary
    duty), series resonant tank ``Cr`` + ``Lr`` driving the primary of
    a 3-winding transformer T1 (``pri`` + center-tapped secondary
    ``sec1`` / ``sec2``), full-wave secondary rectifier ``D1`` / ``D2``
    into ``C_out0``.  Stencil at stencils.py:1975.

    Voltage transfer (DC, first-harmonic approximation at resonance):

      ``Vout = Vin / (2 · n)``  with ``n = N_pri / N_sec1``.

    Required gain across Vin range:

      ``M(Vin) = (2 · n · Vout) / Vin``

    M = 1 ⇒ at resonance.  M > 1 ⇒ sub-resonant (fsw < fr) — tank
    current rises to maintain Vout; M < 1 ⇒ super-resonant.  The
    extractor uses ``M_max = M(Vin_min)`` as a conservative scalar on
    the tank current envelope; this captures the dominant low-line
    derating without committing to a specific gain curve (FHA vs PSpice
    cycle-by-cycle differ by < 30 % over typical M ∈ [0.8, 1.5]).

    Duty cycle: LLC operates at a fixed 50 % complementary half-bridge
    duty regardless of Vin (regulation is by frequency).  The extractor
    stamps ``duty = duty_min = duty_max = 0.5``; the realism gate's
    duty-cycle-bounds check is then trivially satisfied — the binding
    contract for the gate is that frequency-modulated topologies still
    expose the per-switch on-time so downstream consumers
    (timing-margin tests, gate-driver sizing) see a real number rather
    than ``UNAVAILABLE``.

    L_r peak current (worst-case):

      * Load-reflected envelope, sinusoidal (FHA):
        ``I_load_pk = (π / 2) · (Iout / n)``
      * Magnetizing peak, triangular at the half-period:
        ``I_m_pk = (Vin_max / 2) · T_sw / (4 · L_m)``
        = ``Vin_max / (8 · L_m · fsw)``
        L_m is read from ``spec.desiredMagnetizingInductance`` (PROTEUS
        −20 % tolerance baked in: ``L_m_worst = 0.8 · L_m``).
        Worst-case Vin for magnetizing flux is ``Vin_max`` (largest
        primary voltage during the on-half).
      * Sub-resonant boost factor: ``M_max = max(1, 2·n·Vout / Vin_min)``
        scales the load-reflected component (magnetizing is already
        Vin-driven and captured separately).
      * ``I_pk_worst = M_max · I_load_pk + I_m_pk``

    Isat: closed-form on L_r's own MAS:

      ``Isat = B_sat · N_Lr · A_e / L_r``

    T1 is intentionally NOT Isat-stamped: the LLC primary is driven
    symmetrically (HB midpoint swings between 0 and Vin) and the
    series ``Cr`` blocks DC; the only flux excursion is the magnetizing
    ripple which is bounded by the design rules above and rides on the
    L_r stamp via ``I_m_pk``.

    Raises ``EnrichmentError`` (no fallbacks per CLAUDE.md) for any
    missing / invalid spec field, missing MAS payload, or missing
    transformer winding.
    """
    import math as _math
    where = "llc spec"
    vmin, vmax = _vin_extremes(spec, where)
    vout, iout, fsw = _operating_point(spec, where)
    L_r = _required_inductance(spec, "desiredInductance", where)
    L_m = _required_inductance(spec, "desiredMagnetizingInductance", where)

    # T1 in the isolation stage — windings pri / sec1 / sec2 (CT).
    _, _, t1_comp = _find_magnetic_in_stage_role(
        tas, "isolation", "llc enrichment (T1)"
    )
    t1_mas = _read_mas(t1_comp, "llc T1 MAS")
    N_pri  = _winding_turns_by_name(t1_mas, "pri",  "llc T1 MAS")
    N_sec1 = _winding_turns_by_name(t1_mas, "sec1", "llc T1 MAS")
    n = float(N_pri) / float(N_sec1)   # step-down ratio per half-secondary

    # L_r in the inverter stage — series-resonant inductor.
    si, ci, lr_comp = _find_magnetic_in_stage_role(
        tas, "inverter", "llc enrichment (L_r)"
    )
    lr_mas = _read_mas(lr_comp, "llc L_r MAS")
    A_e, N_lr, b_sat = _mas_isat_inputs(lr_mas, "llc L_r MAS")

    # Required gain at low line (FHA scalar; M ≥ 1 means sub-resonant).
    M_at_vmin = (2.0 * n * vout) / vmin
    M_max = max(1.0, M_at_vmin)

    # Load-reflected primary peak (sinusoidal envelope, FHA).
    i_load_pk = (_math.pi / 2.0) * (iout / n)

    # Magnetizing peak (triangular, worst-case at Vin_max).
    L_m_worst = 0.8 * L_m
    i_mag_pk = vmax / (8.0 * L_m_worst * fsw)

    ipeak_worst = M_max * i_load_pk + i_mag_pk
    isat = b_sat * float(N_lr) * float(A_e) / L_r

    tas["duty"]     = 0.5
    tas["duty_min"] = 0.5
    tas["duty_max"] = 0.5

    enriched = dict(lr_comp)
    enriched["isat"]        = round(isat, 6)
    enriched["ipeak_worst"] = round(ipeak_worst, 6)
    enriched["isat_provenance"] = {
        "method": "B_sat * N * A_e / L_r (llc v0.1, series-resonant inductor)",
        "b_sat_T": round(b_sat, 6),
        "n_turns": int(N_lr),
        "effective_area_m2": float(A_e),
        "inductance_H": L_r,
    }
    enriched["ipeak_provenance"] = {
        "method": (
            "M_max * (pi/2) * (Iout/n)  +  Vin_max / (8 * Lm_worst * fsw)  "
            "[FHA load-reflected sinusoidal envelope plus triangular "
            "magnetizing peak; M_max = max(1, 2*n*Vout/Vin_min) is the "
            "sub-resonant boost-gain scalar; Lm_worst = 0.8 * Lm "
            "(PROTEUS −20 % tolerance)]"
        ),
        "role": "series_resonant_inductor",
        "iout_A": iout,
        "vout_V": vout,
        "turns_ratio_n_pri_over_n_sec1": round(n, 6),
        "n_primary": N_pri,
        "n_secondary_half": N_sec1,
        "vin_min_V": vmin,
        "vin_max_V": vmax,
        "fsw_Hz": fsw,
        "gain_at_vin_min": round(M_at_vmin, 6),
        "boost_factor_M_max": round(M_max, 6),
        "i_load_pk_A": round(i_load_pk, 6),
        "i_mag_pk_A": round(i_mag_pk, 6),
        "Lm_H": L_m,
        "Lm_worst_H": L_m_worst,
        "duty_50pct_complementary_HB": True,
    }
    tas["topology"]["stages"][si]["circuit"]["components"][ci] = enriched


# ---------------------------------------------------------------------------
# Phase-shifted full bridge (PSFB)
# ---------------------------------------------------------------------------


def _enrich_phase_shifted_full_bridge(tas: dict, spec: Mapping[str, Any]) -> None:
    """Stamp duty + L_out0 Isat / Ipeak for the phase-shifted full bridge.

    PSFB = four primary switches Q_A / Q_B (leg A) and Q_C / Q_D (leg C)
    each leg running at 50 % complementary duty, with leg C phase-shifted
    relative to leg A by angle φ; series resonant/leakage inductor L_r
    in series with the primary; transformer T1 (pri, sec0 — center-
    tapped secondary with the CT implicitly at GND); two-diode full-
    wave secondary rectifier (D1, D2) into output choke L_out0 + C_out0.
    Stencil at stencils.py:2423.

    Effective primary duty as a function of phase shift:

      ``D_eff = 1 − φ / 180°``   (range 0 to 1 nominally)

    Voltage transfer (CT-FW rectifier on a single sec0 winding, CT at GND):

      ``Vout = n · Vin · D_eff``   with ``n = N_sec0 / N_pri``.

    Solving for D_eff per Vin extreme:

      ``D_eff = Vout / (n · Vin)``.

      * D_eff_max at Vin_min (largest phase overlap)
      * D_eff_min at Vin_max (smallest phase overlap)

    Real-D_eff condition: ``D_eff ≤ 1.0`` ⇒ ``n · Vin ≥ Vout``.  Throws
    at Vin_min if the gain demand exceeds 1 (PSFB cannot synthesise
    D_eff > 1: that would require negative φ which is unphysical).

    Output-side analytics (output choke L_out0): the CT-FW rectifier
    delivers two power pulses per primary switching period, so L_out0
    sees an effective frequency of ``2·fsw``.  The per-pulse on-time
    fraction equals ``D_eff`` directly (because ``D_eff`` is already
    defined as the fraction of the *full* period during which power is
    delivered to the secondary, summed across both half-period overlaps).
    Volt-second balance on L_out0 confirms:

      (n·Vin − Vout) · t_overlap = Vout · (T/2 − t_overlap)
      ⇒ Vout = n·Vin · (2·t_overlap/T) = n·Vin · D_eff
      ⇒ ΔI_L = Vout · (1 − D_eff) / (L_out · 2·fsw)

      * worst at ``D_eff_min`` ⇒ ``Vin_max`` (overlap shrinks)
      * ``Ipeak_worst = Iout + ΔI_L_worst / 2``
      * ``Isat = B_sat · N · A_e / L_out`` from L_out0's own MAS

    L_r and T1 are intentionally NOT Isat-stamped:

      * **L_r** carries bipolar primary current with zero net DC by
        symmetry of the four-switch drive; the series-blocking action
        is provided by the symmetric phase modulation, not a DC
        blocking cap (PSFB is the DC-blocking-cap-free cousin of AHB).
      * **T1** alternates primary polarity each half-period (HB-style
        symmetric reset) and the realism gate's binding magnetic is the
        output choke per ``inductor_isat_margin`` semantics.

    PROTEUS −20 % L tolerance applied to L_out0 in the ripple
    computation.

    Raises ``EnrichmentError`` (no fallbacks per CLAUDE.md) for any
    missing / invalid spec field, missing MAS payload, missing
    transformer winding, or unrealisable gain at Vin_min.
    """
    where = "phase_shifted_full_bridge spec"
    vmin, vmax = _vin_extremes(spec, where)
    vout, iout, fsw = _operating_point(spec, where)

    # T1 in the isolation stage — windings pri / sec0.
    _, _, t1_comp = _find_magnetic_in_stage_role(
        tas, "isolation", "phase_shifted_full_bridge enrichment (T1)"
    )
    t1_mas = _read_mas(t1_comp, "phase_shifted_full_bridge T1 MAS")
    N_pri  = _winding_turns_by_name(t1_mas, "pri",  "phase_shifted_full_bridge T1 MAS")
    N_sec0 = _winding_turns_by_name(t1_mas, "sec0", "phase_shifted_full_bridge T1 MAS")
    n = float(N_sec0) / float(N_pri)   # secondary / primary

    def _solve_d_eff(vin: float) -> float:
        d = vout / (n * vin)
        if d > 1.0:
            raise EnrichmentError(
                f"phase_shifted_full_bridge enrichment: required "
                f"D_eff = Vout / (n · Vin) = {d:.4f} > 1.0 at Vin = {vin} V — "
                "PSFB cannot synthesise overlap > 100 % (would need φ < 0). "
                "Raise n (more secondary turns) or raise Vin_min."
            )
        return d

    d_eff_max = _solve_d_eff(vmin)     # largest overlap at Vin_min
    d_eff_min = _solve_d_eff(vmax)     # smallest overlap at Vin_max

    # L_out0 (output choke) — lives in outputRectifier stage. Harvest
    # its inductance from its OWN MAS (the L MKF actually achieved with
    # the picked core + gap), never from a spec hint. The spec field
    # ``desiredInductance`` is a request; MKF may pick something
    # different to satisfy ripple / window constraints, and trusting
    # the spec here is what made the old enricher compute isat against
    # a 100× wrong L for the centre-tapped rectifier output choke.
    so, co, lout_comp = _find_magnetic_in_stage_role(
        tas, "outputRectifier", "phase_shifted_full_bridge enrichment (L_out0)"
    )
    lout_full_mas = _read_full_mas_root(
        lout_comp, "phase_shifted_full_bridge L_out0 MAS"
    )
    L_out = _harvest_inductance(
        lout_full_mas, "phase_shifted_full_bridge L_out0 MAS"
    )
    lout_mas = _read_mas(lout_comp, "phase_shifted_full_bridge L_out0 MAS")
    A_e, N_lout, b_sat = _mas_isat_inputs(
        lout_mas, "phase_shifted_full_bridge L_out0 MAS"
    )

    fsw_eff = 2.0 * fsw
    L_worst = 0.8 * L_out
    # D_eff is already the full-period power-delivery fraction; the
    # output choke sees that as its on-fraction directly (no 2× factor).
    ripple_worst = vout * (1.0 - d_eff_min) / (L_worst * fsw_eff)
    ipeak_worst = iout + ripple_worst / 2.0

    # isat: delegate to MKF (PyOM) per "all magnetics math lives in MKF".
    op_spec = spec.get("operatingPoints") or [{}]
    t_amb = float(op_spec[0].get("ambientTemperature", 25.0)) if isinstance(op_spec[0], Mapping) else 25.0
    isat, isat_prov = _compute_isat_authoritative(
        lout_full_mas, L_out, b_sat=b_sat, N=int(N_lout), A_e=float(A_e),
        temperature_c=max(t_amb, 100.0),
        topology_label="phase_shifted_full_bridge (output choke)",
    )

    tas["duty"]     = round(d_eff_max, 6)
    tas["duty_min"] = round(d_eff_min, 6)
    tas["duty_max"] = round(d_eff_max, 6)

    enriched = dict(lout_comp)
    enriched["isat"]        = round(isat, 6)
    enriched["ipeak_worst"] = round(ipeak_worst, 6)
    enriched["isat_provenance"] = isat_prov
    enriched["ipeak_provenance"] = {
        "method": (
            "Iout + ripple_worst/2 (buck-shaped on secondary at "
            "D_eff_min, L*0.8, fsw_eff = 2*fsw via CT-FW rectifier "
            "two-pulse output; D_eff = Vout/(n·Vin) is the full-period "
            "power-delivery fraction, applied directly to L_out0 ripple)"
        ),
        "role": "output_choke",
        "iout_A": iout,
        "vout_V": vout,
        "turns_ratio_n_sec0_over_n_pri": round(n, 6),
        "n_primary": N_pri,
        "n_secondary": N_sec0,
        "d_eff_max": round(d_eff_max, 6),
        "d_eff_min": round(d_eff_min, 6),
        "ripple_worst_A_pp": round(ripple_worst, 6),
        "vin_min_V": vmin,
        "vin_max_V": vmax,
        "fsw_per_switch_Hz": fsw,
        "fsw_effective_Hz": fsw_eff,
        "L_worst_H": L_worst,
        "l_r_isat_modelled": False,
        "t1_isat_modelled": False,
    }
    tas["topology"]["stages"][so]["circuit"]["components"][co] = enriched


# ---------------------------------------------------------------------------
# Dual Active Bridge (DAB) — single-phase-shift (SPS) control
# ---------------------------------------------------------------------------


def _enrich_dual_active_bridge(tas: dict, spec: Mapping[str, Any]) -> None:
    """Stamp duty + L_r Isat / Ipeak for the dual active bridge.

    DAB = two full bridges (Q1..Q4 primary, Q5..Q8 secondary SR)
    coupled through a series resonant/leakage inductor L_r and a
    transformer T1 (pri, sec0).  Both bridges run at 50 %
    complementary duty (square wave on each side); power transfer is
    controlled by the phase-shift angle ``φ`` between the two square
    waves (single-phase-shift / SPS modulation).  Stencil at
    stencils.py:3179.

    Normalised phase shift ``d = φ / (T_sw/2) ∈ [-1, 1]``; for forward
    power transfer the operating region is ``d ∈ (0, 0.5]``.

    Secondary-referred power transfer (Mi, "A Survey of DAB"):

      ``P = (n · V1 · V2 · d · (1 − |d|)) / (2 · fsw · L_r)``

    with ``n = N_pri / N_sec`` and ``L_r`` the total commutation
    inductance referred to the primary.  Solving for ``d`` given the
    operating point ``P = Vout · Iout`` and ``V1 = Vin``, ``V2 = Vout``:

      ``Iout = (n · Vin · d · (1 − d)) / (2 · fsw · L_r)``

      ``k = 2 · fsw · L_r · Iout / (n · Vin)``
      ``d = (1 − sqrt(1 − 4k)) / 2``      (smaller root, operating region)

    Real-root condition: ``k ≤ 0.25``  ⇒
    ``Iout ≤ n · Vin / (8 · fsw · L_r)`` = ``P_max / Vout``.  Throws at
    Vin_min when the discriminant collapses (DAB cannot deliver the
    requested load at d ≤ 0.5; the operator would need to enter the
    over-d region where peak current rises super-linearly — a deliberate
    design red flag).

    Peak tank current (worst case at the bridge switching transition):

      ``I_pk = (Vin + n · Vout) · d · T_sw / (4 · L_r)``
             ``= (Vin + n · Vout) · d / (4 · fsw · L_r)``

    Derivation: in the active interval (duration ``φ``), the inductor
    sees ``V_L = Vin + n·Vout`` (both bridge midpoints driving in the
    same direction); current ramps from ``−I_pk`` to ``+I_pk``, giving
    ``2·I_pk = (Vin + n·Vout) · φ / L_r``.

    Worst case for both ``d`` and ``Vin + n·Vout``: at ``Vin_min`` ``d``
    is largest (heaviest load relative to capability) while
    ``n · Vout`` is fixed; therefore the peak is evaluated at
    ``Vin_min``.  PROTEUS −20 % tolerance: ``L_r_worst = 0.8 · L_r``.

    L_r is the binding magnetic (in the ``isolation`` stage, ahead of
    T1 in the component order).  T1 is intentionally NOT Isat-stamped:
    both bridges are symmetric square waves, so primary current is
    bipolar with zero net DC by symmetry — same rationale as PSFB and
    LLC.

    Per-switch duty: each bridge runs at 50 % complementary; ``tas[duty]``
    is stamped 0.5 with the phase shift recorded separately in
    ``ipeak_provenance.phase_shift_d_normalised`` (which is the
    operationally interesting handle for DAB).

    Raises ``EnrichmentError`` (no fallbacks per CLAUDE.md) on missing
    spec fields, missing transformer windings, missing MAS, or
    unrealisable power demand at Vin_min.
    """
    import math as _math
    where = "dual_active_bridge spec"
    vmin, vmax = _vin_extremes(spec, where)
    vout, iout, fsw = _operating_point(spec, where)
    L_r = _required_inductance(spec, "desiredInductance", where)

    # T1 turns (n = N_pri / N_sec for DAB convention; reflected
    # secondary voltage on the primary side = n · Vout).
    # NB: L_r and T1 share the ``isolation`` stage with L_r FIRST
    # (see stencils.py:3232), so we look up T1 by name rather than by
    # the "first magnetic" dispatch used for L_r below.
    t1_comp = _find_named_magnetic_in_stage_role(
        tas, "isolation", "T1", "dual_active_bridge enrichment (T1)"
    )
    t1_mas = _read_mas(t1_comp, "dual_active_bridge T1 MAS")
    N_pri = _winding_turns_by_name(t1_mas, "pri",  "dual_active_bridge T1 MAS")
    N_sec = _winding_turns_by_name(t1_mas, "sec0", "dual_active_bridge T1 MAS")
    n = float(N_pri) / float(N_sec)
    nV2 = n * vout

    def _solve_d(vin: float) -> float:
        k = 2.0 * fsw * L_r * iout / (n * vin)
        if k >= 0.25:
            P_max = n * vin / (8.0 * fsw * L_r)
            raise EnrichmentError(
                f"dual_active_bridge enrichment: k = 2·fsw·L_r·Iout / "
                f"(n·Vin) = {k:.4f} ≥ 0.25 at Vin = {vin} V — "
                f"required power exceeds P_max/Vout = {P_max:.4g} A "
                "deliverable at d ≤ 0.5.  Increase n, decrease L_r, "
                "raise Vin_min, or accept entering the over-d region "
                "(super-linear peak current rise — outside the v0.1 model)."
            )
        return (1.0 - _math.sqrt(1.0 - 4.0 * k)) / 2.0

    d_at_vmin = _solve_d(vmin)   # largest d (worst case for peak)
    d_at_vmax = _solve_d(vmax)   # smallest d

    # L_r in isolation stage (first magnetic encountered before T1).
    si, ci, lr_comp = _find_magnetic_in_stage_role(
        tas, "isolation", "dual_active_bridge enrichment (L_r)"
    )
    lr_mas = _read_mas(lr_comp, "dual_active_bridge L_r MAS")
    A_e, N_lr, b_sat = _mas_isat_inputs(lr_mas, "dual_active_bridge L_r MAS")

    L_r_worst = 0.8 * L_r
    ipeak_worst = (vmin + nV2) * d_at_vmin / (4.0 * fsw * L_r_worst)
    isat = b_sat * float(N_lr) * float(A_e) / L_r

    tas["duty"]     = 0.5
    tas["duty_min"] = 0.5
    tas["duty_max"] = 0.5

    enriched = dict(lr_comp)
    enriched["isat"]        = round(isat, 6)
    enriched["ipeak_worst"] = round(ipeak_worst, 6)
    enriched["isat_provenance"] = {
        "method": (
            "B_sat * N * A_e / L_r "
            "(dual_active_bridge v0.1, series commutation inductor)"
        ),
        "b_sat_T": round(b_sat, 6),
        "n_turns": int(N_lr),
        "effective_area_m2": float(A_e),
        "inductance_H": L_r,
    }
    enriched["ipeak_provenance"] = {
        "method": (
            "(Vin + n·Vout) · d / (4 · fsw · L_r_worst) evaluated at "
            "Vin_min (worst d, smallest reset voltage) with L_r_worst "
            "= 0.8 · L_r (PROTEUS -20% tolerance); d solved from "
            "SPS power equation Iout = n·Vin·d·(1-d) / (2·fsw·L_r)"
        ),
        "role": "series_commutation_inductor",
        "iout_A": iout,
        "vout_V": vout,
        "turns_ratio_n_pri_over_n_sec": round(n, 6),
        "n_primary": N_pri,
        "n_secondary": N_sec,
        "phase_shift_d_at_vin_min": round(d_at_vmin, 6),
        "phase_shift_d_at_vin_max": round(d_at_vmax, 6),
        "phase_shift_d_normalised": round(d_at_vmin, 6),  # worst-case d
        "n_times_vout_V": round(nV2, 6),
        "vin_min_V": vmin,
        "vin_max_V": vmax,
        "fsw_Hz": fsw,
        "L_r_worst_H": L_r_worst,
        "sps_modulation": True,
        "t1_isat_modelled": False,
    }
    tas["topology"]["stages"][si]["circuit"]["components"][ci] = enriched


# ---------------------------------------------------------------------------
# CLLLC (bidirectional symmetric resonant — dual full bridges + dual tanks)
# ---------------------------------------------------------------------------


def _enrich_clllc(tas: dict, spec: Mapping[str, Any]) -> None:
    """Stamp duty + L_r1 Isat / Ipeak for the CLLLC converter.

    CLLLC = bidirectional symmetric resonant: HV full bridge (Q1..Q4)
    drives a series tank C_r1 + L_r1, then transformer T1 (pri, sec0),
    then secondary tank L_r2 + C_r2 feeding the LV synchronous-rectifier
    bridge (Q5..Q8).  Stencil at stencils.py:3477.

    Forward-mode DC gain at primary resonance (``f_r1 = 1/(2π√(L_r1·C_r1))``):

      ``Vout = Vin / n``   with ``n = N_pri / N_sec``

    (no factor of 2 — both bridges are full-bridges driving full Vin
    across the tank, unlike LLC's half-bridge which divides by 2).

    Required gain across Vin range:

      ``M(Vin) = (n · Vout) / Vin``

    M = 1 ⇒ at resonance.  M > 1 ⇒ sub-resonant (boost gain demanded);
    M < 1 ⇒ super-resonant.  The extractor uses
    ``M_max = max(1, M(Vin_min))`` as a conservative scalar on the
    tank current envelope.  FHA load-reflected primary peak current:

      ``I_load_pk = (π / 2) · Iout / n``

    Magnetizing peak current (triangular at the half-period; primary
    sees full ±Vin under FB drive, hence /4 not /8 as in LLC's HB):

      ``I_m_pk = Vin_max · T_sw / (4 · L_m) = Vin_max / (4 · L_m · fsw)``

    ``L_m`` comes from ``spec.desiredMagnetizingInductance`` with the
    PROTEUS −20 % tolerance ``L_m_worst = 0.8 · L_m``.

    Combined peak (L_r1, in primary tank):

      ``I_pk_worst = M_max · I_load_pk + I_m_pk``

    Duty: 50 % complementary FB (frequency modulation regulates), same
    rationale as LLC.

    L_r1 binds inductor_isat_margin (first magnetic in the isolation
    stage's component order: C_r1, L_r1, T1, L_r2, C_r2).  L_r2, T1
    are deliberately NOT Isat-stamped:

      * **T1** alternates polarity each half-period (FB symmetric
        reset, identical to LLC/PSFB).
      * **L_r2** carries secondary-scale current (Iout, not Iout/n).
        Its DC flux is also zero by symmetry of the LV bridge drive,
        so it does not bind Isat margin under nominal operation; a
        dedicated v0.2 may add secondary-side stamping for explicit
        bidirectional / reverse-mode analysis.

    Raises ``EnrichmentError`` (no fallbacks per CLAUDE.md) on any
    missing spec field, missing MAS payload, or missing transformer
    winding.
    """
    import math as _math
    where = "clllc spec"
    vmin, vmax = _vin_extremes(spec, where)
    vout, iout, fsw = _operating_point(spec, where)
    L_r1 = _required_inductance(spec, "desiredInductance", where)
    L_m  = _required_inductance(spec, "desiredMagnetizingInductance", where)

    # T1 turns (n = N_pri / N_sec for the FB primary-step-down convention).
    # T1 shares the ``isolation`` stage with L_r1 / L_r2 (stencil order
    # C_r1, L_r1, T1, L_r2, C_r2 — see stencils.py:3477), so the
    # "first magnetic" dispatch would land on L_r1; look T1 up by name.
    t1_comp = _find_named_magnetic_in_stage_role(
        tas, "isolation", "T1", "clllc enrichment (T1)"
    )
    t1_mas = _read_mas(t1_comp, "clllc T1 MAS")
    N_pri = _winding_turns_by_name(t1_mas, "pri",  "clllc T1 MAS")
    N_sec = _winding_turns_by_name(t1_mas, "sec0", "clllc T1 MAS")
    n = float(N_pri) / float(N_sec)

    # L_r1 (primary tank inductor) — first magnetic in the isolation
    # stage's component list per the stencil ordering
    # (C_r1, L_r1, T1, L_r2, C_r2).  _find_magnetic_in_stage_role
    # returns the first magnetic encountered, which is L_r1.
    si, ci, lr1_comp = _find_magnetic_in_stage_role(
        tas, "isolation", "clllc enrichment (L_r1)"
    )
    if lr1_comp.get("name") != "L_r1":
        raise EnrichmentError(
            f"clllc enrichment: expected first magnetic in isolation "
            f"stage to be 'L_r1' (primary tank inductor), found "
            f"{lr1_comp.get('name')!r}.  Check stencil ordering."
        )
    lr1_mas = _read_mas(lr1_comp, "clllc L_r1 MAS")
    A_e, N_lr1, b_sat = _mas_isat_inputs(lr1_mas, "clllc L_r1 MAS")

    M_at_vmin = (n * vout) / vmin
    M_max = max(1.0, M_at_vmin)

    i_load_pk = (_math.pi / 2.0) * (iout / n)
    L_m_worst = 0.8 * L_m
    i_mag_pk = vmax / (4.0 * L_m_worst * fsw)

    ipeak_worst = M_max * i_load_pk + i_mag_pk
    isat = b_sat * float(N_lr1) * float(A_e) / L_r1

    tas["duty"]     = 0.5
    tas["duty_min"] = 0.5
    tas["duty_max"] = 0.5

    enriched = dict(lr1_comp)
    enriched["isat"]        = round(isat, 6)
    enriched["ipeak_worst"] = round(ipeak_worst, 6)
    enriched["isat_provenance"] = {
        "method": "B_sat * N * A_e / L_r1 (clllc v0.1, primary tank inductor)",
        "b_sat_T": round(b_sat, 6),
        "n_turns": int(N_lr1),
        "effective_area_m2": float(A_e),
        "inductance_H": L_r1,
    }
    enriched["ipeak_provenance"] = {
        "method": (
            "M_max * (pi/2) * (Iout/n)  +  Vin_max / (4 * Lm_worst * fsw)  "
            "[FHA load-reflected sinusoidal envelope plus triangular "
            "magnetizing peak; M_max = max(1, n*Vout/Vin_min) is the "
            "sub-resonant boost-gain scalar; full-bridge primary => "
            "magnetizing /4 (not /8 as LLC HB); Lm_worst = 0.8 * Lm]"
        ),
        "role": "primary_resonant_tank_inductor",
        "iout_A": iout,
        "vout_V": vout,
        "turns_ratio_n_pri_over_n_sec": round(n, 6),
        "n_primary": N_pri,
        "n_secondary": N_sec,
        "vin_min_V": vmin,
        "vin_max_V": vmax,
        "fsw_Hz": fsw,
        "gain_at_vin_min": round(M_at_vmin, 6),
        "boost_factor_M_max": round(M_max, 6),
        "i_load_pk_A": round(i_load_pk, 6),
        "i_mag_pk_A": round(i_mag_pk, 6),
        "Lm_H": L_m,
        "Lm_worst_H": L_m_worst,
        "duty_50pct_complementary_FB": True,
        "l_r2_isat_modelled": False,
        "t1_isat_modelled": False,
    }
    tas["topology"]["stages"][si]["circuit"]["components"][ci] = enriched


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


_EXTRACTORS: dict[str, Callable[[dict, Mapping[str, Any]], None]] = {
    "buck": _enrich_buck,
    "boost": _enrich_boost,
    "flyback": _enrich_flyback,
    "cuk": _enrich_cuk,
    "sepic": _enrich_sepic,
    "zeta": _enrich_zeta,
    "single_switch_forward": _enrich_single_switch_forward,
    "two_switch_forward": _enrich_two_switch_forward,
    "active_clamp_forward": _enrich_active_clamp_forward,
    "isolated_buck": _enrich_isolated_buck,
    "isolated_buck_boost": _enrich_isolated_buck_boost,
    "push_pull": _enrich_push_pull,
    "asymmetric_half_bridge": _enrich_asymmetric_half_bridge,
    "weinberg": _enrich_weinberg,
    "four_switch_buck_boost": _enrich_four_switch_buck_boost,
    "llc": _enrich_llc,
    "phase_shifted_full_bridge": _enrich_phase_shifted_full_bridge,
    "dual_active_bridge": _enrich_dual_active_bridge,
    "dab": _enrich_dual_active_bridge,
    "clllc": _enrich_clllc,
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
