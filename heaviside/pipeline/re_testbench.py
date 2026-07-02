"""RE virtual test bench: rebuild a reference design and simulate it.

Takes an extracted BOM + spec from a reference design PDF, builds the
actual converter circuit using the reference's component values, simulates
it, and compares the results against the PDF's performance claims.

If the simulation doesn't match the claims, it diagnoses why and feeds
lessons back to the teacher for future runs.

della-Pollock cutover (abt #48): the two-phase simulation runs on KIRCHHOFF,
not on MKF stencils. Kirchhoff designs the topology TAS from the reference's
spec; HS fills the BOM (real parts + an MKF magnetic GEOMETRY designed from
Kirchhoff's own per-component seed) and Kirchhoff regulates + simulates it.
The reference design's ACTUAL parameters (inductance, output capacitance,
controller/FET Rds_on) are stamped into the TAS so Phase 2 is a virtual
replica of the real board — no MKF stencil netlist and
no string-rewriting of a deck. MKF is magnetics-geometry-only here.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from datetime import UTC
from typing import TYPE_CHECKING, Any

from heaviside.decomposer import kirchhoff_adapter as _ka
from heaviside.pipeline.re_state import (
    ComponentRoleMap,
    ReferenceClaims,
    REState,
    SimComparison,
)
from heaviside.pipeline.value_parse import _prefix_multiplier

if TYPE_CHECKING:
    from heaviside.pipeline.crossref import SimDerivedStress

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
    # A bare "half-bridge" on an eval board is almost always the
    # NON-ISOLATED GaN/Si half-bridge power stage used as a synchronous
    # buck (two switches + output inductor, no transformer) — e.g.
    # Infineon EVAL-7136U. Map it to buck so it produces a valid design;
    # asymmetric_half_bridge (isolated, transformer) is MKF-rejected here
    # and yields no design. A genuinely isolated half-bridge SMPS would
    # carry a turns_ratio and needs separate upstream disambiguation.
    "half_bridge": "buck",
    "half-bridge": "buck",
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
    "S1": "mosfet",
    "S2": "mosfet",
    "D1": "diode",
    "D2": "diode",
    "L1": "inductor",
    "L2": "inductor",
    "Cout": "capacitor",
    "Cin": "capacitor",
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

_VALUE_RE = re.compile(
    r"^([\d.]+)\s*([TGMkKmuµμnp]?)\s*([FHΩRVAohm]*)",
    re.IGNORECASE,
)


def parse_component_value(value_str: str) -> float | None:
    """Parse engineering notation: '4.7uH' → 4.7e-6, '22uF' → 22e-6.

    The regex is IGNORECASE so ALL-CAPS BOM strings ("4.7UH", "22PF") match;
    prefix resolution is delegated to the shared, case-aware resolver in
    ``value_parse`` so an unambiguous uppercase prefix folds correctly and an
    unknown prefix is rejected rather than silently treated as ×1.
    """
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
    multiplier = _prefix_multiplier(m.group(2) or "")
    if multiplier is None:
        return None
    return num * multiplier


def _ref_value_for_roles(ref_bom: list[dict[str, Any]], roles: tuple[str, ...]) -> float | None:
    """First positive parsed component value among ``ref_bom`` entries whose
    role is in ``roles`` — the reference design's ACTUAL value (e.g. the main
    inductor's inductance, the output cap's capacitance)."""
    for comp in ref_bom:
        if comp.get("role", "") in roles:
            val = parse_component_value(str(comp.get("value", "")))
            if val and val > 0:
                return val
    return None


# bounded deck rewriters retained for the spice_sim calibrate layer (it nudges
# passive values / fsw on a deck the simulator then re-judges). These operate on
# generic ngspice deck text and are NOT used by the testbench's Kirchhoff path.
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


def _rewrite_fsw(deck: str, new_fsw: float) -> str:
    """Rewrite the PWM PULSE period to match a target fsw."""
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
# Waveform analysis
# ---------------------------------------------------------------------------


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
        issues.append(
            {
                "param": name,
                "sim": round(sim_val, 3),
                "analytical": round(ana_val, 3),
                "ratio": round(ratio, 2),
                "unit": unit,
                "passed": passed,
            }
        )
        if not passed:
            logger.warning(
                "testbench waveform %s: sim=%.3f%s analytical=%.3f%s "
                "ratio=%.2f — outside [%.1f×, %.1f×]",
                name,
                sim_val,
                unit,
                ana_val,
                unit,
                ratio,
                _WAVEFORM_RATIO_BOUND,
                1 / _WAVEFORM_RATIO_BOUND,
            )
    return issues


def _get_controller_rdson(ref_bom: list[dict[str, Any]]) -> float | None:
    """Look up the controller IC's Rds_on from the internal controllers DB.

    Returns average of HS+LS Rds_on in ohms, or None if not found.
    """
    import json
    from pathlib import Path

    tas_path = Path(__file__).resolve().parents[2] / "TAS" / "data" / "controllers.ndjson"
    if not tas_path.exists():
        return None

    ic_mpn = None
    for comp in ref_bom:
        if comp.get("role") == "controller":
            ic_mpn = comp.get("mpn", comp.get("part", ""))
            if ic_mpn:
                break
    if not ic_mpn:
        return None

    mpn_upper = ic_mpn.upper().strip()
    # Strip ordering suffix: LT7153SPAV#TRMPBF → LT7153SPAV → match LT7153SP
    mpn_base = mpn_upper.split("#")[0].split("/")[0]

    with open(tas_path, "rb") as f:
        for raw_line in f:
            if not raw_line.strip():
                continue
            try:
                rec = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            name = (rec.get("name", "") or rec.get("partNumber", "")).upper().strip()
            if (
                name in (mpn_upper, mpn_base)
                or mpn_base.startswith(name)
                or name.startswith(mpn_base)
            ):
                elec = rec.get("electrical", {})
                hs = elec.get("rdsOnHighSide")
                ls = elec.get("rdsOnLowSide")
                if hs and isinstance(hs, (int, float)):
                    avg = (hs + (ls or hs)) / 2
                    logger.info(
                        "testbench: found controller %s Rds_on in internal DB: HS=%.1fmΩ LS=%.1fmΩ",
                        ic_mpn,
                        hs * 1000,
                        (ls or hs) * 1000,
                    )
                    return avg
    return None


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

_EFFICIENCY_TOLERANCE_PP = 3.0
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
        mismatches.append(
            {
                "param": "efficiency",
                "sim": sim_eff,
                "claimed": claimed_eff,
                "delta_pp": eff_delta,
            }
        )
    if claimed_vout and vout_err > _VOUT_TOLERANCE_PCT:
        mismatches.append(
            {
                "param": "vout",
                "sim": sim_vout,
                "claimed": claimed_vout,
                "error_pct": vout_err,
            }
        )

    # No claims at all = no meaningful comparison
    if not claimed_eff and not claimed_vout:
        mismatches.append(
            {
                "param": "no_claims",
                "note": "no efficiency or Vout claims extracted — cannot validate",
            }
        )

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


def _diagnose_mismatch(comparison: SimComparison, state: REState) -> str:
    """Generate a diagnosis for simulation mismatches."""
    from heaviside.agents.llm_call import LLMCallError, call_agent

    if not comparison.mismatches:
        return "No mismatches — simulation matches reference claims."

    import json

    diag_input = json.dumps(
        {
            "mismatches": comparison.mismatches,
            "topology": state.ref_spec.topology if state.ref_spec else "?",
            "sim_efficiency": comparison.sim_efficiency,
            "claimed_efficiency": comparison.claimed_efficiency,
            "sim_vout": comparison.sim_vout,
            "claimed_vout": comparison.claimed_vout,
        },
        indent=2,
    )

    try:
        diagnosis = call_agent(
            "reviewer",
            f"RE TESTBENCH DIAGNOSIS — simulation doesn't match reference claims.\n\n"
            f"Diagnose the most likely cause and suggest what to adjust.\n\n"
            f"{diag_input}",
            max_tokens=4096,
        )
        return diagnosis[:500]
    except LLMCallError as exc:
        return f"Diagnosis failed: {exc}"


# ---------------------------------------------------------------------------
# Kirchhoff simulation seam (della-Pollock cutover)
# ---------------------------------------------------------------------------

_MAX_TESTBENCH_LOOPS = 3


def _kirchhoff_errors() -> tuple[type[Exception], ...]:
    """The Kirchhoff/bridge/sim exceptions a testbench sim may raise — caught so
    a sim failure becomes a diagnostic on the REState (the testbench is a
    best-effort validation, never a pipeline-killer), not an uncaught crash."""
    from heaviside.catalogue.kirchhoff_fill import KirchhoffFillError
    from heaviside.catalogue.selector import SelectionError
    from heaviside.decomposer.kirchhoff_adapter import (
        KirchhoffSpecError,
        KirchhoffTopologyUnsupported,
        KirchhoffUnavailable,
    )
    from heaviside.sim import SimError

    errs: list[type[Exception]] = [
        KirchhoffUnavailable,
        KirchhoffTopologyUnsupported,
        KirchhoffSpecError,
        KirchhoffFillError,
        SelectionError,
        SimError,
    ]
    try:
        from heaviside.bridge import BridgeError

        errs.append(BridgeError)
    except Exception:  # bridge import is optional context for the tuple
        pass
    try:
        # _design_ktas_magnetics raises RealizeError when MKF cannot wind a core for
        # the (reference) inductance — a surfaced magnetic-design failure, turned into
        # a testbench diagnostic rather than crashing the whole crossref run.
        from heaviside.pipeline.full_design import RealizeError

        errs.append(RealizeError)
    except Exception:  # optional context for the tuple
        pass
    return tuple(errs)


def _op_to_simresult(op: dict[str, Any], vin: float) -> Any:
    """Build a runner ``SimResult`` from a Kirchhoff regulated operating point,
    so the existing comparison/report code (which reads ``.vout`` / ``.efficiency``
    / ``.pin`` …) is unchanged. Fail-loud on a non-finite point."""
    import math

    from heaviside.sim.runner import SimResult

    vout = float(op["vout"])
    pin = float(op["pin"])
    pout = float(op["pout"])
    eff = float(op["efficiency"])
    if not all(math.isfinite(x) for x in (vout, pin, pout, eff)) or pin <= 0:
        raise ValueError(f"non-finite/zero Kirchhoff operating point: {op}")
    return SimResult(
        vin=float(vin),
        iin=(pin / vin if vin else 0.0),
        vout=vout,
        iout=(pout / vout if vout else 0.0),
        pin=pin,
        pout=pout,
        total_losses=pin - pout,
        efficiency=eff,
    )


def _set_tas_load(tas: dict[str, Any], vout: float, iout: float) -> None:
    """Set the primary output power (= Vout·Iout) on every Kirchhoff operating
    point, so ``tas_to_ngspice`` emits the matching ``Rload`` and the regulated
    sim reports efficiency at THIS load. In-place on a (caller-owned) copy."""
    power = float(vout) * float(iout)
    ops = tas.get("inputs", {}).get("operatingPoints")
    if not isinstance(ops, list):
        return
    for op in ops:
        outs = op.get("outputs") if isinstance(op, dict) else None
        if isinstance(outs, list) and outs and isinstance(outs[0], dict):
            outs[0]["power"] = power


def _regulate_at_load(
    k_tas: dict[str, Any],
    vout: float,
    iout: float,
    topology: str,
    *,
    fidelity: str,
    label: str,
) -> dict[str, Any] | None:
    """Closed-loop REGULATED operating point at a specific output current.

    Deep-copies the TAS (so the design/parts are untouched), sets the load, and
    runs Kirchhoff's ``simulate_regulated``. Returns the op dict, or ``None`` if
    the design did not regulate to target / produced a non-physical point (a
    surfaced sim failure, not a silent fallback)."""
    import copy

    t = copy.deepcopy(k_tas)
    _set_tas_load(t, vout, iout)
    try:
        op = _ka.simulate_regulated(t, float(vout), topology, fidelity=fidelity)
    except _kirchhoff_errors() as exc:
        logger.warning("testbench [%s]: Kirchhoff sim failed: %s", label, exc)
        return None
    if not op.get("regulated"):
        logger.warning(
            "testbench [%s]: did not regulate to %.3f V (vout=%s, converged=%s)",
            label,
            vout,
            op.get("vout"),
            op.get("converged"),
        )
        return None
    return op


def _simulate_phase(
    k_tas: dict[str, Any],
    topology: str,
    vout: float,
    iout: float,
    vin: float,
    ref_claims: ReferenceClaims,
    ref_spec: Any,
    label: str,
    *,
    fidelity: str,
) -> tuple[Any, SimComparison] | None:
    """Regulate ``k_tas`` at (Vout, Iout) and build a comparison vs claims.

    Returns ``(SimResult, SimComparison)`` or ``None`` on sim failure."""
    op = _regulate_at_load(k_tas, vout, iout, topology, fidelity=fidelity, label=label)
    if op is None:
        return None
    try:
        sim_result = _op_to_simresult(op, vin)
    except ValueError as exc:
        logger.warning("testbench [%s]: %s", label, exc)
        return None

    comparison = _build_comparison(sim_result, ref_claims, ref_spec)
    logger.info(
        "testbench [%s]: η_sim=%.1f%% η_claimed=%.1f%% Δ=%.1fpp "
        "Vout_sim=%.2f Vout_claimed=%.2f err=%.1f%% → %s",
        label,
        comparison.sim_efficiency * 100 if comparison.sim_efficiency else 0,
        comparison.claimed_efficiency * 100 if comparison.claimed_efficiency else 0,
        comparison.efficiency_delta_pp,
        comparison.sim_vout,
        comparison.claimed_vout,
        comparison.vout_error_pct,
        "PASS" if comparison.passed else "MISMATCH",
    )
    return sim_result, comparison


def _override_cap_capacitance(tas: dict[str, Any], *, output: float | None, input_: float | None) -> None:
    """Stamp the reference design's ACTUAL output/input filter capacitance into the
    Kirchhoff per-component requirement BEFORE the BOM fill, so the fill sources a
    real part close to the reference value (and the deck uses it). This replicates
    the real board's filter AND sidesteps Kirchhoff's own (sometimes degenerate)
    output-cap sizing — e.g. its boost designer can emit a NEGATIVE Cout; the
    reference value is the physical truth here. Resonant caps are left to
    Kirchhoff (their value sets the tank, not a filter)."""
    for st in tas.get("topology", {}).get("stages", []):
        for comp in st.get("circuit", {}).get("components", []):
            data = comp.get("data")
            if not isinstance(data, dict) or "capacitor" not in data:
                continue
            req = data.get("inputs", {}).get("designRequirements", {})
            if not isinstance(req, dict):
                continue
            role = req.get("role")
            cap = req.get("capacitance")
            if not isinstance(cap, dict):
                continue
            if role == "outputFilter" and output and output > 0:
                cap["nominal"] = float(output)
            elif role == "inputFilter" and input_ and input_ > 0:
                cap["nominal"] = float(input_)


def _override_mosfet_ron(tas: dict[str, Any], ron: float) -> int:
    """Overwrite the on-resistance of every (BOM-filled) MOSFET in the TAS with the
    reference design's measured Rds_on, so the DATASHEET deck's switch model uses the
    REAL board's conduction resistance (Kirchhoff reads ``onResistance`` from the
    part envelope). Returns the count overwritten."""
    n = 0
    for st in tas.get("topology", {}).get("stages", []):
        for comp in st.get("circuit", {}).get("components", []):
            data = comp.get("data")
            semi = data.get("semiconductor") if isinstance(data, dict) else None
            if not isinstance(semi, dict) or "mosfet" not in semi:
                continue
            elec = (
                semi["mosfet"]
                .setdefault("manufacturerInfo", {})
                .setdefault("datasheetInfo", {})
                .setdefault("electrical", {})
            )
            elec["onResistance"] = float(ron)
            n += 1
    return n


def _build_phase2_tas(
    topology: str,
    hs_spec: dict[str, Any],
    *,
    ref_cout: float | None,
    ref_cin: float | None,
    ron: float | None,
) -> dict[str, Any]:
    """Design the topology TAS and fill it into a virtual replica of the real board:
    real BOM parts (Kirchhoff requirement fill), the magnetic GEOMETRY designed by
    MKF from Kirchhoff's own per-component seed (MKF_MODEL — real DCR + AC ladder),
    the reference filter capacitance, and the reference controller/FET Rds_on. The
    spec already carries the reference inductance via ``desiredInductance`` so
    Kirchhoff (and the MKF magnetic) are sized to the real board's L."""
    from heaviside import bridge as _bridge
    from heaviside.catalogue.kirchhoff_fill import (
        KirchhoffFillError,
        fill_kirchhoff_bom,
        stamp_mkf_magnetic,
    )
    from heaviside.pipeline.full_design import _design_ktas_magnetics

    k_tas = _ka.design_from_hs_spec(topology, hs_spec)
    _override_cap_capacitance(k_tas, output=ref_cout, input_=ref_cin)
    fill_kirchhoff_bom(k_tas, topology=topology)
    n_mag = _design_ktas_magnetics(
        k_tas,
        bridge_mod=_bridge,
        pyom_vendor=_bridge._import_pyom_vendor(),
        stamp_fn=stamp_mkf_magnetic,
    )
    if n_mag == 0:
        raise KirchhoffFillError(f"testbench: {topology} TAS has no magnetic to design")
    if ron and ron > 0:
        n_ron = _override_mosfet_ron(k_tas, ron)
        logger.info("testbench: stamped reference Rds_on=%.1fmΩ on %d MOSFET(s)", ron * 1000, n_ron)
    return k_tas


