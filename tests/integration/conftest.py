"""Integration-test overrides.

Re-enables the PyOpenMagnetics cache that the top-level
``tests/conftest.py`` disables for unit/regression runs.  The cache
is the whole point of the integration suite: turning a 30+ s PyOM
loop into a sub-second JSON read on rerun is how the suite stays
runnable as new topologies land.

To force-bypass the cache for a one-off debug run::

    HEAVISIDE_PYOM_CACHE=0 pytest -m integration
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _enable_pyom_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo the unit-test conftest's ``HEAVISIDE_PYOM_CACHE=0``."""
    monkeypatch.delenv("HEAVISIDE_PYOM_CACHE", raising=False)
