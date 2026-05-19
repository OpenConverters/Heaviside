"""Per-topology *stencils* — map parsed SPICE elements onto TAS stages.

A stencil is a callable ``(SpiceDeck) -> TasTopology`` that knows the
canonical shape of one topology family. It does three jobs:

1. **Filter out simulation scaffolding** (Vin sources, current-sense
   ammeters, snubbers, load resistor, behavioural probe sources)
   that are part of the *test bench* MKF emits, not the converter
   under design.
2. **Rename SPICE refdeses to TAS-canonical refdeses**
   (``S1`` → ``Q1``, ``Cout`` → ``C_out``).
3. **Group survivors into TAS stages** with the right
   ``inputPort`` / ``outputPorts`` and synthesise the
   ``interStageCircuit`` external ports.

Stencils are intentionally hand-written per topology family. There are
only ~5 distinct patterns across all 24 MKF converters (non-isolated
PWM / isolated forward / push-pull / bridge / resonant), so the total
code stays bounded.
"""

from __future__ import annotations

from typing import Any, Callable

from heaviside.decomposer.spice_parser import SpiceDeck, SpiceElement

# A "TAS topology" dict matches MAS schemas/inputs/topologies/<topology>.json:
#   {"stages": [...], "interStageCircuit": [...]}
TasTopology = dict[str, Any]


class StencilError(RuntimeError):
    """Raised when a SPICE deck does not match the expected topology stencil.

    Always carries the topology name and a description of what was
    expected vs found — never silently substitutes.
    """


# -----------------------------------------------------------------------------
# Component descriptor helpers (TAS shape)
# -----------------------------------------------------------------------------


def _tas_component(name: str, data_url: str) -> dict[str, str]:
    return {"name": name, "data": data_url}


_DATA_URL = {
    "mosfet":     "TAS/data/mosfets.ndjson?placeholder={name}",
    "diode":      "TAS/data/diodes.ndjson?placeholder={name}",
    "magnetic":   "TAS/data/magnetics.ndjson?placeholder={name}",
    "capacitor":  "TAS/data/capacitors.ndjson?placeholder={name}",
    "resistor":   "TAS/data/resistors.ndjson?placeholder={name}",
    "controller": "TAS/data/controllers.ndjson?placeholder={name}",
}


def _component(category: str, name: str) -> dict[str, str]:
    return _tas_component(name, _DATA_URL[category].format(name=name))


# -----------------------------------------------------------------------------
# Generic testbench classifier
# -----------------------------------------------------------------------------
#
# MKF's spice decks sprinkle current-sense ammeters, snubber networks, DCR
# resistors, ESR resistors, behavioural probes, voltage sources and load
# resistors throughout. These are simulation scaffolding and must never
# appear in a TAS bill of materials.
#
# Rules (in order):
#
# 1. Any V (voltage source) or B (behavioural source) element is scaffolding.
#    MKF never models a real BOM voltage source — Vin is the source under
#    test, V*_sense are current-sense probes, Vpwm* are gate drives, and
#    all B elements are behavioural probes/buses (Bsw, Bvpri_diff, Bimag …).
# 2. Refdeses in the ``_TESTBENCH_EXACT`` set or matching a prefix in
#    ``_TESTBENCH_PREFIXES`` are dropped — covers parasitics (Rsnub_,
#    Csnub_, Rdcr_, R*_esr, Rsec for sec winding DCR, …), load resistors,
#    and a few topology-specific bleeders.
#
# Adding a new MKF parasitic? Extend the constants and the next probe
# round will surface any topologies that now drift past their stencil.

# Refdeses that MUST be dropped regardless of topology (testbench-only).
_TESTBENCH_EXACT: frozenset[str] = frozenset({
    "Rclamp",  # active-clamp bleeder (1MEG sim artefact; not real BOM)
    # LLC bridge body diodes: MKF emits DHI/DLO as explicit DIDEAL diodes
    # because the SW1 switch model has no built-in body diode. In a real
    # bridge the body diode is intrinsic to the MOSFET and is NOT a
    # separate BOM line. Drop them.
    "DHI", "DLO",
    # Phase-shifted full bridge: same story, one synthetic body diode
    # per switch (DA pairs with SA, DB with SB, DC with SC, DD with SD).
    "DA", "DB", "DC", "DD",
})

# Refdeses dropped if they match any of these case-sensitive prefixes.
# Note: V*/B* are handled by kind, not prefix — this list is for R/C/L/W
# parasitics that aren't covered by a single rule.
_TESTBENCH_PREFIXES: tuple[str, ...] = (
    "Rsn", "Csn",                # all snubber networks (Rsnub_, Csnub_,
                                 # Rsn1_o1, Csn1_o1, Rsn2_o1, …)
    "Rdcr_",                     # DCR parasitic resistors on inductors
    "Rco_esr", "Rcs_esr", "Rcc_esr", "Rc1_esr", "R_cb_esr", "R_co_esr",  # cap ESR parasitics
    "Resr_",                     # generic ESR (LLC etc.)
    "Rload", "R_load",           # load resistors (Rload, R_load_o1, …)
    "Rsec",                      # secondary winding DCR
    "Rlout", "R_lo_dcr",         # output choke DCR (two_switch_forward, AHB, …)
    "Rdc_supply_dummy", "Rbus_HV_dummy",
    "Rpri_ret", "Rsec_ret",      # bridge return resistors
    "Rct_",                      # PSFB center-tap return stubs (1µΩ to GND)
)


def _is_testbench(element: SpiceElement) -> bool:
    # V and B sources are always scaffolding in MKF's output.
    if element.kind in ("voltage_source", "behavioural_source"):
        return True
    if element.refdes in _TESTBENCH_EXACT:
        return True
    return any(element.refdes.startswith(p) for p in _TESTBENCH_PREFIXES)


# -----------------------------------------------------------------------------
# Buck stencil
# -----------------------------------------------------------------------------
#
# MKF buck deck layout (verified empirically against MKF f599370d):
#
#   * Buck Converter - Generated by OpenMagnetics
#   * DC Input
#   Vin vin_src 0 48
#   Vin_sense vin_src vin_dc 0
#   * PWM High-side Switch
#   Vpwm pwm_ctrl 0 PULSE(...)
#   .model SW1 SW VT=2.5 VH=0.5
#   S1 vin_dc sw pwm_ctrl 0 SW1
#   Rsnub_s1 vin_dc sw 100
#   Csnub_s1 vin_dc sw 1e-10
#   * Freewheeling Diode
#   .model DIDEAL D(...)
#   D1 0 sw DIDEAL
#   * Inductor with current sense
#   Vl_sense sw l_in 0
#   L1 l_in vout 22e-6
#   Bvpri_diff vpri_diff 0 V=V(l_in)-V(vout)
#   * Output Filter and Load
#   Cout vout 0 1e-4 IC=12
#   Rload vout 0 2.4
#
# Mapping to TAS:
#   switchingCell stage gets {Q1=S1, D1=D1, L1=L1, C_out=Cout}.
#   Snubber Rsnub_s1/Csnub_s1 are auxiliary; we currently DROP them
#   (TAS schema has no auxiliary-component slot in the buck pattern).
#   Vin, Vin_sense, Vpwm, Vl_sense, Bvpri_diff, Rload are testbench only.
#
# The control stage is synthetic — MKF doesn't emit a controller; we
# materialise one because the TAS schema requires it for any active
# converter.


# MKF emits exactly four "real" refdeses for the non-isolated single-switch
# PWM family (buck, boost, and the inverting cousins that share the same
# bill of materials): S1, D1, L1, Cout. Everything else in the deck is
# either testbench scaffolding (sources, probes, snubbers, load) or a
# control card — handled by the generic ``_is_testbench`` classifier.
_PWM_REAL_KINDS = {"S1": "switch", "D1": "diode", "L1": "inductor", "Cout": "capacitor"}


def _validate_pwm_quartet(deck: SpiceDeck, topology: str) -> None:
    """Buck/boost shortcut — delegates to the generic validator."""
    _validate_real_set(deck, topology, _PWM_REAL_KINDS)


def _validate_real_set(
    deck: SpiceDeck,
    topology: str,
    expected_kinds: dict[str, str],
    *,
    extra_testbench: frozenset[str] = frozenset(),
) -> None:
    """Assert deck contains exactly ``expected_kinds`` real components and
    that every other element is recognised testbench scaffolding.

    Fails loudly on:
      * missing expected refdes
      * expected refdes with wrong kind
      * stray refdes that is neither expected nor in the testbench classifier
        (signals the stencil drifted from the MKF spice generator).

    ``extra_testbench`` lets a stencil declare topology-local
    scaffolding refdeses that collide with names used as real
    components in other topologies (e.g. AHB body diodes ``D1``/``D2``
    which clash with the real freewheeling diode of buck/flyback).
    """
    for refdes, expected_kind in expected_kinds.items():
        try:
            el = deck.by_refdes(refdes)
        except KeyError as exc:
            raise StencilError(
                f"{topology}: expected {refdes} in deck — {exc}"
            ) from exc
        if el.kind != expected_kind:
            raise StencilError(
                f"{topology}: {refdes} must be a {expected_kind}, got {el.kind!r}"
            )

    real_set = set(expected_kinds)
    for el in deck.elements:
        if el.refdes in real_set:
            continue
        if el.refdes in extra_testbench:
            continue
        if _is_testbench(el):
            continue
        raise StencilError(
            f"{topology}: unexpected element {el.refdes!r} ({el.kind}) in deck — "
            f"stencil out of date with MKF spice generator"
        )


def _pwm_components() -> list[dict[str, str]]:
    return [
        _component("mosfet",    "Q1"),     # ← S1
        _component("diode",     "D1"),     # ← D1
        _component("magnetic",  "L1"),     # ← L1
        _component("capacitor", "C_out"),  # ← Cout
    ]


# -----------------------------------------------------------------------------
# Implicit-net helpers
# -----------------------------------------------------------------------------
#
# TAS stencils used to model only the "named" wires of a converter
# (Vin, Vout, switch_node, …) and leave SPICE node ``0`` (GND) and
# per-switch gate nets implicit. The TAS→SPICE writer is strict and
# refuses to invent nets, so every stencil now declares:
#
# * a single ``GND`` wire whose endpoints list every grounded pin in the
#   converter (capacitor returns, low-side switch sources, low-side
#   diode anodes, secondary-side rectifier returns, …);
# * one ``<Q>_gate`` wire per active switch, with a single endpoint on
#   that switch's ``G`` pin. The writer expands these into independent
#   PWM PULSE sources.
#
# Stencils call these helpers to keep the boilerplate small.


def _gnd_wire(*endpoints: tuple[str, str]) -> dict[str, Any]:
    """``GND`` interStage wire listing every pin tied to SPICE node ``0``."""
    return {
        "name": "GND",
        "kind": "wire",
        "endpoints": [{"component": c, "pin": p} for c, p in endpoints],
    }


def _gate_wires(*switches: str) -> list[dict[str, Any]]:
    """One ``<Q>_gate`` wire per active switch."""
    return [
        {
            "name": f"{q}_gate",
            "kind": "wire",
            "endpoints": [{"component": q, "pin": "G"}],
        }
        for q in switches
    ]


def _control_stage() -> dict[str, Any]:    return {
        "name": "controller",
        "role": "control",
        "circuit": {
            "components": [_component("controller", "U1")],
            "connections": [],
        },
        "senses": [{"wire": "Vout", "signal": "voltage"}],
        "drives": [{"component": "Q1", "signal": "gate"}],
    }


def buck(deck: SpiceDeck) -> TasTopology:
    """Decompose an MKF buck deck into the canonical TAS buck topology."""
    _validate_pwm_quartet(deck, "buck")

    switching_cell = {
        "name": "power_stage",
        "role": "switchingCell",
        "inputPort":  {"type": "dcBus", "wire": "Vin"},
        "outputPorts": [{"type": "dcOutput", "wire": "Vout"}],
        "circuit": {
            "components": _pwm_components(),
            "connections": [
                {
                    "name": "sw_node",
                    "kind": "wire",
                    "endpoints": [
                        {"component": "Q1", "pin": "S"},
                        {"component": "D1", "pin": "K"},
                        {"component": "L1", "pin": "1"},
                    ],
                },
            ],
        },
    }

    control = _control_stage()

    inter_stage = [
        {
            "name": "Vin",
            "kind": "externalPort",
            "direction": "input",
            "endpoints": [{"component": "Q1", "pin": "D"}],
        },
        {
            "name": "Vout",
            "kind": "externalPort",
            "direction": "output",
            "endpoints": [
                {"component": "L1",    "pin": "2"},
                {"component": "C_out", "pin": "1"},
            ],
        },
        _gnd_wire(("D1", "A"), ("C_out", "2")),
        *_gate_wires("Q1"),
    ]

    return {"stages": [switching_cell, control], "interStageCircuit": inter_stage}


