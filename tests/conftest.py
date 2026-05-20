"""Test-wide fixtures.

Disables the PyOpenMagnetics result cache for every unit and
regression test.  Rationale:

  * Unit tests stub PyOM via ``FakePyOM`` / ``monkeypatch`` — caching
    a stubbed response would leak between tests with no benefit.
  * Regression tests don't call PyOM.

Integration tests (``tests/integration/``) are NOT covered by this
``autouse`` fixture; they want the cache (turning re-runs into
JSON reads is the whole point of the cache).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_pyom_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``heaviside._pyom_cache.cached_call`` into passthrough.

    Applied to every test under ``tests/`` except those that
    explicitly opt back in.  Integration tests live in their own
    directory and override this via a sibling conftest if needed.
    """
    monkeypatch.setenv("HEAVISIDE_PYOM_CACHE", "0")
