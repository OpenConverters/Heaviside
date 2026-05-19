#!/usr/bin/env python3
"""Probe ``PyOpenMagnetics.get_extra_components_inputs`` for every converter.

For each of the 21 wired topologies (24 total minus the 3 magnetic-only),
this script:

1. Starts from a generic, rich converter spec covering all commonly-required
   fields (``inputVoltage``, ``operatingPoints``, ``currentRippleRatio``,
   ``desiredInductance``, ``desiredTurnsRatios``, etc.).
2. Calls ``get_extra_components_inputs(topology, spec, "IDEAL", None)``.
3. On a ``"key 'X' not found"`` error, appends a sensible default for
   field ``X`` and retries (max 8 rounds — loud failure beyond that).
4. Records the ordered list of extras with their
   ``designRequirements.name`` (the binding key).
5. Writes ``docs/extras-probe-report.md`` and an NDJSON map of
   ``{topology: [{kind, name, designRequirements}]}`` for the registry
   wiring step.

Per CLAUDE.md "no fallbacks" — any topology that can't be probed after
8 rounds is reported as ``PROBE_FAILED`` with the last error verbatim;
the script does not invent defaults beyond a tight whitelist.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from heaviside.topologies.registry import CONVERTERS, TopologyEntry

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "docs" / "extras-probe-report.md"
DATA_PATH = ROOT / "docs" / "extras-probe.json"

# Topologies known to be intentionally extras-free (per dispatch_extra_components
# in vendor/PyOpenMagnetics/src/converter.cpp:1056). Probing still verifies
# the engine answers cleanly with [].
EXPECTED_EMPTY: set[str] = {"vienna"}

# Best-effort default value for each field MKF may complain is missing.
# Loud failure for any field outside this whitelist.
FIELD_DEFAULTS: dict[str, Any] = {
    "currentRippleRatio": 0.4,
    "diodeVoltageDrop": 0.5,
    "maximumDutyCycle": 0.45,
    "dutyCycle": 0.3,
    "efficiency": 0.9,
    "maximumSwitchCurrent": 30.0,
    "desiredInductance": 1e-3,
    "desiredMagnetizingInductance": 1e-3,
    "desiredTurnsRatios": [4.0],
    "desiredResonantInductance": 50e-6,
    "desiredResonantCapacitance": 50e-9,
    "desiredOutputInductance": 1e-4,
    "maxSwitchingFrequency": 300000.0,
    "minSwitchingFrequency": 80000.0,
    "resonantFrequency": 150000.0,
    "deadTime": 100e-9,
    "phaseShift": 0.25,
    "qualityFactor": 0.4,
    "magnetizingInductanceRatio": 6.0,
    "leakageInductance": 5e-6,
    "couplingFactor": 0.97,
    "switchingFrequency": 200000.0,
    "ambientTemperature": 25.0,
}

# Per-topology spec overrides (applied on top of the base spec before
# any retry rounds). Use the empirically-confirmed shape from probing.
PER_TOPOLOGY_OVERRIDES: dict[str, dict[str, Any]] = {
    # Resonant: minimum/maximum switching frequency window.
    "llc": {
        "minSwitchingFrequency": 80000.0,
        "maxSwitchingFrequency": 300000.0,
    },
    "cllc": {
        "minSwitchingFrequency": 80000.0,
        "maxSwitchingFrequency": 300000.0,
    },
    "clllc": {
        "minSwitchingFrequency": 80000.0,
        "maxSwitchingFrequency": 300000.0,
    },
    "series_resonant": {
        "minSwitchingFrequency": 80000.0,
        "maxSwitchingFrequency": 300000.0,
    },
    # DAB: phase-shift modulation.
    "dual_active_bridge": {"phaseShift": 0.25},
    # PFC: line-frequency operation, no DC input bound.
    "power_factor_correction": {
        "inputVoltage": {"minimum": 85.0, "nominal": 230.0, "maximum": 265.0},
    },
}


@dataclass(slots=True)
class ProbeOutcome:
    entry: TopologyEntry
    status: str  # OK | PROBE_FAILED | UNBOUND | UNEXPECTED
    rounds: int
    fields_added: list[str] = field(default_factory=list)
    extras: list[dict[str, Any]] = field(default_factory=list)
    last_error: str | None = None
    variant: str | None = None


def _base_spec() -> dict[str, Any]:
    """Generic rich spec — most fields nullable / overridable."""
    return {
        "inputVoltage": {"minimum": 36.0, "nominal": 48.0, "maximum": 60.0},
        "currentRippleRatio": 0.4,
        "diodeVoltageDrop": 0.5,
        "maximumDutyCycle": 0.45,
        "efficiency": 0.9,
        "desiredInductance": 1e-3,
        "desiredTurnsRatios": [4.0],
        "operatingPoints": [
            {
                "outputVoltages": [12.0],
                "outputCurrents": [5.0],
                "switchingFrequency": 200000,
                "ambientTemperature": 25,
            }
        ],
    }


def _import_pyom() -> Any:
    from PyOpenMagnetics import PyOpenMagnetics as _ext  # type: ignore[import-not-found]
    return _ext


_MISSING_KEY_MARKERS = (
    "key '",      # nlohmann::json out_of_range.403
    "missing field",
)


def _parse_missing_field(err: str) -> str | None:
    """Extract the missing field name from an MKF error string."""
    if "key '" in err:
        head = err.split("key '", 1)[1]
        return head.split("'", 1)[0] if "'" in head else None
    if "missing field" in err:
        # belt-and-braces for alternate phrasings
        head = err.split("missing field", 1)[1].strip()
        if head.startswith("'") and "'" in head[1:]:
            return head[1 : head.index("'", 1)]
    return None


def _try_one(pyom: Any, variant: str, spec: dict[str, Any]) -> dict[str, Any] | list[Any]:
    return pyom.get_extra_components_inputs(variant, spec, "IDEAL", None)


def _probe_entry(entry: TopologyEntry, pyom: Any, max_rounds: int = 8) -> ProbeOutcome:
    spec = _base_spec()
    spec.update(PER_TOPOLOGY_OVERRIDES.get(entry.name, {}))
    fields_added: list[str] = []

    last_error: str | None = None
    used_variant: str | None = None

    for round_idx in range(max_rounds):
        # try each name variant; stop at the first that doesn't say "Unknown topology"
        for variant in entry.pyom_names:
            result = _try_one(pyom, variant, deepcopy(spec))
            if isinstance(result, dict) and "error" in result:
                err = result["error"]
                last_error = err
                if isinstance(err, str) and "Unknown topology" in err:
                    continue  # try next variant
                used_variant = variant
                # Maybe a missing-field error we can repair.
                missing = _parse_missing_field(err) if isinstance(err, str) else None
                if missing and missing in FIELD_DEFAULTS and missing not in fields_added:
                    spec[missing] = FIELD_DEFAULTS[missing]
                    fields_added.append(missing)
                    break  # restart the variant loop with the patched spec
                # Unrepairable error
                return ProbeOutcome(
                    entry,
                    "PROBE_FAILED",
                    round_idx + 1,
                    fields_added,
                    [],
                    err,
                    variant,
                )
            if isinstance(result, list):
                used_variant = variant
                extras = []
                for item in result:
                    if not isinstance(item, dict):
                        return ProbeOutcome(
                            entry,
                            "UNEXPECTED",
                            round_idx + 1,
                            fields_added,
                            [],
                            f"extras item is {type(item).__name__}",
                            variant,
                        )
                    dr = item.get("inputs", {}).get("designRequirements", {})
                    extras.append(
                        {
                            "kind": item.get("kind"),
                            "name": dr.get("name") if isinstance(dr, dict) else None,
                            "isolationSides": dr.get("isolationSides")
                            if isinstance(dr, dict)
                            else None,
                        }
                    )
                return ProbeOutcome(
                    entry,
                    "OK",
                    round_idx + 1,
                    fields_added,
                    extras,
                    None,
                    variant,
                )
            # unknown shape
            return ProbeOutcome(
                entry,
                "UNEXPECTED",
                round_idx + 1,
                fields_added,
                [],
                f"result is {type(result).__name__}",
                variant,
            )
        else:
            # exhausted all variants -> Unknown topology everywhere
            return ProbeOutcome(
                entry,
                "UNBOUND",
                round_idx + 1,
                fields_added,
                [],
                last_error,
                None,
            )

    return ProbeOutcome(
        entry,
        "PROBE_FAILED",
        max_rounds,
        fields_added,
        [],
        last_error or "max_rounds exceeded",
        used_variant,
    )


def _render_report(results: list[ProbeOutcome]) -> str:
    today = _dt.date.today().isoformat()
    n_ok = sum(1 for r in results if r.status == "OK")
    n_fail = sum(1 for r in results if r.status != "OK")

    lines: list[str] = [
        "# PyOpenMagnetics — Extras Components Probe",
        "",
        f"Generated: {today}",
        "",
        "## Summary",
        "",
        f"- Converters probed: **{len(results)}**",
        f"- OK: **{n_ok}**",
        f"- Failed / unbound: **{n_fail}**",
        "",
        "## Per-topology extras",
        "",
        "| Topology | Status | Variant | Rounds | Fields added | Extras |",
        "|----------|--------|---------|--------|--------------|--------|",
    ]
    for r in results:
        extras_str = (
            ", ".join(f"{e['kind']}:{e['name']}" for e in r.extras) if r.extras else "—"
        )
        if r.status != "OK" and r.last_error:
            extras_str = f"_{r.last_error[:80]}_"
        lines.append(
            f"| `{r.entry.name}` | **{r.status}** | `{r.variant or '—'}` | {r.rounds} | "
            f"{', '.join(r.fields_added) or '—'} | {extras_str} |"
        )
    lines.append("")
    if any(r.status != "OK" for r in results):
        lines += ["## Failures", ""]
        for r in results:
            if r.status != "OK":
                lines.append(
                    f"- `{r.entry.name}` ({r.status}): {r.last_error or 'no error message'}"
                )
        lines.append("")
    return "\n".join(lines)


def _render_json(results: list[ProbeOutcome]) -> str:
    payload = {
        r.entry.name: {
            "status": r.status,
            "variant": r.variant,
            "rounds": r.rounds,
            "fields_added": r.fields_added,
            "extras": r.extras,
            "last_error": r.last_error,
        }
        for r in results
    }
    return json.dumps(payload, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument(
        "--only",
        action="append",
        help="Probe only the given topology name(s). Repeatable.",
    )
    args = parser.parse_args()

    pyom = _import_pyom()
    targets = [e for e in CONVERTERS if not args.only or e.name in set(args.only)]
    print(f"Probing {len(targets)} topology(ies)...", file=sys.stderr)

    results: list[ProbeOutcome] = []
    for entry in targets:
        r = _probe_entry(entry, pyom)
        results.append(r)
        names = ", ".join(f"{e['kind']}:{e['name']}" for e in r.extras) or "—"
        msg = f"  [{r.status:>13}] {entry.name:30s} extras={names}"
        if r.status != "OK":
            msg += f"  err={r.last_error}"
        print(msg, file=sys.stderr)

    if not args.no_write:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(_render_report(results))
        DATA_PATH.write_text(_render_json(results))
        print(f"\nWrote {REPORT_PATH.relative_to(ROOT)} and {DATA_PATH.relative_to(ROOT)}")

    return 0 if all(r.status in ("OK",) or r.entry.name in EXPECTED_EMPTY for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
