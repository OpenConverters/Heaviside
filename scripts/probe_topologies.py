#!/usr/bin/env python3
"""Empirically probe ``PyOpenMagnetics.process_converter`` for every registry entry.

For each topology we try each of its ``pyom_names`` variants with a
realistic, non-empty converter spec. We classify the outcome as:

* ``BOUND_OK``           — engine returned a result with no error key.
* ``BOUND_NEEDS_INPUT``  — engine error is anything other than "Unknown topology"
                           (means the binding works, our probe inputs are incomplete).
* ``UNBOUND``            — every variant returned "Exception: Unknown topology: ...".

Output:

* Writes a Markdown report to ``docs/probe-report.md``.
* Exits 0 if every converter is at least ``BOUND_NEEDS_INPUT``.
* Exits 1 if any converter is ``UNBOUND`` **and** ``--strict`` is set.

The unbound list drives upstream work in ``vendor/PyOpenMagnetics/``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from heaviside.topologies.registry import TOPOLOGIES, TopologyEntry

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "docs" / "probe-report.md"


Status = Literal["BOUND_OK", "BOUND_NEEDS_INPUT", "UNBOUND", "MAGNETIC_SKIPPED"]


@dataclass(slots=True)
class ProbeResult:
    entry: TopologyEntry
    status: Status
    chosen_variant: str | None
    last_error: str | None


# A reasonably-shaped converter input. Most topologies will reject for
# missing fields, which is fine — we only care whether they're recognised.
PROBE_INPUT: dict[str, Any] = {
    "inputVoltage": 48.0,
    "currentRippleRatio": 0.4,
    "efficiency": 0.95,
    "diodeVoltageDrop": 0.7,
    "dutyCycle": 0.25,
    "maximumSwitchCurrent": 20.0,
    "maxSwitchingFrequency": 250000.0,
    "operatingPoints": [
        {
            "switchingFrequency": 200000.0,
            "ambientTemperature": 25.0,
            "outputVoltages": [{"nominal": 12.0}],
            "outputCurrents": [{"nominal": 5.0}],
        }
    ],
}


def _import_pyom() -> Any:
    from PyOpenMagnetics import PyOpenMagnetics as _ext  # type: ignore[import-not-found]

    return _ext


def _probe_one(entry: TopologyEntry, pyom: Any) -> ProbeResult:
    if entry.kind == "magnetic":
        # Magnetic-only components are not exposed through process_converter().
        # They are reached via PyOpenMagnetics.calculate_advised_magnetics()
        # in Phase 3+. We record them here for completeness.
        return ProbeResult(entry, "MAGNETIC_SKIPPED", None, None)

    last_error: str | None = None
    for variant in entry.pyom_names:
        raw = pyom.process_converter(variant, PROBE_INPUT, False)
        result = json.loads(raw) if isinstance(raw, str) else dict(raw)
        err = result.get("error")
        if isinstance(err, str) and err.startswith("Exception: Unknown topology"):
            last_error = err
            continue
        if err:
            return ProbeResult(entry, "BOUND_NEEDS_INPUT", variant, err)
        return ProbeResult(entry, "BOUND_OK", variant, None)
    return ProbeResult(entry, "UNBOUND", None, last_error)


def _render_report(results: list[ProbeResult]) -> str:
    today = _dt.date.today().isoformat()
    by_status: dict[Status, list[ProbeResult]] = {}
    for r in results:
        by_status.setdefault(r.status, []).append(r)

    n_converters = sum(1 for r in results if r.entry.kind == "converter")
    n_bound = sum(1 for r in results if r.status in ("BOUND_OK", "BOUND_NEEDS_INPUT"))
    n_unbound = sum(1 for r in results if r.status == "UNBOUND")

    lines: list[str] = [
        "# PyOpenMagnetics Topology Probe Report",
        "",
        f"Generated: {today}",
        "",
        "## Summary",
        "",
        f"- Converters in registry: **{n_converters}**",
        f"- Bound in PyOpenMagnetics: **{n_bound}**",
        f"- Unbound (upstream work needed): **{n_unbound}**",
        f"- Magnetic-only (skipped, designed via different API): **"
        f"{sum(1 for r in results if r.status == 'MAGNETIC_SKIPPED')}**",
        "",
        "## Per-topology results",
        "",
        "| Topology | Family | Status | Variant accepted | First error |",
        "|----------|--------|--------|------------------|-------------|",
    ]
    for r in results:
        variant = r.chosen_variant or "—"
        err = (r.last_error or "").replace("|", "\\|")[:80]
        lines.append(
            f"| `{r.entry.name}` | {r.entry.family} | **{r.status}** | `{variant}` | {err} |"
        )

    if by_status.get("UNBOUND"):
        lines += [
            "",
            "## Action: bindings to add in `vendor/PyOpenMagnetics/`",
            "",
        ]
        for r in by_status["UNBOUND"]:
            lines.append(
                f"- `{r.entry.name}` — tried {list(r.entry.pyom_names)}; "
                f"engine response: `{r.last_error}`"
            )

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any converter is UNBOUND.",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Print report to stdout instead of writing docs/probe-report.md.",
    )
    args = parser.parse_args()

    pyom = _import_pyom()
    results = [_probe_one(e, pyom) for e in TOPOLOGIES]

    report = _render_report(results)
    if args.no_write:
        print(report)
    else:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(report)
        print(f"Wrote {REPORT_PATH.relative_to(ROOT)}")

    unbound_converters = [
        r for r in results if r.status == "UNBOUND" and r.entry.kind == "converter"
    ]
    if args.strict and unbound_converters:
        print(
            f"\nSTRICT FAIL: {len(unbound_converters)} converter topology binding(s) missing:",
            file=sys.stderr,
        )
        for r in unbound_converters:
            print(f"  - {r.entry.name}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
