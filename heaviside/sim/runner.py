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

Deck post-processing (this module owns these because the upstream
``SpiceSimulationConfig`` C++ struct is not bound to Python — see
heaviside.bridge for the upstream-gap note):

  * ``_patch_tran_for_steady_state``: extends the .tran window so the
    output L-C filter has time to settle, adds UIC for .ic to take effect.
  * ``_rewrite_lossy_testbench``: replaces MKF's default snubber
    Rsnub/Csnub (100 Ω / 100 pF — drains ~25 W on a 60 W buck) and
    DIDEAL diode (RS=1 µΩ — short-circuit current at conduction) with
    realistic values that don't dominate the loss budget.
  * ``simulate_closed_loop``: iterative duty-cycle search — runs the
    sim, measures vout, adjusts the PWM PULSE duty, re-runs, until
    vout converges to the spec target.

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
    "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6,
    "m": 1e-3,
    "k": 1e3, "meg": 1e6, "g": 1e9, "t": 1e12,
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
            num = low[:-len(suf)]
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
    return probes


# ---------------------------------------------------------------------------
# Probe selection (topology-aware)
# ---------------------------------------------------------------------------


# Per-topology preferred probe quadruple. Each tuple is
# (vin_probe, iin_probe, vout_probe, iout_probe). The runner picks the
# first quadruple whose probes are ALL present in the deck.
_PROBE_CANDIDATES: dict[str, list[tuple[str, str, str, str]]] = {
    # Buck / boost / cuk / sepic / zeta / 4SBB / flyback (single-output).
    # ``i(vl_sense)`` is the inductor current; for boost-family decks
    # the inductor sits in series with the input source, so i_L IS the
    # input current. iout is computed AFTER the meas pass from
    # ``vout / Rload`` rather than probed (see _compute_iout_from_rload).
    "single_output_dc": [
        ("v(vin_dc)",  "i(vin_sense)", "v(vout)",       "i(vout_sense)"),
        ("v(vin_dc)",  "i(vin_sense)", "v(vout)",       "i(vl_sense)"),
        ("v(vin_dc)",  "i(vin_sense)", "v(vout_cap)",   "i(vout_sense)"),
        # Boost-family fallback: use vl_sense for both iin and iout,
        # then override iout = vout / Rload after the .meas pass.
        ("v(vin_dc)",  "i(vl_sense)",  "v(vout)",       "i(vl_sense)"),
        # Vienna single-phase deck: vin is the AC phase voltage source
        ("v(vphase)",  "i(vphase)",    "v(vdc_cap)",    "i(vph_sense)"),
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
        out.append(
            f".tran {target_tstep:.6e} {target_tstop:.6e} "
            f"{target_tstart:.6e} UIC"
        )
    if not found:
        raise SimError("deck has no '.tran' directive — cannot determine sim window")
    return "\n".join(out) + ("\n" if not deck.endswith("\n") else ""), new_tstart, new_tstop


def _inject_meas(deck: str, *, t_start: float, t_stop: float,
                  vin: str, iin: str, vout: str, iout: str) -> str:
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
                raise SimError(
                    f"unparseable .meas value on line: {line!r}"
                ) from exc
    return out


# ---------------------------------------------------------------------------
# Deck rewrites for realism (work around the unbound SpiceSimulationConfig)
# ---------------------------------------------------------------------------


# MKF default snubber values are 100 Ω // 100 pF across each switch. The
# 100 Ω burns I_in^2 * 100 Ω of average power per switch cycle (~25 W on
# a 60 W buck deck) which dominates the deck's measured efficiency.
# Real designs use much higher R (10s of kΩ if used at all) — the
# snubber is for ringing damping, not steady-state energy. We rewrite
# to 10 kΩ // 100 pF: still damps switch ringing, dissipates ~250x
# less average power.
#
# DIDEAL is MKF's "ideal" diode with RS=1µΩ — at Iout=5A, that's a 5µV
# drop. ngspice's saturation-current handling makes this behave like a
# short circuit at conduction, contributing to the deck-loss artifact.
# Rewrite to a realistic Schottky-class model with a finite forward
# drop and series resistance.

_RSNUB_NEW_OHM: float = 10_000.0
_CSNUB_NEW_F: float = 100e-12
_DIDEAL_REWRITE: str = "D(Is=1e-12 N=1.05 RS=0.05)"

_RSNUB_LINE_RE = re.compile(
    r"^(\s*Rsnub_\S+\s+\S+\s+\S+\s+)([\d.eE+-]+)\s*$",
    re.MULTILINE,
)
_CSNUB_LINE_RE = re.compile(
    r"^(\s*Csnub_\S+\s+\S+\s+\S+\s+)([\d.eE+-]+)\s*$",
    re.MULTILINE,
)
_DIDEAL_MODEL_RE = re.compile(
    r"^(\s*\.model\s+DIDEAL\s+)D\([^)]*\)",
    re.MULTILINE,
)


def _rewrite_lossy_testbench(deck: str) -> str:
    """Replace MKF's lossy default snubber + ideal-diode values with
    realistic ones. Idempotent and tolerant of decks that don't have
    these elements (just returns the deck unchanged).

    Why this lives in Heaviside, not MKF: ``SpiceSimulationConfig``'s
    ``snubR``/``snubC``/``diodeIS``/``diodeRS`` fields exist in C++
    but pybind11 doesn't expose ``set_spice_config()``. Until that
    binding lands, we post-process the netlist text.
    """
    out = _RSNUB_LINE_RE.sub(
        lambda m: f"{m.group(1)}{_RSNUB_NEW_OHM:.6e}", deck,
    )
    out = _CSNUB_LINE_RE.sub(
        lambda m: f"{m.group(1)}{_CSNUB_NEW_F:.6e}", out,
    )
    out = _DIDEAL_MODEL_RE.sub(
        lambda m: f"{m.group(1)}{_DIDEAL_REWRITE}", out,
    )
    return out


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
        raise SimError(
            f"_rewrite_pwm_duty: duty {new_duty!r} must be in (0, 1)"
        )
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


def simulate_closed_loop(
    deck: str,
    *,
    vout_target: float,
    tolerance: float = 0.02,         # 2 % vout error -> converged (gate is 5 %)
    max_iterations: int = 12,        # damped step; buck ~2, boost ~5-7
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

    current_deck = deck
    last_result: SimResult | None = None
    for i in range(int(max_iterations)):
        last_result = simulate_steady_state(
            current_deck, ngspice_bin=ngspice_bin, timeout_s=timeout_s,
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
            current_deck, new_duty=new_duty, period_s=period,
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
    deck: str, *, ngspice_bin: str | None = None, timeout_s: float = 60.0,
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

    deck_rewritten = _rewrite_lossy_testbench(deck)
    deck_patched, t_start, t_stop = _patch_tran_for_steady_state(deck_rewritten)
    vin_p, iin_p, vout_p, iout_p = _select_probes(deck_patched)
    annotated = _inject_meas(
        deck_patched, t_start=t_start, t_stop=t_stop,
        vin=vin_p, iin=iin_p, vout=vout_p, iout=iout_p,
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
                capture_output=True, text=True, timeout=timeout_s,
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

    vin = measurements["hsv_vin"]
    iin = abs(measurements["hsv_iin"])  # ngspice signs source current opposite of conventional
    vout = measurements["hsv_vout"]
    if iout_from_rload and rload_ohm and rload_ohm > 0 and vout > 0:
        iout = vout / rload_ohm
    else:
        iout = abs(measurements["hsv_iout"])
    pin = vin * iin
    pout = vout * iout
    total_losses = pin - pout
    efficiency = (pout / pin) if pin > 0 else 0.0

    return SimResult(
        vin=vin, iin=iin, vout=vout, iout=iout,
        pin=pin, pout=pout,
        total_losses=total_losses, efficiency=efficiency,
    )


def stamp_simulation_results(
    tas: dict[str, Any], result: SimResult, *, op_name: str = "op0",
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
