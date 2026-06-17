"""The closed-loop designer's UI stages must stay in lock-step with the progress
messages ``design_converter`` actually emits. This guards against drift: if the
pipeline renames a phase, the message→stage mapping (or the declared stage list)
must be updated too, or the Jobs view silently stops advancing that stage."""

from __future__ import annotations

import pytest

from heaviside.api.server import (
    _CLOSED_LOOP_STAGES,
    _stage_for_message,
)

# The exact (message, expected-stage) pairs design_converter emits, in order.
# Keep this list in sync with the _say(...) calls in
# heaviside/pipeline/converter_designer.py.
_EMITTED = [
    ("Proposing converter constraints (duty ceiling + FET Vds class)", "Topology constraints"),
    ("Building base converter spec (MKF design constraints)", "Converter spec"),
    ("Sweeping switching frequency vs magnetic total loss", "Frequency sweep"),
    ("Sweep done: fsw* = 300 kHz, 7 feasible magnetics", "Frequency sweep"),
    ("Picking the magnetic from the loss-annotated front", "Magnetic pick"),
    ("Reconciling the magnetic across all operating points", "Cross-OP reconcile"),
    ("Realizing converter: selecting real TAS parts + MKF SPICE netlist", "Realize: real BOM + SPICE"),
    ("Re-simulating with SPICE knobs from the real parts (FET RON, diode RS)", "Tune SPICE from real parts"),
    ("Realism gate + gatekeeper on the simulated waveforms", "Realism gate + gatekeeper"),
    ("Adversarial review starting (Ray + Nicola)", "Review: Ray (engineering)"),
    ("Reviewing — Ray (engineering)", "Review: Ray (engineering)"),
    ("Reviewing — Nicola (quality)", "Review: Nicola (quality)"),
]


@pytest.mark.parametrize("msg,expected", _EMITTED)
def test_message_maps_to_expected_stage(msg, expected):
    assert _stage_for_message(msg) == expected


def test_every_declared_stage_is_reachable():
    """Each declared stage must be the target of at least one emitted message —
    a stage no message can advance is dead UI."""
    reached = {_stage_for_message(m) for m, _ in _EMITTED}
    missing = [s for s in _CLOSED_LOOP_STAGES if s not in reached]
    assert not missing, f"declared stages never reached by any message: {missing}"


def test_every_mapped_stage_is_declared():
    """No message may map to a stage that isn't in the declared list (it would
    never render in the Jobs flow)."""
    for _msg, expected in _EMITTED:
        assert expected in _CLOSED_LOOP_STAGES


def test_stages_are_in_emission_order():
    """The declared stage order must match the order messages first reach them,
    so the flow renders top-to-bottom as the design progresses."""
    first_seen: list[str] = []
    for msg, _ in _EMITTED:
        stage = _stage_for_message(msg)
        if stage is not None and stage not in first_seen:
            first_seen.append(stage)
    assert first_seen == _CLOSED_LOOP_STAGES


def test_unknown_message_returns_none():
    assert _stage_for_message("some unrelated chatter") is None
