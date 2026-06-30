"""Mocked verification of the CR-vs-Proteus design-level concurrency fan-out.

These tests do NOT touch the LLM or the real crossref pipeline. They stub
``_cr_coverage_attempt`` with a fast sleep that records how many designs are
active at once, then exercise ``_run_all_designs`` directly to prove:

  * designs really run CONCURRENTLY (observed concurrency > 1, <= the cap),
  * EVERY design is evaluated,
  * a single below-Proteus design fails the gate and is NAMED (and the other
    designs are not blamed),
  * the thread-safe ndjson appends are well-formed (one valid JSON object per
    line — never interleaved/corrupted).

The LLM module itself is module-skipped unless HEAVISIDE_RUN_LLM_CR=1, so we
load it by PATH (independent of pytest's import mode / the skip marker) and
monkeypatch the copy. The fan-out logic under test is self-contained in that
copy, so there is nothing real to leak into.
"""
from __future__ import annotations

import importlib.util
import json
import threading
import time
from pathlib import Path

import pytest

_LLM_MOD_PATH = Path(__file__).with_name("test_cr_vs_proteus_llm.py")


def _load_cr_module():
    spec = importlib.util.spec_from_file_location(
        "_cr_vs_proteus_llm_under_test", _LLM_MOD_PATH
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def cr(tmp_path, monkeypatch):
    """A fresh copy of the LLM module wired to a temp ndjson + a small cap."""
    mod = _load_cr_module()
    monkeypatch.setattr(mod, "_NDJSON_PATH", tmp_path / "cr_results.ndjson")
    monkeypatch.setattr(mod, "_CR_DESIGN_CONCURRENCY", 4)
    monkeypatch.setattr(mod, "_RESULTS", [])  # isolate the summary list
    return mod


def _make_stub(cr, *, below_proteus: set[str] | None = None, sleep_s: float = 0.04):
    """Build a fake ``_cr_coverage_attempt`` that records peak concurrency.

    Returns ``(stub, state)``; ``state["max_active"]`` is the high-water mark of
    simultaneously-active calls, ``state["designs"]`` every design it was asked
    about. Designs in ``below_proteus`` return 0% coverage (a guaranteed miss);
    all others return 100% (a guaranteed pass), so the verdict is deterministic.
    """
    below_proteus = below_proteus or set()
    lock = threading.Lock()
    state = {"active": 0, "max_active": 0, "designs": [], "calls": 0}

    def stub(design: str) -> dict:
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
            state["designs"].append(design)
            state["calls"] += 1
        try:
            time.sleep(sleep_s)
        finally:
            with lock:
                state["active"] -= 1
        miss = design in below_proteus
        ours_scope = 10
        ours_n = 0 if miss else ours_scope
        return {
            "ours_n": ours_n, "ours_scope": ours_scope,
            "ours_pct": 0.0 if miss else 1.0,
            "runtime_s": round(sleep_s, 3),
            "in_tok": 100, "out_tok": 50, "calls": 1,
        }

    return stub, state


def test_designs_run_concurrently(cr, monkeypatch):
    designs = list(cr.PROTEUS_BASELINE)
    stub, state = _make_stub(cr)
    monkeypatch.setattr(cr, "_cr_coverage_attempt", stub)

    failures = cr._run_all_designs(designs)

    # All passing stubs -> no failures, and every design evaluated exactly once
    # (passes on attempt 1, so no retries).
    assert failures == []
    assert set(state["designs"]) == set(designs)
    assert state["calls"] == len(designs)

    # Concurrency actually happened, and never exceeded the cap.
    assert state["max_active"] > 1, "designs did not overlap — still sequential"
    assert state["max_active"] <= cr._CR_DESIGN_CONCURRENCY


def test_below_proteus_design_is_named(cr, monkeypatch):
    designs = list(cr.PROTEUS_BASELINE)
    loser = "eval-lt7176-az"  # Proteus 73/90 = 81%, our stub returns 0% for it
    assert loser in designs
    stub, state = _make_stub(cr, below_proteus={loser})
    monkeypatch.setattr(cr, "_cr_coverage_attempt", stub)

    failures = cr._run_all_designs(designs)

    # Exactly the one losing design fails, and it is named; nobody else blamed.
    assert len(failures) == 1, failures
    assert loser in failures[0]
    assert "BELOW Proteus" in failures[0]
    others = [d for d in designs if d != loser]
    assert not any(d in failures[0] for d in others)

    # The losing design exhausted its retries; the rest passed on attempt 1.
    assert state["calls"] == len(others) + cr._CR_MAX_ATTEMPTS

    # And the assertion in the real test would fail, surfacing that design.
    with pytest.raises(AssertionError) as ei:
        assert not failures, (
            f"{len(failures)}/{len(designs)} design(s) BELOW Proteus:\n  "
            + "\n  ".join(failures)
        )
    assert loser in str(ei.value)


def test_ndjson_appends_not_corrupted(cr, monkeypatch):
    designs = list(cr.PROTEUS_BASELINE)
    loser = "EVL1653F-TF-00A"  # forces 3 retry-appends from one thread too
    stub, state = _make_stub(cr, below_proteus={loser}, sleep_s=0.02)
    monkeypatch.setattr(cr, "_cr_coverage_attempt", stub)

    cr._run_all_designs(designs)

    raw = (cr._NDJSON_PATH).read_text().splitlines()
    # One line per stub call (each attempt appends exactly once).
    assert len(raw) == state["calls"]
    recs = [json.loads(line) for line in raw]  # every line must parse cleanly
    # Every design appears; the loser appears _CR_MAX_ATTEMPTS times.
    seen = [r["design"] for r in recs]
    assert set(seen) == set(designs)
    assert seen.count(loser) == cr._CR_MAX_ATTEMPTS
    # Records carry the expected schema (no torn/half-written rows).
    for r in recs:
        assert set(r) >= {"design", "attempt", "ours", "ours_pct",
                          "proteus", "proteus_pct", "in_tok", "out_tok", "calls"}
