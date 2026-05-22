"""Drift test: stencil-emitted magnetic names must match registry ``magnetic_binding``.

Each golden TAS document under ``tests/regression/decomposer/golden/`` represents
the canonical output of a stencil. The set of magnetic component ``name`` values
in that document must equal the set of keys in the corresponding registry
entry's ``magnetic_binding``.

If a stencil starts emitting a new magnetic (or renames one) without the
registry being updated, ``attach_components_to_tas`` will silently fail to bind
it. This test catches that drift at unit-test speed.

Topologies with golden files but no ``magnetic_binding`` entry yet are skipped
with a visible reason so the gap stays on the radar.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from heaviside.topologies import registry

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "regression" / "decomposer" / "golden"

# Map golden-file prefix → canonical registry name.
PREFIX_TO_TOPOLOGY: dict[str, str] = {
    "2sforward": "two_switch_forward",
    "4sbb": "four_switch_buck_boost",
    "acf": "active_clamp_forward",
    "ahb": "asymmetric_half_bridge",
    "boost": "boost",
    "buck": "buck",
    "cllc": "cllc",
    "clllc": "clllc",
    "cuk": "cuk",
    "dab": "dual_active_bridge",
    "flyback": "flyback",
    "isobb": "isolated_buck_boost",
    "isobuck": "isolated_buck",
    "llc": "llc",
    "psfb": "phase_shifted_full_bridge",
    "pushpull": "push_pull",
    "sepic": "sepic",
    "ssforward": "single_switch_forward",
    "vienna": "vienna",
    "wbg": "weinberg",
    "zeta": "zeta",
}


def _collect_magnetic_names(tas: dict) -> list[str]:
    """Walk a TAS document and return every component ``name`` whose ``data``
    URL points at ``TAS/data/magnetics.ndjson``."""
    out: list[str] = []

    def walk(obj: object) -> None:
        if isinstance(obj, dict):
            data = obj.get("data")
            if isinstance(data, str) and "magnetics.ndjson" in data:
                name = obj.get("name")
                if not isinstance(name, str):
                    raise AssertionError(f"Magnetic component without string name: {obj!r}")
                out.append(name)
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(tas)
    return out


def _golden_cases() -> list[tuple[str, Path]]:
    """One (topology_name, golden_path) per golden TAS file."""
    cases: list[tuple[str, Path]] = []
    for path in sorted(GOLDEN_DIR.glob("*.tas.json")):
        prefix = path.name.split("_", 1)[0]
        if prefix not in PREFIX_TO_TOPOLOGY:
            raise AssertionError(
                f"Golden file {path.name!r} has unknown prefix {prefix!r}; "
                f"update PREFIX_TO_TOPOLOGY."
            )
        cases.append((PREFIX_TO_TOPOLOGY[prefix], path))
    return cases


@pytest.mark.parametrize(
    ("topology_name", "golden_path"),
    _golden_cases(),
    ids=lambda v: v.name if isinstance(v, Path) else v,
)
def test_stencil_matches_registry_binding(topology_name: str, golden_path: Path) -> None:
    entry = registry.get(topology_name)
    tas = json.loads(golden_path.read_text())
    emitted = _collect_magnetic_names(tas)

    if not entry.magnetic_binding:
        pytest.skip(
            f"{topology_name!r} has no magnetic_binding in the registry yet; "
            f"stencil emits {sorted(set(emitted))}. Add a binding to wire it "
            f"into attach_components_to_tas."
        )

    # No duplicate names within a single TAS.
    assert len(emitted) == len(set(emitted)), (
        f"{topology_name}: duplicate magnetic names emitted by stencil: {emitted}"
    )

    expected = set(entry.magnetic_binding)
    actual = set(emitted)
    assert actual == expected, (
        f"{topology_name}: stencil emits {sorted(actual)} but registry "
        f"magnetic_binding has {sorted(expected)}. Stencil and registry are "
        f"out of sync; update one of them."
    )

    # Exactly one binding must be the "main magnetic" (value=None).
    nones = [k for k, v in entry.magnetic_binding.items() if v is None]
    assert len(nones) == 1, (
        f"{topology_name}: magnetic_binding must have exactly one entry with "
        f"value=None (the main magnetic); found {nones}."
    )


def test_every_registry_binding_has_a_golden_or_skip_reason() -> None:
    """Inverse drift check: any topology with a ``magnetic_binding`` must
    either have a golden file (covered above) or be explicitly tracked here.

    This catches the case where someone adds a binding to the registry but
    never produces a stencil/golden for it.
    """
    bound = {t.name for t in registry.TOPOLOGIES if t.magnetic_binding}
    with_golden = {name for name, _ in _golden_cases()}
    missing = bound - with_golden
    assert not missing, (
        f"Topologies have magnetic_binding but no golden TAS: {sorted(missing)}. "
        f"Either add a stencil + golden, or remove the binding."
    )