# -----------------------------------------------------------------------------
# Boost stencil
# -----------------------------------------------------------------------------
#
# MKF boost deck layout (verified empirically):
#
#   Vin vin_dc 0 12
#   Vl_sense vin_dc l_in 0
#   L1 l_in sw 33e-6
#   Bvpri_diff vpri_diff 0 V=V(l_in)-V(sw)
#   Vpwm pwm_ctrl 0 PULSE(...)
#   S1 sw 0 pwm_ctrl 0 SW1            ; low-side switch
#   Rsnub_s1 sw 0 100
#   Csnub_s1 sw 0 1e-10
#   D1 sw vout DIDEAL                 ; output diode (A=sw, K=vout)
#   Cout vout 0 1e-4 IC=48
#   Rload vout 0
#
# Bill of materials is identical to buck (S1, D1, L1, Cout). Differences:
#   * Switch is low-side: Q1.S → ground, Q1.D → sw_node
#   * Diode anode is on sw_node, cathode is on Vout
#   * Inductor sits at the input: L1.1 → Vin (port), L1.2 → sw_node
#   * No Vin_sense in boost decks (only Vl_sense)
#
# So sw_node carries {L1.2, Q1.D, D1.A}, and Vout carries {D1.K, C_out.1}.


def boost(deck: SpiceDeck) -> TasTopology:
    """Decompose an MKF boost deck into the canonical TAS boost topology."""
    _validate_pwm_quartet(deck, "boost")

    switching_cell = {
        "name": "power_stage",
        "role": "switchingCell",
        "inputPort":  {"type": "dcBus", "wire": "Vin"},
        "outputPorts": [{"type": "dcOutput", "wire": "Vout"}],
        "circuit": {
            "components": _pwm_components(),
            "connections": [
                {
                    "name": "sw_node",
                    "kind": "wire",
                    "endpoints": [
                        {"component": "L1", "pin": "2"},
                        {"component": "Q1", "pin": "D"},
                        {"component": "D1", "pin": "A"},
                    ],
                },
            ],
        },
    }

    control = _control_stage()

    inter_stage = [
        {
            "name": "Vin",
            "kind": "externalPort",
            "direction": "input",
            "endpoints": [{"component": "L1", "pin": "1"}],
        },
        {
            "name": "Vout",
            "kind": "externalPort",
            "direction": "output",
            "endpoints": [
                {"component": "D1",    "pin": "K"},
                {"component": "C_out", "pin": "1"},
            ],
        },
        _gnd_wire(("Q1", "S"), ("C_out", "2")),
        *_gate_wires("Q1"),
    ]

    return {"stages": [switching_cell, control], "interStageCircuit": inter_stage}


# -----------------------------------------------------------------------------
# Cuk stencil
# -----------------------------------------------------------------------------
#
# MKF cuk deck layout (verified empirically against /tmp/all_decks.json):
#
#   Vin vin_dc 0 48
#   Vin_sense vin_dc l1_in 0
#   L1 l1_in l1_dcr_mid 1e-3
#   Rdcr_l1 l1_dcr_mid node_A 0.05
#   Vl1_sense node_A node_A_int 0
#   Vpwm pwm_ctrl 0 PULSE(...)
#   S1 node_A_int 0 pwm_ctrl 0 SW1            ; low-side switch
#   Vc1_sense node_A_int node_C 0
#   C1 node_C node_C_esr 1.77e-6              ; flying coupling cap
#   Rc1_esr node_C_esr node_B 0.005
#   D1 node_B d_cath DIDEAL                    ; D1.A=node_B, D1.K=0
#   L2 node_B l2_dcr_mid 3.13e-5
#   Cout vout_load_node co_esr 7.8e-6
#   (… snubbers, ESRs, DCRs, probes, load — all testbench scaffolding)
#
# Real BOM = {L1, L2, Q1=S1, D1, C_flying=C1, C_out=Cout}.
# Topology connections:
#   * node_A: {L1.2, Q1.D, C_flying.1}
#   * node_B: {C_flying.2, D1.A, L2.1}
#   * Vin port: L1.1
#   * Vout port: L2.2 + C_out.1


_CUK_REAL_KINDS = {
    "S1": "switch",
    "D1": "diode",
    "L1": "inductor",
    "L2": "inductor",
    "C1": "capacitor",
    "Cout": "capacitor",
}


def cuk(deck: SpiceDeck) -> TasTopology:
    """Decompose an MKF cuk deck into the canonical TAS cuk topology."""
    _validate_real_set(deck, "cuk", _CUK_REAL_KINDS)

    switching_cell = {
        "name": "power_stage",
        "role": "switchingCell",
        "inputPort":  {"type": "dcBus", "wire": "Vin"},
        "outputPorts": [{"type": "dcOutput", "wire": "Vout"}],
        "circuit": {
            "components": [
                _component("mosfet",    "Q1"),         # ← S1
                _component("diode",     "D1"),         # ← D1
                _component("magnetic",  "L1"),         # ← L1 (input)
                _component("magnetic",  "L2"),         # ← L2 (output)
                _component("capacitor", "C_flying"),   # ← C1
                _component("capacitor", "C_out"),      # ← Cout
            ],
            "connections": [
                {
                    "name": "node_A",
                    "kind": "wire",
                    "endpoints": [
                        {"component": "L1",       "pin": "2"},
                        {"component": "Q1",       "pin": "D"},
                        {"component": "C_flying", "pin": "1"},
                    ],
                },
                {
                    "name": "node_B",
                    "kind": "wire",
                    "endpoints": [
                        {"component": "C_flying", "pin": "2"},
                        {"component": "D1",       "pin": "A"},
                        {"component": "L2",       "pin": "1"},
                    ],
                },
            ],
        },
    }

    control = _control_stage()

    inter_stage = [
        {
            "name": "Vin",
            "kind": "externalPort",
            "direction": "input",
            "endpoints": [{"component": "L1", "pin": "1"}],
        },
        {
            "name": "Vout",
            "kind": "externalPort",
            "direction": "output",
            "endpoints": [
                {"component": "L2",    "pin": "2"},
                {"component": "C_out", "pin": "1"},
            ],
        },
        _gnd_wire(("Q1", "S"), ("D1", "K"), ("C_out", "2")),
        *_gate_wires("Q1"),
    ]
    return {"stages": [switching_cell, control], "interStageCircuit": inter_stage}


# -----------------------------------------------------------------------------
# SEPIC stencil
# -----------------------------------------------------------------------------
#
# MKF SEPIC deck (uncoupled V1): same six real components as cuk but the
# output inductor sits with one pin on GND (TAS convention: ground pins
# are implicit and not enumerated in `connections`).
#
#   L1 l1_in … node_A           ; input inductor
#   S1 node_A 0 …               ; low-side switch (drain=node_A)
#   Cs  node_A side → node_B    ; coupling cap (named "Cs" in SEPIC)
#   L2 0 l2_top → node_B        ; output inductor (L2.1=GND, L2.2=node_B)
#   D1 node_B → vout            ; output diode (A=node_B, K=Vout)
#   Cout vout → 0               ; output cap
#
# Real BOM = {L1, L2, Q1=S1, D1, C_flying=Cs, C_out=Cout}.
# Connections:
#   * node_A: {L1.2, Q1.D, C_flying.1}
#   * node_B: {C_flying.2, L2.2, D1.A}
#   * Vin port: L1.1
#   * Vout port: D1.K + C_out.1


_SEPIC_REAL_KINDS = {
    "S1": "switch",
    "D1": "diode",
    "L1": "inductor",
    "L2": "inductor",
    "Cs": "capacitor",
    "Cout": "capacitor",
}


def sepic(deck: SpiceDeck) -> TasTopology:
    """Decompose an MKF SEPIC deck into the canonical TAS SEPIC topology."""
    _validate_real_set(deck, "sepic", _SEPIC_REAL_KINDS)

    switching_cell = {
        "name": "power_stage",
        "role": "switchingCell",
        "inputPort":  {"type": "dcBus", "wire": "Vin"},
        "outputPorts": [{"type": "dcOutput", "wire": "Vout"}],
        "circuit": {
            "components": [
                _component("mosfet",    "Q1"),
                _component("diode",     "D1"),
                _component("magnetic",  "L1"),
                _component("magnetic",  "L2"),
                _component("capacitor", "C_flying"),   # ← Cs
                _component("capacitor", "C_out"),
            ],
            "connections": [
                {
                    "name": "node_A",
                    "kind": "wire",
                    "endpoints": [
                        {"component": "L1",       "pin": "2"},
                        {"component": "Q1",       "pin": "D"},
                        {"component": "C_flying", "pin": "1"},
                    ],
                },
                {
                    "name": "node_B",
                    "kind": "wire",
                    "endpoints": [
                        {"component": "C_flying", "pin": "2"},
                        {"component": "L2",       "pin": "2"},
                        {"component": "D1",       "pin": "A"},
                    ],
                },
            ],
        },
    }

    control = _control_stage()

    inter_stage = [
        {
            "name": "Vin",
            "kind": "externalPort",
            "direction": "input",
            "endpoints": [{"component": "L1", "pin": "1"}],
        },
        {
            "name": "Vout",
            "kind": "externalPort",
            "direction": "output",
            "endpoints": [
                {"component": "D1",    "pin": "K"},
                {"component": "C_out", "pin": "1"},
            ],
        },
        _gnd_wire(("Q1", "S"), ("L2", "1"), ("C_out", "2")),
        *_gate_wires("Q1"),
    ]

    return {"stages": [switching_cell, control], "interStageCircuit": inter_stage}


# -----------------------------------------------------------------------------
# Zeta stencil
# -----------------------------------------------------------------------------
#
# MKF Zeta deck (uncoupled V1): high-side switch + flying coupling cap (Cc).
#
#   S1 sw_top node_SW …          ; high-side switch (D=Vin, S=node_SW)
#   L1 l1_top → 0                ; magnetising inductor (L1.1=node_SW, L1.2=GND)
#   Cc cc_left cc_right          ; coupling cap (plate1=node_SW, plate2=node_X)
#   D1 0 → node_X                ; catch diode (A=GND, K=node_X)
#   L2 l2_top → vout             ; output inductor (L2.1=node_X, L2.2=Vout)
#   Cout vout → 0                ; output cap
#
# Real BOM = {L1, L2, Q1=S1, D1, C_flying=Cc, C_out=Cout}.
# Connections (ground pins implicit):
#   * node_SW: {Q1.S, L1.1, C_flying.1}
#   * node_X:  {C_flying.2, D1.K, L2.1}
#   * Vin port: Q1.D
#   * Vout port: L2.2 + C_out.1


_ZETA_REAL_KINDS = {
    "S1": "switch",
    "D1": "diode",
    "L1": "inductor",
    "L2": "inductor",
    "Cc": "capacitor",
    "Cout": "capacitor",
}


def zeta(deck: SpiceDeck) -> TasTopology:
    """Decompose an MKF Zeta deck into the canonical TAS Zeta topology."""
    _validate_real_set(deck, "zeta", _ZETA_REAL_KINDS)

    switching_cell = {
        "name": "power_stage",
        "role": "switchingCell",
        "inputPort":  {"type": "dcBus", "wire": "Vin"},
        "outputPorts": [{"type": "dcOutput", "wire": "Vout"}],
        "circuit": {
            "components": [
                _component("mosfet",    "Q1"),
                _component("diode",     "D1"),
                _component("magnetic",  "L1"),
                _component("magnetic",  "L2"),
                _component("capacitor", "C_flying"),   # ← Cc
                _component("capacitor", "C_out"),
            ],
            "connections": [
                {
                    "name": "node_SW",
                    "kind": "wire",
                    "endpoints": [
                        {"component": "Q1",       "pin": "S"},
                        {"component": "L1",       "pin": "1"},
                        {"component": "C_flying", "pin": "1"},
                    ],
                },
                {
                    "name": "node_X",
                    "kind": "wire",
                    "endpoints": [
                        {"component": "C_flying", "pin": "2"},
                        {"component": "D1",       "pin": "K"},
                        {"component": "L2",       "pin": "1"},
                    ],
                },
            ],
        },
    }

    control = _control_stage()

    inter_stage = [
        {
            "name": "Vin",
            "kind": "externalPort",
            "direction": "input",
            "endpoints": [{"component": "Q1", "pin": "D"}],
        },
        {
            "name": "Vout",
            "kind": "externalPort",
            "direction": "output",
            "endpoints": [
                {"component": "L2",    "pin": "2"},
                {"component": "C_out", "pin": "1"},
            ],
        },
        _gnd_wire(("D1", "A"), ("L1", "2"), ("C_out", "2")),
        *_gate_wires("Q1"),
    ]

    return {"stages": [switching_cell, control], "interStageCircuit": inter_stage}


