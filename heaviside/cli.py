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
    out: Path | None = typer.Option(
        None, "--out", "-o", help="Write populated TAS to FILE (default: stdout)."
    ),
    turns: str | None = typer.Option(
        None, "--turns", help="Comma-separated turns ratios; overrides spec."
    ),
    lm: float | None = typer.Option(
        None, "--lm", help="Magnetizing inductance in henries; overrides spec."
    ),
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
    from heaviside.bridge import BridgeError
    from heaviside.decomposer import decompose_from_spec
    from heaviside.decomposer.api import DecomposerError
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
    mode = _resolve_bridge_mode(bridge_mode, topology)

    # L is computed by MKF in design_converter_components — it owns the
    # physics derivation (V·s, ripple ratio, duty). Heaviside should not
    # second-guess it. Order of operations:
    #   1. Design the magnetic first (no_attach path skips this).
    #   2. Harvest L from the picked main magnetic.
    #   3. Inject L into the spec dict so every downstream consumer
    #      (decompose, extract.py, stress.py, sim.runner) reads the
    #      same value via the existing ``desiredInductance`` /
    #      ``desiredMagnetizingInductance`` keys.
    #   4. Decompose with the harvested L.
    #
    # --lm CLI flag is an escape hatch: forces a specific L (e.g. for
    # research / reproducibility) and short-circuits the harvest.
    # --no-attach must still supply L via spec or --lm because there
    # is no design step to harvest from.
    components = None
    if no_attach:
        magnetizing_inductance = _parse_lm(lm, spec_json)
    else:
        try:
            components = _bridge.design_converter_components(
                topology,
                spec_json,
                max_results=1,
                use_ngspice=False,
            )
        except BridgeError as exc:
            typer.echo(f"error: bridge design failed: {exc}", err=True)
            raise typer.Exit(code=4) from None
        except Exception as exc:
            typer.echo(
                f"error: component design failed ({type(exc).__name__}): {exc}",
                err=True,
            )
            raise typer.Exit(code=5) from None

        # Save transformer Lm before clobbering — ACF needs it below.
        orig_lm = spec_json.get("desiredMagnetizingInductance")
        if lm is not None:
            magnetizing_inductance = float(lm)
        else:
            magnetizing_inductance = components.L_authoritative
            spec_json["desiredInductance"] = magnetizing_inductance
            spec_json["desiredMagnetizingInductance"] = magnetizing_inductance

    # For ACF the main magnetic is the output choke; the deck's Lpri
    # must be the transformer Lm from the original spec.
    lm_for_deck = magnetizing_inductance
    if (
        topology == "active_clamp_forward"
        and components is not None
        and isinstance(orig_lm, (int, float))
        and orig_lm > 0
        and orig_lm > 5 * magnetizing_inductance
    ):
        lm_for_deck = float(orig_lm)

    try:
        _, tas = decompose_from_spec(
            topology,
            spec_json,
            turns_ratios=turns_ratios,
            magnetizing_inductance=lm_for_deck,
            bridge_simulation_mode=mode,
        )
    except DecomposerError as exc:
        typer.echo(f"error: decompose failed: {exc}", err=True)
        raise typer.Exit(code=3) from None

    if not no_attach:
        try:
            _bridge.attach_components_to_tas(tas, components, topology=topology)
            # Pick real Q/D/C MPNs from the local TAS DB. Skipped silently
            # for topologies with no stress deriver registered yet; the
            # realism gate's voltage-derating checks will stay UNAVAILABLE
            # for those topologies, which is the honest failure mode.
            from heaviside.catalogue import (
                SelectionError,
                assemble_bom_from_tas,
            )
            from heaviside.pipeline.stress import StressDerivationError

            try:
                assemble_bom_from_tas(tas, topology=topology, spec=spec_json)
            except SelectionError as exc:
                typer.echo(
                    f"warn: BOM selection failed for {topology!r} — "
                    f"realism gate will FAIL on the affected components. "
                    f"Detail: {exc}",
                    err=True,
                )
            except StressDerivationError as exc:
                typer.echo(
                    f"warn: BOM skipped for {topology!r} — "
                    f"spec missing fields for stress derivation. "
                    f"Detail: {exc}",
                    err=True,
                )
        except BridgeError as exc:
            typer.echo(f"error: bridge attach failed: {exc}", err=True)
            raise typer.Exit(code=4) from None
        except Exception as exc:
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
        from heaviside.sim import (
            SimError,
            simulate_closed_loop,
            simulate_steady_state,
            stamp_simulation_results,
        )

        try:
            tas_for_gate = enrich_tas_for_realism(tas, topology=topology, spec=spec_json)
        except EnrichmentError as exc:
            typer.echo(f"error: realism enrichment failed: {exc}", err=True)
            raise typer.Exit(code=6) from None

        # Sim: try closed-loop first (iterative duty search until vout
        # matches spec target). Falls back to open-loop steady-state if
        # the deck has no PWM source (resonant/bridge topologies) or
        # the duty search doesn't converge. SimError is non-fatal — the
        # realism gate keeps sim-dependent checks UNAVAILABLE if both
        # paths fail.
        try:
            from heaviside.sim.parasitics import inject_parasitics

            netlist, _ = decompose_from_spec(
                topology,
                spec_json,
                turns_ratios=turns_ratios,
                magnetizing_inductance=lm_for_deck,
                bridge_simulation_mode=mode,
            )
            netlist = inject_parasitics(netlist, tas)
            sim_result = None
            is_closed_loop = False
            ops = spec_json.get("operatingPoints") or [{}]
            first_op = ops[0] if isinstance(ops[0], dict) else {}
            vouts = first_op.get("outputVoltages")
            vout_target = (
                float(vouts[0])
                if isinstance(vouts, list) and vouts and isinstance(vouts[0], (int, float))
                else None
            )
            if vout_target is not None:
                try:
                    sim_result = simulate_closed_loop(
                        netlist,
                        vout_target=vout_target,
                    )
                    is_closed_loop = True
                except SimError as exc:
                    typer.echo(
                        f"closed-loop sim fell back to open-loop: {exc}",
                        err=True,
                    )
            if sim_result is None:
                sim_result = simulate_steady_state(netlist)
            stamp_simulation_results(tas_for_gate, sim_result)
            if is_closed_loop:
                # Tells the realism gate's output_voltage_regulation
                # check to actually evaluate (instead of falling through
                # to NOT_APPLICABLE on the controller-present heuristic).
                tas_for_gate["simulation_results"]["op0"]["is_closed_loop"] = True
        except (SimError, DecomposerError) as exc:
            typer.echo(f"sim runner skipped: {exc}", err=True)

        # Analyst stage: per-component loss attribution + junction
        # temperatures. No-op for topologies without a registered
        # analyst (realism gate keeps no_negative_losses + thermal_limit
        # UNAVAILABLE for those, which is the honest failure mode).
        from heaviside.pipeline.analyst import AnalystError, run_analyst

        try:
            run_analyst(topology, tas_for_gate, spec_json)
        except AnalystError as exc:
            typer.echo(f"analyst skipped: {exc}", err=True)

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
                typer.echo(
                    f"  [{c.status.value}] {c.name}: {c.detail or ''}".rstrip(": "), err=True
                )
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


