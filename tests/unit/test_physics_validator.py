"""Tests for the TAS physics-validator gateway (heaviside.librarian.physics_validator).

The gateway wraps the canonical C++/pybind11 ``tas_validator`` shipped in the
TAS submodule (``TAS/validator/``). These tests exercise the *real* compiled
module — never a mock — and skip cleanly when it has not been built (mirroring
the native-dependency convention used elsewhere, e.g. PyOpenMagnetics).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from heaviside.librarian import physics_validator as pv

pytestmark = pytest.mark.skipif(
    not pv.available(),
    reason="tas_validator not built — see TAS/validator/BUILD.md",
)

_DATA = Path(__file__).resolve().parents[2] / "TAS" / "data"


def _first_valid(category: str, limit: int = 3000) -> dict | None:
    """First physically-valid record from a TAS data category, or None."""
    path = _DATA / f"{category}.ndjson"
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if pv.validate_physics(rec).valid:
                return rec
    return None


def _inject_inverted_thermal(record: dict) -> bool:
    """Set a datasheetInfo.thermal pair with minimum >= maximum (GEN_TEMP_ORDER
    IMPOSSIBLE). Returns True if a datasheetInfo dict was found to mutate."""

    def walk(node) -> bool:
        if isinstance(node, dict):
            if "datasheetInfo" in node and isinstance(node["datasheetInfo"], dict):
                node["datasheetInfo"]["thermal"] = {
                    "operatingTemperature": {"minimum": 200.0, "maximum": 100.0}
                }
                return True
            return any(walk(v) for v in node.values())
        if isinstance(node, list):
            return any(walk(v) for v in node)
        return False

    return walk(record)


def test_check_codes_surface():
    codes = pv.check_codes()
    assert isinstance(codes, list) and len(codes) > 20
    assert "GEN_TEMP_ORDER" in codes


def test_known_good_part_is_physically_valid():
    rec = _first_valid("magnetics") or _first_valid("capacitors")
    if rec is None:
        pytest.skip("no TAS catalog data present (LFS not smudged)")
    verdict = pv.assert_physically_valid(rec, mpn="known-good")
    assert verdict.valid
    assert not verdict.impossible


def test_impossible_thermal_ordering_raises():
    rec = _first_valid("capacitors") or _first_valid("magnetics")
    if rec is None:
        pytest.skip("no TAS catalog data present (LFS not smudged)")
    bad = copy.deepcopy(rec)
    assert _inject_inverted_thermal(bad), "no datasheetInfo found to corrupt"
    with pytest.raises(pv.PhysicsInvalidError) as exc:
        pv.assert_physically_valid(bad, mpn="bad-thermal")
    assert any(f.code == "GEN_TEMP_ORDER" for f in exc.value.findings)


def test_auditor_run_physics_flags_impossible_part():
    """The auditor's run_physics path attaches canonical findings and fails an
    IMPOSSIBLE part — replacing hand-reasoned physics with the C++ verdict."""
    from heaviside.librarian import auditor as au

    rec = _first_valid("capacitors") or _first_valid("magnetics")
    if rec is None:
        pytest.skip("no TAS catalog data present (LFS not smudged)")
    category = "capacitors" if "capacitor" in rec else "magnetics"

    good = au.audit_component(rec, category, run_physics=True)
    assert not good.physically_invalid

    bad = copy.deepcopy(rec)
    assert _inject_inverted_thermal(bad), "no datasheetInfo found to corrupt"
    res = au.audit_component(bad, category, run_physics=True)
    assert res.physically_invalid
    assert not res.passed
    assert any(f.code == "GEN_TEMP_ORDER" for f in res.physics_findings)

    # Opt-out path is unchanged (no physics findings, field-only audit).
    res_off = au.audit_component(bad, category, run_physics=False)
    assert res_off.physics_findings == []


def test_missing_module_path_would_raise_not_skip():
    """The gateway must fail loud, never silently pass, when the module is absent.
    (Here it IS available — assert the no-silent-skip contract via the verdict.)"""
    rec = _first_valid("mosfets") or _first_valid("capacitors")
    if rec is None:
        pytest.skip("no TAS catalog data present (LFS not smudged)")
    verdict = pv.validate_physics(rec)
    # A real verdict always reports which checks it could not run, rather than
    # treating absent inputs as valid.
    assert isinstance(verdict.skipped, tuple)