# -----------------------------------------------------------------------------
# Four-switch buck-boost stencil
# -----------------------------------------------------------------------------
#
# MKF 4SBB deck: a buck half-bridge cascaded with a boost half-bridge via a
# single inductor. First topology in the family to emit a real Cin.
#
#   S_Q1 vin_p sw1 …             ; buck high-side (D=Vin, S=sw1)
#   S_Q2 sw1   0  …              ; buck low-side  (D=sw1, S=GND)
#   L1   l_in l_out              ; inductor between sw1 and sw2 (via senses)
#   S_Q3 sw2   vout …            ; boost high-side (D=sw2, S=Vout)
#   S_Q4 sw2   0  …              ; boost low-side  (D=sw2, S=GND)
#   Cin  vin_p 0                 ; input cap (NEW: appears in deck)
#   Cout vout  0                 ; output cap
#
# Real BOM = {Q1=S_Q1, Q2=S_Q2, Q3=S_Q3, Q4=S_Q4, L1, C_in=Cin, C_out=Cout}.
# Connections (ground pins implicit):
#   * sw1: {Q1.S, Q2.D, L1.1}
#   * sw2: {L1.2, Q3.D, Q4.D}
#   * Vin port: Q1.D + C_in.1
#   * Vout port: Q3.S + C_out.1


_4SBB_REAL_KINDS = {
    "S_Q1": "switch",
    "S_Q2": "switch",
    "S_Q3": "switch",
    "S_Q4": "switch",
    "L1":   "inductor",
    "Cin":  "capacitor",
    "Cout": "capacitor",
}


def _4sbb_control_stage() -> dict[str, Any]:
    """4SBB controller drives all four switches; override the single-switch
    default in ``_control_stage``."""
    return {
        "name": "controller",
        "role": "control",
        "circuit": {
            "components": [_component("controller", "U1")],
            "connections": [],
        },
        "senses": [{"wire": "Vout", "signal": "voltage"}],
        "drives": [
            {"component": "Q1", "signal": "gate"},
            {"component": "Q2", "signal": "gate"},
            {"component": "Q3", "signal": "gate"},
            {"component": "Q4", "signal": "gate"},
        ],
    }


def four_switch_buck_boost(deck: SpiceDeck) -> TasTopology:
    """Decompose an MKF four-switch buck-boost deck into TAS."""
    _validate_real_set(deck, "four_switch_buck_boost", _4SBB_REAL_KINDS)

    switching_cell = {
        "name": "power_stage",
        "role": "switchingCell",
        "inputPort":  {"type": "dcBus", "wire": "Vin"},
        "outputPorts": [{"type": "dcOutput", "wire": "Vout"}],
        "circuit": {
            "components": [
                _component("mosfet",    "Q1"),    # ← S_Q1 (buck HS)
                _component("mosfet",    "Q2"),    # ← S_Q2 (buck LS)
                _component("mosfet",    "Q3"),    # ← S_Q3 (boost HS)
                _component("mosfet",    "Q4"),    # ← S_Q4 (boost LS)
                _component("magnetic",  "L1"),
                _component("capacitor", "C_in"),  # ← Cin (NEW)
                _component("capacitor", "C_out"),
            ],
            "connections": [
                {
                    "name": "sw1",
                    "kind": "wire",
                    "endpoints": [
                        {"component": "Q1", "pin": "S"},
                        {"component": "Q2", "pin": "D"},
                        {"component": "L1", "pin": "1"},
                    ],
                },
                {
                    "name": "sw2",
                    "kind": "wire",
                    "endpoints": [
                        {"component": "L1", "pin": "2"},
                        {"component": "Q3", "pin": "D"},
                        {"component": "Q4", "pin": "D"},
                    ],
                },
            ],
        },
    }

    control = _4sbb_control_stage()

    inter_stage = [
        {
            "name": "Vin",
            "kind": "externalPort",
            "direction": "input",
            "endpoints": [
                {"component": "Q1",   "pin": "D"},
                {"component": "C_in", "pin": "1"},
            ],
        },
        {
            "name": "Vout",
            "kind": "externalPort",
            "direction": "output",
            "endpoints": [
                {"component": "Q3",    "pin": "S"},
                {"component": "C_out", "pin": "1"},
            ],
        },
        _gnd_wire(
            ("C_in",  "2"),
            ("Q2",    "S"),
            ("Q4",    "S"),
            ("C_out", "2"),
        ),
        *_gate_wires("Q1", "Q2", "Q3", "Q4"),
    ]

    return {"stages": [switching_cell, control], "interStageCircuit": inter_stage}


# =============================================================================
# Isolated single-switch transformer family
# =============================================================================
#
# Common shape: primary side has one or more switches, a transformer (T1)
# couples to N secondary windings, each secondary feeds an output rectifier
# + filter stage. Heaviside TAS shape for this family:
#
#   stages = [
#     {role: "switchingCell",    components: [Q1, (Q2, S_clamp, Cclamp, …)]},
#     {role: "isolation",        components: [T1]},
#     {role: "outputRectifier", … one per secondary winding …},
#     {role: "control"},
#   ]
#
# T1 is a synthetic component that replaces the (Lpri, Lsec0…, K…) triple
# from the SPICE deck. Its pins follow the convention
# ``pri.1 / pri.2 / sec0.1 / sec0.2 / sec1.1 / sec1.2 / demag.1 / demag.2``.
#
# interStageCircuit holds **both** external-port boundaries (Vin / Vout_n)
# **and** internal stage-bridging wires (e.g. ``switch_node`` between
# switchingCell.Q1.S and isolation.T1.pri.1).


_INDUCTOR_REFDES = ("Lpri", "Lsec0", "Lsec1", "Lsec2", "Ldemag", "Lout0", "Lout1")


def _t1_component(windings: tuple[str, ...]) -> dict[str, Any]:
    """Return the TAS dict for a multi-winding transformer T1.

    ``windings`` enumerates which TAS pins T1 exposes — e.g.
    ``("pri", "sec0")`` for a 2-winding flyback, ``("pri", "sec0", "demag")``
    for a single-switch forward.
    """
    return {
        "name": "T1",
        "data": _DATA_URL["magnetic"].format(name="T1"),
        "pins": [f"{w}.{i}" for w in windings for i in (1, 2)],
    }


def _isolation_stage(
    windings: tuple[str, ...],
    *,
    input_wire: str,
    output_wires: tuple[str, ...],
) -> dict[str, Any]:
    """Build the standard one-component (T1) isolation stage."""
    if len(output_wires) != len(windings) - 1:
        raise StencilError(
            f"isolation stage: {len(windings)} windings but "
            f"{len(output_wires)} output wires (expected {len(windings) - 1})"
        )
    return {
        "name": "isolation",
        "role": "isolation",
        "inputPort":  {"type": "switchNode", "wire": input_wire},
        "outputPorts": [
            {"type": "winding", "wire": w} for w in output_wires
        ],
        "circuit": {
            "components": [_t1_component(windings)],
            "connections": [],
        },
    }


def _isolated_control_stage(
    driven_components: tuple[str, ...],
    *,
    sense_wire: str = "Vout0",
) -> dict[str, Any]:
    """Controller for isolated family — drives one or more primary-side
    switches (Q1, optional Q2 synchronous, optional Q_clamp).

    ``sense_wire`` selects which output the regulation loop closes around;
    defaults to ``Vout0`` (single-output topologies). Flybuck-style
    converters close around the primary output instead (``Vout_pri``).
    """
    return {
        "name": "controller",
        "role": "control",
        "circuit": {
            "components": [_component("controller", "U1")],
            "connections": [],
        },
        "senses": [{"wire": sense_wire, "signal": "voltage"}],
        "drives": [{"component": q, "signal": "gate"} for q in driven_components],
    }


# -----------------------------------------------------------------------------
# Flyback stencil (simplest isolated case: 1 switch + 2-winding T1 + 1 output)
# -----------------------------------------------------------------------------
#
# MKF deck:
#   S1 vin_dc pri_p …            ; high-side switch (D=Vin, S=pri_p=switch_node)
#   Vpri_sense pri_p pri_in 0    ; testbench probe
#   Lpri pri_in 0 1e-3           ; T1.pri.1=pri_in (=switch_node), T1.pri.2=GND
#   Lsec0 0 sec0_in 2.5e-4       ; T1.sec0.1=GND, T1.sec0.2=sec0_in=sec0_node
#   K0 Lpri Lsec0 1              ; coupling — folded into T1
#   Dout0 sec0_in sec0_p …       ; D_out0.A=sec0_node, D_out0.K=vout0
#   Vsec_sense0 sec0_p vout0 0   ; testbench probe
#   Cout0 vout0 0                ; C_out0.1=Vout0, C_out0.2=GND
#
# Real BOM = {Q1=S1, T1 (Lpri+Lsec0+K0), D_out0=Dout0, C_out0=Cout0}.


_FLYBACK_REAL_KINDS = {
    "S1":     "switch",
    "Lpri":   "inductor",     # → T1.pri
    "Lsec0":  "inductor",     # → T1.sec0
    "K0":     "coupling",     # → T1 coupling
    "Dout0":  "diode",
    "Cout0":  "capacitor",
}


def flyback(deck: SpiceDeck) -> TasTopology:
    """Decompose an MKF flyback deck into the canonical TAS flyback topology."""
    _validate_real_set(deck, "flyback", _FLYBACK_REAL_KINDS)

    switching_cell = {
        "name": "primary_switch",
        "role": "switchingCell",
        "inputPort":  {"type": "dcBus",     "wire": "Vin"},
        "outputPorts": [{"type": "switchNode", "wire": "switch_node"}],
        "circuit": {
            "components": [_component("mosfet", "Q1")],   # ← S1
            "connections": [],
        },
    }

    isolation = _isolation_stage(
        ("pri", "sec0"),
        input_wire="switch_node",
        output_wires=("sec0_node",),
    )

    output_rectifier_0 = {
        "name": "output_0",
        "role": "outputRectifier",
        "inputPort":  {"type": "winding",  "wire": "sec0_node"},
        "outputPorts": [{"type": "dcOutput", "wire": "Vout0"}],
        "circuit": {
            "components": [
                _component("diode",     "D_out0"),    # ← Dout0
                _component("capacitor", "C_out0"),    # ← Cout0
            ],
            "connections": [],
        },
    }

    control = _isolated_control_stage(("Q1",))

    inter_stage = [
        {
            "name": "Vin",
            "kind": "externalPort",
            "direction": "input",
            "endpoints": [{"component": "Q1", "pin": "D"}],
        },
        {
            "name": "switch_node",
            "kind": "wire",
            "endpoints": [
                {"component": "Q1", "pin": "S"},
                {"component": "T1", "pin": "pri.1"},
            ],
        },
        {
            "name": "sec0_node",
            "kind": "wire",
            "endpoints": [
                {"component": "T1",     "pin": "sec0.2"},
                {"component": "D_out0", "pin": "A"},
            ],
        },
        {
            "name": "Vout0",
            "kind": "externalPort",
            "direction": "output",
            "endpoints": [
                {"component": "D_out0", "pin": "K"},
                {"component": "C_out0", "pin": "1"},
            ],
        },
        _gnd_wire(
            ("T1",     "pri.2"),
            ("T1",     "sec0.1"),
            ("C_out0", "2"),
        ),
        *_gate_wires("Q1"),
    ]

    return {
        "stages": [switching_cell, isolation, output_rectifier_0, control],
        "interStageCircuit": inter_stage,
    }


# -----------------------------------------------------------------------------
# Single-switch forward stencil
# -----------------------------------------------------------------------------
#
# MKF emits the *primary excitation only* for this topology (0 secondaries
# in the schema). The deck describes the switch + transformer + demag-reset
# diode that returns magnetising energy to Vin — there is no output stage
# in the deck. The TAS we emit therefore has switchingCell + isolation +
# control, but no outputRectifier. Callers that want a complete converter
# must augment the BOM downstream; the decomposer stays faithful to MKF.
#
# Deck:
#   S1 vin_dc pri_p …             ; Q1.D=Vin, Q1.S=switch_node
#   Lpri pri_in 0                  ; T1.pri.1=switch_node, T1.pri.2=GND
#   Ldemag 0 demag_in              ; T1.demag.1=GND, T1.demag.2=demag_node
#   Kpri_demag Lpri Ldemag 0.9999  ; coupling
#   Ddemag demag_sense vin_dc      ; D_demag.A=demag_node, D_demag.K=Vin
#
# Real BOM = {Q1=S1, T1 (Lpri+Ldemag+Kpri_demag), D_demag=Ddemag}.


_SSF_REAL_KINDS = {
    "S1":          "switch",
    "Lpri":        "inductor",
    "Ldemag":      "inductor",
    "Kpri_demag":  "coupling",
    "Ddemag":      "diode",
}


