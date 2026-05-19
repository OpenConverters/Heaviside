"""End-to-end validation: TAS golden → bind real parts → tas_to_spice → ngspice.

Proves the TAS→SPICE writer in ``TAS/scripts/tas_to_spice.py`` produces
decks that ngspice can parse and simulate. This is a structural check,
not a physical-correctness check — naive 50%-duty gate drives are
emitted (no complementary phasing, no dead time), so multi-switch
topologies will shoot-through. The goal here is to confirm the
pipeline:

    decomposer golden TAS  →  bind placeholder data: URLs to real
    NDJSON entries  →  tas_to_spice  →  ngspice -b exits 0 with no
    errors.

Validates the buck (high-side switch) and boost (low-side switch +
explicit GND wire) stencils. Isolated topologies cannot be validated
yet — TAS has 53k inductors but zero multi-winding transformers, so
T1 cannot be bound. That gap is component-librarian work.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Make TAS/scripts importable for the writer.
HV_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(HV_ROOT / "TAS" / "scripts"))

from tas_to_spice import tas_to_spice  # noqa: E402

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


# -----------------------------------------------------------------------------
# Real-part bindings (verified present in TAS/data/*.ndjson; see status doc).
# -----------------------------------------------------------------------------
BIND_BUCK = {
    "Q1":    "TAS/data/mosfets.ndjson?mpn=EPC2019",
    "D1":    "TAS/data/diodes.ndjson?mpn=STPS30L60CT",
    "L1":    "TAS/data/magnetics.ndjson?mpn=744230121",
    "C_out": "TAS/data/capacitors.ndjson?mpn=UPW1H102MHD",
}

BIND_BOOST = dict(BIND_BUCK)  # identical PWM quartet


INPUTS_BUCK = {
    "operatingPoints": [{
        "inputVoltage": 48.0,
        "outputs": [{"name": "Vout", "current": 5.0}],
    }],
    "designRequirements": {
        "switchingFrequency": {"nominal": 200_000.0},
        "outputs": [{"name": "Vout", "voltage": {"nominal": 12.0}}],
    },
}

INPUTS_BOOST = {
    "operatingPoints": [{
        "inputVoltage": 12.0,
        "outputs": [{"name": "Vout", "current": 2.0}],
    }],
    "designRequirements": {
        "switchingFrequency": {"nominal": 200_000.0},
        "outputs": [{"name": "Vout", "voltage": {"nominal": 48.0}}],
    },
}


def _bind(tas: dict, bindings: dict[str, str]) -> dict:
    for stage in tas["stages"]:
        for c in stage["circuit"]["components"]:
            if c["name"] in bindings:
                c["data"] = bindings[c["name"]]
    return tas


def _run_ngspice(deck: str) -> tuple[int, str]:
    """Run ``ngspice -b`` on ``deck`` and return ``(returncode, output)``."""
    # Force ngspice to actually simulate by appending a .print line.
    deck = deck.replace(".end", ".print tran v(Vin) v(Vout)\n.end")
    with tempfile.NamedTemporaryFile("w", suffix=".cir", delete=False) as fh:
        fh.write(deck)
        path = fh.name
    try:
        result = subprocess.run(
            ["ngspice", "-b", path],
            capture_output=True, text=True, timeout=60,
        )
        return result.returncode, result.stdout + result.stderr
    finally:
        os.unlink(path)


needs_ngspice = pytest.mark.skipif(
    shutil.which("ngspice") is None,
    reason="ngspice not installed",
)


# -----------------------------------------------------------------------------


@needs_ngspice
def test_buck_writer_end_to_end():
    """Bound buck TAS → SPICE → ngspice parses cleanly + simulation runs."""
    tas = json.loads((GOLDEN_DIR / "buck_48to12_5A.tas.json").read_text())
    _bind(tas, BIND_BUCK)
    deck = tas_to_spice(tas, INPUTS_BUCK, op_index=0)

    # Structural sanity on the deck itself.
    assert "V_input Vin 0 48.0" in deck
    assert "V_gate_Q1 Q1_gate 0 PULSE" in deck
    assert "SQ1 " in deck
    assert "D1 " in deck
    assert "L1 " in deck
    assert "C_out " in deck
    assert ".tran" in deck and ".end" in deck

    rc, out = _run_ngspice(deck)
    assert rc == 0, f"ngspice failed:\n{out}"
    # ngspice exits 0 on parse errors too — look for explicit error markers.
    for bad in ("Error:", "fatal", "aborted"):
        assert bad.lower() not in out.lower(), \
            f"ngspice reported {bad!r}:\n{out}"
    # Must have produced at least one simulation row (rows look like
    # "<idx>\t<time>\t<v1>\t<v2>" — check for the print header).
    assert "v(vin)" in out.lower() and "v(vout)" in out.lower()


@needs_ngspice
def test_boost_writer_end_to_end():
    """Bound boost TAS → SPICE → ngspice parses cleanly + simulation runs.

    Boost is the simplest stencil that exercises the new GND wire
    mechanism: the low-side switch source must resolve to SPICE node 0
    via the ``GND`` interStage wire declared by the boost stencil.
    """
    tas = json.loads((GOLDEN_DIR / "boost_12to48_2A.tas.json").read_text())
    _bind(tas, BIND_BOOST)
    deck = tas_to_spice(tas, INPUTS_BOOST, op_index=0)

    # GND wire must have collapsed Q1.S onto node 0.
    assert "SQ1 sw_node 0 Q1_gate 0 SW1" in deck, deck
    # ...and C_out.2 onto node 0.
    assert "C_out Vout 0 " in deck, deck
    # Inductor sits at the input (boost signature, distinct from buck).
    assert "L1 Vin sw_node " in deck, deck

    rc, out = _run_ngspice(deck)
    assert rc == 0, f"ngspice failed:\n{out}"
    for bad in ("Error:", "fatal", "aborted"):
        assert bad.lower() not in out.lower(), \
            f"ngspice reported {bad!r}:\n{out}"
    assert "v(vin)" in out.lower() and "v(vout)" in out.lower()
