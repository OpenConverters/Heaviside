"""Runtime delta journal for the TAS librarian.

Every part appended via :func:`heaviside.librarian.tas.add_component` — i.e.
*after* it passes schema validation and the C++ physics validator ("Blade
Runner") — is ALSO recorded here as an append-only NDJSON journal.  A deployed
host writes new parts into its own local copy of ``TAS/data``; this journal lets
those runtime additions be reconciled back into the canonical TAS git repo on the
next deploy (see ``scripts/deploy_tas_data.sh``) instead of being lost when prod's
DB is replaced.

Opt-in.  The journal is written only when ``HEAVISIDE_TAS_DELTA_DIR`` is set to a
non-empty path (prod sets it in its supervisor env).  Unset → a no-op, so local
dev (which writes straight to the canonical checkout via the ``TAS/data`` symlink)
and the test suite are unaffected.

No locking of its own: :func:`record_addition` MUST be called by
``add_component`` while it holds the category lock, so appends for the same
category are already serialised.
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV = "HEAVISIDE_TAS_DELTA_DIR"


def delta_dir() -> Path | None:
    """The delta-journal directory, or ``None`` when journalling is disabled."""
    raw = os.environ.get(_ENV, "").strip()
    return Path(raw).expanduser() if raw else None


def record_addition(category: str, line: str) -> None:
    """Append one already-serialised NDJSON ``line`` (with its trailing newline)
    for ``category`` to the delta journal.  No-op when journalling is disabled.

    Delta-FIRST contract: ``add_component`` calls this *before* the main-DB write,
    under the category lock.  If it raises, ``add_component`` aborts before the
    main write, so the journal is never missing a part that reached the main DB
    (the only safe direction — reconcile re-adds and dedups by MPN).
    """
    directory = delta_dir()
    if directory is None:
        return
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{category}.ndjson"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


__all__ = ["delta_dir", "record_addition"]