def single_switch_forward(deck: SpiceDeck) -> TasTopology:
    """Decompose an MKF single-switch forward deck into TAS, with synthetic
    output-stage augmentation.

    MKF's single-switch forward emission contains only the primary
    excitation half (S1 + Lpri + Ldemag + Kpri_demag + Ddemag): no
    secondary winding, no forward / freewheel rectifier, no output choke,
    no output cap, and therefore no Vout port. A converter without an
    output is not simulatable end-to-end and cannot round-trip through
    the SPICE↔TAS pipeline.

    To make the topology useful, the stencil augments the MKF skeleton
    with the canonical single-switch-forward output stage:

      * A third winding ``sec0`` added to T1 (so T1 becomes 3-winding:
        pri + demag + sec0, all mutually coupled).
      * An ``output_0`` outputRectifier stage containing the forward
        rectifier diode ``D_fwd``, freewheel diode ``D_fw``, output
        choke ``L_out0``, and output cap ``C_out0``.
      * A ``Vout0`` external port across the LC filter.

    The injected components are not present in the MKF deck — they
    extend the validated primary skeleton into a complete converter
    topology. ``_validate_real_set`` continues to check only what MKF
    actually emits.
    """
    _validate_real_set(deck, "single_switch_forward", _SSF_REAL_KINDS)

    switching_cell = {
        "name": "primary_switch",
        "role": "switchingCell",
        "inputPort":  {"type": "dcBus",     "wire": "Vin"},
        "outputPorts": [{"type": "switchNode", "wire": "switch_node"}],
        "circuit": {
            "components": [
                _component("mosfet", "Q1"),       # ← S1
                _component("diode",  "D_demag"),  # ← Ddemag (reset path to Vin)
            ],
            "connections": [],
        },
    }

    # 3-winding T1 (pri+demag from MKF, sec0 injected for output stage).
    isolation = _isolation_stage(
        ("pri", "demag", "sec0"),
        input_wire="switch_node",
        output_wires=("demag_node", "sec0_node"),
    )

    # Injected output stage — identical pattern to active_clamp_forward
    # and two_switch_forward (forward diode + freewheel diode + LC filter).
    output_rectifier_0 = {
        "name": "output_0",
        "role": "outputRectifier",
        "inputPort":  {"type": "winding",  "wire": "sec0_node"},
        "outputPorts": [{"type": "dcOutput", "wire": "Vout0"}],
        "circuit": {
            "components": [
                _component("diode",     "D_fwd"),    # forward rectifier
                _component("diode",     "D_fw"),     # freewheel
                _component("magnetic",  "L_out0"),   # output choke
                _component("capacitor", "C_out0"),
            ],
            "connections": [
                {
                    "name": "sec0_rect_node",
                    "kind": "wire",
                    "endpoints": [
                        {"component": "D_fwd",  "pin": "K"},
                        {"component": "D_fw",   "pin": "K"},
                        {"component": "L_out0", "pin": "1"},
                    ],
                },
            ],
        },
    }

    control = _isolated_control_stage(("Q1",))

    inter_stage = [
        {
            "name": "Vin",
            "kind": "externalPort",
            "direction": "input",
            # Vin connects to both Q1 (input) and the demag diode cathode
            # (reset energy returns to Vin).
            "endpoints": [
                {"component": "Q1",      "pin": "D"},
                {"component": "D_demag", "pin": "K"},
            ],
        },
        {
            "name": "switch_node",
            "kind": "wire",
            "endpoints": [
                {"component": "Q1", "pin": "S"},
                {"component": "T1", "pin": "pri.1"},
            ],
        },
        {
            "name": "demag_node",
            "kind": "wire",
            "endpoints": [
                {"component": "T1",      "pin": "demag.2"},
                {"component": "D_demag", "pin": "A"},
            ],
        },
        {
            "name": "sec0_node",
            "kind": "wire",
            "endpoints": [
                {"component": "T1",    "pin": "sec0.1"},
                {"component": "D_fwd", "pin": "A"},
            ],
        },
        {
            "name": "Vout0",
            "kind": "externalPort",
            "direction": "output",
            "endpoints": [
                {"component": "L_out0", "pin": "2"},
                {"component": "C_out0", "pin": "1"},
            ],
        },
        _gnd_wire(
            ("T1",     "pri.2"),
            ("T1",     "demag.1"),
            ("T1",     "sec0.2"),
            ("D_fw",   "A"),
            ("C_out0", "2"),
        ),
        *_gate_wires("Q1"),
    ]

    return {
        "stages": [switching_cell, isolation, output_rectifier_0, control],
        "interStageCircuit": inter_stage,
    }


# -----------------------------------------------------------------------------
# Active-clamp forward stencil
# -----------------------------------------------------------------------------
#
# MKF deck adds an active clamp (S_clamp + Cclamp) on the primary side and
# a full forward output stage (forward + freewheel diodes, output choke,
# output cap) on the secondary.
#
#   S1     vin_dc sw_node …                  ; Q1.D=Vin, Q1.S=switch_node
#   Lpri   pri_in 0                          ; T1.pri.1=switch_node, T1.pri.2=GND
#   Lsec0  sec0_in 0                         ; T1.sec0.1=sec0_node, T1.sec0.2=GND
#   Kpri_sec0 Lpri Lsec0 0.9999              ; coupling
#   S_clamp clamp_cap sw_node …              ; Q_clamp.D=clamp_node, Q_clamp.S=switch_node
#   Cclamp clamp_cap 0                       ; C_clamp.1=clamp_node, C_clamp.2=GND
#   Rclamp clamp_cap 0 1MEG                  ; testbench (high-Z bleeder)
#   Dfwd0  sec0_in sec0_rect                 ; D_fwd.A=sec0_node, D_fwd.K=sec0_rect_node
#   Dfw0   0       sec0_rect                 ; D_fw.A=GND,         D_fw.K=sec0_rect_node
#   Lout0  sec0_l_in vout0                   ; L_out0.1=sec0_rect_node, L_out0.2=Vout0
#   Cout0  vout0 0                           ; C_out0.1=Vout0, C_out0.2=GND
#
# Real BOM = {Q1, Q_clamp, C_clamp, T1 (Lpri+Lsec0+Kpri_sec0), D_fwd, D_fw, L_out0, C_out0}.


_ACF_REAL_KINDS = {
    "S1":         "switch",
    "S_clamp":    "switch",
    "Cclamp":     "capacitor",
    "Lpri":       "inductor",
    "Lsec0":      "inductor",
    "Kpri_sec0":  "coupling",
    "Dfwd0":      "diode",
    "Dfw0":       "diode",
    "Lout0":      "inductor",
    "Cout0":      "capacitor",
}


def active_clamp_forward(deck: SpiceDeck) -> TasTopology:
    """Decompose an MKF active-clamp forward deck into TAS."""
    _validate_real_set(deck, "active_clamp_forward", _ACF_REAL_KINDS)

    switching_cell = {
        "name": "primary_switch",
        "role": "switchingCell",
        "inputPort":  {"type": "dcBus",     "wire": "Vin"},
        "outputPorts": [{"type": "switchNode", "wire": "switch_node"}],
        "circuit": {
            "components": [
                _component("mosfet",    "Q1"),        # ← S1
                _component("mosfet",    "Q_clamp"),   # ← S_clamp
                _component("capacitor", "C_clamp"),   # ← Cclamp
            ],
            "connections": [
                {
                    "name": "clamp_node",
                    "kind": "wire",
                    "endpoints": [
                        {"component": "Q_clamp", "pin": "D"},
                        {"component": "C_clamp", "pin": "1"},
                    ],
                },
            ],
        },
    }

    isolation = _isolation_stage(
        ("pri", "sec0"),
        input_wire="switch_node",
        output_wires=("sec0_node",),
    )

    output_rectifier_0 = {
        "name": "output_0",
        "role": "outputRectifier",
        "inputPort":  {"type": "winding",  "wire": "sec0_node"},
        "outputPorts": [{"type": "dcOutput", "wire": "Vout0"}],
        "circuit": {
            "components": [
                _component("diode",     "D_fwd"),    # ← Dfwd0 (forward rectifier)
                _component("diode",     "D_fw"),     # ← Dfw0  (freewheel)
                _component("magnetic",  "L_out0"),   # ← Lout0 (output choke)
                _component("capacitor", "C_out0"),   # ← Cout0
            ],
            "connections": [
                {
                    "name": "sec0_rect_node",
                    "kind": "wire",
                    "endpoints": [
                        {"component": "D_fwd",  "pin": "K"},
                        {"component": "D_fw",   "pin": "K"},
                        {"component": "L_out0", "pin": "1"},
                    ],
                },
            ],
        },
    }

    control = _isolated_control_stage(("Q1", "Q_clamp"))

    inter_stage = [
        {
            "name": "Vin",
            "kind": "externalPort",
            "direction": "input",
            "endpoints": [{"component": "Q1", "pin": "D"}],
        },
        {
            "name": "switch_node",
            "kind": "wire",
            # Active clamp shares the switch node with Q_clamp.S.
            "endpoints": [
                {"component": "Q1",      "pin": "S"},
                {"component": "Q_clamp", "pin": "S"},
                {"component": "T1",      "pin": "pri.1"},
            ],
        },
        {
            "name": "sec0_node",
            "kind": "wire",
            "endpoints": [
                {"component": "T1",    "pin": "sec0.1"},
                {"component": "D_fwd", "pin": "A"},
            ],
        },
        {
            "name": "Vout0",
            "kind": "externalPort",
            "direction": "output",
            "endpoints": [
                {"component": "L_out0", "pin": "2"},
                {"component": "C_out0", "pin": "1"},
            ],
        },
        _gnd_wire(
            ("T1",      "pri.2"),
            ("T1",      "sec0.2"),
            ("C_clamp", "2"),
            ("D_fw",    "A"),
            ("C_out0",  "2"),
        ),
        *_gate_wires("Q1", "Q_clamp"),
    ]

    return {
        "stages": [switching_cell, isolation, output_rectifier_0, control],
        "interStageCircuit": inter_stage,
    }


# -----------------------------------------------------------------------------
# Isolated buck (flybuck) stencil — TWO outputs (primary + secondary)
# -----------------------------------------------------------------------------
#
# Flybuck = synchronous buck on the primary (Q1 HS, Q2 LS, Lpri as buck
# inductor + C_pri output cap) coupled magnetically to one or more
# secondary windings that each rectify to an isolated DC output.
#
# Real BOM = {Q1=S1, Q2=S2, T1 (Lpri+Lsec0+Kpri_sec0), C_pri=Cpri,
#             D_out0=Dsec0, C_out0=Cout0}.
#
# Two external output ports: Vout_pri (primary buck) and Vout0 (isolated).
# Controller regulates around Vout_pri — secondary is open-loop.


_ISOBUCK_REAL_KINDS = {
    "S1":         "switch",
    "S2":         "switch",
    "Lpri":       "inductor",
    "Lsec0":      "inductor",
    "Kpri_sec0":  "coupling",
    "Cpri":       "capacitor",
    "Dsec0":      "diode",
    "Cout0":      "capacitor",
}


