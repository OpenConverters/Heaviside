"""Tests for :mod:`heaviside.librarian.safe_access`.

Covers:

  * category whitelist enforcement
  * lock acquisition / release / mutual exclusion
  * lock timeout behaviour
  * describe_lock metadata + corruption handling
  * safe_append basic write
  * Transaction read / atomic write / backup retention on failure
  * Transaction.write input validation (strict-mode policy)
"""

from __future__ import annotations

import json
import multiprocessing as mp
import time
from pathlib import Path

import pytest

from heaviside.librarian import safe_access as sa

# ---------------------------------------------------------------------------
# Path isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _retarget_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point TAS_DATA_DIR + LOCK_DIR at a fresh tmp path per test."""
    data_dir = tmp_path / "tas-data"
    lock_dir = tmp_path / "locks"
    data_dir.mkdir()
    monkeypatch.setattr(sa, "TAS_DATA_DIR", data_dir)
    monkeypatch.setattr(sa, "LOCK_DIR", lock_dir)


def _seed(category: str, lines: list[str]) -> Path:
    path = sa.TAS_DATA_DIR / f"{category}.ndjson"
    path.write_text("".join(lines), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Category whitelist
# ---------------------------------------------------------------------------


class TestCategoryWhitelist:
    def test_acquire_lock_rejects_unknown(self):
        with pytest.raises(sa.UnknownCategoryError, match="mosftets"):
            with sa.acquire_lock("mosftets"):
                pass

    def test_safe_append_rejects_unknown(self):
        with pytest.raises(sa.UnknownCategoryError), sa.safe_append("typo_category"):
            pass

    def test_transaction_rejects_unknown(self):
        with pytest.raises(sa.UnknownCategoryError):
            sa.Transaction("nope")

    def test_describe_lock_rejects_unknown(self):
        with pytest.raises(sa.UnknownCategoryError):
            sa.describe_lock("invalid")

    def test_all_documented_categories_accepted(self):
        # Sanity: every category in the whitelist round-trips through
        # acquire_lock without raising.
        for cat in sa.CATEGORIES:
            with sa.acquire_lock(cat, timeout_s=1.0):
                pass


# ---------------------------------------------------------------------------
# Lock semantics
# ---------------------------------------------------------------------------


class TestLockBasics:
    def test_lock_dir_created_on_first_use(self):
        assert not sa.LOCK_DIR.exists()
        with sa.acquire_lock("mosfets"):
            assert sa.LOCK_DIR.exists()

    def test_lock_file_carries_pid_and_timestamp(self):
        with sa.acquire_lock("mosfets"):
            path = sa.LOCK_DIR / "mosfets.lock"
            meta = json.loads(path.read_text())
            assert meta["pid"] == sa.os.getpid()
            assert "acquired_utc" in meta
            # ISO-8601 with 'T' separator + timezone.
            assert "T" in meta["acquired_utc"]

    def test_lock_file_preserved_after_release_by_default(self):
        with sa.acquire_lock("mosfets"):
            pass
        assert (sa.LOCK_DIR / "mosfets.lock").exists()

    def test_lock_file_removed_when_unlink_on_release(self):
        with sa.acquire_lock("mosfets", unlink_on_release=True):
            pass
        assert not (sa.LOCK_DIR / "mosfets.lock").exists()


# ---------------------------------------------------------------------------
# Mutual exclusion (subprocess-based — flock is process-scoped on Linux)
# ---------------------------------------------------------------------------


def _hold_lock(
    data_dir: str, lock_dir: str, ready_path: str, release_path: str, category: str
) -> None:
    """Helper for cross-process lock test; not a test itself."""
    import os as _os
    import time as _time

    from heaviside.librarian import safe_access as _sa

    _sa.TAS_DATA_DIR = Path(data_dir)
    _sa.LOCK_DIR = Path(lock_dir)
    with _sa.acquire_lock(category, timeout_s=10.0):
        Path(ready_path).write_text(str(_os.getpid()))
        deadline = _time.monotonic() + 30.0
        while _time.monotonic() < deadline:
            if Path(release_path).exists():
                return
            _time.sleep(0.05)


class TestMutualExclusion:
    def test_second_acquirer_times_out_while_first_holds(self, tmp_path):
        ready = tmp_path / "ready"
        release = tmp_path / "release"
        proc = mp.Process(
            target=_hold_lock,
            args=(str(sa.TAS_DATA_DIR), str(sa.LOCK_DIR), str(ready), str(release), "mosfets"),
        )
        proc.start()
        try:
            # Wait for the subprocess to actually hold the lock.
            deadline = time.monotonic() + 10.0
            while not ready.exists():
                if time.monotonic() > deadline:
                    raise AssertionError("subprocess never grabbed the lock")
                time.sleep(0.05)

            with pytest.raises(sa.LockTimeoutError, match="mosfets"):
                with sa.acquire_lock("mosfets", timeout_s=0.5):
                    pass
        finally:
            release.write_text("go")
            proc.join(timeout=10.0)
            assert proc.exitcode == 0, f"holder subprocess exit={proc.exitcode}"

    def test_second_acquirer_proceeds_after_first_releases(self, tmp_path):
        ready = tmp_path / "ready"
        release = tmp_path / "release"
        proc = mp.Process(
            target=_hold_lock,
            args=(str(sa.TAS_DATA_DIR), str(sa.LOCK_DIR), str(ready), str(release), "diodes"),
        )
        proc.start()
        try:
            deadline = time.monotonic() + 10.0
            while not ready.exists():
                if time.monotonic() > deadline:
                    raise AssertionError("subprocess never grabbed the lock")
                time.sleep(0.05)

            release.write_text("go")
            proc.join(timeout=10.0)
            assert proc.exitcode == 0

            # Now we should be able to grab the lock with no waiting.
            with sa.acquire_lock("diodes", timeout_s=2.0):
                pass
        finally:
            if proc.is_alive():
                proc.terminate()
                proc.join()


# ---------------------------------------------------------------------------
# describe_lock
# ---------------------------------------------------------------------------


class TestDescribeLock:
    def test_describe_returns_none_when_no_lock(self):
        assert sa.describe_lock("mosfets") is None

    def test_describe_returns_metadata(self):
        with sa.acquire_lock("mosfets"):
            meta = sa.describe_lock("mosfets")
        assert meta is not None
        assert meta["pid"] == sa.os.getpid()
        assert "acquired_utc" in meta
        assert "mtime_utc" in meta
        assert meta["age_s"] >= 0.0
        assert meta["path"].endswith("mosfets.lock")

    def test_describe_throws_on_corrupted_lock(self):
        sa.LOCK_DIR.mkdir(parents=True, exist_ok=True)
        (sa.LOCK_DIR / "mosfets.lock").write_text("{not json")
        with pytest.raises(sa.LibrarianError, match="not valid JSON"):
            sa.describe_lock("mosfets")

    def test_describe_throws_on_non_object_payload(self):
        sa.LOCK_DIR.mkdir(parents=True, exist_ok=True)
        (sa.LOCK_DIR / "mosfets.lock").write_text("[1,2,3]")
        with pytest.raises(sa.LibrarianError, match="expected JSON object"):
            sa.describe_lock("mosfets")


# ---------------------------------------------------------------------------
# safe_append
# ---------------------------------------------------------------------------


class TestSafeAppend:
    def test_appends_to_existing_file(self):
        _seed("mosfets", ['{"a":1}\n'])
        with sa.safe_append("mosfets") as fh:
            fh.write('{"b":2}\n')
        assert (sa.TAS_DATA_DIR / "mosfets.ndjson").read_text() == ('{"a":1}\n{"b":2}\n')

    def test_creates_file_on_first_append(self):
        with sa.safe_append("mosfets") as fh:
            fh.write('{"x":1}\n')
        assert (sa.TAS_DATA_DIR / "mosfets.ndjson").read_text() == '{"x":1}\n'

    def test_throws_if_tas_dir_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sa, "TAS_DATA_DIR", tmp_path / "does-not-exist")
        with pytest.raises(sa.LibrarianError, match="TAS_DATA_DIR"):
            with sa.safe_append("mosfets"):
                pass


