"""TAS librarian: safe concurrent file access.

This is the **only** sanctioned path for writing to
``TAS/data/*.ndjson`` (see ``AGENTS.md`` §6: "TAS writes go through
the librarian, always").  All other code — including agents,
pipeline stages, and bridge orchestrators — must call into this
module rather than touching ``open()`` on a TAS file directly.

What this module provides
-------------------------

* :func:`acquire_lock` — flock-based exclusive lock keyed by TAS
  category (``mosfets``, ``diodes``, etc.).  Throws on timeout.
* :func:`safe_append` — context manager that yields a file handle
  opened in append mode, with the category locked for the duration.
* :class:`Transaction` — read-modify-write with atomic ``os.replace``
  semantics and a sibling ``.bak`` retained on failure for forensics.

Differences from the Proteus prototype
--------------------------------------

This is a strict-mode port.  Per ``CLAUDE.md`` ("no fallbacks, no
silent shortcuts — throw"):

* Bare ``except:`` blocks are gone.  Every catch is type-pinned and
  re-raises with context, except where the OS contract guarantees
  the operation cannot fail (``flock`` unlock on a closed fd).
* No silent "stale lock cleanup" heuristic.  Stale locks are real
  bugs (a librarian process crashed mid-write) and must be examined
  by hand before being removed.  :func:`describe_lock` returns
  enough metadata (pid, holder, age) to make that call.
* Lock files carry the holder's PID and an ISO timestamp so an
  abandoned lock can be attributed.
* Lock cleanup on release is opt-in (``unlink_on_release=True``):
  by default the lock file is left in place so the post-mortem can
  see who held it last.  This costs nothing (the next acquirer
  re-locks the same path).
* The TAS category whitelist is enforced at module level so a typo
  doesn't quietly create a new ``.ndjson`` outside the schema.

Library, not a CLI
------------------

Unlike the Proteus prototype this module exposes no ``__main__``
side effects — importing it does not scan the lock directory or
mutate filesystem state.  Stale-lock cleanup is a deliberate,
auditable operation; see :func:`describe_lock`.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import fcntl
import json
import os
import shutil
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import IO, Any

__all__ = [
    "CATEGORIES",
    "LOCK_DIR",
    "TAS_DATA_DIR",
    "LibrarianError",
    "LockTimeoutError",
    "Transaction",
    "UnknownCategoryError",
    "acquire_lock",
    "describe_lock",
    "safe_append",
]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


# Resolve paths once at import time.  Tests may monkeypatch the module
# globals to retarget them at a tmp_path — supported and tested.
_REPO_ROOT = Path(__file__).resolve().parents[2]

TAS_DATA_DIR: Path = _REPO_ROOT / "TAS" / "data"
"""TAS NDJSON data root.  Frozen submodule layout — see ``AGENTS.md``."""

LOCK_DIR: Path = _REPO_ROOT / ".heaviside" / "librarian-locks"
"""Lock-file directory.  Lives under ``.heaviside/`` (already gitignored).

We deliberately keep locks **outside** the TAS submodule so a
``git submodule update`` cannot interfere with a live lock."""

CATEGORIES: frozenset[str] = frozenset(
    {
        # Order matches AGENTS.md "Component Database" table.
        "mosfets",
        "diodes",
        "capacitors",
        "resistors",
        "varistors",
        "magnetics",
        "connectors",
        "igbts",
        "controllers",
        "converters",
        "quarantine",
    }
)
"""Whitelist of TAS NDJSON categories the librarian is allowed to touch.

A typo in a caller would otherwise quietly create a fresh
``mosftets.ndjson`` outside the schema — strictly rejected here."""


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LibrarianError(RuntimeError):
    """Base class for all librarian failures."""


class LockTimeoutError(LibrarianError):
    """Raised when an exclusive lock cannot be acquired within the timeout."""


class UnknownCategoryError(LibrarianError):
    """Raised when a caller asks for a category not in :data:`CATEGORIES`."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_category(category: str) -> None:
    if category not in CATEGORIES:
        raise UnknownCategoryError(
            f"unknown TAS category {category!r}.  Allowed: "
            f"{sorted(CATEGORIES)}.  If this is a new database file, add it "
            "to librarian.safe_access.CATEGORIES explicitly — the whitelist "
            "exists so typos cannot quietly create stray NDJSON files."
        )


def _ensure_lock_dir() -> None:
    LOCK_DIR.mkdir(parents=True, exist_ok=True)


def _lock_path(category: str) -> Path:
    return LOCK_DIR / f"{category}.lock"


def _data_path(category: str) -> Path:
    return TAS_DATA_DIR / f"{category}.ndjson"