def isolated_buck(deck: SpiceDeck) -> TasTopology:
    """Decompose an MKF isolated buck (flybuck) deck into TAS."""
    _validate_real_set(deck, "isolated_buck", _ISOBUCK_REAL_KINDS)

    switching_cell = {
        "name": "primary_switch",
        "role": "switchingCell",
        "inputPort":  {"type": "dcBus",     "wire": "Vin"},
        "outputPorts": [{"type": "switchNode", "wire": "switch_node"}],
        "circuit": {
            "components": [
                _component("mosfet", "Q1"),   # ← S1
                _component("mosfet", "Q2"),   # ← S2 (synchronous rectifier)
            ],
            "connections": [],
        },
    }

    # T1.pri.2 is NOT ground for flybuck — it sits on Vout_pri.
    isolation = _isolation_stage(
        ("pri", "sec0"),
        input_wire="switch_node",
        output_wires=("sec0_node",),  # pri.2 surfaces as Vout_pri directly
    )

    output_filter_pri = {
        "name": "output_pri",
        "role": "outputFilter",
        "inputPort":  {"type": "winding",  "wire": "Vout_pri"},
        "outputPorts": [{"type": "dcOutput", "wire": "Vout_pri"}],
        "circuit": {
            "components": [_component("capacitor", "C_pri")],   # ← Cpri
            "connections": [],
        },
    }

    output_rectifier_0 = {
        "name": "output_0",
        "role": "outputRectifier",
        "inputPort":  {"type": "winding",  "wire": "sec0_node"},
        "outputPorts": [{"type": "dcOutput", "wire": "Vout0"}],
        "circuit": {
            "components": [
                _component("diode",     "D_out0"),   # ← Dsec0
                _component("capacitor", "C_out0"),   # ← Cout0
            ],
            "connections": [],
        },
    }

    # Flybuck regulates the primary output; secondary is open-loop.
    control = _isolated_control_stage(("Q1", "Q2"), sense_wire="Vout_pri")

    inter_stage = [
        {
            "name": "Vin",
            "kind": "externalPort",
            "direction": "input",
            "endpoints": [{"component": "Q1", "pin": "D"}],
        },
        {
            "name": "switch_node",
            "kind": "wire",
            "endpoints": [
                {"component": "Q1", "pin": "S"},
                {"component": "Q2", "pin": "D"},
                {"component": "T1", "pin": "pri.1"},
            ],
        },
        {
            "name": "Vout_pri",
            "kind": "externalPort",
            "direction": "output",
            "endpoints": [
                {"component": "T1",    "pin": "pri.2"},
                {"component": "C_pri", "pin": "1"},
            ],
        },
        {
            "name": "sec0_node",
            "kind": "wire",
            "endpoints": [
                {"component": "T1",     "pin": "sec0.2"},
                {"component": "D_out0", "pin": "A"},
            ],
        },
        {
            "name": "Vout0",
            "kind": "externalPort",
            "direction": "output",
            "endpoints": [
                {"component": "D_out0", "pin": "K"},
                {"component": "C_out0", "pin": "1"},
            ],
        },
        _gnd_wire(
            ("Q2",     "S"),
            ("T1",     "sec0.1"),
            ("C_pri",  "2"),
            ("C_out0", "2"),
        ),
        *_gate_wires("Q1", "Q2"),
    ]

    return {
        "stages": [switching_cell, isolation, output_filter_pri,
                   output_rectifier_0, control],
        "interStageCircuit": inter_stage,
    }


# -----------------------------------------------------------------------------
# Isolated buck-boost stencil — TWO outputs (inverting primary + isolated sec)
# -----------------------------------------------------------------------------
#
# Topology: single primary switch + flyback-style transformer with BOTH a
# primary buck-boost output (via Dpri tapping off the switch node into
# C_pri) and an isolated secondary output (Dsec0 → C_out0).
#
#   S1   vin_dc pri_p         ; Q1.D=Vin, Q1.S=switch_node
#   Lpri pri_in 0             ; T1.pri.1=switch_node, T1.pri.2=GND
#   Lsec0 0 sec0_in           ; T1.sec0.1=GND, T1.sec0.2=sec0_node
#   Dpri vpri_rect pri_in     ; D_pri.A=Vout_pri side, D_pri.K=switch_node !!
#   Cpri vpri_out 0           ; C_pri.1=Vout_pri, C_pri.2=GND
#   Dsec0 sec0_node sec0_rect ; D_out0.A=sec0_node, D_out0.K=Vout0
#   Cout0 vout0 0
#
# Real BOM = {Q1, T1, D_pri, C_pri, D_out0, C_out0}.


_ISOBB_REAL_KINDS = {
    "S1":         "switch",
    "Lpri":       "inductor",
    "Lsec0":      "inductor",
    "Kpri_sec0":  "coupling",
    "Dpri":       "diode",
    "Cpri":       "capacitor",
    "Dsec0":      "diode",
    "Cout0":      "capacitor",
}


def isolated_buck_boost(deck: SpiceDeck) -> TasTopology:
    """Decompose an MKF isolated buck-boost deck into TAS."""
    _validate_real_set(deck, "isolated_buck_boost", _ISOBB_REAL_KINDS)

    switching_cell = {
        "name": "primary_switch",
        "role": "switchingCell",
        "inputPort":  {"type": "dcBus",     "wire": "Vin"},
        "outputPorts": [{"type": "switchNode", "wire": "switch_node"}],
        "circuit": {
            "components": [_component("mosfet", "Q1")],   # ← S1
            "connections": [],
        },
    }

    isolation = _isolation_stage(
        ("pri", "sec0"),
        input_wire="switch_node",
        output_wires=("sec0_node",),
    )

    # Primary inverting buck-boost output: D_pri taps the switch node
    # (cathode on switch_node) and rectifies to C_pri at Vout_pri.
    output_rectifier_pri = {
        "name": "output_pri",
        "role": "outputRectifier",
        "inputPort":  {"type": "switchNode", "wire": "switch_node"},
        "outputPorts": [{"type": "dcOutput",  "wire": "Vout_pri"}],
        "circuit": {
            "components": [
                _component("diode",     "D_pri"),   # ← Dpri
                _component("capacitor", "C_pri"),   # ← Cpri
            ],
            "connections": [],
        },
    }

    output_rectifier_0 = {
        "name": "output_0",
        "role": "outputRectifier",
        "inputPort":  {"type": "winding",  "wire": "sec0_node"},
        "outputPorts": [{"type": "dcOutput", "wire": "Vout0"}],
        "circuit": {
            "components": [
                _component("diode",     "D_out0"),  # ← Dsec0
                _component("capacitor", "C_out0"),  # ← Cout0
            ],
            "connections": [],
        },
    }

    control = _isolated_control_stage(("Q1",), sense_wire="Vout_pri")

    inter_stage = [
        {
            "name": "Vin",
            "kind": "externalPort",
            "direction": "input",
            "endpoints": [{"component": "Q1", "pin": "D"}],
        },
        {
            "name": "switch_node",
            "kind": "wire",
            # 3 endpoints — D_pri taps switch_node from its cathode.
            "endpoints": [
                {"component": "Q1",    "pin": "S"},
                {"component": "T1",    "pin": "pri.1"},
                {"component": "D_pri", "pin": "K"},
            ],
        },
        {
            "name": "Vout_pri",
            "kind": "externalPort",
            "direction": "output",
            "endpoints": [
                {"component": "D_pri", "pin": "A"},
                {"component": "C_pri", "pin": "1"},
            ],
        },
        {
            "name": "sec0_node",
            "kind": "wire",
            "endpoints": [
                {"component": "T1",     "pin": "sec0.2"},
                {"component": "D_out0", "pin": "A"},
            ],
        },
        {
            "name": "Vout0",
            "kind": "externalPort",
            "direction": "output",
            "endpoints": [
                {"component": "D_out0", "pin": "K"},
                {"component": "C_out0", "pin": "1"},
            ],
        },
        _gnd_wire(
            ("T1",     "pri.2"),
            ("T1",     "sec0.1"),
            ("C_pri",  "2"),
            ("C_out0", "2"),
        ),
        *_gate_wires("Q1"),
    ]

    return {
        "stages": [switching_cell, isolation, output_rectifier_pri,
                   output_rectifier_0, control],
        "interStageCircuit": inter_stage,
    }


# -----------------------------------------------------------------------------
# Registry of stencils. Each key is the canonical Heaviside topology name.
# Add a topology by writing a function above and listing it here.
# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# Two-switch forward stencil
# -----------------------------------------------------------------------------
#
# Two-switch forward adds a second primary switch (Q2, low-side) and pairs
# each switch with a reset diode (D1, D2). When both switches turn OFF, the
# primary current commutates through D1+D2 back to Vin, clamping V_DS of
# each switch to Vin and resetting the transformer.
#
# MKF deck (verified against /tmp/all_decks.json):
#
#   S1   vin_dc   sw1_out  pwm_ctrl 0 SW1      ; Q1.D=Vin,         Q1.S=switch_node
#   D1   0        sw1_out  DIDEAL              ; D1.A=GND,         D1.K=switch_node
#   Vpri_sense sw1_out pri_in 0                ; testbench
#   Lpri pri_in   pri_gnd  1e-3                ; T1.pri.1=switch_node, T1.pri.2=pri_gnd_node
#   Lsec0 sec0_in 0        2.5e-4              ; T1.sec0.1=sec0_node,  T1.sec0.2=GND
#   Kpri_sec0 Lpri Lsec0 0.9999                ; coupling
#   S2   pri_gnd  0        pwm_ctrl 0 SW1      ; Q2.D=pri_gnd_node, Q2.S=GND
#   D2   pri_gnd  vin_dc   DIDEAL              ; D2.A=pri_gnd_node, D2.K=Vin
#   Dfwd0 sec0_in sec0_rect DIDEAL             ; D_fwd.A=sec0_node, D_fwd.K=sec0_rect_node
#   Dfw0  0       sec0_rect DIDEAL             ; D_fw.A=GND,        D_fw.K=sec0_rect_node
#   Rlout0 ... Lout0 lout0_node vout0          ; output choke (Rlout = DCR, testbench)
#   Cout0 vout0 0
#
# Real BOM = {Q1, Q2, D1, D2, T1 (Lpri+Lsec0+Kpri_sec0), D_fwd, D_fw, L_out0, C_out0}.
# Output stage is identical to active_clamp_forward (same diode-OR choke topology).


_2SF_REAL_KINDS = {
    "S1":         "switch",
    "S2":         "switch",
    "D1":         "diode",
    "D2":         "diode",
    "Lpri":       "inductor",
    "Lsec0":      "inductor",
    "Kpri_sec0":  "coupling",
    "Dfwd0":      "diode",
    "Dfw0":       "diode",
    "Lout0":      "inductor",
    "Cout0":      "capacitor",
}


def two_switch_forward(deck: SpiceDeck) -> TasTopology:
    """Decompose an MKF two-switch forward deck into TAS."""
    _validate_real_set(deck, "two_switch_forward", _2SF_REAL_KINDS)

    switching_cell = {
        "name": "primary_switch",
        "role": "switchingCell",
        "inputPort":  {"type": "dcBus", "wire": "Vin"},
        # Two switch-node outputs feed both ends of T1.pri.
        "outputPorts": [
            {"type": "switchNode", "wire": "switch_node"},
            {"type": "switchNode", "wire": "pri_gnd_node"},
        ],
        "circuit": {
            "components": [
                _component("mosfet", "Q1"),   # ← S1 (high-side)
                _component("mosfet", "Q2"),   # ← S2 (low-side)
                _component("diode",  "D1"),   # ← D1 (HS reset diode)
                _component("diode",  "D2"),   # ← D2 (LS reset diode)
            ],
            "connections": [],
        },
    }

    # Custom isolation stage: primary winding is driven differentially
    # (both pri.1 and pri.2 are active nets, neither is GND), so the
    # helper's single-input_wire shape doesn't fit. Build inline.
    isolation = {
        "name": "isolation",
        "role": "isolation",
        "inputPort": {"type": "switchNode", "wire": "switch_node"},
        "outputPorts": [
            {"type": "switchNode", "wire": "pri_gnd_node"},  # secondary-of-pri return
            {"type": "winding",    "wire": "sec0_node"},
        ],
        "circuit": {
            "components": [_t1_component(("pri", "sec0"))],
            "connections": [],
        },
    }

    output_rectifier_0 = {
        "name": "output_0",
        "role": "outputRectifier",
        "inputPort":  {"type": "winding",  "wire": "sec0_node"},
        "outputPorts": [{"type": "dcOutput", "wire": "Vout0"}],
        "circuit": {
            "components": [
                _component("diode",     "D_fwd"),    # ← Dfwd0
                _component("diode",     "D_fw"),     # ← Dfw0
                _component("magnetic",  "L_out0"),   # ← Lout0
                _component("capacitor", "C_out0"),   # ← Cout0
            ],
            "connections": [
                {
                    "name": "sec0_rect_node",
                    "kind": "wire",
                    "endpoints": [
                        {"component": "D_fwd",  "pin": "K"},
                        {"component": "D_fw",   "pin": "K"},
                        {"component": "L_out0", "pin": "1"},
                    ],
                },
            ],
        },
    }

    control = _isolated_control_stage(("Q1", "Q2"))

    inter_stage = [
        {
            "name": "Vin",
            "kind": "externalPort",
            "direction": "input",
            # Q1 sources from Vin; D2 returns reset current to Vin.
            "endpoints": [
                {"component": "Q1", "pin": "D"},
                {"component": "D2", "pin": "K"},
            ],
        },
        {
            "name": "switch_node",
            "kind": "wire",
            "endpoints": [
                {"component": "Q1", "pin": "S"},
                {"component": "D1", "pin": "K"},
                {"component": "T1", "pin": "pri.1"},
            ],
        },
        {
            "name": "pri_gnd_node",
            "kind": "wire",
            "endpoints": [
                {"component": "Q2", "pin": "D"},
                {"component": "D2", "pin": "A"},
                {"component": "T1", "pin": "pri.2"},
            ],
        },
        {
            "name": "sec0_node",
            "kind": "wire",
            "endpoints": [
                {"component": "T1",    "pin": "sec0.1"},
                {"component": "D_fwd", "pin": "A"},
            ],
        },
        {
            "name": "Vout0",
            "kind": "externalPort",
            "direction": "output",
            "endpoints": [
                {"component": "L_out0", "pin": "2"},
                {"component": "C_out0", "pin": "1"},
            ],
        },
        _gnd_wire(
            ("D1",     "A"),
            ("T1",     "sec0.2"),
            ("Q2",     "S"),
            ("D_fw",   "A"),
            ("C_out0", "2"),
        ),
        *_gate_wires("Q1", "Q2"),
    ]

    return {
        "stages": [switching_cell, isolation, output_rectifier_0, control],
        "interStageCircuit": inter_stage,
    }


