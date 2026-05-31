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
import shutil
from dataclasses import dataclass
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
    "polyphase_buck": "buck",
    "polyphase buck": "buck",
    "multi_phase_buck": "buck",
    "interleaved_buck": "buck",
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


def _convert_to_sync_buck(deck: str) -> str:
    """Replace the freewheeling diode with a complementary sync rectifier switch.

    MKF generates a non-sync buck (diode D1). For synchronous buck designs,
    the diode drop (~0.65V) dominates losses. Replace D1 with a second
    voltage-controlled switch driven by an inverted PWM signal.
    """
    if "DIDEAL" not in deck:
        return deck

    # Extract the PULSE parameters from the existing PWM source
    pulse_match = re.search(
        r"PULSE\((\s*[\d.eE+\-]+\s+[\d.eE+\-]+\s+[\d.eE+\-]+\s+"
        r"[\d.eE+\-]+\s+[\d.eE+\-]+\s+)([\d.eE+\-]+)(\s+)([\d.eE+\-]+)\s*\)",
        deck,
    )
    if not pulse_match:
        return deck

    ton = float(pulse_match.group(2))
    tper = float(pulse_match.group(4))
    # Dead time: 2% of period to avoid shoot-through
    t_dead = tper * 0.02
    ton_sr = tper - ton - 2 * t_dead
    if ton_sr <= 0:
        return deck

    # Extract D1 node connections: D1 <anode> <cathode> DIDEAL
    d1_match = re.search(r"^D1\s+(\S+)\s+(\S+)\s+DIDEAL", deck, re.MULTILINE)
    if not d1_match:
        return deck
    anode = d1_match.group(1)   # 0 (ground)
    cathode = d1_match.group(2) # sw node

    # Build the sync rectifier: inverted PWM + SW2 + body diode
    tr_tf = "1.000000e-08"
    sr_block = (
        f"\n* Synchronous Rectifier (replaces D1)\n"
        f"Vpwm_sr pwm_sr_ctrl 0 PULSE(0 5 {ton + t_dead:.6e} "
        f"{tr_tf} {tr_tf} {ton_sr:.6e} {tper:.6e})\n"
        f"S2 {cathode} {anode} pwm_sr_ctrl 0 SW1\n"
        f"Rsnub_s2 {cathode} {anode} 10000.000000\n"
        f"Csnub_s2 {cathode} {anode} 1.000000e-10\n"
        f"* Body diode — freewheels during dead time\n"
        f".model DBODY D(IS=1e-12 RS=0.01 BV=100)\n"
        f"Dbody2 {anode} {cathode} DBODY\n"
    )

    # Remove the diode D1 and its model
    deck = re.sub(r"^\* Freewheeling Diode\n", "", deck, flags=re.MULTILINE)
    deck = re.sub(r"^\.model\s+DIDEAL\s+D\(.*?\)\n", "", deck, flags=re.MULTILINE)
    deck = re.sub(r"^D1\s+.*?\n", sr_block, deck, flags=re.MULTILINE)

    # Add i(Vpwm_sr) to .save if present
    deck = deck.replace(
        "i(Vin_sense)",
        "i(Vin_sense) i(Vpwm_sr)",
    )

    logger.info("testbench: converted to sync buck (D1 → S2)")
    return deck


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


