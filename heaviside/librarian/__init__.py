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
from heaviside.librarian.tas import (
    SCHEMA_MAP,
    DuplicateComponentError,
    SchemaNotFoundError,
    ValidationError,
    add_component,
    component_exists,
    load_validator,
    validate_component,
)

__all__ = [
    "CATEGORIES",
    "LOCK_DIR",
    "SCHEMA_MAP",
    "TAS_DATA_DIR",
    "DuplicateComponentError",
    "LibrarianError",
    "LockTimeoutError",
    "SchemaNotFoundError",
    "Transaction",
    "UnknownCategoryError",
    "ValidationError",
    "acquire_lock",
    "add_component",
    "component_exists",
    "describe_lock",
    "load_validator",
    "safe_append",
    "validate_component",
]