# -----------------------------------------------------------------------------
# LLC stencil (resonant half-bridge with center-tapped secondary)
# -----------------------------------------------------------------------------
#
# Requires ``bridge_simulation_mode="switch"``. Under the default
# behavioural-PULSE mode, MKF replaces the entire half-bridge with a
# single ``Vbridge`` source — no real MOSFETs to decompose.
#
# MKF deck (half-bridge, single CT output), verified empirically against
# the PyOpenMagnetics build with the new ``bridge_simulation_mode``
# parameter:
#
#   Vdc_supply vdc_supply 0 48
#   Cbus_hi vdc_supply mid_point 1u IC=24
#   Cbus_lo mid_point 0 1u IC=24
#   Rbal_hi vdc_supply mid_point 100k
#   Rbal_lo mid_point 0 100k
#   Vpwm_HI pwm_HI 0 PULSE(...)            ; gate drive (testbench)
#   Vpwm_LO pwm_LO 0 PULSE(...)            ; gate drive (testbench)
#   SHI vdc_supply sw_node pwm_HI 0 SW1    ; D=Vin S=sw_node
#   SLO sw_node 0 pwm_LO 0 SW1             ; D=sw_node S=GND
#   DHI 0 sw_node DIDEAL                   ; synthetic body diode (drop)
#   DLO sw_node vdc_supply DIDEAL          ; synthetic body diode (drop)
#   Rsnub_HI/Csnub_HI/Rsnub_LO/Csnub_LO    ; bridge snubbers (drop)
#   Vpri_sense sw_node lr_in 0             ; ammeter (drop)
#   Cr lr_in cr_ls                         ; resonant cap
#   Lr cr_ls pri_top                       ; resonant inductor (separate)
#   Lpri pri_top pri_bot                   ; T1.pri
#   Lsec1_o1 sec_top_sec_o1 sec_ct_o1      ; T1.sec1 (upper half)
#   Lsec2_o1 sec_ct_o1 sec_bot_sec_o1      ; T1.sec2 (lower half)
#   K1/K2/K3                               ; T1 coupling
#   Rpri_ret pri_bot mid_point 0.001       ; 1mΩ return (drop)
#   D1_o1 sec_top_o1 vout_pos_o1 DRECT
#   D2_o1 sec_bot_o1 vout_pos_o1 DRECT
#   Rsn1_o1/Csn1_o1/Rsn2_o1/Csn2_o1        ; rectifier snubbers (drop)
#   Vsec*_sense_o1/Vgnd_o1                 ; ammeters (drop)
#   Resr_o1 vout_pos_o1 vout_cap_o1        ; Cout ESR (drop)
#   Cout_o1 vout_cap_o1 vout_neg_o1
#   Rload_o1 vout_cap_o1 vout_neg_o1       ; load (drop)
#
# Mapping to TAS (Maksimović convention: the resonant tank belongs to the
# ``inverter`` stage that emits hfAc):
#
#   inverter:        Q_HI, Q_LO, C_bus_hi, C_bus_lo, R_bal_hi, R_bal_lo,
#                    C_r, L_r          dcBus Vin → hfAc pri_top
#   isolation:       T1 (pri, sec1, sec2)   hfAc pri_top → hfAc sec_top,
#                                                          hfAc sec_bot
#   outputRectifier: D1, D2, C_out0   hfAc sec_top → dcOutput Vout0
#   control:         U1 drives {Q_HI, Q_LO}, senses Vout0


_LLC_REAL_KINDS = {
    "Cbus_hi":  "capacitor",
    "Cbus_lo":  "capacitor",
    "Rbal_hi":  "resistor",
    "Rbal_lo":  "resistor",
    "SHI":      "switch",
    "SLO":      "switch",
    "Cr":       "capacitor",
    "Lr":       "inductor",
    "Lpri":     "inductor",      # → T1.pri
    "Lsec1_o1": "inductor",      # → T1.sec1 (upper half of CT)
    "Lsec2_o1": "inductor",      # → T1.sec2 (lower half of CT)
    "K1":       "coupling",      # → T1 coupling
    "K2":       "coupling",
    "K3":       "coupling",
    "D1_o1":    "diode",
    "D2_o1":    "diode",
    "Cout_o1":  "capacitor",
}


def llc(deck: SpiceDeck) -> TasTopology:
    """Decompose an MKF LLC deck (switch-mode bridge) into TAS.

    Requires the deck to have been generated with
    ``bridge_simulation_mode="switch"`` so the half-bridge appears as
    real ``SHI``/``SLO`` switches rather than a single ``Vbridge`` pulse.
    Raises :class:`StencilError` (via ``_validate_real_set``) if the
    deck instead contains a behavioural bridge.
    """
    _validate_real_set(deck, "llc", _LLC_REAL_KINDS)

    inverter = {
        "name": "inverter",
        "role": "inverter",
        "inputPort":  {"type": "dcBus", "wire": "Vin"},
        "outputPorts": [{"type": "hfAc", "wire": "pri_top"}],
        "circuit": {
            "components": [
                _component("mosfet",    "Q_HI"),       # ← SHI
                _component("mosfet",    "Q_LO"),       # ← SLO
                _component("capacitor", "C_bus_hi"),   # ← Cbus_hi
                _component("capacitor", "C_bus_lo"),   # ← Cbus_lo
                _component("resistor",  "R_bal_hi"),   # ← Rbal_hi
                _component("resistor",  "R_bal_lo"),   # ← Rbal_lo
                _component("capacitor", "C_r"),        # ← Cr
                _component("magnetic",  "L_r"),        # ← Lr
            ],
            "connections": [
                {
                    "name": "sw_node",
                    "kind": "wire",
                    "endpoints": [
                        {"component": "Q_HI", "pin": "S"},
                        {"component": "Q_LO", "pin": "D"},
                        {"component": "C_r",  "pin": "1"},
                    ],
                },
                {
                    "name": "resonant_mid",
                    "kind": "wire",
                    "endpoints": [
                        {"component": "C_r", "pin": "2"},
                        {"component": "L_r", "pin": "1"},
                    ],
                },
            ],
        },
    }

    isolation = {
        "name": "isolation",
        "role": "isolation",
        "inputPort":  {"type": "hfAc", "wire": "pri_top"},
        "outputPorts": [
            {"type": "hfAc", "wire": "sec_top", "name": "sec_top"},
            {"type": "hfAc", "wire": "sec_bot", "name": "sec_bot"},
        ],
        "circuit": {
            "components": [_t1_component(("pri", "sec1", "sec2"))],
            "connections": [],
        },
    }

    output_rectifier_0 = {
        "name": "output_0",
        "role": "outputRectifier",
        "inputPort":  {"type": "hfAc", "wire": "sec_top"},
        "outputPorts": [{"type": "dcOutput", "wire": "Vout0"}],
        "circuit": {
            "components": [
                _component("diode",     "D1"),       # ← D1_o1
                _component("diode",     "D2"),       # ← D2_o1
                _component("capacitor", "C_out0"),   # ← Cout_o1
            ],
            # D1.K / D2.K / C_out0.1 all sit on the Vout0 externalPort
            # node — see interStageCircuit below. No stage-internal
            # wires are needed; an intra-stage ``vout_pos`` that also
            # listed C_out0.1 would put the pin on two wires at once and
            # the writer would reject it as a duplicate net.
            "connections": [],
        },
    }

    control = _isolated_control_stage(("Q_HI", "Q_LO"))

    inter_stage = [
        {
            "name": "Vin",
            "kind": "externalPort",
            "direction": "input",
            "endpoints": [
                {"component": "Q_HI",     "pin": "D"},
                {"component": "C_bus_hi", "pin": "1"},
                {"component": "R_bal_hi", "pin": "1"},
            ],
        },
        {
            # Bus midpoint — capacitive divider centre, also the
            # primary-winding return. Inverter-internal on one side
            # (C_bus_*, R_bal_*) but must reach T1.pri.2 in the isolation
            # stage, so it lives in interStage rather than as a stage-
            # internal wire.
            "name": "mid_point",
            "kind": "wire",
            "endpoints": [
                {"component": "C_bus_hi", "pin": "2"},
                {"component": "C_bus_lo", "pin": "1"},
                {"component": "R_bal_hi", "pin": "2"},
                {"component": "R_bal_lo", "pin": "1"},
                {"component": "T1",       "pin": "pri.2"},
            ],
        },
        {
            "name": "pri_top",
            "kind": "wire",
            "endpoints": [
                {"component": "L_r", "pin": "2"},
                {"component": "T1",  "pin": "pri.1"},
            ],
        },
        {
            "name": "sec_top",
            "kind": "wire",
            "endpoints": [
                {"component": "T1", "pin": "sec1.1"},
                {"component": "D1", "pin": "A"},
            ],
        },
        {
            "name": "sec_bot",
            "kind": "wire",
            "endpoints": [
                {"component": "T1", "pin": "sec2.2"},
                {"component": "D2", "pin": "A"},
            ],
        },
        {
            # Center tap of the CT secondary = Vout-negative rail.
            # Connects T1.sec1.2 and T1.sec2.1 to the output capacitor
            # return.
            "name": "sec_ct",
            "kind": "wire",
            "endpoints": [
                {"component": "T1",     "pin": "sec1.2"},
                {"component": "T1",     "pin": "sec2.1"},
                {"component": "C_out0", "pin": "2"},
            ],
        },
        {
            "name": "Vout0",
            "kind": "externalPort",
            "direction": "output",
            "endpoints": [
                {"component": "D1",     "pin": "K"},
                {"component": "D2",     "pin": "K"},
                {"component": "C_out0", "pin": "1"},
            ],
        },
        _gnd_wire(
            ("C_bus_lo", "2"),
            ("R_bal_lo", "2"),
            ("Q_LO",     "S"),
        ),
        *_gate_wires("Q_HI", "Q_LO"),
    ]

    return {
        "stages": [inverter, isolation, output_rectifier_0, control],
        "interStageCircuit": inter_stage,
    }


# -----------------------------------------------------------------------------
# Push-pull stencil (center-tapped primary + center-tapped secondary)
# -----------------------------------------------------------------------------
#
# MKF deck (verified empirically against PyOpenMagnetics, see /tmp/pushpull.cir):
#
#   Vin vin_dc 0 48
#   Vpwm1/Vpwm2                                ; non-overlapping gate drives
#   .model SW1 SW VT=2.5 …
#
#   * Center-tapped PRIMARY (CT = vin_dc, switches pull each end to 0)
#   Lpri_top pri_top vin_dc 1m                 ; T1.pri_top.1=pri_top  .2=vin_dc
#   Vpri_top_sense pri_top sw1_node 0          ; testbench ammeter (drop)
#   S1 sw1_node 0 pwm_ctrl1 0 SW1              ; Q1.D=sw1_node Q1.S=0
#   Lpri_bot vin_dc pri_bot 1m                 ; T1.pri_bot.1=vin_dc  .2=pri_bot
#   Vpri_bot_sense pri_bot sw2_node 0          ; testbench ammeter (drop)
#   S2 sw2_node 0 pwm_ctrl2 0 SW1              ; Q2.D=sw2_node Q2.S=0
#
#   Bvpri_top_diff/Bvpri_bot_diff              ; behavioural probes (drop)
#
#   * Center-tapped SECONDARY (CT = GND)
#   Lsec_top sec_top 0 inf                     ; T1.sec_top.1=sec_top  .2=0
#   Lsec_bot 0 sec_bot inf                     ; T1.sec_bot.1=0  .2=sec_bot
#   K1..K6 (pairwise)                          ; → T1 coupling
#
#   Rsnub_top/bot/sec_top/sec_bot              ; 1MEG convergence (drop via Rsn prefix)
#
#   * Rectifier + output filter
#   Vsec_top_sense sec_top sec_top_d 0         ; ammeter (drop)
#   Dsec_top sec_top_d sec_rect DIDEAL         ; D1.A=sec_top  D1.K=sec_rect
#   Vsec_bot_sense sec_bot sec_bot_d 0         ; ammeter (drop)
#   Dsec_bot sec_bot_d sec_rect DIDEAL         ; D2.A=sec_bot  D2.K=sec_rect
#   Rsnub_d1/Csnub_d1/Rsnub_d2/Csnub_d2        ; rectifier snubbers (drop)
#   Vsec_sense sec_rect sec_l_in 0             ; ammeter (drop)
#   Lout sec_l_in vout 10u                     ; L_out0.1=sec_rect  .2=vout
#   Cout vout 0 100u IC=12                     ; C_out0.1=vout  .2=GND
#   Rload vout 0 2.4                           ; testbench load (drop)
#
# Mapping to TAS:
#   switchingCell:   Q1, Q2          dcBus Vin → 2× switchNode (sw_top, sw_bot)
#   isolation:       T1 (4 windings) — dcBus Vin (CT) + 2× switchNode →
#                                       2× winding (sec_top_node, sec_bot_node)
#   outputRectifier: D1, D2, L_out0, C_out0   2× winding → dcOutput Vout0
#   control:         U1 drives {Q1, Q2}, senses Vout0
#
# Note: Vin externalPort endpoints live on the isolation stage (T1 center
# tap), not on the switching cell — neither Q1 nor Q2 touches Vin
# directly. This is the first stencil where the switching cell's
# ``inputPort`` is purely metadata; the writer matches by wire name.