def _write_lock_metadata(fh: IO[str]) -> None:
    """Stamp pid + ISO timestamp into the open lock file.

    Truncates first so a reused lock path doesn't carry stale data.
    """
    fh.seek(0)
    fh.truncate()
    fh.write(
        json.dumps(
            {
                "pid": os.getpid(),
                "acquired_utc": _dt.datetime.now(_dt.UTC).isoformat(),
            }
        )
    )
    fh.flush()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@contextmanager
def acquire_lock(
    category: str,
    *,
    timeout_s: float = 30.0,
    poll_interval_s: float = 0.1,
    unlink_on_release: bool = False,
) -> Iterator[IO[str]]:
    """Acquire an exclusive ``flock`` on the lock file for ``category``.

    The yielded file handle has the holder metadata written into it
    (see :func:`_write_lock_metadata`) — callers don't normally need
    it, but it is exposed for advanced introspection.

    Parameters
    ----------
    category :
        Must be one of :data:`CATEGORIES`.
    timeout_s :
        Maximum wall-clock seconds to wait for the lock.  Raises
        :class:`LockTimeoutError` on expiry.
    poll_interval_s :
        Sleep between non-blocking ``flock`` attempts.
    unlink_on_release :
        When ``True``, removes the lock file at exit.  Default
        ``False`` keeps the file (with the holder PID + timestamp) so
        a post-mortem can attribute crashed librarians.

    Raises
    ------
    UnknownCategoryError
        If ``category`` is not in the whitelist.
    LockTimeoutError
        If the lock cannot be acquired within ``timeout_s``.
    """
    _validate_category(category)
    _ensure_lock_dir()

    import time as _time

    lock_path = _lock_path(category)
    fh = lock_path.open("a+")
    try:
        deadline = _time.monotonic() + float(timeout_s)
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if _time.monotonic() >= deadline:
                    raise LockTimeoutError(
                        f"timed out after {timeout_s:.1f}s waiting for "
                        f"{category!r} lock at {lock_path}.  Check "
                        f"librarian.safe_access.describe_lock({category!r}) "
                        "for current holder; investigate before clearing."
                    ) from None
                _time.sleep(poll_interval_s)

        _write_lock_metadata(fh)
        try:
            yield fh
        finally:
            # Unlock before closing — POSIX guarantees flock release on
            # close, but doing it explicitly makes the intent obvious.
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                # The fd may already be closed if the caller did
                # something unusual.  Re-raise so a real bug isn't
                # hidden — this is exactly the "no silent failure"
                # rule from CLAUDE.md.
                raise
    finally:
        fh.close()
        if unlink_on_release:
            # Another holder may have unlinked it; not an error.
            with suppress(FileNotFoundError):
                lock_path.unlink()


def describe_lock(category: str) -> dict[str, Any] | None:
    """Return holder metadata for an existing lock file, or ``None``.

    The returned dict is whatever was last written by
    :func:`_write_lock_metadata`: typically ``{pid, acquired_utc}``,
    augmented here with ``{path, mtime_utc, age_s}``.

    Does NOT acquire the lock — use this for diagnostic introspection.
    Raises :class:`UnknownCategoryError` for unknown categories and
    :class:`LibrarianError` if the file exists but is unparseable
    (corruption is a bug to investigate, not to paper over).
    """
    _validate_category(category)
    path = _lock_path(category)
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LibrarianError(f"describe_lock({category!r}): cannot read {path}: {exc}") from exc
    try:
        meta = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        raise LibrarianError(
            f"describe_lock({category!r}): lock file at {path} is not valid "
            f"JSON ({exc.msg}).  Investigate before deleting — this almost "
            "certainly means a librarian process crashed mid-write."
        ) from exc
    if not isinstance(meta, dict):
        raise LibrarianError(
            f"describe_lock({category!r}): lock file payload at {path} is "
            f"{type(meta).__name__}, expected JSON object."
        )

    stat = path.stat()
    mtime = _dt.datetime.fromtimestamp(stat.st_mtime, _dt.UTC)
    now = _dt.datetime.now(_dt.UTC)
    meta.update(
        {
            "path": str(path),
            "mtime_utc": mtime.isoformat(),
            "age_s": (now - mtime).total_seconds(),
        }
    )
    return meta


