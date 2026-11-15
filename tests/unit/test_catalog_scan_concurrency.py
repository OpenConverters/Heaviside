"""Catalogue endpoints under concurrency and the /detail offset index.

Prod 2026-11-15: requests arriving during the startup cache warm each ran their
own full scan of the 2.9 GB resistors file (40 at once on 2 vCPU, warm not done
after 2 h), and /detail re-scanned the file on every call (~60 s for a part near
its end). These tests pin the fixes: single-flight builders, 503 while warming,
and an index-backed /detail that returns exactly what the full scan returned.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException

import heaviside.api.server as srv
import heaviside.catalogue._reader as reader
from heaviside.catalogue import mpn_offsets
from heaviside.catalogue.mpn_offsets import IndexStale, MpnOffsetIndex


def _res(pn: str, ohms: float, mfr: str = "Acme") -> dict:
    return {
        "resistor": {
            "manufacturerInfo": {
                "name": mfr,
                "datasheetInfo": {"part": {"partNumber": pn}, "electrical": {"resistance": {"nominal": ohms}}},
            }
        }
    }


def _fet(ref: str) -> dict:
    return {"semiconductor": {"mosfet": {"manufacturerInfo": {"name": "Acme", "reference": ref}}}}


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HEAVISIDE_TAS_DATA_DIR", str(tmp_path))
    _write(tmp_path / "resistors.ndjson", [_res("R-1", 1.0), _res("AR03BTC8663", 866e3), _res("ar03btc8663", 1.0)])
    _write(tmp_path / "mosfets.ndjson", [_fet("CSD19537Q3")])
    for fname, _ in srv._catalog_projectors().values():  # every category file exists
        if not (tmp_path / fname).exists():
            (tmp_path / fname).write_text("", encoding="utf-8")
    # fresh process state for every test
    monkeypatch.setattr(srv, "_DETAIL_INDEX", {})
    monkeypatch.setattr(srv, "_STATS_CACHE", None)
    monkeypatch.setattr(srv, "_WARM_STARTED", threading.Event())
    monkeypatch.setattr(srv, "_WARM_DONE", threading.Event())
    return tmp_path


def _old_full_scan(path: Path, mpn: str):
    """The pre-index /detail semantics: first line (file order) whose key matches."""
    want = mpn.strip().lower()
    for _, env in reader.iter_envelopes(path):
        if any(k.lower() == want for k in srv._detail_keys(env)):
            return env
    return None


def test_detail_matches_full_scan_case_insensitive_first_in_file_order(data_dir):
    for mpn in ("R-1", "r-1", "AR03BTC8663", "Ar03BtC8663", "nope"):
        old = _old_full_scan(data_dir / "resistors.ndjson", mpn)
        if old is None:
            with pytest.raises(HTTPException) as ei:
                srv.catalog_detail("resistors", mpn)
            assert ei.value.status_code == 404
        else:
            assert srv.catalog_detail("resistors", mpn)["data"] == old
    # first in file order: the 866 kOhm row, not the later lower-case duplicate
    got = srv.catalog_detail("resistors", "ar03btc8663")["data"]
    assert got["resistor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]["resistance"]["nominal"] == 866e3
    assert srv.catalog_detail("mosfets", "csd19537q3")["data"] == _fet("CSD19537Q3")


def test_detail_scans_the_file_once(data_dir, monkeypatch):
    scans = []
    real = mpn_offsets.iter_envelopes_at
    monkeypatch.setattr(mpn_offsets, "iter_envelopes_at", lambda p: (scans.append(p), real(p))[1])
    for _ in range(5):
        srv.catalog_detail("resistors", "R-1")
    assert len(scans) == 1


def test_hash_collision_never_returns_the_wrong_part(data_dir, monkeypatch):
    monkeypatch.setattr(mpn_offsets, "_h", lambda key: 7)  # every key collides
    idx = MpnOffsetIndex(data_dir / "resistors.ndjson", srv._detail_keys)
    assert idx.lookup("R-1") == _res("R-1", 1.0)
    assert idx.lookup("missing") is None


def test_index_rebuilds_after_the_file_is_rewritten(data_dir):
    assert srv.catalog_detail("resistors", "R-1")
    p = data_dir / "resistors.ndjson"
    _write(p, [_res("NEW-9", 9.0)])
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    with pytest.raises(IndexStale):
        srv._DETAIL_INDEX["resistors"].lookup("R-1")
    assert srv.catalog_detail("resistors", "NEW-9")["data"] == _res("NEW-9", 9.0)


def test_requests_during_warm_get_503_not_a_scan(data_dir, monkeypatch):
    scans = []
    monkeypatch.setattr(reader, "iter_envelopes", lambda p: (scans.append(p), iter(()))[1])
    srv._WARM_STARTED.set()  # warm running, nothing cached yet
    for call in (srv.catalog_stats, lambda: srv.catalog_detail("resistors", "R-1"), lambda: srv.catalog("resistors")):
        with pytest.raises(HTTPException) as ei:
            call()
        assert ei.value.status_code == 503
        assert ei.value.headers["Retry-After"]
    assert scans == []


def test_concurrent_cold_callers_share_one_scan(data_dir, monkeypatch):
    srv._WARM_STARTED.set()
    srv._WARM_DONE.set()  # warm over (e.g. it failed): lazy path, single-flight
    calls = []
    real = reader.iter_envelopes
    gate = threading.Event()

    def slow(p):
        calls.append(p)
        gate.wait(2)
        return real(p)

    monkeypatch.setattr(reader, "iter_envelopes", slow)
    results, errors = [], []

    def hit():
        try:
            results.append(srv.catalog_stats())
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=hit) for _ in range(8)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join(30)
    assert not errors
    assert len(results) == 8 and all(r == results[0] for r in results)
    # one scan per category file, not one per caller
    assert len(calls) == len(set(calls))


def test_browse_over_the_scan_cap_gets_503(data_dir, monkeypatch):
    monkeypatch.setattr(srv, "_FULL_SCAN_SLOTS", threading.BoundedSemaphore(1))
    monkeypatch.setattr(srv, "_FULL_SCAN_WAIT_S", 0.05)
    assert srv._FULL_SCAN_SLOTS.acquire()  # one browse already running
    try:
        with pytest.raises(HTTPException) as ei:
            srv.catalog("resistors")
        assert ei.value.status_code == 503
    finally:
        srv._FULL_SCAN_SLOTS.release()
    assert srv.catalog("resistors")["category"] == "resistors"