_PUSH_PULL_REAL_KINDS = {
    "S1":       "switch",
    "S2":       "switch",
    "Lpri_top": "inductor",      # → T1.pri_top
    "Lpri_bot": "inductor",      # → T1.pri_bot
    "Lsec_top": "inductor",      # → T1.sec_top
    "Lsec_bot": "inductor",      # → T1.sec_bot
    "K1":       "coupling",      # → T1 coupling (6 pairwise Ks)
    "K2":       "coupling",
    "K3":       "coupling",
    "K4":       "coupling",
    "K5":       "coupling",
    "K6":       "coupling",
    "Dsec_top": "diode",
    "Dsec_bot": "diode",
    "Lout":     "inductor",
    "Cout":     "capacitor",
}


def push_pull(deck: SpiceDeck) -> TasTopology:
    """Decompose an MKF push-pull deck into TAS."""
    _validate_real_set(deck, "push_pull", _PUSH_PULL_REAL_KINDS)

    switching_cell = {
        "name": "primary_switch",
        "role": "switchingCell",
        # Vin port is metadata only — Q1/Q2 drains do NOT touch Vin;
        # Vin enters via T1 center tap (see isolation stage).
        "inputPort":  {"type": "dcBus", "wire": "Vin"},
        "outputPorts": [
            {"type": "switchNode", "wire": "sw_top_node"},
            {"type": "switchNode", "wire": "sw_bot_node"},
        ],
        "circuit": {
            "components": [
                _component("mosfet", "Q1"),   # ← S1 (drives upper primary)
                _component("mosfet", "Q2"),   # ← S2 (drives lower primary)
            ],
            "connections": [],
        },
    }

    isolation = {
        "name": "isolation",
        "role": "isolation",
        # Push-pull's isolation stage has 3 inputs: Vin at the center tap
        # and one switchNode per primary half. Model the dominant power
        # path (Vin) as inputPort and the two switch returns as auxiliary
        # input-side wires expressed in interStage. Output ports are the
        # two secondary winding ends.
        "inputPort": {"type": "dcBus", "wire": "Vin"},
        "outputPorts": [
            {"type": "winding", "wire": "sec_top_node", "name": "sec_top"},
            {"type": "winding", "wire": "sec_bot_node", "name": "sec_bot"},
        ],
        "circuit": {
            "components": [
                _t1_component(("pri_top", "pri_bot", "sec_top", "sec_bot")),
            ],
            "connections": [],
        },
    }

    output_rectifier_0 = {
        "name": "output_0",
        "role": "outputRectifier",
        # Two winding inputs feed the diode-OR at sec_rect.
        "inputPort":  {"type": "winding",  "wire": "sec_top_node"},
        "outputPorts": [{"type": "dcOutput", "wire": "Vout0"}],
        "circuit": {
            "components": [
                _component("diode",     "D1"),       # ← Dsec_top
                _component("diode",     "D2"),       # ← Dsec_bot
                _component("magnetic",  "L_out0"),   # ← Lout
                _component("capacitor", "C_out0"),   # ← Cout
            ],
            "connections": [
                {
                    "name": "sec_rect",
                    "kind": "wire",
                    "endpoints": [
                        {"component": "D1",     "pin": "K"},
                        {"component": "D2",     "pin": "K"},
                        {"component": "L_out0", "pin": "1"},
                    ],
                },
            ],
        },
    }

    control = _isolated_control_stage(("Q1", "Q2"))

    inter_stage = [
        {
            # Vin enters at the primary center tap — endpoints on
            # T1.pri_top.2 and T1.pri_bot.1. Q1/Q2 drains do NOT touch
            # Vin; this is the distinguishing feature of push-pull.
            "name": "Vin",
            "kind": "externalPort",
            "direction": "input",
            "endpoints": [
                {"component": "T1", "pin": "pri_top.2"},
                {"component": "T1", "pin": "pri_bot.1"},
            ],
        },
        {
            "name": "sw_top_node",
            "kind": "wire",
            "endpoints": [
                {"component": "Q1", "pin": "D"},
                {"component": "T1", "pin": "pri_top.1"},
            ],
        },
        {
            "name": "sw_bot_node",
            "kind": "wire",
            "endpoints": [
                {"component": "Q2", "pin": "D"},
                {"component": "T1", "pin": "pri_bot.2"},
            ],
        },
        {
            "name": "sec_top_node",
            "kind": "wire",
            "endpoints": [
                {"component": "T1", "pin": "sec_top.1"},
                {"component": "D1", "pin": "A"},
            ],
        },
        {
            "name": "sec_bot_node",
            "kind": "wire",
            "endpoints": [
                {"component": "T1", "pin": "sec_bot.2"},
                {"component": "D2", "pin": "A"},
            ],
        },
        {
            "name": "Vout0",
            "kind": "externalPort",
            "direction": "output",
            "endpoints": [
                {"component": "L_out0", "pin": "2"},
                {"component": "C_out0", "pin": "1"},
            ],
        },
        _gnd_wire(
            ("Q1",     "S"),
            ("Q2",     "S"),
            ("T1",     "sec_top.2"),  # secondary center tap = GND
            ("T1",     "sec_bot.1"),  # secondary center tap = GND
            ("C_out0", "2"),
        ),
        *_gate_wires("Q1", "Q2"),
    ]

    return {
        "stages": [switching_cell, isolation, output_rectifier_0, control],
        "interStageCircuit": inter_stage,
    }


# -----------------------------------------------------------------------------
# Phase-shifted full bridge stencil
# -----------------------------------------------------------------------------
#
# MKF deck (bridge_simulation_mode="switch", center-tapped rectifier):
#
#   Vdc vin_dc 0 400
#   * Leg A: SA hi-side, SB lo-side, midpoint mid_A
#   SA vin_dc mid_A …            ; Q_A.D=Vin Q_A.S=mid_A
#   DA 0 mid_A DIDEAL             ; synthetic body diode (drop, _TESTBENCH_EXACT)
#   SB mid_A 0 …                  ; Q_B.D=mid_A Q_B.S=0
#   DB mid_A vin_dc DIDEAL        ; synthetic body diode (drop)
#   Rsnub_QA/Csnub_QA/Rsnub_QB/Csnub_QB   ; snubbers (drop via Rsn/Csn prefix)
#   * Leg C: SC hi-side, SD lo-side, midpoint mid_C
#   SC vin_dc mid_C …             ; Q_C.D=Vin Q_C.S=mid_C
#   DC 0 mid_C DIDEAL             ; synthetic body diode (drop)
#   SD mid_C 0 …                  ; Q_D.D=mid_C Q_D.S=0
#   DD mid_C vin_dc DIDEAL        ; synthetic body diode (drop)
#   Rsnub_QC/Csnub_QC/Rsnub_QD/Csnub_QD   ; snubbers (drop)
#   Vpri_sense mid_A pri_lr 0     ; ammeter (drop)
#   Evab vab 0 mid_A mid_C 1      ; differential probe (drop via E-as-behavioural)
#   L_series pri_lr trafo_pri     ; → L_r (seriesInductor)
#   L_pri trafo_pri mid_C         ; T1.pri.1=trafo_pri T1.pri.2=mid_C
#   L_sec_o1 sec_a_o1 sec_b_o1    ; T1.sec0.1=sec_a T1.sec0.2=sec_b
#   K1 L_pri L_sec_o1             ; T1 coupling
#   Vsec1_sense_o1, Vsec2_sense_o1, Vct_o1, Vout_sense_o1   ; ammeters (drop)
#   * Center-tapped rectifier (output 1)
#   D_r1_o1 rec_a_o1 out_rect_o1  ; D1.A=sec_a (post-sense) D1.K=out_rect
#   D_r2_o1 rec_b_o1 out_rect_o1  ; D2.A=sec_b (post-sense) D2.K=out_rect
#   Rct_o1 sec_ct_o1 sec_b_o1 1u  ; 1µΩ CT stub to GND (drop via Rct_ prefix)
#   L_out_o1 out_rect_o1 out_node_o1   ; L_out0
#   C_out_o1 out_node_o1 out_gnd_o1    ; C_out0
#   R_load_o1 out_node_o1 out_gnd_o1   ; load (drop via R_load prefix)
#
# Mapping to TAS:
#   inverter:        Q_A,Q_B,Q_C,Q_D,L_r       dcBus Vin → 2× hfAc (mid_A, mid_C)
#   isolation:       T1 (pri, sec0)            hfAc mid_A → 2× winding (sec_a, sec_b)
#   outputRectifier: D1, D2, L_out0, C_out0    2× winding → dcOutput Vout0
#   control:         U1 drives {Q_A,Q_B,Q_C,Q_D}, senses Vout0
#
# out_gnd_o1 = GND (Vout_sense_o1 grounds it via a 0V source). The MKF
# "center tap" of the secondary is modelled as a 1µΩ stub (Rct_o1) from
# sec_b to GND — drop the stub; D2.A terminates on sec_b which is
# rectified via the GND-relative loop.


_PSFB_REAL_KINDS = {
    "SA":        "switch",
    "SB":        "switch",
    "SC":        "switch",
    "SD":        "switch",
    "L_series":  "inductor",     # → L_r (seriesInductor)
    "L_pri":     "inductor",     # → T1.pri
    "L_sec_o1":  "inductor",     # → T1.sec0
    "K1":        "coupling",
    "D_r1_o1":   "diode",
    "D_r2_o1":   "diode",
    "L_out_o1":  "inductor",     # → L_out0
    "C_out_o1":  "capacitor",    # → C_out0
}


