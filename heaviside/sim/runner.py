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
    # Buck / boost / cuk / sepic / zeta / 4SBB / flyback (single-output)
    "single_output_dc": [
        ("v(vin_dc)",  "i(vin_sense)", "v(vout)",       "i(vout_sense)"),
        ("v(vin_dc)",  "i(vin_sense)", "v(vout)",       "i(vl_sense)"),
        ("v(vin_dc)",  "i(vin_sense)", "v(vout_cap)",   "i(vout_sense)"),
        # Vienna single-phase deck: vin is the AC phase voltage source
        ("v(vphase)",  "i(vphase)",    "v(vdc_cap)",    "i(vph_sense)"),
    ],
}


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

    deck_patched, t_start, t_stop = _patch_tran_for_steady_state(deck)
    vin_p, iin_p, vout_p, iout_p = _select_probes(deck_patched)
    annotated = _inject_meas(
        deck_patched, t_start=t_start, t_stop=t_stop,
        vin=vin_p, iin=iin_p, vout=vout_p, iout=iout_p,
    )

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
    "simulate_steady_state",
    "stamp_simulation_results",
]
