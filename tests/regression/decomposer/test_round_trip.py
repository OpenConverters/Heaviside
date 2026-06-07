"""Full SPICE round-trip: TAS_bound → SPICE_A → TAS_inline → SPICE_B.

Validates the SPICE→TAS reader in ``TAS/scripts/spice_to_tas.py`` and the
TAS→SPICE writer's inline-value + no-controller fallback paths.

Pipeline:
    1. Decomposer golden TAS  →  bind placeholder ``data:`` URLs to real
       NDJSON entries.
    2. ``tas_to_spice`` emits SPICE_A.
    3. ``spice_to_tas`` reads SPICE_A back into a TAS doc with inline
       ``value`` fields and no controller stage.
    4. ``tas_to_spice`` re-emits SPICE_B from the inline-value TAS.
    5. Both decks must (a) simulate cleanly in ngspice and (b) describe
       the same element multiset (kind + value/model fingerprint).

Only non-isolated topologies are exercised — ``spice_to_tas`` cannot
recover isolation transformers from a flat netlist (no K-statements
allowed in MVP).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HV_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(HV_ROOT / "TAS" / "scripts"))

from _spice_parser import parse_spice  # noqa: E402
from spice_to_tas import spice_to_tas  # noqa: E402
from tas_to_spice import tas_to_spice  # noqa: E402

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


BIND_BUCK = {
    "Q1": "TAS/data/mosfets.ndjson?mpn=EPC2019",
    "D1": "TAS/data/diodes.ndjson?mpn=STPS30L60CT",
    "L1": "TAS/data/magnetics.ndjson?mpn=744230121",
    "C_out": "TAS/data/capacitors.ndjson?mpn=UPW1H102MHD",
}
BIND_BOOST = dict(BIND_BUCK)

# Cuk / SEPIC / Zeta share an identical BOM: 1 FET, 1 diode, 2 inductors,
# 1 flying cap, 1 output cap. The two inductors are magnetically
# independent (no K-coupling), so each binds to the same single-winding
# part — the writer emits two distinct ``L`` elements.
BIND_4ELEM = {
    "Q1": "TAS/data/mosfets.ndjson?mpn=EPC2019",
    "D1": "TAS/data/diodes.ndjson?mpn=STPS30L60CT",
    "L1": "TAS/data/magnetics.ndjson?mpn=744230121",
    "L2": "TAS/data/magnetics.ndjson?mpn=744230121",
    "C_flying": "TAS/data/capacitors.ndjson?mpn=UPW1H102MHD",
    "C_out": "TAS/data/capacitors.ndjson?mpn=UPW1H102MHD",
}

# Four-switch buck-boost: 4 mosfets, 1 inductor, input + output caps.
BIND_4SBB = {
    "Q1": "TAS/data/mosfets.ndjson?mpn=EPC2019",
    "Q2": "TAS/data/mosfets.ndjson?mpn=EPC2019",
    "Q3": "TAS/data/mosfets.ndjson?mpn=EPC2019",
    "Q4": "TAS/data/mosfets.ndjson?mpn=EPC2019",
    "L1": "TAS/data/magnetics.ndjson?mpn=744230121",
    "C_in": "TAS/data/capacitors.ndjson?mpn=UPW1H102MHD",
    "C_out": "TAS/data/capacitors.ndjson?mpn=UPW1H102MHD",
}

INPUTS_BUCK = {
    "operatingPoints": [
        {
            "inputVoltage": 48.0,
            "outputs": [{"name": "Vout", "current": 5.0}],
        }
    ],
    "designRequirements": {
        "switchingFrequency": {"nominal": 200_000.0},
        "outputs": [{"name": "Vout", "voltage": {"nominal": 12.0}}],
    },
}
INPUTS_BOOST = {
    "operatingPoints": [
        {
            "inputVoltage": 12.0,
            "outputs": [{"name": "Vout", "current": 2.0}],
        }
    ],
    "designRequirements": {
        "switchingFrequency": {"nominal": 200_000.0},
        "outputs": [{"name": "Vout", "voltage": {"nominal": 48.0}}],
    },
}


def _bind(tas: dict, bindings: dict[str, str]) -> dict:
    for stage in tas["topology"]["stages"]:
        for c in stage["circuit"]["components"]:
            if c["name"] in bindings:
                c["data"] = bindings[c["name"]]
    return tas


def _run_ngspice(deck: str) -> tuple[int, str]:
    deck = deck.replace(".end", ".print tran v(Vin) v(Vout)\n.end")
    with tempfile.NamedTemporaryFile("w", suffix=".cir", delete=False) as fh:
        fh.write(deck)
        path = fh.name
    try:
        r = subprocess.run(
            ["ngspice", "-b", path],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return r.returncode, r.stdout + r.stderr
    finally:
        os.unlink(path)


def _fingerprint(deck_text: str) -> list[tuple[str, str, str]]:
    """Element multiset signature: (kind, value-or-model, refdes-prefix).

    Node names are deliberately omitted — the reader collapses to a
    single ``switchingCell`` stage and renames internal nets, so node
    identity will not match. Element kinds, values, and devices types do.
    """
    deck = parse_spice(deck_text)
    sig: list[tuple[str, str, str]] = []
    for el in deck.elements:
        v = el.value or ""
        if v.startswith("PULSE("):
            vk = "PULSE"
        elif el.kind in ("switch", "diode", "coupling"):
            vk = el.model or v
        else:
            m = re.match(r"[-+]?[\d.eE+-]+", v.strip())
            try:
                vk = f"{float(m.group(0)):.3e}" if m else v
            except (ValueError, AttributeError):
                vk = v
        sig.append((el.kind, vk, el.refdes[:1]))
    return sorted(sig)


needs_ngspice = pytest.mark.skipif(
    shutil.which("ngspice") is None,
    reason="ngspice not installed",
)


@pytest.mark.parametrize(
    "golden,bindings,inputs",
    [
        ("buck_48to12_5A.tas.json", BIND_BUCK, INPUTS_BUCK),
        ("boost_12to48_2A.tas.json", BIND_BOOST, INPUTS_BOOST),
        ("cuk_48to12_5A.tas.json", BIND_4ELEM, INPUTS_BUCK),
        ("sepic_48to12_5A.tas.json", BIND_4ELEM, INPUTS_BUCK),
        ("zeta_48to12_5A.tas.json", BIND_4ELEM, INPUTS_BUCK),
        ("4sbb_48to12_5A.tas.json", BIND_4SBB, INPUTS_BUCK),
    ],
    ids=["buck", "boost", "cuk", "sepic", "zeta", "4sbb"],
)
@needs_ngspice
def test_round_trip(golden: str, bindings: dict[str, str], inputs: dict):
    """TAS → SPICE → TAS → SPICE: both decks simulate + fingerprints match."""
    tas_a = _bind(json.loads((GOLDEN_DIR / golden).read_text()), bindings)
    spice_a = tas_to_spice(tas_a["topology"], inputs, op_index=0)

    tas_b = spice_to_tas(spice_a)
    # Reader must emit a single switchingCell stage with no controller.
    assert len(tas_b["stages"]) == 1
    assert tas_b["stages"][0]["role"] == "switchingCell"
    # Every non-mosfet/diode component carries an inline SI value.
    for c in tas_b["stages"][0]["circuit"]["components"]:
        if c["category"] in ("mosfet", "diode"):
            assert "value" not in c
        else:
            assert isinstance(c.get("value"), float)

    spice_b = tas_to_spice(tas_b, inputs, op_index=0)

    rc_a, out_a = _run_ngspice(spice_a)
    rc_b, out_b = _run_ngspice(spice_b)
    assert rc_a == 0, f"ngspice on SPICE_A failed:\n{out_a}"
    assert rc_b == 0, f"ngspice on SPICE_B failed:\n{out_b}"
    for bad in ("error:", "fatal", "aborted"):
        assert bad not in out_a.lower(), f"SPICE_A: {bad!r}\n{out_a}"
        assert bad not in out_b.lower(), f"SPICE_B: {bad!r}\n{out_b}"

    fa, fb = _fingerprint(spice_a), _fingerprint(spice_b)
    assert fa == fb, (
        f"Round-trip element multisets differ.\n"
        f"  only in A: {sorted(set(fa) - set(fb))}\n"
        f"  only in B: {sorted(set(fb) - set(fa))}"
    )


# -----------------------------------------------------------------------------
# Isolated topology round-trip (K-statement handling)
# -----------------------------------------------------------------------------
# TAS contains zero multi-winding transformers (component-librarian work).
# To exercise the writer's inline multi-winding path and the reader's
# K-statement grouping, we synthesise a fully-inline flyback TAS by:
#   * binding Q1 / D_out0 / C_out0 to real parts (these all exist),
#   * giving T1 inline ``inductances`` + ``coupling`` (no NDJSON entry).
# A SPICE→TAS→SPICE round-trip must preserve the element multiset
# including the transformer's two ``L`` inductances and one ``K`` pair.


INPUTS_FLYBACK = {
    "operatingPoints": [
        {
            "inputVoltage": 48.0,
            "outputs": [{"name": "Vout0", "current": 2.0}],
        }
    ],
    "designRequirements": {
        "switchingFrequency": {"nominal": 200_000.0},
        "outputs": [{"name": "Vout0", "voltage": {"nominal": 12.0}}],
    },
}


def _bind_flyback_inline(tas: dict) -> dict:
    """Bind Q1/D_out0/C_out0 to real parts, T1 to inline values."""
    bindings = {
        "Q1": "TAS/data/mosfets.ndjson?mpn=EPC2019",
        "D_out0": "TAS/data/diodes.ndjson?mpn=STPS30L60CT",
        "C_out0": "TAS/data/capacitors.ndjson?mpn=UPW1H102MHD",
    }
    for stage in tas["topology"]["stages"]:
        for c in stage["circuit"]["components"]:
            if c["name"] in bindings:
                c["data"] = bindings[c["name"]]
            elif c["name"] == "T1":
                # Inline two-winding transformer (pri, sec0). The writer
                # sorts winding labels alphabetically, so ``inductances``
                # must be in sorted-label order: [pri, sec0].
                c.pop("data", None)
                c["category"] = "magnetic"
                c["inductances"] = [1.0e-3, 250e-6]
                c["coupling"] = 0.999
    return tas


@needs_ngspice
def test_round_trip_isolated_flyback():
    """Isolated flyback: K-statement readback + multi-winding inline write."""
    tas_a = _bind_flyback_inline(
        json.loads((GOLDEN_DIR / "flyback_48to12_2A.tas.json").read_text())
    )
    spice_a = tas_to_spice(tas_a["topology"], INPUTS_FLYBACK, op_index=0)

    # SPICE_A must contain the transformer pair and one K.
    assert "LT1_pri " in spice_a, spice_a
    assert "LT1_sec0 " in spice_a, spice_a
    assert "KT1_1 LT1_pri LT1_sec0 9.990000e-01" in spice_a, spice_a

    tas_b = spice_to_tas(spice_a)

    # Reader must collapse the K-pair back into a single multi-winding
    # magnetic with inline inductances + coupling.
    comps = tas_b["stages"][0]["circuit"]["components"]
    # Multi-winding magnetics carry per-winding 'inductances' (vs single-
    # winding inductors that have a scalar 'value' or none). 'pins' is no
    # longer emitted — winding identity is encoded in connection endpoints.
    transformers = [c for c in comps if c["category"] == "magnetic" and c.get("inductances")]
    assert len(transformers) == 1, comps
    t1 = transformers[0]
    # Derive pin set from observed connections (single switchingCell stage).
    t1_pins = sorted(
        {
            ep["pin"]
            for w in tas_b.get("interStageCircuit", [])
            for ep in w.get("endpoints", [])
            if ep["component"] == t1["name"]
        }
    )
    assert t1_pins == ["pri.1", "pri.2", "sec0.1", "sec0.2"], t1_pins
    assert t1["coupling"] == pytest.approx(0.999, abs=1e-9)
    # sorted-label order matches: pri then sec0.
    assert t1["inductances"] == pytest.approx([1.0e-3, 250e-6])

    spice_b = tas_to_spice(tas_b, INPUTS_FLYBACK, op_index=0)

    # Both decks must simulate cleanly. SPICE_B will not necessarily
    # converge to a meaningful operating point (naive single-switch
    # gate drive with no controller), but ngspice must parse + run it
    # without errors.
    rc_a, out_a = _run_ngspice(spice_a)
    rc_b, out_b = _run_ngspice(spice_b)
    assert rc_a == 0, f"ngspice on SPICE_A failed:\n{out_a}"
    assert rc_b == 0, f"ngspice on SPICE_B failed:\n{out_b}"

    fa, fb = _fingerprint(spice_a), _fingerprint(spice_b)
    assert fa == fb, (
        f"Isolated round-trip element multisets differ.\n"
        f"  only in A: {sorted(set(fa) - set(fb))}\n"
        f"  only in B: {sorted(set(fb) - set(fa))}"
    )


# -----------------------------------------------------------------------------
# LLC half-bridge round-trip (3-winding CT secondary, real bus-bal resistors)
# -----------------------------------------------------------------------------
# Exercises:
#   * 3-winding K-group readback (pri + sec1 + sec2 → ``inductances`` of
#     length 3, all-pairs K verified equal).
#   * Real ``R_bal_hi`` / ``R_bal_lo`` survival — these collide with
#     MKF's testbench ``Rbal_*`` pattern, and an over-eager bleeder
#     regex used to strip them on readback.
#   * Real bus capacitors ``C_bus_hi`` / ``C_bus_lo`` on the half-bridge
#     midpoint divider.


INPUTS_LLC = {
    "operatingPoints": [
        {
            "inputVoltage": 48.0,
            "outputs": [{"name": "Vout0", "current": 5.0}],
        }
    ],
    "designRequirements": {
        "switchingFrequency": {"nominal": 200_000.0},
        "outputs": [{"name": "Vout0", "voltage": {"nominal": 12.0}}],
    },
}

# All passive values are typical for a 48→12 V / 5 A LLC at 200 kHz.
_LLC_INLINE = {
    "C_bus_hi": ("capacitor", 1.0e-6),
    "C_bus_lo": ("capacitor", 1.0e-6),
    "R_bal_hi": ("resistor", 100e3),
    "R_bal_lo": ("resistor", 100e3),
    "C_r": ("capacitor", 295.2e-9),
    "L_r": ("magnetic", 2.86e-6),
    "C_out0": ("capacitor", 47e-6),
}


def _bind_llc_inline(tas: dict) -> dict:
    """Strip ``data:`` URLs; replace with inline values + T1 transformer."""
    for stage in tas["topology"]["stages"]:
        for c in stage["circuit"]["components"]:
            if c["name"] in _LLC_INLINE:
                cat, val = _LLC_INLINE[c["name"]]
                c.pop("data", None)
                c["category"] = cat
                c["value"] = val
            elif c["name"] == "T1":
                c.pop("data", None)
                c["category"] = "magnetic"
                # sorted-label order: pri (1mH), sec1 (250µH), sec2 (250µH).
                c["inductances"] = [1.0e-3, 250e-6, 250e-6]
                c["coupling"] = 0.999
    return tas


@needs_ngspice
def test_round_trip_isolated_llc():
    """LLC HB with CT secondary: 3-winding K-group + bus-balance survival."""
    tas_a = _bind_llc_inline(json.loads((GOLDEN_DIR / "llc_48to12_5A.tas.json").read_text()))
    spice_a = tas_to_spice(tas_a["topology"], INPUTS_LLC, op_index=0)

    # 3 windings → 3 L-elements + 3 K-pairs.
    for w in ("LT1_pri ", "LT1_sec1 ", "LT1_sec2 "):
        assert w in spice_a, f"missing {w} in:\n{spice_a}"
    for k in ("KT1_1 ", "KT1_2 ", "KT1_3 "):
        assert k in spice_a, f"missing {k} in:\n{spice_a}"
    # Real bus-balance resistors must survive (these collide with MKF's
    # testbench Rbal_* pattern but must NOT be stripped by the reader).
    assert "R_bal_hi " in spice_a, spice_a
    assert "R_bal_lo " in spice_a, spice_a

    tas_b = spice_to_tas(spice_a)

    # Reader must keep R_bal_hi / R_bal_lo as real BOM (regression
    # against the over-eager _BUS_BAL_RE bleeder filter).
    b_names = {
        c["name"]
        for c in tas_b["stages"][0]["circuit"]["components"]
        if not c["name"].startswith("P_")
    }
    assert "R_bal_hi" in b_names, b_names
    assert "R_bal_lo" in b_names, b_names

    # The 3-winding transformer collapses back to one magnetic component
    # with 6 pins (pri.1/.2 + sec0.1/.2 + sec1.1/.2 in sorted-label order).
    transformers = [
        c
        for c in tas_b["stages"][0]["circuit"]["components"]
        if c["category"] == "magnetic" and c.get("inductances")
    ]
    assert len(transformers) == 1, transformers
    t1 = transformers[0]
    t1_pins = sorted(
        {
            ep["pin"]
            for w in tas_b.get("interStageCircuit", [])
            for ep in w.get("endpoints", [])
            if ep["component"] == t1["name"]
        }
    )
    assert t1_pins == [
        "pri.1",
        "pri.2",
        "sec0.1",
        "sec0.2",
        "sec1.1",
        "sec1.2",
    ]
    assert t1["coupling"] == pytest.approx(0.999, abs=1e-9)
    # Inductances are emitted in sorted-label order: pri, sec0, sec1.
    assert t1["inductances"] == pytest.approx([1.0e-3, 250e-6, 250e-6])

    spice_b = tas_to_spice(tas_b, INPUTS_LLC, op_index=0)

    rc_a, out_a = _run_ngspice(spice_a)
    rc_b, out_b = _run_ngspice(spice_b)
    assert rc_a == 0, f"ngspice on SPICE_A failed:\n{out_a}"
    assert rc_b == 0, f"ngspice on SPICE_B failed:\n{out_b}"

    fa, fb = _fingerprint(spice_a), _fingerprint(spice_b)
    assert fa == fb, (
        f"LLC round-trip element multisets differ.\n"
        f"  only in A: {sorted(set(fa) - set(fb))}\n"
        f"  only in B: {sorted(set(fb) - set(fa))}"
    )


# -----------------------------------------------------------------------------
# Generic isolated round-trip (parametrized over topologies that bind cleanly
# with one inline binding map). Active-clamp forward and two-switch forward
# both have a single output stage with an LC filter, single secondary winding.
# -----------------------------------------------------------------------------

_GENERIC_INLINE = {
    # capacitors (chosen by topology role, not part-number)
    "C_clamp": ("capacitor", 470e-9),  # ACF clamp cap
    "C_pri": ("capacitor", 1.0e-6),  # isolated-buck primary cap (not used here)
    "C_out0": ("capacitor", 100e-6),  # output bulk cap
    # inductors
    "L_out0": ("magnetic", 10e-6),  # output choke (post-rectifier LC)
}

INPUTS_ACF = {
    "operatingPoints": [
        {
            "inputVoltage": 48.0,
            "outputs": [{"name": "Vout0", "current": 5.0}],
        }
    ],
    "designRequirements": {
        "switchingFrequency": {"nominal": 200_000.0},
        "outputs": [{"name": "Vout0", "voltage": {"nominal": 12.0}}],
    },
}
INPUTS_2SF = {
    "operatingPoints": [
        {
            "inputVoltage": 48.0,
            "outputs": [{"name": "Vout0", "current": 2.0}],
        }
    ],
    "designRequirements": {
        "switchingFrequency": {"nominal": 200_000.0},
        "outputs": [{"name": "Vout0", "voltage": {"nominal": 5.0}}],
    },
}

# Isolated buck (flybuck) and isolated buck-boost are dual-output:
# Vout_pri is the regulated primary-side rail (the converter's main
# output, controlled by the feedback loop), and Vout0 is the isolated
# auxiliary rail mirrored through T1. Both ports are real outputs and
# must be specified in inputs — see isobuck/isobb stencil docs.
INPUTS_DUAL_ISO = {
    "operatingPoints": [
        {
            "inputVoltage": 48.0,
            "outputs": [
                {"name": "Vout_pri", "current": 3.0},
                {"name": "Vout0", "current": 1.0},
            ],
        }
    ],
    "designRequirements": {
        "switchingFrequency": {"nominal": 200_000.0},
        "outputs": [
            {"name": "Vout_pri", "voltage": {"nominal": 12.0}},
            {"name": "Vout0", "voltage": {"nominal": 5.0}},
        ],
    },
}


def _bind_generic_inline(tas: dict, t1_windings: dict[str, float]) -> dict:
    """Replace placeholder URLs with inline values for a single-T1 stencil."""
    for stage in tas["topology"]["stages"]:
        for c in stage["circuit"]["components"]:
            if c["name"] in _GENERIC_INLINE:
                cat, val = _GENERIC_INLINE[c["name"]]
                c.pop("data", None)
                c["category"] = cat
                c["value"] = val
            elif c["name"] == "T1":
                c.pop("data", None)
                c["category"] = "magnetic"
                labels = sorted(t1_windings)  # writer sorts labels
                c["inductances"] = [t1_windings[l] for l in labels]
                c["coupling"] = 0.999
    return tas


@pytest.mark.parametrize(
    "golden,inputs,t1_windings",
    [
        (
            "acf_48to12_5A.tas.json",
            INPUTS_ACF,
            {"pri": 1.0e-3, "sec0": 250e-6},
        ),
        (
            "2sforward_48to5_2A.tas.json",
            INPUTS_2SF,
            {"pri": 1.0e-3, "sec0": 100e-6},
        ),
        (
            "isobuck_48to12_5A.tas.json",
            INPUTS_DUAL_ISO,
            {"pri": 1.0e-3, "sec0": 250e-6},
        ),
        (
            "isobb_48to12_5A.tas.json",
            INPUTS_DUAL_ISO,
            {"pri": 1.0e-3, "sec0": 250e-6},
        ),
        # ssforward: stencil augments MKF's primary-only deck with a
        # synthetic output stage (sec0 winding + forward/freewheel diodes
        # + LC filter + Vout0 port), so it round-trips like ACF / 2SF.
        (
            "ssforward_48to12_5A.tas.json",
            INPUTS_2SF,
            {"pri": 1.0e-3, "demag": 1.0e-3, "sec0": 250e-6},
        ),
    ],
    ids=[
        "active_clamp_forward",
        "two_switch_forward",
        "isolated_buck_dual",
        "isolated_buck_boost_dual",
        "single_switch_forward",
    ],
)
@needs_ngspice
def test_round_trip_isolated_generic(
    golden: str,
    inputs: dict,
    t1_windings: dict[str, float],
):
    """Inline-bind a single-T1 isolated topology and round-trip through SPICE."""
    tas_a = _bind_generic_inline(
        json.loads((GOLDEN_DIR / golden).read_text()),
        t1_windings,
    )
    spice_a = tas_to_spice(tas_a["topology"], inputs, op_index=0)
    tas_b = spice_to_tas(spice_a)
    spice_b = tas_to_spice(tas_b, inputs, op_index=0)

    rc_a, out_a = _run_ngspice(spice_a)
    rc_b, out_b = _run_ngspice(spice_b)
    assert rc_a == 0, f"ngspice on SPICE_A failed:\n{out_a}"
    assert rc_b == 0, f"ngspice on SPICE_B failed:\n{out_b}"

    fa, fb = _fingerprint(spice_a), _fingerprint(spice_b)
    assert fa == fb, (
        f"{golden} round-trip multisets differ.\n"
        f"  only in A: {sorted(set(fa) - set(fb))}\n"
        f"  only in B: {sorted(set(fb) - set(fa))}"
    )
