"""Heaviside CLI (entry point: `heaviside`)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer

from heaviside import __version__
from heaviside.topologies import CONVERTERS, MAGNETICS_ONLY, TOPOLOGIES

app = typer.Typer(
    name="heaviside",
    help="PyOpenMagnetics-first power electronics design system.",
    no_args_is_help=True,
    add_completion=False,
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


@app.command()
def design(
    topology: str = typer.Argument(..., help="Canonical topology name (e.g. 'buck', 'dab')."),
    spec: Path = typer.Option(..., "--spec", "-s", help="JSON file with the Heaviside converter spec."),
    out: Path | None = typer.Option(
        None, "--out", "-o", help="Write the TAS to FILE (default: stdout)."
    ),
    no_attach: bool = typer.Option(
        False,
        "--no-attach",
        help="Emit Kirchhoff's BARE TAS (every component a design requirement; no BOM / magnetic fill).",
    ),
    realism: bool = typer.Option(
        False,
        "--realism",
        help="Report the realism-gate verdict (the full realize path runs it); exits 6 if it FAILs.",
    ),
    compact: bool = typer.Option(False, "--compact", help="Emit JSON without indentation."),
) -> None:
    """Design a converter through Kirchhoff (della-Pollock cutover, abt #48).

    Kirchhoff designs the topology and emits the TAS \u2014 every component as a design REQUIREMENT
    (seed). HS fills the BOM (real Q/D/C from the internal DB) and designs each magnetic GEOMETRY via
    PyOM/MKF from Kirchhoff's per-component seed; the realism gate reads the populated TAS. The MKF
    converter models (process_converter / design_magnetics_from_converter / get_extra_components_inputs)
    are retired \u2014 the converter math + the full component list are Kirchhoff's; MKF is geometry-only.

    Examples:

        # Buck \u2014 full realize (BOM + magnetics + gate):
        heaviside design buck --spec buck_48to12.json

        # Just Kirchhoff's bare TAS (component requirements, no fill):
        heaviside design flyback --spec fly.json --no-attach
    """
    from heaviside.spec.validate_topology import (
        SpecValidationError,
        validate_spec_for_topology,
    )

    spec_json = _load_spec(spec)

    # Per-topology spec validation runs up-front so users see "missing maximumDutyCycle" before any
    # Kirchhoff / PyOM call.
    try:
        validate_spec_for_topology(topology, spec_json)
    except SpecValidationError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from None

    verdict: str | None = None
    if no_attach:
        # Kirchhoff's bare TAS: every component is a SEED (family slot + designRequirements). No BOM
        # selection, no magnetic geometry \u2014 the "decompose only" mode.
        from heaviside.decomposer import kirchhoff_adapter as _ka

        try:
            tas = _ka.design_from_hs_spec(topology, spec_json)
        except Exception as exc:  # noqa: BLE001 - surface any Kirchhoff design failure with its type
            typer.echo(f"error: Kirchhoff design failed ({type(exc).__name__}): {exc}", err=True)
            raise typer.Exit(code=3) from None
    else:
        # Full della-Pollock realize: Kirchhoff designs it, HS fills parts + designs the magnetics
        # (PyOM/MKF) from Kirchhoff's seeds, the realism gate reads it. One KH TAS, end to end.
        from heaviside.pipeline.converter_designer import design_converter

        try:
            outcome = design_converter(topology, spec_json, use_llm=False, with_reviewers=False)
        except Exception as exc:  # noqa: BLE001 - surface any realize failure with its type
            typer.echo(f"error: design failed ({type(exc).__name__}): {exc}", err=True)
            raise typer.Exit(code=5) from None
        tas = outcome.tas
        verdict = outcome.verdict

    payload = json.dumps(tas, indent=None if compact else 2)
    if out is None:
        typer.echo(payload)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + ("" if compact else "\n"))
        typer.echo(f"wrote {out}", err=True)

    if realism and verdict is not None:
        typer.echo(f"realism verdict: {verdict}", err=True)
        if str(verdict).lower() == "fail":
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
    from heaviside.stages.topology_id import feasible as feasible_topology_names

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
    from heaviside.pipeline.re_pipeline import run_re_pipeline

    outcome = run_re_pipeline(reference, pdf_path=pdf, verbose=verbose)

    typer.echo(f"RE: {'PASSED' if outcome.passed else 'FAILED'}", err=True)
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