@app.command()
def auto_design(
    spec: Path = typer.Argument(..., help="Converter spec JSON file."),
    n_candidates: int = typer.Option(
        3, "--candidates", "-n", help="Fast-Pareto candidates per topology."
    ),
    pick_criteria: str = typer.Option("lowest_losses", "--criteria", help="Pareto pick criteria."),
    out: Path | None = typer.Option(None, "--out", help="Write best outcome TAS to this path."),
    report_path: Path | None = typer.Option(
        None, "--report", help="Write HTML report for best outcome."
    ),
) -> None:
    """Full auto-design: topology selection → magnetic pick → simulate → realism gate.

    Runs the complete della Pollock pipeline unattended. Returns the
    best-passing topology or reports all verdicts if none pass.
    """
    from heaviside.pipeline.full_design import (
        FullDesignError,
        full_design,
    )
    from heaviside.pipeline.topology_screen import feasible_topology_names

    spec_json = _load_spec(spec)

    def selector_fn(s):
        return (feasible_topology_names(s), "static screen (no LLM)")

    try:
        stage1, stage2, outcomes = full_design(
            spec_json,
            n_candidates_per_topology=n_candidates,
            pick_criteria=pick_criteria,
            parallel=False,
            selector_fn=selector_fn,
        )
    except FullDesignError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(
        f"stage1: {len(stage1.reconciliation.chosen)} topologies "
        f"({', '.join(stage1.reconciliation.chosen)})",
        err=True,
    )
    typer.echo(
        f"stage2: {len(stage2.picks)} magnetic picks, {len(stage2.failures)} failures",
        err=True,
    )

    best = None
    for o in outcomes:
        v = o.verdict_dict
        verdict = v["verdict"] if v else "no_verdict"
        summary = v.get("summary", {}) if v else {}
        p = summary.get("pass", 0)
        f_ = summary.get("fail", 0)
        gk = "APPROVED" if o.gatekeeper and o.gatekeeper.approved else "BLOCKED"
        topo = o.pick.topology.name
        diag = "; ".join(o.diagnostics) if o.diagnostics else ""
        typer.echo(
            f"  {topo:30s} {verdict:10s} {gk:8s} pass={p} fail={f_}  {diag}",
            err=True,
        )
        if o.gatekeeper and o.gatekeeper.warnings:
            for w in o.gatekeeper.warnings[:3]:
                typer.echo(f"    ⚠ {w}", err=True)
        if best is None and verdict == "pass" and gk == "APPROVED":
            best = o

    if best:
        typer.echo(f"\nbest: {best.pick.topology.name}", err=True)
        if best.report:
            typer.echo(best.report)
        if out and best.tas:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(best.tas, indent=2) + "\n")
            typer.echo(f"wrote {out}", err=True)
        if report_path:
            from heaviside.report import render_html

            html = render_html(best)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(html)
            typer.echo(f"wrote HTML report: {report_path}", err=True)
    else:
        typer.echo("\nno topology passed the realism gate + gatekeeper", err=True)
        raise typer.Exit(code=6)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address."),
    port: int = typer.Option(8000, help="Bind port."),
    reload: bool = typer.Option(False, help="Auto-reload on code changes."),
    mcp: bool = typer.Option(False, "--mcp", help="Start MCP server (stdio) instead of REST API."),
) -> None:
    """Start Heaviside as a server (REST API or MCP)."""
    if mcp:
        import asyncio

        from heaviside.mcp_server import main as mcp_main

        asyncio.run(mcp_main())
    else:
        import uvicorn

        uvicorn.run(
            "heaviside.api:app",
            host=host,
            port=port,
            reload=reload,
        )


