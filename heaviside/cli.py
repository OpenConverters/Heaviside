"""Heaviside CLI (entry point: `heaviside`)."""

from __future__ import annotations

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


if __name__ == "__main__":
    app()
