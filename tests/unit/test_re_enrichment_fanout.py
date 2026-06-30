"""Tests for the RE enrichment fan-out in ``run_crossref_with_cre``.

``_run_re_enrichment_stages`` runs MPN verification CONCURRENTLY with the
sequential rdson → claims → testbench chain on a shared ``REState``. These tests
monkeypatch the four RE stages with stubs that:

  * rendezvous on a ``threading.Barrier`` so concurrent dispatch is PROVEN
    deterministically (a sequential implementation would deadlock the barrier),
  * each write their own disjoint ``REState`` field,

then assert the post-fan-out state has ALL fields populated (same as sequential)
and that a stage raising surfaces (no swallowing).
"""

from __future__ import annotations

import threading
import time

import pytest

from heaviside.pipeline import re_pipeline
from heaviside.pipeline.crossref_pipeline import _run_re_enrichment_stages
from heaviside.pipeline.re_state import (
    ReferenceClaims,
    ReferenceSpec,
    REState,
)


def _base_spec() -> ReferenceSpec:
    return ReferenceSpec(
        topology="buck",
        vin_min=36.0,
        vin_nom=48.0,
        vin_max=60.0,
        vout=12.0,
        iout=5.0,
        pout=60.0,
        fsw=200_000.0,
    )


def _make_state() -> REState:
    return REState(
        reference="test-buck-60W",
        ref_spec=_base_spec(),
        ref_bom=[
            {"ref_des": "U1", "role": "controller", "mpn": "MP1653", "category": "mosfet"},
            {"ref_des": "L1", "role": "mainInductor", "mpn": "XAL1010", "category": "magnetic"},
        ],
    )


def test_fanout_runs_verify_concurrently_with_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """verify-mpns and the rdson/claims/testbench chain run on separate threads
    at the same time, and every stage's field lands on the shared state."""
    # 2 parties: verify-mpns + the FIRST chain stage (rdson). If the fan-out were
    # sequential they could never both be inside the barrier at once -> timeout.
    barrier = threading.Barrier(2, timeout=5.0)
    lock = threading.Lock()
    live = {"now": 0, "max": 0}

    def _enter() -> None:
        with lock:
            live["now"] += 1
            live["max"] = max(live["max"], live["now"])

    def _leave() -> None:
        with lock:
            live["now"] -= 1

    def stub_verify(s: REState) -> REState:
        _enter()
        barrier.wait()  # rendezvous with the chain -> proves concurrency
        time.sleep(0.02)
        for row in s.ref_bom:
            row["in_tas"] = True  # disjoint write: per-row flag
        s.diagnostics.append("verify ran")
        _leave()
        return s

    def stub_rdson(s: REState) -> REState:
        _enter()
        barrier.wait()  # rendezvous with verify
        old = s.ref_spec
        assert old is not None
        # disjoint write: wholesale ref_spec reassignment carrying rdson
        s.ref_spec = ReferenceSpec(
            topology=old.topology,
            vin_min=old.vin_min,
            vin_nom=old.vin_nom,
            vin_max=old.vin_max,
            vout=old.vout,
            iout=old.iout,
            pout=old.pout,
            fsw=old.fsw,
            rdson_hs=5.0,
            rdson_ls=3.0,
        )
        _leave()
        return s

    def stub_claims(s: REState) -> REState:
        # chain ordering: must see the spec rdson_hs that stub_rdson wrote
        assert s.ref_spec is not None and s.ref_spec.rdson_hs == 5.0
        s.ref_claims = ReferenceClaims(efficiency={"full_load": 0.92})
        return s

    def stub_testbench(s: REState) -> REState:
        # chain ordering: must see the claims that stub_claims wrote
        assert s.ref_claims.efficiency == {"full_load": 0.92}
        s.role_map = "MAPPED"  # type: ignore[assignment]
        s.sim_result = {"efficiency": 0.91}
        s.passed = True
        return s

    monkeypatch.setattr(re_pipeline, "_stage2_5_verify_mpns", stub_verify)
    monkeypatch.setattr(re_pipeline, "_stage2_65_extract_rdson", stub_rdson)
    monkeypatch.setattr(re_pipeline, "_stage2_7_extract_claims", stub_claims)
    monkeypatch.setattr(re_pipeline, "_stage2_8_testbench", stub_testbench)

    state = _make_state()
    out = _run_re_enrichment_stages(state)

    # Same shared object mutated in place (no fresh-state swap).
    assert out is state

    # Concurrency proven: both branches were live simultaneously.
    assert live["max"] >= 2

    # ALL fields populated, exactly as a sequential run would leave them.
    assert all(row.get("in_tas") for row in out.ref_bom)
    assert "verify ran" in out.diagnostics
    assert out.ref_spec is not None
    assert out.ref_spec.rdson_hs == 5.0
    assert out.ref_spec.rdson_ls == 3.0
    assert out.ref_claims.efficiency == {"full_load": 0.92}
    assert out.role_map == "MAPPED"
    assert out.sim_result == {"efficiency": 0.91}
    assert out.passed is True


def test_fanout_surfaces_verify_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exception in the independent verify-mpns branch propagates (no swallow)."""

    def boom(s: REState) -> REState:
        raise RuntimeError("verify exploded")

    def passthrough(s: REState) -> REState:
        return s

    monkeypatch.setattr(re_pipeline, "_stage2_5_verify_mpns", boom)
    monkeypatch.setattr(re_pipeline, "_stage2_65_extract_rdson", passthrough)
    monkeypatch.setattr(re_pipeline, "_stage2_7_extract_claims", passthrough)
    monkeypatch.setattr(re_pipeline, "_stage2_8_testbench", passthrough)

    with pytest.raises(RuntimeError, match="verify exploded"):
        _run_re_enrichment_stages(_make_state())


def test_fanout_surfaces_chain_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exception inside the sequential chain (testbench) propagates too."""

    def passthrough(s: REState) -> REState:
        return s

    def boom(s: REState) -> REState:
        raise RuntimeError("testbench exploded")

    monkeypatch.setattr(re_pipeline, "_stage2_5_verify_mpns", passthrough)
    monkeypatch.setattr(re_pipeline, "_stage2_65_extract_rdson", passthrough)
    monkeypatch.setattr(re_pipeline, "_stage2_7_extract_claims", passthrough)
    monkeypatch.setattr(re_pipeline, "_stage2_8_testbench", boom)

    with pytest.raises(RuntimeError, match="testbench exploded"):
        _run_re_enrichment_stages(_make_state())