@app.command()
def lessons(
    topology: str | None = typer.Option(None, "--topology", "-t", help="Filter by topology."),
    category: str | None = typer.Option(None, "--category", "-c", help="Filter by category."),
    severity: str | None = typer.Option(None, "--severity", "-s", help="Filter by severity."),
    max_age: int | None = typer.Option(None, "--max-age", help="Max age in days."),
    suggestions_only: bool = typer.Option(
        False, "--suggestions", help="Show only lessons with suggestions."
    ),
) -> None:
    """Query the teacher's lesson store."""
    from heaviside.pipeline.teacher import load_lessons, summarize_lessons

    all_lessons = load_lessons(
        topology=topology,
        category=category,
        severity=severity,
        max_age_days=max_age,
    )
    if suggestions_only:
        all_lessons = [l for l in all_lessons if l.suggestion]

    if not all_lessons:
        typer.echo("no lessons found")
        return

    typer.echo(summarize_lessons(all_lessons))
    typer.echo("")
    for l in all_lessons:
        sev_marker = {"error": "!!", "warning": "!", "info": "."}
        marker = sev_marker.get(l.severity, "?")
        typer.echo(f"[{marker}] {l.topology:25s} {l.category:22s} {l.detail[:80]}")
        if l.suggestion:
            typer.echo(f"    -> {l.suggestion}")


