"""CRE virtual test bench: rebuild a reference design and simulate it.

Takes an extracted BOM + spec from a reference design PDF, builds the
actual converter circuit using the reference's component values, simulates
it, and compares the results against the PDF's performance claims.

If the simulation doesn't match the claims, it diagnoses why and feeds
lessons back to the teacher for future runs.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from heaviside.pipeline.cre import (
    CREState,
    ComponentRoleMap,
    ReferenceClaims,
    SimComparison,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Role mapping: BOM roles → stencil refdeses
# ---------------------------------------------------------------------------

_ROLE_TO_STENCIL: dict[str, str] = {
    "primarySwitch": "S1",
    "highSideSwitch": "S1",
    "lowSideSwitch": "S2",
    "synchronousRectifier": "S2",
    "mainInductor": "L1",
    "boostInductor": "L1",
    "buckInductor": "L1",
    "outputCapacitor": "Cout",
    "inputCapacitor": "Cin",
    "bootstrapCapacitor": "Cboot",
    "outputRectifier": "D1",
    "freewheelDiode": "D1",
    "boostDiode": "D1",
    "mainTransformer": "L1",
    "controller": "U1",
}

_TOPOLOGY_ALIASES: dict[str, str] = {
    "synchronous_buck": "buck",
    "synchronous buck": "buck",
    "sync_buck": "buck",
    "dual_phase_buck": "buck",
    "dual-phase buck": "buck",
    "half_bridge_llc": "llc",
    "half-bridge llc": "llc",
    "half_bridge": "asymmetric_half_bridge",
    "full_bridge": "phase_shifted_full_bridge",
    "phase_shift_full_bridge": "phase_shifted_full_bridge",
    "psfb": "phase_shifted_full_bridge",
    "quasi_resonant_flyback": "flyback",
    "qr_flyback": "flyback",
    "active_clamp_flyback": "flyback",
    "single_stage_pfc_flyback": "flyback",
    "hybrid_flyback": "flyback",
    "forward": "single_switch_forward",
    "pfc": "power_factor_correction",
    "totem_pole_pfc": "power_factor_correction",
    "bridgeless_pfc": "power_factor_correction",
    "dab": "dual_active_bridge",
}


def _normalize_topology(raw: str) -> str | None:
    """Map an LLM-extracted topology name to a Heaviside canonical name."""
    from heaviside.topologies import TOPOLOGIES
    canonical = {t.name for t in TOPOLOGIES}

    # Direct match
    cleaned = raw.lower().replace(" ", "_").replace("-", "_")
    if cleaned in canonical:
        return cleaned

    # Alias lookup
    if cleaned in _TOPOLOGY_ALIASES:
        return _TOPOLOGY_ALIASES[cleaned]

    # Fuzzy: check if any canonical name is a substring
    for name in canonical:
        if name in cleaned or cleaned in name:
            return name

    # Multi-stage topologies (e.g. "pfc + llc"): try the last stage
    if "+" in raw or "," in raw:
        parts = re.split(r"[+,]", raw)
        for part in reversed(parts):
            result = _normalize_topology(part.strip())
            if result:
                return result

    return None


_STENCIL_COMPONENT_TYPES = {
    "S1": "mosfet", "S2": "mosfet",
    "D1": "diode", "D2": "diode",
    "L1": "inductor", "L2": "inductor",
    "Cout": "capacitor", "Cin": "capacitor",
}


def build_role_map(
    ref_bom: list[dict[str, Any]],
    topology: str,
) -> ComponentRoleMap:
    """Map BOM component roles to stencil refdeses."""
    roles: dict[str, str] = {}
    unmapped: list[str] = []

    for comp in ref_bom:
        ref_des = comp.get("ref_des", "")
        role = comp.get("role", "")
        if not ref_des or not role:
            unmapped.append(ref_des or "?")
            continue

        stencil_ref = _ROLE_TO_STENCIL.get(role)
        if stencil_ref:
            roles[ref_des] = stencil_ref
        else:
            unmapped.append(ref_des)

    return ComponentRoleMap(
        roles=roles,
        confidence=len(roles) / max(len(ref_bom), 1),
        unmapped=unmapped,
    )


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------

_SI_PREFIXES = {
    "T": 1e12, "G": 1e9, "M": 1e6, "k": 1e3, "K": 1e3,
    "m": 1e-3, "u": 1e-6, "µ": 1e-6, "μ": 1e-6,
    "n": 1e-9, "p": 1e-12,
}

_VALUE_RE = re.compile(
    r"^([\d.]+)\s*([TGMkKmuµμnp]?)\s*([FHΩRVAohm]*)",
    re.IGNORECASE,
)


def parse_component_value(value_str: str) -> float | None:
    """Parse engineering notation: '4.7uH' → 4.7e-6, '22uF' → 22e-6."""
    if not value_str:
        return None
    try:
        return float(value_str)
    except ValueError:
        pass
    m = _VALUE_RE.match(value_str.strip())
    if not m:
        return None
    try:
        num = float(m.group(1))
    except ValueError:
        return None
    prefix = m.group(2)
    multiplier = _SI_PREFIXES.get(prefix, 1.0)
    return num * multiplier


# ---------------------------------------------------------------------------
# Netlist patching
# ---------------------------------------------------------------------------

def _rewrite_component_value(deck: str, refdes: str, new_value: float) -> str:
    """Replace the numeric value of a two-terminal component (L/C/R)."""
    pattern = re.compile(
        rf"^(\s*{re.escape(refdes)}\s+\S+\s+\S+\s+)([\d.eE+\-]+)(.*?)$",
        re.MULTILINE | re.IGNORECASE,
    )
    new_deck, count = pattern.subn(rf"\g<1>{new_value:.6e}\3", deck)
    if count == 0:
        logger.warning("testbench: refdes %s not found in deck", refdes)
    return new_deck


def _rewrite_vin(deck: str, new_vin: float) -> str:
    """Replace the Vin DC source value."""
    pattern = re.compile(
        r"^(\s*Vin\s+\S+\s+\S+\s+)([\d.eE+\-]+)(.*?)$",
        re.MULTILINE | re.IGNORECASE,
    )
    return pattern.sub(rf"\g<1>{new_vin}\3", deck)


def _rewrite_fsw(deck: str, new_fsw: float) -> str:
    """Rewrite the PWM PULSE period to match the reference fsw."""
    if new_fsw <= 0:
        return deck
    new_period = 1.0 / new_fsw
    pattern = re.compile(
        r"(PULSE\s*\([^)]*?\s)([\d.eE+\-]+)(\s+[\d.eE+\-]+\s*\))",
        re.IGNORECASE,
    )

    def _sub(m: re.Match) -> str:
        parts = m.group(0)
        # PULSE(V1 V2 Td Tr Tf Ton Tper) — Tper is the last number
        nums = re.findall(r"[\d.eE+\-]+", parts)
        if len(nums) >= 7:
            old_period = float(nums[6])
            old_ton = float(nums[5])
            duty = old_ton / old_period if old_period > 0 else 0.5
            new_ton = duty * new_period
            parts = parts.replace(nums[5], f"{new_ton:.6e}", 1)
            parts = parts.replace(nums[6], f"{new_period:.6e}", 1)
        return parts

    return pattern.sub(_sub, deck)


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

_EFFICIENCY_TOLERANCE_PP = 5.0
_VOUT_TOLERANCE_PCT = 5.0


def _build_comparison(
    sim_result: Any,
    ref_claims: ReferenceClaims,
    ref_spec: Any,
) -> SimComparison:
    """Compare simulation results against reference claims."""
    sim_eff = getattr(sim_result, "efficiency", 0.0) or 0.0
    claimed_eff = 0.0
    if ref_claims.efficiency:
        claimed_eff = max(ref_claims.efficiency.values()) if ref_claims.efficiency else 0.0
    elif ref_spec and ref_spec.efficiency_target:
        claimed_eff = ref_spec.efficiency_target

    eff_delta = abs(sim_eff - claimed_eff) * 100 if claimed_eff else 0.0

    sim_vout = getattr(sim_result, "vout", 0.0) or 0.0
    claimed_vout = ref_claims.vout_measured or (ref_spec.vout if ref_spec else 0.0)
    vout_err = abs(sim_vout - claimed_vout) / claimed_vout * 100 if claimed_vout else 0.0

    mismatches: list[dict[str, Any]] = []
    if claimed_eff and eff_delta > _EFFICIENCY_TOLERANCE_PP:
        mismatches.append({
            "param": "efficiency",
            "sim": sim_eff, "claimed": claimed_eff,
            "delta_pp": eff_delta,
        })
    if claimed_vout and vout_err > _VOUT_TOLERANCE_PCT:
        mismatches.append({
            "param": "vout",
            "sim": sim_vout, "claimed": claimed_vout,
            "error_pct": vout_err,
        })

    return SimComparison(
        sim_efficiency=sim_eff,
        claimed_efficiency=claimed_eff,
        efficiency_delta_pp=eff_delta,
        sim_vout=sim_vout,
        claimed_vout=claimed_vout or 0.0,
        vout_error_pct=vout_err,
        mismatches=mismatches,
        passed=len(mismatches) == 0,
    )


# ---------------------------------------------------------------------------
# Diagnosis
# ---------------------------------------------------------------------------

def _diagnose_mismatch(comparison: SimComparison, state: CREState) -> str:
    """Generate a diagnosis for simulation mismatches."""
    from heaviside.agents.llm_call import call_agent, LLMCallError

    if not comparison.mismatches:
        return "No mismatches — simulation matches reference claims."

    import json
    diag_input = json.dumps({
        "mismatches": comparison.mismatches,
        "topology": state.ref_spec.topology if state.ref_spec else "?",
        "sim_efficiency": comparison.sim_efficiency,
        "claimed_efficiency": comparison.claimed_efficiency,
        "sim_vout": comparison.sim_vout,
        "claimed_vout": comparison.claimed_vout,
    }, indent=2)

    try:
        diagnosis = call_agent(
            "reviewer",
            f"CRE TESTBENCH DIAGNOSIS — simulation doesn't match reference claims.\n\n"
            f"Diagnose the most likely cause and suggest what to adjust.\n\n"
            f"{diag_input}",
            max_tokens=4096,
        )
        return diagnosis[:500]
    except LLMCallError as exc:
        return f"Diagnosis failed: {exc}"


# ---------------------------------------------------------------------------
# Main testbench
# ---------------------------------------------------------------------------

_MAX_TESTBENCH_LOOPS = 3


def run_testbench(state: CREState) -> CREState:
    """Virtual test bench: rebuild the reference converter and simulate it.

    1. Map BOM components to stencil roles
    2. Build converter spec, generate scaffold netlist via decompose_from_spec
    3. Patch netlist with reference component values
    4. Inject parasitics, simulate
    5. Compare sim vs claims
    6. If mismatch: diagnose, learn, loop
    """
    if not state.ref_spec:
        state.diagnostics.append("testbench: no spec extracted — cannot build converter")
        return state

    spec = state.ref_spec
    if spec.vout <= 0:
        state.diagnostics.append("testbench: Vout=0 — cannot simulate")
        return state

    # 1. Map BOM roles to stencil positions
    role_map = build_role_map(state.ref_bom, spec.topology)
    state.role_map = role_map
    logger.info("testbench: mapped %d/%d BOM components to stencil roles (confidence %.0f%%)",
                 len(role_map.roles), len(state.ref_bom), role_map.confidence * 100)

    if role_map.confidence < 0.1:
        state.diagnostics.append(
            f"testbench: role mapping confidence too low ({role_map.confidence:.0%}), "
            f"unmapped: {role_map.unmapped[:10]}"
        )
        return state

    # 2. Extract inductor value for magnetizing_inductance
    mag_inductance = 100e-6  # default
    for comp in state.ref_bom:
        role = comp.get("role", "")
        if role in ("mainInductor", "boostInductor", "buckInductor", "mainTransformer"):
            val = parse_component_value(str(comp.get("value", "")))
            if val and val > 0:
                mag_inductance = val
                break

    # 3. Build converter spec and generate scaffold
    converter_json = spec.to_heaviside_spec()
    turns_ratios = [spec.turns_ratio] if spec.turns_ratio else [1.0]
    topology = spec.topology.lower().replace(" ", "_").replace("-", "_")

    # Clamp Vin to valid range for the topology (MKF rejects D>1)
    vin_min = converter_json["inputVoltage"]["minimum"]
    vin_nom = converter_json["inputVoltage"]["nominal"]
    vin_max = converter_json["inputVoltage"]["maximum"]
    vout = converter_json["operatingPoints"][0]["outputVoltages"][0]
    if "buck" in topology and vin_min > 0 and vin_min < vout * 1.5:
        converter_json["inputVoltage"]["minimum"] = vout * 1.5
    if vin_nom <= 0:
        converter_json["inputVoltage"]["nominal"] = vin_max * 0.8 if vin_max > 0 else vout * 2
    if vin_max <= 0:
        converter_json["inputVoltage"]["maximum"] = converter_json["inputVoltage"]["nominal"] * 1.2

    topology = _normalize_topology(topology)
    if not topology:
        state.diagnostics.append(
            f"testbench: cannot map topology '{spec.topology}' to a Heaviside stencil"
        )
        return state

    try:
        from heaviside.decomposer.api import decompose_from_spec
        netlist, tas = decompose_from_spec(
            topology, converter_json, turns_ratios, mag_inductance,
        )
    except Exception as exc:
        state.diagnostics.append(f"testbench: decompose failed: {exc}")
        return state

    logger.info("testbench: scaffold netlist generated (%d chars)", len(netlist))

    # 4. Patch netlist with reference BOM values
    for comp in state.ref_bom:
        ref_des = comp.get("ref_des", "")
        stencil_ref = role_map.roles.get(ref_des)
        if not stencil_ref:
            continue
        value = parse_component_value(str(comp.get("value", "")))
        if value and value > 0:
            cat = comp.get("category", comp.get("component_type", ""))
            if cat in ("capacitor", "inductor", "magnetic"):
                netlist = _rewrite_component_value(netlist, stencil_ref, value)

    # Patch Vin and fsw
    vin_nom = spec.vin_nom or spec.vin_max or 12.0
    netlist = _rewrite_vin(netlist, vin_nom)
    if spec.fsw > 0:
        netlist = _rewrite_fsw(netlist, spec.fsw)

    # 5. Inject parasitics and simulate
    try:
        from heaviside.sim import inject_parasitics
        netlist = inject_parasitics(netlist, tas)
    except Exception as exc:
        state.diagnostics.append(f"testbench: parasitic injection failed: {exc}")

    state.netlist = netlist
    state.tas = tas

    for attempt in range(1, _MAX_TESTBENCH_LOOPS + 1):
        state.attempt = attempt
        try:
            from heaviside.sim.runner import simulate_closed_loop, SimError
            sim_result = simulate_closed_loop(netlist, spec.vout)
        except Exception as exc:
            state.diagnostics.append(f"testbench: simulation failed (attempt {attempt}): {exc}")
            break

        state.sim_result = {
            "vin": sim_result.vin, "iin": sim_result.iin,
            "vout": sim_result.vout, "iout": sim_result.iout,
            "pin": sim_result.pin, "pout": sim_result.pout,
            "efficiency": sim_result.efficiency,
            "total_losses": sim_result.total_losses,
        }

        # 6. Compare
        comparison = _build_comparison(sim_result, state.ref_claims, spec)
        state.comparisons.append(comparison)

        logger.info(
            "testbench attempt %d: η_sim=%.1f%% η_claimed=%.1f%% Δ=%.1fpp "
            "Vout_sim=%.2f Vout_claimed=%.2f err=%.1f%% → %s",
            attempt,
            comparison.sim_efficiency * 100 if comparison.sim_efficiency else 0,
            comparison.claimed_efficiency * 100 if comparison.claimed_efficiency else 0,
            comparison.efficiency_delta_pp,
            comparison.sim_vout, comparison.claimed_vout,
            comparison.vout_error_pct,
            "PASS" if comparison.passed else "MISMATCH",
        )

        if comparison.passed:
            state.passed = True
            break

        # 7. Diagnose
        diagnosis = _diagnose_mismatch(comparison, state)
        comparison.diagnosis = diagnosis
        state.diagnostics.append(f"testbench diagnosis (attempt {attempt}): {diagnosis[:200]}")

        # Learn from this attempt
        state.lessons.append({
            "attempt": attempt,
            "mismatches": comparison.mismatches,
            "diagnosis": diagnosis[:200],
        })

    # 8. Store lessons via teacher
    _learn_from_testbench(state)
    return state


def _learn_from_testbench(state: CREState) -> None:
    """Persist testbench lessons to the teacher."""
    try:
        from heaviside.pipeline.teacher import Lesson, store_lessons
    except ImportError:
        return

    import hashlib
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    fingerprint = hashlib.sha256(state.reference.encode()).hexdigest()[:12]

    lessons: list[Lesson] = []
    for lesson_data in state.lessons:
        for mm in lesson_data.get("mismatches", []):
            lessons.append(Lesson(
                id=hashlib.sha256(
                    f"cre-tb:{state.reference}:{mm.get('param','')}:{lesson_data['attempt']}".encode()
                ).hexdigest()[:16],
                timestamp=now,
                topology=state.ref_spec.topology if state.ref_spec else "?",
                category="simulation_failure",
                severity="high",
                detail=f"Testbench mismatch on {mm.get('param','?')}: "
                       f"sim={mm.get('sim')}, claimed={mm.get('claimed')}",
                spec_fingerprint=fingerprint,
                suggestion=lesson_data.get("diagnosis", "")[:200],
            ))

    if lessons:
        written = store_lessons(lessons)
        logger.info("testbench: persisted %d lessons (%d new)", len(lessons), written)


__all__ = [
    "build_role_map",
    "parse_component_value",
    "run_testbench",
]
