"""Canonical registry of the 24 MKF converter topologies + 3 magnetic-only components.

This is the **single source of truth** for what Heaviside dispatches into
``PyOpenMagnetics.process_converter()``.

Each entry records:

* ``name``        — Python module name under ``heaviside.topologies``
* ``mas_schema``  — base filename in ``MAS/schemas/inputs/topologies/`` (without ``.json``)
* ``pyom_names``  — list of strings to try with ``process_converter`` (first match wins);
                    captures camelCase / short-form variants observed in MKF 1.3.10+.
* ``per_topology_binding`` — name of the dedicated ``process_<topology>`` function in
                    PyOpenMagnetics if one exists, else ``None``. Probed in Phase 1.
* ``kind``        — ``"converter"`` or ``"magnetic"`` (CMC / DMC / current transformer).
* ``family``      — coarse grouping for docs / agent dispatch.

If you add a topology here without first adding (or verifying) the matching
binding in ``vendor/PyOpenMagnetics/``, the empirical probe (``scripts/probe_topologies.py``)
will mark it as ``UNBOUND`` and CI will record it in ``docs/probe-report.md``.
Heaviside ships regardless; the missing binding is upstream work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class TopologyEntry:
    name: str
    mas_schema: str
    pyom_names: tuple[str, ...]
    per_topology_binding: str | None
    kind: Literal["converter", "magnetic"]
    family: str
    # Mapping from TAS magnetic component name (as emitted by the
    # stencil) to its PyOM source. ``None`` means "main magnetic"
    # (the design returned by ``design_magnetics_from_converter``).
    # Any other string is the PyOM extras-role name (e.g.
    # ``"outputInductor"``, ``"seriesInductor"``) as emitted by
    # ``get_extra_components_inputs`` in ``designRequirements.name``.
    #
    # Empty dict ``{}`` means "no stencil bindings yet" — Heaviside
    # will refuse to auto-bind multi-magnetic outputs for this
    # topology and require the caller to supply an explicit mapping.
    magnetic_binding: dict[str, str | None] = field(default_factory=dict)
    # Mapping from TAS capacitor component name (as emitted by the
    # stencil) to its PyOM extras-cap role name (matches
    # ``inputs.designRequirements.name`` from
    # ``get_extra_components_inputs(kind="capacitor")``).
    #
    # Empty dict ``{}`` (the default) means "this topology does not use
    # the extras-capacitor attach path" — typical for non-isolated and
    # forward-class topologies whose only caps are bulk output / input
    # filter caps that the librarian sizes from operating-point
    # ripple, not from a PyOM-side CAS::Inputs envelope. Required for
    # resonant topologies (LLC, CLLC, CLLLC) whose stencils emit
    # ``Cr*`` resonant caps that MKF describes via
    # ``resonantCapacitor_primary`` / ``_secondary`` extras roles.
    capacitor_binding: dict[str, str] = field(default_factory=dict)


# Ordered by family for readability; iteration order is stable.
TOPOLOGIES: tuple[TopologyEntry, ...] = (
    # --- non-isolated DC/DC ---
    TopologyEntry("buck", "buck", ("buck",), "process_buck", "converter", "non_isolated",
                  magnetic_binding={"L1": None}),
    TopologyEntry("boost", "boost", ("boost",), "process_boost", "converter", "non_isolated",
                  magnetic_binding={"L1": None}),
    TopologyEntry("cuk", "cuk", ("cuk", "cukConverter"), "process_cuk", "converter", "non_isolated",
                  magnetic_binding={"L1": None, "L2": "outputInductor"}),
    TopologyEntry("sepic", "sepic", ("sepic", "sepicConverter"), "process_sepic", "converter", "non_isolated",
                  magnetic_binding={"L1": None, "L2": "outputInductor"}),
    TopologyEntry("zeta", "zeta", ("zeta", "zetaConverter"), "process_zeta", "converter", "non_isolated",
                  magnetic_binding={"L1": None, "L2": "outputInductor"}),
    TopologyEntry(
        "four_switch_buck_boost",
        "fourSwitchBuckBoost",
        ("four_switch_buck_boost", "fourSwitchBuckBoostConverter"),
        "process_four_switch_buck_boost",
        "converter",
        "non_isolated",
        magnetic_binding={"L1": None},
    ),
    # --- isolated single-switch ---
    TopologyEntry(
        "isolated_buck",
        "isolatedBuck",
        ("isolated_buck", "isolatedBuckConverter"),
        "process_isolated_buck",
        "converter",
        "isolated_single_switch",
        magnetic_binding={"T1": None},
    ),
    TopologyEntry(
        "isolated_buck_boost",
        "isolatedBuckBoost",
        ("isolated_buck_boost", "isolatedBuckBoostConverter"),
        "process_isolated_buck_boost",
        "converter",
        "isolated_single_switch",
        magnetic_binding={"T1": None},
    ),
    TopologyEntry(
        "flyback",
        "flyback",
        ("flyback", "flybackConverter"),
        "process_flyback",
        "converter",
        "isolated_single_switch",
        magnetic_binding={"T1": None},
    ),
    TopologyEntry(
        "single_switch_forward",
        "forward",
        ("single_switch_forward", "singleSwitchForwardConverter"),
        "process_single_switch_forward",
        "converter",
        "isolated_single_switch",
        magnetic_binding={"T1": None, "L_out0": "outputInductor"},
    ),
    TopologyEntry(
        "two_switch_forward",
        "forward",
        ("two_switch_forward", "twoSwitchForwardConverter"),
        "process_two_switch_forward",
        "converter",
        "isolated_two_switch",
        magnetic_binding={"T1": None, "L_out0": "outputInductor"},
    ),
    TopologyEntry(
        "active_clamp_forward",
        "forward",
        ("active_clamp_forward", "activeClampForwardConverter"),
        "process_active_clamp_forward",
        "converter",
        "isolated_two_switch",
        magnetic_binding={"T1": None, "L_out0": "outputInductor"},
    ),
    # --- isolated push-pull / bridge ---
    TopologyEntry(
        "push_pull",
        "pushPull",
        ("push_pull", "pushPullConverter"),
        "process_push_pull",
        "converter",
        "isolated_push_pull",
        magnetic_binding={"T1": None, "L_out0": "outputInductor"},
    ),
    TopologyEntry(
        "asymmetric_half_bridge",
        "asymmetricHalfBridge",
        ("asymmetric_half_bridge", "asymmetricHalfBridgeConverter"),
        "process_asymmetric_half_bridge",
        "converter",
        "isolated_bridge",
        # L_lk is part of the inverter (DC-blocking C_b + leakage L_lk);
        # MKF does NOT emit it as an extra-component, so no binding here.
        # Only T1 (main) + L_out0 are mapped to MKF outputs.
        magnetic_binding={
            "T1": None,
            "L_out0": "outputInductor",
        },
    ),
    TopologyEntry(
        "phase_shifted_full_bridge",
        "phaseShiftedFullBridge",
        ("phase_shifted_full_bridge", "psfb", "phaseShiftedFullBridgeConverter"),
        None,
        "converter",
        "isolated_bridge",
        magnetic_binding={
            "T1": None,
            "L_r": "seriesInductor",
            "L_out0": "outputInductor",
        },
    ),
    TopologyEntry(
        "phase_shifted_half_bridge",
        "phaseShiftedHalfBridge",
        ("phase_shifted_half_bridge", "pshb", "phaseShiftedHalfBridgeConverter"),
        None,
        "converter",
        "isolated_bridge",
    ),
    TopologyEntry(
        "weinberg",
        "weinberg",
        ("weinberg", "weinbergConverter"),
        "process_weinberg",
        "converter",
        "isolated_bridge",
        # L1 is the input coupled inductor (2 windings); T1 is the
        # main 4-winding push-pull transformer. Cout is an extras
        # capacitor (`outputCapacitor`) but the bridge doesn't attach
        # capacitor extras yet — that's tracked in BACKLOG item 3.
        magnetic_binding={
            "T1": None,
            "L1": "inputCoupledInductor",
        },
    ),
    # --- resonant ---
    TopologyEntry(
        "llc",
        "llcResonant",
        ("llc", "llcResonantConverter"),
        None,
        "converter",
        "resonant",
        magnetic_binding={"T1": None, "L_r": "seriesInductor"},
        # LLC's resonant cap C_r is one of the PyOM extras-cap roles
        # (kind="capacitor" / name="resonantCapacitor"). The bridge
        # routes the CAS::Inputs onto C_r's ``cas_inputs`` field; the
        # downstream librarian agent picks the MPN.
        capacitor_binding={"C_r": "resonantCapacitor"},
    ),
    TopologyEntry(
        "cllc",
        "cllcResonant",
        ("cllc", "cllcResonantConverter"),
        None,
        "converter",
        "resonant",
        # CLLC has a primary resonant tank (C_r1 + L_r1) and a secondary
        # tank (L_r2 + C_r2) flanking the main transformer T1. PyMKF
        # exposes only the two resonant caps as bindable extras; L_r1/L_r2
        # appear in the deck but are not extras-bound (same posture as
        # C_bus_LV in CLLLC — librarian sources them from spec alone).
        magnetic_binding={"T1": None},
        capacitor_binding={
            "C_r1": "Cr1_resonantCapacitor_primary",
            "C_r2": "Cr2_resonantCapacitor_secondary",
        },
    ),
    TopologyEntry(
        "clllc",
        "clllcResonant",
        ("clllc", "clllcResonantConverter"),
        "process_clllc",
        "converter",
        "resonant",
        # CLLLC has dual resonant tanks (one each side of T1) plus the
        # main transformer. Both Lr1 (HV) and Lr2 (LV) are PyOM extras-
        # magnetic roles; T1 is the "main magnetic" (value=None).
        magnetic_binding={
            "T1":   None,
            "L_r1": "Lr1_HV_seriesInductor",
            "L_r2": "Lr2_LV_seriesInductor",
        },
        # Both resonant caps are extras-cap roles. The bridge stamps
        # cas_inputs onto C_r1 / C_r2 so the librarian picks an MPN per
        # tank.
        capacitor_binding={
            "C_r1": "Cr1_HV_resonantCapacitor",
            "C_r2": "Cr2_LV_resonantCapacitor",
        },
    ),
    TopologyEntry(
        "series_resonant",
        "seriesResonant",
        ("src", "seriesResonantConverter"),
        "process_src",
        "converter",
        "resonant",
        # SRC mirrors the LLC binding shape: the main transformer T1
        # (value=None) plus the external series-resonant inductor L_r,
        # which MKF emits as a ``seriesInductor`` extras-magnetic role
        # (Src.cpp ::generate_extras, role name "seriesInductor"). The
        # resonant cap C_r is the ``resonantCapacitor`` extras-cap role
        # (RESONANT application); the bridge stamps its CAS::Inputs so the
        # librarian picks an MPN. (With a full-bridge diode rectifier SRC
        # emits no output inductor — those only appear for CURRENT_DOUBLER.)
        magnetic_binding={"T1": None, "L_r": "seriesInductor"},
        capacitor_binding={"C_r": "resonantCapacitor"},
    ),
    TopologyEntry(
        "dual_active_bridge",
        "dualActiveBridge",
        ("dab", "dualActiveBridgeConverter"),
        None,
        "converter",
        "resonant",
        # DAB has the series leakage/external inductor L_r and the main
        # transformer T1. Both bridges are real MOSFETs (no diode rect).
        magnetic_binding={
            "T1":  None,
            "L_r": "seriesInductor",
        },
    ),
    # --- AC/DC ---
    TopologyEntry(
        "power_factor_correction",
        "powerFactorCorrection",
        ("power_factor_correction", "pfc", "powerFactorCorrection"),
        None,
        "converter",
        "ac_dc",
    ),
    TopologyEntry(
        "vienna",
        "vienna",
        ("vienna", "viennaRectifierConverter"),
        "process_vienna",
        "converter",
        "ac_dc",
        # Vienna emits a single per-phase boost cell (PyMKF's "Phase-1 SPICE"
        # simplification). L1 is the per-phase boost inductor, treated as the
        # main magnetic. C_bus_DC is the shared DC bus output cap (no extras
        # binding — librarian sources from outputDcVoltage in spec).
        magnetic_binding={"L1": None},
    ),
    # --- magnetic-only components (not converters; used directly by EMC filter design) ---
    TopologyEntry(
        "common_mode_choke",
        "commonModeChoke",
        ("commonModeChoke",),
        None,
        "magnetic",
        "filter_magnetic",
    ),
    TopologyEntry(
        "differential_mode_choke",
        "differentialModeChoke",
        ("differentialModeChoke",),
        None,
        "magnetic",
        "filter_magnetic",
    ),
    TopologyEntry(
        "current_transformer",
        "currentTransformer",
        ("currentTransformer",),
        "process_current_transformer",
        "magnetic",
        "sense_magnetic",
    ),
)


CONVERTERS: tuple[TopologyEntry, ...] = tuple(t for t in TOPOLOGIES if t.kind == "converter")
MAGNETICS_ONLY: tuple[TopologyEntry, ...] = tuple(t for t in TOPOLOGIES if t.kind == "magnetic")

_BY_NAME: dict[str, TopologyEntry] = {t.name: t for t in TOPOLOGIES}

# Secondary lookup: PyOM / camelCase aliases (e.g. ``"dab"`` →
# ``dual_active_bridge``). The canonical Python name still wins if the same
# string appears in both dicts.
_BY_ALIAS: dict[str, TopologyEntry] = {}
for _t in TOPOLOGIES:
    for _alias in _t.pyom_names:
        if _alias in _BY_NAME:
            continue  # alias collides with a canonical name — canonical wins
        existing = _BY_ALIAS.get(_alias)
        if existing is not None and existing is not _t:
            raise RuntimeError(
                f"Topology alias {_alias!r} is claimed by both "
                f"{existing.name!r} and {_t.name!r}; aliases must be unique."
            )
        _BY_ALIAS[_alias] = _t
del _t


def get(name: str) -> TopologyEntry:
    """Look up a topology by canonical Python name or PyOM alias.

    Accepts the canonical Python name (e.g. ``"buck"``,
    ``"dual_active_bridge"``) as well as any of the PyOM-side aliases
    declared in :attr:`TopologyEntry.pyom_names` (e.g. ``"dab"``,
    ``"cukConverter"``).

    Raises ``KeyError`` (loudly, per CLAUDE.md "no fallbacks" rule) if
    the name is not in the registry.
    """
    if name in _BY_NAME:
        return _BY_NAME[name]
    if name in _BY_ALIAS:
        return _BY_ALIAS[name]
    raise KeyError(
        f"Unknown topology {name!r}. Known: {', '.join(sorted(_BY_NAME))}."
    )


def names() -> tuple[str, ...]:
    """Canonical Python names of every registered topology (stable order)."""
    return tuple(t.name for t in TOPOLOGIES)


# Compile-time invariant: the registry must cover all 24 MKF converters + 3 magnetics.
assert len(CONVERTERS) == 24, f"Expected 24 converters, got {len(CONVERTERS)}"
assert len(MAGNETICS_ONLY) == 3, f"Expected 3 magnetic-only entries, got {len(MAGNETICS_ONLY)}"
