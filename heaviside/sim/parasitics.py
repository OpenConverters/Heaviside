"""Inject real component parasitics into a SPICE deck.

After ``assemble_bom_from_tas`` stamps real MPNs into the TAS dict,
this module patches the netlist text so the final simulation uses
Rds_on (MOSFET), Vf/RS (diode), and ESR (capacitor) from the
selected parts instead of ideal models.

Parasitic injection is intentionally text-level (regex on the deck
string) because MKF's SPICE output is a flat text file with
predictable refdes naming conventions. A full SPICE AST is overkill
for the three substitutions we need.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


# ---------------------------------------------------------------------------
# SW model: inject RON from selected MOSFET Rds_on
# ---------------------------------------------------------------------------
#
# MKF decks emit:   .model SW1 SW VT=2.500000 VH=0.500000
# ngspice's SW model supports RON (on-resistance) and ROFF (off-resistance).
# Default RON = 1.0 Ω (unrealistic). Injecting the real Rds_on captures
# conduction losses in the simulation.

_SW_MODEL_RE = re.compile(
    r"^(\s*\.model\s+SW\d+\s+SW\b)(.*?)$",
    re.IGNORECASE | re.MULTILINE,
)

_RON_PARAM_RE = re.compile(r"\bRON\s*=\s*[\d.eE+-]+", re.IGNORECASE)


def _inject_mosfet_ron(deck: str, rds_on: float) -> str:
    """Add or replace RON=<rds_on> on every SW model line."""
    def _replace(m: re.Match[str]) -> str:
        prefix = m.group(1)
        rest = m.group(2)
        if _RON_PARAM_RE.search(rest):
            rest = _RON_PARAM_RE.sub(f"RON={rds_on:.6e}", rest)
        else:
            rest = rest.rstrip() + f" RON={rds_on:.6e}"
        return prefix + rest
    return _SW_MODEL_RE.sub(_replace, deck)


# ---------------------------------------------------------------------------
# Diode model: inject RS from selected diode Vf
# ---------------------------------------------------------------------------
#
# MKF decks emit:   .model DIDEAL D(IS=1e-14 RS=1e-6)
# RS is the series resistance. For a real diode, Vf ≈ Vj + Id_avg * RS.
# At low currents the junction drop Vj ≈ 0.3–0.7 V dominates; RS adds
# conduction loss at higher currents. We set RS = Vf / If_avg (a linear
# approximation valid near the operating point) clamped to [1e-3, 10] Ω.
# This is more accurate than the default RS=1e-6 (ideal) while keeping
# the simple DIDEAL model that converges reliably.

_DIDEAL_MODEL_RE = re.compile(
    r"^(\s*\.model\s+DIDEAL\s+D\()([^)]*)\)",
    re.IGNORECASE | re.MULTILINE,
)

_RS_PARAM_RE = re.compile(r"\bRS\s*=\s*[\d.eE+-]+", re.IGNORECASE)


def _inject_diode_rs(deck: str, vf: float, if_avg: float) -> str:
    """Replace RS on every DIDEAL model line with Vf/If_avg."""
    if if_avg <= 0:
        return deck
    rs = max(1e-3, min(10.0, vf / if_avg))

    def _replace(m: re.Match[str]) -> str:
        prefix = m.group(1)
        params = m.group(2)
        if _RS_PARAM_RE.search(params):
            params = _RS_PARAM_RE.sub(f"RS={rs:.6e}", params)
        else:
            params = params.rstrip() + f" RS={rs:.6e}"
        return prefix + params + ")"
    return _DIDEAL_MODEL_RE.sub(_replace, deck)


# ---------------------------------------------------------------------------
# Capacitor ESR: add series resistance to output caps
# ---------------------------------------------------------------------------
#
# MKF decks emit:   Cout vout 0 1e-4 IC=12
# Some stencils already have Rco_esr; most don't. For those that don't,
# we split the cap node: insert an ESR resistor between the original
# node and a new internal node, then move the cap to the internal node.
#
# Before:  Cout vout 0 1e-4 IC=12
# After:   Resr_cout vout vout_esr 0.005
#          Cout vout_esr 0 1e-4 IC=12
#
# Only targets Cout* (output caps). Input caps and flying caps are less
# critical for the realism gate's efficiency/regulation checks.

_COUT_RE = re.compile(
    r"^(\s*)(Cout\w*)\s+(\S+)\s+(\S+)\s+([\d.eE+-]+)(.*?)$",
    re.IGNORECASE | re.MULTILINE,
)

# Check if an ESR resistor already exists for a given cap
_RESR_PREFIX_RE = re.compile(
    r"^\s*R(?:esr_|co_esr|out.*esr)\S*\s+",
    re.IGNORECASE | re.MULTILINE,
)


def _inject_cap_esr(deck: str, esr: float) -> str:
    """Add ESR resistors to Cout* lines that don't already have one."""
    if _RESR_PREFIX_RE.search(deck):
        return _update_existing_esr(deck, esr)

    def _replace(m: re.Match[str]) -> str:
        indent = m.group(1)
        refdes = m.group(2)
        node_a = m.group(3)
        node_b = m.group(4)
        cap_val = m.group(5)
        rest = m.group(6)
        esr_node = f"{node_a}_esr"
        esr_line = f"{indent}Resr_{refdes} {node_a} {esr_node} {esr:.6e}"
        cap_line = f"{indent}{refdes} {esr_node} {node_b} {cap_val}{rest}"
        return esr_line + "\n" + cap_line

    return _COUT_RE.sub(_replace, deck)