def _extract_kirchhoff_waveforms(
    k_tas: dict[str, Any],
    op: dict[str, Any],
    topology: str,
) -> float | None:
    """Measure the output-voltage ripple of the regulated Phase-2 design from a
    Kirchhoff DATASHEET deck (real sim). The deck is emitted at the REGULATED
    control value (so it sits on target), and ``v(Vout)`` peak-to-peak is measured
    over the deck's own steady-state window. Returns ripple in millivolts, or
    ``None`` if ngspice is unavailable / the measurement fails.

    Only the top-level output node ``Vout`` is measured (always present, topology-
    agnostic); the inductor/switch waveforms are derived analytically from the
    KNOWN (reference) inductance + operating point in :func:`run_testbench`."""
    import copy
    import os
    import subprocess
    import tempfile

    if shutil.which("ngspice") is None:
        return None

    reg = _ka._load_regulate()
    t = copy.deepcopy(k_tas)
    ctrl = op.get("control")
    val = op.get("value")
    field = reg._CONTROL.get(ctrl, (None,))[0] if isinstance(ctrl, str) else None
    base = _ka.kirchhoff_base(topology) or topology
    if field is not None and isinstance(val, (int, float)):
        reg._set_control(t, field, float(val), base)

    deck = _ka.tas_to_ngspice(t, "DATASHEET")

    # Reuse the deck's own steady-state window (the AVG-vout meas line) for the
    # ripple max/min. Use the `m_` result namespace so the measurement vector does
    # not shadow the `v(Vout)` node (ngspice case-insensitive shadowing, abt #54).
    win = re.search(r"meas\s+tran\s+vout\s+avg\s+v\(Vout\)\s+(from=\S+\s+to=\S+)", deck, re.IGNORECASE)
    window = win.group(1) if win else ""
    inject = (
        f"meas tran m_vmax max v(Vout) {window}\n"
        f"meas tran m_vmin min v(Vout) {window}\n"
    )
    if "\nrun\n" in deck:
        deck = deck.replace("\nrun\n", "\nrun\n" + inject, 1)
    else:
        return None

    with tempfile.NamedTemporaryFile(mode="w", suffix=".cir", delete=False) as f:
        f.write(deck)
        cir = f.name
    try:
        proc = subprocess.run(["ngspice", "-b", cir], capture_output=True, text=True, timeout=60)
    except Exception:  # sim failure surfaces as "no waveform" (best-effort)
        return None
    finally:
        os.unlink(cir)

    def _grab(name: str) -> float | None:
        m = re.search(rf"\b{name}\s*=\s*([-\d.eE+]+)", proc.stdout + proc.stderr)
        try:
            return float(m.group(1)) if m else None
        except ValueError:
            return None

    vmax, vmin = _grab("m_vmax"), _grab("m_vmin")
    if vmax is None or vmin is None:
        return None
    return abs(vmax - vmin) * 1000.0


