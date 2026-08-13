"""Which TAS components ``assemble_bom_from_tas`` recognises as fillable.

ABT #681: the placeholder predicates only knew the PRE-cutover MKF-stencil
shape, where a component's ``data`` was a catalogue filename string. Since the
della-Pollock cutover (ABT #48/#30) the only TAS producer is Kirchhoff, and a
Kirchhoff component carries ``data`` as an object keyed by family — so
``isinstance(data, str)`` was False for every component of every real design,
no placeholder matched, and the selection loop ran over nothing. Nothing
raised: a BOM in which Q1/D1/Cout were never even looked at reported exactly
like one that needed no parts.

These tests pin the recognition rule itself (no catalogue, no Kirchhoff, no
PyOM). ``tests/integration/test_assemble_kirchhoff_tas.py`` is the end-to-end
counterpart: a real buck TAS must come back with real MPNs on Q1/D1/Cout.
"""

from __future__ import annotations

from typing import Any

import pytest

from heaviside.catalogue.assemble import (
    _is_capacitor_placeholder,
    _is_controller_placeholder,
    _is_diode_placeholder,
    _is_mosfet_placeholder,
)

pytestmark = pytest.mark.unit


def _seed(family: str, kind: str | None = None, **req: Any) -> dict[str, Any]:
    """A Kirchhoff component seed: ``data`` keyed by family, excitation
    alongside it under ``inputs`` (the exact shape design_from_hs_spec emits)."""
    slot: dict[str, Any] = {kind: {}} if kind else {}
    return {"data": {family: slot, "inputs": {"designRequirements": req}}}


# ---------------------------------------------------------------------------
# The Kirchhoff shape (the live one)
# ---------------------------------------------------------------------------


def test_kirchhoff_mosfet_seed_is_a_mosfet_placeholder() -> None:
    q1 = _seed("semiconductor", "mosfet", deviceType="mosfet", role="mainSwitch")
    assert _is_mosfet_placeholder(q1)
    assert not _is_diode_placeholder(q1)
    assert not _is_capacitor_placeholder(q1)
    assert not _is_controller_placeholder(q1)


def test_kirchhoff_diode_seed_is_a_diode_placeholder() -> None:
    d1 = _seed("semiconductor", "diode", deviceType="diode", ratedReverseVoltage=75.0)
    assert _is_diode_placeholder(d1)
    # Both are family "semiconductor" — the kind is what separates them, so a
    # diode must never be sourced as a MOSFET (or vice versa).
    assert not _is_mosfet_placeholder(d1)


def test_kirchhoff_output_capacitor_seed_is_a_capacitor_placeholder() -> None:
    cout = _seed("capacitor", role="outputFilter", ratedVoltage=15.0)
    assert _is_capacitor_placeholder(cout)


def test_kirchhoff_controller_seed_is_a_controller_placeholder() -> None:
    u1 = _seed("controller", category="pwmController", topology="buckConverter")
    assert _is_controller_placeholder(u1)


def test_a_magnetic_seed_is_no_ones_placeholder() -> None:
    """The magnetic is designed by MKF (della-Pollock), never sourced here."""
    l1 = _seed("magnetic", turnsRatios=[])
    assert not any(
        p(l1)
        for p in (
            _is_mosfet_placeholder,
            _is_diode_placeholder,
            _is_capacitor_placeholder,
            _is_controller_placeholder,
        )
    )


def test_a_semiconductor_of_unknown_kind_is_not_guessed_at() -> None:
    """An empty family slot names no kind. Sourcing it as whichever kind asked
    first would be a guess; leaving it unsourced reports it as deferred, which
    is what it is."""
    unknown = _seed("semiconductor")
    assert not _is_mosfet_placeholder(unknown)
    assert not _is_diode_placeholder(unknown)


# ---------------------------------------------------------------------------
# Roles: not every capacitor is sized by the output-ripple budget
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["inputFilter", "snubber", "resonant"])
def test_a_capacitor_this_module_derives_no_stress_for_is_left_alone(role: str) -> None:
    """``assemble`` derives ONE capacitance target: C_out for 1 % output
    ripple. Applying it to a clamp cap sources a flyback's 129 pF snubber as an
    85 mF supercap. Those seeds stay unfilled — nobody derived their stress."""
    assert not _is_capacitor_placeholder(_seed("capacitor", role=role, ratedVoltage=135.0))


def test_a_body_diode_is_not_a_bom_line() -> None:
    """A body diode belongs to the MOSFET that carries it (same rule as
    ``kirchhoff_fill``, which defers these)."""
    assert not _is_diode_placeholder(_seed("semiconductor", "diode", role="bodyDiode"))


# ---------------------------------------------------------------------------
# The pre-cutover stencil shape still works
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("predicate", "data"),
    [
        (_is_mosfet_placeholder, "TAS/data/mosfets.ndjson?placeholder=Q1"),
        (_is_diode_placeholder, "TAS/data/diodes.ndjson?placeholder=D1"),
        (_is_capacitor_placeholder, "TAS/data/capacitors.ndjson?placeholder=C_out"),
        (_is_controller_placeholder, "TAS/data/controllers.ndjson?placeholder=U1"),
    ],
)
def test_the_stencil_filename_form_is_still_recognised(predicate: Any, data: str) -> None:
    """An older stencil TAS — and this module's own synthesized aux caps, which
    are created with exactly this string — must not start failing."""
    assert predicate({"name": "X", "data": data})


# ---------------------------------------------------------------------------
# A sourced component is not a placeholder any more
# ---------------------------------------------------------------------------


def test_a_stamped_component_is_no_longer_a_placeholder() -> None:
    """Stamping replaces ``data`` with the chosen part's envelope, which has the
    SAME family shape as the seed it replaced. Without the provenance guard a
    second assemble pass would re-select every line."""
    stamped = {
        "name": "Q1",
        "data": {"semiconductor": {"mosfet": {"manufacturerInfo": {}}}},
        "selection_provenance": {"category": "mosfet", "mpn": "IRFB4110"},
    }
    assert not _is_mosfet_placeholder(stamped)

    stamped_cap = {
        "name": "C_out",
        "data": {"capacitor": {}},
        "selection_provenance": {"category": "capacitor", "mpn": "EEU-FR1V181"},
    }
    assert not _is_capacitor_placeholder(stamped_cap)


def test_a_component_with_no_data_is_not_a_placeholder() -> None:
    assert not _is_mosfet_placeholder({"name": "Q1"})
    assert not _is_capacitor_placeholder({"name": "C1", "data": None})