_EXISTING_ESR_RE = re.compile(
    r"^(\s*R(?:esr_|co_esr|out.*esr)\S*\s+\S+\s+\S+\s+)([\d.eE+-]+)",
    re.IGNORECASE | re.MULTILINE,
)


def _update_existing_esr(deck: str, esr: float) -> str:
    """Update existing ESR resistor values."""
    return _EXISTING_ESR_RE.sub(
        lambda m: f"{m.group(1)}{esr:.6e}",
        deck,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def inject_parasitics(
    deck: str,
    tas: Mapping[str, Any],
) -> str:
    """Patch ``deck`` with real component parasitics from ``tas``.

    Walks the TAS component tree looking for selection_provenance
    stamps (written by ``assemble_bom_from_tas``). For each stamped
    component, injects the appropriate parasitic into the deck.

    Returns the patched deck string. If no components are stamped,
    returns the deck unchanged.
    """
    rds_on: float | None = None
    vf: float | None = None
    if_avg: float | None = None
    esr: float | None = None

    for stage in tas.get("topology", {}).get("stages", []):
        for comp in stage.get("circuit", {}).get("components", []):
            if not isinstance(comp, Mapping):
                continue
            prov = comp.get("selection_provenance")
            if not isinstance(prov, Mapping):
                continue
            cat = prov.get("category")
            if cat == "mosfet" and rds_on is None:
                r = comp.get("rds_on")
                if isinstance(r, (int, float)) and r > 0:
                    rds_on = float(r)
            elif cat == "diode" and vf is None:
                v = comp.get("vf_typ")
                i = comp.get("if_avg_stress")
                if (isinstance(v, (int, float)) and v > 0
                        and isinstance(i, (int, float)) and i > 0):
                    vf = float(v)
                    if_avg = float(i)
            elif cat == "capacitor" and esr is None:
                e = comp.get("esr")
                if isinstance(e, (int, float)) and e > 0:
                    esr = float(e)

    patched = deck
    if rds_on is not None:
        patched = _inject_mosfet_ron(patched, rds_on)
    if vf is not None and if_avg is not None:
        patched = _inject_diode_rs(patched, vf, if_avg)
    if esr is not None:
        patched = _inject_cap_esr(patched, esr)

    return patched
