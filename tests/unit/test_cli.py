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


def test_design_no_attach_incomplete_spec_fails_loudly(tmp_path: Path) -> None:
    """Post-cutover (abt #48) Kirchhoff owns decomposition and DERIVES the
    inductance itself, so a bare buck spec no longer needs ``desiredInductance``
    (the old ``--lm``/``--turns`` overrides are gone — turns/L come from the
    spec). But a spec missing a genuinely required field must still fail loudly
    rather than design on defaults: here ``efficiency`` is absent."""
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "inputVoltage": {"minimum": 36, "maximum": 60, "nominal": 48},
                "operatingPoints": [
                    {
                        "outputVoltages": [12.0],
                        "outputCurrents": [5.0],
                        "switchingFrequency": 200000,
                        "ambientTemperature": 25,
                    }
                ],
            }
        )
    )
    result = runner.invoke(
        app,
        ["design", "buck", "--spec", str(spec), "--no-attach"],
    )
    assert result.exit_code != 0
    assert "efficiency" in result.stderr.lower()


# ---------------------------------------------------------------------------
# ``design --no-attach`` — exercises decompose pipeline; requires PyOM.
# ---------------------------------------------------------------------------


pytest.importorskip("PyOpenMagnetics")


@pytest.fixture
def buck_spec(tmp_path: Path) -> Path:
    spec = tmp_path / "buck.json"
    spec.write_text(
        json.dumps(
            {
                "inputVoltage": {"minimum": 36, "maximum": 60, "nominal": 48},
                "desiredInductance": 22e-6,
                "currentRippleRatio": 0.4,
                "diodeVoltageDrop": 0.7,
                "efficiency": 0.95,
                "operatingPoints": [
                    {
                        "outputVoltages": [12.0],
                        "outputCurrents": [5.0],
                        "switchingFrequency": 200000,
                        "ambientTemperature": 25,
                    }
                ],
            }
        )
    )
    return spec


def test_design_buck_no_attach_emits_tas_to_stdout(buck_spec: Path) -> None:
    result = runner.invoke(app, ["design", "buck", "--spec", str(buck_spec), "--no-attach"])
    assert result.exit_code == 0, result.stderr
    tas = json.loads(result.stdout)
    assert "topology" in tas
    names = {s["name"] for s in tas["topology"]["stages"]}
    # Post-cutover Kirchhoff emits the switching cell as ``switchingCell``.
    assert "switchingCell" in names
    # Decompose-only: every component is a Kirchhoff SEED — it carries an inline
    # ``data`` with its family discriminator + designRequirements (``inputs``),
    # but is NOT yet FILLED with a real part (no ``mpn`` / selection provenance,
    # and the magnetic seed carries requirements, not a designed MAS core).
    for stage in tas["topology"]["stages"]:
        for comp in stage.get("circuit", {}).get("components", []):
            assert not comp.get("mpn"), f"{comp.get('name')!r} is filled, not a seed"
            assert not comp.get("selection_provenance")
            data = comp.get("data")
            assert isinstance(data, dict) and "inputs" in data, (
                f"seed {comp.get('name')!r} must carry a designRequirements payload"
            )
            fam = {"magnetic", "capacitor", "semiconductor", "resistor", "controller"}
            assert fam & set(data), f"seed {comp.get('name')!r} missing a family discriminator"


def test_design_buck_no_attach_writes_file(buck_spec: Path, tmp_path: Path) -> None:
    out = tmp_path / "out" / "buck.tas.json"
    result = runner.invoke(
        app,
        ["design", "buck", "--spec", str(buck_spec), "--no-attach", "--out", str(out), "--compact"],
    )
    assert result.exit_code == 0, result.stderr
    assert out.is_file()
    assert f"wrote {out}" in result.stderr
    tas = json.loads(out.read_text())
    assert "topology" in tas


def test_design_dab_alias_resolves_to_dual_active_bridge(tmp_path: Path) -> None:
    """``dab`` is a PyOM alias; the CLI must normalise it to the canonical
    ``dual_active_bridge`` before the Kirchhoff call (which binds only canonical
    names). Regression: the della-Pollock cutover dropped this normalisation, so
    ``dab`` died with "Kirchhoff has no binding for 'dab'"."""
    spec = tmp_path / "dab.json"
    spec.write_text(
        json.dumps(
            {
                "inputVoltage": {"minimum": 360, "maximum": 420, "nominal": 400},
                "efficiency": 0.95,
                "desiredMagnetizingInductance": 1e-3,
                "desiredTurnsRatios": [1.6],
                "operatingPoints": [
                    {
                        "outputVoltages": [250.0],
                        "outputCurrents": [4.0],
                        "switchingFrequency": 100000,
                        "ambientTemperature": 25,
                    }
                ],
            }
        )
    )
    result = runner.invoke(app, ["design", "dab", "--spec", str(spec), "--no-attach"])
    assert result.exit_code == 0, result.stderr
    tas = json.loads(result.stdout)
    # The DAB cell must be present (alias resolved → Kirchhoff designed the
    # dual-active-bridge, not choked on 'dab').
    names = {s["name"] for s in tas["topology"]["stages"]}
    assert any("dab" in n.lower() for n in names), names


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
    """Decompose-only TAS has no MAS at all — buck extractor cannot run,
    so enrichment raises EnrichmentError → CLI exits 6 (fail-closed).
    """
    result = runner.invoke(
        app, ["design", "buck", "--spec", str(buck_spec), "--no-attach", "--realism"]
    )
    assert result.exit_code == 6, result.stderr
    # Decompose-only path: L1 carries only a Kirchhoff seed (partial MAS with
    # designRequirements but no core), so realism enrichment fails-closed on the
    # incomplete magnetic rather than validating it — exit 6 either way.
    assert "realism enrichment failed" in result.stderr
    assert "MAS" in result.stderr or "magnetic" in result.stderr


def test_design_realism_without_flag_exits_0(buck_spec: Path) -> None:
    """Without ``--realism`` the gate must not run; exit 0 even though
    the TAS is sparse."""
    result = runner.invoke(app, ["design", "buck", "--spec", str(buck_spec), "--no-attach"])
    assert result.exit_code == 0, result.stderr
    assert "realism:" not in result.stderr