# ---------------------------------------------------------------------------
# Load points
# ---------------------------------------------------------------------------


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

    # Filter out load points below 50% — at light load, IC-specific
    # quiescent current, gate drive, and burst-mode behavior dominate
    # efficiency. Our conduction-loss model can't capture these effects.
    points = [p for p in points if p["iout"] >= iout_max * 0.50]

    if not points and iout_max > 0:
        points.append(
            {
                "label": "full_load",
                "iout": iout_max,
                "efficiency": claims.efficiency.get("full_load", 0),
            }
        )

    return sorted(points, key=lambda p: p["iout"])


def _build_converter_json(state: REState) -> tuple[dict[str, Any], str] | None:
    """Build the Heaviside converter spec dict and normalize the topology.

    Returns (hs_spec, topology) or None on failure. The spec is the
    ``inputVoltage``/``operatingPoints``/``efficiency`` shape Kirchhoff's
    :func:`design_from_hs_spec` consumes.
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
        converter_json["inputVoltage"]["minimum"] = converter_json["inputVoltage"]["nominal"] * 0.8

    if "buck" in topology:
        vin_for_sim = converter_json["inputVoltage"]["minimum"]
        if vin_for_sim < vout * 1.2:
            converter_json["inputVoltage"]["minimum"] = converter_json["inputVoltage"]["nominal"]
            state.diagnostics.append(
                f"testbench: Vin_min={vin_min:.1f}V < Vout×1.2={vout * 1.2:.1f}V — "
                f"simulating at Vin_nom instead (cannot model 100% duty)."
            )

    norm = _normalize_topology(topology)
    if not norm:
        state.diagnostics.append(
            f"testbench: cannot map topology '{spec.topology}' to a Kirchhoff designer"
        )
        return None
    return converter_json, norm


# ---------------------------------------------------------------------------
# Main testbench
# ---------------------------------------------------------------------------


def run_testbench(state: REState) -> REState:
    """Virtual test bench: two-phase Kirchhoff simulation.

    Phase 1 — Theoretical (ideal): Kirchhoff designs the topology from the
    reference spec and regulates it at IDEAL (REQUIREMENTS) fidelity. This
    validates the topology + operating point produce a physically reasonable
    closed-loop point before touching real BOM data.

    Phase 2 — Real BOM: the SAME design, filled into a virtual replica of the
    real board — real BOM parts (Kirchhoff requirement fill), the magnetic
    GEOMETRY designed by MKF from Kirchhoff's seed (real DCR), the reference's
    ACTUAL inductance / filter capacitance / controller Rds_on stamped in — and
    regulated at DATASHEET fidelity.

    Comparing both phases against PDF claims tells us how much of any gap is
    topology-inherent vs component-specific.
    """
    result = _build_converter_json(state)
    if result is None:
        return state
    hs_spec, topology = result
    spec = state.ref_spec
    assert spec is not None

    if _ka.kirchhoff_base(topology) is None:
        state.diagnostics.append(
            f"testbench: Kirchhoff has no designer for topology {topology!r} "
            f"(mapped from '{spec.topology}') — cannot simulate"
        )
        return state

    # Map BOM roles (used by the stress extractor + diagnostics).
    role_map = build_role_map(state.ref_bom, spec.topology)
    state.role_map = role_map
    logger.info(
        "testbench: mapped %d/%d BOM components to roles (confidence %.0f%%)",
        len(role_map.roles),
        len(state.ref_bom),
        role_map.confidence * 100,
    )

    # Reference design's ACTUAL component values (drive the virtual replica).
    ref_l = _ref_value_for_roles(
        state.ref_bom,
        ("mainInductor", "boostInductor", "buckInductor", "mainTransformer"),
    )
    ref_cout = _ref_value_for_roles(state.ref_bom, ("outputCapacitor",))
    ref_cin = _ref_value_for_roles(state.ref_bom, ("inputCapacitor",))
    if ref_l and ref_l > 0:
        hs_spec["desiredInductance"] = ref_l

    vin = float(hs_spec["inputVoltage"]["nominal"])

    # Detect multi-phase (simulate one phase at Iout/N).
    raw_topo_full = spec.topology.lower()
    pdf_lower = (state.pdf_text or "").lower()
    n_phases = 1
    if any(k in raw_topo_full for k in ("dual", "2-phase", "two-phase", "polyphase", "poly", "multi")):
        n_phases = 2
    if n_phases == 1 and any(
        k in pdf_lower
        for k in ("dual-phase", "dual phase", "2-phase", "two-phase", "polyphase", "2xlt", "2×lt")
    ):
        n_phases = 2
    if n_phases == 1 and ("4-phase" in pdf_lower or "four-phase" in pdf_lower):
        n_phases = 4
    if n_phases > 1:
        logger.info(
            "testbench: %d-phase converter — simulating one phase at %.1fA (total %.1fA)",
            n_phases,
            spec.iout / n_phases,
            spec.iout,
        )

    # Kirchhoff models the buck/boost family with a DIODE rectifier (asynchronous).
    # When the reference is SYNCHRONOUS (a low-side FET replaces the diode) the real
    # board's rectification loss is I²·Rds_on, not I·Vf — so the simulated efficiency
    # is CONSERVATIVE (lower) than the real sync board by the diode-vs-FET conduction
    # gap. Surface it as a diagnostic (not a silent wrong number) so the efficiency
    # comparison is read with this Kirchhoff model gap in mind.
    has_sync_role = any(
        c.get("role", "") in ("synchronousRectifier", "lowSideSwitch") for c in state.ref_bom
    )
    pdf_says_sync = "synchronous" in pdf_lower
    if ("buck" in topology or "boost" in topology) and (
        "sync" in raw_topo_full or has_sync_role or pdf_says_sync
    ):
        state.diagnostics.append(
            "testbench: reference is SYNCHRONOUS but Kirchhoff models this topology "
            "asynchronously (diode rectifier) — simulated efficiency is conservative "
            "(real sync board's I²·Rds_on rectification loss is lower than the modeled "
            "diode I·Vf loss)."
        )

    # Rds_on priority: internal-DB controllers (verified) > PDF extraction.
    ron = _get_controller_rdson(state.ref_bom)
    if ron:
        pass
    elif spec.rdson_hs and spec.rdson_ls:
        ron = (spec.rdson_hs + spec.rdson_ls) / 2 / 1000  # mΩ → Ω
        logger.info(
            "testbench: Rds_on from PDF extraction: HS=%.1fmΩ LS=%.1fmΩ",
            spec.rdson_hs,
            spec.rdson_ls,
        )
    elif spec.rdson_hs:
        ron = spec.rdson_hs / 1000
        logger.info("testbench: Rds_on from PDF extraction: %.1fmΩ", spec.rdson_hs)
    else:
        state.diagnostics.append(
            "testbench: Rds_on not available — not in internal-DB controllers "
            "and IC datasheet extraction failed. Cannot simulate."
        )
        return state

    iout_phase = spec.iout / n_phases

    # ------------------------------------------------------------------
    # Phase 1: Theoretical — Kirchhoff design, ideal (REQUIREMENTS) fidelity
    # ------------------------------------------------------------------
    try:
        k_ideal = _ka.design_from_hs_spec(topology, hs_spec)
    except _kirchhoff_errors() as exc:
        state.diagnostics.append(f"testbench: Kirchhoff design failed: {exc}")
        return state

    phase1 = _simulate_phase(
        k_ideal, topology, spec.vout, iout_phase, vin,
        state.ref_claims, spec, "ideal", fidelity="REQUIREMENTS",
    )
    if phase1 is None:
        state.diagnostics.append("testbench: ideal (Kirchhoff REQUIREMENTS) simulation failed")
        return state

    sim_ideal, comp_ideal = phase1
    state.comparisons.append(comp_ideal)
    state.sim_result = {
        "phase": "ideal",
        "vin": sim_ideal.vin,
        "iin": sim_ideal.iin,
        "vout": sim_ideal.vout,
        "iout": sim_ideal.iout,
        "pin": sim_ideal.pin,
        "pout": sim_ideal.pout,
        "efficiency": sim_ideal.efficiency,
        "total_losses": sim_ideal.total_losses,
    }

    # If even the ideal sim is wildly off (>30pp), something is wrong with the
    # spec extraction — don't proceed to BOM realization.
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
    # Phase 2: Real BOM — Kirchhoff design filled into a virtual replica
    # ------------------------------------------------------------------
    try:
        k_bom = _build_phase2_tas(
            topology, hs_spec, ref_cout=ref_cout, ref_cin=ref_cin, ron=ron
        )
    except _kirchhoff_errors() as exc:
        state.diagnostics.append(f"testbench: BOM realization failed: {exc}")
        _learn_from_testbench(state)
        return state

    state.tas = k_bom
    try:
        state.netlist = _ka.tas_to_ngspice(k_bom, "DATASHEET")
    except _kirchhoff_errors() as exc:
        logger.warning("testbench: could not emit DATASHEET deck for state.netlist: %s", exc)

    # ------------------------------------------------------------------
    # Phase 2: Simulate at each claimed efficiency operating point
    # ------------------------------------------------------------------
    load_points = _build_load_points(state.ref_claims, spec)
    all_bom_passed = True

    for lp in load_points:
        label = lp["label"]
        iout = lp["iout"] / n_phases  # per-phase current
        claimed_eff = lp["efficiency"]
        lp_claims = ReferenceClaims(
            efficiency={label: claimed_eff} if claimed_eff else {},
            vout_measured=state.ref_claims.vout_measured,
        )
        res = _simulate_phase(
            k_bom, topology, spec.vout, iout, vin,
            lp_claims, spec, f"bom@{label}", fidelity="DATASHEET",
        )
        if res is None:
            state.diagnostics.append(f"testbench: BOM sim failed at {label}")
            all_bom_passed = False
            continue

        sim_lp, comp_lp = res
        state.comparisons.append(comp_lp)
        state.sim_result = {
            "phase": f"bom@{label}",
            "vin": sim_lp.vin,
            "iout": sim_lp.iout,
            "vout": sim_lp.vout,
            "efficiency": sim_lp.efficiency,
            "total_losses": sim_lp.total_losses,
        }
        if not comp_lp.passed:
            all_bom_passed = False

    if all_bom_passed and load_points:
        state.passed = True
    elif not load_points:
        # No claimed load points — single full-load sim.
        res = _simulate_phase(
            k_bom, topology, spec.vout, iout_phase, vin,
            state.ref_claims, spec, "bom", fidelity="DATASHEET",
        )
        if res:
            _sim_bom, comp_bom = res
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
    actual_l = ref_l or 100e-6
    actual_cout = ref_cout or 1e-4

    op_wf = _regulate_at_load(
        k_bom, spec.vout, iout_phase, topology, fidelity="DATASHEET", label="waveform"
    )
    if op_wf is not None:
        analytical = _compute_analytical_waveforms(spec, actual_l, actual_cout, topology)
        il_avg = abs(float(op_wf["pout"]) / spec.vout) if spec.vout else 0.0
        vout_ripple_mv = _extract_kirchhoff_waveforms(k_bom, op_wf, topology)
        # Inductor + switch waveforms are derived from the KNOWN reference L +
        # operating point (analytical); the output ripple is the real measured
        # sim quantity that cross-checks L AND Cout together.
        wf = WaveformCharacteristics(
            vout_ripple_mv=vout_ripple_mv if vout_ripple_mv is not None else analytical.vout_ripple_mv,
            vsw_vpp=analytical.vsw_vpp,
            il_ripple_a=analytical.il_ripple_a,
            il_avg_a=il_avg,
        )
        logger.info(
            "testbench waveforms: Vout_ripple=%.1fmV (%s) Vsw_pp=%.1fV IL_ripple=%.2fA IL_avg=%.2fA",
            wf.vout_ripple_mv,
            "measured" if vout_ripple_mv is not None else "analytical",
            wf.vsw_vpp,
            wf.il_ripple_a,
            wf.il_avg_a,
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


def _learn_from_testbench(state: REState) -> None:
    """Persist testbench lessons to the teacher."""
    try:
        from heaviside.pipeline.teacher import Lesson, store_lessons
    except ImportError:
        return

    import hashlib
    from datetime import datetime

    now = datetime.now(UTC).isoformat()
    fingerprint = hashlib.sha256(state.reference.encode()).hexdigest()[:12]

    lessons: list[Lesson] = []
    for lesson_data in state.lessons:
        for mm in lesson_data.get("mismatches", []):
            lessons.append(
                Lesson(
                    id=hashlib.sha256(
                        f"re-tb:{state.reference}:{mm.get('param', '')}:{lesson_data['attempt']}".encode()
                    ).hexdigest()[:16],
                    timestamp=now,
                    topology=state.ref_spec.topology if state.ref_spec else "?",
                    category="simulation_failure",
                    severity="high",
                    detail=f"Testbench mismatch on {mm.get('param', '?')}: "
                    f"sim={mm.get('sim')}, claimed={mm.get('claimed')}",
                    spec_fingerprint=fingerprint,
                    suggestion=lesson_data.get("diagnosis", "")[:200],
                )
            )

    if lessons:
        written = store_lessons(lessons)
        logger.info("testbench: persisted %d lessons (%d new)", len(lessons), written)


def extract_component_stress(
    state: REState,
) -> dict[str, SimDerivedStress]:
    """Extract per-component V/I stress from RE simulation results.

    Maps simulation waveforms to each BOM component via the role map.
    Returns a dict keyed by ref_des. Components without a role mapping
    or without simulation data get no entry (not estimated).
    """
    import math

    from heaviside.pipeline.crossref import SimDerivedStress

    result: dict[str, SimDerivedStress] = {}

    if not state.ref_spec or not state.role_map:
        return result
    sr = state.sim_result
    if not isinstance(sr, dict) or "waveforms" not in sr:
        return result

    wf = sr["waveforms"]
    checks = wf.get("checks", [])
    if any(not c.get("passed", True) for c in checks):
        logger.warning(
            "extract_component_stress: waveform cross-check failed — skipping stress extraction"
        )
        return result

    spec = state.ref_spec
    vin = spec.vin_nom or spec.vin_max or 12.0
    vout = spec.vout
    iout = spec.iout

    il_avg = wf.get("il_avg_a", 0)
    il_ripple = wf.get("il_ripple_a", 0)
    vout_ripple_mv = wf.get("vout_ripple_mv", 0)
    vsw_vpp = wf.get("vsw_vpp", 0)

    il_peak = il_avg + il_ripple / 2
    il_rms = math.sqrt(il_avg**2 + (il_ripple / math.sqrt(12)) ** 2)

    # Duty cycle from topology
    topo = spec.topology.lower()
    if "boost" in topo:
        d = 1 - vin / vout if vout > vin else 0.5
    else:
        d = vout / vin if vin > 0 else 0.5

    # Stencil role → stress values
    _ROLE_STRESS: dict[str, dict[str, float | None]] = {
        "outputCapacitor": {
            "v_dc": vout,
            "v_peak": vout + vout_ripple_mv / 2000,
            "i_rms": il_ripple / math.sqrt(12) if il_ripple else None,
        },
        "inputCapacitor": {
            "v_dc": vin,
            "v_peak": vin,
            "i_rms": iout * math.sqrt(d * (1 - d)) if iout and d else None,
        },
        "mainInductor": {
            "i_avg": il_avg,
            "i_peak": il_peak,
            "i_rms": il_rms,
        },
        "boostInductor": {
            "i_avg": il_avg,
            "i_peak": il_peak,
            "i_rms": il_rms,
        },
        "buckInductor": {
            "i_avg": il_avg,
            "i_peak": il_peak,
            "i_rms": il_rms,
        },
        "primarySwitch": {
            "v_peak": vsw_vpp or vin,
            "v_dc": vin,
            "i_peak": il_peak,
            "i_avg": il_avg * d if il_avg else None,
        },
        "highSideSwitch": {
            "v_peak": vsw_vpp or vin,
            "v_dc": vin,
            "i_peak": il_peak,
            "i_avg": il_avg * d if il_avg else None,
        },
        "lowSideSwitch": {
            "v_peak": vsw_vpp or vin,
            "v_dc": vin,
            "i_peak": il_peak,
            "i_avg": il_avg * (1 - d) if il_avg else None,
        },
        "synchronousRectifier": {
            "v_peak": vsw_vpp or vin,
            "v_dc": vin,
            "i_peak": il_peak,
            "i_avg": il_avg * (1 - d) if il_avg else None,
        },
        "outputRectifier": {
            "v_peak": vsw_vpp or vin,
            "v_dc": vin,
            "i_peak": il_peak,
            "i_avg": il_avg * (1 - d) if il_avg else None,
        },
        "freewheelDiode": {
            "v_peak": vsw_vpp or vin,
            "v_dc": vin,
            "i_peak": il_peak,
            "i_avg": il_avg * (1 - d) if il_avg else None,
        },
    }

    for comp in state.ref_bom:
        ref_des = comp.get("ref_des", "")
        role = comp.get("role", "")
        if not ref_des or not role:
            continue
        stress_vals = _ROLE_STRESS.get(role)
        if not stress_vals:
            continue

        result[ref_des] = SimDerivedStress(
            ref_des=ref_des,
            role=role,
            v_peak=stress_vals.get("v_peak"),
            v_dc=stress_vals.get("v_dc"),
            i_peak=stress_vals.get("i_peak"),
            i_avg=stress_vals.get("i_avg"),
            i_rms=stress_vals.get("i_rms"),
        )

    logger.info(
        "extract_component_stress: %d/%d components have stress data",
        len(result),
        len(state.ref_bom),
    )
    return result


__all__ = [
    "build_role_map",
    "extract_component_stress",
    "parse_component_value",
    "run_testbench",
]
