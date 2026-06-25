"""HS Kirchhoff-backend "delivers-spec" gate — mirrors Kirchhoff's requirements gate.

Kirchhoff added closed-loop control + a *requirements gate*: every topology must
DELIVER ITS SPEC (simulated steady-state Vout within ±5% of the requirement),
which is stronger than (and supersedes) the older "agree with MKF's open-loop
reference" check — Kirchhoff now regulates/designs to the target rather than
reproducing MKF's open-loop operating point (see Kirchhoff commits
"Requirements gate GREEN + MKF-equivalence reconciled", "AC control hardening").

This harness drives HS's Kirchhoff backend (``spice_sim.simulate_from_spec(...,
backend="kirchhoff")``) on each bound topology and asserts the settled output
voltage delivers the spec within the same ±5% the Kirchhoff requirements gate
uses (``kReqTol``). It reads the spec inputs from Kirchhoff's reference fixtures
(``Kirchhoff/tests/reference/<topo>.mkf.json``) purely as a convenient,
canonical source of per-topology design requirements.

Real PyKirchhoff + ngspice only (never mocked); skips when either, or the
Kirchhoff reference fixtures, are unavailable. Auto-parametrises over every
PyKirchhoff-bound topology with a fixture, so it grows as more designers bind.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from heaviside.decomposer import kirchhoff_adapter as ka
from heaviside.stages import spice_sim

#: Kirchhoff's requirements-gate tolerance on Vout (tests/test_requirements.cpp kReqTol).
_REQ_TOL = 0.05


def _reference_dir() -> Path | None:
    env = os.environ.get("KIRCHHOFF_ROOT")
    candidates = [Path(env) / "tests" / "reference"] if env else []
    candidates.append(Path(__file__).resolve().parents[2].parent / "Kirchhoff" / "tests" / "reference")
    for d in candidates:
        if d.is_dir():
            return d
    return None


_REF_DIR = _reference_dir()
_TOPOLOGIES = sorted(set(ka.available_topologies()) if ka.available() else set())

pytestmark = pytest.mark.skipif(
    not ka.available() or _REF_DIR is None or shutil.which("ngspice") is None,
    reason="needs PyKirchhoff, ngspice, and the Kirchhoff reference fixtures",
)


# Isolated-output topologies need a 2-output design spec (primary buck/boost rail
# + isolated secondary). The DELIVERED/measured output is the primary (output 0);
# we synthesize a plausible isolated secondary (half the primary voltage/power) so
# the design has its required 2 outputs.
_DUAL_OUTPUT = {"isolated_buck", "isolated_buck_boost"}


def _kirchhoff_spec(inp: dict, topology: str) -> dict:
    """Build a Kirchhoff design spec from a reference 'inputs' block. Isolated
    topologies get a synthesized secondary rail; the delivers-spec check targets
    the primary (output 0)."""
    vout = inp["outputVoltage"]
    p = inp["outputPower"]
    outs_dr = [{"name": "out0", "voltage": {"nominal": vout}}]
    outs_op = [{"power": p}]
    if topology in _DUAL_OUTPUT:
        outs_dr.append({"name": "out1", "voltage": {"nominal": vout / 2.0}})
        outs_op.append({"power": p / 2.0})
    return {
        "designRequirements": {
            "efficiency": 1.0,  # ideal (REQUIREMENTS fidelity)
            "inputVoltage": {"nominal": inp["inputVoltage"]},
            "switchingFrequency": {"nominal": inp["switchingFrequency"]},
            "outputs": outs_dr,
        },
        "operatingPoints": [{"inputVoltage": inp["inputVoltage"], "outputs": outs_op}],
    }

# Known delivers-spec gaps owned by Kirchhoff, surfaced via strict xfail so a fix
# flips the test to XPASS and forces removing the marker (signal preserved, not
# silenced). Empty now: abt #26 (fsbb 12->12 emitted L=0 -> output collapsed) was
# fixed upstream in Kirchhoff 0f1fdde — the strict xfail caught the fix, so the
# marker was removed and fsbb is a normal passing delivers-spec case again.
# Empty: abt #29 (acf non-convergent) was fixed upstream (Kirchhoff 43b1477, ACF
# node snubbers) — the strict xfail caught the fix; acf delivers again (~12.3V).
_KNOWN_GAPS: dict[str, str] = {}


def _topology_params():
    for t in _TOPOLOGIES:
        if t in _KNOWN_GAPS:
            yield pytest.param(t, marks=pytest.mark.xfail(reason=_KNOWN_GAPS[t], strict=True))
        else:
            yield t


@pytest.mark.parametrize("topology", list(_topology_params()))
def test_kirchhoff_backend_delivers_spec(topology: str):
    """HS's Kirchhoff backend must deliver each topology's spec Vout within ±5%
    (the same contract Kirchhoff's closed-loop requirements gate enforces).
    Magnitude is compared so inverting topologies (cuk) count as delivering."""
    # Reference fixtures are named by Kirchhoff's base name (e.g. psfb, src, forward).
    ref_path = _REF_DIR / f"{ka.kirchhoff_base(topology)}.mkf.json"
    if not ref_path.exists():
        pytest.skip(f"no reference fixture for {topology} ({ka.kirchhoff_base(topology)})")
    inp = json.loads(ref_path.read_text())["inputs"]
    target_vout = float(inp["outputVoltage"])  # primary rail (output 0) for isolated topologies
    spec = _kirchhoff_spec(inp, topology)

    result = spice_sim.simulate_from_spec(
        topology,
        spec,
        turns_ratios=[],
        magnetizing_inductance=0.0,  # Kirchhoff designs its own magnetic from the spec
        vout_target=target_vout,
        backend="kirchhoff",
    )
    vout = result.result["vout"]
    # Compare magnitude: an inverting topology delivering -V satisfies a +V spec.
    assert abs(vout) == pytest.approx(abs(target_vout), rel=_REQ_TOL), (
        f"{topology}: HS-Kirchhoff vout {vout:.4f} V does not deliver spec "
        f"{target_vout:.4f} V within {_REQ_TOL:.0%} (rel err {abs(abs(vout) - abs(target_vout)) / abs(target_vout):.3%})"
    )
