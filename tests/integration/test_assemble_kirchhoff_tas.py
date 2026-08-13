"""A real design must come back with its POWER STAGE sourced.

The regression this guards (ABT #681): ``assemble_bom_from_tas`` only knew the
pre-cutover MKF-stencil placeholder shape (``data`` = a catalogue filename
string), so on a Kirchhoff-built TAS — the only kind there is since the
della-Pollock cutover — no placeholder matched and the selection loop ran over
nothing. A 48 V -> 12 V buck came back with ONE sourced line, ``Cin``, and that
one is a synthesized auxiliary (``_add_input_capacitor``), not a component of
the power stage. Q1, D1, Cout and U1 were never looked at, and nothing raised.

The suite passed throughout, because every existing test asserted that assemble
RAN — not that it sourced anything. So this test names the refdeses: Q1, D1 and
Cout must each carry a real MPN. It fails loudly if the walk ever goes blind
again.

Slow (Kirchhoff design + catalogue scans) — opt in with ``-m integration``.
"""

from __future__ import annotations

from typing import Any

import pytest

from heaviside.catalogue import assemble_bom_from_tas
from heaviside.decomposer import kirchhoff_adapter as _ka

pytestmark = pytest.mark.integration

BUCK_SPEC: dict[str, Any] = {
    "inputVoltage": {"minimum": 36, "maximum": 60, "nominal": 48},
    "desiredInductance": 22e-6,
    "currentRippleRatio": 0.4,
    "diodeVoltageDrop": 0.7,
    "efficiency": 0.95,
    "operatingPoints": [
        {
            "outputVoltages": [12.0],
            "outputCurrents": [5.0],
            "switchingFrequency": 200_000,
            "ambientTemperature": 25,
        }
    ],
}


def _components(tas: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        c["name"]: c
        for s in tas.get("topology", {}).get("stages", [])
        for c in s.get("circuit", {}).get("components", [])
        if isinstance(c, dict) and c.get("name")
    }


@pytest.fixture(scope="module")
def sourced_buck() -> dict[str, dict[str, Any]]:
    """spec -> Kirchhoff TAS -> assemble_bom_from_tas, by refdes."""
    try:
        tas = _ka.design_from_hs_spec("buck", BUCK_SPEC)
    except (_ka.KirchhoffUnavailable, _ka.KirchhoffTopologyUnsupported) as exc:
        pytest.skip(f"Kirchhoff backend unavailable: {exc}")
    return _components(assemble_bom_from_tas(tas, topology="buck", spec=BUCK_SPEC))


@pytest.mark.parametrize(
    ("ref", "category"), [("Q1", "mosfet"), ("D1", "diode"), ("Cout", "capacitor")]
)
def test_the_power_stage_is_sourced(
    sourced_buck: dict[str, dict[str, Any]], ref: str, category: str
) -> None:
    """Every power-stage line of a 48 V -> 12 V buck gets a real orderable part."""
    assert ref in sourced_buck, f"{ref} is not in the design: {sorted(sourced_buck)}"
    prov = sourced_buck[ref].get("selection_provenance")
    assert isinstance(prov, dict), (
        f"{ref} was never sourced. If it was not even LOOKED at, the placeholder "
        f"predicates have stopped recognising the TAS shape again (ABT #681)."
    )
    assert prov["category"] == category
    assert prov["mpn"], f"{ref} has selection provenance but no part number"
    assert prov["manufacturer"]


def test_more_than_the_synthesized_input_cap_is_sourced(
    sourced_buck: dict[str, dict[str, Any]],
) -> None:
    """The exact shape of the bug: the only sourced line was ``Cin``, which
    assemble SYNTHESIZES itself. A BOM whose only part is one it invented has
    not sourced the design."""
    sourced = {
        r for r, c in sourced_buck.items() if isinstance(c.get("selection_provenance"), dict)
    }
    assert sourced - {"Cin"}, "nothing but the synthesized input cap was sourced"
    assert len(sourced) >= 4, f"only {len(sourced)} of {len(sourced_buck)} lines sourced: {sourced}"


def test_the_magnetic_stays_deferred_not_rejected(
    sourced_buck: dict[str, dict[str, Any]],
) -> None:
    """'Nobody looked' and 'nothing fit' are different answers, and the MCP
    server distinguishes them. L1 is designed by MKF (della-Pollock), never
    sourced from the catalogue: it must come back UNFILLED, not as a failure —
    and a capacitor whose stress this module does not derive behaves the same
    way. What must NOT happen is the whole power stage reading like L1."""
    l1 = sourced_buck["L1"]
    assert "selection_provenance" not in l1
    assert "magnetic" in l1["data"]


def test_assembling_twice_does_not_re_source(sourced_buck: dict[str, dict[str, Any]]) -> None:
    """A sourced component's ``data`` is the chosen part's envelope, which has
    the same family shape as the seed. It must not read as a placeholder again."""
    tas = {"topology": {"stages": [{"circuit": {"components": list(sourced_buck.values())}}]}}
    before = {r: (c.get("selection_provenance") or {}).get("mpn") for r, c in sourced_buck.items()}
    after = {
        r: (c.get("selection_provenance") or {}).get("mpn")
        for r, c in _components(assemble_bom_from_tas(tas, topology="buck", spec=BUCK_SPEC)).items()
    }
    assert after == before