def _inject_waveform_meas(deck: str, vout_target: float) -> str:
    """Add .meas directives and extend the sim window for waveform measurement.

    Extends the .tran window to ensure the LC filter has fully settled
    before measuring ripple. Measures over the last 20 switching cycles.
    """
    # Extract switching frequency from PULSE period
    pulse_match = re.search(r"PULSE\([^)]*\s([\d.eE+\-]+)\s*\)", deck)
    if not pulse_match:
        return deck
    tper = float(pulse_match.group(1))
    if tper <= 0:
        return deck
    fsw = 1.0 / tper

    # Extend sim to 200 cycles for full settling, measure last 20
    n_settle = 200
    n_meas = 20
    t_stop = tper * (n_settle + n_meas)
    t_start = tper * n_settle
    tstep = tper / 50

    # Replace .tran with extended window
    deck = re.sub(
        r"^\.tran\s+.*$",
        f".tran {tstep:.6e} {t_stop:.6e} {t_start:.6e} UIC",
        deck, flags=re.MULTILINE | re.IGNORECASE,
    )
    # Add .ic for output voltage
    if ".ic" not in deck.lower():
        deck = deck.replace(".end", f".ic v(vout)={vout_target}\n.end")

    meas = [
        "",
        "* waveform characterization (CRE testbench)",
        f".meas tran vout_max max v(vout) FROM={t_start:.6e} TO={t_stop:.6e}",
        f".meas tran vout_min min v(vout) FROM={t_start:.6e} TO={t_stop:.6e}",
        f".meas tran vsw_max max v(sw) FROM={t_start:.6e} TO={t_stop:.6e}",
        f".meas tran vsw_min min v(sw) FROM={t_start:.6e} TO={t_stop:.6e}",
        f".meas tran il_max max i(Vl_sense) FROM={t_start:.6e} TO={t_stop:.6e}",
        f".meas tran il_min min i(Vl_sense) FROM={t_start:.6e} TO={t_stop:.6e}",
        "",
    ]
    lines = deck.splitlines()
    out: list[str] = []
    for line in lines:
        if line.strip().lower() == ".end":
            out.extend(meas)
        out.append(line)
    return "\n".join(out) + "\n"


def _parse_waveform_meas(stdout: str) -> dict[str, float]:
    """Parse waveform .meas results from ngspice output."""
    results: dict[str, float] = {}
    pattern = re.compile(r"^\s*(vout_max|vout_min|vsw_max|vsw_min|il_max|il_min)\s*=\s*([-+]?[\d.]+(?:[eE][-+]?\d+)?)")
    for line in stdout.splitlines():
        m = pattern.match(line)
        if m:
            results[m.group(1)] = float(m.group(2))
    return results


@dataclass
class WaveformCharacteristics:
    """Waveform measurements from simulation."""
    vout_ripple_mv: float = 0.0
    vsw_vpp: float = 0.0
    il_ripple_a: float = 0.0
    il_avg_a: float = 0.0


@dataclass
class WaveformAnalytical:
    """Analytically expected waveform values from specs + BOM."""
    il_ripple_a: float = 0.0
    vout_ripple_mv: float = 0.0
    vsw_vpp: float = 0.0


def _compute_analytical_waveforms(
    spec: Any,
    inductance: float,
    cout: float,
    topology: str,
) -> WaveformAnalytical:
    """Compute expected ripple from component values and operating point."""
    vin = spec.vin_nom or spec.vin_max or 12.0
    vout = spec.vout
    fsw = spec.fsw
    if vin <= 0 or vout <= 0 or fsw <= 0 or inductance <= 0:
        return WaveformAnalytical()

    if "buck" in topology:
        d = vout / vin
        il_ripple = vin * d * (1 - d) / (inductance * fsw)
        vsw_vpp = vin
    elif "boost" in topology:
        d = 1 - vin / vout if vout > vin else 0.5
        il_ripple = vin * d / (inductance * fsw)
        vsw_vpp = vout
    else:
        il_ripple = vin * 0.5 / (inductance * fsw)
        vsw_vpp = vin

    vout_ripple_mv = 0.0
    if cout > 0:
        vout_ripple_mv = il_ripple / (8 * fsw * cout) * 1000

    return WaveformAnalytical(
        il_ripple_a=il_ripple,
        vout_ripple_mv=vout_ripple_mv,
        vsw_vpp=vsw_vpp,
    )


_WAVEFORM_RATIO_BOUND = 0.5  # sim must be within 0.5× to 2× of analytical


