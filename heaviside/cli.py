"""Heaviside CLI (entry point: `heaviside`)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer

from heaviside import __version__
from heaviside.topologies import CONVERTERS, MAGNETICS_ONLY, TOPOLOGIES, get

app = typer.Typer(
    name="heaviside",
    help="PyOpenMagnetics-first power electronics design system.",
    no_args_is_help=True,
    add_completion=False,
)


# Families whose MKF decks need ``bridge_simulation_mode="switch"`` so that
# real MOSFET refdeses appear in the netlist (otherwise the stencils refuse
# the deck because the bridge collapses to a single ``Vbridge`` source).
_BRIDGE_FAMILIES = frozenset(
    {"isolated_bridge", "isolated_push_pull", "resonant", "series_resonant"}
)


@app.command()
def version() -> None:
    """Print the Heaviside version."""
    typer.echo(__version__)


@app.command()
def topologies(family: str | None = None) -> None:
    """List supported topologies. Optionally filter by family."""
    entries = TOPOLOGIES if family is None else tuple(t for t in TOPOLOGIES if t.family == family)
    if not entries:
        typer.echo(f"No topologies match family={family!r}")
        raise typer.Exit(code=1)
    typer.echo(
        f"{len(entries)} topologies "
        f"({len(CONVERTERS)} converters + {len(MAGNETICS_ONLY)} magnetic-only):\n"
    )
    for t in entries:
        binding = t.per_topology_binding or "—"
        typer.echo(f"  {t.name:<28} {t.family:<22} {t.kind:<10} binding={binding}")


def _load_spec(path: Path) -> dict[str, Any]:
    if not path.is_file():
        typer.echo(f"error: spec file not found: {path}", err=True)
        raise typer.Exit(code=2)
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        typer.echo(f"error: spec file is not valid JSON: {exc}", err=True)
        raise typer.Exit(code=2) from None


def _parse_turns(raw: str | None, spec: dict[str, Any]) -> list[float]:
    """Resolve turns ratios from --turns flag or fall back to spec fields."""
    if raw is not None:
        try:
            return [float(t.strip()) for t in raw.split(",") if t.strip()]
        except ValueError as exc:
            typer.echo(f"error: --turns must be a comma-separated list of floats: {exc}", err=True)
            raise typer.Exit(code=2) from None
    # Common MAS field names — accept the first one we find.
    for key in ("desiredTurnsRatios", "turnsRatios"):
        if key in spec:
            value = spec[key]
            if isinstance(value, list) and all(isinstance(v, (int, float)) for v in value):
                return [float(v) for v in value]
    # Many non-isolated topologies legitimately have zero turns ratios.
    return []


def _parse_lm(raw: float | None, spec: dict[str, Any]) -> float:
    if raw is not None:
        return float(raw)
    for key in ("desiredInductance", "magnetizingInductance"):
        if key in spec and isinstance(spec[key], (int, float)):
            return float(spec[key])
    typer.echo(
        "error: magnetizing inductance not provided (--lm) and not found in spec "
        "(looked for 'desiredInductance', 'magnetizingInductance').",
        err=True,
    )
    raise typer.Exit(code=2)


def _resolve_bridge_mode(bridge_mode: str, topology: str) -> str:
    if bridge_mode != "auto":
        return bridge_mode
    entry = get(topology)
    return "switch" if entry.family in _BRIDGE_FAMILIES else ""


@app.command()
def design(
    topology: str = typer.Argument(..., help="Canonical topology name (e.g. 'buck', 'dab')."),
    spec: Path = typer.Option(..., "--spec", "-s", help="JSON file with MAS converter spec."),
    out: Path | None = typer.Option(None, "--out", "-o", help="Write populated TAS to FILE (default: stdout)."),
    turns: str | None = typer.Option(None, "--turns", help="Comma-separated turns ratios; overrides spec."),
    lm: float | None = typer.Option(None, "--lm", help="Magnetizing inductance in henries; overrides spec."),
    bridge_mode: str = typer.Option(
        "auto",
        "--bridge-mode",
        help="MKF bridge simulation mode: 'auto' (default), '', 'switch', or 'pulse'.",
    ),
    no_attach: bool = typer.Option(
        False,
        "--no-attach",
        help="Skip Phase B (component design + attachment). Emit decomposed TAS only.",
    ),
    realism: bool = typer.Option(
        False,
        "--realism",
        help=(
            "Run the realism gate on the populated TAS. Fail-closed: exits 6 "
            "if any check FAILS or every applicable check is UNAVAILABLE "
            "(INCOMPLETE). v0.1 typically returns INCOMPLETE until the "
            "librarian / sim agents enrich the pipeline."
        ),
    ),
    compact: bool = typer.Option(False, "--compact", help="Emit JSON without indentation."),
) -> None:
    """Run the end-to-end pipeline: spec → MKF deck → TAS → designed components → populated TAS.

    Examples:

        # Buck — non-isolated, no turns ratios:
        heaviside design buck --spec buck_48to12.json

        # DAB — bidirectional bridge (auto switch-mode):
        heaviside design dab --spec dab_800to500.json --turns 1.6 --out dab.tas.json

        # Just decompose; skip component design:
        heaviside design flyback --spec fly.json --no-attach
    """
    # Lazy imports so that ``heaviside version`` / ``heaviside topologies``
    # do not pay for PyOpenMagnetics' large native module load.
    from heaviside import bridge as _bridge
    from heaviside.decomposer import decompose_from_spec
    from heaviside.decomposer.api import DecomposerError
    from heaviside.bridge import BridgeError
    from heaviside.spec.validate_topology import (
        SpecValidationError,
        validate_spec_for_topology,
    )

    spec_json = _load_spec(spec)

    # Per-topology spec validation runs BEFORE any PyMKF call so users
    # see "missing maximumDutyCycle" up-front instead of mid-pipeline.
    try:
        validate_spec_for_topology(topology, spec_json)
    except SpecValidationError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from None

    turns_ratios = _parse_turns(turns, spec_json)
    magnetizing_inductance = _parse_lm(lm, spec_json)
    mode = _resolve_bridge_mode(bridge_mode, topology)

    try:
        _, tas = decompose_from_spec(
            topology,
            spec_json,
            turns_ratios=turns_ratios,
            magnetizing_inductance=magnetizing_inductance,
            bridge_simulation_mode=mode,
        )
    except DecomposerError as exc:
        typer.echo(f"error: decompose failed: {exc}", err=True)
        raise typer.Exit(code=3) from None

    if not no_attach:
        try:
            components = _bridge.design_converter_components(
                topology, spec_json, max_results=1, use_ngspice=False,
            )
            _bridge.attach_components_to_tas(tas, components, topology=topology)
        except BridgeError as exc:
            typer.echo(f"error: bridge attach failed: {exc}", err=True)
            raise typer.Exit(code=4) from None
        except Exception as exc:  # noqa: BLE001 — surface PyOM errors verbatim
            typer.echo(
                f"error: component design failed ({type(exc).__name__}): {exc}",
                err=True,
            )
            raise typer.Exit(code=5) from None

    payload = json.dumps(tas, indent=None if compact else 2)
    if out is None:
        typer.echo(payload)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + ("" if compact else "\n"))
        typer.echo(f"wrote {out}", err=True)

    if realism:
        from heaviside.pipeline import (
            EnrichmentError,
            RealismVerdict,
            enrich_tas_for_realism,
            evaluate_tas,
        )

        try:
            tas_for_gate = enrich_tas_for_realism(tas, topology=topology, spec=spec_json)
        except EnrichmentError as exc:
            typer.echo(f"error: realism enrichment failed: {exc}", err=True)
            raise typer.Exit(code=6) from None

        report = evaluate_tas(tas_for_gate, topology=topology, spec=spec_json)
        summary = report.summary
        typer.echo(
            f"realism: verdict={report.verdict.value} "
            f"pass={summary['pass']} fail={summary['fail']} "
            f"unavailable={summary['unavailable']} "
            f"not_applicable={summary['not_applicable']}",
            err=True,
        )
        for c in report.checks:
            if c.status.value == "pass":
                typer.echo(
                    f"  [pass] {c.name}: value={c.value} margin={c.margin}",
                    err=True,
                )
            elif c.status.value in ("fail", "unavailable"):
                typer.echo(f"  [{c.status.value}] {c.name}: {c.detail or ''}".rstrip(": "), err=True)
        if report.verdict is not RealismVerdict.PASS:
            raise typer.Exit(code=6)


@app.command()
def validate(
    tas_file: Path = typer.Argument(
        ...,
        help="Path to a TAS JSON file. Use '-' to read from stdin.",
    ),
    tas_only: bool = typer.Option(
        False,
        "--tas-only",
        help=(
            "Skip per-component PEAS + URI-shape checks. Useful while "
            "stencils still emit placeholder URIs."
        ),
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit the report as JSON instead of human-readable text.",
    ),
) -> None:
    """Validate a TAS document against the TAS + PEAS schema stack.

    Exit codes:
      0 — document conforms
      1 — one or more violations
      2 — tooling error (cannot read input, malformed JSON, schema load fail)
    """
    from heaviside.validate import ValidatorError, validate_tas, validate_tas_file

    try:
        if str(tas_file) == "-":
            try:
                doc = json.loads(sys.stdin.read())
            except json.JSONDecodeError as exc:
                typer.echo(f"error: stdin is not valid JSON: {exc}", err=True)
                raise typer.Exit(code=2) from None
            report = validate_tas(doc, strict=not tas_only)
        else:
            report = validate_tas_file(tas_file, strict=not tas_only)
    except ValidatorError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from None

    if as_json:
        typer.echo(json.dumps(report.as_dict(), indent=2))
    else:
        if report.ok:
            mode = "tas-only" if tas_only else "strict"
            typer.echo(f"OK ({mode}): {tas_file}")
        else:
            typer.echo(
                f"FAIL: {len(report.violations)} violation(s) "
                f"({'tas-only' if tas_only else 'strict'} mode)",
                err=True,
            )
            for v in report.violations:
                typer.echo(f"  [{v.code}] {v.path}: {v.message}", err=True)

    raise typer.Exit(code=0 if report.ok else 1)


if __name__ == "__main__":
    app()
