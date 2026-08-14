"""A failed Otto must not be recorded as an Otto who agreed.

Otto is an ADVERSARIAL check: he refuses a ``no_substitute`` unless the
catalogue evidence supports it. That makes his silence a verdict in one
direction — every missing challenge resolves to "the no_substitute stands" —
so a stage that never ran must never look like a stage that ran and confirmed.

Before the fix it looked exactly like one. A failed LLM call left ``challenges``
empty, an empty ``challenges`` left ``overturned`` empty, and the code logged
"Otto confirmed all N no_substitute items" either way. Both meanings printed the
same line, character for character; the only trace of the difference was a
``diagnostics`` entry nothing prompted anyone to read. Observed live: two runs
of the same two-line BOM an hour apart, one where every Otto call 400'd and one
where they all succeeded, produced identical output.

``bool(otto_log)`` was no help — the dict is always built — which is why
``scripts/validate_nonwurth_cr.py`` reported "Otto ran: True" for a run in
which he had not.
"""

from __future__ import annotations

import json

from heaviside.pipeline import crossref_pipeline as cp
from heaviside.pipeline.crossref import CrossRefState


def _state(n: int = 2) -> CrossRefState:
    refs = [f"C{i}" for i in range(1, n + 1)]
    state = CrossRefState(
        source_bom=[{"ref_des": r, "component_type": "capacitor"} for r in refs],
        target_manufacturer="Würth Elektronik",
    )
    state.crossref_result = [
        {
            "ref_des": r,
            "component_type": "capacitor",
            "original_pn": f"PART-{r}",
            "original_value": "100nF",
            "original_package": "0603",
            "status": "no_substitute",
            "substitute_pn": "no_substitute",
            "notes": "",
        }
        for r in refs
    ]
    return state


def test_otto_that_never_ran_is_not_recorded_as_confirmation(monkeypatch) -> None:
    """Every batch fails -> did_not_run, and the unchallenged refs are named."""
    state = _state()

    def _boom(*a, **k):
        raise RuntimeError("no MOONSHOT_API_KEY or OPENAI_API_KEY in environment")

    monkeypatch.setattr(cp, "call_agent", _boom)

    out = cp._stage6_otto(state)

    assert out.otto_log["status"] == "did_not_run"
    assert out.otto_log["batches_failed"] == out.otto_log["batches"] > 0
    # Naming the items is the point: "Otto failed" is not actionable,
    # "C1 and C2 went out unchallenged" is.
    assert set(out.otto_log["unchallenged_refs"]) == {"C1", "C2"}
    assert not out.otto_log["challenges"]
    # The old truthiness test that lied.
    assert bool(out.otto_log) is True and out.otto_log["status"] != "ran"


def test_otto_that_ran_and_agreed_is_distinguishable(monkeypatch) -> None:
    """No batch fails and nothing is overturned -> ran. The other meaning."""
    state = _state()
    monkeypatch.setattr(
        cp, "call_agent",
        lambda *a, **k: json.dumps({"challenges": [
            {"ref_des": "C1", "verdict": "CONFIRMED", "diagnosis": "nothing at this voltage"},
            {"ref_des": "C2", "verdict": "CONFIRMED", "diagnosis": "package unavailable"},
        ]}),
    )
    monkeypatch.setattr(cp, "extract_json_block", lambda raw: json.loads(raw))

    out = cp._stage6_otto(state)

    assert out.otto_log["status"] == "ran"
    assert out.otto_log["batches_failed"] == 0
    assert out.otto_log["unchallenged_refs"] == []
    assert len(out.otto_log["challenges"]) == 2


def test_partial_failure_is_neither_ran_nor_did_not_run(monkeypatch) -> None:
    """Some batches fail -> partial. Reported separately because the result is
    a genuine mixture: some items were challenged and some never were, and
    collapsing that into either neighbour loses which is which."""
    state = _state(n=2)
    calls = {"n": 0}

    def _flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return json.dumps({"challenges": [
            {"ref_des": "C2", "verdict": "CONFIRMED", "diagnosis": "ok"}]})

    monkeypatch.setattr(cp, "call_agent", _flaky)
    monkeypatch.setattr(cp, "extract_json_block", lambda raw: json.loads(raw))
    # One item per batch so the first batch can fail and the second succeed.
    monkeypatch.setattr(cp, "_batch_for_llm", lambda items, **k: [[i] for i in items])

    out = cp._stage6_otto(state)

    assert out.otto_log["status"] == "partial"
    assert out.otto_log["batches"] == 2
    assert out.otto_log["batches_failed"] == 1
    assert out.otto_log["unchallenged_refs"] == ["C1"]