def phase_shifted_full_bridge(deck: SpiceDeck) -> TasTopology:
    """Decompose an MKF phase-shifted full bridge deck (switch-mode) into TAS.

    Requires ``bridge_simulation_mode="switch"`` so SA/SB/SC/SD are real
    switches and not collapsed into a single behavioural source.
    """
    _validate_real_set(deck, "phase_shifted_full_bridge", _PSFB_REAL_KINDS)

    inverter = {
        "name": "inverter",
        "role": "inverter",
        "inputPort":  {"type": "dcBus", "wire": "Vin"},
        "outputPorts": [
            {"type": "hfAc", "wire": "mid_A", "name": "leg_a"},
            {"type": "hfAc", "wire": "mid_C", "name": "leg_c"},
        ],
        "circuit": {
            "components": [
                _component("mosfet",   "Q_A"),   # ← SA (leg A high-side)
                _component("mosfet",   "Q_B"),   # ← SB (leg A low-side)
                _component("mosfet",   "Q_C"),   # ← SC (leg C high-side)
                _component("mosfet",   "Q_D"),   # ← SD (leg C low-side)
                _component("magnetic", "L_r"),   # ← L_series (resonant/leakage)
            ],
            "connections": [
                {
                    "name": "leg_a_mid",
                    "kind": "wire",
                    "endpoints": [
                        {"component": "Q_A", "pin": "S"},
                        {"component": "Q_B", "pin": "D"},
                        {"component": "L_r", "pin": "1"},
                    ],
                },
            ],
        },
    }

    isolation = {
        "name": "isolation",
        "role": "isolation",
        "inputPort": {"type": "hfAc", "wire": "pri_top"},
        "outputPorts": [
            {"type": "winding", "wire": "sec_a", "name": "sec_a"},
            {"type": "winding", "wire": "sec_b", "name": "sec_b"},
        ],
        "circuit": {
            "components": [_t1_component(("pri", "sec0"))],
            "connections": [],
        },
    }

    output_rectifier_0 = {
        "name": "output_0",
        "role": "outputRectifier",
        "inputPort":  {"type": "winding",  "wire": "sec_a"},
        "outputPorts": [{"type": "dcOutput", "wire": "Vout0"}],
        "circuit": {
            "components": [
                _component("diode",     "D1"),       # ← D_r1_o1
                _component("diode",     "D2"),       # ← D_r2_o1
                _component("magnetic",  "L_out0"),   # ← L_out_o1
                _component("capacitor", "C_out0"),   # ← C_out_o1
            ],
            "connections": [
                {
                    "name": "out_rect",
                    "kind": "wire",
                    "endpoints": [
                        {"component": "D1",     "pin": "K"},
                        {"component": "D2",     "pin": "K"},
                        {"component": "L_out0", "pin": "1"},
                    ],
                },
            ],
        },
    }

    control = _isolated_control_stage(("Q_A", "Q_B", "Q_C", "Q_D"))

    inter_stage = [
        {
            "name": "Vin",
            "kind": "externalPort",
            "direction": "input",
            "endpoints": [
                {"component": "Q_A", "pin": "D"},
                {"component": "Q_C", "pin": "D"},
            ],
        },
        {
            # L_r feeds T1.pri.1; mkf names this node trafo_pri.
            "name": "pri_top",
            "kind": "wire",
            "endpoints": [
                {"component": "L_r", "pin": "2"},
                {"component": "T1",  "pin": "pri.1"},
            ],
        },
        {
            # Leg C midpoint = primary winding return + Q_C.S + Q_D.D.
            "name": "mid_C",
            "kind": "wire",
            "endpoints": [
                {"component": "Q_C", "pin": "S"},
                {"component": "Q_D", "pin": "D"},
                {"component": "T1",  "pin": "pri.2"},
            ],
        },
        {
            "name": "sec_a",
            "kind": "wire",
            "endpoints": [
                {"component": "T1", "pin": "sec0.1"},
                {"component": "D1", "pin": "A"},
            ],
        },
        {
            # MKF center-tap stub Rct_o1 ties sec_b to GND via 1µΩ;
            # the stencil drops the stub and places D2.A on sec_b
            # which then naturally reaches the output rectifier.
            "name": "sec_b",
            "kind": "wire",
            "endpoints": [
                {"component": "T1", "pin": "sec0.2"},
                {"component": "D2", "pin": "A"},
            ],
        },
        {
            "name": "Vout0",
            "kind": "externalPort",
            "direction": "output",
            "endpoints": [
                {"component": "L_out0", "pin": "2"},
                {"component": "C_out0", "pin": "1"},
            ],
        },
        _gnd_wire(
            ("Q_B",     "S"),
            ("Q_D",     "S"),
            ("C_out0",  "2"),
        ),
        *_gate_wires("Q_A", "Q_B", "Q_C", "Q_D"),
    ]

    return {
        "stages": [inverter, isolation, output_rectifier_0, control],
        "interStageCircuit": inter_stage,
    }


# -----------------------------------------------------------------------------
# Asymmetric half-bridge (AHB) stencil
# -----------------------------------------------------------------------------
#
# MKF deck (bridge_simulation_mode="switch", rectifierType="fullBridge"):
#
#   Vdc vin_dc 0 400
#   Vpwm_Q1 / Vpwm_Q2                      ; gate drives (drop)
#   Vq1_sense vin_dc q1_drain 0            ; ammeter (drop)
#   S1 q1_drain sw …                       ; Q1.D=Vin (via sense) Q1.S=sw
#   D1 sw vin_dc DIDEAL                    ; synthetic body diode (drop, exact)
#   Rsnub_Q1/Csnub_Q1                      ; snubbers (drop)
#   S2 sw 0 …                              ; Q2.D=sw Q2.S=0
#   D2 0 sw DIDEAL                         ; synthetic body diode (drop)
#   Rsnub_Q2/Csnub_Q2                      ; snubbers (drop)
#
#   Vcb_sense vin_dc cb_lo 0               ; ammeter (drop)
#   C_b cb_lo pri_top                      ; DC blocking cap (Cb.1=Vin Cb.2=pri_top)
#   R_cb_esr pri_top pri_top_esr 1m        ; ESR (drop)
#   L_lk pri_top_esr pri_lk                ; leakage/series inductor
#   Evab vab 0 sw pri_top 1                ; differential probe (drop)
#   Vpri_sense pri_lk pri_dot 0            ; ammeter (drop)
#   L_pri pri_dot sw                       ; T1.pri.1=pri_lk T1.pri.2=sw
#
#   L_sec sec_a sec_b                      ; T1.sec0.1=sec_a T1.sec0.2=sec_b
#   K1 L_pri L_sec
#   Vsec_a_sense / Vsec_b_sense            ; ammeters (drop)
#
#   * Full-bridge rectifier:
#   D_r1 sec_a_d out_rect                  ; D_r1.A=sec_a D_r1.K=out_rect
#   D_r2 sec_b_d out_rect                  ; D_r2.A=sec_b D_r2.K=out_rect
#   D_r3 out_gnd sec_a_d                   ; D_r3.A=GND   D_r3.K=sec_a
#   D_r4 out_gnd sec_b_d                   ; D_r4.A=GND   D_r4.K=sec_b
#   L_o out_rect out_node                  ; → L_out0
#   R_lo_dcr / R_co_esr                    ; parasitics (drop)
#   C_o co_top out_gnd                     ; → C_out0
#   R_load / Vout_sense                    ; drop
#
# Mapping to TAS:
#   inverter:        Q1, Q2, C_b, L_lk     dcBus Vin → 2× hfAc (pri_lk, sw)
#   isolation:       T1 (pri, sec0)        hfAc pri_lk → 2× winding (sec_a, sec_b)
#   outputRectifier: D_r1..D_r4, L_out0, C_out0   2× winding → dcOutput Vout0
#   control:         U1 drives {Q1, Q2}, senses Vout0


_AHB_REAL_KINDS = {
    "S1":     "switch",
    "S2":     "switch",
    "C_b":    "capacitor",     # DC blocking cap (Imbertson-Mohan)
    # NOTE: L_lk (primary leakage / ZVS inductor) is intentionally absent
    # from the real-set. MKF emits it (with a real 1µH value) but does
    # NOT dispatch a designer for it — there's no extra-component slot,
    # and the bridge only supports one main + N named extras. Until a
    # binding strategy lands (e.g. ``seriesInductor`` extras-role or a
    # T1 leakage-as-winding spec), the stencil drops L_lk and treats it
    # as transformer leakage absorbed into T1. Tracked in BACKLOG.
    "L_pri":  "inductor",      # → T1.pri
    "L_sec":  "inductor",      # → T1.sec0
    "K1":     "coupling",      # → T1 coupling
    "D_r1":   "diode",         # full-bridge rectifier
    "D_r2":   "diode",
    "D_r3":   "diode",
    "D_r4":   "diode",
    "L_o":    "inductor",      # → L_out0
    "C_o":    "capacitor",     # → C_out0
}


def asymmetric_half_bridge(deck: SpiceDeck) -> TasTopology:
    """Decompose an MKF asymmetric half-bridge deck (switch-mode) into TAS.

    Requires ``bridge_simulation_mode="switch"`` and
    ``rectifierType="fullBridge"`` (centerTapped doubles internal turns
    ratios silently — confirmed against the MKF test fixture).
    """
    _validate_real_set(
        deck,
        "asymmetric_half_bridge",
        _AHB_REAL_KINDS,
        # Topology-local scaffolding:
        # - D1, D2: synthetic body diodes for S1/S2 (collide with real
        #   D1 in buck/flyback, so cannot live in global _TESTBENCH_EXACT).
        # - L_lk: MKF emits a real 1µH leakage inductor but has no
        #   designer dispatch for it; absorbed into T1 leakage for now
        #   (see _AHB_REAL_KINDS comment).
        extra_testbench=frozenset({"D1", "D2", "L_lk"}),
    )

    inverter = {
        "name": "inverter",
        "role": "inverter",
        "inputPort":  {"type": "dcBus", "wire": "Vin"},
        "outputPorts": [
            {"type": "hfAc", "wire": "pri_top", "name": "pri_top"},
            {"type": "hfAc", "wire": "sw",      "name": "sw"},
        ],
        "circuit": {
            "components": [
                _component("mosfet",    "Q1"),    # ← S1 (high-side)
                _component("mosfet",    "Q2"),    # ← S2 (low-side)
                _component("capacitor", "C_b"),   # ← C_b (DC blocking)
            ],
            "connections": [],
        },
    }

    isolation = {
        "name": "isolation",
        "role": "isolation",
        "inputPort": {"type": "hfAc", "wire": "pri_top"},
        "outputPorts": [
            {"type": "winding", "wire": "sec_a", "name": "sec_a"},
            {"type": "winding", "wire": "sec_b", "name": "sec_b"},
        ],
        "circuit": {
            "components": [_t1_component(("pri", "sec0"))],
            "connections": [],
        },
    }

    output_rectifier_0 = {
        "name": "output_0",
        "role": "outputRectifier",
        "inputPort":  {"type": "winding",  "wire": "sec_a"},
        "outputPorts": [{"type": "dcOutput", "wire": "Vout0"}],
        "circuit": {
            "components": [
                _component("diode",     "D1"),       # ← D_r1
                _component("diode",     "D2"),       # ← D_r2
                _component("diode",     "D3"),       # ← D_r3
                _component("diode",     "D4"),       # ← D_r4
                _component("magnetic",  "L_out0"),   # ← L_o
                _component("capacitor", "C_out0"),   # ← C_o
            ],
            "connections": [
                {
                    "name": "out_rect",
                    "kind": "wire",
                    "endpoints": [
                        {"component": "D1",     "pin": "K"},
                        {"component": "D2",     "pin": "K"},
                        {"component": "L_out0", "pin": "1"},
                    ],
                },
            ],
        },
    }

    control = _isolated_control_stage(("Q1", "Q2"))

    inter_stage = [
        {
            "name": "Vin",
            "kind": "externalPort",
            "direction": "input",
            "endpoints": [
                {"component": "Q1",  "pin": "D"},
                {"component": "C_b", "pin": "1"},
            ],
        },
        {
            # T1.pri.1 wires to C_b.2 directly (L_lk leakage absorbed
            # into T1 model — see _AHB_REAL_KINDS comment).
            "name": "pri_top",
            "kind": "wire",
            "endpoints": [
                {"component": "C_b", "pin": "2"},
                {"component": "T1",  "pin": "pri.1"},
            ],
        },
        {
            # T1.pri.2 returns to the half-bridge midpoint sw.
            "name": "sw",
            "kind": "wire",
            "endpoints": [
                {"component": "Q1", "pin": "S"},
                {"component": "Q2", "pin": "D"},
                {"component": "T1", "pin": "pri.2"},
            ],
        },
        {
            "name": "sec_a",
            "kind": "wire",
            "endpoints": [
                {"component": "T1", "pin": "sec0.1"},
                {"component": "D1", "pin": "A"},
                {"component": "D3", "pin": "K"},
            ],
        },
        {
            "name": "sec_b",
            "kind": "wire",
            "endpoints": [
                {"component": "T1", "pin": "sec0.2"},
                {"component": "D2", "pin": "A"},
                {"component": "D4", "pin": "K"},
            ],
        },
        {
            "name": "Vout0",
            "kind": "externalPort",
            "direction": "output",
            "endpoints": [
                {"component": "L_out0", "pin": "2"},
                {"component": "C_out0", "pin": "1"},
            ],
        },
        _gnd_wire(
            ("Q2",     "S"),
            ("D3",     "A"),
            ("D4",     "A"),
            ("C_out0", "2"),
        ),
        *_gate_wires("Q1", "Q2"),
    ]

    return {
        "stages": [inverter, isolation, output_rectifier_0, control],
        "interStageCircuit": inter_stage,
    }


STENCILS: dict[str, Callable[[SpiceDeck], TasTopology]] = {
    "buck": buck,
    "boost": boost,
    "cuk": cuk,
    "sepic": sepic,
    "zeta": zeta,
    "four_switch_buck_boost": four_switch_buck_boost,
    "flyback": flyback,
    "single_switch_forward": single_switch_forward,
    "two_switch_forward": two_switch_forward,
    "active_clamp_forward": active_clamp_forward,
    "isolated_buck": isolated_buck,
    "isolated_buck_boost": isolated_buck_boost,
    "llc": llc,
    "push_pull": push_pull,
    "phase_shifted_full_bridge": phase_shifted_full_bridge,
    "asymmetric_half_bridge": asymmetric_half_bridge,
}


def get_stencil(topology: str) -> Callable[[SpiceDeck], TasTopology]:
    """Return the stencil for ``topology``; raise ``StencilError`` if missing."""
    try:
        return STENCILS[topology]
    except KeyError as exc:
        raise StencilError(
            f"No MKF→TAS stencil implemented yet for topology {topology!r}. "
            f"Implemented: {sorted(STENCILS)}. Add one to heaviside/decomposer/stencils.py."
        ) from exc


# Silence "imported but unused" linting on SpiceElement re-export.
_ = SpiceElement
