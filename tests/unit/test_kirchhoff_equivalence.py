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


def _kirchhoff_spec(inp: dict) -> dict:
    """Build a Kirchhoff design spec from a reference 'inputs' block."""
    return {
        "designRequirements": {
            "efficiency": 1.0,  # ideal (REQUIREMENTS fidelity)
            "inputVoltage": {"nominal": inp["inputVoltage"]},
            "switchingFrequency": {"nominal": inp["switchingFrequency"]},
            "outputs": [{"name": "out", "voltage": {"nominal": inp["outputVoltage"]}}],
        },
        "operatingPoints": [
            {"inputVoltage": inp["inputVoltage"], "outputs": [{"power": inp["outputPower"]}]}
        ],
    }


@pytest.mark.parametrize("topology", _TOPOLOGIES)
def test_kirchhoff_backend_delivers_spec(topology: str):
    """HS's Kirchhoff backend must deliver each topology's spec Vout within ±5%
    (the same contract Kirchhoff's closed-loop requirements gate enforces)."""
    ref_path = _REF_DIR / f"{topology}.mkf.json"
    if not ref_path.exists():
        pytest.skip(f"no reference fixture for {topology}")
    inp = json.loads(ref_path.read_text())["inputs"]
    target_vout = float(inp["outputVoltage"])
    spec = _kirchhoff_spec(inp)

    result = spice_sim.simulate_from_spec(
        topology,
        spec,
        turns_ratios=[],
        magnetizing_inductance=0.0,  # Kirchhoff designs its own magnetic from the spec
        vout_target=target_vout,
        backend="kirchhoff",
    )
    vout = result.result["vout"]
    assert vout == pytest.approx(target_vout, rel=_REQ_TOL), (
        f"{topology}: HS-Kirchhoff vout {vout:.4f} V does not deliver spec "
        f"{target_vout:.4f} V within {_REQ_TOL:.0%} (rel err {abs(vout - target_vout) / target_vout:.3%})"
    )
