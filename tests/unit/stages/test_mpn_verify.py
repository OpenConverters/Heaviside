"""Unit tests for mpn_verify (deterministic, no LLM)."""
from __future__ import annotations

import pytest

from heaviside.stages.mpn_verify import verify_mpn


def test_existing_capacitor_mpn_found():
    r = verify_mpn("885012004001", category="capacitor")  # Würth ceramic in TAS
    assert r.exists is True
    assert r.tas_category == "capacitors"
    assert r.env is not None


def test_found_without_category_hint():
    r = verify_mpn("885012004001")
    assert r.exists is True
    assert r.tas_category == "capacitors"


def test_nonexistent_mpn():
    r = verify_mpn("DEFINITELY-NOT-A-REAL-PART-XYZ-999", category="capacitor")
    assert r.exists is False
    assert r.env is None


def test_blank_mpn():
    r = verify_mpn("")
    assert r.exists is False


def test_unknown_category_raises():
    with pytest.raises(ValueError, match="unknown PEAS category"):
        verify_mpn("X1", category="not-a-category")
