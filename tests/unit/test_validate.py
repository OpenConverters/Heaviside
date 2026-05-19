"""Tests for the Phase B TAS-conformance gate (``heaviside/validate.py``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from heaviside.validate import (
    Report,
    ValidatorError,
    Violation,
    _build_registry,
    validate_tas,
    validate_tas_file,
)

GOLDEN = Path(__file__).resolve().parents[1] / "regression" / "decomposer" / "golden"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_golden(name: str) -> dict:
    return json.loads((GOLDEN / name).read_text())


def _codes(report: Report) -> list[str]:
    return [v.code for v in report.violations]


# ---------------------------------------------------------------------------
# Registry smoke test
# ---------------------------------------------------------------------------


def test_registry_builds_with_tas_and_peas_roots() -> None:
    """The schema discovery walk must find both TAS and PEAS roots."""
    registry, tas_root, peas_root = _build_registry()
    assert tas_root["$id"] == "http://openconverters.com/schemas/TAS/TAS.json"
    assert peas_root["$id"] == "http://openconverters.com/schemas/PEAS/peas.json"
    # Registry must hold a reasonable number of schemas (TAS + PEAS + MAS
    # + CAS + SAS + RAS + COAS, all with $id). 50 is a safe floor.
    assert sum(1 for _ in registry) > 50


# ---------------------------------------------------------------------------
# TAS root layer (--tas-only)
# ---------------------------------------------------------------------------


def test_tas_only_on_decomposer_golden_buck_only_known_stencil_bugs() -> None:
    """Fresh decomposer output (pre-bridge) must satisfy the TAS root
    schema except for *known* stencil bugs that are tracked separately.

    Known violation (deferred to a stencil-fix phase): the buck stencil
    emits ``interStageCircuit`` connections with a single endpoint (e.g.
    ``Q1.D`` or ``Q1.G``), but the TAS schema requires ``minItems: 2``.
    Any *other* tas_root violation, or any schema_ref / unexpected code,
    must fail the test loudly — that signals a regression.

    Note: strict mode would fail on the placeholder URIs; this test
    exercises ``--tas-only`` which skips the per-component PEAS / URI
    shape layers.
    """
    tas = _load_golden("buck_48to12_5A.tas.json")
    report = validate_tas(tas, strict=False)

    unexpected = [
        v for v in report.violations
        if not (
            v.code == "tas_root"
            and "interStageCircuit" in v.path
            and "endpoints" in v.path
            and "too short" in v.message
        )
    ]
    if unexpected:
        pytest.fail(
            "tas-only validation surfaced unexpected violations (not the "
            "known single-endpoint interStageCircuit stencil bug): "
            + "; ".join(f"[{v.code}] {v.path}: {v.message}" for v in unexpected)
        )


def test_tas_root_rejects_missing_topology() -> None:
    """A TAS document with no ``topology`` key fails the TAS root schema."""
    bad = {"inputs": {"designRequirements": {}, "operatingPoints": []}}
    report = validate_tas(bad, strict=False)
    assert not report.ok
    assert "tas_root" in _codes(report)
    assert any("topology" in v.message for v in report.violations)


def test_tas_root_rejects_missing_inputs() -> None:
    """A TAS document with no ``inputs`` key fails the TAS root schema."""
    bad = {"topology": {"stages": [], "interStageCircuit": []}}
    report = validate_tas(bad, strict=False)
    assert not report.ok
    assert "tas_root" in _codes(report)


def test_tas_root_rejects_flat_legacy_shape() -> None:
    """The pre-migration flat shape ({stages, interStageCircuit} at root)
    must be rejected by the current TAS root schema. This regression
    guards against accidentally reintroducing the legacy emission.
    """
    legacy = {"stages": [], "interStageCircuit": []}
    report = validate_tas(legacy, strict=False)
    assert not report.ok
    assert any("inputs" in v.message or "topology" in v.message
               for v in report.violations)


# ---------------------------------------------------------------------------
# Strict layer: URI shape + placeholder detection
# ---------------------------------------------------------------------------


def _minimal_tas_with_data(data_value: object) -> dict:
    """Build a minimally-valid TAS document with one component carrying
    the given ``data`` payload. Used to exercise per-component layers in
    isolation from real PyOM artefacts.
    """
    return {
        "inputs": {
            "designRequirements": {
                "efficiency": 0.95,
                "inputType": "dc",
                "inputVoltage": {"nominal": 48.0},
                "outputs": [
                    {"name": "out0",
                     "voltage": {"nominal": 12.0},
                     "regulation": "voltage"},
                ],
            },
            "operatingPoints": [{
                "name": "op0",
                "inputVoltage": 48.0,
                "ambientTemperature": 25.0,
                "outputs": [{"name": "out0", "current": 5.0}],
            }],
        },
        "topology": {
            "stages": [{
                "name": "power_stage",
                "role": "switchingCell",
                "inputPort": {"type": "dcBus", "wire": "Vin"},
                "outputPorts": [{"type": "dcOutput", "wire": "Vout"}],
                "circuit": {
                    "components": [{"name": "Q1", "data": data_value}],
                    "connections": [],
                },
            }],
            "interStageCircuit": [],
        },
    }


def test_strict_flags_placeholder_uri_as_violation() -> None:
    """A stencil placeholder URI (``?placeholder=...``) must be reported
    in strict mode — this is the canonical pre-bridge sentinel that the
    gate is designed to catch."""
    tas = _minimal_tas_with_data("TAS/data/mosfets.ndjson?placeholder=Q1")
    report = validate_tas(tas, strict=True)
    assert "placeholder_uri" in _codes(report)


def test_strict_accepts_real_uri_shape() -> None:
    """A well-formed data URI passes the URI-shape check (the gate does
    not dereference; binding correctness is a separate downstream concern).
    """
    tas = _minimal_tas_with_data("TAS/data/mosfets.ndjson?mpn=EPC2019")
    report = validate_tas(tas, strict=True)
    # No URI-shape or placeholder violations.
    codes = _codes(report)
    assert "placeholder_uri" not in codes
    assert "uri_shape" not in codes


def test_strict_rejects_malformed_uri() -> None:
    """A URI string that doesn't match ``TAS/data/<file>.ndjson[?query]``
    fails the URI-shape check."""
    tas = _minimal_tas_with_data("https://example.com/some/other/place")
    report = validate_tas(tas, strict=True)
    assert "uri_shape" in _codes(report)


def test_strict_rejects_missing_data() -> None:
    """A component with no ``data`` field is rejected (TAS schema marks
    ``data`` as required, but we also report it explicitly per-component
    so the failure surfaces in component context."""
    tas = _minimal_tas_with_data(None)
    # Manually drop the data key (None is still present).
    tas["topology"]["stages"][0]["circuit"]["components"][0].pop("data")
    report = validate_tas(tas, strict=True)
    assert "missing_data" in _codes(report) or "tas_root" in _codes(report)


def test_tas_only_skips_uri_and_peas_layers() -> None:
    """``strict=False`` must not raise on placeholder URIs or inline PEAS
    documents that wouldn't validate in strict mode."""
    tas = _minimal_tas_with_data("TAS/data/mosfets.ndjson?placeholder=Q1")
    report = validate_tas(tas, strict=False)
    codes = _codes(report)
    assert "placeholder_uri" not in codes
    assert "peas_root" not in codes
    assert "schema_ref" not in codes
    assert "uri_shape" not in codes


# ---------------------------------------------------------------------------
# File loader + error handling
# ---------------------------------------------------------------------------


def test_validate_tas_file_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "buck.tas.json"
    p.write_text(json.dumps(_minimal_tas_with_data(
        "TAS/data/mosfets.ndjson?mpn=EPC2019"
    )))
    report = validate_tas_file(p, strict=False)
    assert report.ok, report.as_dict()


def test_validate_tas_file_raises_on_malformed_json(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("not json at all {")
    with pytest.raises(ValidatorError, match="not valid JSON"):
        validate_tas_file(p)


def test_validate_tas_raises_on_non_mapping_input() -> None:
    with pytest.raises(ValidatorError, match="must be a mapping"):
        validate_tas([1, 2, 3])  # type: ignore[arg-type]


def test_validate_tas_file_raises_on_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ValidatorError, match="cannot read"):
        validate_tas_file(tmp_path / "does_not_exist.json")


# ---------------------------------------------------------------------------
# Report serialisation
# ---------------------------------------------------------------------------


def test_report_as_dict_is_json_serialisable() -> None:
    """The JSON CLI output mode relies on ``Report.as_dict``."""
    tas = _minimal_tas_with_data("TAS/data/mosfets.ndjson?placeholder=Q1")
    report = validate_tas(tas, strict=True)
    payload = report.as_dict()
    # Must survive a JSON round-trip.
    json.loads(json.dumps(payload))
    assert payload["ok"] is False
    assert payload["strict"] is True
    assert payload["violation_count"] == len(report.violations)
    assert payload["violations"]