def _check_waveforms(
    sim: WaveformCharacteristics,
    analytical: WaveformAnalytical,
) -> list[dict[str, Any]]:
    """Compare sim waveforms against analytical, flag if outside 0.5×–2×."""
    issues: list[dict[str, Any]] = []
    checks = [
        ("IL_ripple", sim.il_ripple_a, analytical.il_ripple_a, "A"),
        ("Vout_ripple", sim.vout_ripple_mv, analytical.vout_ripple_mv, "mV"),
        ("Vsw_pp", sim.vsw_vpp, analytical.vsw_vpp, "V"),
    ]
    for name, sim_val, ana_val, unit in checks:
        if ana_val <= 0 or sim_val <= 0:
            continue
        ratio = sim_val / ana_val
        passed = _WAVEFORM_RATIO_BOUND <= ratio <= 1 / _WAVEFORM_RATIO_BOUND
        issues.append({
            "param": name,
            "sim": round(sim_val, 3),
            "analytical": round(ana_val, 3),
            "ratio": round(ratio, 2),
            "unit": unit,
            "passed": passed,
        })
        if not passed:
            logger.warning(
                "testbench waveform %s: sim=%.3f%s analytical=%.3f%s "
                "ratio=%.2f — outside [%.1f×, %.1f×]",
                name, sim_val, unit, ana_val, unit, ratio,
                _WAVEFORM_RATIO_BOUND, 1 / _WAVEFORM_RATIO_BOUND,
            )
    return issues


