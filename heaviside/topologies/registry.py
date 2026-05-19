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
    ),
    TopologyEntry(
        "cllc",
        "cllcResonant",
        ("cllc", "cllcResonantConverter"),
        None,
        "converter",
        "resonant",
    ),
    TopologyEntry(
        "clllc",
        "clllcResonant",
        ("clllc", "clllcResonantConverter"),
        "process_clllc",
        "converter",
        "resonant",
    ),
    TopologyEntry(
        "series_resonant",
        "seriesResonant",
        ("src", "seriesResonantConverter"),
        "process_src",
        "converter",
        "resonant",
    ),
    TopologyEntry(
        "dual_active_bridge",
        "dualActiveBridge",
        ("dab", "dualActiveBridgeConverter"),
        None,
        "converter",
        "resonant",
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


def get(name: str) -> TopologyEntry:
    """Look up a topology by canonical Python name (e.g. ``"buck"``).

    Raises ``KeyError`` (loudly, per CLAUDE.md "no fallbacks" rule) if the
    name is not in the registry.
    """
    if name not in _BY_NAME:
        raise KeyError(f"Unknown topology {name!r}. Known: {', '.join(sorted(_BY_NAME))}.")
    return _BY_NAME[name]


def names() -> tuple[str, ...]:
    """Canonical Python names of every registered topology (stable order)."""
    return tuple(t.name for t in TOPOLOGIES)


# Compile-time invariant: the registry must cover all 24 MKF converters + 3 magnetics.
assert len(CONVERTERS) == 24, f"Expected 24 converters, got {len(CONVERTERS)}"
assert len(MAGNETICS_ONLY) == 3, f"Expected 3 magnetic-only entries, got {len(MAGNETICS_ONLY)}"