# ---------------------------------------------------------------------------
# Transaction
# ---------------------------------------------------------------------------


class TestTransaction:
    def test_read_returns_empty_for_missing_file(self):
        with sa.Transaction("mosfets") as txn:
            assert txn.read() == []

    def test_read_returns_existing_lines(self):
        _seed("mosfets", ['{"a":1}\n', '{"b":2}\n'])
        with sa.Transaction("mosfets") as txn:
            assert txn.read() == ['{"a":1}\n', '{"b":2}\n']

    def test_write_replaces_file_atomically(self):
        _seed("mosfets", ['{"a":1}\n'])
        with sa.Transaction("mosfets") as txn:
            lines = txn.read()
            lines.append('{"b":2}\n')
            txn.write(lines)
        assert (sa.TAS_DATA_DIR / "mosfets.ndjson").read_text() == ('{"a":1}\n{"b":2}\n')

    def test_successful_write_removes_backup(self):
        _seed("mosfets", ['{"a":1}\n'])
        with sa.Transaction("mosfets") as txn:
            txn.write(['{"x":1}\n'])
        assert not (sa.TAS_DATA_DIR / "mosfets.ndjson.bak").exists()
        assert not (sa.TAS_DATA_DIR / "mosfets.ndjson.tmp").exists()

    def test_backup_retained_when_exception_raised_after_write(self):
        _seed("mosfets", ['{"original":1}\n'])
        with pytest.raises(RuntimeError, match="boom"), sa.Transaction("mosfets") as txn:
            txn.write(['{"new":1}\n'])
            raise RuntimeError("boom")
        # File contains the new content (write committed), but backup
        # is retained for forensics.
        assert (sa.TAS_DATA_DIR / "mosfets.ndjson").read_text() == ('{"new":1}\n')
        assert (sa.TAS_DATA_DIR / "mosfets.ndjson.bak").read_text() == ('{"original":1}\n')

    def test_tmp_cleaned_up_on_exception_without_write(self):
        _seed("mosfets", ['{"a":1}\n'])
        # Force a failure mid-transaction by writing manually to the
        # tmp path, then raising.
        with pytest.raises(RuntimeError), sa.Transaction("mosfets") as txn:
            txn.temp_path.write_text("garbage")
            raise RuntimeError("simulated crash")
        assert not txn.temp_path.exists(), f"tmp file leaked: {txn.temp_path}"

    def test_write_rejects_string_argument(self):
        with sa.Transaction("mosfets") as txn:
            with pytest.raises(sa.LibrarianError, match="Sequence\\[str\\]"):
                txn.write("not a list")

    def test_write_rejects_non_str_elements(self):
        with sa.Transaction("mosfets") as txn:
            with pytest.raises(sa.LibrarianError, match="expected str"):
                txn.write(['{"a":1}\n', 42])

    def test_lock_held_for_duration_of_transaction(self):
        with sa.Transaction("mosfets"):
            # Outside transaction, the lock must be held → timeout
            # immediately for a competing acquirer in the same process
            # too?  No: flock is reentrant within the same fd, but a
            # FRESH fd from the same process competes correctly because
            # the kernel scopes locks by (process, fd) on Linux.  Use a
            # short timeout via subprocess instead.
            with pytest.raises(sa.LockTimeoutError):
                # Reuse the same-process check via the lock file path
                # directly: opening a NEW handle and asking for
                # non-blocking flock should fail because the
                # transaction's fd holds it.
                import fcntl

                lock_path = sa.LOCK_DIR / "mosfets.lock"
                fh = lock_path.open("a+")
                try:
                    try:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError as exc:
                        raise sa.LockTimeoutError("competing fd blocked") from exc
                finally:
                    fh.close()
