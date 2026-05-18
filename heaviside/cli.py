"""Heaviside CLI (entry point: `heaviside`).

Minimal v0.1 surface — subcommands grow with each phase.
"""

from __future__ import annotations

import typer

from heaviside import __version__

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
def topologies() -> None:
    """List supported MKF topologies (populated in Phase 1)."""
    typer.echo("Phase 1 will enumerate the 24 MKF topologies here.")


if __name__ == "__main__":
    app()
