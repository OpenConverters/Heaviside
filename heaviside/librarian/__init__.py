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
from heaviside.librarian.auditor import (
    AUDITABLE_CATEGORIES,
    CRITICAL_PARAMS,
    REQUIRED_PARAMS,
    CategoryAudit,
    ComponentAudit,
    CorruptLine,
    FieldGap,
    FieldStatus,
    audit_all,
    audit_category,
    audit_component,
)

__all__ = [
    "AUDITABLE_CATEGORIES",
    "CATEGORIES",
    "CRITICAL_PARAMS",
    "CategoryAudit",
    "ComponentAudit",
    "CorruptLine",
    "DuplicateComponentError",
    "FieldGap",
    "FieldStatus",
    "LOCK_DIR",
    "LibrarianError",
    "LockTimeoutError",
    "REQUIRED_PARAMS",
    "SCHEMA_MAP",
    "SchemaNotFoundError",
    "TAS_DATA_DIR",
    "Transaction",
    "UnknownCategoryError",
    "ValidationError",
    "acquire_lock",
    "add_component",
    "audit_all",
    "audit_category",
    "audit_component",
    "component_exists",
    "describe_lock",
    "load_validator",
    "safe_append",
    "validate_component",
]
