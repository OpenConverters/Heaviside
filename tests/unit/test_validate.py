"""Tests for the Phase B TAS-conformance gate (``heaviside/validate.py``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from heaviside.validate import (
    Report,
    ValidatorError,
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
    assert tas_root["$id"] == "https://psma.com/tas/TAS.json"
    assert peas_root["$id"] == "https://psma.com/peas/peas.json"
    # Registry must hold a reasonable number of schemas (TAS + PEAS + MAS
    # + CAS + SAS + RAS + COAS, all with $id). 50 is a safe floor.
    assert sum(1 for _ in registry) > 50


# ---------------------------------------------------------------------------
# TAS root layer (--tas-only)
# ---------------------------------------------------------------------------


def test_tas_only_on_decomposer_golden_buck_is_clean() -> None:
    """Fresh decomposer output (pre-bridge) for a non-isolated buck
    must satisfy the TAS root schema with zero violations under
    ``--tas-only``.

    This is a regression guard for the Phase C stencil cleanup. Earlier
    iterations allowed a documented carve-out for single-endpoint
    interStage connections (gate/control singletons emitted by the
    stencils), but Phase C eliminated those entirely:

      * Step 2 dropped internal-signal singleton connections — the
        writer auto-synthesises gate nets from controller ``drives``
        declarations.
      * Step 3 introduced first-class ``terminal`` components so
        externalPort connections always have ≥2 endpoints.
      * Step 4 dropped the ``pins`` field from magnetic components in
        favour of pin derivation from observed connection endpoints.

    The remaining ``[tas_root] is too long`` violations on bridge
    topologies (half-bridge, full-bridge, push-pull, DAB) are tracked
    separately — they require a TAS schema unfreeze (multi-output role
    expansion) and are exercised by their own regression tests, not
    this gate.

    Note: strict mode would fail on the placeholder URIs; this test
    exercises ``--tas-only`` which skips the per-component PEAS / URI
    shape layers.
    """
    tas = _load_golden("buck_48to12_5A.tas.json")
    report = validate_tas(tas, strict=False)

    if report.violations:
        pytest.fail(
            "tas-only validation surfaced violations on a clean buck "
            "golden — Phase C cleanup is supposed to leave non-bridge "
            "topologies with zero schema-level issues: "
            + "; ".join(f"[{v.code}] {v.path}: {v.message}" for v in report.violations)
        )
    assert report.ok


def test_tas_root_rejects_missing_topology() -> None:
    """A TAS document with no ``topology`` key fails the TAS root schema."""
    bad = {"inputs": {"designRequirements": {}, "operatingPoints": []}}
    report = validate_tas(bad, strict=False)
    assert not report.ok
    assert "tas_root" in _codes(report)
    assert any("topology" in v.message for v in report.violations)


def test_tas_root_rejects_missing_inputs() -> None:
    """A TAS document with no ``inputs`` key fails the TAS root schema."""
    bad = {"topology": {"stages": [], "interStageConnections": []}}
    report = validate_tas(bad, strict=False)
    assert not report.ok
    assert "tas_root" in _codes(report)


def test_tas_root_rejects_flat_legacy_shape() -> None:
    """The pre-migration flat shape ({stages, interStageConnections} at root)
    must be rejected by the current TAS root schema. This regression
    guards against accidentally reintroducing the legacy emission.
    """
    legacy = {"stages": [], "interStageConnections": []}
    report = validate_tas(legacy, strict=False)
    assert not report.ok
    assert any("inputs" in v.message or "topology" in v.message for v in report.violations)


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
                    {"name": "out0", "voltage": {"nominal": 12.0}, "regulation": "voltage"},
                ],
            },
            "operatingPoints": [
                {
                    "name": "op0",
                    "inputVoltage": 48.0,
                    "ambientTemperature": 25.0,
                    "outputs": [{"name": "out0", "current": 5.0}],
                }
            ],
        },
        "topology": {
            "stages": [
                {
                    "name": "power_stage",
                    "role": "switchingCell",
                    "inputPort": {"port": "in", "type": "dcBus"},
                    "outputPort": {"port": "out", "type": "dcOutput"},
                    "circuit": {
                        "name": "power-cell",
                        "ports": [{"name": "in"}, {"name": "out"}],
                        "components": [{"name": "Q1", "data": data_value}],
                        "connections": [],
                    },
                }
            ],
            "interStageConnections": [],
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
    p.write_text(json.dumps(_minimal_tas_with_data("TAS/data/mosfets.ndjson?mpn=EPC2019")))
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


# ---------------------------------------------------------------------------
# Cross-schema $ref resolution (PEAS → MAS / CAS / SAS / RAS)
# ---------------------------------------------------------------------------


def _first_real_entry(ndjson_path: Path) -> dict:
    """Return the first JSON line of an NDJSON file as a dict.

    Used to pull a real MAS/CAS/SAS/RAS payload out of the TAS data
    directory so the PEAS validator gets exercised against actual
    librarian-provided artefacts rather than synthetic fixtures.
    """
    with ndjson_path.open() as f:
        line = f.readline()
    if not line:
        raise RuntimeError(f"NDJSON file is empty: {ndjson_path}")
    entry = json.loads(line)
    if not isinstance(entry, dict):
        raise RuntimeError(
            f"NDJSON {ndjson_path}: expected object at line 1, got {type(entry).__name__}"
        )
    return entry


def test_strict_resolves_peas_to_mas_ref_on_real_magnetic() -> None:
    """The PEAS magnetic branch ``$ref``s the MAS magnetic schema by URI
    (``https://psma.com/mas/magnetic.json``). This test inlines a real
    MAS document from ``TAS/data/magnetics.ndjson`` as a component's
    ``data`` payload and runs strict validation, verifying that:

      1. The cross-schema ``$ref`` from PEAS to MAS resolves cleanly
         through the registry (no ``schema_ref`` violations) — this is
         the regression guard for the host migration committed in
         249751f (openconverters.com / openmagnetics.com → psma.com,
         aligned with MAS's existing ``$id`` scheme).

      2. PEAS root validation actually runs on the inline MAS document
         (not silently skipped). Confirmed by observing the legitimate
         "missing 'inputs' property" violation: NDJSON entries are MAS-
         only and don't yet carry the per-component PEAS ``inputs``
         block — that's librarian work (Phase B carry-over). What
         matters here is the *shape* of the violation: ``peas_root``,
         not ``schema_ref``.

    Before the URI migration this test would have produced a
    ``schema_ref`` violation ("schema reference unresolvable") because
    PEAS's ``$ref: http://openmagnetics.com/schemas/magnetic.json`` had
    no matching ``$id`` in any loaded schema (MAS declared
    ``https://psma.com/mas/magnetic.json``). After the migration the
    URIs align and the lookup succeeds.
    """
    magnetics_path = Path(__file__).resolve().parents[2] / "TAS" / "data" / "magnetics.ndjson"
    if not magnetics_path.exists():
        pytest.skip(f"{magnetics_path} not present — test requires real NDJSON data")

    mag = _first_real_entry(magnetics_path)
    if "magnetic" not in mag:
        pytest.fail(
            f"first entry of {magnetics_path.name} is not wrapped as "
            f"{{'magnetic': ...}} — TAS data shape has regressed"
        )

    tas = _minimal_tas_with_data(mag)
    report = validate_tas(tas, strict=True)

    schema_ref = [v for v in report.violations if v.code == "schema_ref"]
    assert not schema_ref, (
        "PEAS→MAS $ref resolution failed — the URI migration regressed:\n"
        + "\n".join(f"  {v.path}: {v.message}" for v in schema_ref)
    )

    # Positive assertion: the PEAS layer ran. Otherwise this test would
    # be vacuous (an inline data payload that nobody validates would
    # also produce zero schema_ref violations).
    peas_root_violations = [v for v in report.violations if v.code == "peas_root"]
    assert peas_root_violations, (
        "PEAS root validation produced zero violations on an inline MAS "
        "document that is known to lack the per-component 'inputs' block "
        "— suggests the PEAS layer was silently skipped. Check that "
        "_validate_component_peas is wired into validate_tas for inline "
        "(non-string) data payloads."
    )
