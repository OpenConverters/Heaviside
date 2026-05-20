"""TAS librarian — the sole sanctioned writer to ``TAS/data/*.ndjson``.

v0.1 surfaces only the concurrency primitives ported from Proteus
(``safe_access`` module).  Future revisions will add the fetcher,
the Digi-Key / Mouser API wrappers, and the auditor — each behind
its own explicit import so this package's import-time footprint
stays minimal.
"""

from __future__ import annotations

from heaviside.librarian.safe_access import (
    CATEGORIES,
    LOCK_DIR,
    TAS_DATA_DIR,
    LibrarianError,
    LockTimeoutError,
    Transaction,
    UnknownCategoryError,
    acquire_lock,
    describe_lock,
    safe_append,
)

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