@app.command()
def reverse_engineer(
    reference: str = typer.Argument(..., help="Reference design name (e.g. 'TI TIDA-050072')."),
    pdf: Path | None = typer.Option(None, "--pdf", help="Path to reference design PDF."),
    out: Path | None = typer.Option(None, "--out", help="Write outcome JSON to this path."),
    report_path: Path | None = typer.Option(None, "--report", help="Write HTML report."),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Reverse-engineer a reference design: PDF → BOM → design → review."""
    from heaviside.pipeline.cre_pipeline import run_cre_pipeline

    outcome = run_cre_pipeline(reference, pdf_path=pdf, verbose=verbose)

    typer.echo(f"CRE: {'PASSED' if outcome.passed else 'FAILED'}", err=True)
    if outcome.ref_spec:
        s = outcome.ref_spec
        typer.echo(
            f"  {s.topology}: {s.vin_min}-{s.vin_max}V → {s.vout}V/{s.iout}A ({s.pout}W)",
            err=True,
        )
    typer.echo(f"  BOM: {len(outcome.ref_bom)} components", err=True)
    if outcome.diagnostics:
        for d in outcome.diagnostics:
            typer.echo(f"  diag: {d}", err=True)

    if out:
        import json as _json

        payload = {
            "reference": outcome.reference,
            "passed": outcome.passed,
            "ref_spec": outcome.ref_spec.__dict__ if outcome.ref_spec else None,
            "ref_bom": list(outcome.ref_bom),
            "review_verdicts": list(outcome.review_verdicts),
            "diagnostics": list(outcome.diagnostics),
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(payload, indent=2) + "\n")
        typer.echo(f"wrote {out}", err=True)

    if not outcome.passed:
        raise typer.Exit(code=1)


@app.command()
def crossref(
    bom_file: Path = typer.Argument(..., help="Source BOM JSON file."),
    manufacturer: str = typer.Option(..., "--mfr", help="Target manufacturer."),
    context: str | None = typer.Option(None, "--context", help="Circuit context description."),
    out: Path | None = typer.Option(None, "--out", help="Write outcome JSON to this path."),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Cross-reference a BOM to a target manufacturer."""
    import json as _json

    from heaviside.pipeline.crossref_pipeline import run_crossref_pipeline

    bom_data = _json.loads(bom_file.read_text())
    if not isinstance(bom_data, list):
        bom_data = bom_data.get("bom", bom_data.get("components", []))

    outcome = run_crossref_pipeline(
        bom_data,
        manufacturer,
        circuit_context=context,
        verbose=verbose,
    )

    typer.echo(f"Crossref: {'PASSED' if outcome.passed else 'FAILED'}", err=True)
    typer.echo(f"  Target: {outcome.target_manufacturer}", err=True)
    n_exact = sum(1 for c in outcome.components if c.status == "exact")
    n_rec = sum(1 for c in outcome.components if c.status == "recommended")
    n_part = sum(1 for c in outcome.components if c.status == "partial")
    n_none = sum(1 for c in outcome.components if c.status == "no_substitute")
    n_keep = sum(1 for c in outcome.components if c.status == "keep_original")
    typer.echo(
        f"  exact={n_exact} recommended={n_rec} partial={n_part} "
        f"no_substitute={n_none} keep_original={n_keep}",
        err=True,
    )
    if outcome.guardrail_log:
        typer.echo(f"  guardrail fires: {len(outcome.guardrail_log)}", err=True)

    for c in outcome.components:
        status_mark = {
            "exact": "+",
            "recommended": "~",
            "partial": "?",
            "no_substitute": "X",
            "keep_original": "=",
        }.get(c.status.value, "?")
        sub = c.substitute_mpn or "-"
        typer.echo(f"  [{status_mark}] {c.ref_des:8s} {c.original_mpn:20s} → {sub}")

    if out:
        payload = {
            "target_manufacturer": outcome.target_manufacturer,
            "passed": outcome.passed,
            "components": [
                {
                    "ref_des": c.ref_des,
                    "component_type": c.component_type,
                    "original_mpn": c.original_mpn,
                    "substitute_mpn": c.substitute_mpn,
                    "status": c.status.value,
                    "notes": c.notes,
                }
                for c in outcome.components
            ],
            "guardrail_fires": list(outcome.guardrail_log),
            "diagnostics": list(outcome.diagnostics),
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(payload, indent=2) + "\n")
        typer.echo(f"wrote {out}", err=True)

    if not outcome.passed:
        raise typer.Exit(code=1)


librarian_app = typer.Typer(help="TAS component librarian — fetch, validate, audit.")
app.add_typer(librarian_app, name="librarian")


@librarian_app.command()
def search(
    mpn: str = typer.Argument(..., help="Manufacturer part number to look up."),
    category: str = typer.Option(
        None,
        "--category",
        "-c",
        help="TAS category (mosfets, diodes, capacitors, resistors, magnetics, igbts). "
        "Auto-detected from distributor data if omitted.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Append to TAS/data/<category>.ndjson after validation.",
    ),
    distributor: str = typer.Option(
        "digikey",
        "--distributor",
        "-d",
        help="Distributor API to query (digikey or mouser).",
    ),
) -> None:
    """Fetch a component by MPN from a distributor API, convert to TAS, and optionally append.

    Exit codes:
      0 — fetched (and appended if --apply)
      1 — MPN not found or conversion failed
      2 — credentials missing or API error
      3 — validation failed (component data doesn't pass schema)
      4 — duplicate (component already in TAS)
    """
    from heaviside.librarian.fetcher.base import DistributorError
    from heaviside.librarian.tas import LibrarianError

    try:
        if distributor == "digikey":
            from heaviside.librarian.fetcher.convert import (
                convert_digikey_to_tas_capacitor,
                convert_digikey_to_tas_diode,
                convert_digikey_to_tas_igbt,
                convert_digikey_to_tas_mosfet,
                convert_digikey_to_tas_resistor,
            )
            from heaviside.librarian.fetcher.digikey import DigiKeyClient

            with DigiKeyClient() as client:
                product = client.get_product(mpn)
            converters = {
                "mosfets": convert_digikey_to_tas_mosfet,
                "diodes": convert_digikey_to_tas_diode,
                "igbts": convert_digikey_to_tas_igbt,
                "capacitors": convert_digikey_to_tas_capacitor,
                "resistors": convert_digikey_to_tas_resistor,
            }
        elif distributor == "mouser":
            from heaviside.librarian.fetcher.convert import (
                convert_mouser_to_tas_capacitor,
                convert_mouser_to_tas_diode,
                convert_mouser_to_tas_igbt,
                convert_mouser_to_tas_mosfet,
                convert_mouser_to_tas_resistor,
            )
            from heaviside.librarian.fetcher.mouser import MouserClient

            with MouserClient() as client:
                product = client.get_product(mpn)
            converters = {
                "mosfets": convert_mouser_to_tas_mosfet,
                "diodes": convert_mouser_to_tas_diode,
                "igbts": convert_mouser_to_tas_igbt,
                "capacitors": convert_mouser_to_tas_capacitor,
                "resistors": convert_mouser_to_tas_resistor,
            }
        else:
            typer.echo(f"error: unknown distributor {distributor!r}", err=True)
            raise typer.Exit(code=2)
    except DistributorError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from None

    if category is None:
        from heaviside.librarian.fetcher.convert import detect_category

        category = detect_category(product, distributor)
        if category is None:
            typer.echo(
                f"error: could not auto-detect category for {mpn!r}. Use --category to specify.",
                err=True,
            )
            raise typer.Exit(code=1)
    typer.echo(f"category: {category}", err=True)

    converter = converters.get(category)
    if converter is None:
        typer.echo(f"error: no converter for category {category!r}", err=True)
        raise typer.Exit(code=1)

    try:
        component = converter(product)
    except Exception as exc:
        typer.echo(f"error: conversion failed: {exc}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(json.dumps(component, indent=2))

    if apply:
        try:
            from heaviside.librarian.tas import add_component, component_exists

            if component_exists(category, component):
                typer.echo(f"duplicate: {mpn} already in TAS/{category}.ndjson", err=True)
                raise typer.Exit(code=4)
            add_component(category, component)
            typer.echo(f"appended to TAS/data/{category}.ndjson", err=True)
        except LibrarianError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=3) from None


@librarian_app.command()
def audit(
    category: str = typer.Argument(
        None,
        help="Category to audit (mosfets, diodes, capacitors, resistors, magnetics, igbts). "
        "Omit to audit all.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit the report as JSON.",
    ),
    integrity: bool = typer.Option(
        False,
        "--integrity",
        help="Run the offline integrity scan instead of the field audit: "
        "rows that would fail the insert guard (synthetic series, "
        "placeholder MPNs, junk datasheet URLs, telemetry shapes), "
        "exact-duplicate payloads, same-MPN groups over --max-mpn-copies, "
        "and datasheet URLs pointing at a different known manufacturer.",
    ),
    max_mpn_copies: int = typer.Option(
        1,
        "--max-mpn-copies",
        help="(--integrity) Flag MPNs with more rows than this.",
    ),
    check_schema: bool = typer.Option(
        False,
        "--check-schema",
        help="(--integrity) Also run full JSON-schema validation per row "
        "(slow on the 100k+-row files).",
    ),
) -> None:
    """Audit TAS/data for pipeline-critical fields.

    Reports missing fields per component type (Isat, DCR, ESR, Coss,
    Vth, Qg, Qrr, etc.) without modifying any data. The output shows
    pass rate and top failure reasons per category.

    With --integrity, runs the read-only database-integrity scan
    (heaviside.librarian.guards.integrity_scan) instead.

    Exit codes:
      0 — all categories at 100% (or integrity-clean)
      1 — at least one category has failures / integrity findings
      2 — tooling error
    """
    from heaviside.librarian.auditor import (
        CategoryAudit,
        audit_all,
        audit_category,
    )

    if integrity:
        _run_integrity_audit(
            category,
            as_json=as_json,
            max_mpn_copies=max_mpn_copies,
            check_schema=check_schema,
        )
        return

    categories_to_run = [category] if category else None
    results: dict[str, CategoryAudit] = {}
    total_pass = 0
    total_count = 0
    any_fail = False

    try:
        if categories_to_run:
            for cat in categories_to_run:
                results[cat] = audit_category(cat, on_corruption="report")
        else:
            results = audit_all(on_corruption="report")
    except Exception as exc:
        typer.echo(f"error: audit failed: {exc}", err=True)
        raise typer.Exit(code=2) from None

    for _cat, r in results.items():
        total_pass += r.passed
        total_count += r.total
        if r.failures:
            any_fail = True

    if as_json:
        report: dict[str, object] = {}
        for cat, r in results.items():
            top_misses = sorted(r.critical_field_misses.items(), key=lambda x: -x[1])[:5]
            report[cat] = {
                "total": r.total,
                "passed": r.passed,
                "failed": len(r.failures),
                "pass_rate": round(r.passed / r.total, 4) if r.total else 0,
                "top_critical_misses": top_misses,
            }
        report["overall"] = {
            "total": total_count,
            "passed": total_pass,
            "failed": total_count - total_pass,
            "pass_rate": round(total_pass / total_count, 4) if total_count else 0,
        }
        typer.echo(json.dumps(report, indent=2))
    else:
        for cat, r in results.items():
            pct = round(100 * r.passed / r.total, 1) if r.total else 0
            n_fail = len(r.failures)
            status = "PASS" if n_fail == 0 else "FAIL"
            typer.echo(f"  {cat:20s} {status:4s}  {r.passed:>6d}/{r.total:<6d}  ({pct}%)")
            if r.critical_field_misses:
                top = sorted(r.critical_field_misses.items(), key=lambda x: -x[1])[:3]
                fields = ", ".join(f"{f}({n})" for f, n in top)
                typer.echo(f"  {'':20s}       missing: {fields}")
        if total_count:
            pct = round(100 * total_pass / total_count, 1)
            typer.echo(f"  {'overall':20s}       {total_pass:>6d}/{total_count:<6d}  ({pct}%)")

    raise typer.Exit(code=0 if not any_fail else 1)


def _run_integrity_audit(
    category: str | None,
    *,
    as_json: bool,
    max_mpn_copies: int,
    check_schema: bool,
) -> None:
    """Run the read-only integrity scan and render it (see `audit --integrity`)."""
    from heaviside.librarian.guards import IntegrityReport, integrity_scan
    from heaviside.librarian.safe_access import CATEGORIES, TAS_DATA_DIR

    if category:
        categories = [category]
    else:
        # Every whitelisted category with a live NDJSON, except the
        # quarantine bin itself (it is *expected* to hold junk).
        categories = sorted(
            cat
            for cat in CATEGORIES
            if cat != "quarantine" and (TAS_DATA_DIR / f"{cat}.ndjson").exists()
        )

    reports: dict[str, IntegrityReport] = {}
    try:
        for cat in categories:
            reports[cat] = integrity_scan(
                cat,
                max_mpn_copies=max_mpn_copies,
                check_schema=check_schema,
            )
    except Exception as exc:
        typer.echo(f"error: integrity scan failed: {exc}", err=True)
        raise typer.Exit(code=2) from None

    any_findings = any(not r.clean for r in reports.values())

    if as_json:
        payload: dict[str, object] = {}
        for cat, r in reports.items():
            payload[cat] = {
                "total": r.total,
                "guard_failures": [
                    {"line": f.line, "mpn": f.mpn, "reasons": f.reasons}
                    for f in r.guard_failures[:50]
                ],
                "guard_failure_count": len(r.guard_failures),
                "exact_duplicate_groups": len(r.exact_duplicates),
                "exact_duplicate_rows": sum(len(v) for v in r.exact_duplicates.values()),
                "mpn_over_limit": dict(
                    sorted(r.mpn_over_limit.items(), key=lambda x: -x[1])[:50]
                ),
                "mpn_over_limit_count": len(r.mpn_over_limit),
                "domain_mismatches": [
                    {"line": f.line, "mpn": f.mpn, "reasons": f.reasons}
                    for f in r.domain_mismatches[:50]
                ],
                "domain_mismatch_count": len(r.domain_mismatches),
            }
        typer.echo(json.dumps(payload, indent=2))
    else:
        for cat, r in reports.items():
            status = "CLEAN" if r.clean else "FINDINGS"
            typer.echo(f"  {cat:20s} {status:8s}  rows={r.total}")
            if r.guard_failures:
                typer.echo(f"    guard failures: {len(r.guard_failures)}")
                for f in r.guard_failures[:5]:
                    typer.echo(f"      L{f.line} {f.mpn}: {f.reasons[0]}")
            if r.exact_duplicates:
                dup_rows = sum(len(v) for v in r.exact_duplicates.values())
                typer.echo(
                    f"    exact-duplicate payloads: {len(r.exact_duplicates)} "
                    f"groups / {dup_rows} rows"
                )
            if r.mpn_over_limit:
                top = sorted(r.mpn_over_limit.items(), key=lambda x: -x[1])[:5]
                shown = ", ".join(f"{m}({n})" for m, n in top)
                typer.echo(
                    f"    MPNs with >{max_mpn_copies} copies: "
                    f"{len(r.mpn_over_limit)}  (top: {shown})"
                )
            if r.domain_mismatches:
                typer.echo(f"    manufacturer/domain mismatches: {len(r.domain_mismatches)}")
                for f in r.domain_mismatches[:5]:
                    typer.echo(f"      L{f.line} {f.mpn}: {f.reasons[0]}")

    raise typer.Exit(code=0 if not any_findings else 1)


if __name__ == "__main__":
    app()
