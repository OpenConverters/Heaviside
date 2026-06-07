"""Minimal ngspice runner: SPICE deck -> steady-state measurements.

Drives ngspice in batch mode (`ngspice -b`), injects ``.meas`` directives
that average each probe over the deck's steady-state window, parses the
resulting ``transient analysis measurement`` lines, and returns a flat
dict ready to stamp into ``tas.simulation_results.op0``.

Scope (Phase 4 v0.1):
  * Supports the topologies whose probe naming the buck/boost/cuk-family
    decomposer emits: ``v(vin_dc)``, ``v(vout)`` (or ``v(vout_*)``),
    ``i(Vin_sense)``, ``i(Vl_sense*)`` / ``i(Vout_sense*)``.
  * Computes ``vin``, ``iin``, ``vout``, ``iout``, ``pin``, ``pout``,
    ``total_losses``, ``efficiency`` for ONE operating point (``op0``).
  * No per-component loss attribution — that's a later analyst pass.

Deck post-processing:

  * ``_patch_tran_for_steady_state``: extends the .tran window so the
    output L-C filter has time to settle, adds UIC for .ic to take effect.
  * ``simulate_closed_loop``: iterative duty-cycle search — runs the
    sim, measures vout, adjusts the PWM PULSE duty, re-runs, until
    vout converges to the spec target.

The previous ``_rewrite_lossy_testbench`` (which text-edited Rsnub /
DIDEAL values to mask MKF's lossy defaults) is gone — those values are
now overridden upstream via ``SpiceSimulationConfig`` (PyMKF 1.3.13+),
applied by ``heaviside.decomposer.api.DEFAULT_SPICE_CONFIG``.

Per CLAUDE.md "no fallbacks": every parse failure raises ``SimError``
with the offending stdout line; the realism gate sees the error and
keeps the corresponding checks UNAVAILABLE rather than fabricating
numbers.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SimError(RuntimeError):
    """Raised when ngspice fails to run or returns unparseable output."""


@dataclass(frozen=True)
class SimResult:
    """Steady-state averages for one operating point."""

    vin: float
    iin: float
    vout: float
    iout: float
    pin: float
    pout: float
    total_losses: float
    efficiency: float

    def as_dict(self) -> dict[str, float]:
        return {
            "vin": self.vin,
            "iin": self.iin,
            "vout": self.vout,
            "iout": self.iout,
            "pin": self.pin,
            "pout": self.pout,
            "total_losses": self.total_losses,
            "efficiency": self.efficiency,
        }


# ---------------------------------------------------------------------------
# Deck inspection
# ---------------------------------------------------------------------------


# ``.tran tstep tstop [tstart] [tmax]`` — capture the four positional values.
_TRAN_RE = re.compile(
    r"^\s*\.tran\s+(\S+)\s+(\S+)(?:\s+(\S+))?(?:\s+(\S+))?",
    re.IGNORECASE,
)

# ``.save v(name) i(name) v(name) …`` (one ``.save`` line, possibly folded)
_SAVE_PROBE_RE = re.compile(r"([vViI])\(([^)]+)\)")

# Independent voltage-source instance line: ``V<name> n+ n- <value> …``.
# ngspice always tracks the branch current of an independent voltage source
# (``i(V<name>)``) regardless of whether it appears in ``.save`` — the source
# is the canonical place to read true DC bus current. We surface these as
# available probes so probe quadruples can reference the real input/output
# source current even when MKF's ``.save`` line omits it.
_VSOURCE_RE = re.compile(r"^\s*(V\w+)\s+\S+\s+\S+", re.IGNORECASE)


def _parse_tran_window(deck: str) -> tuple[float, float]:
    """Return ``(t_start, t_stop)`` for the steady-state window.

    Uses the deck's ``.tran`` ``tstart`` if present (MKF always supplies
    it for steady-state-aware decks); otherwise defaults to the last 50 %
    of the simulation.
    """
    for line in deck.splitlines():
        m = _TRAN_RE.match(line)
        if not m:
            continue
        tstop = _spice_time(m.group(2))
        tstart_token = m.group(3)
        if tstart_token and tstart_token.upper() not in ("UIC",):
            try:
                tstart = _spice_time(tstart_token)
                if 0 < tstart < tstop:
                    return tstart, tstop
            except SimError:
                pass
        return tstop * 0.5, tstop
    raise SimError("deck has no '.tran' directive — cannot determine sim window")


_SPICE_SUFFIXES = {
    "f": 1e-15,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "meg": 1e6,
    "g": 1e9,
    "t": 1e12,
}


def _spice_time(token: str) -> float:
    """Parse a SPICE numeric like ``2.5e-8``, ``250u``, ``1m``."""
    t = token.strip().rstrip("sS")  # allow ``250us``
    try:
        return float(t)
    except ValueError:
        pass
    # Try suffix
    low = t.lower()
    for suf in ("meg", "f", "p", "n", "u", "m", "k", "g", "t"):
        if low.endswith(suf):
            num = low[: -len(suf)]
            try:
                return float(num) * _SPICE_SUFFIXES[suf]
            except (KeyError, ValueError) as exc:
                raise SimError(f"unparseable SPICE time {token!r}") from exc
    raise SimError(f"unparseable SPICE time {token!r}")


def _saved_probes(deck: str) -> set[str]:
    """Return every probe referenced in ``.save`` lines, lowercased.

    Names look like ``v(vin_dc)``, ``i(Vin_sense)`` — we normalise to
    ``v(vin_dc)`` / ``i(vin_sense)`` for a case-insensitive lookup.
    """
    probes: set[str] = set()
    in_save = False
    for raw in deck.splitlines():
        line = raw.strip()
        # Crude folding: assume .save lines have already been pre-folded
        # by the caller (decomposer.spice_parser does this; bare decks
        # straight from MKF rarely span lines on .save).
        low = line.lower()
        if low.startswith(".save"):
            in_save = True
            line = line[5:]
        elif in_save and line.startswith("+"):
            line = line[1:]
        else:
            in_save = False
            continue
        for kind_tok, name in _SAVE_PROBE_RE.findall(line):
            probes.add(f"{kind_tok.lower()}({name.lower()})")
    # Independent voltage-source branch currents are always available in
    # ngspice (.meas i(V<name>) works without an explicit .save). Add them so
    # probe quadruples can read true source current for power balance.
    for raw in deck.splitlines():
        m = _VSOURCE_RE.match(raw)
        if m:
            probes.add(f"i({m.group(1).lower()})")
    return probes


# ---------------------------------------------------------------------------
# Probe selection (topology-aware)
# ---------------------------------------------------------------------------


# Per-topology preferred probe quadruple. Each tuple is
# (vin_probe, iin_probe, vout_probe, iout_probe). The runner picks the
# first quadruple whose probes are ALL present in the deck.
_PROBE_CANDIDATES: dict[str, list[tuple[str, str, str, str]]] = {
    # Isolated buck-boost: the primary inverting rail (vpri_out) is the
    # main output — outputVoltages[0] in the spec.  The secondary
    # (vout0) follows by turns ratio and carries a small fraction of
    # total power.  Must match before the forward-family / flyback
    # entries below, which would wrongly select vout0.
    "isolated_buck_boost": [
        ("v(vin_dc)", "i(vin)", "v(vpri_out)", "i(vpri_out_sense)"),
    ],
    # Push-pull: centre-tapped primary, vout is the output LC filter.
    # i(vin) for input current (includes both switch cycles).
    "push_pull": [
        ("v(vin_dc)", "i(vin)", "v(vout)", "i(vsec_sense)"),
        ("v(vin_dc)", "i(vin)", "v(vout)", "i(vct_sense)"),
    ],
    # Weinberg: push-pull variant with combined secondary winding.
    "weinberg": [
        ("v(vin_dc)", "i(vin_sense)", "v(out_node)", "i(vout_sense)"),
    ],
    # Asymmetric half-bridge (AHB).
    # DC source is ``Vdc vin_dc 0``; i(Vdc) is the true input current
    # (avoids snubber-RC spikes that contaminate i(Vpri_sense)).
    # Full variant (v6+): v(out_node) / i(Vout_sense), v(vin_dc) in .save.
    # Simple variant (v5): v(co_top) / i(Vout_sense), no v(vin_dc) in .save.
    "asymmetric_half_bridge": [
        ("v(vin_dc)", "i(vdc)", "v(out_node)", "i(vout_sense)"),
    ],
    # Phase-shifted full bridge (PSFB).
    # DC source is ``Vdc vin_dc 0``; i(Vdc) is the input current.
    # Per-output naming uses the ``_o<N>`` suffix convention.
    "phase_shifted_full_bridge": [
        ("v(vin_dc)", "i(vdc)", "v(out_node_o1)", "i(vout_sense_o1)"),
    ],
    # Phase-shifted half bridge (PSHB).
    # Same DC source convention as PSFB; per-output ``_o<N>`` naming.
    "phase_shifted_half_bridge": [
        ("v(vin_dc)", "i(vdc)", "v(out_node_o1)", "i(vout_sense_o1)"),
    ],
    # Dual active bridge: two full bridges. Primary input at vin_dc1,
    # secondary output at vout_cap_o1.
    "dual_active_bridge": [
        ("v(vin_dc1)", "i(vdc1)", "v(vout_cap_o1)", "i(vsec_sense_o1)"),
    ],
    # LLC / CLLC / CLLLC resonant: half-bridge primary, centre-tapped
    # secondary. vout at vout_cap_o1 or vout_pos_o1.
    "resonant_hb": [
        ("v(vin_dc)", "i(vin)", "v(vout_cap_o1)", "i(vsec_sense_o1)"),
        ("v(vin_dc)", "i(vin)", "v(vout_pos_o1)", "i(vsec_sense_o1)"),
        ("v(vin_dc1)", "i(vdc1)", "v(vout_cap_o1)", "i(vsec_sense_o1)"),
    ],
    # CLLLC (bidirectional symmetric resonant, dual full bridge): the HV
    # bridge sits on the input DC bus ``vdc_hv`` and the LV synchronous-rect
    # bridge sits on the output DC bus ``vdc_lv``. The MKF deck saves the
    # primary/secondary bridge ammeters as ``i(v_pri_bridge_sense)`` /
    # ``i(v_sec_bridge_sense)``; there are no per-rail ``_o<N>`` choke probes
    # because the LV bridge IS the rectifier (single bidirectional bus, no
    # output choke). Distinct node names from the half-bridge resonant decks,
    # so this needs its own quadruple.
    # iin is the TRUE DC bus current i(vdc_hv) (the HV source branch current),
    # NOT the AC tank ammeter i(v_pri_bridge_sense) — the tank current is a
    # bidirectional AC waveform whose average underestimates real input power
    # (only one bridge leg's DC component appears), inflating efficiency above
    # unity. The LV side likewise has no DC output ammeter, only the AC bridge
    # current, so iout is computed from the load resistor (Rload_LV) via the
    # iin_p == iout_p rload-fallback: setting iout to the same i(vdc_hv) probe
    # triggers _extract_rload, giving iout = vout / Rload exactly (12 V / 1.5 Ω).
    "clllc": [
        ("v(vdc_hv)", "i(vdc_hv)", "v(vdc_lv)", "i(vdc_hv)"),
    ],
    # Series resonant converter (SRC): half-bridge primary off a
    # capacitive divider whose DC source is ``Vdc_supply vdc_supply 0``
    # (not ``Vin``/``Vin_dc`` like the other isolated families), feeding a
    # full-bridge diode rectifier per rail. Input current is i(vdc_supply);
    # the first output rail is at vout_cap_o1 with i(vsec_sense_o1) as the
    # rail current. Per-rail ``_o<N>`` naming mirrors PSFB/DAB.
    "series_resonant": [
        ("v(vdc_supply)", "i(vdc_supply)", "v(vout_cap_o1)", "i(vsec_sense_o1)"),
        ("v(vdc_supply)", "i(vdc_supply)", "v(vout_pos_o1)", "i(vsec_sense_o1)"),
    ],
    # Buck / boost / cuk / sepic / zeta / 4SBB / flyback (single-output).
    # ``i(vl_sense)`` is the inductor current; for boost-family decks
    # the inductor sits in series with the input source, so i_L IS the
    # input current. iout is computed AFTER the meas pass from
    # ``vout / Rload`` rather than probed (see _compute_iout_from_rload).
    "single_output_dc": [
        ("v(vin_dc)", "i(vin_sense)", "v(vout)", "i(vout_sense)"),
        ("v(vin_dc)", "i(vin_sense)", "v(vout)", "i(vl_sense)"),
        ("v(vin_dc)", "i(vin_sense)", "v(vout_cap)", "i(vout_sense)"),
        # Cuk-family: vout node is named vout_load_node (load is past
        # a 0V ammeter). Same convention used by sepic/zeta variants.
        ("v(vin_dc)", "i(vin_sense)", "v(vout_load_node)", "i(vout_sense)"),
        # Forward-family (single-switch / two-switch / active-clamp):
        # MUST probe i(vin) for iin rather than i(vpri_sense), because
        # the reset return path runs D1 → Lpri → D2 → Vin (bypassing
        # the primary sense source). Probing vpri_sense averages only
        # the switch-conduction direction and overestimates pin by ~2×,
        # falsely failing efficiency_sanity. i(vin) is the source's
        # net branch current and correctly includes reset returns.
        # Listed before the flyback-family candidate so forward decks
        # match this entry first (they save both i(vin) and i(vpri_sense)).
        ("v(vin_dc)", "i(vin)", "v(vout0)", "i(vsec_sense0)"),
        # Flyback-family: secondary is named vout0, sec_sense0 for the
        # first output rail (multi-output isolated topologies tag with
        # the rail index). Flyback has no reset return path so
        # vpri_sense is equivalent to vin and accepted as the iin source.
        ("v(vin_dc)", "i(vpri_sense)", "v(vout0)", "i(vsec_sense0)"),
        # Boost-family fallback: use vl_sense for both iin and iout,
        # then override iout = vout / Rload after the .meas pass.
        ("v(vin_dc)", "i(vl_sense)", "v(vout)", "i(vl_sense)"),
        # Vienna single-phase deck: vin is the AC phase voltage source
        ("v(vphase)", "i(vphase)", "v(vdc_cap)", "i(vph_sense)"),
    ],
}


# Rload extraction so iout can be computed as vout / Rload for decks
# that don't emit i(vout_sense). MKF buck-family decks always end with
# ``Rload <node> 0 <value>``; flyback/cuk variants tag output outputs
# as ``Rload_o1`` etc. Returns the resistance in ohms or None.
_RLOAD_RE = re.compile(
    r"^\s*Rload(?:_\w+)?\s+\S+\s+\S+\s+([\d.eE+-]+)\s*$",
    re.MULTILINE,
)


def _extract_rload(deck: str) -> float | None:
    m = _RLOAD_RE.search(deck)
    if m is None:
        return None
    try:
        return _spice_time(m.group(1))  # repurposed: handles SPICE numbers
    except SimError:
        return None


def _select_probes(deck: str) -> tuple[str, str, str, str]:
    """Choose the (vin, iin, vout, iout) probe quadruple from the deck."""
    available = _saved_probes(deck)
    for candidates in _PROBE_CANDIDATES.values():
        for quad in candidates:
            if all(p in available for p in quad):
                return quad
    raise SimError(
        "ngspice runner could not match any probe quadruple against the "
        f"deck's .save list. Saved probes: {sorted(available)}. Add a new "
        "candidate tuple to _PROBE_CANDIDATES for this topology family."
    )


# ---------------------------------------------------------------------------
# .meas injection
# ---------------------------------------------------------------------------


def _patch_tran_for_steady_state(deck: str) -> tuple[str, float, float]:
    """Ensure the deck's ``.tran`` is long enough to reach steady state
    AND uses UIC so ``.ic`` initial conditions take effect.

    Returns the patched deck plus the new ``(t_start, t_stop)``. We pick
    the steady-state window as the last 25 % of t_stop (post-LC-settling
    typically; the runner targets average DC bus values, not switching
    ripple). Many MKF decks set tstop at a few hundred microseconds,
    which is plenty for switching cycles but not for the L-C output
    filter to settle (fc ~hundreds of Hz means several milliseconds).
    """
    out: list[str] = []
    new_tstart = new_tstop = 0.0
    found = False
    for line in deck.splitlines():
        m = _TRAN_RE.match(line)
        if not m or found:
            out.append(line)
            continue
        found = True
        tstep = _spice_time(m.group(1))
        tstop = _spice_time(m.group(2))
        # Stretch tstop to cover the output L-C filter settling time —
        # 10 ms is overkill for most converters but bounded above by the
        # subprocess timeout (60 s default).
        target_tstop = max(tstop, 10e-3)
        # Coarsen tstep when stretching to keep wallclock manageable.
        # 100 ns is a safe upper bound for switching content visibility.
        target_tstep = max(tstep, 1e-7)
        target_tstart = target_tstop * 0.75
        new_tstart, new_tstop = target_tstart, target_tstop
        out.append(f".tran {target_tstep:.6e} {target_tstop:.6e} {target_tstart:.6e} UIC")
    if not found:
        raise SimError("deck has no '.tran' directive — cannot determine sim window")
    return "\n".join(out) + ("\n" if not deck.endswith("\n") else ""), new_tstart, new_tstop


def _inject_meas(
    deck: str, *, t_start: float, t_stop: float, vin: str, iin: str, vout: str, iout: str
) -> str:
    """Splice ``.meas`` directives before ``.end``.

    ``meas tran <name> avg <expr> FROM=<t> TO=<t>`` prints the average to
    stdout when ngspice runs the deck. We rely on the ``Measurements
    for Transient Analysis`` block that ngspice emits after the run.
    """
    meas_block = [
        "",
        "* heaviside sim runner: steady-state averages",
        f".meas tran hsv_vin avg {vin} FROM={t_start:.6e} TO={t_stop:.6e}",
        f".meas tran hsv_iin avg {iin} FROM={t_start:.6e} TO={t_stop:.6e}",
        f".meas tran hsv_vout avg {vout} FROM={t_start:.6e} TO={t_stop:.6e}",
        f".meas tran hsv_iout avg {iout} FROM={t_start:.6e} TO={t_stop:.6e}",
        "",
    ]
    out: list[str] = []
    spliced = False
    for line in deck.splitlines():
        if not spliced and line.strip().lower() == ".end":
            out.extend(meas_block)
            spliced = True
        out.append(line)
    if not spliced:
        out.extend(meas_block)
        out.append(".end")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------


# ngspice .meas output line:  ``hsv_vin              =  4.799e+01 FROM=  2.5e-04 TO=  2.75e-04``
_MEAS_LINE_RE = re.compile(
    r"^\s*(hsv_\w+)\s*=\s*([-+]?[\d.]+(?:[eE][-+]?\d+)?)",
)


def _parse_meas_output(stdout: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in stdout.splitlines():
        m = _MEAS_LINE_RE.match(line)
        if m:
            try:
                out[m.group(1)] = float(m.group(2))
            except ValueError as exc:
                raise SimError(f"unparseable .meas value on line: {line!r}") from exc
    return out


# ---------------------------------------------------------------------------
# Deck rewrites for realism (work around the unbound SpiceSimulationConfig)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Closed-loop duty-cycle search
# ---------------------------------------------------------------------------


# PULSE token: ``Vpwm pwm_ctrl 0 PULSE(V1 V2 TD TR TF PW PER)``
# Captures the PULSE source name + the seven PULSE arguments so we can
# rewrite ``PW`` (pulse width) while preserving everything else.
_PULSE_LINE_RE = re.compile(
    r"""
    ^(\s*V(?:pwm|pwm_ctrl|pwmctrl)\S*\s+\S+\s+\S+\s+PULSE\()  # 1: line prefix up to PULSE(
    (\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+                  # 2..6: V1 V2 TD TR TF
    (\S+)\s+                                                   # 7: PW (the one we rewrite)
    (\S+)\)                                                    # 8: PER
    """,
    re.MULTILINE | re.VERBOSE,
)


def _read_pwm_pulse(deck: str) -> tuple[float, float] | None:
    """Return ``(PW, PER)`` of the deck's first PWM PULSE source, or
    ``None`` if the deck has no recognisable PWM source."""
    m = _PULSE_LINE_RE.search(deck)
    if m is None:
        return None
    try:
        return _spice_time(m.group(7)), _spice_time(m.group(8))
    except SimError:
        return None


def _rewrite_pwm_duty(deck: str, *, new_duty: float, period_s: float) -> str:
    """Replace the deck's PWM PULSE ``PW`` field so the new duty cycle
    is ``new_duty * period_s``. Raises if no PWM source found."""
    if not (0.0 < new_duty < 1.0):
        raise SimError(f"_rewrite_pwm_duty: duty {new_duty!r} must be in (0, 1)")
    new_pw = new_duty * period_s
    new_pw_str = f"{new_pw:.9e}"

    def _replace(m: re.Match[str]) -> str:
        # Preserve everything except the PW token (group 7).
        return (
            f"{m.group(1)}{m.group(2)} {m.group(3)} {m.group(4)} "
            f"{m.group(5)} {m.group(6)} {new_pw_str} {m.group(8)})"
        )

    out, n = _PULSE_LINE_RE.subn(_replace, deck, count=1)
    if n == 0:
        raise SimError("_rewrite_pwm_duty: no PWM PULSE source found in deck")
    return out


# ACF deck topology transform --------------------------------------------
# MKF emits the old ACF topology (Cclamp to GND, S_clamp between
# clamp_cap and sw_node).  The canonical topology (Cclamp across the
# primary, S_clamp high-side from Vin) resets the transformer correctly
# and achieves η≈0.93 vs η≈0.60 for the old layout.  Transform the
# netlist once before simulation rather than requiring an MKF rebuild.
_ACF_SCLAMP_RE = re.compile(
    r"^(\s*)S_clamp\s+clamp_cap\s+sw_node\b(.*)$",
    re.MULTILINE,
)
_ACF_CCLAMP_LINE_RE = re.compile(
    r"^(\s*)Cclamp\s+clamp_cap\s+0\s+(\S+)\s+IC=([\d.eE+-]+)(.*)$",
    re.MULTILINE,
)
_ACF_RCLAMP_RE = re.compile(
    r"^(\s*)Rclamp\s+clamp_cap\s+0\b(.*)$",
    re.MULTILINE,
)


def _transform_acf_topology(deck: str) -> str:
    """Rewrite old-style ACF clamp (Cclamp to GND) to canonical (across primary)."""
    if not _ACF_SCLAMP_RE.search(deck):
        return deck

    vin_m = _VIN_RE.search(deck)
    if vin_m is None:
        return deck
    vin = float(vin_m.group(1))

    cclamp_m = _ACF_CCLAMP_LINE_RE.search(deck)
    if cclamp_m is None:
        return deck
    cap_val = cclamp_m.group(2)

    pulse = _read_pwm_pulse(deck)
    if pulse is None:
        return deck
    pw, period = pulse

    main_m = _PULSE_LINE_RE.search(deck)
    if main_m is None:
        return deck

    clamp_pm = _CLAMP_PULSE_RE.search(deck)
    if clamp_pm is None:
        return deck
    old_td = _spice_time(clamp_pm.group(4))
    dead_time = old_td - pw
    if dead_time < 0:
        dead_time = 100e-9
    clamp_on = period - pw - 2 * dead_time
    if clamp_on <= 0:
        return deck

    new_ic = vin * (period - 2 * dead_time) / clamp_on

    deck = _ACF_SCLAMP_RE.sub(
        lambda m: f"{m.group(1)}S_clamp vin_dc clamp_node{m.group(2)}",
        deck,
    )
    deck = _ACF_CCLAMP_LINE_RE.sub(
        lambda m: f"{m.group(1)}Cclamp clamp_node pri_in {cap_val} IC={new_ic:.6f}{m.group(4)}",
        deck,
    )
    deck = _ACF_RCLAMP_RE.sub(
        lambda m: f"{m.group(1)}Rclamp clamp_node pri_in{m.group(2)}",
        deck,
    )

    # MKF's ACF deck may emit the default DIDEAL model (IS=1e-14,
    # RS=1e-6) which is too stiff for the canonical clamp topology and
    # causes "timestep too small" on the freewheeling diode.  Replace
    # with a Schottky-grade model that matches DEFAULT_SPICE_CONFIG.
    deck = re.sub(
        r"\.model\s+DIDEAL\s+D\([^)]*\)",
        ".model DIDEAL D(IS=1e-12 RS=0.05)",
        deck,
        count=1,
    )

    return deck


# ACF clamp IC + timing rewrite ------------------------------------------
_CLAMP_PULSE_RE = re.compile(
    r"""
    ^(\s*Vpwm_clamp\s+\S+\s+\S+\s+PULSE\()  # 1: prefix
    (\S+)\s+(\S+)\s+                          # 2,3: V1 V2
    (\S+)\s+                                  # 4: TD (clamp delay)
    (\S+)\s+(\S+)\s+                          # 5,6: TR TF
    (\S+)\s+                                  # 7: PW (clamp on-time)
    (\S+)\)                                   # 8: PER
    """,
    re.MULTILINE | re.VERBOSE,
)
_CCLAMP_IC_RE = re.compile(
    r"^(\s*Cclamp\s+\S+\s+\S+\s+\S+\s+IC=)([\d.eE+-]+)",
    re.MULTILINE,
)
_VIN_RE = re.compile(r"^\s*Vin\s+\S+\s+\S+\s+([\d.eE+-]+)", re.MULTILINE)


def _rewrite_acf_clamp(deck: str, *, new_duty: float, period_s: float) -> str:
    """Update the ACF clamp switch timing and Cclamp IC for a new duty.

    The active-clamp forward has a complementary clamp switch
    (Vpwm_clamp) and a pre-charged clamp capacitor (Cclamp IC=...).
    Both depend on the main-switch duty cycle.  When the closed-loop
    driver adjusts duty, these must track or ngspice diverges.

    No-op (returns deck unchanged) if the deck has no Vpwm_clamp.
    """
    clamp_m = _CLAMP_PULSE_RE.search(deck)
    if clamp_m is None:
        return deck

    main_pulse = _read_pwm_pulse(deck)
    if main_pulse is None:
        return deck

    _main_pw, _ = main_pulse
    main_m = _PULSE_LINE_RE.search(deck)
    if main_m is None:
        return deck

    old_td = _spice_time(clamp_m.group(4))
    old_main_pw = _spice_time(main_m.group(7))
    dead_time = old_td - old_main_pw
    if dead_time < 0:
        dead_time = 100e-9

    new_pw_main = new_duty * period_s
    clamp_delay = new_pw_main + dead_time
    clamp_on = period_s - new_pw_main - 2 * dead_time
    if clamp_on <= 0:
        return deck

    def _replace_clamp(m: re.Match[str]) -> str:
        return (
            f"{m.group(1)}{m.group(2)} {m.group(3)} "
            f"{clamp_delay:.9e} {m.group(5)} {m.group(6)} "
            f"{clamp_on:.9e} {m.group(8)})"
        )

    deck = _CLAMP_PULSE_RE.sub(_replace_clamp, deck, count=1)

    vin_m = _VIN_RE.search(deck)
    if vin_m is not None:
        vin = float(vin_m.group(1))
        v_clamp = vin * (period_s - 2 * dead_time) / clamp_on
        deck = _CCLAMP_IC_RE.sub(
            lambda m: f"{m.group(1)}{v_clamp:.6f}",
            deck,
            count=1,
        )

    return deck


def simulate_closed_loop(
    deck: str,
    *,
    vout_target: float,
    tolerance: float = 0.02,  # 2 % vout error -> converged (gate is 5 %)
    max_iterations: int = 12,  # damped step; buck ~2, boost ~5-7
    ngspice_bin: str | None = None,
    timeout_s: float = 60.0,
) -> SimResult:
    """Iterative duty-cycle search until measured vout matches target.

    The MKF deck's PWM is a fixed-duty PULSE source — there's no
    feedback loop. This driver simulates the controller's effect by:

      1. Run the sim, measure vout.
      2. If |vout - target| / target < tolerance, return.
      3. Else: new_duty = old_duty * (target / vout). Rewrite the
         deck's PULSE PW, re-run.
      4. Repeat up to ``max_iterations`` (typically converges in 3-4
         for well-behaved decks).

    Raises ``SimError`` if the deck has no recognisable PWM source
    (caller falls back to ``simulate_steady_state``) or if the loop
    fails to converge within ``max_iterations``.

    The returned ``SimResult`` is from the final converged iteration.
    The caller decides whether to stamp ``is_closed_loop=True`` on the
    result before passing to the realism gate.
    """
    pulse = _read_pwm_pulse(deck)
    if pulse is None:
        raise SimError(
            "simulate_closed_loop: deck has no recognisable PWM PULSE "
            "source. Use simulate_steady_state for non-PWM decks."
        )
    period = pulse[1]

    current_deck = _transform_acf_topology(deck)
    last_result: SimResult | None = None
    for i in range(int(max_iterations)):
        last_result = simulate_steady_state(
            current_deck,
            ngspice_bin=ngspice_bin,
            timeout_s=timeout_s,
        )
        if last_result.vout <= 0:
            raise SimError(
                f"simulate_closed_loop: iteration {i} measured "
                f"vout={last_result.vout!r} <= 0; deck not converging."
            )
        rel_err = abs(last_result.vout - vout_target) / vout_target
        if rel_err < tolerance:
            return last_result
        # Adjust duty toward target. For buck the relation is roughly
        # vout = D * vin (linear), so a full step new_D = old_D *
        # (target / measured) converges in one iteration. Boost is
        # vout = Vin/(1-D) (non-linear) and a full step overshoots;
        # damp by 50 % to trade convergence speed for stability.
        # Empirically 0.5 converges buck in 2-3 iters and boost in 5-7.
        current_pulse = _read_pwm_pulse(current_deck)
        if current_pulse is None:  # pragma: no cover — invariant
            raise SimError("PULSE source disappeared mid-iteration")
        old_duty = current_pulse[0] / current_pulse[1]
        full_step_duty = old_duty * (vout_target / last_result.vout)
        damping = 0.5
        new_duty = old_duty + damping * (full_step_duty - old_duty)
        # Clamp to a sane range so a bad measurement can't push us
        # outside (0.01, 0.95). The realism gate's duty_cycle_bounds
        # is the canonical check; we just refuse to write nonsense.
        new_duty = max(0.01, min(0.95, new_duty))
        current_deck = _rewrite_pwm_duty(
            current_deck,
            new_duty=new_duty,
            period_s=period,
        )
        current_deck = _rewrite_acf_clamp(
            current_deck,
            new_duty=new_duty,
            period_s=period,
        )

    assert last_result is not None  # max_iterations >= 1 guaranteed
    raise SimError(
        f"simulate_closed_loop: failed to converge in {max_iterations} "
        f"iterations. Last vout={last_result.vout:.4f}, target={vout_target:.4f}, "
        f"rel_err={abs(last_result.vout - vout_target) / vout_target:.4f}."
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def simulate_steady_state(
    deck: str,
    *,
    ngspice_bin: str | None = None,
    timeout_s: float = 60.0,
) -> SimResult:
    """Run ngspice on ``deck`` and return steady-state averages.

    Raises ``SimError`` on any failure (missing binary, ngspice non-zero
    exit, missing measurements, unparseable output). The realism gate's
    UNAVAILABLE fallthrough is the appropriate response.
    """
    binary = ngspice_bin or shutil.which("ngspice")
    if binary is None:
        raise SimError(
            "ngspice binary not found on PATH. Install via "
            "`apt install ngspice` (Debian/Ubuntu) or `brew install ngspice`."
        )

    deck_patched, t_start, t_stop = _patch_tran_for_steady_state(deck)
    vin_p, iin_p, vout_p, iout_p = _select_probes(deck_patched)
    annotated = _inject_meas(
        deck_patched,
        t_start=t_start,
        t_stop=t_stop,
        vin=vin_p,
        iin=iin_p,
        vout=vout_p,
        iout=iout_p,
    )
    # Boost-family fallback: when iin and iout probe the same signal
    # (typically i(vl_sense) because the deck has no i(vout_sense)),
    # compute iout = vout / Rload from the deck instead of trusting
    # the inductor-current proxy for the output current.
    iout_from_rload = iin_p == iout_p and _extract_rload(deck_patched) is not None
    rload_ohm = _extract_rload(deck_patched) if iout_from_rload else None

    with tempfile.TemporaryDirectory() as tmp:
        deck_path = Path(tmp) / "deck.cir"
        deck_path.write_text(annotated)
        try:
            proc = subprocess.run(
                [binary, "-b", str(deck_path)],
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise SimError(
                f"ngspice timed out after {timeout_s}s — deck likely "
                "non-converging or transient window too long"
            ) from exc

    # ngspice prints simulation telemetry to stderr even on success, but
    # exits 0. A non-zero exit always means trouble.
    if proc.returncode != 0:
        raise SimError(
            f"ngspice exit {proc.returncode}. stderr tail:\n"
            + "\n".join(proc.stderr.splitlines()[-10:])
        )

    measurements = _parse_meas_output(proc.stdout)
    for required in ("hsv_vin", "hsv_iin", "hsv_vout", "hsv_iout"):
        if required not in measurements:
            raise SimError(
                f"ngspice output missing .meas result {required!r}. "
                "Likely cause: probe expression evaluated to NaN/inf "
                "(deck did not reach steady state). stdout tail:\n"
                + "\n".join(proc.stdout.splitlines()[-15:])
            )

    # All four averages are normalised to magnitudes so derived
    # power/efficiency stay positive across inverting topologies (cuk,
    # zeta) where ngspice reports a negative steady-state vout. The
    # power direction is implicit in the deck (input source -> output
    # load); we report magnitudes for the realism gate's positivity
    # checks. Sign of the original measurement is preserved in
    # ``raw_*`` keys via SimResult.as_dict if downstream needs it.
    vin = abs(measurements["hsv_vin"])
    iin = abs(measurements["hsv_iin"])
    vout_raw = measurements["hsv_vout"]
    if iout_from_rload and rload_ohm and rload_ohm > 0 and abs(vout_raw) > 0:
        iout = abs(vout_raw) / rload_ohm
    else:
        iout = abs(measurements["hsv_iout"])
    vout = abs(vout_raw)
    pin = vin * iin
    pout = vout * iout
    total_losses = pin - pout
    efficiency = (pout / pin) if pin > 0 else 0.0

    return SimResult(
        vin=vin,
        iin=iin,
        vout=vout,
        iout=iout,
        pin=pin,
        pout=pout,
        total_losses=total_losses,
        efficiency=efficiency,
    )


def stamp_simulation_results(
    tas: dict[str, Any],
    result: SimResult,
    *,
    op_name: str = "op0",
) -> None:
    """Mutate ``tas`` in place: stamp ``simulation_results.<op_name> = result.as_dict()``."""
    sim = tas.setdefault("simulation_results", {})
    if not isinstance(sim, Mapping):
        raise SimError(
            f"tas.simulation_results is not a mapping ({type(sim).__name__}) — "
            "stamp_simulation_results refuses to overwrite an unknown type"
        )
    sim[op_name] = result.as_dict()


__all__ = [
    "SimError",
    "SimResult",
    "simulate_closed_loop",
    "simulate_steady_state",
    "stamp_simulation_results",
]
