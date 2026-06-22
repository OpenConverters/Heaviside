"""Cross-backend equivalence: HS's Kirchhoff backend vs MKF's reference design+sim.

The Kirchhoff repo ships ``tests/reference/<topology>.mkf.json`` — MKF's *own*
design + ideal-component ngspice settled outputs for each topology (the
ground-truth contract Kirchhoff is built against, see Kirchhoff's
test_mkf_equivalence). This harness drives HS's Kirchhoff backend
(``spice_sim.simulate_from_spec(..., backend="kirchhoff")``) on the same inputs
and asserts the settled output voltage reproduces MKF's reference within
tolerance — i.e. swapping in the Kirchhoff backend yields the same converter
MKF would design and the same operating point it would simulate.

This deliberately compares against the MKF *reference fixtures* rather than HS's
own MKF deck path: the latter (``decompose_from_spec``) needs the full realize-
chain spec (diodeVoltageDrop, ambientTemperature, …) that only ``stage3_realize``
assembles, so a fair standalone comparison uses MKF's captured reference.

Real PyKirchhoff + ngspice only (never mocked); skips when either, or the
Kirchhoff reference fixtures, are unavailable.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from heaviside.decomposer import kirchhoff_adapter as ka
from heaviside.stages import spice_sim


def _reference_dir() -> Path | None:
    env = os.environ.get("KIRCHHOFF_ROOT")
    candidates = [Path(env) / "tests" / "reference"] if env else []
    candidates.append(Path(__file__).resolve().parents[2].parent / "Kirchhoff" / "tests" / "reference")
    for d in candidates:
        if d.is_dir():
            return d
    return None


_REF_DIR = _reference_dir()
# Topologies bound in PyKirchhoff that also have an MKF reference fixture.
_TOPOLOGIES = sorted(set(ka.available_topologies()) if ka.available() else set())

pytestmark = pytest.mark.skipif(
    not ka.available() or _REF_DIR is None or shutil.which("ngspice") is None,
    reason="needs PyKirchhoff, ngspice, and the Kirchhoff MKF-reference fixtures",
)


def _kirchhoff_spec(inp: dict) -> dict:
    """Build a Kirchhoff design spec from an MKF-reference 'inputs' block."""
    return {
        "designRequirements": {
            "efficiency": 1.0,  # ideal (REQUIREMENTS fidelity), matching the reference deck
            "inputVoltage": {"nominal": inp["inputVoltage"]},
            "switchingFrequency": {"nominal": inp["switchingFrequency"]},
            "outputs": [{"name": "out", "voltage": {"nominal": inp["outputVoltage"]}}],
        },
        "operatingPoints": [
            {"inputVoltage": inp["inputVoltage"], "outputs": [{"power": inp["outputPower"]}]}
        ],
    }


@pytest.mark.parametrize("topology", _TOPOLOGIES)
def test_kirchhoff_backend_matches_mkf_reference(topology: str):
    ref_path = _REF_DIR / f"{topology}.mkf.json"
    if not ref_path.exists():
        pytest.skip(f"no MKF reference fixture for {topology}")
    ref = json.loads(ref_path.read_text())
    ref_vout = ref["sim"]["voutMean"]
    spec = _kirchhoff_spec(ref["inputs"])

    result = spice_sim.simulate_from_spec(
        topology,
        spec,
        turns_ratios=[],
        magnetizing_inductance=0.0,  # Kirchhoff designs its own magnetic from the spec
        vout_target=ref["inputs"]["outputVoltage"],
        backend="kirchhoff",
    )
    vout = result.result["vout"]
    # MKF-equivalence tolerance (Kirchhoff's own contract is Vout 2% / eff 3%).
    assert vout == pytest.approx(ref_vout, rel=0.03), (
        f"{topology}: HS-Kirchhoff vout {vout:.4f} V vs MKF reference {ref_vout:.4f} V "
        f"(rel diff {abs(vout - ref_vout) / ref_vout:.3%})"
    )
