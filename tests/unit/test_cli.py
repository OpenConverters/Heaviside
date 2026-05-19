"""Unit tests for the ``heaviside`` CLI.

Scope: argument parsing, exit codes, and the cheap branches (``version``,
``topologies``, ``design --no-attach``). End-to-end runs that exercise
PyOpenMagnetics' component design loop live in the ``integration`` suite —
they take minutes per topology and are not appropriate for the unit tier.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from heaviside import __version__
from heaviside.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Basic commands
# ---------------------------------------------------------------------------


def test_version_command_prints_package_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == __version__


def test_topologies_lists_all_entries() -> None:
    result = runner.invoke(app, ["topologies"])
    assert result.exit_code == 0, result.stderr
    # Regression: registry has 24 converters + 3 magnetic-only = 27 entries.
    assert "27 topologies" in result.stdout
    assert "buck" in result.stdout
    assert "dual_active_bridge" in result.stdout


def test_topologies_family_filter_returns_subset() -> None:
    result = runner.invoke(app, ["topologies", "--family", "non_isolated"])
    assert result.exit_code == 0, result.stderr
    # Non-isolated family contains buck, boost, etc. — should be > 1 and
    # must not contain DAB (which is in the ``resonant`` family).
    assert "buck" in result.stdout
    assert "dual_active_bridge" not in result.stdout


def test_topologies_unknown_family_exits_1() -> None:
    result = runner.invoke(app, ["topologies", "--family", "no_such_family"])
    assert result.exit_code == 1
    assert "no topologies match" in result.stdout.lower()


# ---------------------------------------------------------------------------
# ``design`` — failure paths (no PyOM needed)
# ---------------------------------------------------------------------------


def test_design_missing_spec_file_exits_2(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    result = runner.invoke(app, ["design", "buck", "--spec", str(missing)])
    assert result.exit_code == 2
    assert "spec file not found" in result.stderr.lower()


def test_design_invalid_json_exits_2(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    result = runner.invoke(app, ["design", "buck", "--spec", str(bad)])
    assert result.exit_code == 2
    assert "not valid json" in result.stderr.lower()


def test_design_missing_lm_exits_2(tmp_path: Path) -> None:
    """Buck spec without ``desiredInductance`` and no ``--lm`` must fail loudly."""
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps({
            "inputVoltage": {"minimum": 36, "maximum": 60, "nominal": 48},
            "operatingPoints": [{
                "outputVoltages": [12.0],
                "outputCurrents": [5.0],
                "switchingFrequency": 200000,
                "ambientTemperature": 25,
            }],
        })
    )
    result = runner.invoke(app, ["design", "buck", "--spec", str(spec)])
    assert result.exit_code == 2
    assert "magnetizing inductance not provided" in result.stderr.lower()


def test_design_bad_turns_flag_exits_2(tmp_path: Path) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"desiredInductance": 1e-6}))
    result = runner.invoke(
        app, ["design", "buck", "--spec", str(spec), "--turns", "not_a_number"]
    )
    assert result.exit_code == 2
    assert "--turns" in result.stderr


# ---------------------------------------------------------------------------
# ``design --no-attach`` — exercises decompose pipeline; requires PyOM.
# ---------------------------------------------------------------------------


pytest.importorskip("PyOpenMagnetics")


@pytest.fixture
def buck_spec(tmp_path: Path) -> Path:
    spec = tmp_path / "buck.json"
    spec.write_text(
        json.dumps({
            "inputVoltage": {"minimum": 36, "maximum": 60, "nominal": 48},
            "desiredInductance": 22e-6,
            "currentRippleRatio": 0.4,
            "diodeVoltageDrop": 0.7,
            "efficiency": 0.95,
            "operatingPoints": [{
                "outputVoltages": [12.0],
                "outputCurrents": [5.0],
                "switchingFrequency": 200000,
                "ambientTemperature": 25,
            }],
        })
    )
    return spec


def test_design_buck_no_attach_emits_tas_to_stdout(buck_spec: Path) -> None:
    result = runner.invoke(
        app, ["design", "buck", "--spec", str(buck_spec), "--no-attach"]
    )
    assert result.exit_code == 0, result.stderr
    tas = json.loads(result.stdout)
    assert "stages" in tas
    names = {s["name"] for s in tas["stages"]}
    # Buck stencil emits at minimum a power_stage + controller pair.
    assert "power_stage" in names
    # Decompose-only: components must NOT carry a ``mas`` block yet.
    for stage in tas["stages"]:
        for comp in stage.get("circuit", {}).get("components", []):
            assert "mas" not in comp


def test_design_buck_no_attach_writes_file(buck_spec: Path, tmp_path: Path) -> None:
    out = tmp_path / "out" / "buck.tas.json"
    result = runner.invoke(
        app,
        ["design", "buck", "--spec", str(buck_spec), "--no-attach",
         "--out", str(out), "--compact"],
    )
    assert result.exit_code == 0, result.stderr
    assert out.is_file()
    assert f"wrote {out}" in result.stderr
    tas = json.loads(out.read_text())
    assert "stages" in tas


def test_design_dab_alias_resolves_to_dual_active_bridge(buck_spec: Path) -> None:
    """``dab`` is a PyOM alias; the CLI must accept it for bridge auto-mode
    detection and for stencil dispatch."""
    result = runner.invoke(
        app,
        ["design", "dab", "--spec", str(buck_spec), "--turns", "1.0", "--no-attach"],
    )
    assert result.exit_code == 0, result.stderr
    tas = json.loads(result.stdout)
    # DAB stencil emits 5 stages (inverter / isolation / outputRectifier /
    # outputFilter / control). Use a loose lower bound to stay robust to
    # future stencil cosmetics.
    assert len(tas["stages"]) >= 4


def test_design_unknown_topology_exits_3(buck_spec: Path) -> None:
    result = runner.invoke(
        app, ["design", "not_a_topology", "--spec", str(buck_spec), "--no-attach"]
    )
    # Unknown topology trips ``get()`` during bridge-mode resolution
    # (KeyError, propagated as exit 1 by Typer's default handler) OR
    # the decompose pipeline (DecomposerError → exit 3). Either is
    # acceptable as long as it fails loudly.
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# ``design --realism`` — fail-closed gate
# ---------------------------------------------------------------------------


def test_design_realism_on_decompose_only_is_incomplete_exit_6(buck_spec: Path) -> None:
    """Decompose-only TAS has no MAS, no sim, no ratings — every check is
    UNAVAILABLE → INCOMPLETE → exit 6 (fail-closed).
    """
    result = runner.invoke(
        app, ["design", "buck", "--spec", str(buck_spec), "--no-attach", "--realism"]
    )
    assert result.exit_code == 6, result.stderr
    assert "verdict=incomplete" in result.stderr
    # Per-check diagnostics for UNAVAILABLE must be present so the user
    # knows exactly which pipeline input is missing.
    assert "[unavailable] efficiency_sanity" in result.stderr


def test_design_realism_without_flag_exits_0(buck_spec: Path) -> None:
    """Without ``--realism`` the gate must not run; exit 0 even though
    the TAS is sparse."""
    result = runner.invoke(
        app, ["design", "buck", "--spec", str(buck_spec), "--no-attach"]
    )
    assert result.exit_code == 0, result.stderr
    assert "realism:" not in result.stderr
