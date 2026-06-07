#!/usr/bin/env python3
"""Iteratively probe `generate_ngspice_circuit` for every converter topology.

For each topology we start with a per-schema minimal spec and, when the
engine complains about a missing key, we add it from a curated dictionary
of "extra fields PyOM reads but the JSON schema doesn't list" until either
(a) we get a netlist back, or (b) we hit an unknown error.

Dumps `{topology: {"status": ..., "netlist"|"error": ...}}` to /tmp/all_decks.json.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyOpenMagnetics import PyOpenMagnetics as _ext  # type: ignore[import-not-found]

from heaviside.topologies.registry import CONVERTERS

# Universal optional fields shared across most topologies. Layered on top of
# what each schema declares "required".
COMMON_EXTRA: dict[str, object] = {
    "diodeVoltageDrop": 0.7,
    "currentRippleRatio": 0.4,
    "efficiency": 0.95,
    "maximumSwitchCurrent": 20.0,
    "desiredInductance": 22e-6,
    "desiredMagnetizingInductance": 1e-3,
    "desiredTurnsRatios": [2.0],
    "dutyCycle": 0.5,
    "phaseShift": 0.5,
    "minSwitchingFrequency": 100000.0,
    "maxSwitchingFrequency": 300000.0,
    "minimumSwitchingFrequency": 100000.0,
    "maximumSwitchingFrequency": 300000.0,
    "switchingFrequency": 200000.0,
    "ambientTemperature": 25.0,
    "powerFlow": "forward",
    "outputVoltage": 400.0,
    "outputPower": 100.0,
    "highVoltageBusVoltage": {"nominal": 400.0},
    "lowVoltageBusVoltage": {"nominal": 48.0},
    "lineToLineVoltage": {"nominal": 400.0},
    "outputDcVoltage": {"nominal": 800.0},
}

BASE_OP = {
    "switchingFrequency": 200000.0,
    "ambientTemperature": 25.0,
    "outputVoltages": [12.0],
    "outputCurrents": [5.0],
}


def _seed_spec(name: str) -> dict[str, object]:
    """Seed with everything most topologies want; per-topology overrides below."""
    spec: dict[str, object] = {
        "inputVoltage": {"nominal": 48.0},
        "diodeVoltageDrop": 0.7,
        "maximumSwitchCurrent": 20.0,
        "currentRippleRatio": 0.4,
        "efficiency": 0.95,
        "desiredInductance": 22e-6,
        "operatingPoints": [dict(BASE_OP)],
    }
    return spec


_KEY_RE = re.compile(r"key '([^']+)' not found")


def _add_missing_keys(spec: dict[str, object], error: str) -> bool:
    """Return True if we added something useful, False if stuck."""
    m = _KEY_RE.search(error)
    if not m:
        return False
    key = m.group(1)
    if key in spec:
        return False  # already there — different code path needs it
    if key not in COMMON_EXTRA:
        return False  # unknown key; surface to caller
    spec[key] = COMMON_EXTRA[key]
    return True


def _probe(entry) -> dict[str, object]:
    if entry.name in ("buck", "boost"):
        return {"status": "ALREADY_STENCILLED"}
    # Adjust seed for the few topologies whose schemas omit inputVoltage
    # (Vienna: lineToLineVoltage; PFC: scalar inputVoltage; CLLLC: bus voltages).
    spec = _seed_spec(entry.name)
    if entry.name == "vienna":
        del spec["inputVoltage"]
        spec["lineToLineVoltage"] = {"nominal": 400.0}
        spec["outputDcVoltage"] = {"nominal": 800.0}
        spec["switchingFrequency"] = 100000.0
    if entry.name == "clllc":
        del spec["inputVoltage"]
        spec["highVoltageBusVoltage"] = {"nominal": 400.0}
        spec["lowVoltageBusVoltage"] = {"nominal": 48.0}
        spec["powerFlow"] = "forward"
        spec["minSwitchingFrequency"] = 100000.0
        spec["maxSwitchingFrequency"] = 300000.0
    if entry.name == "power_factor_correction":
        spec["inputVoltage"] = 230.0
        spec["outputVoltage"] = 400.0
        spec["outputPower"] = 100.0
        spec["switchingFrequency"] = 100000.0
    if entry.family == "resonant":
        spec.setdefault("minSwitchingFrequency", 100000.0)
        spec.setdefault("maxSwitchingFrequency", 300000.0)

    # Two-output (transformer with sense winding) topologies.
    if entry.name in ("isolated_buck", "isolated_buck_boost"):
        spec["operatingPoints"][0]["outputVoltages"] = [12.0, 5.0]
        spec["operatingPoints"][0]["outputCurrents"] = [5.0, 1.0]

    # Flyback wants drain-source voltage cap or maximum duty cycle.
    if entry.name == "flyback":
        spec["maximumDrainSourceVoltage"] = 200.0
        spec["maximumDutyCycle"] = 0.5

    # Two-switch forward: keep duty cycle below 0.5. Drop output voltage.
    if entry.name == "two_switch_forward":
        spec["operatingPoints"][0]["outputVoltages"] = [5.0]
        spec["operatingPoints"][0]["outputCurrents"] = [2.0]

    # Push-pull: T1 must be ≤ period/2 — push duty cycle low by raising input.
    if entry.name == "push_pull":
        spec["inputVoltage"] = {"nominal": 200.0}
        spec["operatingPoints"][0]["outputVoltages"] = [12.0]

    # Bridges that demand explicit duty / phase fields.
    if entry.name == "asymmetric_half_bridge":
        spec["dutyCycle"] = 0.5
    if entry.name in ("phase_shifted_full_bridge", "phase_shifted_half_bridge"):
        spec["phaseShift"] = 0.5

    last_error = None
    for _ in range(20):
        try:
            raw = _ext.generate_ngspice_circuit(entry.name, spec, [2.0], 1e-3, 0, 0)
            result = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception as exc:
            return {"status": "EXCEPTION", "error": str(exc), "spec": spec}
        if "error" not in result or not result["error"]:
            return {
                "status": "OK",
                "netlist": result.get("netlist", ""),
                "spec": spec,
            }
        err = result["error"]
        last_error = err
        if not _add_missing_keys(spec, err):
            return {"status": "BLOCKED", "error": err, "spec": spec}
    return {"status": "LOOP_LIMIT", "error": last_error, "spec": spec}


def main() -> int:
    out = {}
    for entry in CONVERTERS:
        r = _probe(entry)
        out[entry.name] = r
        status = r["status"]
        extras = ""
        if status in ("BLOCKED", "EXCEPTION", "LOOP_LIMIT"):
            extras = " :: " + str(r.get("error", ""))[:120]
        print(f"{entry.name:35s} {status}{extras}")
    Path("/tmp/all_decks.json").write_text(json.dumps(out, indent=2))
    print("\nWrote /tmp/all_decks.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