def _extract_waveforms(netlist: str, vout_target: float) -> WaveformCharacteristics | None:
    """Run simulation with waveform measurements and extract characteristics."""
    import subprocess, tempfile, os

    deck = _inject_waveform_meas(netlist, vout_target)

    ngspice = shutil.which("ngspice")
    if not ngspice:
        return None

    with tempfile.NamedTemporaryFile(mode="w", suffix=".cir", delete=False) as f:
        f.write(deck)
        cir_path = f.name
    try:
        result = subprocess.run(
            [ngspice, "-b", cir_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        meas = _parse_waveform_meas(result.stdout)
    except Exception:
        return None
    finally:
        os.unlink(cir_path)

    vout_max = meas.get("vout_max", 0)
    vout_min = meas.get("vout_min", 0)
    vsw_max = meas.get("vsw_max", 0)
    vsw_min = meas.get("vsw_min", 0)
    il_max = meas.get("il_max", 0)
    il_min = meas.get("il_min", 0)

    return WaveformCharacteristics(
        vout_ripple_mv=(vout_max - vout_min) * 1000,
        vsw_vpp=vsw_max - vsw_min,
        il_ripple_a=il_max - il_min,
        il_avg_a=(il_max + il_min) / 2,
    )


def _rewrite_rload(deck: str, new_rload: float) -> str:
    """Replace the Rload value to set a different output current."""
    pattern = re.compile(
        r"^(\s*Rload\s+\S+\s+\S+\s+)([\d.eE+\-]+)(.*?)$",
        re.MULTILINE | re.IGNORECASE,
    )
    return pattern.sub(rf"\g<1>{new_rload:.6f}\3", deck)


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

# Ideal-switch SPICE sim vs real bench measurement gap is 10-15pp
# (no Rds_on, no gate drive, no PCB trace R, no core losses).
_EFFICIENCY_TOLERANCE_PP = 15.0
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

    # No claims at all = no meaningful comparison
    if not claimed_eff and not claimed_vout:
        mismatches.append({
            "param": "no_claims",
            "note": "no efficiency or Vout claims extracted — cannot validate",
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


def _build_load_points(
    claims: ReferenceClaims,
    spec: Any,
) -> list[dict[str, Any]]:
    """Build a list of load points to simulate from efficiency claims.

    Each entry has: label, iout, efficiency (claimed).
    """
    points: list[dict[str, Any]] = []
    iout_max = spec.iout if spec else 1.0

    for label, eff in (claims.efficiency or {}).items():
        # Parse load percentage from label
        pct_match = re.search(r"([\d.]+)\s*%", label)
        if pct_match:
            pct = float(pct_match.group(1)) / 100.0
            iout = iout_max * pct
        elif "full" in label.lower():
            iout = iout_max
        else:
            iout = iout_max
        points.append({"label": label, "iout": max(iout, 0.01), "efficiency": eff})

    if not points and iout_max > 0:
        points.append({
            "label": "full_load",
            "iout": iout_max,
            "efficiency": claims.efficiency.get("full_load", 0),
        })

    return sorted(points, key=lambda p: p["iout"])


def _build_converter_json(state: CREState) -> tuple[dict[str, Any], str] | None:
    """Build the MKF converter spec dict and normalize topology.

    Returns (converter_json, topology) or None on failure.
    """
    spec = state.ref_spec
    if not spec or spec.vout <= 0:
        state.diagnostics.append("testbench: no spec or Vout=0 — cannot simulate")
        return None

    converter_json = spec.to_heaviside_spec()
    topology = spec.topology.lower().replace(" ", "_").replace("-", "_")

    vin_min = converter_json["inputVoltage"]["minimum"]
    vin_nom = converter_json["inputVoltage"]["nominal"]
    vin_max = converter_json["inputVoltage"]["maximum"]
    vout = converter_json["operatingPoints"][0]["outputVoltages"][0]

    if vin_nom <= 0 and vin_max <= 0:
        state.diagnostics.append(
            f"testbench: Vin_nom={vin_nom} and Vin_max={vin_max} — "
            f"both zero or negative. Cannot simulate."
        )
        return None
    if vin_nom <= 0:
        converter_json["inputVoltage"]["nominal"] = vin_max * 0.8
        state.diagnostics.append(
            f"testbench: Vin_nom not extracted, using Vin_max×0.8 = "
            f"{converter_json['inputVoltage']['nominal']:.1f}V"
        )
    if vin_max <= 0:
        converter_json["inputVoltage"]["maximum"] = vin_nom * 1.2
    if vin_min <= 0:
        converter_json["inputVoltage"]["minimum"] = (
            converter_json["inputVoltage"]["nominal"] * 0.8
        )

    if "buck" in topology:
        vin_for_sim = converter_json["inputVoltage"]["minimum"]
        if vin_for_sim < vout * 1.2:
            converter_json["inputVoltage"]["minimum"] = (
                converter_json["inputVoltage"]["nominal"]
            )
            state.diagnostics.append(
                f"testbench: Vin_min={vin_min:.1f}V < Vout×1.2={vout*1.2:.1f}V — "
                f"simulating at Vin_nom instead (MKF does not model 100% duty)."
            )

    norm = _normalize_topology(topology)
    if not norm:
        state.diagnostics.append(
            f"testbench: cannot map topology '{spec.topology}' to a stencil"
        )
        return None
    return converter_json, norm


def _simulate_netlist(
    netlist: str,
    vout_target: float,
    ref_claims: ReferenceClaims,
    ref_spec: Any,
    label: str,
) -> tuple[Any, SimComparison] | None:
    """Simulate a netlist and build a comparison against claims.

    Returns (sim_result, comparison) or None on sim failure.
    """
    try:
        from heaviside.sim.runner import simulate_closed_loop
        sim_result = simulate_closed_loop(netlist, vout_target=vout_target)
    except Exception as exc:
        logger.warning("testbench [%s]: simulation failed: %s", label, exc)
        return None

    comparison = _build_comparison(sim_result, ref_claims, ref_spec)
    logger.info(
        "testbench [%s]: η_sim=%.1f%% η_claimed=%.1f%% Δ=%.1fpp "
        "Vout_sim=%.2f Vout_claimed=%.2f err=%.1f%% → %s",
        label,
        comparison.sim_efficiency * 100 if comparison.sim_efficiency else 0,
        comparison.claimed_efficiency * 100 if comparison.claimed_efficiency else 0,
        comparison.efficiency_delta_pp,
        comparison.sim_vout, comparison.claimed_vout,
        comparison.vout_error_pct,
        "PASS" if comparison.passed else "MISMATCH",
    )
    return sim_result, comparison


def run_testbench(state: CREState) -> CREState:
    """Virtual test bench: two-phase simulation.

    Phase 1 — Theoretical: decompose from spec with MKF's ideal
    component values. This validates that the topology + operating point
    produce physically reasonable efficiency before touching real BOM data.

    Phase 2 — Real BOM: patch the scaffold netlist with actual component
    values from the reference design's BOM (inductor, capacitor values),
    inject parasitics, and re-simulate. This is the "virtual replica" of
    the real board.

    Comparing both phases against PDF claims tells us how much of any gap
    is topology-inherent vs component-specific.
    """
    result = _build_converter_json(state)
    if result is None:
        return state
    converter_json, topology = result
    spec = state.ref_spec
    assert spec is not None

    # Map BOM roles to stencil positions
    role_map = build_role_map(state.ref_bom, spec.topology)
    state.role_map = role_map
    logger.info(
        "testbench: mapped %d/%d BOM components to stencil roles (confidence %.0f%%)",
        len(role_map.roles), len(state.ref_bom), role_map.confidence * 100,
    )

    # Extract inductor value for magnetizing_inductance
    mag_inductance = 100e-6
    for comp in state.ref_bom:
        role = comp.get("role", "")
        if role in ("mainInductor", "boostInductor", "buckInductor", "mainTransformer"):
            val = parse_component_value(str(comp.get("value", "")))
            if val and val > 0:
                mag_inductance = val
                break

    turns_ratios = [spec.turns_ratio] if spec.turns_ratio else [1.0]

    # ------------------------------------------------------------------
    # Phase 1: Theoretical — ideal components from MKF
    # ------------------------------------------------------------------
    try:
        from heaviside.decomposer.api import decompose_from_spec
        netlist_ideal, tas = decompose_from_spec(
            topology, converter_json, turns_ratios, mag_inductance,
        )
    except Exception as exc:
        state.diagnostics.append(f"testbench: decompose failed: {exc}")
        return state

    logger.info("testbench: scaffold netlist generated (%d chars)", len(netlist_ideal))

    # For synchronous buck/boost: replace the diode with a sync rectifier.
    # Detect from: topology name, BOM roles, or PDF text.
    raw_topo = spec.topology.lower()
    has_sync_role = any(
        comp.get("role", "") in ("synchronousRectifier", "lowSideSwitch")
        for comp in state.ref_bom
    )
    pdf_says_sync = "synchronous" in (state.pdf_text or "").lower()
    is_sync = (
        "synchronous" in raw_topo
        or "sync" in raw_topo
        or has_sync_role
        or pdf_says_sync
    )
    if is_sync and "buck" in topology:
        netlist_ideal = _convert_to_sync_buck(netlist_ideal)

    phase1 = _simulate_netlist(
        netlist_ideal, spec.vout, state.ref_claims, spec, "ideal",
    )
    if phase1 is None:
        state.diagnostics.append("testbench: ideal simulation failed")
        return state

    sim_ideal, comp_ideal = phase1
    state.comparisons.append(comp_ideal)
    state.sim_result = {
        "phase": "ideal",
        "vin": sim_ideal.vin, "iin": sim_ideal.iin,
        "vout": sim_ideal.vout, "iout": sim_ideal.iout,
        "pin": sim_ideal.pin, "pout": sim_ideal.pout,
        "efficiency": sim_ideal.efficiency,
        "total_losses": sim_ideal.total_losses,
    }

    # If even the ideal sim is wildly off (>30pp), something is wrong
    # with the spec extraction — don't proceed to BOM patching.
    if comp_ideal.efficiency_delta_pp > 30 and comp_ideal.claimed_efficiency > 0:
        state.diagnostics.append(
            f"testbench: ideal sim η={sim_ideal.efficiency:.1%} vs "
            f"claimed {comp_ideal.claimed_efficiency:.1%} — "
            f"Δ={comp_ideal.efficiency_delta_pp:.0f}pp is too large. "
            f"Likely a spec extraction error (wrong Vin/Vout/topology)."
        )
        _learn_from_testbench(state)
        return state

    # ------------------------------------------------------------------
    # Phase 2: Real BOM — patch with actual component values
    # ------------------------------------------------------------------
    netlist_bom = netlist_ideal

    patched = 0
    for comp in state.ref_bom:
        ref_des = comp.get("ref_des", "")
        stencil_ref = role_map.roles.get(ref_des)
        if not stencil_ref:
            continue
        value = parse_component_value(str(comp.get("value", "")))
        if value and value > 0:
            cat = comp.get("category", comp.get("component_type", ""))
            if cat in ("capacitor", "inductor", "magnetic"):
                netlist_bom = _rewrite_component_value(
                    netlist_bom, stencil_ref, value,
                )
                patched += 1

    # Vin and fsw are already correct from decompose_from_spec — the
    # BOM phase only patches component values (L, C), not operating point.

    # Inject parasitics from TAS
    try:
        from heaviside.sim import inject_parasitics
        netlist_bom = inject_parasitics(netlist_bom, tas)
    except Exception as exc:
        state.diagnostics.append(f"testbench: parasitic injection failed: {exc}")

    logger.info("testbench: patched %d BOM values into netlist", patched)

    state.netlist = netlist_bom
    state.tas = tas

    # ------------------------------------------------------------------
    # Phase 2: Simulate at each claimed efficiency operating point
    # ------------------------------------------------------------------
    load_points = _build_load_points(state.ref_claims, spec)
    all_bom_passed = True

    for lp in load_points:
        label = lp["label"]
        iout = lp["iout"]
        claimed_eff = lp["efficiency"]
        rload = spec.vout / iout if iout > 0 else spec.vout / spec.iout

        nl_lp = _rewrite_rload(netlist_bom, rload)
        lp_claims = ReferenceClaims(
            efficiency={label: claimed_eff} if claimed_eff else {},
            vout_measured=state.ref_claims.vout_measured,
        )
        result = _simulate_netlist(nl_lp, spec.vout, lp_claims, spec, f"bom@{label}")
        if result is None:
            state.diagnostics.append(f"testbench: BOM sim failed at {label}")
            all_bom_passed = False
            continue

        sim_lp, comp_lp = result
        state.comparisons.append(comp_lp)
        state.sim_result = {
            "phase": f"bom@{label}",
            "vin": sim_lp.vin, "iout": sim_lp.iout,
            "vout": sim_lp.vout,
            "efficiency": sim_lp.efficiency,
            "total_losses": sim_lp.total_losses,
        }
        if not comp_lp.passed:
            all_bom_passed = False

    if all_bom_passed and load_points:
        state.passed = True
    elif not load_points:
        # No claimed load points — fall back to single full-load sim
        phase2 = _simulate_netlist(
            netlist_bom, spec.vout, state.ref_claims, spec, "bom",
        )
        if phase2:
            sim_bom, comp_bom = phase2
            state.comparisons.append(comp_bom)
            state.passed = comp_bom.passed
    else:
        failed = [c for c in state.comparisons[1:] if not c.passed]
        if failed:
            diagnosis = _diagnose_mismatch(failed[0], state)
            failed[0].diagnosis = diagnosis
            state.diagnostics.append(f"testbench diagnosis: {diagnosis[:200]}")

    # ------------------------------------------------------------------
    # Phase 3: Waveform characterization + analytical cross-check
    # ------------------------------------------------------------------

    # Extract Cout from BOM for analytical calc
    cout_val = 1e-4  # default
    for comp in state.ref_bom:
        role = comp.get("role", "")
        if role == "outputCapacitor":
            val = parse_component_value(str(comp.get("value", "")))
            if val and val > 0:
                cout_val = val
                break

    wf = _extract_waveforms(netlist_bom, spec.vout)
    if wf:
        logger.info(
            "testbench waveforms: Vout_ripple=%.1fmV Vsw_pp=%.1fV "
            "IL_ripple=%.2fA IL_avg=%.2fA",
            wf.vout_ripple_mv, wf.vsw_vpp, wf.il_ripple_a, wf.il_avg_a,
        )

        analytical = _compute_analytical_waveforms(
            spec, mag_inductance, cout_val, topology,
        )
        wf_checks = _check_waveforms(wf, analytical)

        state.sim_result = state.sim_result or {}
        if isinstance(state.sim_result, dict):
            state.sim_result["waveforms"] = {
                "vout_ripple_mv": round(wf.vout_ripple_mv, 1),
                "vsw_vpp": round(wf.vsw_vpp, 1),
                "il_ripple_a": round(wf.il_ripple_a, 2),
                "il_avg_a": round(wf.il_avg_a, 2),
                "analytical": {
                    "vout_ripple_mv": round(analytical.vout_ripple_mv, 1),
                    "il_ripple_a": round(analytical.il_ripple_a, 2),
                    "vsw_vpp": round(analytical.vsw_vpp, 1),
                },
                "checks": wf_checks,
            }

        wf_failed = [c for c in wf_checks if not c["passed"]]
        if wf_failed:
            for f in wf_failed:
                state.diagnostics.append(
                    f"testbench waveform {f['param']}: sim={f['sim']}{f['unit']} "
                    f"analytical={f['analytical']}{f['unit']} ratio={f['ratio']}× "
                    f"— outside [0.5×, 2×]"
                )
        else:
            logger.info(
                "testbench waveforms: all %d checks within [0.5×, 2×] of analytical",
                len(wf_checks),
            )

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