@contextmanager
def safe_append(category: str, *, timeout_s: float = 30.0) -> Iterator[IO[str]]:
    """Yield a file handle in append mode with the category locked.

    Forwards ``timeout_s`` to :func:`acquire_lock`.  Writes are
    flushed and fsynced on close so a crashed librarian leaves a
    durable trailing record.
    """
    _validate_category(category)
    path = _data_path(category)
    if not path.parent.exists():
        raise LibrarianError(
            f"safe_append({category!r}): TAS_DATA_DIR {TAS_DATA_DIR} does "
            "not exist.  Did the submodule fail to initialise?"
        )
    with acquire_lock(category, timeout_s=timeout_s), path.open("a", encoding="utf-8") as fh:
        try:
            yield fh
        finally:
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                # fsync on some FS types (procfs / virtualised tmpfs)
                # can fail; re-raise so the librarian operator knows
                # the write isn't durable.  No silent skip.
                raise


# ---------------------------------------------------------------------------
# Transaction
# ---------------------------------------------------------------------------


class Transaction:
    """Atomic read-modify-write on a TAS category file.

    Usage::

        with Transaction("mosfets") as txn:
            lines = txn.read()              # list[str], trailing \\n kept
            lines.append(new_row + "\\n")
            txn.write(lines)                # atomic os.replace

    Semantics
    ---------

    * Acquires :func:`acquire_lock` on enter; releases on exit.
    * ``read()`` returns the file's raw lines (NDJSON is line-
      delimited; we don't parse here — that's the caller's job, so
      the librarian doesn't need to know about every schema).
    * ``write(lines)`` writes ``lines`` to a sibling ``.tmp`` file,
      copies the original to ``.bak``, then ``os.replace`` the tmp
      onto the canonical path.  On success the ``.bak`` is removed.
    * On exception inside the ``with`` block, the ``.tmp`` (if any)
      is deleted and the ``.bak`` is **kept** — leaving forensic
      evidence per the strict-mode policy.
    """

    def __init__(self, category: str, *, timeout_s: float = 30.0) -> None:
        _validate_category(category)
        self.category = category
        self.filepath = _data_path(category)
        self.temp_path = self.filepath.with_suffix(".ndjson.tmp")
        self.backup_path = self.filepath.with_suffix(".ndjson.bak")
        self.timeout_s = float(timeout_s)
        self._lock_cm: Any = None
        self._data: list[str] | None = None
        self._wrote: bool = False

    # Context manager ---------------------------------------------------

    def __enter__(self) -> Transaction:
        if not self.filepath.parent.exists():
            raise LibrarianError(
                f"Transaction({self.category!r}): TAS_DATA_DIR "
                f"{TAS_DATA_DIR} does not exist.  Did the submodule "
                "fail to initialise?"
            )
        self._lock_cm = acquire_lock(self.category, timeout_s=self.timeout_s)
        self._lock_cm.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            if exc_type is not None and self.temp_path.exists():
                # Clean up only the half-written tmp.  Backup stays for
                # post-mortem.
                with suppress(FileNotFoundError):
                    self.temp_path.unlink()
            elif exc_type is None and self._wrote and self.backup_path.exists():
                # Successful write: drop the now-redundant backup.
                self.backup_path.unlink()
        finally:
            assert self._lock_cm is not None  # set in __enter__
            self._lock_cm.__exit__(exc_type, exc_val, exc_tb)

    # I/O ---------------------------------------------------------------

    def read(self) -> list[str]:
        """Return the file's raw lines (cached for the transaction).

        Empty/missing file yields ``[]``.  Caller may mutate the list
        in place — :meth:`write` will use whatever list is passed in,
        not the cached one.
        """
        if self._data is None:
            if self.filepath.exists():
                with self.filepath.open("r", encoding="utf-8") as fh:
                    self._data = fh.readlines()
            else:
                self._data = []
        return self._data

    def write(self, lines: Sequence[str]) -> None:
        """Atomically replace the file with ``lines``.

        ``lines`` is a sequence of strings (typically newline-
        terminated NDJSON records).  We do NOT add or strip newlines
        — the caller is responsible for the on-disk format.
        """
        if not isinstance(lines, Sequence) or isinstance(lines, (str, bytes)):
            raise LibrarianError(
                f"Transaction.write: lines must be a Sequence[str], got "
                f"{type(lines).__name__}.  (Did you pass a single string "
                "instead of a list of records?)"
            )
        for i, line in enumerate(lines):
            if not isinstance(line, str):
                raise LibrarianError(
                    f"Transaction.write: lines[{i}] is {type(line).__name__}, expected str."
                )

        # Backup the current file (if any) before touching anything.
        if self.filepath.exists():
            shutil.copy2(self.filepath, self.backup_path)

        # Write to tmp, then atomic-replace.
        with self.temp_path.open("w", encoding="utf-8") as fh:
            fh.writelines(lines)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                raise  # see safe_append rationale
        os.replace(self.temp_path, self.filepath)
        self._wrote = True
